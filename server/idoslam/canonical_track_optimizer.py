#!/usr/bin/env python3
"""
Collapse repeated laps of a closed track into one canonical track.

Pipeline:
1. load per-frame GPS positions and width estimates
2. estimate lap period from GPS self-similarity
3. split the recording into laps
4. build sparse GPS-close correspondence candidates between each lap and a reference lap
5. confirm candidates visually with masked SIFT
6. keep a monotone anchor set per lap
7. interpolate every frame onto canonical progress along the reference lap
8. optimize one canonical centerline and one canonical width profile
9. output per-lap trajectories inside the single canonical track
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
    bike_mask_for_frame,
    camera_from_first_frame,
    canonical_output_dir,
    decode_rgb,
    gps_to_local_xy,
    iter_messages,
    load_segmentation_index,
    plane_output_dir,
    seg_path,
)


@dataclass
class LapSegment:
    lap_id: int
    start: int
    end: int
    is_partial: bool = False


@dataclass
class Anchor:
    frame_index: int
    ref_frame_index: int
    gps_distance_m: float
    heading_cos: float
    good_matches: int
    inliers: int
    inlier_ratio: float
    score: float
    method: str


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Collapse repeated laps into one canonical track")
    p.add_argument("recording", type=Path, help="Path to .vis.pb recording")
    p.add_argument("--sample-every", type=int, default=20, help="Frame stride for cross-lap anchor search")
    p.add_argument("--gps-radius-m", type=float, default=4.0, help="GPS proximity threshold for correspondence candidates")
    p.add_argument("--min-heading-cos", type=float, default=0.5, help="Minimum heading cosine for candidate pairs")
    p.add_argument("--max-candidates-per-sample", type=int, default=3, help="Visual candidates to test per sampled frame")
    p.add_argument("--mask-dilate", type=int, default=9)
    p.add_argument("--bottom-border", type=int, default=24)
    p.add_argument("--sift-nfeatures", type=int, default=2500)
    p.add_argument("--ratio-test", type=float, default=0.75)
    p.add_argument("--essential-threshold", type=float, default=1.5)
    p.add_argument("--min-good-matches", type=int, default=30)
    p.add_argument("--min-inliers", type=int, default=25)
    p.add_argument("--min-inlier-ratio", type=float, default=0.35)
    p.add_argument("--bins", type=int, default=320, help="Canonical progress bins")
    return p.parse_args()


def default_width_csv(recording: Path) -> Path:
    return plane_output_dir(recording) / "track_width_estimates.csv"


def output_path(recording: Path) -> Path:
    return canonical_output_dir(recording)


def smooth_xy(xy: np.ndarray, window: int) -> np.ndarray:
    if window <= 1:
        return xy.copy()
    out = xy.copy()
    half = window // 2
    for i in range(len(xy)):
        lo = max(0, i - half)
        hi = min(len(xy), i + half + 1)
        out[i] = xy[lo:hi].mean(axis=0)
    return out


def smooth_1d_periodic(values: np.ndarray, window: int) -> np.ndarray:
    if window <= 1 or len(values) == 0:
        return values.copy()
    radius = window // 2
    out = values.copy()
    n = len(values)
    for i in range(n):
        idx = [(i + k) % n for k in range(-radius, radius + 1)]
        out[i] = np.median(values[idx])
    return out


def smooth_1d_linear(values: np.ndarray, window: int) -> np.ndarray:
    if window <= 1 or len(values) == 0:
        return values.copy()
    radius = window // 2
    out = values.copy()
    for i in range(len(values)):
        lo = max(0, i - radius)
        hi = min(len(values), i + radius + 1)
        out[i] = np.median(values[lo:hi])
    return out


def cumulative_distance(xy: np.ndarray) -> np.ndarray:
    out = np.zeros(len(xy), dtype=np.float64)
    if len(xy) <= 1:
        return out
    deltas = np.linalg.norm(xy[1:] - xy[:-1], axis=1)
    out[1:] = np.cumsum(deltas)
    return out


def heading_unit_vectors(xy: np.ndarray) -> np.ndarray:
    tangents = np.zeros_like(xy)
    tangents[1:-1] = xy[2:] - xy[:-2]
    tangents[0] = xy[1] - xy[0]
    tangents[-1] = xy[-1] - xy[-2]
    norms = np.linalg.norm(tangents, axis=1, keepdims=True)
    norms[norms < 1e-9] = 1.0
    return tangents / norms


def estimate_lap_length_frames(xy: np.ndarray) -> tuple[int, list[dict[str, float]]]:
    n = len(xy)
    min_lag = max(300, n // 12)
    max_lag = min(n // 2, 2400)
    if max_lag <= min_lag:
        return max(1, n), []
    diagnostics: list[dict[str, float]] = []
    for lag in range(min_lag, max_lag + 1, 30):
        d = np.linalg.norm(xy[lag:] - xy[:-lag], axis=1)
        diagnostics.append(
            {
                "lag": float(lag),
                "median_distance_m": float(np.median(d)),
                "p10_distance_m": float(np.percentile(d, 10)),
                "mean_distance_m": float(np.mean(d)),
            }
        )
    best = min(diagnostics, key=lambda row: row["median_distance_m"])
    return int(best["lag"]), diagnostics


def estimate_lap_start_offset(
    xy: np.ndarray,
    lap_length_frames: int,
    sample_every: int,
) -> tuple[int, list[dict[str, float]]]:
    n = len(xy)
    if lap_length_frames <= 0 or n <= lap_length_frames:
        return 0, []
    diagnostics: list[dict[str, float]] = []
    offset_step = max(1, sample_every // 2)
    local_samples = range(0, lap_length_frames, max(sample_every, 1))
    for offset in range(0, lap_length_frames, offset_step):
        full_laps = (n - offset) // lap_length_frames
        if full_laps < 3:
            continue
        errors: list[float] = []
        for local_idx in local_samples:
            pts = []
            for lap_idx in range(full_laps):
                frame_idx = offset + lap_idx * lap_length_frames + local_idx
                if frame_idx >= n:
                    break
                pts.append(xy[frame_idx])
            if len(pts) < 2:
                continue
            pts_arr = np.asarray(pts, dtype=np.float64)
            median_pt = np.median(pts_arr, axis=0)
            errors.extend(np.linalg.norm(pts_arr - median_pt[None, :], axis=1).tolist())
        if not errors:
            continue
        errors_arr = np.asarray(errors, dtype=np.float64)
        prefix = offset
        suffix = n - (offset + full_laps * lap_length_frames)
        diagnostics.append(
            {
                "offset": float(offset),
                "full_lap_count": float(full_laps),
                "median_spread_m": float(np.median(errors_arr)),
                "p90_spread_m": float(np.percentile(errors_arr, 90)),
                "balance_frames": float(abs(prefix - suffix)),
                "prefix_partial_frames": float(prefix),
                "suffix_partial_frames": float(suffix),
                "score": float(np.median(errors_arr) + 0.25 * np.percentile(errors_arr, 90) + 0.01 * abs(prefix - suffix)),
            }
        )
    if not diagnostics:
        return 0, []
    best = min(diagnostics, key=lambda row: row["score"])
    return int(best["offset"]), diagnostics


def build_lap_segments(
    frame_count: int,
    lap_length_frames: int,
    lap_start_offset: int,
) -> tuple[list[LapSegment], list[LapSegment]]:
    laps: list[LapSegment] = []
    partials: list[LapSegment] = []
    if lap_start_offset > 0:
        partials.append(LapSegment(lap_id=-1, start=0, end=int(lap_start_offset), is_partial=True))

    lap_id = 0
    cur = int(lap_start_offset)
    while cur + lap_length_frames <= frame_count:
        laps.append(LapSegment(lap_id=lap_id, start=cur, end=cur + lap_length_frames, is_partial=False))
        cur += lap_length_frames
        lap_id += 1

    if cur < frame_count:
        partials.append(LapSegment(lap_id=lap_id, start=cur, end=frame_count, is_partial=True))

    return laps, partials


def weighted_lis(anchors: list[Anchor]) -> list[Anchor]:
    if not anchors:
        return []
    anchors = sorted(anchors, key=lambda a: (a.frame_index, a.ref_frame_index))
    n = len(anchors)
    dp = [a.score for a in anchors]
    prev = [-1] * n
    for i in range(n):
        for j in range(i):
            if anchors[j].ref_frame_index < anchors[i].ref_frame_index and anchors[j].frame_index < anchors[i].frame_index:
                candidate = dp[j] + anchors[i].score
                if candidate > dp[i]:
                    dp[i] = candidate
                    prev[i] = j
    end = int(np.argmax(np.array(dp)))
    out: list[Anchor] = []
    while end >= 0:
        out.append(anchors[end])
        end = prev[end]
    out.reverse()
    # Deduplicate near-identical anchors.
    filtered: list[Anchor] = []
    for anchor in out:
        if filtered and anchor.frame_index - filtered[-1].frame_index < 5:
            if anchor.score > filtered[-1].score:
                filtered[-1] = anchor
            continue
        filtered.append(anchor)
    return filtered


def circular_fill(values: np.ndarray) -> np.ndarray:
    out = values.copy()
    n = len(out)
    valid = np.isfinite(out)
    if np.all(valid):
        return out
    if not np.any(valid):
        return np.zeros(n, dtype=np.float64)
    valid_idx = np.flatnonzero(valid)
    for i in range(n):
        if valid[i]:
            continue
        left = valid_idx[valid_idx < i]
        right = valid_idx[valid_idx > i]
        li = int(left[-1]) if len(left) else int(valid_idx[-1] - n)
        ri = int(right[0]) if len(right) else int(valid_idx[0] + n)
        lv = out[li % n]
        rv = out[ri % n]
        t = (i - li) / max(ri - li, 1)
        out[i] = (1.0 - t) * lv + t * rv
    return out


def interpolate_progress_for_lap(
    lap: LapSegment,
    anchors: list[Anchor],
    ref_s_by_frame: dict[int, float],
    ref_length_m: float,
) -> np.ndarray:
    lap_len = lap.end - lap.start
    if lap_len <= 0:
        return np.zeros(0, dtype=np.float64)
    lap_local = np.arange(lap_len, dtype=np.float64)
    if len(anchors) >= 2:
        anchor_local = np.array([a.frame_index - lap.start for a in anchors], dtype=np.float64)
        anchor_s = np.array([ref_s_by_frame[a.ref_frame_index] for a in anchors], dtype=np.float64)
        anchor_local_ext = np.r_[anchor_local - lap_len, anchor_local, anchor_local + lap_len]
        anchor_s_ext = np.r_[anchor_s - ref_length_m, anchor_s, anchor_s + ref_length_m]
        return np.mod(np.interp(lap_local, anchor_local_ext, anchor_s_ext), ref_length_m)
    if lap_len == 1:
        return np.zeros(1, dtype=np.float64)
    return np.linspace(0.0, ref_length_m, lap_len, endpoint=False)


def rotation_matrix_to_quaternion_xyzw(r: np.ndarray) -> np.ndarray:
    trace = float(np.trace(r))
    if trace > 0.0:
        s = math.sqrt(trace + 1.0) * 2.0
        w = 0.25 * s
        x = (r[2, 1] - r[1, 2]) / s
        y = (r[0, 2] - r[2, 0]) / s
        z = (r[1, 0] - r[0, 1]) / s
    elif r[0, 0] > r[1, 1] and r[0, 0] > r[2, 2]:
        s = math.sqrt(1.0 + r[0, 0] - r[1, 1] - r[2, 2]) * 2.0
        w = (r[2, 1] - r[1, 2]) / s
        x = 0.25 * s
        y = (r[0, 1] + r[1, 0]) / s
        z = (r[0, 2] + r[2, 0]) / s
    elif r[1, 1] > r[2, 2]:
        s = math.sqrt(1.0 + r[1, 1] - r[0, 0] - r[2, 2]) * 2.0
        w = (r[0, 2] - r[2, 0]) / s
        x = (r[0, 1] + r[1, 0]) / s
        y = 0.25 * s
        z = (r[1, 2] + r[2, 1]) / s
    else:
        s = math.sqrt(1.0 + r[2, 2] - r[0, 0] - r[1, 1]) * 2.0
        w = (r[1, 0] - r[0, 1]) / s
        x = (r[0, 2] + r[2, 0]) / s
        y = (r[1, 2] + r[2, 1]) / s
        z = 0.25 * s
    q = np.array([x, y, z, w], dtype=np.float64)
    norm = np.linalg.norm(q)
    return q if norm == 0 else q / norm


def visual_match_report(
    frame_a: perceiver_pb2.PerceiverDataFrame,
    frame_b: perceiver_pb2.PerceiverDataFrame,
    seg_frames: dict,
    intr: dict[str, float],
    mask_dilate: int,
    bottom_border: int,
    sift_nfeatures: int,
    ratio_test: float,
    essential_threshold: float,
) -> tuple[int, int, float]:
    bgr_a = decode_rgb(frame_a)
    bgr_b = decode_rgb(frame_b)
    gray_a = cv2.cvtColor(bgr_a, cv2.COLOR_BGR2GRAY)
    gray_b = cv2.cvtColor(bgr_b, cv2.COLOR_BGR2GRAY)
    seg_a = seg_frames.get((int(frame_a.frame_identifier.frame_number), int(frame_a.frame_identifier.timestamp_ns)))
    seg_b = seg_frames.get((int(frame_b.frame_identifier.frame_number), int(frame_b.frame_identifier.timestamp_ns)))
    mask_a, _ = bike_mask_for_frame(seg_a, gray_a.shape, "bike", mask_dilate, bottom_border)
    mask_b, _ = bike_mask_for_frame(seg_b, gray_b.shape, "bike", mask_dilate, bottom_border)

    sift = cv2.SIFT_create(nfeatures=sift_nfeatures)
    kp_a, desc_a = sift.detectAndCompute(gray_a, cv2.bitwise_not(mask_a))
    kp_b, desc_b = sift.detectAndCompute(gray_b, cv2.bitwise_not(mask_b))
    if desc_a is None or desc_b is None or len(kp_a) < 8 or len(kp_b) < 8:
        return 0, 0, 0.0

    matcher = cv2.BFMatcher(cv2.NORM_L2, crossCheck=False)
    raw_matches = matcher.knnMatch(desc_a, desc_b, k=2)
    good_matches = []
    for pair in raw_matches:
        if len(pair) < 2:
            continue
        m, n = pair
        if m.distance < ratio_test * n.distance:
            good_matches.append(m)
    if len(good_matches) < 8:
        return len(good_matches), 0, 0.0

    pts_a = np.float32([kp_a[m.queryIdx].pt for m in good_matches])
    pts_b = np.float32([kp_b[m.trainIdx].pt for m in good_matches])
    k = np.array(
        [
            [intr["fx"], 0.0, intr["cx"]],
            [0.0, intr["fy"], intr["cy"]],
            [0.0, 0.0, 1.0],
        ],
        dtype=np.float64,
    )
    e_mat, e_mask = cv2.findEssentialMat(
        pts_a,
        pts_b,
        k,
        method=cv2.RANSAC,
        prob=0.999,
        threshold=essential_threshold,
    )
    if e_mat is None or e_mask is None:
        return len(good_matches), 0, 0.0
    _, _, _, pose_mask = cv2.recoverPose(e_mat, pts_a, pts_b, k)
    inliers = int(np.count_nonzero(pose_mask))
    return len(good_matches), inliers, 0.0 if len(good_matches) == 0 else inliers / len(good_matches)


def main() -> None:
    args = parse_args()
    recording = args.recording.resolve()
    segmentation = seg_path(recording)
    width_csv = default_width_csv(recording)
    out_dir = output_path(recording)
    out_dir.mkdir(parents=True, exist_ok=True)

    with width_csv.open() as f:
        width_rows = list(csv.DictReader(f))
    frame_count = len(width_rows)
    gps_xy = gps_to_local_xy(
        np.array([float(row["latitude"]) for row in width_rows], dtype=np.float64),
        np.array([float(row["longitude"]) for row in width_rows], dtype=np.float64),
    )
    gps_xy_s = smooth_xy(gps_xy, 31)
    headings = heading_unit_vectors(gps_xy_s)
    widths_m = np.array([float(row["width_m"]) for row in width_rows], dtype=np.float64)

    lap_length_frames, lag_diagnostics = estimate_lap_length_frames(gps_xy_s)
    lap_start_offset, offset_diagnostics = estimate_lap_start_offset(gps_xy_s, lap_length_frames, args.sample_every)
    laps, partial_laps = build_lap_segments(frame_count, lap_length_frames, lap_start_offset)
    if len(laps) < 2:
        raise RuntimeError("Not enough laps detected to optimize a canonical track")
    ref_lap = laps[len(laps) // 2]
    ref_indices = np.arange(ref_lap.start, ref_lap.end)
    ref_xy = gps_xy_s[ref_indices]
    ref_s = cumulative_distance(ref_xy)
    ref_length_m = float(ref_s[-1]) if len(ref_s) > 0 else 0.0
    ref_s_by_frame = {int(frame_idx): float(s) for frame_idx, s in zip(ref_indices, ref_s)}

    # Build candidate frame set for visual confirmation.
    intr = None
    frame_iter = iter_messages(recording, perceiver_pb2.PerceiverDataFrame)
    first_frame = next(frame_iter, None)
    if first_frame is None:
        raise RuntimeError("Recording is empty")
    first_bgr = decode_rgb(first_frame)
    intr = camera_from_first_frame(first_frame, first_bgr.shape[1], first_bgr.shape[0], args.bottom_border)
    seg_frames, label_counts = load_segmentation_index(segmentation)

    candidate_indices: set[int] = {0}
    ref_samples = list(range(ref_lap.start, ref_lap.end, max(args.sample_every, 1)))
    ref_search_indices = np.array(ref_samples, dtype=np.int32)
    candidate_indices.update(ref_samples)

    anchors_by_lap: dict[int, list[Anchor]] = {}
    lap_samples_all: dict[int, list[int]] = {}
    for lap in laps:
        if lap.lap_id == ref_lap.lap_id:
            continue
        lap_samples = list(range(lap.start, lap.end, max(args.sample_every, 1)))
        lap_samples_all[lap.lap_id] = lap_samples
        candidate_indices.update(lap_samples)

    selected_frames: dict[int, perceiver_pb2.PerceiverDataFrame] = {0: first_frame} if 0 in candidate_indices else {}
    for frame_index, frame in enumerate(frame_iter, start=1):
        if frame_index in candidate_indices:
            selected_frames[frame_index] = frame

    # Cross-lap correspondence search.
    for lap in laps:
        if lap.lap_id == ref_lap.lap_id:
            continue
        sampled_indices = lap_samples_all[lap.lap_id]
        raw_anchors: list[Anchor] = []
        for frame_index in sampled_indices:
            d = np.linalg.norm(gps_xy_s[ref_search_indices] - gps_xy_s[frame_index], axis=1)
            hcos = headings[ref_search_indices] @ headings[frame_index]
            candidate_mask = (d <= args.gps_radius_m) & (hcos >= args.min_heading_cos)
            candidate_local = np.flatnonzero(candidate_mask)
            if len(candidate_local) == 0:
                continue
            candidate_local = sorted(candidate_local, key=lambda idx: (d[idx], -hcos[idx]))[: args.max_candidates_per_sample]
            best_anchor: Anchor | None = None
            for local_idx in candidate_local:
                ref_frame_index = int(ref_search_indices[local_idx])
                good_matches, inliers, inlier_ratio = visual_match_report(
                    frame_a=selected_frames[frame_index],
                    frame_b=selected_frames[ref_frame_index],
                    seg_frames=seg_frames,
                    intr=intr,
                    mask_dilate=args.mask_dilate,
                    bottom_border=args.bottom_border,
                    sift_nfeatures=args.sift_nfeatures,
                    ratio_test=args.ratio_test,
                    essential_threshold=args.essential_threshold,
                )
                if good_matches < args.min_good_matches or inliers < args.min_inliers or inlier_ratio < args.min_inlier_ratio:
                    continue
                score = float(inliers) + 0.25 * good_matches - 4.0 * float(d[local_idx]) + 5.0 * float(hcos[local_idx])
                candidate = Anchor(
                    frame_index=int(frame_index),
                    ref_frame_index=ref_frame_index,
                    gps_distance_m=float(d[local_idx]),
                    heading_cos=float(hcos[local_idx]),
                    good_matches=int(good_matches),
                    inliers=int(inliers),
                    inlier_ratio=float(inlier_ratio),
                    score=score,
                    method="gps+visual",
                )
                if best_anchor is None or candidate.score > best_anchor.score:
                    best_anchor = candidate
            if best_anchor is not None:
                raw_anchors.append(best_anchor)
            else:
                local_idx = int(candidate_local[0])
                fallback = Anchor(
                    frame_index=int(frame_index),
                    ref_frame_index=int(ref_search_indices[local_idx]),
                    gps_distance_m=float(d[local_idx]),
                    heading_cos=float(hcos[local_idx]),
                    good_matches=0,
                    inliers=0,
                    inlier_ratio=0.0,
                    score=max(0.0, 3.0 - float(d[local_idx])),
                    method="gps_only",
                )
                raw_anchors.append(fallback)
        anchors_by_lap[lap.lap_id] = weighted_lis(raw_anchors)

    # Map every frame onto canonical progress along the reference lap.
    progress_s = np.full(frame_count, np.nan, dtype=np.float64)
    lap_id_by_frame = np.full(frame_count, -1, dtype=np.int32)
    is_partial_frame = np.ones(frame_count, dtype=bool)
    for lap in laps:
        lap_frames = np.arange(lap.start, lap.end, dtype=np.int32)
        lap_id_by_frame[lap.start : lap.end] = lap.lap_id
        is_partial_frame[lap.start : lap.end] = False
        if lap.lap_id == ref_lap.lap_id:
            progress_s[lap.start : lap.end] = ref_s[: len(lap_frames)]
            continue
        anchors = anchors_by_lap.get(lap.lap_id, [])
        progress_s[lap.start : lap.end] = interpolate_progress_for_lap(
            lap=lap,
            anchors=anchors,
            ref_s_by_frame=ref_s_by_frame,
            ref_length_m=ref_length_m,
        )

    for partial in partial_laps:
        lap_len = partial.end - partial.start
        if lap_len <= 0:
            continue
        lap_id_by_frame[partial.start : partial.end] = partial.lap_id
        if partial.lap_id < 0:
            local = np.arange(lap_length_frames - lap_len, lap_length_frames, dtype=np.float64)
        else:
            local = np.arange(lap_len, dtype=np.float64)
        progress_s[partial.start : partial.end] = np.mod(local / max(lap_length_frames, 1) * ref_length_m, ref_length_m)

    if np.any(~np.isfinite(progress_s)):
        finite = np.isfinite(progress_s)
        progress_s[~finite] = np.interp(
            np.flatnonzero(~finite),
            np.flatnonzero(finite),
            progress_s[finite],
        )

    # Canonical centerline and width profile.
    bin_edges = np.linspace(0.0, ref_length_m, args.bins + 1)
    bin_centers_s = 0.5 * (bin_edges[:-1] + bin_edges[1:])
    center_x = np.full(args.bins, np.nan, dtype=np.float64)
    center_y = np.full(args.bins, np.nan, dtype=np.float64)
    width_profile = np.full(args.bins, np.nan, dtype=np.float64)
    canonical_source_mask = ~is_partial_frame
    for b in range(args.bins):
        if b == args.bins - 1:
            mask = (progress_s >= bin_edges[b]) & (progress_s <= bin_edges[b + 1])
        else:
            mask = (progress_s >= bin_edges[b]) & (progress_s < bin_edges[b + 1])
        mask &= canonical_source_mask
        if not np.any(mask):
            continue
        center_x[b] = float(np.median(gps_xy_s[mask, 0]))
        center_y[b] = float(np.median(gps_xy_s[mask, 1]))
        width_profile[b] = float(np.median(widths_m[mask]))
    center_x = smooth_1d_periodic(circular_fill(center_x), 9)
    center_y = smooth_1d_periodic(circular_fill(center_y), 9)
    width_profile = smooth_1d_periodic(circular_fill(width_profile), 9)
    width_profile = np.maximum(width_profile, 0.5)
    centerline = np.column_stack([center_x, center_y])

    centerline_closed = np.vstack([centerline, centerline[0]])
    centerline_seg = cumulative_distance(centerline_closed)
    total_centerline_length_m = float(centerline_seg[-1]) if len(centerline_seg) > 0 else ref_length_m
    if total_centerline_length_m <= 1e-6:
        total_centerline_length_m = ref_length_m

    tangents = np.zeros_like(centerline)
    tangents[1:-1] = centerline[2:] - centerline[:-2]
    tangents[0] = centerline[1] - centerline[-1]
    tangents[-1] = centerline[0] - centerline[-2]
    tangent_norm = np.linalg.norm(tangents, axis=1, keepdims=True)
    tangent_norm[tangent_norm < 1e-6] = 1.0
    tangents = tangents / tangent_norm
    normals = np.column_stack([-tangents[:, 1], tangents[:, 0]])
    half_width = 0.5 * width_profile
    left_boundary = centerline + normals * half_width[:, None]
    right_boundary = centerline - normals * half_width[:, None]

    # Per-frame projection onto canonical track.
    centerline_s = np.linspace(0.0, ref_length_m, args.bins, endpoint=False)
    interp_center_x = np.interp(progress_s, centerline_s, center_x, period=ref_length_m)
    interp_center_y = np.interp(progress_s, centerline_s, center_y, period=ref_length_m)
    interp_normal_x = np.interp(progress_s, centerline_s, normals[:, 0], period=ref_length_m)
    interp_normal_y = np.interp(progress_s, centerline_s, normals[:, 1], period=ref_length_m)
    interp_half_width = np.interp(progress_s, centerline_s, half_width, period=ref_length_m)
    center_for_frame = np.column_stack([interp_center_x, interp_center_y])
    normals_for_frame = np.column_stack([interp_normal_x, interp_normal_y])
    norm_len = np.linalg.norm(normals_for_frame, axis=1, keepdims=True)
    norm_len[norm_len < 1e-6] = 1.0
    normals_for_frame = normals_for_frame / norm_len
    lateral_offset_m = np.sum((gps_xy_s - center_for_frame) * normals_for_frame, axis=1)
    image_lateral_m = np.full(frame_count, np.nan, dtype=np.float64)
    if "bike_fraction" in width_rows[0]:
        bike_fraction = np.array([float(row["bike_fraction"]) for row in width_rows], dtype=np.float64)
        image_lateral_m = (0.5 - bike_fraction) * widths_m
    trajectory_lateral_m = np.where(np.isfinite(image_lateral_m), image_lateral_m, lateral_offset_m)
    trajectory_lateral_m = np.clip(trajectory_lateral_m, -0.95 * interp_half_width, 0.95 * interp_half_width)
    for lap in laps + partial_laps:
        lap_mask = lap_id_by_frame == lap.lap_id
        if not np.any(lap_mask):
            continue
        trajectory_lateral_m[lap_mask] = smooth_1d_linear(trajectory_lateral_m[lap_mask], 15)
        trajectory_lateral_m[lap_mask] = np.clip(
            trajectory_lateral_m[lap_mask],
            -0.95 * interp_half_width[lap_mask],
            0.95 * interp_half_width[lap_mask],
        )
    trajectory_xy = center_for_frame + normals_for_frame * trajectory_lateral_m[:, None]

    # Outputs.
    lap_trajectories: dict[int, np.ndarray] = {}
    canonical_centerline_csv = out_dir / "canonical_centerline.csv"
    with canonical_centerline_csv.open("w", newline="") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=["bin_index", "progress_m", "center_x", "center_y", "width_m", "left_x", "left_y", "right_x", "right_y"],
        )
        writer.writeheader()
        for i in range(args.bins):
            writer.writerow(
                {
                    "bin_index": i,
                    "progress_m": float(centerline_s[i]),
                    "center_x": float(centerline[i, 0]),
                    "center_y": float(centerline[i, 1]),
                    "width_m": float(width_profile[i]),
                    "left_x": float(left_boundary[i, 0]),
                    "left_y": float(left_boundary[i, 1]),
                    "right_x": float(right_boundary[i, 0]),
                    "right_y": float(right_boundary[i, 1]),
                }
            )

    trajectory_csv = out_dir / "lap_trajectories.csv"
    with trajectory_csv.open("w", newline="") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=[
                "frame_index",
                "frame_number",
                "timestamp_ns",
                "lap_id",
                "is_partial_lap",
                "progress_m",
                "progress_fraction",
                "gps_x",
                "gps_y",
                "canonical_x",
                "canonical_y",
                "lateral_offset_m",
                "image_lateral_m",
                "trajectory_lateral_m",
                "trajectory_x",
                "trajectory_y",
                "width_m",
                "half_width_m",
            ],
        )
        writer.writeheader()
        for i, row in enumerate(width_rows):
            lap_id = int(lap_id_by_frame[i])
            writer.writerow(
                {
                    "frame_index": i,
                    "frame_number": int(row["frame_number"]),
                    "timestamp_ns": int(row["timestamp_ns"]),
                    "lap_id": lap_id,
                    "is_partial_lap": bool(is_partial_frame[i]),
                    "progress_m": float(progress_s[i]),
                    "progress_fraction": 0.0 if ref_length_m <= 1e-6 else float(progress_s[i] / ref_length_m),
                    "gps_x": float(gps_xy_s[i, 0]),
                    "gps_y": float(gps_xy_s[i, 1]),
                    "canonical_x": float(center_for_frame[i, 0]),
                    "canonical_y": float(center_for_frame[i, 1]),
                    "lateral_offset_m": float(lateral_offset_m[i]),
                    "image_lateral_m": "" if not np.isfinite(image_lateral_m[i]) else float(image_lateral_m[i]),
                    "trajectory_lateral_m": float(trajectory_lateral_m[i]),
                    "trajectory_x": float(trajectory_xy[i, 0]),
                    "trajectory_y": float(trajectory_xy[i, 1]),
                    "width_m": float(widths_m[i]),
                    "half_width_m": float(interp_half_width[i]),
                }
            )
        for lap in laps:
            lap_mask = lap_id_by_frame == lap.lap_id
            lap_trajectories[lap.lap_id] = trajectory_xy[lap_mask]

    anchor_csv = out_dir / "loop_correspondences.csv"
    with anchor_csv.open("w", newline="") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=[
                "lap_id",
                "frame_index",
                "ref_frame_index",
                "gps_distance_m",
                "heading_cos",
                "good_matches",
                "inliers",
                "inlier_ratio",
                "score",
                "method",
            ],
        )
        writer.writeheader()
        for lap_id, anchors in anchors_by_lap.items():
            for anchor in anchors:
                writer.writerow(
                    {
                        "lap_id": lap_id,
                        "frame_index": anchor.frame_index,
                        "ref_frame_index": anchor.ref_frame_index,
                        "gps_distance_m": anchor.gps_distance_m,
                        "heading_cos": anchor.heading_cos,
                        "good_matches": anchor.good_matches,
                        "inliers": anchor.inliers,
                        "inlier_ratio": anchor.inlier_ratio,
                        "score": anchor.score,
                        "method": anchor.method,
                    }
                )

    laps_json = out_dir / "lap_segments.json"
    laps_json.write_text(
        json.dumps(
            {
                "estimated_lap_length_frames": lap_length_frames,
                "estimated_lap_length_s": lap_length_frames / 30.0,
                "lap_start_offset_frames": lap_start_offset,
                "lap_start_offset_s": lap_start_offset / 30.0,
                "reference_lap_id": ref_lap.lap_id,
                "reference_length_m": ref_length_m,
                "laps": [{"lap_id": lap.lap_id, "start": lap.start, "end": lap.end, "length_frames": lap.end - lap.start} for lap in laps],
                "partial_laps": [
                    {"lap_id": lap.lap_id, "start": lap.start, "end": lap.end, "length_frames": lap.end - lap.start}
                    for lap in partial_laps
                ],
                "lag_diagnostics_best": sorted(lag_diagnostics, key=lambda row: row["median_distance_m"])[:20],
                "offset_diagnostics_best": sorted(offset_diagnostics, key=lambda row: row["score"])[:20],
            },
            indent=2,
        )
    )

    summary = {
        "recording": str(recording),
        "segmentation": str(segmentation),
        "width_csv": str(width_csv),
        "output_dir": str(out_dir),
        "frame_count": frame_count,
        "estimated_lap_length_frames": lap_length_frames,
        "estimated_lap_length_s": lap_length_frames / 30.0,
        "lap_start_offset_frames": lap_start_offset,
        "lap_start_offset_s": lap_start_offset / 30.0,
        "lap_count": len(laps),
        "partial_lap_count": len(partial_laps),
        "reference_lap_id": ref_lap.lap_id,
        "reference_length_m": ref_length_m,
        "sample_every": args.sample_every,
        "gps_radius_m": args.gps_radius_m,
        "min_heading_cos": args.min_heading_cos,
        "total_anchor_count": int(sum(len(v) for v in anchors_by_lap.values())),
        "visual_anchor_count": int(sum(sum(1 for a in v if a.method == 'gps+visual') for v in anchors_by_lap.values())),
        "gps_only_anchor_count": int(sum(sum(1 for a in v if a.method == 'gps_only') for v in anchors_by_lap.values())),
        "canonical_bins": args.bins,
        "median_width_m": float(np.median(width_profile)),
        "p10_width_m": float(np.percentile(width_profile, 10)),
        "p90_width_m": float(np.percentile(width_profile, 90)),
        "median_abs_lateral_offset_m": float(np.median(np.abs(lateral_offset_m[canonical_source_mask]))),
        "p90_abs_lateral_offset_m": float(np.percentile(np.abs(lateral_offset_m[canonical_source_mask]), 90)),
        "median_abs_trajectory_lateral_m": float(np.median(np.abs(trajectory_lateral_m[canonical_source_mask]))),
        "p90_abs_trajectory_lateral_m": float(np.percentile(np.abs(trajectory_lateral_m[canonical_source_mask]), 90)),
        "median_abs_lateral_offset_all_frames_m": float(np.median(np.abs(lateral_offset_m))),
        "p90_abs_lateral_offset_all_frames_m": float(np.percentile(np.abs(lateral_offset_m), 90)),
        "label_counts": label_counts,
    }
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=2))
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
