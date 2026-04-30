#!/usr/bin/env python3
"""
Unified motion capture: RAFT optical flow + persistent object tracking.

Two pipeline steps, both written to a single .motioncap.pb output:
  Step 1 — RAFT: dense optical flow residuals → per-frame heatmaps
  Step 2 — Tracking: temporal filter + blob detection + nearest-neighbour tracker

Both steps are controlled via config.yaml:
  pipeline.regenerate_raft: false      → skip RAFT, load heatmaps from existing .motioncap.pb
  pipeline.regenerate_tracking: false  → skip tracker, load tracks from existing .motioncap.pb

Output
------
  <name>.motioncap.pb   — length-delimited MotionCaptureResponse frames (heatmaps)
                       followed by one summary record containing all tracks
  <name>.motioncap.mp4  — (optional) heatmap overlay on RGB with track visualization

Usage
-----
    cd server
    uv run python motioncap/main.py ../recordings/<name>.vis.pb
    uv run python motioncap/main.py ../recordings/<name>.vis.pb --debug-render-video
    uv run python motioncap/main.py ../recordings/<name>.vis.pb --max-frames 200
"""

import argparse
import bisect
import gc
import os
import sys
import time
from pathlib import Path

# Must be set before torch is imported by motioncap.raft_motion.
os.environ.setdefault("PYTORCH_CUDA_ALLOC_CONF", "expandable_segments:True")

import cv2
import numpy as np
import yaml
from tqdm import tqdm

_server_root = Path(__file__).resolve().parent.parent
_project_root = _server_root.parent
sys.path.insert(0, str(_project_root))
sys.path.insert(0, str(_project_root / "proto"))
sys.path.insert(0, str(_server_root))

from proto import perceiver_pb2, motioncap_pb2, segmentation_pb2
from streamlog.protoio import ProtoIO

from motioncap.geometry import (
    decode_frame_rgb,
    decode_depth,
    encode_heatmap,
    build_K,
    pose_components,
    Method,
)
from motioncap.raft_motion import (
    load_raft,
    compute_residual,
    compute_residual_and_flow_small,
    _to_tensor,
)
from motioncap.tracker import (
    decode_heatmap,
    run_tracker,
    Track,
    TrackPoint,
    RichDetection,
    TemporalFilter,
    detect_blobs,
)
from motioncap.segmentation_tracks import build_segmentation_trajectories

_frame_io = ProtoIO(perceiver_pb2.PerceiverDataFrame)
_motion_io = ProtoIO(motioncap_pb2.MotionCaptureResponse)
_seg_io = ProtoIO(segmentation_pb2.SegmentationResponse)

_config_path = Path(__file__).parent / "config.yaml"
with open(_config_path) as _f:
    _CONFIG = yaml.safe_load(_f)

# Distinct colours for up to 10 tracks (BGR)
_TRACK_COLORS = [
    (0, 200, 255),  # orange
    (50, 255, 50),  # green
    (255, 80, 80),  # blue
    (255, 50, 200),  # pink
    (255, 220, 0),  # cyan
    (100, 100, 255),  # salmon
    (0, 255, 200),  # yellow-green
    (200, 0, 255),  # violet
    (255, 180, 0),  # sky-blue
    (0, 128, 255),  # gold
]

_DEBUG_VIDEO_TRAJECTORY_MODES = {
    "ALL_TRAJECTORIES",
    "SEGMENTATION_ONLY",
    "RAFT_ONLY",
}


# ── Helpers ───────────────────────────────────────────────────────────────────


def _motion_path(rec: Path) -> Path:
    stem = (
        rec.name.removesuffix(".vis.pb") if rec.name.endswith(".vis.pb") else rec.stem
    )
    return rec.parent / (stem + ".motioncap.pb")


def _legacy_motion_path(rec: Path) -> Path:
    stem = (
        rec.name.removesuffix(".vis.pb") if rec.name.endswith(".vis.pb") else rec.stem
    )
    return rec.parent / (stem + ".motion.pb")


def _existing_motion_path(rec: Path) -> Path:
    primary = _motion_path(rec)
    legacy = _legacy_motion_path(rec)
    return primary if primary.exists() or not legacy.exists() else legacy


def _segmentation_path(rec: Path) -> Path:
    stem = (
        rec.name.removesuffix(".vis.pb") if rec.name.endswith(".vis.pb") else rec.stem
    )
    return rec.parent / (stem + ".segmentation.pb")


