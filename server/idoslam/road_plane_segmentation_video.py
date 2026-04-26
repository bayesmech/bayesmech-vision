#!/usr/bin/env python3
"""
Render road segmentation projected onto the fitted SIFT ground plane.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import cv2
import numpy as np

_server_root = Path(__file__).resolve().parent.parent
_project_root = _server_root.parent
for _p in (str(_project_root), str(_project_root / "proto"), str(_server_root)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from proto import perceiver_pb2
from idoslam.common import (
    camera_from_first_frame,
    decode_rgb,
    idoslam_proto_path,
    iter_messages,
    load_segmentation_index,
    road_plane_projection_video_output_path,
    seg_path,
)
from idoslam.export import read_idoslam_pb
from idoslam.road_feature_ground_track import (
    fit_plane,
    pose_rows_from_proto,
    triangulate_road_correspondences,
)
from idoslam.track_width_map_plane import TRAVERSABLE_LABELS, build_label_mask


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Render road-mask projection on the fitted SIFT ground plane")
    p.add_argument("recording", type=Path, help="Path to .vis.pb recording")
    p.add_argument("--output", type=Path, default=None)
    p.add_argument("--fps", type=float, default=20.0)
    p.add_argument("--codec", type=str, default="mp4v")
    p.add_argument("--start-frame", type=int, default=0)
    p.add_argument("--max-frames", type=int, default=0)
    p.add_argument("--mask-sample-step", type=int, default=6)
    p.add_argument("--edge-bin-m", type=float, default=0.75)
    p.add_argument("--forward-min-m", type=float, default=-5.0)
    p.add_argument("--edge-forward-min-m", type=float, default=1.0)
    p.add_argument("--forward-max-m", type=float, default=120.0)
    p.add_argument("--lateral-range-m", type=float, default=50.0)
    p.add_argument("--max-reprojection-error-px", type=float, default=8.0)
    p.add_argument("--plane-percentile", type=float, default=85.0)
    p.add_argument("--max-correspondences", type=int, default=60000)
    p.add_argument("--allow-raw-poses", action="store_true")
    return p.parse_args()


def alpha_fill_mask(frame: np.ndarray, mask: np.ndarray, color_bgr: tuple[int, int, int], alpha: float) -> None:
    if not np.any(mask):
        return
    overlay = np.full_like(frame, color_bgr)
    keep = mask > 0
    frame[keep] = cv2.addWeighted(frame[keep], 1.0 - alpha, overlay[keep], alpha, 0.0)


def image_points_to_plane(
    pixels_uv: np.ndarray,
    pose,
    intr: dict[str, float],
    plane_point: np.ndarray,
    plane_normal: np.ndarray,
) -> tuple[np.ndarray, np.ndarray]:
    if len(pixels_uv) == 0:
        return np.empty((0, 3), dtype=np.float64), np.zeros(0, dtype=bool)
    rays_cam = np.column_stack(
        [
            (pixels_uv[:, 0] - float(intr["cx"])) / float(intr["fx"]),
            (pixels_uv[:, 1] - float(intr["cy"])) / float(intr["fy"]),
            np.ones(len(pixels_uv), dtype=np.float64),
        ]
    )
    rays_world = (pose.world_r_cam @ rays_cam.T).T
    denom = rays_world @ plane_normal
    valid = np.abs(denom) > 1e-9
    t = np.full(len(pixels_uv), np.nan, dtype=np.float64)
    t[valid] = ((plane_point - pose.center) @ plane_normal) / denom[valid]
    valid &= t > 0.0
    points = pose.center[None, :] + rays_world * t[:, None]
    return points[valid], valid


def world_to_image(points_world: np.ndarray, pose, intr: dict[str, float], image_shape: tuple[int, int]) -> np.ndarray:
    if len(points_world) == 0:
        return np.empty((0, 2), dtype=np.int32)
    cam = (pose.world_r_cam.T @ (points_world - pose.center[None, :]).T).T
    valid = cam[:, 2] > 1e-6
    uv = np.full((len(points_world), 2), np.nan, dtype=np.float64)
    uv[valid, 0] = float(intr["fx"]) * cam[valid, 0] / cam[valid, 2] + float(intr["cx"])
    uv[valid, 1] = float(intr["fy"]) * cam[valid, 1] / cam[valid, 2] + float(intr["cy"])
    h, w = image_shape
    valid &= np.isfinite(uv[:, 0]) & np.isfinite(uv[:, 1])
    valid &= (uv[:, 0] >= 0) & (uv[:, 0] < w) & (uv[:, 1] >= 0) & (uv[:, 1] < h)
    return np.round(uv[valid]).astype(np.int32)


def local_ground_axes(pose, plane_normal: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    forward = pose.world_r_cam @ np.array([0.0, 0.0, 1.0], dtype=np.float64)
    forward = forward - float(forward @ plane_normal) * plane_normal
    if np.linalg.norm(forward) < 1e-6:
        forward = pose.world_r_cam @ np.array([0.0, -1.0, 0.0], dtype=np.float64)
        forward = forward - float(forward @ plane_normal) * plane_normal
    forward = forward / max(np.linalg.norm(forward), 1e-12)
    right = np.cross(plane_normal, forward)
    right = right / max(np.linalg.norm(right), 1e-12)
    return right, forward


def to_local_xy(points_world: np.ndarray, origin: np.ndarray, right: np.ndarray, forward: np.ndarray) -> np.ndarray:
    rel = points_world - origin[None, :]
    return np.column_stack([rel @ right, rel @ forward])


def from_local_xy(local_xy: np.ndarray, origin: np.ndarray, right: np.ndarray, forward: np.ndarray) -> np.ndarray:
    return origin[None, :] + local_xy[:, 0:1] * right[None, :] + local_xy[:, 1:2] * forward[None, :]


def sample_mask_pixels(mask: np.ndarray, step: int) -> np.ndarray:
    step = max(1, int(step))
    sampled = mask[::step, ::step] > 0
    ys, xs = np.nonzero(sampled)
    if len(xs) == 0:
        return np.empty((0, 2), dtype=np.float64)
    return np.column_stack([xs * step + 0.5 * step, ys * step + 0.5 * step]).astype(np.float64)


def estimate_edges(
    local_xy: np.ndarray,
    forward_min_m: float,
    forward_max_m: float,
    bin_m: float,
    min_points_per_bin: int = 4,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    if len(local_xy) == 0:
        empty = np.empty((0, 2), dtype=np.float64)
        return empty, empty, empty
    bins = np.arange(forward_min_m, forward_max_m + bin_m, bin_m)
    left: list[list[float]] = []
    right: list[list[float]] = []
    mid: list[list[float]] = []
    for lo, hi in zip(bins[:-1], bins[1:]):
        keep = (local_xy[:, 1] >= lo) & (local_xy[:, 1] < hi)
        if np.count_nonzero(keep) < min_points_per_bin:
            continue
        chunk = local_xy[keep]
        y = float(0.5 * (lo + hi))
        x_left = float(np.percentile(chunk[:, 0], 3.0))
        x_right = float(np.percentile(chunk[:, 0], 97.0))
        if x_right <= x_left:
            continue
        left.append([x_left, y])
        right.append([x_right, y])
        mid.append([0.5 * (x_left + x_right), y])
    return (
        np.asarray(left, dtype=np.float64).reshape(-1, 2),
        np.asarray(right, dtype=np.float64).reshape(-1, 2),
        np.asarray(mid, dtype=np.float64).reshape(-1, 2),
    )


def local_to_panel_px(
    local_xy: np.ndarray,
    panel_shape: tuple[int, int],
    lateral_range_m: float,
    forward_min_m: float,
    forward_max_m: float,
) -> np.ndarray:
    h, w = panel_shape
    if len(local_xy) == 0:
        return np.empty((0, 2), dtype=np.int32)
    x = local_xy[:, 0]
    y = local_xy[:, 1]
    u = (x / (2.0 * lateral_range_m) + 0.5) * (w - 1)
    v = (1.0 - (y - forward_min_m) / max(forward_max_m - forward_min_m, 1e-6)) * (h - 1)
    valid = (u >= 0) & (u < w) & (v >= 0) & (v < h)
    return np.round(np.column_stack([u[valid], v[valid]])).astype(np.int32)


def draw_points_and_polyline(frame: np.ndarray, points: np.ndarray, color: tuple[int, int, int], radius: int, thickness: int) -> None:
    if len(points) == 0:
        return
    for pt in points:
        cv2.circle(frame, tuple(int(v) for v in pt), radius, color, -1, cv2.LINE_AA)
    if len(points) >= 2:
        cv2.polylines(frame, [points.astype(np.int32)], False, color, thickness, cv2.LINE_AA)


def draw_panel(
    panel_shape: tuple[int, int],
    road_local_xy: np.ndarray,
    left_xy: np.ndarray,
    right_xy: np.ndarray,
    mid_xy: np.ndarray,
    lateral_range_m: float,
    forward_min_m: float,
    forward_max_m: float,
) -> np.ndarray:
    h, w = panel_shape
    panel = np.full((h, w, 3), (246, 246, 246), dtype=np.uint8)
    for x_m in np.arange(-lateral_range_m, lateral_range_m + 1e-6, 5.0):
        pts = local_to_panel_px(
            np.array([[x_m, forward_min_m], [x_m, forward_max_m]], dtype=np.float64),
            panel_shape,
            lateral_range_m,
            forward_min_m,
            forward_max_m,
        )
        if len(pts) == 2:
            cv2.line(panel, tuple(pts[0]), tuple(pts[1]), (218, 218, 218), 1, cv2.LINE_AA)
    for y_m in np.arange(np.ceil(forward_min_m / 5.0) * 5.0, forward_max_m + 1e-6, 5.0):
        pts = local_to_panel_px(
            np.array([[-lateral_range_m, y_m], [lateral_range_m, y_m]], dtype=np.float64),
            panel_shape,
            lateral_range_m,
            forward_min_m,
            forward_max_m,
        )
        if len(pts) == 2:
            cv2.line(panel, tuple(pts[0]), tuple(pts[1]), (218, 218, 218), 1, cv2.LINE_AA)

    road_px = local_to_panel_px(road_local_xy, panel_shape, lateral_range_m, forward_min_m, forward_max_m)
    for pt in road_px:
        cv2.circle(panel, tuple(int(v) for v in pt), 2, (225, 150, 40), -1, cv2.LINE_AA)

    left_px = local_to_panel_px(left_xy, panel_shape, lateral_range_m, forward_min_m, forward_max_m)
    right_px = local_to_panel_px(right_xy, panel_shape, lateral_range_m, forward_min_m, forward_max_m)
    mid_px = local_to_panel_px(mid_xy, panel_shape, lateral_range_m, forward_min_m, forward_max_m)
    draw_points_and_polyline(panel, left_px, (0, 0, 255), 4, 2)
    draw_points_and_polyline(panel, right_px, (0, 170, 0), 4, 2)
    draw_points_and_polyline(panel, mid_px, (255, 255, 255), 4, 2)

    camera_px = local_to_panel_px(
        np.array([[0.0, 0.0]], dtype=np.float64),
        panel_shape,
        lateral_range_m,
        forward_min_m,
        forward_max_m,
    )
    if len(camera_px):
        cv2.circle(panel, tuple(int(v) for v in camera_px[0]), 8, (0, 0, 0), -1, cv2.LINE_AA)
    cv2.putText(panel, "Ground plane view", (18, 34), cv2.FONT_HERSHEY_SIMPLEX, 0.85, (25, 25, 25), 2, cv2.LINE_AA)
    cv2.putText(panel, "blue=road projection  black=camera", (18, 66), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (55, 55, 55), 1, cv2.LINE_AA)
    return panel


def render_frame(
    frame: perceiver_pb2.PerceiverDataFrame,
    pose,
    seg_frames: dict[tuple[int, int], object],
    intr: dict[str, float],
    plane_point: np.ndarray,
    plane_normal: np.ndarray,
    args: argparse.Namespace,
) -> np.ndarray:
    bgr = decode_rgb(frame)
    h, w = bgr.shape[:2]
    seg_frame = seg_frames.get((int(frame.frame_identifier.frame_number), int(frame.frame_identifier.timestamp_ns)))
    road_mask = build_label_mask(seg_frame, TRAVERSABLE_LABELS, (h, w))
    if np.any(road_mask):
        road_mask = cv2.morphologyEx(
            road_mask,
            cv2.MORPH_CLOSE,
            cv2.getStructuringElement(cv2.MORPH_RECT, (31, 11)),
        )

    right_axis, forward_axis = local_ground_axes(pose, plane_normal)
    camera_on_plane = pose.center + float((plane_point - pose.center) @ plane_normal) * plane_normal

    sampled_uv = sample_mask_pixels(road_mask, args.mask_sample_step)
    road_world, _ = image_points_to_plane(sampled_uv, pose, intr, plane_point, plane_normal)
    road_local = to_local_xy(road_world, camera_on_plane, right_axis, forward_axis)
    keep = (
        (road_local[:, 1] >= args.forward_min_m)
        & (road_local[:, 1] <= args.forward_max_m)
        & (np.abs(road_local[:, 0]) <= args.lateral_range_m)
    )
    road_local = road_local[keep]

    left_xy, right_xy, mid_xy = estimate_edges(
        road_local,
        forward_min_m=args.edge_forward_min_m,
        forward_max_m=args.forward_max_m,
        bin_m=args.edge_bin_m,
    )

    left_world = from_local_xy(left_xy, camera_on_plane, right_axis, forward_axis)
    right_world = from_local_xy(right_xy, camera_on_plane, right_axis, forward_axis)
    mid_world = from_local_xy(mid_xy, camera_on_plane, right_axis, forward_axis)

    left_frame = bgr.copy()
    alpha_fill_mask(left_frame, road_mask, (220, 150, 60), 0.18)
    draw_points_and_polyline(left_frame, world_to_image(left_world, pose, intr, (h, w)), (0, 0, 255), 4, 2)
    draw_points_and_polyline(left_frame, world_to_image(right_world, pose, intr, (h, w)), (0, 220, 0), 4, 2)
    draw_points_and_polyline(left_frame, world_to_image(mid_world, pose, intr, (h, w)), (255, 255, 255), 4, 2)

    panel = draw_panel(
        panel_shape=(h, w),
        road_local_xy=road_local,
        left_xy=left_xy,
        right_xy=right_xy,
        mid_xy=mid_xy,
        lateral_range_m=args.lateral_range_m,
        forward_min_m=args.forward_min_m,
        forward_max_m=args.forward_max_m,
    )
    stitched = np.hstack([left_frame, panel])
    cv2.putText(
        stitched,
        f"frame {pose.frame_index}  ts {pose.timestamp_ns}",
        (22, h - 18),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.65,
        (245, 245, 245),
        1,
        cv2.LINE_AA,
    )
    return stitched


def main() -> None:
    args = parse_args()
    recording = args.recording.resolve()
    output = args.output.resolve() if args.output else road_plane_projection_video_output_path(recording)
    output.parent.mkdir(parents=True, exist_ok=True)

    first_frame = next(iter_messages(recording, perceiver_pb2.PerceiverDataFrame), None)
    if first_frame is None:
        raise RuntimeError("Recording is empty")
    first_bgr = decode_rgb(first_frame)
    h, w = first_bgr.shape[:2]
    intr = camera_from_first_frame(first_frame, w, h, 0)

    response = read_idoslam_pb(idoslam_proto_path(recording))
    pose_source = "refined_frame_poses"
    pose_list = list(response.refined_frame_poses)
    if not pose_list:
        if not args.allow_raw_poses:
            raise RuntimeError("No post-GPS-refinement poses found in checkpoint")
        pose_source = "frame_poses"
        pose_list = list(response.frame_poses)
    poses = pose_rows_from_proto(pose_list)
    if not poses:
        raise RuntimeError("No poses found in checkpoint")

    road_points, stats = triangulate_road_correspondences(
        response=response,
        poses=poses,
        intr=intr,
        max_correspondences=args.max_correspondences,
        max_reprojection_error_px=args.max_reprojection_error_px,
    )
    plane_point, plane_normal, plane_keep = fit_plane(road_points, args.plane_percentile)

    seg_frames, _ = load_segmentation_index(seg_path(recording))
    writer = cv2.VideoWriter(
        str(output),
        cv2.VideoWriter_fourcc(*args.codec),
        float(args.fps),
        (w * 2, h),
    )
    if not writer.isOpened():
        raise RuntimeError(f"Failed to open video writer for {output}")

    start_frame = max(0, int(args.start_frame))
    stop_frame = None if args.max_frames <= 0 else start_frame + int(args.max_frames)
    rendered = 0
    try:
        for frame_index, frame in enumerate(iter_messages(recording, perceiver_pb2.PerceiverDataFrame)):
            if frame_index < start_frame:
                continue
            if stop_frame is not None and frame_index >= stop_frame:
                break
            pose = poses.get(frame_index)
            if pose is None:
                continue
            stitched = render_frame(frame, pose, seg_frames, intr, plane_point, plane_normal, args)
            writer.write(stitched)
            rendered += 1
            if rendered == 1 or rendered % 100 == 0:
                print(f"rendered {rendered} frames")
    finally:
        writer.release()

    summary = {
        "recording": str(recording),
        "output": str(output),
        "pose_source": pose_source,
        "pose_count": len(poses),
        "rendered_frames": rendered,
        "plane_points": int(np.count_nonzero(plane_keep)),
    }
    summary.update(stats)
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
