#!/usr/bin/env python3
"""
Estimate relative pose between two frames using masked SIFT correspondences.

This is intentionally independent of any tracker state so we can inspect
whether a single frame pair still supports a non-zero visual pose estimate.
"""

from __future__ import annotations

import argparse
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
    pair_pose_output_dir,
    seg_path,
)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Estimate pairwise pose with masked SIFT features")
    p.add_argument("recording", type=Path, help="Path to .vis.pb recording")
    p.add_argument("frame_a", type=int, help="Global frame index for the first image")
    p.add_argument("frame_b", type=int, help="Global frame index for the second image")
    p.add_argument(
        "--mask-labels",
        default="bike",
        help="Comma-separated segmentation labels to suppress before feature extraction",
    )
    p.add_argument("--mask-dilate", type=int, default=9)
    p.add_argument("--bottom-border", type=int, default=24)
    p.add_argument("--ratio-test", type=float, default=0.75)
    p.add_argument("--essential-threshold", type=float, default=1.5)
    return p.parse_args()


def output_path(recording: Path, frame_a: int, frame_b: int) -> Path:
    return pair_pose_output_dir(recording, frame_a, frame_b)


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


def parse_labels(raw: str) -> list[str]:
    return [item.strip() for item in raw.split(",") if item.strip()]


def load_target_frames(recording: Path, targets: set[int]) -> dict[int, perceiver_pb2.PerceiverDataFrame]:
    found: dict[int, perceiver_pb2.PerceiverDataFrame] = {}
    for idx, frame in enumerate(iter_messages(recording, perceiver_pb2.PerceiverDataFrame)):
        if idx in targets:
            found[idx] = frame
            if len(found) == len(targets):
                break
    missing = sorted(targets - set(found))
    if missing:
        raise RuntimeError(f"Missing frame indices: {missing}")
    return found


def combined_mask(
    seg_frames: dict,
    frame: perceiver_pb2.PerceiverDataFrame,
    image_shape: tuple[int, int],
    labels: list[str],
    dilate_radius: int,
    bottom_border: int,
) -> tuple[np.ndarray, int]:
    frame_number = int(frame.frame_identifier.frame_number)
    timestamp_ns = int(frame.frame_identifier.timestamp_ns)
    seg_frame = seg_frames.get((frame_number, timestamp_ns))
    mask = np.zeros(image_shape, dtype=np.uint8)
    for label in labels:
        label_mask, _ = bike_mask_for_frame(
            seg_frame,
            image_shape,
            label=label,
            dilate_radius=dilate_radius,
            bottom_border=0,
        )
        mask = cv2.bitwise_or(mask, label_mask)
    if bottom_border > 0:
        h = image_shape[0]
        mask[h - min(bottom_border, h) :, :] = 255
    return mask, int(np.count_nonzero(mask))


