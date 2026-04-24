#!/usr/bin/env python3
"""
Build a monocular trajectory from consecutive masked local-feature frame pairs.

The default backend uses ALIKED + LightGlue on CUDA when available and falls
back to masked SIFT + BF matching otherwise.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import sys
import time
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
    decode_rgb,
    fit_similarity,
    gps_to_local_xy,
    iter_messages,
    load_segmentation_index,
    pairwise_output_dir,
    project_track_to_2d,
    seg_path,
    write_track_plot,
)
from idoslam.sift_pair_pose import rotation_matrix_to_quaternion_xyzw


@dataclass
class ExtractedFeatures:
    keypoints_xy: np.ndarray
    payload: object


@dataclass
class MatchedFeatures:
    points_prev: np.ndarray
    points_cur: np.ndarray


class PairwiseFeatureBackend:
    name = "unknown"
    device = "cpu"
    uses_gpu = False

    def detect_and_compute(self, bgr: np.ndarray, mask: np.ndarray) -> ExtractedFeatures:
        raise NotImplementedError

    def match(self, prev: ExtractedFeatures, cur: ExtractedFeatures) -> MatchedFeatures:
        raise NotImplementedError


class SIFTFeatureBackend(PairwiseFeatureBackend):
    name = "sift"
    device = "cpu"
    uses_gpu = False

    def __init__(self, args: argparse.Namespace) -> None:
        self.sift = cv2.SIFT_create(nfeatures=args.sift_nfeatures)
        self.matcher = cv2.BFMatcher(cv2.NORM_L2, crossCheck=False)
        self.ratio_test = float(args.ratio_test)

    def detect_and_compute(self, bgr: np.ndarray, mask: np.ndarray) -> ExtractedFeatures:
        gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
        kp, desc = self.sift.detectAndCompute(gray, cv2.bitwise_not(mask))
        keypoints_xy = (
            np.array([keypoint.pt for keypoint in kp], dtype=np.float32)
            if kp is not None and len(kp) > 0
            else np.empty((0, 2), dtype=np.float32)
        )
        return ExtractedFeatures(keypoints_xy=keypoints_xy, payload=(kp or [], desc))

    def match(self, prev: ExtractedFeatures, cur: ExtractedFeatures) -> MatchedFeatures:
        prev_kp, prev_desc = prev.payload
        cur_kp, cur_desc = cur.payload
        if prev_desc is None or cur_desc is None or len(prev_kp) < 8 or len(cur_kp) < 8:
            return MatchedFeatures(
                points_prev=np.empty((0, 2), dtype=np.float32),
                points_cur=np.empty((0, 2), dtype=np.float32),
            )
        good_matches: list[cv2.DMatch] = []
        raw_matches = self.matcher.knnMatch(prev_desc, cur_desc, k=2)
        for pair in raw_matches:
            if len(pair) < 2:
                continue
            m, n = pair
            if m.distance < self.ratio_test * n.distance:
                good_matches.append(m)
        if not good_matches:
            return MatchedFeatures(
                points_prev=np.empty((0, 2), dtype=np.float32),
                points_cur=np.empty((0, 2), dtype=np.float32),
            )
        points_prev = np.float32([prev_kp[m.queryIdx].pt for m in good_matches])
        points_cur = np.float32([cur_kp[m.trainIdx].pt for m in good_matches])
        return MatchedFeatures(points_prev=points_prev, points_cur=points_cur)


class LightGlueFeatureBackend(PairwiseFeatureBackend):
    uses_gpu = True

    def __init__(self, args: argparse.Namespace, extractor_name: str) -> None:
        try:
            import torch
            from lightglue import ALIKED, LightGlue, SuperPoint
        except ImportError as exc:
            raise RuntimeError("The LightGlue backends require torch and lightglue") from exc
        if not torch.cuda.is_available():
            raise RuntimeError("The LightGlue backends require a CUDA-capable torch runtime")
        self._torch = torch
        self.name = f"{extractor_name}_lightglue"
        self.device = f"cuda:{torch.cuda.current_device()}"
        self.resize = int(args.lightglue_resize) if int(args.lightglue_resize) > 0 else None
        torch.set_float32_matmul_precision("high")
        torch.backends.cudnn.benchmark = True
        if extractor_name == "aliked":
            self.extractor = ALIKED(max_num_keypoints=int(args.lightglue_max_keypoints)).eval().to(self.device)
        elif extractor_name == "superpoint":
            self.extractor = SuperPoint(max_num_keypoints=int(args.lightglue_max_keypoints)).eval().to(self.device)
        else:
            raise ValueError(f"Unsupported LightGlue extractor: {extractor_name}")
        self.matcher = (
            LightGlue(
                features=extractor_name,
                filter_threshold=float(args.lightglue_filter_threshold),
                depth_confidence=float(args.lightglue_depth_confidence),
                width_confidence=float(args.lightglue_width_confidence),
                mp=bool(args.lightglue_mixed_precision),
            )
            .eval()
            .to(self.device)
        )
        if bool(args.lightglue_compile) and hasattr(self.matcher, "compile"):
            try:
                self.matcher.compile(mode="reduce-overhead")
            except Exception as exc:
                print(f"Warning: failed to compile LightGlue matcher, using eager mode: {exc}", file=sys.stderr)

    def detect_and_compute(self, bgr: np.ndarray, mask: np.ndarray) -> ExtractedFeatures:
        rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
        if np.any(mask):
            rgb = rgb.copy()
            rgb[mask > 0] = 0
        image = (
            self._torch.from_numpy(np.ascontiguousarray(rgb))
            .permute(2, 0, 1)
            .float()
            .div(255.0)
            .to(self.device, non_blocking=True)
        )
        with self._torch.inference_mode():
            payload = self.extractor.extract(image, resize=self.resize)
        keypoints_xy = payload["keypoints"][0].detach().cpu().numpy().astype(np.float32)
        return ExtractedFeatures(keypoints_xy=keypoints_xy, payload=payload)

    def match(self, prev: ExtractedFeatures, cur: ExtractedFeatures) -> MatchedFeatures:
        if len(prev.keypoints_xy) < 8 or len(cur.keypoints_xy) < 8:
            return MatchedFeatures(
                points_prev=np.empty((0, 2), dtype=np.float32),
                points_cur=np.empty((0, 2), dtype=np.float32),
            )
        with self._torch.inference_mode():
            matches = self.matcher({"image0": prev.payload, "image1": cur.payload})
        matched_indices = matches.get("matches")
        if isinstance(matched_indices, list):
            matched_indices = matched_indices[0] if matched_indices else None
        elif matched_indices is not None and matched_indices.dim() == 3:
            matched_indices = matched_indices[0]
        if matched_indices is None or int(matched_indices.shape[0]) == 0:
            return MatchedFeatures(
                points_prev=np.empty((0, 2), dtype=np.float32),
                points_cur=np.empty((0, 2), dtype=np.float32),
            )
        index_prev = matched_indices[:, 0].detach().cpu().numpy()
        index_cur = matched_indices[:, 1].detach().cpu().numpy()
        return MatchedFeatures(
            points_prev=prev.keypoints_xy[index_prev],
            points_cur=cur.keypoints_xy[index_cur],
        )


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Build a custom pairwise feature trajectory")
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
    p.add_argument(
        "--feature-backend",
        choices=["auto", "sift", "aliked_lightglue", "superpoint_lightglue"],
        default="auto",
        help="Feature extraction and matching backend",
    )
    p.add_argument("--sift-nfeatures", type=int, default=3000)
    p.add_argument("--ratio-test", type=float, default=0.75)
    p.add_argument("--lightglue-max-keypoints", type=int, default=1024)
    p.add_argument("--lightglue-resize", type=int, default=1024, help="Resize long side for LightGlue extractors")
    p.add_argument("--lightglue-filter-threshold", type=float, default=0.1)
    p.add_argument("--lightglue-depth-confidence", type=float, default=0.95)
    p.add_argument("--lightglue-width-confidence", type=float, default=0.99)
    p.add_argument("--lightglue-mixed-precision", action=argparse.BooleanOptionalAction, default=True)
    p.add_argument("--lightglue-compile", action=argparse.BooleanOptionalAction, default=False)
    p.add_argument("--essential-threshold", type=float, default=1.5)
    p.add_argument("--min-good-matches", type=int, default=20)
    p.add_argument("--min-inliers", type=int, default=20)
    return p.parse_args()


def parse_labels(raw: str) -> list[str]:
    return [item.strip() for item in raw.split(",") if item.strip()]


def output_path(recording: Path) -> Path:
    return pairwise_output_dir(recording)


def resolve_backend(args: argparse.Namespace) -> PairwiseFeatureBackend:
    if args.feature_backend == "sift":
        return SIFTFeatureBackend(args)
    if args.feature_backend == "aliked_lightglue":
        return LightGlueFeatureBackend(args, extractor_name="aliked")
    if args.feature_backend == "superpoint_lightglue":
        return LightGlueFeatureBackend(args, extractor_name="superpoint")
    try:
        return LightGlueFeatureBackend(args, extractor_name="aliked")
    except Exception as exc:
        print(f"Falling back to SIFT backend because GPU backend is unavailable: {exc}", file=sys.stderr)
        return SIFTFeatureBackend(args)


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


def points_to_draw_kp(points_xy: np.ndarray) -> list[cv2.KeyPoint]:
    return [cv2.KeyPoint(float(x), float(y), 1.0) for x, y in points_xy]


def draw_match_debug(
    prev_bgr: np.ndarray,
    prev_mask: np.ndarray,
    prev_points_xy: np.ndarray,
    cur_bgr: np.ndarray,
    cur_mask: np.ndarray,
    cur_points_xy: np.ndarray,
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
    count = int(min(len(prev_points_xy), len(cur_points_xy)))
    prev_kp = points_to_draw_kp(prev_points_xy[:count])
    cur_kp = points_to_draw_kp(cur_points_xy[:count])
    matches = [cv2.DMatch(_queryIdx=i, _trainIdx=i, _distance=0.0) for i in range(count)]
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

    backend = resolve_backend(args)
    print(f"Using feature backend {backend.name} on {backend.device}")

    prev_frame = first_frame
    prev_bgr = first_bgr
    prev_mask, prev_mask_pixels = combined_mask(
        seg_frames,
        prev_frame,
        (image_h, image_w),
        labels,
        args.mask_dilate,
        args.bottom_border,
    )
    prev_features = backend.detect_and_compute(prev_bgr, prev_mask)

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
        cur_mask, cur_mask_pixels = combined_mask(
            seg_frames,
            cur_frame,
            (image_h, image_w),
            labels,
            args.mask_dilate,
            args.bottom_border,
        )
        cur_features = backend.detect_and_compute(cur_bgr, cur_mask)
        matched = backend.match(prev_features, cur_features)

        pair_status = "no_descriptors"
        inlier_prev = np.empty((0, 2), dtype=np.float32)
        inlier_cur = np.empty((0, 2), dtype=np.float32)
        rel_r = np.eye(3, dtype=np.float64)
        rel_t = np.zeros((3, 1), dtype=np.float64)
        rot_deg = 0.0
        trans_mag = 0.0

        if len(matched.points_prev) >= args.min_good_matches:
            E, e_mask = cv2.findEssentialMat(
                matched.points_prev,
                matched.points_cur,
                K,
                method=cv2.RANSAC,
                prob=0.999,
                threshold=args.essential_threshold,
            )
            if E is not None and e_mask is not None:
                _, rel_r, rel_t, pose_mask = cv2.recoverPose(E, matched.points_prev, matched.points_cur, K)
                pose_mask = pose_mask.reshape(-1).astype(bool)
                inlier_prev = matched.points_prev[pose_mask]
                inlier_cur = matched.points_cur[pose_mask]
                if len(inlier_prev) >= args.min_inliers:
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
        elif len(matched.points_prev) > 0:
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
            "keypoints_prev": int(len(prev_features.keypoints_xy)),
            "keypoints": int(len(cur_features.keypoints_xy)),
            "good_match_count": int(len(matched.points_prev)),
            "essential_inlier_count": int(len(inlier_prev)),
            "essential_inlier_ratio": 0.0 if len(matched.points_prev) == 0 else len(inlier_prev) / len(matched.points_prev),
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
                matched.points_prev[:150],
                cur_bgr,
                cur_mask,
                matched.points_cur[:150],
            )
            cv2.imwrite(str(debug_dir / f"pair_{cur_index - 1:05d}_{cur_index:05d}_{pair_status}.png"), debug_image)

        prev_frame = cur_frame
        prev_bgr = cur_bgr
        prev_mask = cur_mask
        prev_mask_pixels = cur_mask_pixels
        prev_features = cur_features

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
        "feature_backend": backend.name,
        "feature_device": backend.device,
        "uses_gpu": bool(backend.uses_gpu),
        "sift_nfeatures": args.sift_nfeatures,
        "ratio_test": args.ratio_test,
        "lightglue_max_keypoints": args.lightglue_max_keypoints,
        "lightglue_resize": args.lightglue_resize,
        "lightglue_filter_threshold": args.lightglue_filter_threshold,
        "lightglue_depth_confidence": args.lightglue_depth_confidence,
        "lightglue_width_confidence": args.lightglue_width_confidence,
        "lightglue_mixed_precision": bool(args.lightglue_mixed_precision),
        "lightglue_compile": bool(args.lightglue_compile),
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