def _legacy_segmentation_path(rec: Path) -> Path:
    stem = (
        rec.name.removesuffix(".vis.pb") if rec.name.endswith(".vis.pb") else rec.stem
    )
    return rec.parent / (stem + ".seg.pb")


def _existing_segmentation_path(rec: Path) -> Path:
    primary = _segmentation_path(rec)
    legacy = _legacy_segmentation_path(rec)
    return primary if primary.exists() or not legacy.exists() else legacy


def _video_path(motion_pb: Path) -> Path:
    if motion_pb.name.endswith(".motioncap.pb"):
        stem = motion_pb.name.removesuffix(".motioncap.pb")
    else:
        stem = motion_pb.name.removesuffix(".motion.pb")
    return motion_pb.parent / (stem + ".motioncap.mp4")


def _color_for(track_id: int) -> tuple[int, int, int]:
    return _TRACK_COLORS[track_id % len(_TRACK_COLORS)]


def _segmentation_color_for(track_id: int) -> tuple[int, int, int]:
    return _TRACK_COLORS[(track_id + 5) % len(_TRACK_COLORS)]


def _is_summary_record(resp: motioncap_pb2.MotionCaptureResponse) -> bool:
    return bool(resp.tracks or resp.segmentation_trajectories or resp.total_frames)


def _debug_video_trajectory_mode(cfg: dict) -> str:
    mode = str(
        cfg.get("output", {}).get("debug_video_trajectory_mode", "ALL_TRAJECTORIES")
    ).upper()
    if mode not in _DEBUG_VIDEO_TRAJECTORY_MODES:
        allowed = ", ".join(sorted(_DEBUG_VIDEO_TRAJECTORY_MODES))
        raise ValueError(
            f"Invalid output.debug_video_trajectory_mode={mode!r}; "
            f"expected one of: {allowed}"
        )
    return mode


def _show_raft_trajectories(mode: str) -> bool:
    return mode in {"ALL_TRAJECTORIES", "RAFT_ONLY"}


def _show_segmentation_trajectories(mode: str) -> bool:
    return mode in {"ALL_TRAJECTORIES", "SEGMENTATION_ONLY"}


def _to_heatmap_u8(
    residual: np.ndarray,
    scale: float,
    res_thresh: float,
    blur_kernel: int,
) -> np.ndarray:
    """Noise-floor threshold + Gaussian blur + global-scale normalisation → uint8."""
    r = residual.copy()
    r[r < res_thresh] = 0.0
    if blur_kernel > 1:
        k = blur_kernel if blur_kernel % 2 == 1 else blur_kernel + 1
        r = cv2.GaussianBlur(r, (k, k), 0)
    if scale > 0:
        r = (r / scale * 255.0).clip(0, 255)
    return r.astype(np.uint8)


def _clear_cuda_cache(device) -> None:
    """Drop Python refs and release PyTorch's unused CUDA cache."""
    gc.collect()
    if getattr(device, "type", None) == "cuda":
        import torch

        torch.cuda.empty_cache()


def _resize_for_feature_encoder(
    rgb: np.ndarray,
    cfg: dict,
) -> tuple[np.ndarray, float, float]:
    """Resize RGB for RAFT's feature encoder and return input/original scales."""
    inference_height = cfg.get("raft", {}).get("inference_height")
    h, w = rgb.shape[:2]
    if inference_height is None or inference_height <= 0 or h <= inference_height:
        return rgb, 1.0, 1.0

    feat_h = int(inference_height)
    feat_w = max(8, int(round(w * feat_h / h)))
    resized = cv2.resize(rgb, (feat_w, feat_h), interpolation=cv2.INTER_AREA)
    return resized, feat_w / w, feat_h / h


# ── Proto track encoding / decoding ───────────────────────────────────────────


def _append_tracks_to_proto(
    out_tracks,
    tracks: list[Track],
    total_frames: int,
) -> None:
    """Append Track objects into a repeated MotionTrack proto field."""
    for track in tracks:
        detected = sum(1 for p in track.positions.values() if not p.interpolated)
        t = out_tracks.add()
        t.track_id = track.track_id
        t.detected_frames = detected
        t.total_positions = len(track.positions)
        t.presence_fraction = round(detected / max(total_frames, 1), 4)
        t.label = track.label
        for frame_idx in sorted(track.positions):
            p = track.positions[frame_idx]
            tp = t.positions.add()
            tp.frame_idx = frame_idx
            tp.cx = p.cx
            tp.cy = p.cy
            tp.area = p.area
            tp.interpolated = p.interpolated


