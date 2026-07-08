"""
Evaluate VGGT-Omega depth estimation on Virtual KITTI 2.

Depth metrics (AbsRel, RMSE, δ₁/₂/₃) are computed against the GT depth maps,
both raw (metric) and after per-sequence median scale alignment.

Point clouds are saved as NPZ (xyz, rgb, depth, conf) for the first frame of
each evaluated sequence.

Virtual KITTI 2 depth images are uint16, values in centimetres, 65535 = invalid.
The RGB images (1242×375) are wide-AR; VGGT-Omega's preprocessor centre-crops
them to ≤2:1 before resizing — we apply the same crop to GT depth for a
pixel-exact comparison.

Usage
-----
    python scripts/eval_depth_vkitti2.py \\
        --data   data/vkitti2 \\
        --ckpt   vendor/checkpoints/vggt_omega/vggt_omega_1b_512.pt \\
        --out    outputs/vkitti2_eval \\
        [--scenes Scene01 Scene02 Scene06 Scene18 Scene20] \\
        [--variants clone] \\
        [--max-frames 50] \\
        [--window 10] \\
        [--resolution 512] \\
        [--conf-thresh 0.5] \\
        [--device cuda]
"""

from __future__ import annotations

import argparse
import json
import struct
import sys
import warnings
from pathlib import Path

import numpy as np
import torch
import torch.nn.functional as F
from PIL import Image

# ── vkitti2 helpers ────────────────────────────────────────────────────────────

VKITTI2_DEPTH_SCALE = 100.0   # uint16 cm → divide by 100 for metres
VKITTI2_INVALID = 65535        # sentinel value = no valid depth
MAX_DEPTH_EVAL = 80.0          # cap very far pixels (standard KITTI cap)


def load_gt_depth(path: Path) -> np.ndarray:
    """Load a vkitti2 depth PNG → float32 array in metres (0 = invalid)."""
    raw = np.array(Image.open(path), dtype=np.float32)
    depth = raw / VKITTI2_DEPTH_SCALE
    depth[raw >= VKITTI2_INVALID] = 0.0
    return depth  # [H, W]


# ── aspect-ratio crop (mirrors vggt_omega/utils/load_fn.py) ───────────────────

