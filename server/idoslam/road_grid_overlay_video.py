#!/usr/bin/env python3
"""
Render a stitched debugging video for the bike track recording.

Left side:
- original RGB frame
- traversable road/pavement mask overlay
- road-plane grid reprojected into the image

Right side:
- current percentage from the left road edge
- live canonical track visualization
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
import numpy as np

_server_root = Path(__file__).resolve().parent.parent
_project_root = _server_root.parent
for _p in (str(_project_root), str(_project_root / "proto"), str(_server_root)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from proto import perceiver_pb2
from idoslam.common import (
    canonical_output_dir,
    camera_from_first_frame,
    decode_rgb,
    iter_messages,
    load_segmentation_index,
    plane_output_dir,
    road_debug_video_output_path,
    seg_path,
)
from idoslam.track_width_map_plane import (
    TRAVERSABLE_LABELS,
    build_label_mask,
    estimate_bike_center_x,
    segment_candidates,
)


TRACK_COLORS_BGR = [
    (0, 165, 255),
    (0, 220, 0),
    (255, 120, 0),
    (255, 90, 180),
    (255, 255, 0),
    (200, 140, 255),
]


@dataclass
class FrameTrackRow:
    frame_index: int
    frame_number: int
    timestamp_ns: int
    lap_id: int
    is_partial_lap: bool
    progress_fraction: float
    trajectory_lateral_m: float
    trajectory_x: float
    trajectory_y: float
    width_m: float
    half_width_m: float


@dataclass
class CanonicalTrack:
    center: np.ndarray
    left: np.ndarray
    right: np.ndarray


@dataclass
class ContourOverlay:
    raw_left_points: np.ndarray
    raw_right_points: np.ndarray
    display_left_polyline: np.ndarray
    display_right_polyline: np.ndarray
    display_mid_polyline: np.ndarray


class GroundProjector:
    def __init__(self, intr: dict[str, float], pitch_deg: float, camera_height_m: float) -> None:
        self.fx = float(intr["fx"])
        self.fy = float(intr["fy"])
        self.cx = float(intr["cx"])
        self.cy = float(intr["cy"])
        self.camera_height_m = float(camera_height_m)

        pitch = math.radians(float(pitch_deg))
        r0 = np.array([[1.0, 0.0, 0.0], [0.0, 0.0, 1.0], [0.0, -1.0, 0.0]], dtype=np.float64)
        rx = np.array(
            [
                [1.0, 0.0, 0.0],
                [0.0, math.cos(pitch), math.sin(pitch)],
                [0.0, -math.sin(pitch), math.cos(pitch)],
            ],
            dtype=np.float64,
        )
        self.cam_to_ground = rx @ r0
        self.ground_to_cam = self.cam_to_ground.T

    def image_to_ground(self, u: float, v: float) -> np.ndarray | None:
        ray = np.array([(u - self.cx) / self.fx, (v - self.cy) / self.fy, 1.0], dtype=np.float64)
        ground_ray = self.cam_to_ground @ ray
        if ground_ray[2] >= -1e-6:
            return None
        scale = -self.camera_height_m / ground_ray[2]
        point = scale * ground_ray
        return point[:2]

    def ground_to_image(self, x_lateral_m: float, y_forward_m: float) -> tuple[int, int] | None:
        ground_point = np.array([x_lateral_m, y_forward_m, -self.camera_height_m], dtype=np.float64)
        cam_point = self.ground_to_cam @ ground_point
        if cam_point[2] <= 1e-6:
            return None
        u = self.fx * cam_point[0] / cam_point[2] + self.cx
        v = self.fy * cam_point[1] / cam_point[2] + self.cy
        return int(round(u)), int(round(v))


class GroundPlaneContourTracker:
    def __init__(
        self,
        projector: GroundProjector,
        image_shape: tuple[int, int],
        forward_near_m: float = 1.5,
        forward_far_m: float = 18.0,
        forward_step_m: float = 0.5,
        hold_frames: int = 6,
        ema_alpha: float = 0.35,
        max_measurement_delta_m: float = 1.5,
    ) -> None:
        self.projector = projector
        self.image_shape = image_shape
        self.forward_bins = np.arange(forward_near_m, forward_far_m + 1e-6, forward_step_m, dtype=np.float64)
        self.hold_frames = int(hold_frames)
        self.ema_alpha = float(ema_alpha)
        self.max_measurement_delta_m = float(max_measurement_delta_m)
        self.left_track = np.full(len(self.forward_bins), np.nan, dtype=np.float64)
        self.right_track = np.full(len(self.forward_bins), np.nan, dtype=np.float64)
        self.left_age = np.full(len(self.forward_bins), self.hold_frames + 1, dtype=np.int32)
        self.right_age = np.full(len(self.forward_bins), self.hold_frames + 1, dtype=np.int32)

    def _extract_measurements(
        self,
        road_mask: np.ndarray,
        bike_mask: np.ndarray,
        anchor_x: float,
    ) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
        h, w = road_mask.shape
        sample_rows_desc = np.arange(h - 24, int(0.16 * h), -3, dtype=np.int32)
        min_segment_px = max(8, int(0.02 * w))
        min_total_width_px = max(60, int(0.16 * w))
        bike_dilated = cv2.dilate(
            bike_mask,
            cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (25, 25)),
            iterations=1,
        )

        raw_left_img: list[list[float]] = []
        raw_right_img: list[list[float]] = []
        raw_left_ground: list[list[float]] = []
        raw_right_ground: list[list[float]] = []
        prev_left_x: float | None = None
        prev_right_x: float | None = None

        for y in sample_rows_desc:
            segments = sorted(segment_candidates(road_mask[y] > 0, min_segment_px), key=lambda seg: seg[0])
            if not segments:
                continue
            left_edge = int(min(seg[0] for seg in segments))
            right_edge = int(max(seg[1] for seg in segments))
            if (right_edge - left_edge) < min_total_width_px:
                continue

            # Reject implausible horizontal jumps but keep the outermost corridor envelope.
            if prev_left_x is not None and abs(left_edge - prev_left_x) > 160:
                continue
            if prev_right_x is not None and abs(right_edge - prev_right_x) > 160:
                continue

            if bike_dilated[y, max(0, left_edge - 2) : min(w, left_edge + 3)].any():
                left_ok = False
            else:
                left_ok = True
            if bike_dilated[y, max(0, right_edge - 2) : min(w, right_edge + 3)].any():
                right_ok = False
            else:
                right_ok = True

            if left_ok:
                ground_left = self.projector.image_to_ground(float(left_edge), float(y))
                if ground_left is not None and self.forward_bins[0] <= ground_left[1] <= self.forward_bins[-1]:
                    raw_left_img.append([left_edge, y])
                    raw_left_ground.append([ground_left[0], ground_left[1]])
                    prev_left_x = float(left_edge)
            if right_ok:
                ground_right = self.projector.image_to_ground(float(right_edge), float(y))
                if ground_right is not None and self.forward_bins[0] <= ground_right[1] <= self.forward_bins[-1]:
                    raw_right_img.append([right_edge, y])
                    raw_right_ground.append([ground_right[0], ground_right[1]])
                    prev_right_x = float(right_edge)

        return (
            np.asarray(raw_left_img, dtype=np.float64),
            np.asarray(raw_right_img, dtype=np.float64),
            np.asarray(raw_left_ground, dtype=np.float64),
            np.asarray(raw_right_ground, dtype=np.float64),
        )

    def _resample_lateral(self, ground_points: np.ndarray) -> np.ndarray:
        if len(ground_points) < 3:
            return np.full(len(self.forward_bins), np.nan, dtype=np.float64)
        order = np.argsort(ground_points[:, 1])
        ground_sorted = ground_points[order]
        y = ground_sorted[:, 1]
        x = ground_sorted[:, 0]
        keep = np.r_[True, np.diff(y) > 1e-4]
        y = y[keep]
        x = x[keep]
        if len(y) < 3:
            return np.full(len(self.forward_bins), np.nan, dtype=np.float64)
        sampled = np.full(len(self.forward_bins), np.nan, dtype=np.float64)
        valid = (self.forward_bins >= y[0]) & (self.forward_bins <= y[-1])
        sampled[valid] = np.interp(self.forward_bins[valid], y, x)
        return sampled

    def _update_track(self, track: np.ndarray, age: np.ndarray, measurement: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        updated = track.copy()
        updated_age = age.copy()
        for idx, meas in enumerate(measurement):
            if np.isfinite(meas):
                if np.isfinite(updated[idx]):
                    delta = float(meas - updated[idx])
                    if abs(delta) > self.max_measurement_delta_m:
                        meas = float(updated[idx] + np.sign(delta) * self.max_measurement_delta_m)
                    updated[idx] = (1.0 - self.ema_alpha) * updated[idx] + self.ema_alpha * meas
                else:
                    updated[idx] = float(meas)
                updated_age[idx] = 0
            else:
                if np.isfinite(updated[idx]) and updated_age[idx] < self.hold_frames:
                    updated_age[idx] += 1
                else:
                    updated[idx] = np.nan
                    updated_age[idx] = self.hold_frames + 1
        return updated, updated_age

    def _ground_track_to_image(self, track: np.ndarray) -> np.ndarray:
        pts: list[list[int]] = []
        for lateral_m, forward_m in zip(track, self.forward_bins):
            if not np.isfinite(lateral_m):
                continue
            image_pt = self.projector.ground_to_image(float(lateral_m), float(forward_m))
            if image_pt is None:
                continue
            u, v = image_pt
            if 0 <= u < self.image_shape[1] and 0 <= v < self.image_shape[0]:
                pts.append([u, v])
        return np.asarray(pts, dtype=np.int32)

    def _inset_points(self, points: np.ndarray, margin: int = 6) -> np.ndarray:
        if len(points) == 0:
            return np.zeros((0, 2), dtype=np.int32)
        out = points.astype(np.int32).copy()
        out[:, 0] = np.clip(out[:, 0], margin, self.image_shape[1] - 1 - margin)
        out[:, 1] = np.clip(out[:, 1], margin, self.image_shape[0] - 1 - margin)
        return out

    def _merge_display_polyline(self, raw_points: np.ndarray, stable_points: np.ndarray) -> np.ndarray:
        if len(raw_points) == 0 and len(stable_points) == 0:
            return np.zeros((0, 2), dtype=np.int32)
        inputs = []
        if len(stable_points):
            inputs.append(stable_points.astype(np.float64))
        if len(raw_points):
            raw_float = raw_points.astype(np.float64)
            inputs.extend([raw_float, raw_float])  # make current observations dominate the display line
        combined = np.vstack(inputs)
        buckets: dict[int, list[np.ndarray]] = {}
        for point in combined:
            key = int(round(point[1] / 4.0))
            buckets.setdefault(key, []).append(point)
        merged: list[list[int]] = []
        prev_y: int | None = None
        for key in sorted(buckets):
            pts = np.asarray(buckets[key], dtype=np.float64)
            x = int(round(np.median(pts[:, 0])))
            y = int(round(np.median(pts[:, 1])))
            if prev_y is not None and (y - prev_y) > 40:
                continue
            merged.append([x, y])
            prev_y = y
        return self._inset_points(np.asarray(merged, dtype=np.int32))

    def _raw_midpoints(self, raw_left: np.ndarray, raw_right: np.ndarray) -> np.ndarray:
        if len(raw_left) == 0 or len(raw_right) == 0:
            return np.zeros((0, 2), dtype=np.int32)
        right_by_y = {int(pt[1]): int(pt[0]) for pt in raw_right}
        mids: list[list[int]] = []
        for left_pt in raw_left:
            y = int(left_pt[1])
            x_right = right_by_y.get(y)
            if x_right is None:
                continue
            mids.append([int(round(0.5 * (int(left_pt[0]) + x_right))), y])
        return self._inset_points(np.asarray(mids, dtype=np.int32))

    def update(self, road_mask: np.ndarray, bike_mask: np.ndarray, anchor_x: float) -> ContourOverlay:
        raw_left_img, raw_right_img, raw_left_ground, raw_right_ground = self._extract_measurements(
            road_mask=road_mask,
            bike_mask=bike_mask,
            anchor_x=anchor_x,
        )
        left_measurement = self._resample_lateral(raw_left_ground)
        right_measurement = self._resample_lateral(raw_right_ground)
        self.left_track, self.left_age = self._update_track(self.left_track, self.left_age, left_measurement)
        self.right_track, self.right_age = self._update_track(self.right_track, self.right_age, right_measurement)

        mid_track = np.full(len(self.forward_bins), np.nan, dtype=np.float64)
        both = np.isfinite(self.left_track) & np.isfinite(self.right_track)
        mid_track[both] = 0.5 * (self.left_track[both] + self.right_track[both])

        raw_left_display = self._inset_points(raw_left_img.astype(np.int32) if len(raw_left_img) else np.zeros((0, 2), dtype=np.int32))
        raw_right_display = self._inset_points(raw_right_img.astype(np.int32) if len(raw_right_img) else np.zeros((0, 2), dtype=np.int32))
        stable_left = self._ground_track_to_image(self.left_track)
        stable_right = self._ground_track_to_image(self.right_track)
        stable_mid = self._ground_track_to_image(mid_track)
        raw_mid = self._raw_midpoints(raw_left_display, raw_right_display)

        return ContourOverlay(
            raw_left_points=raw_left_display,
            raw_right_points=raw_right_display,
            display_left_polyline=self._merge_display_polyline(raw_left_display, stable_left),
            display_right_polyline=self._merge_display_polyline(raw_right_display, stable_right),
            display_mid_polyline=self._merge_display_polyline(raw_mid, stable_mid),
        )


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Render stitched road-contour debug video")
    p.add_argument("recording", type=Path, help="Path to .vis.pb recording")
    p.add_argument("--panel-width", type=int, default=560)
    p.add_argument("--mask-alpha", type=float, default=0.35)
    p.add_argument("--contour-hold-frames", type=int, default=6)
    p.add_argument("--contour-forward-near-m", type=float, default=1.5)
    p.add_argument("--contour-forward-far-m", type=float, default=18.0)
    p.add_argument("--contour-forward-step-m", type=float, default=0.5)
    p.add_argument("--max-frames", type=int, default=None)
    p.add_argument("--start-frame", type=int, default=0)
    p.add_argument("--fps", type=float, default=30.0)
    p.add_argument("--codec", type=str, default="mp4v")
    return p.parse_args()


def recording_stem(recording: Path) -> str:
    return recording.name.removesuffix(".vis.pb") if recording.name.endswith(".vis.pb") else recording.stem


def default_track_width_dir(recording: Path) -> Path:
    return plane_output_dir(recording)


def default_canonical_dir(recording: Path) -> Path:
    return canonical_output_dir(recording)


def default_output_path(recording: Path) -> Path:
    return road_debug_video_output_path(recording)


def alpha_fill_mask(
    canvas: np.ndarray,
    mask: np.ndarray,
    color_bgr: tuple[int, int, int],
    alpha: float,
) -> None:
    if not np.any(mask):
        return
    alpha = float(np.clip(alpha, 0.0, 1.0))
    overlay = np.zeros_like(canvas)
    overlay[mask > 0] = color_bgr
    canvas[mask > 0] = cv2.addWeighted(canvas[mask > 0], 1.0 - alpha, overlay[mask > 0], alpha, 0.0)


def draw_percentage_gauge(
    panel: np.ndarray,
    left_fraction: float,
    row: FrameTrackRow,
) -> None:
    left_fraction = float(np.clip(left_fraction, 0.0, 1.0))
    left_percent = 100.0 * left_fraction
    width_m = float(row.width_m)
    left_dist_m = width_m * left_fraction
    right_dist_m = width_m - left_dist_m

    cv2.putText(panel, "Position On Road", (28, 46), cv2.FONT_HERSHEY_SIMPLEX, 0.95, (245, 245, 245), 2, cv2.LINE_AA)
    cv2.putText(panel, f"{left_percent:5.1f}% from left edge", (28, 100), cv2.FONT_HERSHEY_SIMPLEX, 0.95, (85, 220, 255), 2, cv2.LINE_AA)

    x0, x1 = 40, panel.shape[1] - 40
    y0 = 138
    cv2.line(panel, (x0, y0), (x1, y0), (130, 130, 130), 4, cv2.LINE_AA)
    marker_x = int(round(x0 + left_fraction * (x1 - x0)))
    cv2.circle(panel, (marker_x, y0), 10, (85, 220, 255), -1, cv2.LINE_AA)
    cv2.putText(panel, "Left", (x0 - 10, y0 + 28), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (210, 210, 210), 1, cv2.LINE_AA)
    cv2.putText(panel, "Right", (x1 - 30, y0 + 28), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (210, 210, 210), 1, cv2.LINE_AA)

    cv2.putText(panel, f"road width  {width_m:0.2f} m", (28, 190), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (235, 235, 235), 1, cv2.LINE_AA)
    cv2.putText(panel, f"left dist   {left_dist_m:0.2f} m", (28, 220), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (235, 235, 235), 1, cv2.LINE_AA)
    cv2.putText(panel, f"right dist  {right_dist_m:0.2f} m", (28, 250), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (235, 235, 235), 1, cv2.LINE_AA)
    cv2.putText(panel, f"lap {row.lap_id}   progress {100.0 * row.progress_fraction:0.1f}%", (28, 280), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (205, 205, 205), 1, cv2.LINE_AA)


def fit_points_to_rect(points_xy: np.ndarray, width_px: int, height_px: int, margin_px: int) -> tuple[np.ndarray, float, np.ndarray]:
    mins = points_xy.min(axis=0)
    maxs = points_xy.max(axis=0)
    span = np.maximum(maxs - mins, 1e-6)
    scale = min((width_px - 2 * margin_px) / span[0], (height_px - 2 * margin_px) / span[1])
    scaled = (points_xy - mins[None, :]) * scale
    offset = np.array(
        [
            margin_px + 0.5 * ((width_px - 2 * margin_px) - scaled[:, 0].max()),
            margin_px + 0.5 * ((height_px - 2 * margin_px) - scaled[:, 1].max()),
        ],
        dtype=np.float64,
    )
    mapped = scaled + offset[None, :]
    mapped[:, 1] = height_px - mapped[:, 1]
    return mapped, scale, mins


def load_frame_track_rows(csv_path: Path) -> list[FrameTrackRow]:
    rows: list[FrameTrackRow] = []
    with csv_path.open() as f:
        for row in csv.DictReader(f):
            rows.append(
                FrameTrackRow(
                    frame_index=int(row["frame_index"]),
                    frame_number=int(row["frame_number"]),
                    timestamp_ns=int(row["timestamp_ns"]),
                    lap_id=int(row["lap_id"]),
                    is_partial_lap=row["is_partial_lap"].lower() == "true",
                    progress_fraction=float(row["progress_fraction"]),
                    trajectory_lateral_m=float(row["trajectory_lateral_m"]),
                    trajectory_x=float(row["trajectory_x"]),
                    trajectory_y=float(row["trajectory_y"]),
                    width_m=float(row["width_m"]),
                    half_width_m=float(row["half_width_m"]),
                )
            )
    return rows


def load_canonical_track(csv_path: Path) -> CanonicalTrack:
    center: list[list[float]] = []
    left: list[list[float]] = []
    right: list[list[float]] = []
    with csv_path.open() as f:
        for row in csv.DictReader(f):
            center.append([float(row["center_x"]), float(row["center_y"])])
            left.append([float(row["left_x"]), float(row["left_y"])])
            right.append([float(row["right_x"]), float(row["right_y"])])
    return CanonicalTrack(center=np.asarray(center), left=np.asarray(left), right=np.asarray(right))


def transform_xy(points_xy: np.ndarray, mins: np.ndarray, scale: float, width_px: int, height_px: int, margin_px: int) -> np.ndarray:
    scaled = (points_xy - mins[None, :]) * scale
    offset = np.array(
        [
            margin_px + 0.5 * ((width_px - 2 * margin_px) - scaled[:, 0].max()),
            margin_px + 0.5 * ((height_px - 2 * margin_px) - scaled[:, 1].max()),
        ],
        dtype=np.float64,
    )
    mapped = scaled + offset[None, :]
    mapped[:, 1] = height_px - mapped[:, 1]
    return mapped


def build_track_map_base(
    canonical: CanonicalTrack,
    frame_rows: list[FrameTrackRow],
    map_width: int,
    map_height: int,
) -> tuple[np.ndarray, np.ndarray]:
    all_points = np.vstack(
        [
            canonical.center,
            canonical.left,
            canonical.right,
            np.asarray([[row.trajectory_x, row.trajectory_y] for row in frame_rows], dtype=np.float64),
        ]
    )
    mapped_all, scale, mins = fit_points_to_rect(all_points, map_width, map_height, margin_px=28)
    c_count = len(canonical.center)
    l_count = len(canonical.left)
    r_count = len(canonical.right)
    center_px = mapped_all[:c_count].astype(np.int32)
    left_px = mapped_all[c_count : c_count + l_count].astype(np.int32)
    right_px = mapped_all[c_count + l_count : c_count + l_count + r_count].astype(np.int32)
    traj_px = mapped_all[c_count + l_count + r_count :].astype(np.int32)

    base = np.full((map_height, map_width, 3), (24, 24, 24), dtype=np.uint8)
    cv2.polylines(base, [left_px], True, (255, 210, 60), 2, cv2.LINE_AA)
    cv2.polylines(base, [right_px], True, (100, 220, 120), 2, cv2.LINE_AA)
    cv2.polylines(base, [center_px], True, (120, 120, 120), 1, cv2.LINE_AA)
    return base, traj_px


def draw_live_track_panel(
    panel: np.ndarray,
    base_map: np.ndarray,
    trajectory_px: np.ndarray,
    frame_rows: list[FrameTrackRow],
    frame_index: int,
) -> None:
    y0 = 320
    map_img = base_map.copy()

    current_row = frame_rows[frame_index]
    lap_id = current_row.lap_id
    lap_color = TRACK_COLORS_BGR[lap_id % len(TRACK_COLORS_BGR)] if lap_id >= 0 else (200, 200, 200)

    same_lap_indices = [i for i in range(max(0, frame_index - 450), frame_index + 1) if frame_rows[i].lap_id == lap_id]
    if len(same_lap_indices) >= 2:
        pts = trajectory_px[np.asarray(same_lap_indices, dtype=np.int32)]
        cv2.polylines(map_img, [pts], False, lap_color, 3, cv2.LINE_AA)

    current_pt = tuple(int(v) for v in trajectory_px[frame_index])
    cv2.circle(map_img, current_pt, 7, lap_color, -1, cv2.LINE_AA)
    cv2.circle(map_img, current_pt, 10, (255, 255, 255), 1, cv2.LINE_AA)

    panel[y0 : y0 + map_img.shape[0], 20 : 20 + map_img.shape[1]] = map_img
    cv2.putText(panel, "Canonical Track View", (28, y0 - 18), cv2.FONT_HERSHEY_SIMPLEX, 0.85, (245, 245, 245), 2, cv2.LINE_AA)


def render_frame(
    frame: perceiver_pb2.PerceiverDataFrame,
    row: FrameTrackRow,
    seg_frames: dict[tuple[int, int], object],
    contour_tracker: GroundPlaneContourTracker,
    panel_width: int,
    base_map: np.ndarray,
    trajectory_px: np.ndarray,
    frame_rows: list[FrameTrackRow],
    mask_alpha: float,
) -> np.ndarray:
    bgr = decode_rgb(frame)
    h, w = bgr.shape[:2]
    seg_frame = seg_frames.get((int(frame.frame_identifier.frame_number), int(frame.frame_identifier.timestamp_ns)))
    road_mask = build_label_mask(seg_frame, TRAVERSABLE_LABELS, (h, w))
    bike_mask = build_label_mask(seg_frame, ("bike",), (h, w))
    if np.any(road_mask):
        road_mask = cv2.morphologyEx(
            road_mask,
            cv2.MORPH_CLOSE,
            cv2.getStructuringElement(cv2.MORPH_RECT, (31, 11)),
        )

    left_frame = bgr.copy()
    alpha_fill_mask(left_frame, road_mask, (210, 125, 35), mask_alpha)
    bike_center_x = estimate_bike_center_x(seg_frame, (h, w), 0.5 * w)
    overlay = contour_tracker.update(road_mask=road_mask, bike_mask=bike_mask, anchor_x=bike_center_x)
    if len(overlay.display_left_polyline) >= 2:
        cv2.polylines(left_frame, [overlay.display_left_polyline], False, (255, 245, 120), 3, cv2.LINE_AA)
    if len(overlay.display_right_polyline) >= 2:
        cv2.polylines(left_frame, [overlay.display_right_polyline], False, (100, 255, 180), 3, cv2.LINE_AA)
    if len(overlay.display_mid_polyline) >= 2:
        cv2.polylines(left_frame, [overlay.display_mid_polyline], False, (255, 255, 255), 2, cv2.LINE_AA)
    for pt in overlay.raw_left_points:
        center = tuple(int(v) for v in pt)
        cv2.circle(left_frame, center, 5, (15, 15, 15), -1, cv2.LINE_AA)
        cv2.circle(left_frame, center, 3, (255, 255, 0), -1, cv2.LINE_AA)
    for pt in overlay.raw_right_points:
        center = tuple(int(v) for v in pt)
        cv2.circle(left_frame, center, 5, (15, 15, 15), -1, cv2.LINE_AA)
        cv2.circle(left_frame, center, 3, (120, 255, 180), -1, cv2.LINE_AA)
    cv2.putText(
        left_frame,
        "Road mask + stabilized edge correspondences + midline",
        (22, 34),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.76,
        (255, 255, 255),
        2,
        cv2.LINE_AA,
    )

    panel = np.full((h, panel_width, 3), (18, 18, 18), dtype=np.uint8)
    left_fraction = 0.5 + 0.5 * row.trajectory_lateral_m / max(row.half_width_m, 1e-6)
    draw_percentage_gauge(panel, left_fraction, row)
    draw_live_track_panel(panel, base_map, trajectory_px, frame_rows, row.frame_index)

    stitched = np.hstack([left_frame, panel])
    cv2.putText(
        stitched,
        f"frame {row.frame_index}  ts {row.timestamp_ns}",
        (24, stitched.shape[0] - 18),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.65,
        (240, 240, 240),
        1,
        cv2.LINE_AA,
    )
    return stitched


def main() -> None:
    args = parse_args()
    recording = args.recording.resolve()
    segmentation = seg_path(recording)
    track_width_dir = default_track_width_dir(recording)
    canonical_dir = default_canonical_dir(recording)
    output_path = default_output_path(recording)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    plane_summary = json.loads((track_width_dir / "summary.json").read_text())
    frame_rows = load_frame_track_rows(canonical_dir / "lap_trajectories.csv")
    canonical = load_canonical_track(canonical_dir / "canonical_centerline.csv")
    seg_frames, _ = load_segmentation_index(segmentation)

    first_frame = next(iter_messages(recording, perceiver_pb2.PerceiverDataFrame), None)
    if first_frame is None:
        raise RuntimeError("Recording is empty")
    first_bgr = decode_rgb(first_frame)
    intr = camera_from_first_frame(first_frame, first_bgr.shape[1], first_bgr.shape[0], bottom_border=24)
    projector = GroundProjector(intr, plane_summary["pitch_deg"], plane_summary["camera_height_m"])
    contour_tracker = GroundPlaneContourTracker(
        projector=projector,
        image_shape=first_bgr.shape[:2],
        forward_near_m=args.contour_forward_near_m,
        forward_far_m=args.contour_forward_far_m,
        forward_step_m=args.contour_forward_step_m,
        hold_frames=args.contour_hold_frames,
    )

    map_height = max(320, first_bgr.shape[0] - 350)
    map_width = args.panel_width - 40
    base_map, trajectory_px = build_track_map_base(canonical, frame_rows, map_width, map_height)

    writer = cv2.VideoWriter(
        str(output_path),
        cv2.VideoWriter_fourcc(*args.codec),
        float(args.fps),
        (first_bgr.shape[1] + args.panel_width, first_bgr.shape[0]),
    )
    if not writer.isOpened():
        raise RuntimeError(f"Failed to open video writer for {output_path}")

    start_frame = max(0, int(args.start_frame))
    stop_frame = len(frame_rows) if args.max_frames is None else min(len(frame_rows), start_frame + int(args.max_frames))

    for frame_index, frame in enumerate(iter_messages(recording, perceiver_pb2.PerceiverDataFrame)):
        if frame_index < start_frame:
            continue
        if frame_index >= stop_frame:
            break
        if frame_index >= len(frame_rows):
            break
        stitched = render_frame(
            frame=frame,
            row=frame_rows[frame_index],
            seg_frames=seg_frames,
            contour_tracker=contour_tracker,
            panel_width=args.panel_width,
            base_map=base_map,
            trajectory_px=trajectory_px,
            frame_rows=frame_rows,
            mask_alpha=args.mask_alpha,
        )
        writer.write(stitched)
        if frame_index == start_frame or (frame_index - start_frame + 1) % 100 == 0:
            print(f"rendered {frame_index - start_frame + 1} / {stop_frame - start_frame} frames")

    writer.release()
    print(f"wrote {output_path}")


if __name__ == "__main__":
    main()
