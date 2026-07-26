"""
Minimal VGGT-Omega video inference.

Input:
    A video file.

Output:
    One depth-unprojected point cloud per sampled frame, plus the predicted
    camera trajectory. Point clouds are derived only from model outputs:
    predicted depth, depth confidence, intrinsics, extrinsics, and RGB frames.

Example:
    python scripts/infer_vggt_omega_video.py \
        --video examples/clip.mp4 \
        --ckpt checkpoints/vggt_omega/vggt_omega_1b_512.pt \
        --out outputs/clip \
        --ply
"""

from __future__ import annotations

import argparse
import json
import shutil
import struct
import sys
import tempfile
from pathlib import Path

import numpy as np
from PIL import Image


def _import_torch():
    try:
        import torch
    except ImportError as exc:
        raise RuntimeError("Install VGGT-Omega dependencies, including torch, before running inference.") from exc
    return torch


def _import_cv2():
    try:
        import cv2
    except ImportError as exc:
        raise RuntimeError("Install opencv-python to read videos.") from exc
    return cv2


def extract_video_frames(
    video_path: Path,
    frame_dir: Path,
    every_n: int,
    max_frames: int | None,
) -> tuple[list[Path], list[int], list[float]]:
    cv2 = _import_cv2()

    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        raise ValueError(f"Could not open video: {video_path}")

    fps = float(cap.get(cv2.CAP_PROP_FPS) or 0.0)
    frame_paths: list[Path] = []
    frame_indices: list[int] = []
    timestamps_sec: list[float] = []

    frame_idx = 0
    kept_idx = 0
    while True:
        ok, bgr = cap.read()
        if not ok:
            break
        if frame_idx % every_n == 0:
            rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
            path = frame_dir / f"frame_{kept_idx:06d}.png"
            Image.fromarray(rgb).save(path)
            frame_paths.append(path)
            frame_indices.append(frame_idx)
            timestamps_sec.append(frame_idx / fps if fps > 0 else float("nan"))
            kept_idx += 1
            if max_frames is not None and kept_idx >= max_frames:
                break
        frame_idx += 1

    cap.release()
    if not frame_paths:
        raise ValueError(f"No frames sampled from {video_path}")
    return frame_paths, frame_indices, timestamps_sec


def load_model(
    ckpt_path: str | None,
    model_id: str,
    device,
    model_filename: str = "vggt_omega_1b_512.pt",
):
    torch = _import_torch()

    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "vendor" / "vggt_omega"))
    from vggt_omega.models import VGGTOmega

    model = VGGTOmega()
    if ckpt_path:
        state = torch.load(ckpt_path, map_location="cpu", weights_only=True)
    else:
        from huggingface_hub import hf_hub_download

        weights_path = hf_hub_download(repo_id=model_id, filename=model_filename)
        state = torch.load(weights_path, map_location="cpu", weights_only=True)

    model.load_state_dict(state)
    return model.to(device).eval()


def camera_to_world(extrinsic: np.ndarray) -> np.ndarray:
    world_to_camera = np.eye(4, dtype=np.float32)
    world_to_camera[:3, :4] = extrinsic.astype(np.float32)
    return np.linalg.inv(world_to_camera).astype(np.float32)


def depth_to_pointcloud(depth, intrinsics, extrinsics, conf=None, conf_thresh: float = 0.0):
    torch = _import_torch()

    height, width = depth.shape
    device = depth.device
    dtype = torch.float32

    depth = depth.to(dtype)
    intrinsics = intrinsics.to(dtype)
    extrinsics = extrinsics.to(dtype)

    fx, fy = intrinsics[0, 0], intrinsics[1, 1]
    cx, cy = intrinsics[0, 2], intrinsics[1, 2]

    us = torch.arange(width, device=device, dtype=dtype)
    vs = torch.arange(height, device=device, dtype=dtype)
    grid_v, grid_u = torch.meshgrid(vs, us, indexing="ij")

    x_cam = (grid_u - cx) / fx * depth
    y_cam = (grid_v - cy) / fy * depth
    z_cam = depth
    pts_cam = torch.stack([x_cam, y_cam, z_cam], dim=-1).reshape(-1, 3)

    rotation = extrinsics[:3, :3]
    translation = extrinsics[:3, 3]
    pts_world = (pts_cam - translation) @ rotation

    valid = depth.reshape(-1) > 0
    if conf is not None and conf_thresh > 0:
        valid = valid & (conf.to(dtype).reshape(-1) >= conf_thresh)

    uv = torch.stack([grid_u, grid_v], dim=-1).reshape(-1, 2)
    flat_indices = torch.arange(height * width, device=device)

    return {
        "xyz": pts_world[valid].cpu().numpy().astype(np.float32),
        "uv": uv[valid].cpu().numpy().astype(np.float32),
        "flat_indices": flat_indices[valid].cpu().numpy().astype(np.int64),
        "valid_mask": valid.cpu().numpy(),
        "depth": depth.cpu().numpy().astype(np.float32),
        "conf": conf.cpu().numpy().astype(np.float32) if conf is not None else None,
    }


