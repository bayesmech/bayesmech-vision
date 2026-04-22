#!/usr/bin/env python3
"""
Build a monocular trajectory from consecutive masked SIFT frame pairs.

This is a thin custom visual-odometry pipeline:
1. load consecutive RGB frames from the recording
2. mask semantic regions before feature extraction
3. extract SIFT features per frame
4. match adjacent frames with Lowe ratio filtering
5. estimate an essential matrix with RANSAC
6. recover relative pose and chain the camera trajectory

The trajectory is monocular and therefore only up to scale.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import sys
import time
from pathlib import Path

import cv2
import numpy as np

_server_root = Path(__file__).resolve().parent.parent
_project_root = _server_root.parent
for _p in (str(_project_root), str(_project_root / "proto"), str(_server_root)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from proto import perceiver_pb2
from visual_odometry.common import (
    bike_mask_for_frame,
    camera_from_first_frame,
    decode_rgb,
    fit_similarity,
    gps_to_local_xy,
    iter_messages,
    load_segmentation_index,
    project_track_to_2d,
    seg_path,
    write_track_plot,
)
from visual_odometry.sift_pair_pose import rotation_matrix_to_quaternion_xyzw


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Build a custom pairwise SIFT trajectory")
    p.add_argument("recording", type=Path, help="Path to .vis.pb recording")
    p.add_argument("--segmentation", type=Path, default=None, help="Optional .seg.pb path")
    p.add_argument("--output-dir", type=Path, default=None, help="Optional output directory")
    p.add_argument("--start-frame", type=int, default=0)
    p.add_argument("--max-frames", type=int, default=0)
    p.add_argument(
        "--mask-labels",
        default="bike",
        help="Comma-separated segmentation labels to suppress before feature extraction",
    )
    p.add_argument("--mask-dilate", type=int, default=9)
    p.add_argument("--bottom-border", type=int, default=24)
    p.add_argument("--sift-nfeatures", type=int, default=3000)
    p.add_argument("--ratio-test", type=float, default=0.75)
    p.add_argument("--essential-threshold", type=float, default=1.5)
    p.add_argument("--min-good-matches", type=int, default=20)
    p.add_argument("--min-inliers", type=int, default=20)
    return p.parse_args()


def parse_labels(raw: str) -> list[str]:
    return [item.strip() for item in raw.split(",") if item.strip()]


def output_path(recording: Path) -> Path:
    stem = recording.name.removesuffix(".vis.pb") if recording.name.endswith(".vis.pb") else recording.stem
    return recording.parent / f"{stem}.sift_pairwise"


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


def draw_match_debug(
    prev_bgr: np.ndarray,
    prev_mask: np.ndarray,
    prev_kp: list[cv2.KeyPoint],
    cur_bgr: np.ndarray,
    cur_mask: np.ndarray,
    cur_kp: list[cv2.KeyPoint],
    matches: list[cv2.DMatch],
) -> np.ndarray:
    left = prev_bgr.copy()
    right = cur_bgr.copy()
    if np.any(prev_mask):
        overlay = left.copy()
        overlay[prev_mask > 0] = (40, 40, 240)
        left = cv2.addWeighted(left, 0.75, overlay, 0.25, 0.0)
    if np.any(cur_mask):
        overlay = right.copy()
        overlay[cur_mask > 0] = (40, 40, 240)
        right = cv2.addWeighted(right, 0.75, overlay, 0.25, 0.0)
    return cv2.drawMatches(
        left,
        prev_kp,
        right,
        cur_kp,
        matches,
        None,
        matchColor=(50, 220, 50),
        singlePointColor=(255, 255, 255),
        flags=cv2.DrawMatchesFlags_NOT_DRAW_SINGLE_POINTS,
    )


def world_quaternion_from_rotation(world_r_cam: np.ndarray) -> np.ndarray:
    return rotation_matrix_to_quaternion_xyzw(world_r_cam)


def main() -> None:
    args = parse_args()
    recording = args.recording.resolve()
    segmentation = args.segmentation.resolve() if args.segmentation else seg_path(recording)
    out_dir = args.output_dir.resolve() if args.output_dir else output_path(recording)
    out_dir.mkdir(parents=True, exist_ok=True)

    labels = parse_labels(args.mask_labels)
    t0 = time.time()
    seg_frames, label_counts = load_segmentation_index(segmentation)
    print(f"Loaded {len(seg_frames)} segmentation frames in {time.time() - t0:.1f}s")

    frame_iter = iter_messages(recording, perceiver_pb2.PerceiverDataFrame)
    first_frame = next(frame_iter, None)
    if first_frame is None:
        raise RuntimeError("Recording is empty")

    # Skip until requested start frame.
    frame_index = 0
    while frame_index < args.start_frame and first_frame is not None:
        first_frame = next(frame_iter, None)
        frame_index += 1
    if first_frame is None:
        raise RuntimeError("start-frame beyond end of recording")

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

    sift = cv2.SIFT_create(nfeatures=args.sift_nfeatures)
    matcher = cv2.BFMatcher(cv2.NORM_L2, crossCheck=False)

    prev_frame = first_frame
    prev_bgr = first_bgr
    prev_gray = cv2.cvtColor(first_bgr, cv2.COLOR_BGR2GRAY)
    prev_mask, prev_mask_pixels = combined_mask(
        seg_frames,
        prev_frame,
        prev_gray.shape,
        labels,
        args.mask_dilate,
        args.bottom_border,
    )
    prev_kp, prev_desc = sift.detectAndCompute(prev_gray, cv2.bitwise_not(prev_mask))

    world_r_cam = np.eye(3, dtype=np.float64)
    world_t_cam = np.zeros(3, dtype=np.float64)

    traj_csv = (out_dir / "trajectory_pairwise_sift.csv").open("w", newline="")
    traj_writer = csv.DictWriter(
        traj_csv,
        fieldnames=["frame_index", "frame_number", "timestamp_ns", "x", "y", "z", "qx", "qy", "qz", "qw"],
    )
    traj_writer.writeheader()

    pair_csv = (out_dir / "pairwise_sift_motion.csv").open("w", newline="")
    pair_writer = csv.DictWriter(
        pair_csv,
        fieldnames=[
            "prev_frame_index",
            "frame_index",
            "prev_timestamp_ns",
            "timestamp_ns",
            "status",
            "keypoints_prev",
            "keypoints",
            "good_match_count",
            "essential_inlier_count",
            "essential_inlier_ratio",
            "translation_magnitude",
            "rotation_deg",
            "dx",
            "dy",
            "dz",
            "qx",
            "qy",
            "qz",
            "qw",
            "mask_pixels_prev",
            "mask_pixels",
        ],
    )
    pair_writer.writeheader()

    gps_csv = (out_dir / "gps_track.csv").open("w", newline="")
    gps_writer = csv.DictWriter(
        gps_csv,
        fieldnames=["frame_index", "frame_number", "timestamp_ns", "latitude", "longitude", "altitude", "accuracy"],
    )
    gps_writer.writeheader()

    events_file = (out_dir / "events.jsonl").open("w")
    debug_dir = out_dir / "debug_pairs"
    debug_dir.mkdir(parents=True, exist_ok=True)

    first_q = world_quaternion_from_rotation(world_r_cam)
    traj_rows = [
        {
            "frame_index": args.start_frame,
            "frame_number": int(prev_frame.frame_identifier.frame_number),
            "timestamp_ns": int(prev_frame.frame_identifier.timestamp_ns),
            "x": float(world_t_cam[0]),
            "y": float(world_t_cam[1]),
            "z": float(world_t_cam[2]),
            "qx": float(first_q[0]),
            "qy": float(first_q[1]),
            "qz": float(first_q[2]),
            "qw": float(first_q[3]),
        }
    ]
    traj_writer.writerow(traj_rows[0])
    gps_rows: list[dict[str, float]] = []
    if prev_frame.HasField("gps_location"):
        gps_row = {
            "frame_index": args.start_frame,
            "frame_number": int(prev_frame.frame_identifier.frame_number),
            "timestamp_ns": int(prev_frame.frame_identifier.timestamp_ns),
            "latitude": float(prev_frame.gps_location.latitude),
            "longitude": float(prev_frame.gps_location.longitude),
            "altitude": float(prev_frame.gps_location.altitude),
            "accuracy": float(prev_frame.gps_location.accuracy),
        }
        gps_writer.writerow(gps_row)
        gps_rows.append(gps_row)

    processed_pairs = 0
    ok_pairs = 0
    last_successful_pair_index = None
    start_time = time.time()

    for cur_frame in frame_iter:
        cur_index = args.start_frame + processed_pairs + 1
        if args.max_frames > 0 and processed_pairs >= max(args.max_frames - 1, 0):
            break

        cur_bgr = decode_rgb(cur_frame)
        cur_gray = cv2.cvtColor(cur_bgr, cv2.COLOR_BGR2GRAY)
        cur_mask, cur_mask_pixels = combined_mask(
            seg_frames,
            cur_frame,
            cur_gray.shape,
            labels,
            args.mask_dilate,
            args.bottom_border,
        )
        cur_kp, cur_desc = sift.detectAndCompute(cur_gray, cv2.bitwise_not(cur_mask))

        pair_status = "no_descriptors"
        good_matches: list[cv2.DMatch] = []
        inlier_matches: list[cv2.DMatch] = []
        rel_r = np.eye(3, dtype=np.float64)
        rel_t = np.zeros((3, 1), dtype=np.float64)
        rot_deg = 0.0
        trans_mag = 0.0

        if prev_desc is not None and cur_desc is not None and len(prev_kp) >= 8 and len(cur_kp) >= 8:
            raw_matches = matcher.knnMatch(prev_desc, cur_desc, k=2)
            for pair in raw_matches:
                if len(pair) < 2:
                    continue
                m, n = pair
                if m.distance < args.ratio_test * n.distance:
                    good_matches.append(m)
            if len(good_matches) >= args.min_good_matches:
                pts_prev = np.float32([prev_kp[m.queryIdx].pt for m in good_matches])
                pts_cur = np.float32([cur_kp[m.trainIdx].pt for m in good_matches])
                E, e_mask = cv2.findEssentialMat(
                    pts_prev,
                    pts_cur,
                    K,
                    method=cv2.RANSAC,
                    prob=0.999,
                    threshold=args.essential_threshold,
                )
                if E is not None and e_mask is not None:
                    _, rel_r, rel_t, pose_mask = cv2.recoverPose(E, pts_prev, pts_cur, K)
                    pose_mask = pose_mask.reshape(-1).astype(bool)
                    inlier_matches = [m for m, keep in zip(good_matches, pose_mask) if keep]
                    if len(inlier_matches) >= args.min_inliers:
                        pair_status = "ok"
                        ok_pairs += 1
                        trans_cam1 = (-rel_r.T @ rel_t).reshape(3)
                        world_t_cam = world_t_cam + world_r_cam @ trans_cam1
                        world_r_cam = world_r_cam @ rel_r.T
                        trans_mag = float(np.linalg.norm(trans_cam1))
                        rot_deg = float(math.degrees(np.linalg.norm(cv2.Rodrigues(rel_r)[0])))
                        last_successful_pair_index = cur_index
                    else:
                        pair_status = "low_inliers"
                else:
                    pair_status = "essential_failed"
            else:
                pair_status = "too_few_matches"

        q = world_quaternion_from_rotation(world_r_cam)
        traj_row = {
            "frame_index": cur_index,
            "frame_number": int(cur_frame.frame_identifier.frame_number),
            "timestamp_ns": int(cur_frame.frame_identifier.timestamp_ns),
            "x": float(world_t_cam[0]),
            "y": float(world_t_cam[1]),
            "z": float(world_t_cam[2]),
            "qx": float(q[0]),
            "qy": float(q[1]),
            "qz": float(q[2]),
            "qw": float(q[3]),
        }
        traj_rows.append(traj_row)
        traj_writer.writerow(traj_row)

        if cur_frame.HasField("gps_location"):
            gps_row = {
                "frame_index": cur_index,
                "frame_number": int(cur_frame.frame_identifier.frame_number),
                "timestamp_ns": int(cur_frame.frame_identifier.timestamp_ns),
                "latitude": float(cur_frame.gps_location.latitude),
                "longitude": float(cur_frame.gps_location.longitude),
                "altitude": float(cur_frame.gps_location.altitude),
                "accuracy": float(cur_frame.gps_location.accuracy),
            }
            gps_writer.writerow(gps_row)
            gps_rows.append(gps_row)

        rel_q = rotation_matrix_to_quaternion_xyzw(rel_r)
        pair_row = {
            "prev_frame_index": cur_index - 1,
            "frame_index": cur_index,
            "prev_timestamp_ns": int(prev_frame.frame_identifier.timestamp_ns),
            "timestamp_ns": int(cur_frame.frame_identifier.timestamp_ns),
            "status": pair_status,
            "keypoints_prev": 0 if prev_kp is None else len(prev_kp),
            "keypoints": 0 if cur_kp is None else len(cur_kp),
            "good_match_count": len(good_matches),
            "essential_inlier_count": len(inlier_matches),
            "essential_inlier_ratio": 0.0 if not good_matches else len(inlier_matches) / len(good_matches),
            "translation_magnitude": trans_mag,
            "rotation_deg": rot_deg,
            "dx": float(rel_t[0, 0]),
            "dy": float(rel_t[1, 0]),
            "dz": float(rel_t[2, 0]),
            "qx": float(rel_q[0]),
            "qy": float(rel_q[1]),
            "qz": float(rel_q[2]),
            "qw": float(rel_q[3]),
            "mask_pixels_prev": prev_mask_pixels,
            "mask_pixels": cur_mask_pixels,
        }
        pair_writer.writerow(pair_row)
        events_file.write(json.dumps(pair_row) + "\n")

        if pair_status != "ok":
            debug_image = draw_match_debug(
                prev_bgr,
                prev_mask,
                prev_kp if prev_kp is not None else [],
                cur_bgr,
                cur_mask,
                cur_kp if cur_kp is not None else [],
                good_matches[:150],
            )
            cv2.imwrite(str(debug_dir / f"pair_{cur_index - 1:05d}_{cur_index:05d}_{pair_status}.png"), debug_image)

        prev_frame = cur_frame
        prev_bgr = cur_bgr
        prev_gray = cur_gray
        prev_mask = cur_mask
        prev_mask_pixels = cur_mask_pixels
        prev_kp = cur_kp
        prev_desc = cur_desc

        processed_pairs += 1
        if processed_pairs % 100 == 0:
            elapsed = time.time() - start_time
            print(
                f"Processed {processed_pairs} pairs in {elapsed:.1f}s "
                f"({processed_pairs / max(elapsed, 1e-6):.1f} pairs/s), ok={ok_pairs}"
            )

    write_track_plot(out_dir / "track_plot.png", traj_rows, gps_rows)

    if traj_rows and gps_rows:
        slam_xyz = np.array([[r["x"], r["y"], r["z"]] for r in traj_rows], dtype=np.float64)
        slam_2d = project_track_to_2d(slam_xyz)
        gps_2d = gps_to_local_xy(
            np.array([r["latitude"] for r in gps_rows], dtype=np.float64),
            np.array([r["longitude"] for r in gps_rows], dtype=np.float64),
        )
        common_count = min(len(slam_2d), len(gps_2d))
        rmse = None
        if common_count >= 20:
            scale, rot, trans = fit_similarity(slam_2d[:common_count], gps_2d[:common_count])
            aligned = (scale * (rot @ slam_2d[:common_count].T)).T + trans
            rmse = float(np.sqrt(np.mean(np.sum((aligned - gps_2d[:common_count]) ** 2, axis=1))))
        else:
            rmse = None
    else:
        rmse = None

    summary = {
        "recording": str(recording),
        "segmentation": str(segmentation),
        "output_dir": str(out_dir),
        "start_frame": args.start_frame,
        "processed_pairs": processed_pairs,
        "ok_pairs": ok_pairs,
        "success_rate": 0.0 if processed_pairs == 0 else ok_pairs / processed_pairs,
        "last_successful_pair_frame_index": last_successful_pair_index,
        "mask_labels": labels,
        "mask_dilate": args.mask_dilate,
        "bottom_border": args.bottom_border,
        "sift_nfeatures": args.sift_nfeatures,
        "ratio_test": args.ratio_test,
        "essential_threshold": args.essential_threshold,
        "min_good_matches": args.min_good_matches,
        "min_inliers": args.min_inliers,
        "trajectory_rows": len(traj_rows),
        "gps_rows": len(gps_rows),
        "elapsed_s": time.time() - start_time,
        "aligned_gps_rmse_m": rmse,
        "intrinsics": intr_meta,
    }
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=2))
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
