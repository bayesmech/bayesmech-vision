"""
VGGT-Omega 4D inference: run the model on a temporal sequence of frames and
save one point cloud per frame (world-space XYZ + RGB) as a function of time.

Usage
-----
    python inference/run_inference.py \\
        --frames  data/sintel/training/clean/alley_1 \\
        --ckpt    checkpoints/vggt_omega/vggt_omega_1b_512.pt \\
        --out     outputs/alley_1 \\
        [--model  facebook/VGGT-Omega-1B-512] \\
        [--resolution 512] \\
        [--conf-thresh 0.5] \\
        [--window  50] \\
        [--stride  25]

Output
------
outputs/alley_1/
    frame_0000.npz   # keys: xyz [H*W,3], rgb [H*W,3], conf [H*W], depth [H,W]
    frame_0001.npz
    ...
    cameras.npz      # keys: extrinsics [N,3,4], intrinsics [N,3,3]

To export PLY files instead of NPZ, pass --ply.

The model is run in a sliding window over the full sequence so that VRAM stays
bounded. All per-frame point clouds are expressed in the coordinate frame of
frame 0 of each window (the model's implicit world frame). For very long
sequences this means adjacent windows share a frame and the second window's
cloud is rotated into the first window's world frame via the shared-frame
extrinsic — this alignment is done automatically when --align-windows is set.
"""

from __future__ import annotations

import argparse
import struct
from pathlib import Path

import numpy as np
import torch

# ── helpers ───────────────────────────────────────────────────────────────────


def depth_to_pointcloud(
    depth: torch.Tensor,       # [H, W]
    intrinsics: torch.Tensor,  # [3, 3]
    extrinsics: torch.Tensor,  # [3, 4]  camera-from-world
    conf: torch.Tensor | None = None,
    conf_thresh: float = 0.0,
) -> dict[str, np.ndarray]:
    """Unproject a depth map to world-space XYZ.

    Extrinsics follow the OpenCV convention used by VGGT-Omega:
        X_cam = R @ X_world + t
    So the world-to-camera map is [R | t], and camera-to-world is R^T @ (X_cam - t).
    """
    H, W = depth.shape
    device = depth.device
    dtype = torch.float32

    depth = depth.to(dtype)
    intrinsics = intrinsics.to(dtype)
    extrinsics = extrinsics.to(dtype)

    fx, fy = intrinsics[0, 0], intrinsics[1, 1]
    cx, cy = intrinsics[0, 2], intrinsics[1, 2]

    us = torch.arange(W, device=device, dtype=dtype)
    vs = torch.arange(H, device=device, dtype=dtype)
    grid_v, grid_u = torch.meshgrid(vs, us, indexing="ij")  # [H, W]

    x_cam = (grid_u - cx) / fx * depth
    y_cam = (grid_v - cy) / fy * depth
    z_cam = depth
    pts_cam = torch.stack([x_cam, y_cam, z_cam], dim=-1).reshape(-1, 3)  # [H*W, 3]

    R = extrinsics[:3, :3]   # [3, 3]
    t = extrinsics[:3, 3]    # [3]
    pts_world = (pts_cam - t) @ R  # R^T @ (X_cam - t), written as row-vector mul

    mask = depth.reshape(-1) > 0
    if conf is not None and conf_thresh > 0:
        mask = mask & (conf.to(dtype).reshape(-1) >= conf_thresh)

    return {
        "xyz": pts_world[mask].cpu().numpy().astype(np.float32),
        "mask": mask.cpu().numpy(),
        "depth": depth.cpu().numpy().astype(np.float32),
        "conf": conf.cpu().numpy().astype(np.float32) if conf is not None else None,
    }


def save_npz(path: Path, xyz: np.ndarray, rgb: np.ndarray, depth: np.ndarray, conf: np.ndarray | None) -> None:
    arrays = {"xyz": xyz, "rgb": rgb, "depth": depth}
    if conf is not None:
        arrays["conf"] = conf
    np.savez_compressed(path, **arrays)


