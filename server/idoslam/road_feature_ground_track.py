#!/usr/bin/env python3
"""
Render an approximate road track from on-road SIFT correspondences.

The red points in the SIFT debug video are correspondences that landed on the
road/pavement segmentation masks. This tool re-triangulates those points using
the post-GPS-refinement poses, fits a ground plane, projects both the road
features and camera centers onto that plane, and writes a top-down PNG.
"""

from __future__ import annotations

import argparse
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

from proto import idoslam_pb2, perceiver_pb2
from idoslam.common import (
    apply_pairwise_global_alignment,
    camera_from_first_frame,
    decode_rgb,
    estimate_pairwise_global_alignment,
    gps_to_local_xy,
    idoslam_proto_path,
    iter_messages,
    road_feature_track_output_path,
)
from idoslam.export import read_idoslam_pb


@dataclass
class PoseRow:
    frame_index: int
    frame_number: int
    timestamp_ns: int
    center: np.ndarray
    world_r_cam: np.ndarray


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Render a ground-plane road-feature track PNG")
    p.add_argument("recording", type=Path, help="Path to .vis.pb recording")
    p.add_argument("--output", type=Path, default=None, help="Output PNG path")
    p.add_argument("--max-correspondences", type=int, default=60000)
    p.add_argument("--max-reprojection-error-px", type=float, default=8.0)
    p.add_argument("--plane-percentile", type=float, default=85.0)
    p.add_argument("--allow-raw-poses", action="store_true", help="Fall back to raw poses if refined poses are absent")
    return p.parse_args()


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


def pose_rows_from_proto(poses: list[idoslam_pb2.IdoSlamFramePose]) -> dict[int, PoseRow]:
    out: dict[int, PoseRow] = {}
    for pose in poses:
        q = np.array(
            [
                float(pose.world_pose.rotation.x),
                float(pose.world_pose.rotation.y),
                float(pose.world_pose.rotation.z),
                float(pose.world_pose.rotation.w),
            ],
            dtype=np.float64,
        )
        out[int(pose.frame_index)] = PoseRow(
            frame_index=int(pose.frame_index),
            frame_number=int(pose.frame_id.frame_number),
            timestamp_ns=int(pose.frame_id.timestamp_ns),
            center=np.array(
                [
                    float(pose.world_pose.position.x),
                    float(pose.world_pose.position.y),
                    float(pose.world_pose.position.z),
                ],
                dtype=np.float64,
            ),
            world_r_cam=quaternion_xyzw_to_matrix(q),
        )
    return out


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


def positive_depth_mask(world_points: np.ndarray, pose: PoseRow) -> np.ndarray:
    cam_points = (pose.world_r_cam.T @ (world_points - pose.center).T).T
    return cam_points[:, 2] > 1e-4


def reprojection_error_px(p_mat: np.ndarray, world_points: np.ndarray, image_points: np.ndarray) -> np.ndarray:
    homog = np.column_stack([world_points, np.ones(len(world_points), dtype=np.float64)])
    proj = (p_mat @ homog.T).T
    valid = np.abs(proj[:, 2]) > 1e-9
    xy = np.full((len(world_points), 2), np.nan, dtype=np.float64)
    xy[valid] = proj[valid, :2] / proj[valid, 2:3]
    return np.linalg.norm(xy - image_points, axis=1)


def fit_plane(points: np.ndarray, percentile: float) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    if len(points) < 3:
        raise RuntimeError("Need at least three road points to fit a ground plane")
    center = np.median(points, axis=0)
    _, _, vh = np.linalg.svd(points - center[None, :], full_matrices=False)
    normal = vh[-1]
    dists = np.abs((points - center[None, :]) @ normal)
    cutoff = float(np.percentile(dists, np.clip(percentile, 10.0, 99.0)))
    keep = dists <= max(cutoff, 1e-9)
    if np.count_nonzero(keep) >= 3:
        center = points[keep].mean(axis=0)
        _, _, vh = np.linalg.svd(points[keep] - center[None, :], full_matrices=False)
        normal = vh[-1]
    normal = normal / max(np.linalg.norm(normal), 1e-12)
    return center, normal, keep


