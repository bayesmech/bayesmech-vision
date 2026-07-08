"""
Evaluate VGGT-Omega depth estimation on MPI-Sintel and produce:
  • depth metrics vs GT  (AbsRel, RMSE, δ₁/₂/₃, both scale-only and scale+shift aligned)
  • colorized depth comparison images  (rgb | pred | gt | error)
  • point clouds per sampled frame     (.npz + .ply)

Evaluation follows the VGGT-Omega paper protocol:
  - Clean pass, training split
  - 10 evenly-spaced frames sampled per sequence
  - Per-sequence scale alignment (median, scale-only)
  - Per-sequence affine alignment (least-squares scale+shift) also reported
  - Valid mask: 0 < depth < MAX_DEPTH (sky/invalid pixels excluded)

Usage
-----
    python scripts/eval_depth_sintel.py \\
        --data   data/sintel \\
        --ckpt   vendor/checkpoints/vggt_omega/vggt_omega_1b_512.pt \\
        --out    outputs/sintel_eval \\
        [--n-frames 10] \\
        [--window 10] \\
        [--resolution 512] \\
        [--max-depth 70] \\
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

# ── Sintel .dpt depth reader ───────────────────────────────────────────────────

TAG_FLOAT = 202021.25


def read_sintel_depth(path: Path) -> np.ndarray:
    """Read a Sintel .dpt depth file, return float32 [H, W] in metres.
    Sky/invalid pixels are large (>1e9); caller should mask those out.
    """
    with open(path, "rb") as f:
        tag = np.fromfile(f, dtype=np.float32, count=1)[0]
        assert abs(tag - TAG_FLOAT) < 1e-4, f"bad .dpt tag {tag} in {path}"
        w = np.fromfile(f, dtype=np.int32, count=1)[0]
        h = np.fromfile(f, dtype=np.int32, count=1)[0]
        depth = np.fromfile(f, dtype=np.float32, count=w * h).reshape(h, w)
    return depth


# ── aspect-ratio crop (mirrors vggt_omega/utils/load_fn.py) ───────────────────

def compute_ar_crop(h: int, w: int, min_ar: float = 0.5, max_ar: float = 2.0):
    ar = h / max(w, 1)
    if ar < min_ar:
        cw = max(1, int(round(h / min_ar)))
        left = max((w - cw) // 2, 0)
        return (left, 0, left + cw, h)
    if ar > max_ar:
        ch = max(1, int(round(w * max_ar)))
        top = max((h - ch) // 2, 0)
        return (0, top, w, top + ch)
    return (0, 0, w, h)


def balanced_target_shape(ar: float, resolution: int, patch_size: int = 16):
    tokens = (resolution // patch_size) ** 2
    wp = max(1, int(round(np.sqrt(tokens / ar))))
    hp = max(1, int(round(tokens / wp)))
    return hp * patch_size, wp * patch_size


# ── depth metrics ──────────────────────────────────────────────────────────────

def compute_metrics(pred: np.ndarray, gt: np.ndarray, max_depth: float) -> dict | None:
    """Scale-only (median) aligned metrics."""
    mask = (gt > 0) & (gt < max_depth) & (pred > 0)
    if mask.sum() < 10:
        return None
    p = pred[mask]
    g = gt[mask]
    scale = np.median(g) / (np.median(p) + 1e-8)
    p = np.clip(p * scale, 1e-3, None)
    g = np.clip(g, 1e-3, None)
    thr = np.maximum(p / g, g / p)
    return {
        "abs_rel":  float(np.mean(np.abs(p - g) / g)),
        "sq_rel":   float(np.mean((p - g) ** 2 / g)),
        "rmse":     float(np.sqrt(np.mean((p - g) ** 2))),
        "rmse_log": float(np.sqrt(np.mean((np.log(p) - np.log(g)) ** 2))),
        "d1": float((thr < 1.25).mean()),
        "d2": float((thr < 1.25 ** 2).mean()),
        "d3": float((thr < 1.25 ** 3).mean()),
        "scale": float(scale),
        "n_valid": int(mask.sum()),
    }


def compute_metrics_affine(pred: np.ndarray, gt: np.ndarray, max_depth: float) -> dict | None:
    """Scale+shift (affine, least-squares) aligned metrics."""
    mask = (gt > 0) & (gt < max_depth) & (pred > 0)
    if mask.sum() < 10:
        return None
    p = pred[mask]
    g = gt[mask]
    # least-squares: min ||s*p + t - g||  → [s, t]
    A = np.stack([p, np.ones_like(p)], axis=1)
    result = np.linalg.lstsq(A, g, rcond=None)
    s, t = result[0]
    p_aligned = np.clip(s * p + t, 1e-3, None)
    g = np.clip(g, 1e-3, None)
    thr = np.maximum(p_aligned / g, g / p_aligned)
    return {
        "abs_rel":  float(np.mean(np.abs(p_aligned - g) / g)),
        "rmse":     float(np.sqrt(np.mean((p_aligned - g) ** 2))),
        "d1": float((thr < 1.25).mean()),
        "d2": float((thr < 1.25 ** 2).mean()),
        "d3": float((thr < 1.25 ** 3).mean()),
        "scale": float(s), "shift": float(t),
        "n_valid": int(mask.sum()),
    }


def avg(records: list[dict]) -> dict:
    if not records:
        return {}
    keys = [k for k in records[0] if k not in ("n_valid", "scale", "shift")]
    return {k: float(np.mean([r[k] for r in records])) for k in keys}


# ── visualization ──────────────────────────────────────────────────────────────

def colorize_depth(depth: np.ndarray, vmin: float | None = None, vmax: float | None = None) -> np.ndarray:
    """Map depth [H,W] float → uint8 RGB [H,W,3] using plasma colormap."""
    try:
        import matplotlib.cm as cm
    except ImportError:
        # fallback: grayscale
        d = np.clip(depth, 0, None)
        if d.max() > 0:
            d = (d / d.max() * 255).astype(np.uint8)
        return np.stack([d, d, d], axis=-1)

    d = depth.copy().astype(np.float64)
    vmin = vmin if vmin is not None else np.percentile(d[d > 0], 5) if (d > 0).any() else 0
    vmax = vmax if vmax is not None else np.percentile(d[d > 0], 95) if (d > 0).any() else 1
    d = np.clip((d - vmin) / max(vmax - vmin, 1e-8), 0, 1)
    rgba = cm.plasma(d)
    rgb = (rgba[:, :, :3] * 255).astype(np.uint8)
    # mark invalid pixels gray
    invalid = depth <= 0
    rgb[invalid] = 128
    return rgb


def save_comparison_image(
    path: Path,
    rgb_hw3: np.ndarray,          # uint8 [H, W, 3]
    pred_depth: np.ndarray,       # float [H, W]  (model scale)
    gt_depth: np.ndarray,         # float [H, W]  (metres, 0=invalid)
    scale: float,                 # median scale factor
    frame_label: str,
) -> None:
    """Save a 4-panel image: RGB | pred depth | GT depth | abs-error map."""
    H, W = rgb_hw3.shape[:2]

    # shared depth range for pred and GT panels (use GT valid range)
    valid_gt = gt_depth[gt_depth > 0]
    if len(valid_gt) == 0:
        vmin, vmax = 0, 1
    else:
        vmin = float(np.percentile(valid_gt, 2))
        vmax = float(np.percentile(valid_gt, 98))

    pred_m = pred_depth * scale           # scale pred to metres for display
    pred_col = colorize_depth(pred_m, vmin, vmax)
    gt_col   = colorize_depth(gt_depth,  vmin, vmax)

    # abs error in metres (valid pixels only)
    err = np.zeros_like(gt_depth)
    valid = (gt_depth > 0) & (pred_depth > 0)
    err[valid] = np.abs(pred_m[valid] - gt_depth[valid])
    err_col = colorize_depth(err, 0, float(np.percentile(err[valid], 95)) if valid.any() else 1)

    # resize GT to model size if needed
    if gt_col.shape[:2] != (H, W):
        gt_pil = Image.fromarray(gt_col).resize((W, H), Image.NEAREST)
        gt_col = np.array(gt_pil)
        err_pil = Image.fromarray(err_col).resize((W, H), Image.NEAREST)
        err_col = np.array(err_pil)

    # add simple text labels via PIL
    panels = [rgb_hw3, pred_col, gt_col, err_col]
    labels = ["RGB input", "Predicted depth", "GT depth", "|pred − GT|"]
    label_imgs = []
    for panel, label in zip(panels, labels):
        pil = Image.fromarray(panel)
        label_imgs.append(pil)

    # stitch horizontally
    total_w = sum(p.width for p in label_imgs)
    combined = Image.new("RGB", (total_w, H + 20), (20, 20, 20))
    x = 0
    for pil_img, label in zip(label_imgs, labels):
        combined.paste(pil_img, (x, 20))
        # draw label text
        from PIL import ImageDraw
        draw = ImageDraw.Draw(combined)
        draw.text((x + 4, 2), label, fill=(240, 240, 240))
        x += pil_img.width

    path.parent.mkdir(parents=True, exist_ok=True)
    combined.save(path)


# ── point cloud helpers (same as vkitti2 script) ──────────────────────────────

def depth_to_pointcloud(depth, intrinsics, extrinsics, conf=None, conf_thresh=0.0):
    H, W = depth.shape
    device = depth.device
    depth = depth.float(); intrinsics = intrinsics.float(); extrinsics = extrinsics.float()
    fx, fy = intrinsics[0, 0], intrinsics[1, 1]
    cx, cy = intrinsics[0, 2], intrinsics[1, 2]
    us = torch.arange(W, device=device, dtype=torch.float32)
    vs = torch.arange(H, device=device, dtype=torch.float32)
    gv, gu = torch.meshgrid(vs, us, indexing="ij")
    pts_cam = torch.stack([(gu-cx)/fx*depth, (gv-cy)/fy*depth, depth], dim=-1).reshape(-1, 3)
    R = extrinsics[:3, :3]; t = extrinsics[:3, 3]
    pts_world = (pts_cam - t) @ R
    mask = depth.reshape(-1) > 0
    if conf is not None and conf_thresh > 0:
        mask = mask & (conf.float().reshape(-1) >= conf_thresh)
    return {
        "xyz":   pts_world[mask].cpu().numpy().astype(np.float32),
        "mask":  mask.cpu().numpy(),
        "depth": depth.cpu().numpy().astype(np.float32),
        "conf":  conf.cpu().numpy().astype(np.float32) if conf is not None else None,
    }


def save_npz(path, xyz, rgb, depth, conf):
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
        "property uchar red\nproperty uchar green\nproperty uchar blue\nend_header\n"
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
def run_window(model, frame_paths, resolution, device):
    from vggt_omega.utils.load_fn import load_and_preprocess_images
    from vggt_omega.utils.pose_enc import encoding_to_camera

    images = load_and_preprocess_images(
        [str(p) for p in frame_paths], mode="balanced", image_resolution=resolution,
    )
    images = images.unsqueeze(0).to(device)
    predictions = model(images)
    H, W = images.shape[-2], images.shape[-1]
    extrinsics, intrinsics = encoding_to_camera(predictions["pose_enc"], image_size_hw=(H, W))
    return {
        "images":     images[0].cpu(),
        "depth":      predictions["depth"][0].squeeze(-1).cpu(),
        "depth_conf": predictions["depth_conf"][0].cpu(),
        "extrinsics": extrinsics[0].cpu(),
        "intrinsics": intrinsics[0].cpu(),
        "model_hw":   (H, W),
    }


# ── per-sequence evaluation ────────────────────────────────────────────────────

def eval_sequence(
    model, seq_name: str, rgb_dir: Path, depth_dir: Path, out_dir: Path,
    n_frames: int, resolution: int, window: int, max_depth: float,
    conf_thresh: float, device: torch.device,
) -> dict:
    exts = {".png", ".jpg", ".jpeg"}
    all_rgb = sorted(p for p in rgb_dir.iterdir() if p.suffix.lower() in exts)
    if not all_rgb:
        return {}

    # evenly sample n_frames
    if n_frames >= len(all_rgb):
        sampled_rgb = all_rgb
    else:
        idxs = np.linspace(0, len(all_rgb) - 1, n_frames, dtype=int)
        sampled_rgb = [all_rgb[i] for i in idxs]

    # compute crop + model shape from first frame
    with Image.open(sampled_rgb[0]) as im:
        W_orig, H_orig = im.size
    crop = compute_ar_crop(H_orig, W_orig)
    H_crop = crop[3] - crop[1]; W_crop = crop[2] - crop[0]
    H_model, W_model = balanced_target_shape(H_crop / max(W_crop, 1), resolution)

    print(f"  {seq_name}: {len(all_rgb)} frames total, sampling {len(sampled_rgb)}  "
          f"orig {H_orig}×{W_orig} → crop {H_crop}×{W_crop} → model {H_model}×{W_model}")

    # run inference over the full sampled set as one window (≤window frames per call)
    # split into windows if needed
    windows = []
    i = 0
    while i < len(sampled_rgb):
        windows.append(sampled_rgb[i: i + window])
        i += window

    # collect all results
    all_pred:  list[np.ndarray] = []
    all_conf:  list[np.ndarray] = []
    all_extri: list[torch.Tensor] = []
    all_intri: list[torch.Tensor] = []
    all_rgb_t: list[torch.Tensor] = []

    for win in windows:
        res = run_window(model, win, resolution, device)
        N = len(win)
        for li in range(N):
            all_pred.append(res["depth"][li].numpy())
            all_conf.append(res["depth_conf"][li].numpy())
            all_extri.append(res["extrinsics"][li])
            all_intri.append(res["intrinsics"][li])
            all_rgb_t.append(res["images"][li])

    # compute per-sequence scale using ALL sampled frames (median over all valid pixels)
    # then compute metrics frame by frame
    all_gt_resized: list[np.ndarray] = []
    for rgb_path in sampled_rgb:
        dpt_name = rgb_path.stem + ".dpt"
        dpt_path = depth_dir / dpt_name
        if not dpt_path.exists():
            all_gt_resized.append(None)
            continue
        gt_full = read_sintel_depth(dpt_path)
        # crop same as model
        gt_crop = gt_full[crop[1]:crop[3], crop[0]:crop[2]]
        gt_t = torch.from_numpy(gt_crop).unsqueeze(0).unsqueeze(0)
        gt_rs = F.interpolate(gt_t, size=(H_model, W_model), mode="nearest")[0, 0].numpy()
        # mask sky / invalid (> 1e6 metres in sintel means no geometry)
        gt_rs[gt_rs > 1e6] = 0.0
        all_gt_resized.append(gt_rs)

    # per-sequence median scale (aggregate over all valid frame pixels)
    all_pred_valid, all_gt_valid = [], []
    for pred, gt in zip(all_pred, all_gt_resized):
        if gt is None:
            continue
        mask = (gt > 0) & (gt < max_depth) & (pred > 0)
        all_pred_valid.append(pred[mask])
        all_gt_valid.append(gt[mask])
    if all_pred_valid:
        seq_scale = float(np.median(np.concatenate(all_gt_valid)) /
                          (np.median(np.concatenate(all_pred_valid)) + 1e-8))
    else:
        seq_scale = 1.0

    # per-frame metrics
    scale_metrics: list[dict] = []
    affine_metrics: list[dict] = []
    out_dir.mkdir(parents=True, exist_ok=True)

    for fi, (rgb_path, pred, gt, conf, extri, intri, rgb_t) in enumerate(
        zip(sampled_rgb, all_pred, all_gt_resized, all_conf, all_extri, all_intri, all_rgb_t)
    ):
        if gt is None:
            continue

        sm = compute_metrics(pred, gt, max_depth)
        am = compute_metrics_affine(pred, gt, max_depth)
        if sm:
            scale_metrics.append(sm)
        if am:
            affine_metrics.append(am)

        frame_tag = f"{seq_name}_f{fi:02d}"

        # ── save comparison image ─────────────────────────────────────────
        rgb_hw3 = (rgb_t.permute(1, 2, 0).numpy() * 255).clip(0, 255).astype(np.uint8)
        save_comparison_image(
            out_dir / f"{frame_tag}_depth_compare.jpg",
            rgb_hw3, pred, gt, seq_scale, frame_tag,
        )

        # ── save point cloud ──────────────────────────────────────────────
        pc = depth_to_pointcloud(
            torch.from_numpy(pred), intri, extri,
            conf=torch.from_numpy(conf), conf_thresh=conf_thresh,
        )
        rgb_flat = rgb_t.permute(1, 2, 0).reshape(-1, 3)
        rgb_masked = rgb_flat[pc["mask"]].numpy()
        save_npz(out_dir / f"{frame_tag}_pc.npz",  pc["xyz"], rgb_masked, pc["depth"], pc["conf"])
        save_ply(out_dir / f"{frame_tag}_pc.ply",  pc["xyz"], rgb_masked)

    return {
        "scale_aligned": avg(scale_metrics),
        "affine_aligned": avg(affine_metrics),
        "per_frame_scale": scale_metrics,
        "n_frames": len(scale_metrics),
        "seq_scale": seq_scale,
    }


# ── main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--data",       default="data/sintel")
    parser.add_argument("--ckpt",       default="checkpoints/vggt_omega/vggt_omega_1b_512.pt")
    parser.add_argument("--out",        default="outputs/sintel_eval")
    parser.add_argument("--pass",       dest="sintel_pass", default="clean", choices=["clean", "final"])
    parser.add_argument("--n-frames",   type=int, default=10)
    parser.add_argument("--window",     type=int, default=10)
    parser.add_argument("--resolution", type=int, default=512)
    parser.add_argument("--max-depth",  type=float, default=70.0)
    parser.add_argument("--conf-thresh",type=float, default=0.5)
    parser.add_argument("--device",     default="cuda" if torch.cuda.is_available() else "cpu")
    args = parser.parse_args()

    root     = Path(args.data) / "training"
    rgb_root = root / args.sintel_pass
    dep_root = root / "depth"
    out_dir  = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    device   = torch.device(args.device)

    if not rgb_root.is_dir():
        print(f"ERROR: {rgb_root} not found. Run download_datasets.sh first.")
        sys.exit(1)

    sequences = sorted(p.name for p in rgb_root.iterdir() if p.is_dir())
    print(f"Found {len(sequences)} Sintel sequences in {rgb_root}")

    model = load_model(args.ckpt, device)

    all_results: dict[str, dict] = {}
    for seq in sequences:
        print(f"\n=== {seq} ===")
        result = eval_sequence(
            model       = model,
            seq_name    = seq,
            rgb_dir     = rgb_root / seq,
            depth_dir   = dep_root / seq,
            out_dir     = out_dir  / seq,
            n_frames    = args.n_frames,
            resolution  = args.resolution,
            window      = args.window,
            max_depth   = args.max_depth,
            conf_thresh = args.conf_thresh,
            device      = device,
        )
        all_results[seq] = result
        if result.get("scale_aligned"):
            m = result["scale_aligned"]
            print(f"  scale-aligned  AbsRel={m['abs_rel']:.4f}  RMSE={m['rmse']:.4f}  "
                  f"δ₁={m['d1']:.4f}  δ₂={m['d2']:.4f}  δ₃={m['d3']:.4f}")

    # ── aggregate ──────────────────────────────────────────────────────────────
    all_scale  = [v["scale_aligned"]  for v in all_results.values() if v.get("scale_aligned")]
    all_affine = [v["affine_aligned"] for v in all_results.values() if v.get("affine_aligned")]
    agg_scale  = avg(all_scale)
    agg_affine = avg(all_affine)

    print("\n" + "=" * 60)
    print("AGGREGATE — Sintel (clean pass, scale-only aligned)")
    print("=" * 60)
    _row("Scale-aligned  ", agg_scale)
    _row("Affine-aligned ", agg_affine)
    print(f"\nPaper reports (VGGT-Ω 1B):  AbsRel=0.097  δ₁=0.895")

    summary = {
        "per_sequence": {k: {kk: vv for kk, vv in v.items() if kk != "per_frame_scale"}
                         for k, v in all_results.items()},
        "aggregate": {"scale_aligned": agg_scale, "affine_aligned": agg_affine},
        "paper_reference": {"abs_rel": 0.097, "d1": 0.895, "model": "VGGT-Ω 1B"},
        "config": vars(args),
    }
    json_path = out_dir / "metrics.json"
    with open(json_path, "w") as f:
        json.dump(summary, f, indent=2)
    print(f"\nMetrics → {json_path}")
    print(f"Images  → {out_dir}/<seq>/<seq>_f*_depth_compare.jpg")
    print(f"Clouds  → {out_dir}/<seq>/<seq>_f*_pc.{{npz,ply}}")


def _row(label, m):
    if not m:
        return
    print(f"  {label}  AbsRel={m['abs_rel']:.4f}  RMSE={m['rmse']:.4f}  "
          f"δ₁={m['d1']:.4f}  δ₂={m['d2']:.4f}  δ₃={m['d3']:.4f}")


if __name__ == "__main__":
    main()
