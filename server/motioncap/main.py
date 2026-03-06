#!/usr/bin/env python3
"""
Offline motion heatmap generation from a .vis.pb recording.

For each frame, camera motion is removed via a 5-level stabilization hierarchy
(best available method is chosen automatically), then per-pixel residual motion
is computed and stored as a compressed heatmap.

Output: recordings/<name>.motion.pb — length-delimited MotionCaptureResponse protos.

Usage:
    cd server
    uv run python motioncap/main.py ../recordings/<name>.vis.pb
    uv run python motioncap/main.py ../recordings/<name>.vis.pb --method pose
    uv run python motioncap/main.py ../recordings/<name>.vis.pb --max-frames 200 --output-video

Stabilization method hierarchy (auto mode):
    1. DEPTH_WARP   — per-pixel dense warp from depth + 6DOF pose
    2. PLANE_HOMO   — plane homography H = K(R - tnᵀ/d)K⁻¹
    3. POINT_RANSAC — RANSAC homography from projected ARCore point cloud
    4. POSE_APPROX  — rotation + translation with assumed/estimated depth
    5. OPTICAL_FLOW — 2D optical flow dominant background (no pose required)
"""

import argparse
import struct
import sys
import time
import zlib
from pathlib import Path

import cv2
import numpy as np
import yaml
from tqdm import tqdm

_server_root = Path(__file__).resolve().parent.parent
_project_root = _server_root.parent
sys.path.insert(0, str(_project_root))
sys.path.insert(0, str(_project_root / "proto"))
sys.path.insert(0, str(_server_root))

from proto import perceiver_pb2, motioncap_pb2
from streamlog.protoio import ProtoIO

from motioncap.geometry import (
    decode_frame_rgb, decode_depth, encode_heatmap,
    quat_to_rot, pose_components, build_K, world_to_pixel,
    _warp_depth, _warp_homography, stabilize, compute_heatmap,
    Method,
)

_frame_io = ProtoIO(perceiver_pb2.PerceiverDataFrame)
_motion_io = ProtoIO(motioncap_pb2.MotionCaptureResponse)

_config_path = Path(__file__).parent / "motioncap_config.yaml"
with open(_config_path) as _f:
    _CONFIG = yaml.safe_load(_f)


# ── Output path ─────────────────────────────────────────────────────────────

def motion_path(recording_path: Path) -> Path:
    name = recording_path.name
    if name.endswith(".vis.pb"):
        return recording_path.parent / (name.removesuffix(".vis.pb") + ".motion.pb")
    return recording_path.with_suffix(".motion.pb")