def plane_basis_from_track(centers: np.ndarray, plane_center: np.ndarray, normal: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    projected = centers - ((centers - plane_center[None, :]) @ normal)[:, None] * normal[None, :]
    centered = projected - projected.mean(axis=0, keepdims=True)
    _, _, vh = np.linalg.svd(centered, full_matrices=False)
    axis_x = vh[0]
    axis_x = axis_x - float(axis_x @ normal) * normal
    axis_x = axis_x / max(np.linalg.norm(axis_x), 1e-12)
    axis_y = np.cross(normal, axis_x)
    axis_y = axis_y / max(np.linalg.norm(axis_y), 1e-12)
    return axis_x, axis_y


def project_to_plane_2d(points: np.ndarray, origin: np.ndarray, normal: np.ndarray, axis_x: np.ndarray, axis_y: np.ndarray) -> np.ndarray:
    projected = points - ((points - origin[None, :]) @ normal)[:, None] * normal[None, :]
    rel = projected - origin[None, :]
    return np.column_stack([rel @ axis_x, rel @ axis_y])


def load_gps_xy_by_timestamp(recording: Path) -> dict[int, np.ndarray]:
    rows: list[tuple[int, float, float]] = []
    for frame in iter_messages(recording, perceiver_pb2.PerceiverDataFrame):
        if frame.HasField("gps_location"):
            rows.append(
                (
                    int(frame.frame_identifier.timestamp_ns),
                    float(frame.gps_location.latitude),
                    float(frame.gps_location.longitude),
                )
            )
    if not rows:
        return {}
    gps_xy = gps_to_local_xy(
        np.array([row[1] for row in rows], dtype=np.float64),
        np.array([row[2] for row in rows], dtype=np.float64),
    )
    return {row[0]: gps_xy[i] for i, row in enumerate(rows)}


def triangulate_road_correspondences(
    response: idoslam_pb2.IdoSlamResponse,
    poses: dict[int, PoseRow],
    intr: dict[str, float],
    max_correspondences: int,
    max_reprojection_error_px: float,
) -> tuple[np.ndarray, dict[str, int]]:
    candidates: list[tuple[int, int, float, float, float, float]] = []
    for debug in response.pair_debug:
        frame_index = int(debug.frame_index)
        paired_frame_index = int(debug.paired_frame_index)
        if frame_index not in poses or paired_frame_index not in poses:
            continue
        for corr in debug.correspondences:
            if not corr.on_road or not corr.inlier:
                continue
            candidates.append(
                (
                    frame_index,
                    paired_frame_index,
                    float(corr.source_x),
                    float(corr.source_y),
                    float(corr.target_x),
                    float(corr.target_y),
                )
            )

    if max_correspondences > 0 and len(candidates) > max_correspondences:
        idx = np.linspace(0, len(candidates) - 1, num=max_correspondences, dtype=np.int64)
        candidates = [candidates[int(i)] for i in idx]

    grouped: dict[tuple[int, int], list[tuple[float, float, float, float]]] = {}
    for frame_index, paired_frame_index, sx, sy, tx, ty in candidates:
        grouped.setdefault((frame_index, paired_frame_index), []).append((sx, sy, tx, ty))

    accepted_chunks: list[np.ndarray] = []
    rejected_depth = 0
    rejected_reprojection = 0
    for (frame_index, paired_frame_index), rows in grouped.items():
        pose_i = poses[frame_index]
        pose_j = poses[paired_frame_index]
        p_i = projection_matrix(intr, pose_i)
        p_j = projection_matrix(intr, pose_j)
        src = np.array([[row[0], row[1]] for row in rows], dtype=np.float64)
        dst = np.array([[row[2], row[3]] for row in rows], dtype=np.float64)
        tri = cv2.triangulatePoints(p_i, p_j, src.T, dst.T)
        valid_h = np.abs(tri[3]) > 1e-9
        if not np.any(valid_h):
            continue
        world = (tri[:3, valid_h] / tri[3:4, valid_h]).T
        src_valid = src[valid_h]
        dst_valid = dst[valid_h]
        depth_ok = positive_depth_mask(world, pose_i) & positive_depth_mask(world, pose_j)
        rejected_depth += int(np.count_nonzero(~depth_ok))
        if not np.any(depth_ok):
            continue
        world = world[depth_ok]
        src_valid = src_valid[depth_ok]
        dst_valid = dst_valid[depth_ok]
        err = np.maximum(
            reprojection_error_px(p_i, world, src_valid),
            reprojection_error_px(p_j, world, dst_valid),
        )
        reproj_ok = np.isfinite(err) & (err <= max_reprojection_error_px)
        rejected_reprojection += int(np.count_nonzero(~reproj_ok))
        if np.any(reproj_ok):
            accepted_chunks.append(world[reproj_ok])

    if not accepted_chunks:
        raise RuntimeError("No on-road SIFT correspondences triangulated with the post-refinement poses")
    points = np.vstack(accepted_chunks)
    stats = {
        "candidate_correspondences": len(candidates),
        "triangulated_points": len(points),
        "rejected_depth": rejected_depth,
        "rejected_reprojection": rejected_reprojection,
        "pair_count": len(grouped),
    }
    return points, stats


def render_plot(
    out_path: Path,
    road_xy: np.ndarray,
    pose_xy: np.ndarray,
    gps_xy: np.ndarray | None,
    title_suffix: str,
) -> None:
    fig, ax = plt.subplots(figsize=(12, 12))
    if len(road_xy):
        ax.scatter(
            road_xy[:, 0],
            road_xy[:, 1],
            s=1.0,
            c="#d62728",
            alpha=0.16,
            linewidths=0,
            label="Triangulated road SIFT points",
        )
    ax.plot(pose_xy[:, 0], pose_xy[:, 1], color="#111111", linewidth=1.8, label="Post-GPS pose track")
    ax.scatter(pose_xy[0, 0], pose_xy[0, 1], s=40, color="#1f77b4", label="start", zorder=5)
    ax.scatter(pose_xy[-1, 0], pose_xy[-1, 1], s=40, color="#ff7f0e", label="end", zorder=5)
    if gps_xy is not None and len(gps_xy) >= 2:
        ax.plot(gps_xy[:, 0], gps_xy[:, 1], color="#2f7f3f", linewidth=1.2, alpha=0.75, label="GPS")
    ax.set_title(f"Approx Road Track From On-Road SIFT Features{title_suffix}")
    ax.set_xlabel("x (m, GPS-aligned)" if gps_xy is not None else "ground-plane x")
    ax.set_ylabel("y (m, GPS-aligned)" if gps_xy is not None else "ground-plane y")
    ax.set_aspect("equal", adjustable="box")
    ax.grid(True, alpha=0.25)
    ax.legend(markerscale=4)
    fig.tight_layout()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path, dpi=180)
    plt.close(fig)