def main() -> None:
    args = parse_args()
    if args.frame_a >= args.frame_b:
        raise ValueError("frame_a must be smaller than frame_b")

    recording = args.recording.resolve()
    segmentation = seg_path(recording)
    out_dir = output_path(recording, args.frame_a, args.frame_b)
    out_dir.mkdir(parents=True, exist_ok=True)

    labels = parse_labels(args.mask_labels)
    seg_frames, label_counts = load_segmentation_index(segmentation)
    frames = load_target_frames(recording, {args.frame_a, args.frame_b})
    frame_a = frames[args.frame_a]
    frame_b = frames[args.frame_b]

    bgr_a = decode_rgb(frame_a)
    bgr_b = decode_rgb(frame_b)
    gray_a = cv2.cvtColor(bgr_a, cv2.COLOR_BGR2GRAY)
    gray_b = cv2.cvtColor(bgr_b, cv2.COLOR_BGR2GRAY)

    mask_a, mask_pixels_a = combined_mask(seg_frames, frame_a, gray_a.shape, labels, args.mask_dilate, args.bottom_border)
    mask_b, mask_pixels_b = combined_mask(seg_frames, frame_b, gray_b.shape, labels, args.mask_dilate, args.bottom_border)

    intr_meta = camera_from_first_frame(frame_a, bgr_a.shape[1], bgr_a.shape[0], args.bottom_border)
    K = np.array(
        [
            [intr_meta["fx"], 0.0, intr_meta["cx"]],
            [0.0, intr_meta["fy"], intr_meta["cy"]],
            [0.0, 0.0, 1.0],
        ],
        dtype=np.float64,
    )

    sift = cv2.SIFT_create()
    kp_a, desc_a = sift.detectAndCompute(gray_a, cv2.bitwise_not(mask_a))
    kp_b, desc_b = sift.detectAndCompute(gray_b, cv2.bitwise_not(mask_b))
    if desc_a is None or desc_b is None or len(kp_a) < 8 or len(kp_b) < 8:
        raise RuntimeError("Too few SIFT features after masking")

    matcher = cv2.BFMatcher(cv2.NORM_L2, crossCheck=False)
    raw_matches = matcher.knnMatch(desc_a, desc_b, k=2)
    good_matches: list[cv2.DMatch] = []
    for pair in raw_matches:
        if len(pair) < 2:
            continue
        m, n = pair
        if m.distance < args.ratio_test * n.distance:
            good_matches.append(m)
    if len(good_matches) < 8:
        raise RuntimeError(f"Too few good SIFT matches: {len(good_matches)}")

    pts_a = np.float32([kp_a[m.queryIdx].pt for m in good_matches])
    pts_b = np.float32([kp_b[m.trainIdx].pt for m in good_matches])

    E, e_mask = cv2.findEssentialMat(
        pts_a,
        pts_b,
        K,
        method=cv2.RANSAC,
        prob=0.999,
        threshold=args.essential_threshold,
    )
    if E is None or e_mask is None:
        raise RuntimeError("Essential matrix estimation failed")

    _, R, t, pose_mask = cv2.recoverPose(E, pts_a, pts_b, K)
    pose_mask = pose_mask.reshape(-1).astype(bool)
    inlier_matches = [m for m, keep in zip(good_matches, pose_mask) if keep]
    q = rotation_matrix_to_quaternion_xyzw(R)
    t_norm = float(np.linalg.norm(t))
    rot_deg = float(math.degrees(np.linalg.norm(cv2.Rodrigues(R)[0])))

    report = {
        "recording": str(recording),
        "segmentation": str(segmentation),
        "frame_a": {
            "frame_index": args.frame_a,
            "frame_number": int(frame_a.frame_identifier.frame_number),
            "timestamp_ns": int(frame_a.frame_identifier.timestamp_ns),
            "mask_pixels": mask_pixels_a,
        },
        "frame_b": {
            "frame_index": args.frame_b,
            "frame_number": int(frame_b.frame_identifier.frame_number),
            "timestamp_ns": int(frame_b.frame_identifier.timestamp_ns),
            "mask_pixels": mask_pixels_b,
        },
        "mask_labels": labels,
        "mask_dilate": args.mask_dilate,
        "bottom_border": args.bottom_border,
        "label_counts": label_counts,
        "keypoints_a": len(kp_a),
        "keypoints_b": len(kp_b),
        "raw_knn_match_count": len(raw_matches),
        "good_match_count": len(good_matches),
        "essential_inlier_count": len(inlier_matches),
        "essential_inlier_ratio": len(inlier_matches) / max(len(good_matches), 1),
        "translation_direction_norm": t_norm,
        "rotation_deg": rot_deg,
        "dx": float(t[0, 0]),
        "dy": float(t[1, 0]),
        "dz": float(t[2, 0]),
        "qx": float(q[0]),
        "qy": float(q[1]),
        "qz": float(q[2]),
        "qw": float(q[3]),
        "output_dir": str(out_dir),
    }
    (out_dir / "report.json").write_text(json.dumps(report, indent=2))
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
