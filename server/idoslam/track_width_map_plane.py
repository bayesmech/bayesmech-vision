#!/usr/bin/env python3
"""
Estimate road width and bike lateral position from segmentation masks.

This first-pass estimator uses a flat-ground projection with:
- a single global pitch estimate from road-edge convergence
- an assumed camera height
- per-frame road/pavement masks to infer left/right corridor edges

The output is intended as a debugging baseline before switching to a
triangulation-based boundary estimate.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import sys
from pathlib import Path

import cv2
import matplotlib
import numpy as np

matplotlib.use("Agg")
import matplotlib.pyplot as plt

_server_root = Path(__file__).resolve().parent.parent
_project_root = _server_root.parent
for _p in (str(_project_root), str(_project_root / "proto"), str(_server_root)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from proto import perceiver_pb2
from idoslam.common import (
    bike_mask_for_frame,
    camera_from_first_frame,
    decode_mask,
    decode_rgb,
    gps_to_local_xy,
    iter_messages,
    load_segmentation_index,
    plane_output_dir,
    seg_path,
)


TRAVERSABLE_LABELS = ("road", "pavement")


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Estimate road width along the GPS track")
    p.add_argument("recording", type=Path, help="Path to .vis.pb recording")
    p.add_argument("--camera-height-m", type=float, default=1.45)
    p.add_argument("--pitch-deg", type=float, default=None, help="Optional fixed pitch; otherwise estimate from masks")
    p.add_argument("--pitch-sample-every", type=int, default=8)
    p.add_argument("--width-sample-every", type=int, default=1)
    p.add_argument("--mask-dilate", type=int, default=9)
    p.add_argument("--bottom-border", type=int, default=24)
    p.add_argument("--closing-width", type=int, default=181)
    p.add_argument("--closing-height", type=int, default=9)
    p.add_argument("--row-start-frac", type=float, default=0.56)
    p.add_argument("--row-end-frac", type=float, default=0.82)
    p.add_argument("--row-step", type=int, default=14)
    p.add_argument("--min-segment-frac", type=float, default=0.06)
    p.add_argument("--plot-every", type=int, default=25)
    return p.parse_args()


def output_path(recording: Path) -> Path:
    return plane_output_dir(recording)


def build_label_mask(seg_frame, labels: tuple[str, ...], image_shape: tuple[int, int]) -> np.ndarray:
    h, w = image_shape
    mask = np.zeros((h, w), dtype=np.uint8)
    if seg_frame is None:
        return mask
    for seg_mask in seg_frame.masks:
        if seg_mask.label not in labels:
            continue
        decoded = decode_mask(seg_mask.mask_data)
        if decoded.shape != mask.shape:
            decoded = cv2.resize(decoded, (w, h), interpolation=cv2.INTER_NEAREST)
        mask = cv2.bitwise_or(mask, decoded)
    return mask


def estimate_bike_center_x(seg_frame, image_shape: tuple[int, int], default_x: float) -> float:
    h, w = image_shape
    mask = np.zeros((h, w), dtype=np.uint8)
    if seg_frame is not None:
        for seg_mask in seg_frame.masks:
            if seg_mask.label != "bike":
                continue
            decoded = decode_mask(seg_mask.mask_data)
            if decoded.shape != mask.shape:
                decoded = cv2.resize(decoded, (w, h), interpolation=cv2.INTER_NEAREST)
            mask = cv2.bitwise_or(mask, decoded)
    ys, xs = np.nonzero(mask)
    if len(xs) == 0:
        return default_x
    return float(xs.mean())


def segment_candidates(rowmask: np.ndarray, min_width_px: int) -> list[tuple[int, int]]:
    xs = np.flatnonzero(rowmask)
    if len(xs) == 0:
        return []
    splits = np.where(np.diff(xs) > 1)[0]
    starts = np.r_[0, splits + 1]
    ends = np.r_[splits, len(xs) - 1]
    segments: list[tuple[int, int]] = []
    for s, e in zip(starts, ends):
        l = int(xs[s])
        r = int(xs[e])
        if r - l >= min_width_px:
            segments.append((l, r))
    return segments


def choose_corridor_segment(segments: list[tuple[int, int]], anchor_x: float) -> tuple[int, int] | None:
    if not segments:
        return None
    containing = [seg for seg in segments if seg[0] <= anchor_x <= seg[1]]
    if containing:
        return max(containing, key=lambda seg: seg[1] - seg[0])
    return max(segments, key=lambda seg: seg[1] - seg[0])


def estimate_pitch_deg(
    seg_frames: dict[tuple[int, int], object],
    image_shape: tuple[int, int],
    intr: dict[str, float],
    sample_every: int,
    row_start_frac: float,
    row_end_frac: float,
    row_step: int,
    closing_kernel: np.ndarray,
    min_segment_px: int,
) -> tuple[float, dict[str, float]]:
    h, w = image_shape
    rows = list(range(int(h * row_start_frac), int(h * row_end_frac), row_step))
    vxs: list[float] = []
    vys: list[float] = []
    cx = intr["cx"]
    for idx, seg_frame in enumerate(seg_frames.values()):
        if idx % max(sample_every, 1) != 0:
            continue
        road = build_label_mask(seg_frame, TRAVERSABLE_LABELS, image_shape)
        if not np.any(road):
            continue
        road = cv2.morphologyEx(road, cv2.MORPH_CLOSE, closing_kernel)
        left_pts: list[tuple[int, int]] = []
        right_pts: list[tuple[int, int]] = []
        for y in rows:
            segments = segment_candidates(road[y] > 0, min_segment_px)
            seg = choose_corridor_segment(segments, cx)
            if seg is None:
                continue
            left_pts.append((y, seg[0]))
            right_pts.append((y, seg[1]))
        if len(left_pts) < 6:
            continue
        yl = np.array([p[0] for p in left_pts], dtype=np.float64)
        xl = np.array([p[1] for p in left_pts], dtype=np.float64)
        yr = np.array([p[0] for p in right_pts], dtype=np.float64)
        xr = np.array([p[1] for p in right_pts], dtype=np.float64)
        al, bl = np.polyfit(yl, xl, 1)
        ar, br = np.polyfit(yr, xr, 1)
        denom = al - ar
        if abs(denom) < 1e-4:
            continue
        vy = float((br - bl) / denom)
        vx = float(al * vy + bl)
        if -h <= vy <= h * 0.8 and 0 <= vx <= w:
            vxs.append(vx)
            vys.append(vy)
    if not vys:
        return 18.0, {"sample_count": 0}
    vy = float(np.median(np.array(vys)))
    pitch_deg = float(math.degrees(math.atan((intr["cy"] - vy) / intr["fy"])))
    debug = {
        "sample_count": len(vys),
        "vanishing_x_median": float(np.median(np.array(vxs))),
        "vanishing_y_median": vy,
        "vanishing_y_p10": float(np.percentile(np.array(vys), 10)),
        "vanishing_y_p90": float(np.percentile(np.array(vys), 90)),
    }
    return pitch_deg, debug


def ground_projection_function(intr: dict[str, float], pitch_deg: float, camera_height_m: float):
    cx = intr["cx"]
    cy = intr["cy"]
    fx = intr["fx"]
    fy = intr["fy"]
    pitch = math.radians(pitch_deg)
    r0 = np.array([[1.0, 0.0, 0.0], [0.0, 0.0, 1.0], [0.0, -1.0, 0.0]], dtype=np.float64)
    rx = np.array(
        [
            [1.0, 0.0, 0.0],
            [0.0, math.cos(pitch), math.sin(pitch)],
            [0.0, -math.sin(pitch), math.cos(pitch)],
        ],
        dtype=np.float64,
    )
    cam_to_ground = rx @ r0

    def project(u: float, v: float) -> np.ndarray | None:
        ray = np.array([(u - cx) / fx, (v - cy) / fy, 1.0], dtype=np.float64)
        ground_ray = cam_to_ground @ ray
        if ground_ray[2] >= -1e-6:
            return None
        scale = -camera_height_m / ground_ray[2]
        point = scale * ground_ray
        return point[:2]

    return project


def smooth_series(values: np.ndarray, window: int) -> np.ndarray:
    if len(values) == 0 or window <= 1:
        return values
    out = values.copy()
    half = window // 2
    for i in range(len(values)):
        lo = max(0, i - half)
        hi = min(len(values), i + half + 1)
        chunk = values[lo:hi]
        out[i] = np.median(chunk)
    return out


def write_track_width_plot(
    out_path: Path,
    gps_xy: np.ndarray,
    left_offsets_m: np.ndarray,
    right_offsets_m: np.ndarray,
    valid_mask: np.ndarray,
    plot_every: int,
) -> None:
    if len(gps_xy) < 2:
        return
    tangents = np.zeros_like(gps_xy)
    tangents[1:-1] = gps_xy[2:] - gps_xy[:-2]
    tangents[0] = gps_xy[1] - gps_xy[0]
    tangents[-1] = gps_xy[-1] - gps_xy[-2]
    tangent_norm = np.linalg.norm(tangents, axis=1, keepdims=True)
    tangent_norm[tangent_norm < 1e-6] = 1.0
    tangents = tangents / tangent_norm
    normals = np.column_stack([-tangents[:, 1], tangents[:, 0]])

    left_boundary = gps_xy + normals * left_offsets_m[:, None]
    right_boundary = gps_xy - normals * right_offsets_m[:, None]

    fig, ax = plt.subplots(figsize=(12, 12))
    ax.plot(gps_xy[:, 0], gps_xy[:, 1], color="#b22222", linewidth=2.0, label="Bike GPS path")
    ax.plot(left_boundary[valid_mask, 0], left_boundary[valid_mask, 1], color="#1f77b4", linewidth=1.5, label="Left edge")
    ax.plot(right_boundary[valid_mask, 0], right_boundary[valid_mask, 1], color="#2ca02c", linewidth=1.5, label="Right edge")

    valid_idx = np.flatnonzero(valid_mask)
    for idx in valid_idx[:: max(plot_every, 1)]:
        ax.plot(
            [left_boundary[idx, 0], right_boundary[idx, 0]],
            [left_boundary[idx, 1], right_boundary[idx, 1]],
            color="#666666",
            alpha=0.25,
            linewidth=1.0,
        )

    ax.set_title("GPS Track With Estimated Road Width")
    ax.set_aspect("equal", adjustable="box")
    ax.grid(True, alpha=0.3)
    ax.legend()
    fig.tight_layout()
    fig.savefig(out_path, dpi=180)
    plt.close(fig)


def main() -> None:
    args = parse_args()
    recording = args.recording.resolve()
    segmentation = seg_path(recording)
    out_dir = output_path(recording)
    out_dir.mkdir(parents=True, exist_ok=True)

    seg_frames, label_counts = load_segmentation_index(segmentation)
    frame_iter = iter_messages(recording, perceiver_pb2.PerceiverDataFrame)
    first_frame = next(frame_iter, None)
    if first_frame is None:
        raise RuntimeError("Recording is empty")

    first_bgr = decode_rgb(first_frame)
    h, w = first_bgr.shape[:2]
    intr = camera_from_first_frame(first_frame, w, h, args.bottom_border)
    closing_kernel = cv2.getStructuringElement(
        cv2.MORPH_RECT,
        (max(3, int(args.closing_width) | 1), max(3, int(args.closing_height) | 1)),
    )
    min_segment_px = max(24, int(w * args.min_segment_frac))

    if args.pitch_deg is None:
        pitch_deg, pitch_debug = estimate_pitch_deg(
            seg_frames=seg_frames,
            image_shape=(h, w),
            intr=intr,
            sample_every=args.pitch_sample_every,
            row_start_frac=args.row_start_frac,
            row_end_frac=args.row_end_frac,
            row_step=args.row_step,
            closing_kernel=closing_kernel,
            min_segment_px=min_segment_px,
        )
    else:
        pitch_deg = float(args.pitch_deg)
        pitch_debug = {"sample_count": 0, "fixed_pitch_deg": pitch_deg}

    project_ground = ground_projection_function(intr, pitch_deg, args.camera_height_m)
    rows = list(range(int(h * args.row_start_frac), int(h * args.row_end_frac), args.row_step))

    estimates: list[dict[str, float | int | str]] = []
    gps_rows: list[dict[str, float]] = []

    all_frames = [first_frame]
    all_frames.extend(frame_iter)
    for frame_index, frame in enumerate(all_frames):
        if frame_index % max(args.width_sample_every, 1) != 0:
            continue
        frame_number = int(frame.frame_identifier.frame_number)
        timestamp_ns = int(frame.frame_identifier.timestamp_ns)
        seg_frame = seg_frames.get((frame_number, timestamp_ns))
        if frame.HasField("gps_location"):
            gps_rows.append(
                {
                    "frame_index": frame_index,
                    "frame_number": frame_number,
                    "timestamp_ns": timestamp_ns,
                    "latitude": float(frame.gps_location.latitude),
                    "longitude": float(frame.gps_location.longitude),
                    "altitude": float(frame.gps_location.altitude),
                    "accuracy": float(frame.gps_location.accuracy),
                }
            )
        if seg_frame is None:
            continue

        road = build_label_mask(seg_frame, TRAVERSABLE_LABELS, (h, w))
        if not np.any(road):
            continue
        road = cv2.morphologyEx(road, cv2.MORPH_CLOSE, closing_kernel)
        road[-int(args.bottom_border) :, :] = 0

        bike_center_x = estimate_bike_center_x(seg_frame, (h, w), intr["cx"])
        widths: list[float] = []
        left_offsets: list[float] = []
        right_offsets: list[float] = []
        bike_fractions: list[float] = []
        used_rows = 0
        for y in rows:
            segments = segment_candidates(road[y] > 0, min_segment_px)
            seg = choose_corridor_segment(segments, bike_center_x)
            if seg is None:
                continue
            x_left, x_right = seg
            x_bike = float(np.clip(bike_center_x, x_left, x_right))
            p_left = project_ground(x_left, y)
            p_right = project_ground(x_right, y)
            p_bike = project_ground(x_bike, y)
            if p_left is None or p_right is None or p_bike is None:
                continue
            width_m = float(np.linalg.norm(p_right - p_left))
            left_m = float(np.linalg.norm(p_bike - p_left))
            right_m = float(np.linalg.norm(p_right - p_bike))
            if width_m <= 0.05:
                continue
            widths.append(width_m)
            left_offsets.append(left_m)
            right_offsets.append(right_m)
            bike_fractions.append((x_bike - x_left) / max(x_right - x_left, 1))
            used_rows += 1

        if not widths:
            continue
        estimates.append(
            {
                "frame_index": frame_index,
                "frame_number": frame_number,
                "timestamp_ns": timestamp_ns,
                "width_m": float(np.median(np.array(widths))),
                "left_offset_m": float(np.median(np.array(left_offsets))),
                "right_offset_m": float(np.median(np.array(right_offsets))),
                "bike_fraction": float(np.median(np.array(bike_fractions))),
                "bike_center_x_px": bike_center_x,
                "rows_used": used_rows,
                "method": "plane_projection",
            }
        )

    if not gps_rows:
        raise RuntimeError("No GPS rows found in recording")

    gps_xy = gps_to_local_xy(
        np.array([row["latitude"] for row in gps_rows], dtype=np.float64),
        np.array([row["longitude"] for row in gps_rows], dtype=np.float64),
    )
    gps_by_frame = {int(row["frame_index"]): (idx, row) for idx, row in enumerate(gps_rows)}

    width_map = np.full(len(gps_rows), np.nan, dtype=np.float64)
    left_map = np.full(len(gps_rows), np.nan, dtype=np.float64)
    right_map = np.full(len(gps_rows), np.nan, dtype=np.float64)
    frac_map = np.full(len(gps_rows), np.nan, dtype=np.float64)
    for row in estimates:
        gps_idx_row = gps_by_frame.get(int(row["frame_index"]))
        if gps_idx_row is None:
            continue
        gps_idx = gps_idx_row[0]
        width_map[gps_idx] = float(row["width_m"])
        left_map[gps_idx] = float(row["left_offset_m"])
        right_map[gps_idx] = float(row["right_offset_m"])
        frac_map[gps_idx] = float(row["bike_fraction"])

    valid_mask = np.isfinite(width_map)
    if np.any(valid_mask):
        fill_value = float(np.nanmedian(width_map[valid_mask]))
        width_map[~valid_mask] = fill_value
        left_fill = float(np.nanmedian(left_map[np.isfinite(left_map)]))
        right_fill = float(np.nanmedian(right_map[np.isfinite(right_map)]))
        frac_fill = float(np.nanmedian(frac_map[np.isfinite(frac_map)]))
        left_map[~np.isfinite(left_map)] = left_fill
        right_map[~np.isfinite(right_map)] = right_fill
        frac_map[~np.isfinite(frac_map)] = frac_fill
    else:
        raise RuntimeError("No usable width estimates were produced")

    width_map = smooth_series(width_map, 31)
    left_map = smooth_series(left_map, 31)
    right_map = smooth_series(right_map, 31)
    frac_map = smooth_series(frac_map, 31)

    csv_path = out_dir / "track_width_estimates.csv"
    with csv_path.open("w", newline="") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=[
                "frame_index",
                "frame_number",
                "timestamp_ns",
                "latitude",
                "longitude",
                "width_m",
                "left_offset_m",
                "right_offset_m",
                "bike_fraction",
            ],
        )
        writer.writeheader()
        for i, gps_row in enumerate(gps_rows):
            writer.writerow(
                {
                    "frame_index": gps_row["frame_index"],
                    "frame_number": gps_row["frame_number"],
                    "timestamp_ns": gps_row["timestamp_ns"],
                    "latitude": gps_row["latitude"],
                    "longitude": gps_row["longitude"],
                    "width_m": float(width_map[i]),
                    "left_offset_m": float(left_map[i]),
                    "right_offset_m": float(right_map[i]),
                    "bike_fraction": float(frac_map[i]),
                }
            )

    write_track_width_plot(
        out_path=out_dir / "track_width_map.png",
        gps_xy=gps_xy,
        left_offsets_m=left_map,
        right_offsets_m=right_map,
        valid_mask=np.ones(len(gps_rows), dtype=bool),
        plot_every=args.plot_every,
    )

    summary = {
        "recording": str(recording),
        "segmentation": str(segmentation),
        "output_dir": str(out_dir),
        "label_counts": label_counts,
        "pitch_deg": pitch_deg,
        "pitch_debug": pitch_debug,
        "camera_height_m": args.camera_height_m,
        "gps_rows": len(gps_rows),
        "raw_estimate_rows": len(estimates),
        "median_width_m": float(np.median(width_map)),
        "p10_width_m": float(np.percentile(width_map, 10)),
        "p90_width_m": float(np.percentile(width_map, 90)),
    }
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=2))
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