def compute_ar_crop(h: int, w: int, min_ar: float = 0.5, max_ar: float = 2.0):
    """Return (left, top, right, bottom) crop box that keeps AR in [min_ar, max_ar]."""
    ar = h / max(w, 1)
    if ar < min_ar:
        crop_w = max(1, int(round(h / min_ar)))
        left = max((w - crop_w) // 2, 0)
        return (left, 0, left + crop_w, h)
    if ar > max_ar:
        crop_h = max(1, int(round(w * max_ar)))
        top = max((h - crop_h) // 2, 0)
        return (0, top, w, top + crop_h)
    return (0, 0, w, h)


def balanced_target_shape(ar: float, resolution: int, patch_size: int = 16):
    """Mirrors _balanced_target_shape in load_fn.py."""
    tokens = (resolution // patch_size) ** 2
    w_patches = max(1, int(round(np.sqrt(tokens / ar))))
    h_patches = max(1, int(round(tokens / w_patches)))
    return h_patches * patch_size, w_patches * patch_size


# ── depth metrics ──────────────────────────────────────────────────────────────

def compute_depth_metrics(pred: np.ndarray, gt: np.ndarray, scale_align: bool = False):
    """Compute standard monocular depth metrics on valid pixels.

    pred, gt: float32 arrays, same shape. gt == 0 → invalid.
    Returns dict of float scalars.
    """
    mask = (gt > 0) & (gt < MAX_DEPTH_EVAL) & (pred > 0)
    if mask.sum() == 0:
        return None

    p = pred[mask]
    g = gt[mask]

    if scale_align:
        scale = np.median(g) / (np.median(p) + 1e-8)
        p = p * scale

    p = np.clip(p, 1e-3, None)
    g = np.clip(g, 1e-3, None)

    thresh = np.maximum(p / g, g / p)
    d1 = (thresh < 1.25).mean()
    d2 = (thresh < 1.25 ** 2).mean()
    d3 = (thresh < 1.25 ** 3).mean()

    abs_rel = np.mean(np.abs(p - g) / g)
    sq_rel  = np.mean((p - g) ** 2 / g)
    rmse    = np.sqrt(np.mean((p - g) ** 2))
    rmse_log = np.sqrt(np.mean((np.log(p) - np.log(g)) ** 2))

    return {
        "abs_rel": float(abs_rel),
        "sq_rel":  float(sq_rel),
        "rmse":    float(rmse),
        "rmse_log": float(rmse_log),
        "d1":      float(d1),
        "d2":      float(d2),
        "d3":      float(d3),
        "n_valid": int(mask.sum()),
    }


def average_metrics(records: list[dict]) -> dict:
    if not records:
        return {}
    keys = [k for k in records[0] if k != "n_valid"]
    return {k: float(np.mean([r[k] for r in records])) for k in keys}


# ── visualization ──────────────────────────────────────────────────────────────

def colorize_depth(depth: np.ndarray, vmin: float | None = None, vmax: float | None = None) -> np.ndarray:
    """depth [H,W] float → uint8 RGB [H,W,3] via plasma colormap. 0 = invalid → gray."""
    try:
        import matplotlib.cm as cm
    except ImportError:
        d = np.clip(depth, 0, None)
        if d.max() > 0:
            d = (d / d.max() * 255).astype(np.uint8)
        return np.stack([d, d, d], axis=-1)
    d = depth.copy().astype(np.float64)
    valid = d > 0
    vmin = vmin if vmin is not None else (float(np.percentile(d[valid], 5)) if valid.any() else 0)
    vmax = vmax if vmax is not None else (float(np.percentile(d[valid], 95)) if valid.any() else 1)
    d = np.clip((d - vmin) / max(vmax - vmin, 1e-8), 0, 1)
    rgb = (cm.plasma(d)[:, :, :3] * 255).astype(np.uint8)
    rgb[~valid] = 128
    return rgb


def save_comparison_image(path: Path, rgb_hw3: np.ndarray, pred: np.ndarray,
                          gt: np.ndarray, scale: float) -> None:
    """4-panel image: RGB | pred depth (scaled) | GT depth | abs error."""
    from PIL import ImageDraw
    H, W = rgb_hw3.shape[:2]
    valid_gt = gt[gt > 0]
    vmin = float(np.percentile(valid_gt, 2)) if len(valid_gt) else 0
    vmax = float(np.percentile(valid_gt, 98)) if len(valid_gt) else 1
    pred_col = colorize_depth(pred * scale, vmin, vmax)
    gt_col   = colorize_depth(gt,           vmin, vmax)
    err = np.zeros_like(gt)
    valid = (gt > 0) & (pred > 0)
    err[valid] = np.abs(pred[valid] * scale - gt[valid])
    err_vmax = float(np.percentile(err[valid], 95)) if valid.any() else 1
    err_col  = colorize_depth(err, 0, err_vmax)
    labels = ["RGB input", "Predicted depth", "GT depth", "|pred − GT|"]
    panels = [Image.fromarray(rgb_hw3), Image.fromarray(pred_col),
              Image.fromarray(gt_col),  Image.fromarray(err_col)]
    combined = Image.new("RGB", (W * 4, H + 20), (20, 20, 20))
    draw = ImageDraw.Draw(combined)
    for idx, (pil, label) in enumerate(zip(panels, labels)):
        combined.paste(pil, (idx * W, 20))
        draw.text((idx * W + 4, 2), label, fill=(240, 240, 240))
    path.parent.mkdir(parents=True, exist_ok=True)
    combined.save(path)


# ── point cloud helpers ────────────────────────────────────────────────────────

def depth_to_pointcloud(
    depth: torch.Tensor,
    intrinsics: torch.Tensor,
    extrinsics: torch.Tensor,
    conf: torch.Tensor | None = None,
    conf_thresh: float = 0.0,
) -> dict:
    """Unproject depth → world-space XYZ.  Extrinsics: camera-from-world [3,4]."""
    H, W = depth.shape
    device = depth.device
    depth = depth.float()
    intrinsics = intrinsics.float()
    extrinsics = extrinsics.float()

    fx, fy = intrinsics[0, 0], intrinsics[1, 1]
    cx, cy = intrinsics[0, 2], intrinsics[1, 2]

    us = torch.arange(W, device=device, dtype=torch.float32)
    vs = torch.arange(H, device=device, dtype=torch.float32)
    gv, gu = torch.meshgrid(vs, us, indexing="ij")

    pts_cam = torch.stack(
        [(gu - cx) / fx * depth, (gv - cy) / fy * depth, depth], dim=-1
    ).reshape(-1, 3)

    R = extrinsics[:3, :3]
    t = extrinsics[:3,  3]
    pts_world = (pts_cam - t) @ R   # R^T @ (X_cam - t) as row-vectors

    mask = depth.reshape(-1) > 0
    if conf is not None and conf_thresh > 0:
        mask = mask & (conf.float().reshape(-1) >= conf_thresh)

    return {
        "xyz":  pts_world[mask].cpu().numpy().astype(np.float32),
        "mask": mask.cpu().numpy(),
        "depth": depth.cpu().numpy().astype(np.float32),
        "conf": conf.cpu().numpy().astype(np.float32) if conf is not None else None,
    }


def save_npz(path: Path, xyz, rgb, depth, conf):
    d = {"xyz": xyz, "rgb": rgb, "depth": depth}
    if conf is not None:
        d["conf"] = conf
    np.savez_compressed(path, **d)


def save_ply(path: Path, xyz: np.ndarray, rgb: np.ndarray):
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


# ── model loading ──────────────────────────────────────────────────────────────

def load_model(ckpt_path: str, device: torch.device):
    sys.path.insert(0, str(Path(__file__).parent.parent / "vendor" / "vggt_omega"))
    from vggt_omega.models.vggt_omega import VGGTOmega
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        model = VGGTOmega()
    state = torch.load(ckpt_path, map_location="cpu", weights_only=True)
    model.load_state_dict(state)
    print(f"Loaded checkpoint: {ckpt_path}")
    return model.to(device).eval()


# ── windowed inference ─────────────────────────────────────────────────────────

@torch.no_grad()
def run_window(model, frame_paths: list[Path], resolution: int, device: torch.device):
    from vggt_omega.utils.load_fn import load_and_preprocess_images
    from vggt_omega.utils.pose_enc import encoding_to_camera

    images = load_and_preprocess_images(
        [str(p) for p in frame_paths],
        mode="balanced",
        image_resolution=resolution,
    )  # [N, 3, H_model, W_model]

    images = images.unsqueeze(0).to(device)   # [1, N, 3, H, W]
    predictions = model(images)

    H, W = images.shape[-2], images.shape[-1]
    extrinsics, intrinsics = encoding_to_camera(
        predictions["pose_enc"], image_size_hw=(H, W)
    )  # [1,N,3,4] , [1,N,3,3]

    return {
        "images":     images[0].cpu(),                         # [N,3,H,W]
        "depth":      predictions["depth"][0].squeeze(-1).cpu(),  # [N,H,W]
        "depth_conf": predictions["depth_conf"][0].cpu(),      # [N,H,W]
        "extrinsics": extrinsics[0].cpu(),                     # [N,3,4]
        "intrinsics": intrinsics[0].cpu(),                     # [N,3,3]
        "model_hw":   (H, W),
    }


# ── per-sequence evaluation ────────────────────────────────────────────────────

def eval_sequence(
    model,
    rgb_dir: Path,
    depth_dir: Path,
    out_dir: Path,
    seq_name: str,
    resolution: int,
    window: int,
    conf_thresh: float,
    device: torch.device,
    save_ply_files: bool = False,
) -> dict:
    """Run inference + depth eval on one Scene/variant sequence (Camera_0 only)."""
    exts = {".jpg", ".jpeg", ".png"}
    rgb_frames = sorted(p for p in rgb_dir.iterdir() if p.suffix.lower() in exts)
    if not rgb_frames:
        print(f"  [skip] no RGB frames in {rgb_dir}")
        return {}

    # precompute crop box from the first image so GT depth can be cropped identically
    with Image.open(rgb_frames[0]) as im:
        W_orig, H_orig = im.size   # PIL gives (width, height)
    crop_box = compute_ar_crop(H_orig, W_orig)           # (left, top, right, bottom)
    H_crop = crop_box[3] - crop_box[1]
    W_crop = crop_box[2] - crop_box[0]
    ar_crop = H_crop / max(W_crop, 1)
    H_model, W_model = balanced_target_shape(ar_crop, resolution)

    print(f"  {seq_name}: {len(rgb_frames)} frames  "
          f"orig {H_orig}×{W_orig} → crop {H_crop}×{W_crop} → model {H_model}×{W_model}")

    # sliding windows
    windows: list[list[Path]] = []
    i = 0
    while i < len(rgb_frames):
        windows.append(rgb_frames[i: i + window])
        i += window

    raw_metrics: list[dict]   = []
    aligned_metrics: list[dict] = []
    saved_pc = False
    out_dir.mkdir(parents=True, exist_ok=True)

    for w_idx, win_frames in enumerate(windows):
        result = run_window(model, win_frames, resolution, device)
        N_win = len(win_frames)

        for local_i in range(N_win):
            frame_name = win_frames[local_i].stem   # e.g. rgb_00000
            # infer the matching GT depth name: depth_00000.png
            gt_name = frame_name.replace("rgb_", "depth_") + ".png"
            gt_path = depth_dir / gt_name
            if not gt_path.exists():
                continue

            pred_depth_hw = result["depth"][local_i]   # [H_model, W_model] torch
            conf_hw       = result["depth_conf"][local_i]

            # ── align GT depth to model output resolution ──────────────────
            gt_full = load_gt_depth(gt_path)           # [H_orig, W_orig] float32
            # apply same crop as model
            gt_crop = gt_full[
                crop_box[1]: crop_box[3],
                crop_box[0]: crop_box[2],
            ]  # [H_crop, W_crop]
            # resize to model HW using nearest neighbour (preserves valid-pixel mask)
            gt_t = torch.from_numpy(gt_crop).unsqueeze(0).unsqueeze(0)  # [1,1,H_crop,W_crop]
            gt_resized = F.interpolate(gt_t, size=(H_model, W_model), mode="nearest")[0, 0]
            gt_np = gt_resized.numpy()

            pred_np = pred_depth_hw.numpy()

            raw_m     = compute_depth_metrics(pred_np, gt_np, scale_align=False)
            aligned_m = compute_depth_metrics(pred_np, gt_np, scale_align=True)
            if raw_m:
                raw_metrics.append(raw_m)
            if aligned_m:
                aligned_metrics.append(aligned_m)

            # ── save first frame point cloud ───────────────────────────────
            if not saved_pc:
                pc = depth_to_pointcloud(
                    pred_depth_hw,
                    result["intrinsics"][local_i],
                    result["extrinsics"][local_i],
                    conf=conf_hw,
                    conf_thresh=conf_thresh,
                )
                rgb_flat = result["images"][local_i].permute(1, 2, 0).reshape(-1, 3)
                rgb_masked = rgb_flat[pc["mask"]].numpy()
                npz_path = out_dir / f"{seq_name}_frame0.npz"
                save_npz(npz_path, pc["xyz"], rgb_masked, pc["depth"], pc["conf"])
                if save_ply_files:
                    save_ply(out_dir / f"{seq_name}_frame0.ply", pc["xyz"], rgb_masked)
                saved_pc = True

        print(f"    window {w_idx+1}/{len(windows)} done")

    return {
        "raw":     average_metrics(raw_metrics),
        "aligned": average_metrics(aligned_metrics),
        "n_frames": len(raw_metrics),
    }


# ── main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--data",       default="data/vkitti2")
    parser.add_argument("--ckpt",       default="checkpoints/vggt_omega/vggt_omega_1b_512.pt")
    parser.add_argument("--out",        default="outputs/vkitti2_eval")
    parser.add_argument("--scenes",     nargs="+", default=["Scene01","Scene02","Scene06","Scene18","Scene20"])
    parser.add_argument("--variants",   nargs="+", default=["clone"])
    parser.add_argument("--max-frames", type=int, default=50,
                        help="Max frames per sequence (0 = all)")
    parser.add_argument("--window",     type=int, default=10)
    parser.add_argument("--resolution", type=int, default=512)
    parser.add_argument("--conf-thresh",type=float, default=0.5)
    parser.add_argument("--ply",        action="store_true")
    parser.add_argument("--device",     default="cuda" if torch.cuda.is_available() else "cpu")
    args = parser.parse_args()

    root    = Path(args.data)
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    device  = torch.device(args.device)

    model = load_model(args.ckpt, device)

    all_results: dict[str, dict] = {}

    for scene in args.scenes:
        for variant in args.variants:
            seq_path = root / scene / variant
            if not seq_path.is_dir():
                print(f"[skip] {seq_path} not found")
                continue

            # vkitti2 has Camera_0 and Camera_1; we use Camera_0
            rgb_dir   = seq_path / "frames" / "rgb"   / "Camera_0"
            depth_dir = seq_path / "frames" / "depth" / "Camera_0"
            if not rgb_dir.is_dir() or not depth_dir.is_dir():
                print(f"[skip] missing rgb/depth dirs in {seq_path}")
                continue

            # Optionally cap frames
            if args.max_frames > 0:
                exts = {".jpg", ".jpeg", ".png"}
                all_rgb = sorted(p for p in rgb_dir.iterdir() if p.suffix.lower() in exts)
                if len(all_rgb) > args.max_frames:
                    # create a temp dir symlink approach is messy; instead pass a subset list
                    # We monkey-patch by temporarily renaming — just pass paths directly
                    # Better: pass explicit frame list to eval_sequence
                    pass  # handled below

            seq_name = f"{scene}_{variant}"
            print(f"\n=== {seq_name} ===")

            seq_out = out_dir / seq_name
            result  = eval_sequence_with_frame_limit(
                model      = model,
                rgb_dir    = rgb_dir,
                depth_dir  = depth_dir,
                out_dir    = seq_out,
                seq_name   = seq_name,
                resolution = args.resolution,
                window     = args.window,
                conf_thresh= args.conf_thresh,
                device     = device,
                max_frames = args.max_frames,
                save_ply_files = args.ply,
            )
            all_results[seq_name] = result
            if result:
                _print_metrics(seq_name, result)

    # ── aggregate across all sequences ────────────────────────────────────────
    all_raw     = [v["raw"]     for v in all_results.values() if v.get("raw")]
    all_aligned = [v["aligned"] for v in all_results.values() if v.get("aligned")]

    print("\n" + "="*60)
    print("AGGREGATE (across all sequences)")
    print("="*60)
    agg_raw     = average_metrics(all_raw)
    agg_aligned = average_metrics(all_aligned)
    _print_row("Raw (metric)",    agg_raw)
    _print_row("Scale-aligned",   agg_aligned)

    # Save JSON summary
    summary = {
        "per_sequence": all_results,
        "aggregate": {"raw": agg_raw, "scale_aligned": agg_aligned},
        "config": vars(args),
    }
    json_path = out_dir / "metrics.json"
    with open(json_path, "w") as f:
        json.dump(summary, f, indent=2)
    print(f"\nMetrics  → {json_path}")
    print(f"Images   → {out_dir}/<seq>/<seq>_f*_depth_compare.jpg")
    print(f"Clouds   → {out_dir}/<seq>/<seq>_f*_pc.{{npz,ply}}")


# helper that respects max_frames
def eval_sequence_with_frame_limit(
    model, rgb_dir, depth_dir, out_dir, seq_name,
    resolution, window, conf_thresh, device, max_frames, save_ply_files
):
    exts = {".jpg", ".jpeg", ".png"}
    rgb_frames = sorted(p for p in rgb_dir.iterdir() if p.suffix.lower() in exts)
    if not rgb_frames:
        print(f"  [skip] no RGB frames in {rgb_dir}")
        return {}

    if max_frames > 0:
        rgb_frames = rgb_frames[:max_frames]

    with Image.open(rgb_frames[0]) as im:
        W_orig, H_orig = im.size
    crop_box = compute_ar_crop(H_orig, W_orig)
    H_crop = crop_box[3] - crop_box[1]
    W_crop = crop_box[2] - crop_box[0]
    H_model, W_model = balanced_target_shape(H_crop / max(W_crop, 1), resolution)

    print(f"  {seq_name}: {len(rgb_frames)} frames  "
          f"orig {H_orig}×{W_orig} → crop {H_crop}×{W_crop} → model {H_model}×{W_model}")

    windows = []
    i = 0
    while i < len(rgb_frames):
        windows.append(rgb_frames[i: i + window])
        i += window

    raw_metrics, aligned_metrics = [], []
    out_dir.mkdir(parents=True, exist_ok=True)
    global_fi = 0  # frame index across windows

    for w_idx, win_frames in enumerate(windows):
        result = run_window(model, win_frames, resolution, device)
        N_win  = len(win_frames)

        for local_i in range(N_win):
            frame_stem = win_frames[local_i].stem
            gt_name = frame_stem.replace("rgb_", "depth_") + ".png"
            gt_path = depth_dir / gt_name
            if not gt_path.exists():
                global_fi += 1
                continue

            pred_hw = result["depth"][local_i]
            conf_hw = result["depth_conf"][local_i]
            pred_np = pred_hw.numpy()

            gt_full = load_gt_depth(gt_path)
            gt_crop = gt_full[crop_box[1]:crop_box[3], crop_box[0]:crop_box[2]]
            gt_t    = torch.from_numpy(gt_crop).unsqueeze(0).unsqueeze(0)
            gt_rs   = F.interpolate(gt_t, size=(H_model, W_model), mode="nearest")[0, 0].numpy()

            raw_m     = compute_depth_metrics(pred_np, gt_rs, scale_align=False)
            aligned_m = compute_depth_metrics(pred_np, gt_rs, scale_align=True)
            if raw_m:
                raw_metrics.append(raw_m)
            if aligned_m:
                aligned_metrics.append(aligned_m)

            # per-frame scale for visualization
            vis_scale = aligned_m["scale"] if aligned_m and "scale" in aligned_m else 1.0

            frame_tag = f"{seq_name}_f{global_fi:03d}"

            # ── comparison image ──────────────────────────────────────────
            rgb_hw3 = (result["images"][local_i].permute(1, 2, 0).numpy() * 255).clip(0, 255).astype(np.uint8)
            save_comparison_image(
                out_dir / f"{frame_tag}_depth_compare.jpg",
                rgb_hw3, pred_np, gt_rs, vis_scale,
            )

            # ── point cloud ───────────────────────────────────────────────
            pc = depth_to_pointcloud(
                pred_hw, result["intrinsics"][local_i], result["extrinsics"][local_i],
                conf=conf_hw, conf_thresh=conf_thresh,
            )
            rgb_flat   = result["images"][local_i].permute(1, 2, 0).reshape(-1, 3)
            rgb_masked = rgb_flat[pc["mask"]].numpy()
            save_npz(out_dir / f"{frame_tag}_pc.npz",  pc["xyz"], rgb_masked, pc["depth"], pc["conf"])
            if save_ply_files:
                save_ply(out_dir / f"{frame_tag}_pc.ply", pc["xyz"], rgb_masked)

            global_fi += 1

        print(f"    window {w_idx+1}/{len(windows)} done  "
              f"(frames {w_idx*window}–{min((w_idx+1)*window, len(rgb_frames))-1})")

    return {
        "raw":      average_metrics(raw_metrics),
        "aligned":  average_metrics(aligned_metrics),
        "n_frames": len(raw_metrics),
    }


def _print_row(label: str, m: dict):
    if not m:
        return
    print(f"  {label:20s}  AbsRel={m['abs_rel']:.4f}  RMSE={m['rmse']:.4f}  "
          f"δ₁={m['d1']:.4f}  δ₂={m['d2']:.4f}  δ₃={m['d3']:.4f}")


def _print_metrics(seq_name: str, result: dict):
    print(f"  → {result['n_frames']} frames evaluated")
    _print_row("raw (metric)",   result.get("raw", {}))
    _print_row("scale-aligned",  result.get("aligned", {}))


if __name__ == "__main__":
    main()