def save_ply(path: Path, xyz: np.ndarray, rgb: np.ndarray) -> None:
    """Write a binary PLY file with XYZ + RGB (uint8)."""
    n = len(xyz)
    rgb_u8 = (rgb * 255).clip(0, 255).astype(np.uint8)
    header = (
        "ply\nformat binary_little_endian 1.0\n"
        f"element vertex {n}\n"
        "property float x\nproperty float y\nproperty float z\n"
        "property uchar red\nproperty uchar green\nproperty uchar blue\n"
        "end_header\n"
    )
    with open(path, "wb") as f:
        f.write(header.encode())
        for i in range(n):
            f.write(struct.pack("<fff", *xyz[i]))
            f.write(struct.pack("BBB", *rgb_u8[i]))


# ── model loading ─────────────────────────────────────────────────────────────


def load_model(ckpt_path: str | None, model_id: str, device: torch.device) -> torch.nn.Module:
    import sys
    sys.path.insert(0, str(Path(__file__).parent.parent / "vendor" / "vggt_omega"))
    from vggt_omega.models.vggt_omega import VGGTOmega

    model = VGGTOmega()

    if ckpt_path:
        state = torch.load(ckpt_path, map_location="cpu", weights_only=True)
        model.load_state_dict(state)
        print(f"Loaded checkpoint from {ckpt_path}")
    else:
        from huggingface_hub import hf_hub_download
        weights_path = hf_hub_download(repo_id=model_id, filename="model.pt")
        state = torch.load(weights_path, map_location="cpu", weights_only=True)
        model.load_state_dict(state)
        print(f"Loaded checkpoint from HF: {model_id}")

    return model.to(device).eval()


# ── inference over one window ─────────────────────────────────────────────────


@torch.no_grad()
def run_window(
    model: torch.nn.Module,
    frame_paths: list[Path],
    resolution: int,
    device: torch.device,
) -> dict:
    from vggt_omega.utils.load_fn import load_and_preprocess_images
    from vggt_omega.utils.pose_enc import encoding_to_camera

    images = load_and_preprocess_images(
        [str(p) for p in frame_paths],
        mode="balanced",
        image_resolution=resolution,
    )  # [N, 3, H, W]

    images = images.unsqueeze(0).to(device)  # [1, N, 3, H, W]
    predictions = model(images)

    H, W = images.shape[-2], images.shape[-1]
    extrinsics, intrinsics = encoding_to_camera(
        predictions["pose_enc"], image_size_hw=(H, W)
    )  # [1, N, 3, 4], [1, N, 3, 3]

    return {
        "images": images[0].cpu(),          # [N, 3, H, W]
        "depth": predictions["depth"][0].squeeze(-1).cpu(),  # [N, H, W]
        "depth_conf": predictions["depth_conf"][0].cpu(),  # [N, H, W]
        "extrinsics": extrinsics[0].cpu(),  # [N, 3, 4]
        "intrinsics": intrinsics[0].cpu(),  # [N, 3, 3]
    }


# ── main ──────────────────────────────────────────────────────────────────────