def save_ply(path: Path, xyz: np.ndarray, rgb: np.ndarray) -> None:
    rgb_u8 = (rgb * 255).clip(0, 255).astype(np.uint8)
    header = (
        "ply\nformat binary_little_endian 1.0\n"
        f"element vertex {len(xyz)}\n"
        "property float x\nproperty float y\nproperty float z\n"
        "property uchar red\nproperty uchar green\nproperty uchar blue\n"
        "end_header\n"
    )
    with open(path, "wb") as handle:
        handle.write(header.encode("ascii"))
        for point, color in zip(xyz, rgb_u8):
            handle.write(struct.pack("<fffBBB", point[0], point[1], point[2], color[0], color[1], color[2]))


def find_segmentation_path(seg_dir: Path, frame_stem: str) -> Path | None:
    candidates = [
        seg_dir / f"{frame_stem}.png",
        seg_dir / f"{frame_stem}.jpg",
        seg_dir / f"{frame_stem}.jpeg",
        seg_dir / f"{frame_stem}.npy",
    ]
    return next((path for path in candidates if path.exists()), None)


def load_segmentation_map(path: Path) -> np.ndarray:
    if path.suffix == ".npy":
        return np.load(path)
    return np.asarray(Image.open(path))


def preprocess_segmentation_maps(seg_paths: list[Path], mode: str, image_resolution: int) -> list[np.ndarray]:
    from vggt_omega.utils.load_fn import (
        _balanced_target_shape,
        _crop_to_supported_aspect_ratio,
        _max_size_target_shape,
        _pad_images_to_common_size,
    )

    import torch

    labels: list[np.ndarray] = []
    tensors = []
    shapes = set()

    for path in seg_paths:
        image = Image.fromarray(load_segmentation_map(path)) if path.suffix == ".npy" else Image.open(path)

        image = _crop_to_supported_aspect_ratio(image)
        width, height = image.size
        aspect_ratio = height / max(width, 1)
        if mode == "balanced":
            target_h, target_w = _balanced_target_shape(aspect_ratio, image_resolution, patch_size=16)
        else:
            target_h, target_w = _max_size_target_shape(aspect_ratio, image_resolution, patch_size=16)

        resized = np.asarray(image.resize((target_w, target_h), Image.Resampling.NEAREST))
        if resized.ndim == 2:
            tensor = torch.from_numpy(resized[None])
        else:
            tensor = torch.from_numpy(np.moveaxis(resized, -1, 0))
        tensors.append(tensor)
        shapes.add((target_h, target_w))

    if len(shapes) > 1:
        tensors = _pad_images_to_common_size(tensors, shapes)

    for tensor in tensors:
        array = tensor.numpy()
        if array.shape[0] == 1:
            labels.append(array[0])
        else:
            labels.append(np.moveaxis(array, 0, -1))
    return labels


def gather_segmentation_labels(
    seg_dir: Path | None,
    frame_stem: str,
    flat_indices: np.ndarray,
    image_size_hw: tuple[int, int],
    preprocessed_segmentation: np.ndarray | None,
) -> np.ndarray | None:
    labels = preprocessed_segmentation
    if labels is None and seg_dir is not None:
        path = find_segmentation_path(seg_dir, frame_stem)
        if path is None:
            return None
        labels = load_segmentation_map(path)

    if labels is None:
        return None

    if labels.shape[:2] != image_size_hw:
        raise ValueError(
            f"Segmentation map for {frame_stem} has shape {labels.shape[:2]}, "
            f"but VGGT-Omega output pixels are {image_size_hw}. "
            "Remove --seg-preprocessed to crop/resize labels like the RGB frames, "
            "or provide maps that already match the preprocessed output."
        )

    if labels.ndim == 2:
        return labels.reshape(-1)[flat_indices]
    return labels.reshape(-1, labels.shape[-1])[flat_indices]


