#!/usr/bin/env python3
"""Benchmark one Depth Anything 3 checkpoint on a fixed Sintel subset."""

from __future__ import annotations

import argparse
import json
import struct
import time
from pathlib import Path

import numpy as np
import torch
import torch.nn.functional as F

from depth_anything_3.api import DepthAnything3


TAG_FLOAT = 202021.25


def read_dpt(path: Path) -> np.ndarray:
    with path.open("rb") as handle:
        tag = struct.unpack("f", handle.read(4))[0]
        width = struct.unpack("i", handle.read(4))[0]
        height = struct.unpack("i", handle.read(4))[0]
        if tag != TAG_FLOAT or not (0 < width < 100_000 and 0 < height < 100_000):
            raise ValueError(f"Invalid Sintel depth file: {path}")
        depth = np.fromfile(handle, np.float32, width * height)
    return depth.reshape(height, width)


def resize_depth(depth: np.ndarray, shape: tuple[int, int]) -> np.ndarray:
    tensor = torch.from_numpy(depth)[None, None]
    return F.interpolate(tensor, size=shape, mode="nearest")[0, 0].numpy()


def scene_metrics(pred: np.ndarray, gt: np.ndarray, max_depth: float) -> dict[str, float]:
    if pred.shape != gt.shape:
        gt = np.stack([resize_depth(frame, pred.shape[-2:]) for frame in gt])

    valid = np.isfinite(gt) & np.isfinite(pred) & (gt > 0) & (gt < max_depth) & (pred > 1e-6)
    pred_valid = pred[valid].astype(np.float64)
    gt_valid = gt[valid].astype(np.float64)
    if not pred_valid.size:
        raise ValueError("No valid depth pixels")

    # One robust metric-scale factor per video, as used in video-depth evaluation.
    scale = float(np.median(gt_valid) / np.median(pred_valid))
    pred_valid *= scale

    diff = pred_valid - gt_valid
    abs_diff = np.abs(diff)
    ratio = np.maximum(pred_valid / gt_valid, gt_valid / pred_valid)
    return {
        "pixels": int(gt_valid.size),
        "scale": scale,
        "mae_m": float(abs_diff.mean()),
        "rmse_m": float(np.sqrt(np.square(diff).mean())),
        "abs_rel": float((abs_diff / gt_valid).mean()),
        "delta1": float((ratio < 1.25).mean()),
    }


def aggregate(rows: list[dict[str, float]], key: str) -> dict[str, float]:
    values = np.asarray([row[key] for row in rows], dtype=np.float64)
    return {
        "mean": float(values.mean()),
        "std": float(values.std(ddof=1)),
        "median": float(np.median(values)),
        "min": float(values.min()),
        "max": float(values.max()),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", required=True)
    parser.add_argument("--data", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--process-res", type=int, default=504)
    parser.add_argument("--max-depth", type=float, default=80.0)
    args = parser.parse_args()

    image_root = args.data / "training" / "final"
    depth_root = args.data / "training" / "depth"
    scenes = sorted(path.name for path in image_root.iterdir() if path.is_dir())
    scene_paths = {scene: sorted((image_root / scene).glob("*.png")) for scene in scenes}
    if any(not paths for paths in scene_paths.values()):
        raise ValueError("A scene has no images")

    device = torch.device("cuda")
    load_start = time.perf_counter()
    model = DepthAnything3.from_pretrained(args.model).to(device).eval()
    torch.cuda.synchronize()
    load_seconds = time.perf_counter() - load_start
    parameters = sum(parameter.numel() for parameter in model.parameters())

    # Warm kernels and allocator on the same five-frame shape used below.
    first_paths = [str(path) for path in scene_paths[scenes[0]]]
    model.inference(
        first_paths,
        process_res=args.process_res,
        process_res_method="upper_bound_resize",
        ref_view_strategy="middle",
    )
    torch.cuda.synchronize()

    rows: list[dict[str, float]] = []
    for index, scene in enumerate(scenes, start=1):
        paths = scene_paths[scene]
        torch.cuda.reset_peak_memory_stats()
        torch.cuda.synchronize()
        start = time.perf_counter()
        prediction = model.inference(
            [str(path) for path in paths],
            process_res=args.process_res,
            process_res_method="upper_bound_resize",
            ref_view_strategy="middle",
        )
        torch.cuda.synchronize()
        elapsed = time.perf_counter() - start

        pred = np.asarray(prediction.depth, dtype=np.float32)
        gt = np.stack(
            [read_dpt(depth_root / scene / f"{path.stem}.dpt") for path in paths]
        )
        row = scene_metrics(pred, gt, args.max_depth)
        row.update(
            {
                "scene": scene,
                "frames": len(paths),
                "processed_height": int(pred.shape[-2]),
                "processed_width": int(pred.shape[-1]),
                "elapsed_s": elapsed,
                "ms_per_frame": elapsed * 1000.0 / len(paths),
                "peak_gpu_gb": torch.cuda.max_memory_allocated() / 1e9,
            }
        )
        rows.append(row)
        print(
            f"[{index:02d}/{len(scenes)}] {scene}: "
            f"{row['ms_per_frame']:.1f} ms/frame, "
            f"AbsRel={100 * row['abs_rel']:.2f}%, MAE={row['mae_m']:.3f}m",
            flush=True,
        )

    summary = {
        "model": args.model,
        "parameters": parameters,
        "load_seconds": load_seconds,
        "gpu": torch.cuda.get_device_name(),
        "torch": torch.__version__,
        "process_res": args.process_res,
        "max_depth": args.max_depth,
        "scenes": len(rows),
        "frames": sum(row["frames"] for row in rows),
        "metrics": {
            key: aggregate(rows, key)
            for key in ("mae_m", "rmse_m", "abs_rel", "delta1", "ms_per_frame", "peak_gpu_gb")
        },
        "per_scene": rows,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(summary, indent=2) + "\n")
    print(json.dumps({key: value for key, value in summary.items() if key != "per_scene"}, indent=2))


if __name__ == "__main__":
    main()
