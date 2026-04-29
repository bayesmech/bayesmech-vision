#!/usr/bin/env python3
"""
Direct pairwise visual motion estimation from a BayesMech recording.

This estimates relative motion between consecutive frames from tracked image
points plus an essential matrix.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
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
    bike_mask_for_frame,
    camera_from_first_frame,
    decode_rgb,
    iter_messages,
    load_segmentation_index,
    seg_path,
    visual_pairs_output_dir,
)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Direct pairwise visual motion estimation")
    p.add_argument("recording", type=Path, help="Path to .vis.pb recording")
    p.add_argument("--start-frame", type=int, default=0)
    p.add_argument("--max-frames", type=int, default=0)
    p.add_argument("--mask-label", default="bike")
    p.add_argument("--mask-dilate", type=int, default=9)
    p.add_argument("--bottom-border", type=int, default=24)
    p.add_argument("--max-corners", type=int, default=2000)
    p.add_argument("--quality-level", type=float, default=0.01)
    p.add_argument("--min-distance", type=float, default=7.0)
    p.add_argument("--essential-threshold", type=float, default=1.5)
    return p.parse_args()


def output_path(recording: Path) -> Path:
    return visual_pairs_output_dir(recording)


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


def classify_motion(smoothed_translation: float, smoothed_rotation_deg: float) -> str:
    if smoothed_translation < 0.005 and smoothed_rotation_deg < 0.2:
        return "stalled"
    if smoothed_rotation_deg > 2.0 and smoothed_translation < 0.08:
        return "turning"
    if smoothed_translation >= 0.01 and smoothed_rotation_deg < 2.0:
        return "straight"
    return "mixed"


def main() -> None:
    args = parse_args()
    recording = args.recording.resolve()
    segmentation = seg_path(recording)
    out_dir = output_path(recording)
    out_dir.mkdir(parents=True, exist_ok=True)

    seg_frames, _ = load_segmentation_index(segmentation)
    frame_iter = iter_messages(recording, perceiver_pb2.PerceiverDataFrame)
    first_frame = next(frame_iter, None)
    if first_frame is None:
        raise RuntimeError("Empty recording")

    first_bgr = decode_rgb(first_frame)
    image_h, image_w = first_bgr.shape[:2]
    intr_meta = camera_from_first_frame(first_frame, image_w, image_h, args.bottom_border)
    K = np.array(
        [
            [intr_meta["fx"], 0.0, intr_meta["cx"]],
            [0.0, intr_meta["fy"], intr_meta["cy"]],
            [0.0, 0.0, 1.0],
        ],
        dtype=np.float64,
    )

    summary_rows: list[dict[str, object]] = []
    prev_frame = first_frame
    prev_gray = cv2.cvtColor(first_bgr, cv2.COLOR_BGR2GRAY)
    prev_seg = seg_frames.get((int(prev_frame.frame_identifier.frame_number), int(prev_frame.frame_identifier.timestamp_ns)))
    prev_mask, _ = bike_mask_for_frame(
        prev_seg,
        prev_gray.shape,
        label=args.mask_label,
        dilate_radius=args.mask_dilate,
        bottom_border=args.bottom_border,
    )

    frame_index = 1
    processed_pairs = 0
    feature_params = dict(
        maxCorners=args.max_corners,
        qualityLevel=args.quality_level,
        minDistance=args.min_distance,
        blockSize=7,
    )
    lk_params = dict(
        winSize=(21, 21),
        maxLevel=3,
        criteria=(cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, 30, 0.01),
    )

    for frame in frame_iter:
        if frame_index < args.start_frame:
            prev_frame = frame
            prev_gray = cv2.cvtColor(decode_rgb(frame), cv2.COLOR_BGR2GRAY)
            prev_seg = seg_frames.get((int(prev_frame.frame_identifier.frame_number), int(prev_frame.frame_identifier.timestamp_ns)))
            prev_mask, _ = bike_mask_for_frame(
                prev_seg,
                prev_gray.shape,
                label=args.mask_label,
                dilate_radius=args.mask_dilate,
                bottom_border=args.bottom_border,
            )
            frame_index += 1
            continue
        if args.max_frames > 0 and processed_pairs >= args.max_frames:
            break

        gray = cv2.cvtColor(decode_rgb(frame), cv2.COLOR_BGR2GRAY)
        seg_frame = seg_frames.get((int(frame.frame_identifier.frame_number), int(frame.frame_identifier.timestamp_ns)))
        mask, mask_pixels = bike_mask_for_frame(
            seg_frame,
            gray.shape,
            label=args.mask_label,
            dilate_radius=args.mask_dilate,
            bottom_border=args.bottom_border,
        )

        detect_mask = cv2.bitwise_not(prev_mask)
        pts0 = cv2.goodFeaturesToTrack(prev_gray, mask=detect_mask, **feature_params)
        status_name = "no_features"
        row = {
            "prev_frame_index": frame_index - 1,
            "frame_index": frame_index,
            "prev_timestamp_ns": int(prev_frame.frame_identifier.timestamp_ns),
            "timestamp_ns": int(frame.frame_identifier.timestamp_ns),
            "feature_count": 0,
            "tracked_count": 0,
            "fb_ok_count": 0,
            "essential_inlier_count": 0,
            "status": status_name,
            "translation_magnitude": 0.0,
            "rotation_deg": 0.0,
            "dx": 0.0,
            "dy": 0.0,
            "dz": 0.0,
            "qx": 0.0,
            "qy": 0.0,
            "qz": 0.0,
            "qw": 1.0,
            "mask_pixels_prev": int(np.count_nonzero(prev_mask)),
            "mask_pixels": mask_pixels,
            "median_flow_px": 0.0,
        }
        if pts0 is not None and len(pts0) >= 8:
            row["feature_count"] = int(len(pts0))
            pts1, st, _ = cv2.calcOpticalFlowPyrLK(prev_gray, gray, pts0, None, **lk_params)
            pts0_back, st_back, _ = cv2.calcOpticalFlowPyrLK(gray, prev_gray, pts1, None, **lk_params)
            if pts1 is not None and st is not None and pts0_back is not None and st_back is not None:
                fb_err = np.linalg.norm(pts0.reshape(-1, 2) - pts0_back.reshape(-1, 2), axis=1)
                ok = (st.reshape(-1) == 1) & (st_back.reshape(-1) == 1) & (fb_err < 1.5)
                p0 = pts0.reshape(-1, 2)[ok]
                p1 = pts1.reshape(-1, 2)[ok]
                inside = (
                    (p1[:, 0] >= 0)
                    & (p1[:, 0] < image_w)
                    & (p1[:, 1] >= 0)
                    & (p1[:, 1] < image_h)
                )
                p0 = p0[inside]
                p1 = p1[inside]
                if len(p1):
                    keep = mask[p1[:, 1].astype(int), p1[:, 0].astype(int)] == 0
                    p0 = p0[keep]
                    p1 = p1[keep]
                row["tracked_count"] = int(np.count_nonzero(st))
                row["fb_ok_count"] = int(len(p0))
                if len(p0) >= 8:
                    flow = np.linalg.norm(p1 - p0, axis=1)
                    row["median_flow_px"] = float(np.median(flow))
                    E, inlier_mask = cv2.findEssentialMat(
                        p0,
                        p1,
                        K,
                        method=cv2.RANSAC,
                        prob=0.999,
                        threshold=args.essential_threshold,
                    )
                    if E is not None:
                        _, R, t, pose_mask = cv2.recoverPose(E, p0, p1, K)
                        pose_inliers = int(np.count_nonzero(pose_mask))
                        row["essential_inlier_count"] = pose_inliers
                        q = rotation_matrix_to_quaternion_xyzw(R)
                        row["dx"] = float(t[0, 0])
                        row["dy"] = float(t[1, 0])
                        row["dz"] = float(t[2, 0])
                        row["qx"] = float(q[0])
                        row["qy"] = float(q[1])
                        row["qz"] = float(q[2])
                        row["qw"] = float(q[3])
                        row["translation_magnitude"] = float(np.linalg.norm(t))
                        row["rotation_deg"] = float(math.degrees(np.linalg.norm(cv2.Rodrigues(R)[0])))
                        row["status"] = "ok" if pose_inliers >= 20 else "low_inliers"
                    else:
                        row["status"] = "essential_failed"
                else:
                    row["status"] = "too_few_tracked"
            else:
                row["status"] = "lk_failed"
        summary_rows.append(row)

        prev_frame = frame
        prev_gray = gray
        prev_mask = mask
        frame_index += 1
        processed_pairs += 1

    window = 30
    for i, row in enumerate(summary_rows):
        chunk = summary_rows[max(0, i - window + 1) : i + 1]
        mean_t = sum(float(r["translation_magnitude"]) for r in chunk) / len(chunk)
        mean_r = sum(float(r["rotation_deg"]) for r in chunk) / len(chunk)
        row["smoothed_translation_magnitude"] = mean_t
        row["smoothed_rotation_deg"] = mean_r
        row["motion_state"] = classify_motion(mean_t, mean_r)

    summary_csv = out_dir / "pairwise_visual_motion.csv"
    with summary_csv.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(summary_rows[0].keys()))
        writer.writeheader()
        writer.writerows(summary_rows)

    segments: list[dict[str, object]] = []
    state = None
    start_idx = None
    for row in summary_rows:
        motion_state = str(row["motion_state"])
        if motion_state != state:
            if state is not None:
                segments.append(
                    {
                        "start_frame_index": start_idx,
                        "end_frame_index": prev_idx,
                        "motion_state": state,
                    }
                )
            state = motion_state
            start_idx = int(row["frame_index"])
        prev_idx = int(row["frame_index"])
    if state is not None:
        segments.append(
            {
                "start_frame_index": start_idx,
                "end_frame_index": prev_idx,
                "motion_state": state,
            }
        )

    report = {
        "recording": str(recording),
        "segmentation": str(segmentation),
        "output_dir": str(out_dir),
        "pair_count": len(summary_rows),
        "ok_pair_count": sum(1 for r in summary_rows if r["status"] == "ok"),
        "nonzero_motion_pairs": sum(
            1
            for r in summary_rows
            if float(r["translation_magnitude"]) > 1e-4 or float(r["rotation_deg"]) > 0.05
        ),
        "segments": segments,
    }
    (out_dir / "summary.json").write_text(json.dumps(report, indent=2))
    (out_dir / "motion_segments.json").write_text(json.dumps({"segments": segments}, indent=2))
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