def main() -> None:
    parser = argparse.ArgumentParser(description="VGGT-Omega 4D point cloud inference")
    parser.add_argument("--frames", required=True, help="Directory of frame images (sorted alphabetically = temporal order)")
    parser.add_argument("--ckpt", default=None, help="Path to local model.pt (skips HF download)")
    parser.add_argument("--model", default="facebook/VGGT-Omega-1B-512", help="HF model ID if --ckpt not given")
    parser.add_argument("--out", required=True, help="Output directory")
    parser.add_argument("--resolution", type=int, default=512, help="Token resolution passed to preprocessor")
    parser.add_argument("--conf-thresh", type=float, default=0.5, help="Minimum depth confidence to include a point")
    parser.add_argument("--window", type=int, default=50, help="Max frames per inference window (VRAM budget)")
    parser.add_argument("--stride", type=int, default=None, help="Window stride (defaults to window size, i.e. no overlap)")
    parser.add_argument("--ply", action="store_true", help="Also write PLY files alongside NPZ")
    parser.add_argument("--align-windows", action="store_true", help="Align consecutive windows via shared anchor frame")
    parser.add_argument("--device", default="cuda" if torch.cuda.is_available() else "cpu")
    args = parser.parse_args()

    frame_dir = Path(args.frames)
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    exts = {".jpg", ".jpeg", ".png", ".webp"}
    all_frames = sorted(p for p in frame_dir.iterdir() if p.suffix.lower() in exts)
    if not all_frames:
        raise ValueError(f"No images found in {frame_dir}")
    print(f"Found {len(all_frames)} frames in {frame_dir}")

    device = torch.device(args.device)
    model = load_model(args.ckpt, args.model, device)

    stride = args.stride or args.window
    windows: list[list[Path]] = []
    i = 0
    while i < len(all_frames):
        windows.append(all_frames[i : i + args.window])
        i += stride

    all_xyz: dict[int, np.ndarray] = {}
    all_rgb: dict[int, np.ndarray] = {}
    all_depth: dict[int, np.ndarray] = {}
    all_conf: dict[int, np.ndarray] = {}
    all_extrinsics: list[np.ndarray] = [None] * len(all_frames)
    all_intrinsics: list[np.ndarray] = [None] * len(all_frames)

    # anchor_T: 4x4 world-space transform that maps this window's frame-0 into
    # the global coordinate frame (identity for first window).
    anchor_T = np.eye(4, dtype=np.float32)

    for w_idx, window_frames in enumerate(windows):
        print(f"Window {w_idx + 1}/{len(windows)}: frames {all_frames.index(window_frames[0])}–{all_frames.index(window_frames[-1])}")
        result = run_window(model, window_frames, args.resolution, device)

        N = len(window_frames)
        for local_i in range(N):
            global_i = all_frames.index(window_frames[local_i])

            extri = result["extrinsics"][local_i]  # [3, 4]  camera-from-world
            intri = result["intrinsics"][local_i]  # [3, 3]

            if args.align_windows and w_idx > 0:
                # Compose: cam-from-local-world × local-world-from-global-world
                extri_4x4 = torch.eye(4)
                extri_4x4[:3] = extri
                aligned = extri_4x4.numpy() @ np.linalg.inv(anchor_T)
                extri = torch.from_numpy(aligned[:3])

            pc = depth_to_pointcloud(
                result["depth"][local_i],
                intri,
                extri,
                conf=result["depth_conf"][local_i],
                conf_thresh=args.conf_thresh,
            )

            rgb_flat = result["images"][local_i].permute(1, 2, 0).reshape(-1, 3)  # [H*W, 3]
            rgb_masked = rgb_flat[pc["mask"]].numpy()

            all_xyz[global_i] = pc["xyz"]
            all_rgb[global_i] = rgb_masked
            all_depth[global_i] = pc["depth"]
            all_conf[global_i] = pc["conf"]
            all_extrinsics[global_i] = extri.numpy()
            all_intrinsics[global_i] = intri.numpy()

        if args.align_windows:
            # Update anchor: the last frame of this window becomes the anchor for the next.
            last_extri = result["extrinsics"][-1]  # [3, 4]
            last_4x4 = np.eye(4, dtype=np.float32)
            last_4x4[:3] = last_extri.numpy()
            anchor_T = anchor_T @ np.linalg.inv(last_4x4)

    # Save per-frame outputs
    for i in range(len(all_frames)):
        if i not in all_xyz:
            continue
        frame_name = f"frame_{i:04d}"
        npz_path = out_dir / f"{frame_name}.npz"
        save_npz(npz_path, all_xyz[i], all_rgb[i], all_depth[i], all_conf[i])
        if args.ply:
            save_ply(out_dir / f"{frame_name}.ply", all_xyz[i], all_rgb[i])

    # Save camera track
    np.savez_compressed(
        out_dir / "cameras.npz",
        extrinsics=np.stack([e for e in all_extrinsics if e is not None]),
        intrinsics=np.stack([k for k in all_intrinsics if k is not None]),
        frame_paths=[str(p) for p in all_frames],
    )

    print(f"\nDone. Saved {len(all_xyz)} point clouds to {out_dir}")
    print(f"  NPZ keys per frame: xyz [N,3], rgb [N,3], depth [H,W], conf [H,W]")
    print(f"  cameras.npz: extrinsics [T,3,4], intrinsics [T,3,3]")


if __name__ == "__main__":
    main()