def _encode_tracks_record(
    tracks: list[Track],
    segmentation_trajectories: list[Track],
    total_frames: int,
) -> motioncap_pb2.MotionCaptureResponse:
    """Build the trailing summary record that stores all track data."""
    resp = motioncap_pb2.MotionCaptureResponse()
    resp.total_frames = total_frames
    _append_tracks_to_proto(resp.tracks, tracks, total_frames)
    _append_tracks_to_proto(
        resp.segmentation_trajectories,
        segmentation_trajectories,
        total_frames,
    )
    return resp


def _decode_tracks_record(
    resp: motioncap_pb2.MotionCaptureResponse,
    field_name: str = "tracks",
) -> list[Track]:
    """Reconstruct Track objects from a proto summary record."""
    tracks: list[Track] = []
    for t in getattr(resp, field_name):
        track = Track(track_id=t.track_id)
        track.label = t.label
        for tp in t.positions:
            track.positions[tp.frame_idx] = TrackPoint(
                cx=tp.cx, cy=tp.cy, area=tp.area, interpolated=tp.interpolated
            )
        tracks.append(track)
    return tracks


# ── Load existing .motioncap.pb ────────────────────────────────────────────────


def _load_heatmaps_from_pb(
    motion_path: Path,
) -> tuple[list[np.ndarray], list[int], list[int]] | None:
    """
    Read heatmaps + frame metadata from an existing .motioncap.pb.
    Skips the trailing tracks-summary record.
    Returns (heatmaps, frame_ids, timestamps) or None if file is absent/empty.
    """
    if not motion_path.exists():
        return None
    responses = _motion_io.read_file(motion_path)
    if not responses:
        return None

    heatmaps: list[np.ndarray] = []
    frame_ids: list[int] = []
    timestamps: list[int] = []

    for resp in responses:
        if _is_summary_record(resp):  # skip the summary record
            continue
        if not resp.heatmap.heatmap_data:
            continue
        heatmaps.append(decode_heatmap(resp.heatmap.heatmap_data))
        frame_ids.append(resp.frame_identifier.frame_number)
        timestamps.append(resp.frame_identifier.timestamp_ns)

    return (heatmaps, frame_ids, timestamps) if heatmaps else None


def _load_tracks_from_pb(motion_path: Path) -> list[Track] | None:
    """
    Read the tracks-summary record from an existing .motioncap.pb.
    Returns None if no summary record exists.
    """
    if not motion_path.exists():
        return None
    for resp in _motion_io.read_file(motion_path):
        if _is_summary_record(resp):
            return _decode_tracks_record(resp)
    return None


def _load_segmentation_trajectories_from_pb(motion_path: Path) -> list[Track] | None:
    """
    Read segmentation trajectories from an existing .motioncap.pb summary record.
    Returns None if no summary record exists.
    """
    if not motion_path.exists():
        return None
    for resp in _motion_io.read_file(motion_path):
        if _is_summary_record(resp):
            return _decode_tracks_record(resp, field_name="segmentation_trajectories")
    return None


