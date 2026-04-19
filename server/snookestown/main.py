#!/usr/bin/env python3
"""Snookestown: estimate per-frame snooker ball positions on the table.

Input
-----
    recordings/<name>/<name>.vis.pb     (PerceiverDataFrame)
    recordings/<name>/<name>.seg.pb     (SegmentationResponse with SAM3 labels
                                          "table" / "ball" / "cue" / "person")

Output
------
    recordings/<name>/<name>.snook.pb   (length-delimited SnookerResponse —
                                          one per frame + a trailing summary
                                          record containing persistent ball
                                          tracks and canonical pockets)
    recordings/<name>/<name>.snook.mp4  (top-down table visualisation)
    recordings/<name>/<name>.snook/debug/
        table_pose.json
        detections.json
        tracks.json
        overlay/frame_XXXXXX.png

Usage
-----
    cd server
    uv run python snookestown/main.py ../recordings/<name>/<name>.vis.pb
    uv run python snookestown/main.py ../recordings/<name>/<name>.vis.pb --max-frames 200
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
import time
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

from proto import snookestown_pb2
from streamlog.protoio import ProtoIO

from motioncap.geometry import decode_frame_rgb

from snookestown.ball_detect import BallDetection, detect_balls
from snookestown.loader import LoadedRecording, load_recording
from snookestown.pockets import compute_hull_quad
from snookestown.orientation_lock import lock_orientation
from snookestown.render import render_debug_overlay, render_segoverlay_mp4, render_topdown_mp4
from snookestown.table_pose import TablePoseResult, canonical_pockets_mm, estimate_table_pose
from snookestown.tracker import Track, build_tracks
from snookestown import colors as snook_colors

logging.basicConfig(level=logging.INFO, format="%(levelname)s  %(message)s")
log = logging.getLogger(__name__)

_snook_io = ProtoIO(snookestown_pb2.SnookerResponse)

_config_path = Path(__file__).parent / "config.yaml"
with open(_config_path) as _f:
    _CONFIG = yaml.safe_load(_f)


# ── Path helpers ──────────────────────────────────────────────────────────────

def _stem(vis_path: Path) -> str:
    name = vis_path.name
    return name.removesuffix(".vis.pb") if name.endswith(".vis.pb") else vis_path.stem


def _snook_pb_path(vis_path: Path) -> Path:
    return vis_path.parent / (_stem(vis_path) + ".snook.pb")


def _snook_mp4_path(vis_path: Path) -> Path:
    return vis_path.parent / (_stem(vis_path) + ".snook.mp4")


def _snook_segoverlay_mp4_path(vis_path: Path) -> Path:
    return vis_path.parent / (_stem(vis_path) + ".snook.segoverlay.mp4")


def _snook_workspace(vis_path: Path) -> Path:
    return vis_path.parent / (_stem(vis_path) + ".snook")


# ── Argparse ──────────────────────────────────────────────────────────────────

def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Snooker ball tracking in table 2D")
    p.add_argument("recording", type=Path, help="Path to .vis.pb recording")
    p.add_argument("--max-frames", type=int, default=None)
    p.add_argument("--sample-every", type=int, default=None)
    p.add_argument("--no-render", action="store_true",
                   help="Skip MP4 rendering")
    p.add_argument("--no-debug", action="store_true",
                   help="Skip debug JSON + overlay dumps")
    return p.parse_args()


# ── Stage orchestration ──────────────────────────────────────────────────────

def _align_hull_to_prev(hull: np.ndarray, prev: np.ndarray) -> np.ndarray:
    """Cyclically rotate `hull` corners to best match `prev` (min total distance)."""
    n = len(hull)
    best_roll, best_dist = 0, float("inf")
    for roll in range(n):
        rolled = np.roll(hull, roll, axis=0)
        dist = float(np.sum(np.linalg.norm(rolled - prev, axis=1)))
        if dist < best_dist:
            best_dist = dist
            best_roll = roll
    return np.roll(hull, best_roll, axis=0)


def _run_stage_table_pose(
    rec: LoadedRecording,
    cfg: dict,
) -> list[TablePoseResult]:
    results: list[TablePoseResult] = []
    prev_hull: np.ndarray | None = None       # smoothed hull from previous frame
    hull_alpha = 0.75                         # weight for current-frame hull in EMA

    for idx, frame in enumerate(tqdm(rec.frames, desc="Table pose", unit="frame")):
        fm = rec.masks_by_index.get(idx)

        # ── Hull quad: computed from raw table pixels, smoothed across frames ──
        raw_table_mask = fm.largest_table_mask_raw() if fm is not None else None
        if raw_table_mask is not None:
            raw_hull = compute_hull_quad(raw_table_mask)
        else:
            raw_hull = None

        if raw_hull is not None and raw_hull.shape[0] == 4:
            if prev_hull is not None:
                aligned = _align_hull_to_prev(raw_hull, prev_hull)
                smoothed_hull = (hull_alpha * aligned + (1 - hull_alpha) * prev_hull).astype(np.float32)
            else:
                smoothed_hull = raw_hull
            prev_hull = smoothed_hull
        else:
            # No hull this frame — carry previous (table might be fully occluded).
            smoothed_hull = prev_hull

        # ── Any-mask union: pixels with any label are "valid" inside the projection ──
        any_mask: np.ndarray | None = None
        if fm is not None:
            for group in (fm.table, fm.balls, fm.cues, fm.persons):
                for _, _, m in group:
                    if any_mask is None:
                        any_mask = m.copy()
                    elif m.shape == any_mask.shape:
                        any_mask |= m

        table_mask = fm.largest_table_mask() if fm is not None else None
        if table_mask is None:
            results.append(TablePoseResult(
                H_img_to_table=None,
                method=snookestown_pb2.SnookerResponse.TablePose.Method.FAILED,
                quality=0.0, pockets=[], assigned_mm=[], cushions=[],
                hull_quad=smoothed_hull,
            ))
            continue

        res = estimate_table_pose(
            table_mask, cfg,
            raw_table_mask=raw_table_mask,
            hull_quad_override=smoothed_hull,
            any_mask=any_mask,
        )
        results.append(res)
    return results


def _run_stage_detections(
    rec: LoadedRecording,
    poses: list[TablePoseResult],
    cfg: dict,
) -> tuple[list[list[BallDetection]], list[np.ndarray]]:
    per_frame_dets: list[list[BallDetection]] = []
    rgb_cache: list[np.ndarray] = []
    for idx, frame in enumerate(tqdm(rec.frames, desc="Detect balls", unit="frame")):
        rgb = decode_frame_rgb(frame)
        rgb_cache.append(rgb)
        fm = rec.masks_by_index.get(idx)
        if fm is None:
            per_frame_dets.append([])
            continue
        per_frame_dets.append(
            detect_balls(rgb, fm, poses[idx].H_img_to_table, cfg)
        )
    return per_frame_dets, rgb_cache


# ── Proto writing ────────────────────────────────────────────────────────────

def _write_snook_pb(
    out_path: Path,
    rec: LoadedRecording,
    poses: list[TablePoseResult],
    per_frame_dets: list[list[BallDetection]],
    tracks: list[Track],
    cfg: dict,
) -> None:
    if out_path.exists():
        out_path.unlink()

    # Index: for each frame, which tracks have a position there, and what is
    # that track's observation at that frame.
    frame_to_points: dict[int, list[tuple[Track, int]]] = {}
    for t in tracks:
        for f in t.positions:
            frame_to_points.setdefault(f, []).append((t, f))

    batch: list[snookestown_pb2.SnookerResponse] = []
    for idx, frame in enumerate(rec.frames):
        resp = snookestown_pb2.SnookerResponse()
        resp.frame_identifier.timestamp_ns = frame.frame_identifier.timestamp_ns
        resp.frame_identifier.frame_number = frame.frame_identifier.frame_number
        resp.frame_identifier.device_id = frame.frame_identifier.device_id

        pose = poses[idx]
        tp = resp.table_pose
        tp.method = pose.method
        tp.quality = pose.quality
        if pose.H_img_to_table is not None:
            tp.homography_img_to_table_mm.extend(
                float(v) for v in pose.H_img_to_table.flatten()
            )
        for i, pk in enumerate(pose.pockets):
            dp = tp.detected_pockets.add()
            dp.x_img = pk.x_img
            dp.y_img = pk.y_img
            dp.kind = pk.kind
            if i < len(pose.assigned_mm):
                dp.assigned_x_mm = pose.assigned_mm[i][0]
                dp.assigned_y_mm = pose.assigned_mm[i][1]
        for c in pose.cushions:
            tp.detected_edges_img.extend(
                [float(c.p0[0]), float(c.p0[1]), float(c.p1[0]), float(c.p1[1])]
            )

        for (track, f) in frame_to_points.get(idx, []):
            p = track.positions[f]
            ob = resp.balls.add()
            ob.track_id = track.track_id
            ob.x_mm = p.x_mm
            ob.y_mm = p.y_mm
            ob.confidence = track.color_confidence
            ob.moving = p.moving
            ob.interpolated = p.interpolated
            ob.mask_object_id = p.mask_object_id

        batch.append(resp)
        if len(batch) >= 50:
            _snook_io.write_file(out_path, batch)
            batch.clear()
    if batch:
        _snook_io.write_file(out_path, batch)

    # Summary record.
    W_mm = float(cfg["table"]["width_mm"])
    H_mm = float(cfg["table"]["height_mm"])
    summary = snookestown_pb2.SnookerResponse()
    summary.total_frames = len(rec.frames)
    summary.table_width_mm = W_mm
    summary.table_height_mm = H_mm

    for x, y, kind in canonical_pockets_mm(W_mm, H_mm):
        cp = summary.canonical_pockets.add()
        cp.x_mm = x
        cp.y_mm = y
        cp.kind = kind

    for t in tracks:
        tr = summary.tracks.add()
        tr.track_id = t.track_id
        tr.color = t.color
        tr.color_confidence = t.color_confidence
        tr.first_frame_idx = t.first_frame
        tr.last_frame_idx = t.last_frame
        tr.observed_frames = t.observed_frames
        tr.mean_rgb.extend(float(v) for v in t.mean_rgb)

    _snook_io.write_file(out_path, [summary])


# ── Debug dumps ──────────────────────────────────────────────────────────────

def _dump_debug(
    workspace: Path,
    poses: list[TablePoseResult],
    per_frame_dets: list[list[BallDetection]],
    tracks: list[Track],
    rgb_cache: list[np.ndarray],
    cfg: dict,
) -> None:
    workspace.mkdir(parents=True, exist_ok=True)
    dbg = workspace / "debug"
    dbg.mkdir(exist_ok=True)

    pose_rows = []
    for idx, p in enumerate(poses):
        pose_rows.append({
            "frame_idx": idx,
            "method": int(p.method),
            "quality": float(p.quality),
            "n_pockets": len(p.pockets),
            "n_cushions": len(p.cushions),
            "pockets": [
                {"x_img": pk.x_img, "y_img": pk.y_img,
                 "kind": int(pk.kind), "depth_px": pk.depth_px}
                for pk in p.pockets
            ],
            "assigned_mm": [list(mm) for mm in p.assigned_mm],
        })
    (dbg / "table_pose.json").write_text(json.dumps(pose_rows, indent=2))

    det_rows = []
    for idx, dets in enumerate(per_frame_dets):
        det_rows.append({
            "frame_idx": idx,
            "detections": [
                {"x_mm": d.x_mm, "y_mm": d.y_mm,
                 "x_img": d.x_img, "y_img": d.y_img,
                 "area_px": d.area_px,
                 "mean_rgb": list(d.mean_rgb),
                 "mask_object_id": d.mask_object_id}
                for d in dets
            ],
        })
    (dbg / "detections.json").write_text(json.dumps(det_rows, indent=2))

    track_rows = []
    for t in tracks:
        track_rows.append({
            "track_id": t.track_id,
            "color": int(t.color),
            "color_name": snook_colors.color_name(t.color),
            "color_confidence": t.color_confidence,
            "first_frame": t.first_frame,
            "last_frame": t.last_frame,
            "observed_frames": t.observed_frames,
            "mean_rgb": list(t.mean_rgb),
            "positions": [
                {"frame_idx": f,
                 "x_mm": p.x_mm, "y_mm": p.y_mm,
                 "moving": p.moving, "interpolated": p.interpolated}
                for f, p in sorted(t.positions.items())
            ],
        })
    (dbg / "tracks.json").write_text(json.dumps(track_rows, indent=2))

    # Overlay images (sampled).
    overlay_dir = dbg / "overlay"
    overlay_dir.mkdir(exist_ok=True)
    every = max(1, int(cfg["pipeline"]["debug_overlay_every_n"]))

    # Map mask_object_id → Track for current-frame annotation.
    # (Track ids come from detection order, not mask_object_id, so build the
    # mapping by (frame, mask_object_id) lookup.)
    obj_to_track_per_frame: dict[int, dict[int, Track]] = {}
    for t in tracks:
        for f, p in t.positions.items():
            if p.interpolated:
                continue
            obj_to_track_per_frame.setdefault(f, {})[p.mask_object_id] = t

    for idx in range(0, len(rgb_cache), every):
        overlay = render_debug_overlay(
            rgb_cache[idx], poses[idx], per_frame_dets[idx],
            obj_to_track_per_frame.get(idx, {}), cfg,
        )
        cv2.imwrite(str(overlay_dir / f"frame_{idx:06d}.png"), overlay)


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    args = parse_args()
    cfg = _CONFIG

    vis_path = args.recording.resolve()
    if not vis_path.exists():
        log.error("Recording not found: %s", vis_path)
        sys.exit(1)

    if args.sample_every is not None:
        cfg["extraction"]["sample_every"] = args.sample_every
    if args.max_frames is not None:
        cfg["extraction"]["max_frames"] = args.max_frames

    sample_every = int(cfg["extraction"]["sample_every"])
    max_frames = int(cfg["extraction"]["max_frames"]) or 0

    t_start = time.time()

    log.info("Loading %s", vis_path.name)
    rec = load_recording(
        vis_path,
        max_frames=max_frames,
        sample_every=sample_every,
    )
    log.info(
        "Loaded %d frames; annotations for %d frames from %s",
        len(rec.frames), len(rec.masks_by_index), rec.seg_path.name,
    )
    if len(rec.masks_by_index) == 0:
        log.error(
            "No recognised masks found in %s. Ensure SAM3 ran with labels "
            "'table ball cue person'.", rec.seg_path,
        )
        sys.exit(1)

    log.info("Stage 2: per-frame table pose")
    poses = _run_stage_table_pose(rec, cfg)
    poses = lock_orientation(poses)
    n_ok = sum(1 for p in poses if p.H_img_to_table is not None)
    log.info("Table pose solved for %d / %d frames", n_ok, len(poses))

    log.info("Stage 3: per-frame ball detection")
    per_frame_dets, rgb_cache = _run_stage_detections(rec, poses, cfg)
    n_dets = sum(len(d) for d in per_frame_dets)
    log.info("Total ball detections: %d", n_dets)

    log.info("Stage 4: building tracks")
    timestamps_ns = [f.frame_identifier.timestamp_ns for f in rec.frames]
    tracks = build_tracks(per_frame_dets, timestamps_ns, cfg)
    log.info("Found %d tracks", len(tracks))
    for t in tracks:
        log.info(
            "  Track %2d  color=%-8s conf=%.2f  obs=%d  frames [%d, %d]",
            t.track_id, snook_colors.color_name(t.color),
            t.color_confidence, t.observed_frames, t.first_frame, t.last_frame,
        )

    # Output.
    out_pb = _snook_pb_path(vis_path)
    log.info("Writing %s", out_pb.name)
    _write_snook_pb(out_pb, rec, poses, per_frame_dets, tracks, cfg)

    if not args.no_debug and cfg["pipeline"]["debug_dumps"]:
        ws = _snook_workspace(vis_path)
        log.info("Writing debug dumps to %s", ws)
        _dump_debug(ws, poses, per_frame_dets, tracks, rgb_cache, cfg)

    if not args.no_render and cfg["pipeline"]["render_mp4"]:
        out_mp4 = _snook_mp4_path(vis_path)
        log.info("Rendering top-down MP4 → %s", out_mp4.name)
        render_topdown_mp4(tracks, len(rec.frames), timestamps_ns, out_mp4, cfg)

        seg_mp4 = _snook_segoverlay_mp4_path(vis_path)
        log.info("Rendering seg-overlay MP4 → %s", seg_mp4.name)
        render_segoverlay_mp4(
            rec.masks_by_index, len(rec.frames), poses, seg_mp4, timestamps_ns, cfg,
        )

    elapsed = time.time() - t_start
    log.info("Done in %.1fs", elapsed)


if __name__ == "__main__":
    main()
