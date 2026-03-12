#!/usr/bin/env python3
"""
Offline motion heatmap generation from a .vis.pb recording.

Uses RAFT optical flow to get per-pixel motion, then subtracts the flow
predicted by the ARCore camera pose + depth to isolate independently moving
objects.  Normalisation is global across the whole recording so that quiet
frames are dark and frames with real motion are bright.

Output: recordings/<name>.motion.pb  (length-delimited MotionCaptureResponse)

Usage:
    cd server
    uv run python motioncap/main.py ../recordings/<name>.vis.pb
    uv run python motioncap/main.py ../recordings/<name>.vis.pb --max-frames 200
    uv run python motioncap/main.py ../recordings/<name>.vis.pb --output-video
"""

import argparse
import bisect
import sys
import time
from pathlib import Path

import cv2
import numpy as np
import yaml
from tqdm import tqdm

_server_root  = Path(__file__).resolve().parent.parent
_project_root = _server_root.parent
sys.path.insert(0, str(_project_root))
sys.path.insert(0, str(_project_root / "proto"))
sys.path.insert(0, str(_server_root))

from proto import perceiver_pb2, motioncap_pb2
from streamlog.protoio import ProtoIO

from motioncap.geometry import (
    decode_frame_rgb, decode_depth, encode_heatmap,
    build_K, pose_components, Method,
)
from motioncap.raft_motion import load_raft, compute_residual

_frame_io  = ProtoIO(perceiver_pb2.PerceiverDataFrame)
_motion_io = ProtoIO(motioncap_pb2.MotionCaptureResponse)

_config_path = Path(__file__).parent / "motioncap_config.yaml"
with open(_config_path) as _f:
    _CONFIG = yaml.safe_load(_f)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _motion_path(rec: Path) -> Path:
    stem = rec.name.removesuffix(".vis.pb") if rec.name.endswith(".vis.pb") else rec.stem
    return rec.parent / (stem + ".motion.pb")


