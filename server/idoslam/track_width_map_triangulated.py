#!/usr/bin/env python3
"""
Estimate road width from triangulated boundary features.

This reduces dependence on a fixed camera height by:
- reusing the custom SIFT trajectory as camera poses
- matching SIFT features across a frame gap
- triangulating edge-adjacent boundary points
- aligning the monocular reconstruction to GPS for metric scale
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import sys
from dataclasses import dataclass
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
    fit_similarity,
    gps_to_local_xy,
    iter_messages,
    load_segmentation_index,
    pairwise_output_dir,
    project_track_to_2d,
    seg_path,
    triangulated_output_dir,
)


TRAVERSABLE_LABELS = ("road", "pavement")


@dataclass
class PoseRow:
    frame_index: int
    frame_number: int
    timestamp_ns: int
    center: np.ndarray
    world_r_cam: np.ndarray


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Estimate road width from triangulated edge features")
    p.add_argument("recording", type=Path, help="Path to .vis.pb recording")
    p.add_argument("--segmentation", type=Path, default=None, help="Optional .seg.pb path")
    p.add_argument("--trajectory-csv", type=Path, default=None, help="Path to trajectory_pairwise_sift.csv")
    p.add_argument("--output-dir", type=Path, default=None, help="Optional output directory")
    p.add_argument("--sample-every", type=int, default=4)
    p.add_argument("--pair-gap", type=int, default=4)
    p.add_argument("--mask-dilate", type=int, default=9)
    p.add_argument("--bottom-border", type=int, default=24)
    p.add_argument("--sift-nfeatures", type=int, default=2500)
    p.add_argument("--ratio-test", type=float, default=0.75)
    p.add_argument("--essential-threshold", type=float, default=1.5)
    p.add_argument("--min-good-matches", type=int, default=30)
    p.add_argument("--min-inliers", type=int, default=20)
    p.add_argument("--edge-distance-px", type=float, default=18.0)
    p.add_argument("--closing-width", type=int, default=181)
    p.add_argument("--closing-height", type=int, default=9)
    p.add_argument("--min-segment-frac", type=float, default=0.06)
    p.add_argument("--plot-every", type=int, default=25)
    return p.parse_args()


def output_path(recording: Path) -> Path:
    return triangulated_output_dir(recording)


def default_trajectory_path(recording: Path) -> Path:
    return pairwise_output_dir(recording) / "trajectory_pairwise_sift.csv"


def quaternion_xyzw_to_matrix(q: np.ndarray) -> np.ndarray:
    x, y, z, w = q
    xx, yy, zz = x * x, y * y, z * z
    xy, xz, yz = x * y, x * z, y * z
    wx, wy, wz = w * x, w * y, w * z
    return np.array(
        [
            [1.0 - 2.0 * (yy + zz), 2.0 * (xy - wz), 2.0 * (xz + wy)],
            [2.0 * (xy + wz), 1.0 - 2.0 * (xx + zz), 2.0 * (yz - wx)],
            [2.0 * (xz - wy), 2.0 * (yz + wx), 1.0 - 2.0 * (xx + yy)],
        ],
        dtype=np.float64,
    )


def load_trajectory(csv_path: Path) -> dict[int, PoseRow]:
    poses: dict[int, PoseRow] = {}
    with csv_path.open() as f:
        reader = csv.DictReader(f)
        for row in reader:
            center = np.array([float(row["x"]), float(row["y"]), float(row["z"])], dtype=np.float64)
            q = np.array([float(row["qx"]), float(row["qy"]), float(row["qz"]), float(row["qw"])], dtype=np.float64)
            poses[int(row["frame_index"])] = PoseRow(
                frame_index=int(row["frame_index"]),
                frame_number=int(row["frame_number"]),
                timestamp_ns=int(row["timestamp_ns"]),
                center=center,
                world_r_cam=quaternion_xyzw_to_matrix(q),
            )
    return poses


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


def edge_distance_map(mask: np.ndarray) -> np.ndarray:
    edge = cv2.morphologyEx(mask, cv2.MORPH_GRADIENT, np.ones((3, 3), np.uint8))
    inv = (edge == 0).astype(np.uint8)
    return cv2.distanceTransform(inv, cv2.DIST_L2, 3)


def image_to_side(
    road_mask: np.ndarray,
    x: float,
    y: float,
    bike_center_x: float,
    min_segment_px: int,
) -> str | None:
    yi = int(np.clip(round(y), 0, road_mask.shape[0] - 1))
    segments = segment_candidates(road_mask[yi] > 0, min_segment_px)
    seg = choose_corridor_segment(segments, bike_center_x)
    if seg is None:
        return None
    center_x = 0.5 * (seg[0] + seg[1])
    return "left" if x < center_x else "right"


def projection_matrix(intr: dict[str, float], pose: PoseRow) -> np.ndarray:
    k = np.array(
        [
            [intr["fx"], 0.0, intr["cx"]],
            [0.0, intr["fy"], intr["cy"]],
            [0.0, 0.0, 1.0],
        ],
        dtype=np.float64,
    )
    r_cw = pose.world_r_cam.T
    t_cw = -r_cw @ pose.center
    return k @ np.hstack([r_cw, t_cw.reshape(3, 1)])


def positive_depth(world_point: np.ndarray, pose: PoseRow) -> bool:
    cam_point = pose.world_r_cam.T @ (world_point - pose.center)
    return cam_point[2] > 1e-4


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
            alpha=0.2,
            linewidth=1.0,
        )
    ax.set_title("GPS Track With Triangulated Road Width")
    ax.set_aspect("equal", adjustable="box")
    ax.grid(True, alpha=0.3)
    ax.legend()
    fig.tight_layout()
    fig.savefig(out_path, dpi=180)
    plt.close(fig)


def main() -> None:
    args = parse_args()
    recording = args.recording.resolve()
    segmentation = args.segmentation.resolve() if args.segmentation else seg_path(recording)
    trajectory_csv = args.trajectory_csv.resolve() if args.trajectory_csv else default_trajectory_path(recording)
    out_dir = args.output_dir.resolve() if args.output_dir else output_path(recording)
    out_dir.mkdir(parents=True, exist_ok=True)

    poses = load_trajectory(trajectory_csv)
    seg_frames, label_counts = load_segmentation_index(segmentation)

    frame_iter = iter_messages(recording, perceiver_pb2.PerceiverDataFrame)
    first_frame = next(frame_iter, None)
    if first_frame is None:
        raise RuntimeError("Recording is empty")
    first_bgr = decode_rgb(first_frame)
    h, w = first_bgr.shape[:2]
    intr = camera_from_first_frame(first_frame, w, h, args.bottom_border)
    min_segment_px = max(24, int(w * args.min_segment_frac))
    closing_kernel = cv2.getStructuringElement(
        cv2.MORPH_RECT,
        (max(3, int(args.closing_width) | 1), max(3, int(args.closing_height) | 1)),
    )

    needed_indices: set[int] = set()
    max_frame = max(poses) if poses else -1
    start_indices = list(range(0, max(0, max_frame - args.pair_gap + 1), max(args.sample_every, 1)))
    for idx in start_indices:
        j = idx + args.pair_gap
        if idx in poses and j in poses:
            needed_indices.add(idx)
            needed_indices.add(j)

    selected_frames: dict[int, perceiver_pb2.PerceiverDataFrame] = {0: first_frame} if 0 in needed_indices else {}
    for frame_index, frame in enumerate(frame_iter, start=1):
        if frame_index in needed_indices:
            selected_frames[frame_index] = frame

    sift = cv2.SIFT_create(nfeatures=args.sift_nfeatures)
    matcher = cv2.BFMatcher(cv2.NORM_L2, crossCheck=False)

    feature_cache: dict[int, tuple[np.ndarray, list[cv2.KeyPoint], np.ndarray | None, np.ndarray, np.ndarray, float]] = {}

    def features_for(frame_index: int):
        cached = feature_cache.get(frame_index)
        if cached is not None:
            return cached
        frame = selected_frames[frame_index]
        bgr = decode_rgb(frame)
        gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
        seg_frame = seg_frames.get((int(frame.frame_identifier.frame_number), int(frame.frame_identifier.timestamp_ns)))
        road = build_label_mask(seg_frame, TRAVERSABLE_LABELS, (h, w))
        road = cv2.morphologyEx(road, cv2.MORPH_CLOSE, closing_kernel)
        road[-int(args.bottom_border) :, :] = 0
        edge_dist = edge_distance_map(road)
        bike_mask, _ = bike_mask_for_frame(seg_frame, gray.shape, "bike", args.mask_dilate, args.bottom_border)
        bike_center_x = estimate_bike_center_x(seg_frame, gray.shape, intr["cx"])
        kp, desc = sift.detectAndCompute(gray, cv2.bitwise_not(bike_mask))
        feature_cache[frame_index] = (gray, kp, desc, road, edge_dist, bike_center_x)
        return feature_cache[frame_index]

    boundary_points_world: list[np.ndarray] = []
    boundary_meta: list[tuple[int, int, str]] = []
    correspondence_rows: list[dict[str, object]] = []
    pair_logs: list[dict[str, object]] = []

    p_k = np.array(
        [
            [intr["fx"], 0.0, intr["cx"]],
            [0.0, intr["fy"], intr["cy"]],
            [0.0, 0.0, 1.0],
        ],
        dtype=np.float64,
    )

    for idx in start_indices:
        j = idx + args.pair_gap
        if idx not in poses or j not in poses or idx not in selected_frames or j not in selected_frames:
            continue
        gray_i, kp_i, desc_i, road_i, edge_i, bike_x_i = features_for(idx)
        gray_j, kp_j, desc_j, road_j, edge_j, bike_x_j = features_for(j)
        log_row = {
            "frame_index": idx,
            "paired_frame_index": j,
            "status": "no_descriptors",
            "good_match_count": 0,
            "inlier_count": 0,
            "triangulated_left": 0,
            "triangulated_right": 0,
        }
        if desc_i is None or desc_j is None or len(kp_i) < 8 or len(kp_j) < 8:
            pair_logs.append(log_row)
            continue
        raw_matches = matcher.knnMatch(desc_i, desc_j, k=2)
        good_matches: list[cv2.DMatch] = []
        for pair in raw_matches:
            if len(pair) < 2:
                continue
            m, n = pair
            if m.distance < args.ratio_test * n.distance:
                good_matches.append(m)
        log_row["good_match_count"] = len(good_matches)
        if len(good_matches) < args.min_good_matches:
            log_row["status"] = "too_few_matches"
            pair_logs.append(log_row)
            continue

        pts_i = np.float32([kp_i[m.queryIdx].pt for m in good_matches])
        pts_j = np.float32([kp_j[m.trainIdx].pt for m in good_matches])
        e_mat, e_mask = cv2.findEssentialMat(
            pts_i,
            pts_j,
            p_k,
            method=cv2.RANSAC,
            prob=0.999,
            threshold=args.essential_threshold,
        )
        if e_mat is None or e_mask is None:
            log_row["status"] = "essential_failed"
            pair_logs.append(log_row)
            continue

        _, _, _, pose_mask = cv2.recoverPose(e_mat, pts_i, pts_j, p_k)
        inlier_mask = pose_mask.reshape(-1).astype(bool)
        inlier_matches = [m for m, keep in zip(good_matches, inlier_mask) if keep]
        log_row["inlier_count"] = len(inlier_matches)
        if len(inlier_matches) < args.min_inliers:
            log_row["status"] = "low_inliers"
            pair_logs.append(log_row)
            continue

        p_i = projection_matrix(intr, poses[idx])
        p_j = projection_matrix(intr, poses[j])
        left_count = 0
        right_count = 0
        for m in inlier_matches:
            pt_i = np.array(kp_i[m.queryIdx].pt, dtype=np.float64)
            pt_j = np.array(kp_j[m.trainIdx].pt, dtype=np.float64)
            xi = int(np.clip(round(pt_i[0]), 0, w - 1))
            yi = int(np.clip(round(pt_i[1]), 0, h - 1))
            xj = int(np.clip(round(pt_j[0]), 0, w - 1))
            yj = int(np.clip(round(pt_j[1]), 0, h - 1))
            if edge_i[yi, xi] > args.edge_distance_px or edge_j[yj, xj] > args.edge_distance_px:
                continue
            side_i = image_to_side(road_i, pt_i[0], pt_i[1], bike_x_i, min_segment_px)
            side_j = image_to_side(road_j, pt_j[0], pt_j[1], bike_x_j, min_segment_px)
            if side_i is None or side_j is None or side_i != side_j:
                continue
            tri = cv2.triangulatePoints(
                p_i,
                p_j,
                pt_i.reshape(2, 1),
                pt_j.reshape(2, 1),
            )
            if abs(tri[3, 0]) < 1e-8:
                continue
            world_point = (tri[:3, 0] / tri[3, 0]).astype(np.float64)
            if not positive_depth(world_point, poses[idx]) or not positive_depth(world_point, poses[j]):
                continue
            boundary_points_world.append(world_point)
            boundary_meta.append((idx, j, side_i))
            correspondence_rows.append(
                {
                    "frame_index": idx,
                    "paired_frame_index": j,
                    "source_x": float(pt_i[0]),
                    "source_y": float(pt_i[1]),
                    "target_x": float(pt_j[0]),
                    "target_y": float(pt_j[1]),
                    "world_x": float(world_point[0]),
                    "world_y": float(world_point[1]),
                    "world_z": float(world_point[2]),
                    "side": side_i,
                }
            )
            if side_i == "left":
                left_count += 1
            else:
                right_count += 1
        log_row["status"] = "ok"
        log_row["triangulated_left"] = left_count
        log_row["triangulated_right"] = right_count
        pair_logs.append(log_row)

    if not boundary_points_world:
        raise RuntimeError("No triangulated boundary points were produced")

    traj_indices = sorted(poses)
    traj_xyz = np.vstack([poses[idx].center for idx in traj_indices])
    traj_mean = traj_xyz.mean(axis=0, keepdims=True)
    _, _, vh = np.linalg.svd(traj_xyz - traj_mean, full_matrices=False)
    traj_basis = vh[:2].T
    traj_2d = (traj_xyz - traj_mean) @ traj_basis

    gps_rows = []
    for frame_index, frame in enumerate(iter_messages(recording, perceiver_pb2.PerceiverDataFrame)):
        if frame.HasField("gps_location") and frame_index in poses:
            gps_rows.append(
                {
                    "frame_index": frame_index,
                    "frame_number": int(frame.frame_identifier.frame_number),
                    "timestamp_ns": int(frame.frame_identifier.timestamp_ns),
                    "latitude": float(frame.gps_location.latitude),
                    "longitude": float(frame.gps_location.longitude),
                    "altitude": float(frame.gps_location.altitude),
                    "accuracy": float(frame.gps_location.accuracy),
                }
            )
    gps_indices = [row["frame_index"] for row in gps_rows]
    gps_xy = gps_to_local_xy(
        np.array([row["latitude"] for row in gps_rows], dtype=np.float64),
        np.array([row["longitude"] for row in gps_rows], dtype=np.float64),
    )

    traj_index_to_local = {frame_index: i for i, frame_index in enumerate(traj_indices)}
    common_traj = np.vstack([traj_2d[traj_index_to_local[idx]] for idx in gps_indices])
    scale, rot, trans = fit_similarity(common_traj, gps_xy)
    aligned_traj_2d = (scale * (rot @ traj_2d.T)).T + trans

    boundary_world = np.vstack(boundary_points_world)
    boundary_2d = (boundary_world - traj_mean) @ traj_basis
    aligned_boundary_2d = (scale * (rot @ boundary_2d.T)).T + trans

    gps_index_to_row = {row["frame_index"]: i for i, row in enumerate(gps_rows)}
    left_samples: dict[int, list[float]] = {}
    right_samples: dict[int, list[float]] = {}

    aligned_traj_tangents = np.zeros_like(aligned_traj_2d)
    aligned_traj_tangents[1:-1] = aligned_traj_2d[2:] - aligned_traj_2d[:-2]
    aligned_traj_tangents[0] = aligned_traj_2d[1] - aligned_traj_2d[0]
    aligned_traj_tangents[-1] = aligned_traj_2d[-1] - aligned_traj_2d[-2]
    tangent_norm = np.linalg.norm(aligned_traj_tangents, axis=1, keepdims=True)
    tangent_norm[tangent_norm < 1e-6] = 1.0
    aligned_traj_tangents = aligned_traj_tangents / tangent_norm
    aligned_traj_normals = np.column_stack([-aligned_traj_tangents[:, 1], aligned_traj_tangents[:, 0]])

    for point_xy, (frame_index, _paired_frame_index, side) in zip(aligned_boundary_2d, boundary_meta):
        gps_row_idx = gps_index_to_row.get(frame_index)
        if gps_row_idx is None:
            continue
        traj_row_idx = traj_index_to_local.get(frame_index)
        if traj_row_idx is None:
            continue
        center_xy = aligned_traj_2d[traj_row_idx]
        offset = float(abs(np.dot(point_xy - center_xy, aligned_traj_normals[traj_row_idx])))
        if side == "left":
            left_samples.setdefault(gps_row_idx, []).append(offset)
        else:
            right_samples.setdefault(gps_row_idx, []).append(offset)

    left_offsets = np.full(len(gps_rows), np.nan, dtype=np.float64)
    right_offsets = np.full(len(gps_rows), np.nan, dtype=np.float64)
    for idx, values in left_samples.items():
        left_offsets[idx] = float(np.median(np.array(values)))
    for idx, values in right_samples.items():
        right_offsets[idx] = float(np.median(np.array(values)))
    valid_mask = np.isfinite(left_offsets) & np.isfinite(right_offsets)
    if not np.any(valid_mask):
        raise RuntimeError("No paired left/right width estimates were produced")

    left_fill = float(np.nanmedian(left_offsets[np.isfinite(left_offsets)]))
    right_fill = float(np.nanmedian(right_offsets[np.isfinite(right_offsets)]))
    left_offsets[~np.isfinite(left_offsets)] = left_fill
    right_offsets[~np.isfinite(right_offsets)] = right_fill
    left_offsets = smooth_series(left_offsets, 31)
    right_offsets = smooth_series(right_offsets, 31)
    width_map = left_offsets + right_offsets

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
                "method",
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
                    "left_offset_m": float(left_offsets[i]),
                    "right_offset_m": float(right_offsets[i]),
                    "method": "triangulated",
                }
            )

    ground_points_csv = out_dir / "ground_points.csv"
    with ground_points_csv.open("w", newline="") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=[
                "frame_index",
                "paired_frame_index",
                "world_x",
                "world_y",
                "world_z",
                "side",
            ],
        )
        writer.writeheader()
        for world_point, (frame_index, paired_frame_index, side) in zip(boundary_points_world, boundary_meta):
            writer.writerow(
                {
                    "frame_index": frame_index,
                    "paired_frame_index": paired_frame_index,
                    "world_x": float(world_point[0]),
                    "world_y": float(world_point[1]),
                    "world_z": float(world_point[2]),
                    "side": side,
                }
            )

    correspondences_csv = out_dir / "point_correspondences.csv"
    with correspondences_csv.open("w", newline="") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=[
                "frame_index",
                "paired_frame_index",
                "source_x",
                "source_y",
                "target_x",
                "target_y",
                "world_x",
                "world_y",
                "world_z",
                "side",
            ],
        )
        writer.writeheader()
        writer.writerows(correspondence_rows)

    with (out_dir / "pair_logs.json").open("w") as f:
        json.dump(pair_logs, f, indent=2)

    write_track_width_plot(
        out_path=out_dir / "track_width_map.png",
        gps_xy=gps_xy,
        left_offsets_m=left_offsets,
        right_offsets_m=right_offsets,
        valid_mask=np.ones(len(gps_rows), dtype=bool),
        plot_every=args.plot_every,
    )

    summary = {
        "recording": str(recording),
        "segmentation": str(segmentation),
        "trajectory_csv": str(trajectory_csv),
        "output_dir": str(out_dir),
        "label_counts": label_counts,
        "sample_every": args.sample_every,
        "pair_gap": args.pair_gap,
        "pair_log_count": len(pair_logs),
        "ok_pairs": sum(1 for row in pair_logs if row["status"] == "ok"),
        "triangulated_point_count": len(boundary_points_world),
        "gps_rows": len(gps_rows),
        "valid_width_rows": int(np.count_nonzero(valid_mask)),
        "median_width_m": float(np.median(width_map)),
        "p10_width_m": float(np.percentile(width_map, 10)),
        "p90_width_m": float(np.percentile(width_map, 90)),
    }
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=2))
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