# ── Main ─────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Generate per-frame motion heatmaps from a .vis.pb recording")
    parser.add_argument("recording", help="Path to .vis.pb recording")
    parser.add_argument(
        "--method", default="auto",
        choices=["auto", "depth", "plane", "points", "pose", "flow"],
        help="Stabilization method (default: auto = best available)")
    parser.add_argument(
        "--reference", default=None, choices=["first", "rolling"],
        help="Reference frame strategy (default: from config)")
    parser.add_argument(
        "--rolling-gap", type=int, default=None,
        help="Frames apart for rolling reference (default: from config)")
    parser.add_argument(
        "--threshold", type=int, default=None,
        help="Noise threshold 0–255 (default: from config)")
    parser.add_argument(
        "--max-frames", type=int, default=0,
        help="Limit to first N frames for testing (0 = all)")
    parser.add_argument(
        "--output-video", action="store_true",
        help="Also write a heatmap overlay .mp4 alongside the .motion.pb")
    args = parser.parse_args()

    # Merge CLI overrides into config
    cfg = _CONFIG
    if args.reference:
        cfg["reference"]["strategy"] = args.reference
    if args.rolling_gap is not None:
        cfg["reference"]["rolling_gap"] = args.rolling_gap
    if args.threshold is not None:
        cfg["heatmap"]["noise_threshold"] = args.threshold

    rec_path = Path(args.recording).resolve()
    if not rec_path.exists():
        print(f"File not found: {rec_path}")
        sys.exit(1)

    out_path = motion_path(rec_path)

    # ── Load frames ───────────────────────────────────────────────────────
    print(f"Loading {rec_path.name}...")
    t0 = time.time()
    frames = _frame_io.read_file(rec_path)
    if not frames:
        print("No frames in recording")
        sys.exit(1)
    print(f"Loaded {len(frames)} frames in {time.time()-t0:.1f}s")

    if args.max_frames > 0 and len(frames) > args.max_frames:
        frames = frames[:args.max_frames]
        print(f"Limiting to first {args.max_frames} frames")

    # ── Cache intrinsics ──────────────────────────────────────────────────
    K_cache: list[np.ndarray] = []
    for f in frames:
        K = build_K(f, K_cache)
        if K is not None:
            break
    if not K_cache:
        print("No camera intrinsics found in recording — cannot stabilize")
        sys.exit(1)
    K = K_cache[0]
    print(f"Intrinsics: fx={K[0,0]:.1f} fy={K[1,1]:.1f} cx={K[0,2]:.1f} cy={K[1,2]:.1f}")

    # ── Clear output file ─────────────────────────────────────────────────
    if out_path.exists():
        out_path.unlink()

    # ── Video writer (optional) ───────────────────────────────────────────
    video_writer = None
    if args.output_video:
        sample_rgb = decode_frame_rgb(frames[0])
        h, w = sample_rgb.shape[:2]
        video_path = rec_path.with_suffix("").with_suffix("").with_suffix(".motion.mp4")
        if rec_path.name.endswith(".vis.pb"):
            video_path = rec_path.parent / (rec_path.name.removesuffix(".vis.pb") + ".motion.mp4")
        codec = cv2.VideoWriter_fourcc(*cfg["output"]["video_codec"])
        # Estimate native FPS from timestamps
        if len(frames) >= 2:
            dur = (frames[-1].frame_identifier.timestamp_ns -
                   frames[0].frame_identifier.timestamp_ns) / 1e9
            fps_native = (len(frames) - 1) / dur if dur > 0 else 30.0
        else:
            fps_native = 30.0
        video_writer = cv2.VideoWriter(str(video_path), codec, fps_native, (w, h))
        print(f"Writing video to {video_path.name} at {fps_native:.1f} fps")

    # ── Process frames ────────────────────────────────────────────────────
    noise_thresh = cfg["heatmap"]["noise_threshold"]
    blur_kernel  = cfg["heatmap"]["blur_kernel"]
    ref_strategy = cfg["reference"]["strategy"]
    rolling_gap  = cfg["reference"]["rolling_gap"]
    alpha        = cfg["output"]["overlay_alpha"]

    total_results = 0
    batch: list[motioncap_pb2.MotionCaptureResponse] = []
    total_t0 = time.time()

    method_counts: dict[str, int] = {}

    with tqdm(total=len(frames), desc="Motion heatmap", unit="frame") as progress:
        for idx, frame_curr in enumerate(frames):

            # Select reference frame
            if ref_strategy == "rolling":
                ref_idx = max(0, idx - rolling_gap)
            else:  # "first"
                ref_idx = 0

            if ref_idx == idx:
                # Nothing to compare against — emit zero heatmap
                rgb_curr = decode_frame_rgb(frame_curr)
                h, w = rgb_curr.shape[:2]
                heatmap_u8 = np.zeros((h, w), dtype=np.uint8)
                method_used = Method.STABILIZATION_UNKNOWN
                stab_conf = 0.0
                max_raw = 0.0
            else:
                frame_ref = frames[ref_idx]
                R_ref, t_ref = pose_components(frame_ref)
                R_curr, t_curr = pose_components(frame_curr)

                rgb_curr = decode_frame_rgb(frame_curr)
                h, w = rgb_curr.shape[:2]

                if R_ref is None or R_curr is None:
                    # No pose — fall back to optical flow only
                    warped, method_used, stab_conf = stabilize(
                        rgb_curr, frame_curr, frame_ref,
                        None, None, None, None, K, cfg, "flow")
                else:
                    warped, method_used, stab_conf = stabilize(
                        rgb_curr, frame_curr, frame_ref,
                        R_ref, t_ref, R_curr, t_curr, K, cfg, args.method)

                rgb_ref = decode_frame_rgb(frame_ref)
                heatmap_u8, max_raw = compute_heatmap(
                    warped, rgb_ref, noise_thresh, blur_kernel)

            # Track method usage
            m_name = Method.Name(method_used)
            method_counts[m_name] = method_counts.get(m_name, 0) + 1

            # Build proto
            resp = motioncap_pb2.MotionCaptureResponse()
            resp.frame_identifier.CopyFrom(frame_curr.frame_identifier)
            resp.method_used = method_used
            resp.stabilization_confidence = stab_conf
            resp.heatmap.heatmap_data = encode_heatmap(heatmap_u8)
            resp.heatmap.max_motion_raw = max_raw
            batch.append(resp)
            total_results += 1

            # Write every 50 frames
            if len(batch) >= 50:
                _motion_io.write_file(out_path, batch)
                batch.clear()

            # Optional video frame
            if video_writer is not None:
                rgb_bgr = cv2.cvtColor(rgb_curr, cv2.COLOR_RGB2BGR)
                heatmap_color = cv2.applyColorMap(heatmap_u8, cv2.COLORMAP_JET)
                overlay = cv2.addWeighted(rgb_bgr, 1 - alpha, heatmap_color, alpha, 0)
                video_writer.write(overlay)

            progress.update(1)

    if batch:
        _motion_io.write_file(out_path, batch)
    if video_writer:
        video_writer.release()

    total_elapsed = time.time() - total_t0
    fps = len(frames) / total_elapsed if total_elapsed > 0 else 0
    print(f"\nDone: {len(frames)} frames in {total_elapsed:.1f}s ({fps:.2f} fps)")
    print(f"Wrote {total_results} MotionCaptureResponse protos → {out_path.name}")
    print(f"Methods used: {method_counts}")
    if args.output_video:
        print(f"Video: {video_path.name}")


if __name__ == "__main__":
    main()