# ── Main ──────────────────────────────────────────────────────────────────────


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Unified RAFT motion capture + persistent object tracker"
    )
    parser.add_argument("recording", help="Path to .vis.pb recording")
    parser.add_argument(
        "--max-frames",
        type=int,
        default=0,
        help="Limit to first N frames for testing (0 = all)",
    )
    parser.add_argument(
        "--debug-render-video",
        dest="debug_render_video",
        action="store_true",
        help="Write a .motioncap.mp4 with heatmap overlay and track visualization",
    )
    args = parser.parse_args()

    cfg = _CONFIG
    pipe_cfg = cfg.get("pipeline", {})
    regen_raft = pipe_cfg.get("regenerate_raft", True)
    regen_tracking = pipe_cfg.get("regenerate_tracking", True)

    rec_path = Path(args.recording).resolve()
    if not rec_path.exists():
        print(f"File not found: {rec_path}")
        sys.exit(1)

    out_path = _motion_path(rec_path)
    read_path = _existing_motion_path(rec_path)

    # ── Step 1: RAFT heatmaps ─────────────────────────────────────────────────

    heatmaps: list[np.ndarray] = []
    frame_ids: list[int] = []
    timestamps: list[int] = []
    rgb_cache: list[np.ndarray] = []  # decoded RGB per frame (needed for video)
    max_raws: list[float] = []  # per-frame RAFT peak residual (for writing proto)
    methods: list[int] = []  # method_used per frame

    if not regen_raft:
        loaded = _load_heatmaps_from_pb(read_path)
        if loaded is not None:
            heatmaps, frame_ids, timestamps = loaded
            if args.max_frames > 0 and len(heatmaps) > args.max_frames:
                heatmaps = heatmaps[: args.max_frames]
                frame_ids = frame_ids[: args.max_frames]
                timestamps = timestamps[: args.max_frames]
            print(
                f"Loaded {len(heatmaps)} heatmaps from {read_path.name} "
                f"(regenerate_raft=false)"
            )
            # Populate dummy per-frame metadata (not needed for proto re-write)
            max_raws = [1.0] * len(heatmaps)
            methods = [Method.OPTICAL_FLOW] * len(heatmaps)

    if not heatmaps:
        # Run full RAFT pipeline
        variant = cfg["raft"]["variant"]
        model, device = load_raft(variant=variant)

        print(f"\nLoading {rec_path.name} …")
        t0 = time.time()
        frames = _frame_io.read_file(rec_path)
        if not frames:
            print("No frames found in recording")
            sys.exit(1)
        print(f"Loaded {len(frames)} frames in {time.time()-t0:.1f}s")

        if args.max_frames > 0 and len(frames) > args.max_frames:
            frames = frames[: args.max_frames]
            print(f"Limiting to first {args.max_frames} frames")

        # Camera intrinsics
        K_cache: list[np.ndarray] = []
        for f in frames:
            if build_K(f, K_cache) is not None:
                break
        K = K_cache[0] if K_cache else None
        if K is not None:
            print(
                f"Intrinsics: fx={K[0,0]:.1f}  fy={K[1,1]:.1f}  "
                f"cx={K[0,2]:.1f}  cy={K[1,2]:.1f}"
            )
        else:
            print("No camera intrinsics found — will use median-flow subtraction")

        ref_cfg = cfg["reference"]
        ref_strategy = ref_cfg["strategy"]
        rolling_gap = ref_cfg["rolling_gap"]
        time_delta_s = ref_cfg["time_delta_s"]

        time_index = None
        if ref_strategy == "time":
            ts = [f.frame_identifier.timestamp_ns for f in frames]
            time_index = sorted(zip(ts, range(len(frames))))

        raft_cfg = cfg["raft"]
        subtraction_mode = raft_cfg.get("subtraction_mode", "pose")
        feat_cfg = cfg.get("features", {})
        feat_enabled = feat_cfg.get("enabled", True)
        store_flow_small = feat_cfg.get("store_flow_small", True)

        raw_residuals: list[np.ndarray] = []
        flow_smalls: list[np.ndarray] = []  # (H//8, W//8, 2) float16 per frame

        print(
            f"\nPass 1 / 2 — computing RAFT residuals "
            f"(ref={ref_strategy}, subtraction={subtraction_mode}) …"
        )
        t_pass1 = time.time()

        with tqdm(total=len(frames), desc=f"RAFT-{variant}", unit="frame") as bar:
            for idx, frame_curr in enumerate(frames):
                if ref_strategy == "consecutive":
                    ref_idx = max(0, idx - 1)
                elif ref_strategy == "time":
                    target_ns = frame_curr.frame_identifier.timestamp_ns - int(
                        time_delta_s * 1e9
                    )
                    pos = bisect.bisect_left(time_index, (target_ns, -1))
                    ref_idx = time_index[pos - 1][1] if pos > 0 else 0
                elif ref_strategy == "rolling":
                    ref_idx = max(0, idx - rolling_gap)
                else:
                    ref_idx = 0

                rgb_curr = decode_frame_rgb(frame_curr)
                rgb_cache.append(rgb_curr)
                h, w = rgb_curr.shape[:2]

                fh, fw = h // 8, w // 8
                if ref_idx == idx:
                    raw_residuals.append(np.zeros((h, w), dtype=np.float16))
                    max_raws.append(0.0)
                    methods.append(Method.OPTICAL_FLOW)
                    if feat_enabled and store_flow_small:
                        flow_smalls.append(np.zeros((fh, fw, 2), dtype=np.float16))
                else:
                    frame_ref = frames[ref_idx]
                    rgb_ref = decode_frame_rgb(frame_ref)
                    R_ref, t_ref = pose_components(frame_ref)
                    R_curr, t_curr = pose_components(frame_curr)
                    depth = decode_depth(frame_curr, h, w)
                    try:
                        if feat_enabled and store_flow_small:
                            residual, max_raw, fs = compute_residual_and_flow_small(
                                rgb_ref,
                                rgb_curr,
                                model,
                                device,
                                cfg,
                                K=K,
                                R_ref=R_ref,
                                t_ref=t_ref,
                                R_curr=R_curr,
                                t_curr=t_curr,
                                depth=depth,
                                subtraction_mode=subtraction_mode,
                            )
                            flow_smalls.append(fs)
                        else:
                            residual, max_raw = compute_residual(
                                rgb_ref,
                                rgb_curr,
                                model,
                                device,
                                cfg,
                                K=K,
                                R_ref=R_ref,
                                t_ref=t_ref,
                                R_curr=R_curr,
                                t_curr=t_curr,
                                depth=depth,
                                subtraction_mode=subtraction_mode,
                            )
                        raw_residuals.append(residual.astype(np.float16))
                        max_raws.append(max_raw)
                        methods.append(Method.OPTICAL_FLOW)
                    except Exception as exc:
                        tqdm.write(f"Warning: frame {idx} failed: {exc}")
                        _clear_cuda_cache(device)
                        raw_residuals.append(np.zeros((h, w), dtype=np.float16))
                        max_raws.append(0.0)
                        methods.append(Method.OPTICAL_FLOW)
                        if feat_enabled and store_flow_small:
                            flow_smalls.append(np.zeros((fh, fw, 2), dtype=np.float16))
                bar.update(1)

        elapsed1 = time.time() - t_pass1
        fps1 = len(frames) / elapsed1 if elapsed1 > 0 else 0
        print(f"Pass 1 done in {elapsed1:.1f}s ({fps1:.2f} fps)")

        # Global normalisation
        norm_pct = cfg.get("normalization", {}).get("percentile", 99)
        non_zero = [m for m in max_raws if m > 0]
        global_scale = float(np.percentile(non_zero, norm_pct)) if non_zero else 1.0
        print(f"Global scale ({norm_pct}th percentile): {global_scale:.2f} px")

        # Pass 2: build uint8 heatmaps
        res_thresh = raft_cfg["residual_threshold_px"]
        blur_kernel = raft_cfg["blur_kernel"]
        print(f"\nPass 2 / 2 — normalising heatmaps …")
        for idx, frame_curr in enumerate(tqdm(frames, desc="Normalise", unit="frame")):
            residual = raw_residuals[idx].astype(np.float32)
            heatmaps.append(
                _to_heatmap_u8(residual, global_scale, res_thresh, blur_kernel)
            )
            frame_ids.append(frame_curr.frame_identifier.frame_number)
            timestamps.append(frame_curr.frame_identifier.timestamp_ns)

    total = len(heatmaps)

    # ── Step 3 (optional): Feature extraction + blob detection ────────────────
    # Runs the RAFT feature encoder on each frame's RGB, detects blobs from the
    # already-computed heatmaps, and pools per-blob 256-dim descriptors from the
    # feature map.  Also attaches RAFT flow vectors at blob centroids from the
    # downsampled flow stored in Pass 1.
    # Only runs when regen_raft=True and features.enabled=True.

    rich_detections: list[list[RichDetection]] | None = None

    if regen_raft and feat_enabled and rgb_cache and model is not None:
        import torch

        tc = cfg.get("tracking", {})
        filt_feat = TemporalFilter(
            window=tc.get("temporal_window", 5),
            sigma=tc.get("neighborhood_sigma", 15),
            threshold_norm=tc.get("motion_threshold_norm", 0.08),
            base_weight=tc.get("combined_base_weight", 0.25),
        )
        rich_detections = []
        print(f"\nPass 3 / 3 — feature extraction ({len(heatmaps)} frames) …")
        t_feat = time.time()

        with torch.no_grad():
            for fidx, (hm, rgb) in enumerate(
                tqdm(
                    zip(heatmaps, rgb_cache), total=total, desc="Features", unit="frame"
                )
            ):
                combined = filt_feat.update(hm)
                blobs = detect_blobs(
                    combined,
                    persistence_threshold=tc.get("persistence_threshold", 0.08),
                    close_size=tc.get("morphology_close_size", 15),
                    min_area=tc.get("min_blob_area", 150),
                    max_area=tc.get("max_blob_area", 50_000),
                )

                if not blobs:
                    rich_detections.append([])
                    continue

                # Feature encoder — use the same inference resolution as RAFT.
                rgb_feat, feat_scale_x, feat_scale_y = _resize_for_feature_encoder(
                    rgb, cfg
                )
                t_rgb, _, _ = _to_tensor(rgb_feat, device)
                fmap = model.feature_encoder(t_rgb)  # (1, 256, fH, fW)
                fmap_np = fmap.squeeze(0).cpu().numpy()  # (256, fH, fW)
                _, fH, fW = fmap_np.shape
                del t_rgb, fmap
                _clear_cuda_cache(device)

                # Flow small for this frame (if available)
                fs = flow_smalls[fidx] if flow_smalls else None

                frame_rich: list[RichDetection] = []
                for det in blobs:
                    r0, c0, r1, c1 = det.bbox
                    fr0 = max(0, int(r0 * feat_scale_y) // 8)
                    fc0 = max(0, int(c0 * feat_scale_x) // 8)
                    fr1 = min(fH, max(fr0 + 1, int((r1 + 1) * feat_scale_y) // 8 + 1))
                    fc1 = min(fW, max(fc0 + 1, int((c1 + 1) * feat_scale_x) // 8 + 1))
                    patch = fmap_np[:, fr0:fr1, fc0:fc1]  # (256, ph, pw)
                    desc = patch.mean(axis=(1, 2)).astype(np.float32)
                    norm = float(np.linalg.norm(desc))
                    if norm > 1e-8:
                        desc /= norm

                    flow_vec = None
                    if fs is not None:
                        cy, cx = det.centroid
                        fsH, fsW = fs.shape[:2]
                        fcy = min(int(cy) // 8, fsH - 1)
                        fcx = min(int(cx) // 8, fsW - 1)
                        # flow_small stores (dx, dy); flow_vec is (dy, dx) to match (cy,cx)
                        flow_vec = (float(fs[fcy, fcx, 1]), float(fs[fcy, fcx, 0]))

                    frame_rich.append(
                        RichDetection(
                            centroid=det.centroid,
                            area=det.area,
                            bbox=det.bbox,
                            flow_vec=flow_vec,
                            appearance=desc,
                        )
                    )
                rich_detections.append(frame_rich)

        elapsed_feat = time.time() - t_feat
        print(
            f"Feature extraction done in {elapsed_feat:.1f}s "
            f"({elapsed_feat/total*1000:.1f} ms/frame)"
        )

    # ── Step 4: Tracking ──────────────────────────────────────────────────────

    tracks: list[Track] | None = None
    segmentation_trajectories: list[Track] | None = None

    if not regen_tracking:
        tracks = _load_tracks_from_pb(read_path)
        if tracks is not None:
            print(
                f"\nLoaded {len(tracks)} track(s) from {read_path.name} "
                f"(regenerate_tracking=false)"
            )
        segmentation_trajectories = _load_segmentation_trajectories_from_pb(read_path)
        if segmentation_trajectories is not None:
            print(
                f"Loaded {len(segmentation_trajectories)} segmentation trajectory(s) "
                f"from {read_path.name} (regenerate_tracking=false)"
            )

    if tracks is None:
        mode_str = "feature-aware" if rich_detections is not None else "spatial-only"
        print(f"\nRunning {mode_str} tracker on {total} frames …")
        tracks = run_tracker(heatmaps, cfg, rich_detections=rich_detections)
        print(f"Found {len(tracks)} persistent track(s):")
        for t in tracks:
            detected = sum(1 for p in t.positions.values() if not p.interpolated)
            interp = sum(1 for p in t.positions.values() if p.interpolated)
            print(
                f"  Track {t.track_id}: {detected} detected + {interp} interpolated "
                f"({detected / total:.1%} presence)"
            )

    if segmentation_trajectories is None:
        segmentation_trajectories = []
        if cfg.get("segmentation_tracking", {}).get("enabled", True):
            seg_path = _existing_segmentation_path(rec_path)
            if seg_path.exists():
                print(f"\nBuilding segmentation trajectories from {seg_path.name} …")
                seg_records = _seg_io.read_file(seg_path)
                segmentation_trajectories = build_segmentation_trajectories(
                    seg_records,
                    heatmaps,
                    frame_ids,
                    timestamps,
                    cfg,
                )
                print(
                    f"Found {len(segmentation_trajectories)} segmentation trajectory(s)"
                )
            else:
                print(
                    "\nNo segmentation file found; skipping segmentation trajectories"
                )

    # ── Write .motioncap.pb (heatmaps + tracks summary) ───────────────────────

    print(f"\nWriting {out_path.name} …")
    if out_path.exists():
        out_path.unlink()

    batch: list[motioncap_pb2.MotionCaptureResponse] = []
    for idx in range(total):
        resp = motioncap_pb2.MotionCaptureResponse()
        # Re-use frame_identifier from recorded metadata when available
        resp.frame_identifier.frame_number = frame_ids[idx]
        resp.frame_identifier.timestamp_ns = timestamps[idx]
        resp.method_used = methods[idx] if idx < len(methods) else Method.OPTICAL_FLOW
        resp.stabilization_confidence = (
            1.0 if (idx < len(max_raws) and max_raws[idx] > 0) else 0.0
        )
        resp.heatmap.heatmap_data = encode_heatmap(heatmaps[idx])
        resp.heatmap.max_motion_raw = (
            float(max_raws[idx]) if idx < len(max_raws) else 0.0
        )
        batch.append(resp)
        if len(batch) >= 50:
            _motion_io.write_file(out_path, batch)
            batch.clear()
    if batch:
        _motion_io.write_file(out_path, batch)

    # Append the tracks summary record
    summary = _encode_tracks_record(tracks, segmentation_trajectories, total)
    _motion_io.write_file(out_path, [summary])

    print(
        f"→ {out_path.name}  "
        f"({total} frames, {len(tracks)} track(s), "
        f"{len(segmentation_trajectories)} segmentation trajectory(s))"
    )

    # ── Optional video ────────────────────────────────────────────────────────

    if not args.debug_render_video:
        print("\nDone.  Pass --debug-render-video to also write a visualization.")
        return

    # Need RGB frames for the overlay — load from .vis.pb if not already cached
    if not rgb_cache:
        if not rec_path.exists() or not rec_path.name.endswith(".vis.pb"):
            print("\nSkipping video: .vis.pb not available for RGB overlay.")
            return
        print(f"\nLoading RGB frames from {rec_path.name} for video …")
        vis_frames = _frame_io.read_file(rec_path)
        if args.max_frames > 0:
            vis_frames = vis_frames[: args.max_frames]
        for f in tqdm(vis_frames, desc="RGB decode", unit="frame"):
            rgb_cache.append(decode_frame_rgb(f))

    if len(rgb_cache) != total:
        print(
            f"\nWarning: RGB frame count ({len(rgb_cache)}) ≠ heatmap count ({total}). "
            f"Skipping video."
        )
        return

    h_img, w_img = heatmaps[0].shape
    vid_path = _video_path(out_path)
    codec = cv2.VideoWriter_fourcc(*cfg["output"]["video_codec"])
    alpha = cfg["output"]["overlay_alpha"]
    tail_len = cfg.get("tracking", {}).get("trajectory_tail_frames", 30)
    debug_trajectory_mode = _debug_video_trajectory_mode(cfg)
    draw_raft_trajectories = _show_raft_trajectories(debug_trajectory_mode)
    draw_segmentation_trajectories = _show_segmentation_trajectories(
        debug_trajectory_mode
    )

    if len(timestamps) >= 2 and timestamps[-1] > timestamps[0]:
        dur = (timestamps[-1] - timestamps[0]) / 1e9
        fps_v = (len(timestamps) - 1) / dur
    else:
        fps_v = 30.0

    writer = cv2.VideoWriter(str(vid_path), codec, fps_v, (w_img, h_img))

    # Build per-frame lookup for track positions
    frame_to_positions: dict[int, list] = {i: [] for i in range(total)}
    if draw_raft_trajectories:
        for track in tracks:
            for frame_idx, tp in track.positions.items():
                if 0 <= frame_idx < total:
                    frame_to_positions[frame_idx].append(
                        (track.track_id, tp.cx, tp.cy, tp.interpolated)
                    )
    frame_to_seg_positions: dict[int, list] = {i: [] for i in range(total)}
    if draw_segmentation_trajectories:
        for track in segmentation_trajectories:
            for frame_idx, tp in track.positions.items():
                if 0 <= frame_idx < total:
                    frame_to_seg_positions[frame_idx].append(
                        (track.track_id, tp.cx, tp.cy, tp.interpolated)
                    )

    print(
        f"\nWriting {vid_path.name}  "
        f"({fps_v:.1f} fps, trajectories={debug_trajectory_mode}) …"
    )
    for frame_idx, (hm, rgb_frame) in enumerate(
        tqdm(zip(heatmaps, rgb_cache), total=total, desc="Video", unit="frame")
    ):
        # Heatmap overlay on RGB
        rgb_bgr = cv2.cvtColor(rgb_frame, cv2.COLOR_RGB2BGR)
        heatmap_color = cv2.applyColorMap(hm, cv2.COLORMAP_JET)
        canvas = cv2.addWeighted(rgb_bgr, 1 - alpha, heatmap_color, alpha, 0)

        # Trajectory tails
        if draw_raft_trajectories:
            for track in tracks:
                color = _color_for(track.track_id)
                tail_start = max(0, frame_idx - tail_len)
                tail_pts = [
                    (
                        int(round(track.positions[fi].cx)),
                        int(round(track.positions[fi].cy)),
                    )
                    for fi in range(tail_start, frame_idx + 1)
                    if fi in track.positions
                ]
                for i in range(1, len(tail_pts)):
                    fade = (i / len(tail_pts)) ** 1.5
                    c = tuple(int(v * fade) for v in color)
                    cv2.line(canvas, tail_pts[i - 1], tail_pts[i], c, 1, cv2.LINE_AA)

        if draw_segmentation_trajectories:
            for track in segmentation_trajectories:
                color = _segmentation_color_for(track.track_id)
                tail_start = max(0, frame_idx - tail_len)
                tail_pts = [
                    (
                        int(round(track.positions[fi].cx)),
                        int(round(track.positions[fi].cy)),
                    )
                    for fi in range(tail_start, frame_idx + 1)
                    if fi in track.positions
                ]
                for i in range(1, len(tail_pts)):
                    fade = (i / len(tail_pts)) ** 1.5
                    c = tuple(int(v * fade) for v in color)
                    cv2.line(canvas, tail_pts[i - 1], tail_pts[i], c, 2, cv2.LINE_AA)

        # Current-frame blobs
        for tid, cx, cy, interp in frame_to_positions[frame_idx]:
            color = _color_for(tid)
            center = (int(round(cx)), int(round(cy)))
            if interp:
                cv2.drawMarker(
                    canvas,
                    center,
                    color,
                    markerType=cv2.MARKER_CROSS,
                    markerSize=10,
                    thickness=1,
                    line_type=cv2.LINE_AA,
                )
            else:
                cv2.circle(canvas, center, 6, color, 2, cv2.LINE_AA)
            cv2.putText(
                canvas,
                f"T{tid}",
                (center[0] + 8, center[1] - 8),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.45,
                color,
                1,
                cv2.LINE_AA,
            )

        for tid, cx, cy, interp in frame_to_seg_positions[frame_idx]:
            color = _segmentation_color_for(tid)
            center = (int(round(cx)), int(round(cy)))
            if interp:
                cv2.drawMarker(
                    canvas,
                    center,
                    color,
                    markerType=cv2.MARKER_TILTED_CROSS,
                    markerSize=12,
                    thickness=1,
                    line_type=cv2.LINE_AA,
                )
            else:
                cv2.drawMarker(
                    canvas,
                    center,
                    color,
                    markerType=cv2.MARKER_DIAMOND,
                    markerSize=14,
                    thickness=2,
                    line_type=cv2.LINE_AA,
                )
            cv2.putText(
                canvas,
                f"S{tid}",
                (center[0] + 8, center[1] + 14),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.45,
                color,
                1,
                cv2.LINE_AA,
            )

        # Frame counter
        cv2.putText(
            canvas,
            f"frame {frame_idx}",
            (8, 22),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.5,
            (200, 200, 200),
            1,
            cv2.LINE_AA,
        )
        writer.write(canvas)

    writer.release()
    print(f"→ {vid_path.name}")
    print("\nDone.")


if __name__ == "__main__":
    main()