def main() -> None:
    args = parse_args()
    recording = args.recording.resolve()
    output = args.output.resolve() if args.output else road_feature_track_output_path(recording)

    first_frame = next(iter_messages(recording, perceiver_pb2.PerceiverDataFrame), None)
    if first_frame is None:
        raise RuntimeError("Recording is empty")
    first_bgr = decode_rgb(first_frame)
    intr = camera_from_first_frame(first_frame, first_bgr.shape[1], first_bgr.shape[0], 0)

    response = read_idoslam_pb(idoslam_proto_path(recording))
    pose_source = "refined_frame_poses"
    pose_list = list(response.refined_frame_poses)
    if not pose_list:
        if not args.allow_raw_poses:
            raise RuntimeError("No post-GPS-refinement poses found in checkpoint")
        pose_source = "frame_poses"
        pose_list = list(response.frame_poses)
    if not pose_list:
        raise RuntimeError("No poses found in checkpoint")

    poses = pose_rows_from_proto(pose_list)
    road_points, stats = triangulate_road_correspondences(
        response=response,
        poses=poses,
        intr=intr,
        max_correspondences=args.max_correspondences,
        max_reprojection_error_px=args.max_reprojection_error_px,
    )

    sorted_poses = [poses[idx] for idx in sorted(poses)]
    centers = np.vstack([pose.center for pose in sorted_poses])
    plane_center, plane_normal, plane_keep = fit_plane(road_points, args.plane_percentile)
    axis_x, axis_y = plane_basis_from_track(centers, plane_center, plane_normal)
    road_xy = project_to_plane_2d(road_points[plane_keep], plane_center, plane_normal, axis_x, axis_y)
    pose_xy = project_to_plane_2d(centers, plane_center, plane_normal, axis_x, axis_y)

    gps_by_ts = load_gps_xy_by_timestamp(recording)
    gps_xy_for_plot = None
    title_suffix = ""
    common_pose_xy: list[np.ndarray] = []
    common_gps_xy: list[np.ndarray] = []
    for i, pose in enumerate(sorted_poses):
        gps_xy = gps_by_ts.get(int(pose.timestamp_ns))
        if gps_xy is None:
            continue
        common_pose_xy.append(pose_xy[i])
        common_gps_xy.append(gps_xy)
    if len(common_pose_xy) >= 2:
        common_pose_arr = np.vstack(common_pose_xy)
        common_gps_arr = np.vstack(common_gps_xy)
        alignment = estimate_pairwise_global_alignment(common_pose_arr, common_gps_arr)
        road_xy = apply_pairwise_global_alignment(road_xy, alignment)
        pose_xy = apply_pairwise_global_alignment(pose_xy, alignment)
        gps_xy_for_plot = common_gps_arr
        title_suffix = (
            f" (theta={float(alignment['theta_deg']):.1f}deg, "
            f"scale_div={float(alignment['scale_divisor']):.2f})"
        )

    render_plot(output, road_xy, pose_xy, gps_xy_for_plot, title_suffix)

    summary = {
        "recording": str(recording),
        "output": str(output),
        "pose_source": pose_source,
        "pose_count": len(poses),
        "road_plane_points": int(np.count_nonzero(plane_keep)),
        "road_plane_point_fraction": float(np.count_nonzero(plane_keep) / max(len(road_points), 1)),
        "plane_percentile": float(args.plane_percentile),
    }
    summary.update(stats)
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