def _to_heatmap_u8(residual: np.ndarray, scale: float,
                   res_thresh: float, blur_kernel: int) -> np.ndarray:
    """
    Apply noise-floor threshold, Gaussian blur, and global-scale normalisation
    to a raw float residual map.  Returns uint8 (H, W).
    """
    r = residual.copy()
    r[r < res_thresh] = 0.0
    if blur_kernel > 1:
        k = blur_kernel if blur_kernel % 2 == 1 else blur_kernel + 1
        r = cv2.GaussianBlur(r, (k, k), 0)
    if scale > 0:
        r = (r / scale * 255.0).clip(0, 255)
    return r.astype(np.uint8)


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate motion heatmaps from a .vis.pb recording using RAFT")
    parser.add_argument("recording",      help="Path to .vis.pb recording")
    parser.add_argument("--max-frames",   type=int, default=0,
                        help="Limit to first N frames for testing (0 = all)")
    parser.add_argument("--output-video", action="store_true",
                        help="Write a heatmap-overlay .mp4 alongside the .motion.pb")
    args = parser.parse_args()

    cfg = _CONFIG

    rec_path = Path(args.recording).resolve()
    if not rec_path.exists():
        print(f"File not found: {rec_path}")
        sys.exit(1)

    out_path = _motion_path(rec_path)

    # ── Load RAFT ─────────────────────────────────────────────────────────────
    variant = cfg["raft"]["variant"]
    model, device = load_raft(variant=variant)

    # ── Load frames ───────────────────────────────────────────────────────────
    print(f"\nLoading {rec_path.name} …")
    t0     = time.time()
    frames = _frame_io.read_file(rec_path)
    if not frames:
        print("No frames found in recording")
        sys.exit(1)
    print(f"Loaded {len(frames)} frames in {time.time()-t0:.1f}s")

    if args.max_frames > 0 and len(frames) > args.max_frames:
        frames = frames[:args.max_frames]
        print(f"Limiting to first {args.max_frames} frames")

    # ── Camera intrinsics ─────────────────────────────────────────────────────
    K_cache: list[np.ndarray] = []
    for f in frames:
        if build_K(f, K_cache) is not None:
            break
    K = K_cache[0] if K_cache else None
    if K is not None:
        print(f"Intrinsics: fx={K[0,0]:.1f}  fy={K[1,1]:.1f}  "
              f"cx={K[0,2]:.1f}  cy={K[1,2]:.1f}")
    else:
        print("No camera intrinsics found — will fall back to median-flow subtraction")

    # ── Reference-frame strategy ──────────────────────────────────────────────
    ref_cfg      = cfg["reference"]
    ref_strategy = ref_cfg["strategy"]
    rolling_gap  = ref_cfg["rolling_gap"]
    time_delta_s = ref_cfg["time_delta_s"]

    time_index = None
    if ref_strategy == "time":
        ts = [f.frame_identifier.timestamp_ns for f in frames]
        time_index = sorted(zip(ts, range(len(frames))))

    # ── Pass 1: compute residuals, collect raw results ────────────────────────
    # We store float16 residual maps (halves memory vs float32) plus max_raw.
    # This allows global normalisation in pass 2 without re-running RAFT.
    # Memory estimate: H * W * 2 bytes * N_frames
    #   e.g. 720×1280 frames × 300 frames ≈ 553 MB

    raft_cfg         = cfg["raft"]
    subtraction_mode = raft_cfg.get("subtraction_mode", "pose")
    raw_residuals: list[np.ndarray] = []   # float16 (H, W) per frame
    max_raws:      list[float]      = []
    rgb_cache:     list[np.ndarray] = []   # keep decoded RGB for video pass

    print(f"\nPass 1 / 2 — computing RAFT residuals "
          f"(ref={ref_strategy}, subtraction={subtraction_mode}) …")
    t_pass1 = time.time()

    with tqdm(total=len(frames), desc=f"RAFT-{variant}", unit="frame") as bar:
        for idx, frame_curr in enumerate(frames):

            # Select reference frame
            if ref_strategy == "consecutive":
                ref_idx = max(0, idx - 1)
            elif ref_strategy == "time":
                target_ns = (frame_curr.frame_identifier.timestamp_ns
                             - int(time_delta_s * 1e9))
                pos     = bisect.bisect_left(time_index, (target_ns, -1))
                ref_idx = time_index[pos - 1][1] if pos > 0 else 0
            elif ref_strategy == "rolling":
                ref_idx = max(0, idx - rolling_gap)
            else:  # "first"
                ref_idx = 0

            rgb_curr = decode_frame_rgb(frame_curr)
            rgb_cache.append(rgb_curr)
            h, w = rgb_curr.shape[:2]

            if ref_idx == idx:
                # Nothing to compare — zero residual
                raw_residuals.append(np.zeros((h, w), dtype=np.float16))
                max_raws.append(0.0)
            else:
                frame_ref = frames[ref_idx]
                rgb_ref   = decode_frame_rgb(frame_ref)

                R_ref, t_ref   = pose_components(frame_ref)
                R_curr, t_curr = pose_components(frame_curr)

                # Use depth from the current frame if available
                depth = decode_depth(frame_curr, h, w)

                try:
                    residual, max_raw = compute_residual(
                        rgb_ref, rgb_curr,
                        model, device, cfg,
                        K=K,
                        R_ref=R_ref, t_ref=t_ref,
                        R_curr=R_curr, t_curr=t_curr,
                        depth=depth,
                        subtraction_mode=subtraction_mode,
                    )
                    raw_residuals.append(residual.astype(np.float16))
                    max_raws.append(max_raw)
                except Exception as exc:
                    tqdm.write(f"Warning: frame {idx} failed: {exc}")
                    raw_residuals.append(np.zeros((h, w), dtype=np.float16))
                    max_raws.append(0.0)

            bar.update(1)

    elapsed1 = time.time() - t_pass1
    fps1 = len(frames) / elapsed1 if elapsed1 > 0 else 0
    print(f"Pass 1 done in {elapsed1:.1f}s ({fps1:.2f} fps)")

    # ── Global normalisation scale ────────────────────────────────────────────
    norm_pct   = cfg.get("normalization", {}).get("percentile", 99)
    non_zero   = [m for m in max_raws if m > 0]
    if non_zero:
        global_scale = float(np.percentile(non_zero, norm_pct))
    else:
        global_scale = 1.0
    print(f"Global scale ({norm_pct}th percentile of peak residuals): "
          f"{global_scale:.2f} px")

    # ── Pass 2: normalise → write proto + video ───────────────────────────────
    res_thresh  = raft_cfg["residual_threshold_px"]
    blur_kernel = raft_cfg["blur_kernel"]
    alpha       = cfg["output"]["overlay_alpha"]

    if out_path.exists():
        out_path.unlink()

    video_writer = None
    video_path   = None
    if args.output_video and rgb_cache:
        h_v, w_v   = rgb_cache[0].shape[:2]
        stem       = (rec_path.name.removesuffix(".vis.pb")
                      if rec_path.name.endswith(".vis.pb") else rec_path.stem)
        video_path = rec_path.parent / (stem + ".motion.mp4")
        codec      = cv2.VideoWriter_fourcc(*cfg["output"]["video_codec"])
        if len(frames) >= 2:
            dur = (frames[-1].frame_identifier.timestamp_ns
                   - frames[0].frame_identifier.timestamp_ns) / 1e9
            fps_v = (len(frames) - 1) / dur if dur > 0 else 30.0
        else:
            fps_v = 30.0
        video_writer = cv2.VideoWriter(str(video_path), codec, fps_v, (w_v, h_v))
        print(f"Writing video → {video_path.name}  ({fps_v:.1f} fps)")

    print(f"\nPass 2 / 2 — normalising and writing …")
    batch: list[motioncap_pb2.MotionCaptureResponse] = []

    for idx, frame_curr in enumerate(tqdm(frames, desc="Writing", unit="frame")):
        residual  = raw_residuals[idx].astype(np.float32)
        max_raw   = max_raws[idx]

        heatmap_u8 = _to_heatmap_u8(residual, global_scale, res_thresh, blur_kernel)

        resp = motioncap_pb2.MotionCaptureResponse()
        resp.frame_identifier.CopyFrom(frame_curr.frame_identifier)
        resp.method_used               = Method.OPTICAL_FLOW
        resp.stabilization_confidence  = 1.0 if max_raw > 0 else 0.0
        resp.heatmap.heatmap_data      = encode_heatmap(heatmap_u8)
        resp.heatmap.max_motion_raw    = max_raw
        batch.append(resp)

        if len(batch) >= 50:
            _motion_io.write_file(out_path, batch)
            batch.clear()

        if video_writer is not None:
            rgb_bgr       = cv2.cvtColor(rgb_cache[idx], cv2.COLOR_RGB2BGR)
            heatmap_color = cv2.applyColorMap(heatmap_u8, cv2.COLORMAP_JET)
            overlay       = cv2.addWeighted(rgb_bgr, 1 - alpha, heatmap_color, alpha, 0)
            video_writer.write(overlay)

    if batch:
        _motion_io.write_file(out_path, batch)
    if video_writer:
        video_writer.release()

    print(f"\nDone.")
    print(f"  {len(frames)} frames → {out_path.name}")
    if video_path:
        print(f"  Video → {video_path.name}")


if __name__ == "__main__":
    main()