def run_vggt_window(model, frame_paths: list[Path], resolution: int, mode: str, device):
    torch = _import_torch()

    from vggt_omega.utils.load_fn import load_and_preprocess_images
    from vggt_omega.utils.pose_enc import encoding_to_camera

    images = load_and_preprocess_images(
        [str(path) for path in frame_paths],
        mode=mode,
        image_resolution=resolution,
    ).unsqueeze(0)
    images = images.to(device)

    with torch.inference_mode():
        predictions = model(images)

    height, width = images.shape[-2:]
    extrinsics, intrinsics = encoding_to_camera(predictions["pose_enc"], image_size_hw=(height, width))

    return {
        "images": images[0].detach().cpu(),
        "depth": predictions["depth"][0].squeeze(-1).detach().cpu(),
        "depth_conf": predictions["depth_conf"][0].detach().cpu(),
        "extrinsics": extrinsics[0].detach().cpu(),
        "intrinsics": intrinsics[0].detach().cpu(),
        "image_size": (height, width),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Run VGGT-Omega on a video and save per-frame point clouds.")
    parser.add_argument("--video", required=True, help="Input video path.")
    parser.add_argument("--out", required=True, help="Output directory.")
    parser.add_argument("--ckpt", default=None, help="Local VGGT-Omega checkpoint. If omitted, --model is downloaded.")
    parser.add_argument("--model", default="facebook/VGGT-Omega-1B-512", help="Hugging Face model ID used when --ckpt is omitted.")
    parser.add_argument("--resolution", type=int, default=512, help="VGGT-Omega preprocessing resolution.")
    parser.add_argument("--preprocess-mode", choices=["balanced", "max_size"], default="balanced")
    parser.add_argument("--every-n", type=int, default=1, help="Sample every Nth video frame.")
    parser.add_argument("--max-frames", type=int, default=None, help="Optional cap on sampled frames.")
    parser.add_argument("--conf-thresh", type=float, default=0.5, help="Minimum depth confidence for exported points.")
    parser.add_argument(
        "--window",
        type=int,
        default=0,
        help="Frames per model call. The default 0 runs all sampled frames together for one coherent trajectory.",
    )
    parser.add_argument("--seg-video", default=None, help="Optional segmentation video aligned to --video.")
    parser.add_argument("--seg-dir", default=None, help="Optional segmentation maps named like extracted frames.")
    parser.add_argument(
        "--seg-preprocessed",
        action="store_true",
        help="Treat --seg-dir maps as already matching VGGT output pixels; otherwise crop/resize them like RGB.",
    )
    parser.add_argument("--ply", action="store_true", help="Also export binary PLY point clouds.")
    parser.add_argument("--keep-frames", action="store_true", help="Keep extracted RGB frames under the output directory.")
    parser.add_argument("--device", default=None, help="Torch device. Defaults to cuda when available.")
    args = parser.parse_args()

    if args.every_n < 1:
        raise ValueError("--every-n must be >= 1")
    if args.window < 0:
        raise ValueError("--window must be >= 0")

    torch = _import_torch()
    device = torch.device(args.device or ("cuda" if torch.cuda.is_available() else "cpu"))

    out_dir = Path(args.out)
    pointcloud_dir = out_dir / "pointclouds"
    frames_dir = out_dir / "frames"
    pointcloud_dir.mkdir(parents=True, exist_ok=True)
    frames_dir.mkdir(parents=True, exist_ok=True)

    temp_dir = None
    temp_seg_dir = None
    if args.keep_frames:
        extract_dir = frames_dir
    else:
        temp_dir = Path(tempfile.mkdtemp(prefix="vggt_omega_frames_"))
        extract_dir = temp_dir

    try:
        frame_paths, frame_indices, timestamps_sec = extract_video_frames(
            Path(args.video),
            extract_dir,
            every_n=args.every_n,
            max_frames=args.max_frames,
        )
        seg_frame_paths: list[Path] | None = None
        if args.seg_video is not None:
            temp_seg_dir = Path(tempfile.mkdtemp(prefix="vggt_omega_seg_"))
            seg_frame_paths, _, _ = extract_video_frames(
                Path(args.seg_video),
                temp_seg_dir,
                every_n=args.every_n,
                max_frames=args.max_frames,
            )
            if len(seg_frame_paths) != len(frame_paths):
                raise ValueError("--seg-video produced a different number of sampled frames than --video")

        model = load_model(args.ckpt, args.model, device)

        extrinsics_out: list[np.ndarray] = []
        intrinsics_out: list[np.ndarray] = []
        cam_to_world_out: list[np.ndarray] = []
        centers_out: list[np.ndarray] = []
        image_sizes: list[tuple[int, int]] = []
        saved_frames: list[str] = []

        window_size = args.window or len(frame_paths)
        for start in range(0, len(frame_paths), window_size):
            end = min(start + window_size, len(frame_paths))
            window_paths = frame_paths[start:end]
            result = run_vggt_window(model, window_paths, args.resolution, args.preprocess_mode, device)
            image_sizes.extend([result["image_size"]] * len(window_paths))
            window_segmentation: list[np.ndarray | None] = [None] * len(window_paths)

            if seg_frame_paths is not None:
                window_segmentation = preprocess_segmentation_maps(
                    seg_frame_paths[start:end],
                    args.preprocess_mode,
                    args.resolution,
                )
            elif args.seg_dir is not None and not args.seg_preprocessed:
                seg_paths = []
                for frame_path in window_paths:
                    seg_path = find_segmentation_path(Path(args.seg_dir), frame_path.stem)
                    if seg_path is None:
                        raise ValueError(f"No segmentation map found for {frame_path.stem} in {args.seg_dir}")
                    seg_paths.append(seg_path)
                window_segmentation = preprocess_segmentation_maps(
                    seg_paths,
                    args.preprocess_mode,
                    args.resolution,
                )

            for local_idx, frame_path in enumerate(window_paths):
                global_idx = start + local_idx
                pc = depth_to_pointcloud(
                    result["depth"][local_idx],
                    result["intrinsics"][local_idx],
                    result["extrinsics"][local_idx],
                    conf=result["depth_conf"][local_idx],
                    conf_thresh=args.conf_thresh,
                )
                rgb = result["images"][local_idx].permute(1, 2, 0).reshape(-1, 3).numpy()[pc["valid_mask"]]

                arrays = {
                    "xyz": pc["xyz"],
                    "rgb": rgb.astype(np.float32),
                    "uv": pc["uv"],
                    "flat_indices": pc["flat_indices"],
                    "depth": pc["depth"],
                }
                if pc["conf"] is not None:
                    arrays["conf"] = pc["conf"]

                if args.seg_dir is not None:
                    labels = gather_segmentation_labels(
                        Path(args.seg_dir),
                        frame_path.stem,
                        pc["flat_indices"],
                        result["image_size"],
                        window_segmentation[local_idx],
                    )
                    if labels is not None:
                        arrays["segmentation"] = labels
                elif seg_frame_paths is not None:
                    labels = gather_segmentation_labels(
                        None,
                        frame_path.stem,
                        pc["flat_indices"],
                        result["image_size"],
                        window_segmentation[local_idx],
                    )
                    if labels is not None:
                        arrays["segmentation"] = labels

                frame_name = f"frame_{global_idx:06d}"
                np.savez_compressed(pointcloud_dir / f"{frame_name}.npz", **arrays)
                if args.ply:
                    save_ply(pointcloud_dir / f"{frame_name}.ply", arrays["xyz"], arrays["rgb"])

                extrinsic = result["extrinsics"][local_idx].numpy().astype(np.float32)
                intrinsic = result["intrinsics"][local_idx].numpy().astype(np.float32)
                c2w = camera_to_world(extrinsic)
                extrinsics_out.append(extrinsic)
                intrinsics_out.append(intrinsic)
                cam_to_world_out.append(c2w)
                centers_out.append(c2w[:3, 3])
                saved_frames.append(str(frame_path if args.keep_frames else Path(args.video)))

        np.savez_compressed(
            out_dir / "camera_trajectory.npz",
            extrinsics=np.stack(extrinsics_out),
            intrinsics=np.stack(intrinsics_out),
            camera_to_world=np.stack(cam_to_world_out),
            camera_centers=np.stack(centers_out),
            source_frame_indices=np.asarray(frame_indices, dtype=np.int64),
            timestamps_sec=np.asarray(timestamps_sec, dtype=np.float32),
        )

        metadata = {
            "video": str(Path(args.video)),
            "num_frames": len(frame_paths),
            "frame_indices": frame_indices,
            "timestamps_sec": timestamps_sec,
            "image_sizes_hw": image_sizes,
            "preprocess_mode": args.preprocess_mode,
            "resolution": args.resolution,
            "window": window_size,
            "trajectory_scope": (
                "single coherent model world frame"
                if window_size >= len(frame_paths)
                else "per-window model world frames; increase --window for a single coherent trajectory"
            ),
            "conf_thresh": args.conf_thresh,
            "segmentation_source": args.seg_video or args.seg_dir,
            "segmentation_preprocessed": args.seg_preprocessed,
            "pointcloud_npz_keys": ["xyz", "rgb", "uv", "flat_indices", "depth", "conf", "segmentation"],
            "pixel_space": "uv and flat_indices refer to VGGT-Omega's preprocessed output image, not the original video frame",
            "camera_convention": "extrinsics are camera-from-world [R|t], OpenCV coordinates",
        }
        (out_dir / "metadata.json").write_text(json.dumps(metadata, indent=2), encoding="utf-8")
    finally:
        if temp_dir is not None:
            shutil.rmtree(temp_dir, ignore_errors=True)
        if temp_seg_dir is not None:
            shutil.rmtree(temp_seg_dir, ignore_errors=True)

    print(f"Saved {len(frame_paths)} point clouds to {pointcloud_dir}")
    print(f"Saved camera trajectory to {out_dir / 'camera_trajectory.npz'}")


if __name__ == "__main__":
    main()
