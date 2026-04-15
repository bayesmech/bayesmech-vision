"""Build persistent ball tracks from per-frame detections in table-mm.

Pipeline:
  1. Intra-frame dedupe (merge detections within dedupe_radius_mm).
  2. Hungarian nearest-neighbour linking across adjacent frames, gated by
     max_ball_speed * dt + ball_radius.
  3. Short-gap interpolation (linear).
  4. Per-track colour classification using the hardcoded palette in colors.py.
  5. Motion flagging per-frame-per-track via rolling median + static threshold;
     static stretches snap to the median so frame-to-frame table-pose jitter
     doesn't manifest as ball movement.
"""
from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from scipy.optimize import linear_sum_assignment

_server_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_server_root))

from snookestown.ball_detect import BallDetection
from snookestown import colors


@dataclass
class TrackPoint:
    x_mm: float
    y_mm: float
    area_px: int
    mean_rgb: tuple[float, float, float]
    mask_object_id: int
    moving: bool = False
    interpolated: bool = False


@dataclass
class Track:
    track_id: int
    positions: dict[int, TrackPoint] = field(default_factory=dict)
    color: int = 0              # Color enum value
    color_confidence: float = 0.0
    mean_rgb: tuple[float, float, float] = (0.0, 0.0, 0.0)

    @property
    def first_frame(self) -> int:
        return min(self.positions) if self.positions else 0

    @property
    def last_frame(self) -> int:
        return max(self.positions) if self.positions else 0

    @property
    def observed_frames(self) -> int:
        return sum(1 for p in self.positions.values() if not p.interpolated)


# ── Dedupe ────────────────────────────────────────────────────────────────────

def _dedupe(dets: list[BallDetection], radius_mm: float) -> list[BallDetection]:
    """Merge detections within `radius_mm`; keep the larger-area one."""
    if not dets:
        return []
    dets = sorted(dets, key=lambda d: -d.area_px)
    kept: list[BallDetection] = []
    r2 = radius_mm * radius_mm
    for d in dets:
        if any((d.x_mm - k.x_mm) ** 2 + (d.y_mm - k.y_mm) ** 2 < r2 for k in kept):
            continue
        kept.append(d)
    return kept


# ── Linking ───────────────────────────────────────────────────────────────────

def _link(
    per_frame_dets: list[list[BallDetection]],
    timestamps_ns: list[int],
    cfg: dict,
) -> list[Track]:
    tcfg = cfg["tracker"]
    max_speed = float(tcfg["max_ball_speed_mps"])
    base_gate = float(tcfg["gating_radius_base_mm"])
    max_gap = int(tcfg["max_gap_frames"])

    tracks: list[Track] = []
    # active[i] = (track_index, last_frame_with_obs, last_x_mm, last_y_mm)
    active: list[tuple[int, int, float, float]] = []
    next_id = 1

    for frame_idx, dets in enumerate(per_frame_dets):
        if not dets:
            # Age active tracks
            active = [a for a in active if frame_idx - a[1] <= max_gap]
            continue

        # Determine dt to last frame.
        if frame_idx == 0 or not active:
            dt_s = 0.033
        else:
            prev_ts = timestamps_ns[max(0, frame_idx - 1)]
            curr_ts = timestamps_ns[frame_idx]
            dt_s = max(0.001, (curr_ts - prev_ts) / 1e9)
        gate_mm = base_gate + max_speed * 1000.0 * dt_s  # convert m to mm

        if not active:
            # Seed tracks from all detections.
            for d in dets:
                t = Track(track_id=next_id)
                next_id += 1
                t.positions[frame_idx] = TrackPoint(
                    x_mm=d.x_mm, y_mm=d.y_mm, area_px=d.area_px,
                    mean_rgb=d.mean_rgb, mask_object_id=d.mask_object_id,
                )
                tracks.append(t)
                active.append((len(tracks) - 1, frame_idx, d.x_mm, d.y_mm))
            continue

        # Build cost matrix (active × detections).
        cost = np.full((len(active), len(dets)), 1e9, dtype=np.float64)
        for i, (_ti, _lf, lx, ly) in enumerate(active):
            for j, d in enumerate(dets):
                dx = d.x_mm - lx
                dy = d.y_mm - ly
                dist = float(np.hypot(dx, dy))
                # Age penalty: old tracks with big dt get more slack.
                lf = active[i][1]
                eff_gate = gate_mm + max_speed * 1000.0 * max(0, frame_idx - lf - 1) * dt_s
                if dist <= eff_gate:
                    cost[i, j] = dist

        rows, cols = linear_sum_assignment(cost)
        matched_dets: set[int] = set()
        matched_active: set[int] = set()
        for i, j in zip(rows, cols):
            if cost[i, j] >= 1e9:
                continue
            ti, _lf, _lx, _ly = active[i]
            d = dets[j]
            tracks[ti].positions[frame_idx] = TrackPoint(
                x_mm=d.x_mm, y_mm=d.y_mm, area_px=d.area_px,
                mean_rgb=d.mean_rgb, mask_object_id=d.mask_object_id,
            )
            active[i] = (ti, frame_idx, d.x_mm, d.y_mm)
            matched_active.add(i)
            matched_dets.add(j)

        # Unmatched detections → new tracks.
        for j, d in enumerate(dets):
            if j in matched_dets:
                continue
            t = Track(track_id=next_id)
            next_id += 1
            t.positions[frame_idx] = TrackPoint(
                x_mm=d.x_mm, y_mm=d.y_mm, area_px=d.area_px,
                mean_rgb=d.mean_rgb, mask_object_id=d.mask_object_id,
            )
            tracks.append(t)
            active.append((len(tracks) - 1, frame_idx, d.x_mm, d.y_mm))

        # Prune tracks that have been unseen too long.
        active = [a for a in active if frame_idx - a[1] <= max_gap]

    return tracks


# ── Interpolation ────────────────────────────────────────────────────────────

def _interpolate(tracks: list[Track], max_gap: int) -> None:
    """Linearly interpolate gaps of <= max_gap frames within each track."""
    for t in tracks:
        frames = sorted(t.positions)
        for a, b in zip(frames, frames[1:]):
            if b - a <= 1:
                continue
            if b - a > max_gap + 1:
                continue
            pa = t.positions[a]
            pb = t.positions[b]
            for k in range(a + 1, b):
                alpha = (k - a) / (b - a)
                x = pa.x_mm * (1 - alpha) + pb.x_mm * alpha
                y = pa.y_mm * (1 - alpha) + pb.y_mm * alpha
                t.positions[k] = TrackPoint(
                    x_mm=x, y_mm=y,
                    area_px=int((pa.area_px + pb.area_px) / 2),
                    mean_rgb=pa.mean_rgb,
                    mask_object_id=pa.mask_object_id,
                    interpolated=True,
                )


# ── Motion flagging ───────────────────────────────────────────────────────────

def _flag_motion(tracks: list[Track], cfg: dict) -> None:
    tcfg = cfg["tracker"]
    win = int(tcfg["rolling_median_half_window"])
    thresh = float(tcfg["static_threshold_mm"])
    for t in tracks:
        frames = sorted(t.positions)
        if not frames:
            continue
        xs = np.array([t.positions[f].x_mm for f in frames])
        ys = np.array([t.positions[f].y_mm for f in frames])
        for i, f in enumerate(frames):
            lo = max(0, i - win)
            hi = min(len(frames), i + win + 1)
            mx = float(np.median(xs[lo:hi]))
            my = float(np.median(ys[lo:hi]))
            dist = float(np.hypot(xs[i] - mx, ys[i] - my))
            pt = t.positions[f]
            if dist > thresh:
                # Position differs from rolling median by more than the static
                # threshold → genuine motion.
                pt.moving = True
            else:
                # Snap to median to kill table-pose jitter.
                pt.x_mm = mx
                pt.y_mm = my
                pt.moving = False


# ── Colour classification ─────────────────────────────────────────────────────

def _classify_colors(tracks: list[Track], cfg: dict) -> None:
    for t in tracks:
        if not t.positions:
            continue
        rgbs = np.array(
            [p.mean_rgb for p in t.positions.values() if not p.interpolated],
            dtype=np.float32,
        )
        if len(rgbs) == 0:
            continue
        mean_rgb = tuple(float(v) for v in rgbs.mean(axis=0))
        t.mean_rgb = mean_rgb
        color_id, conf = colors.classify(np.array(mean_rgb))
        if conf < float(cfg["colors"]["min_color_confidence"]):
            # Fallback: snooker has 15 reds (~65% of balls on the table), so
            # unclassified balls are most likely reds.
            t.color = colors.Color.RED
        else:
            t.color = color_id
        t.color_confidence = conf


# ── Entry point ───────────────────────────────────────────────────────────────

def build_tracks(
    per_frame_dets: list[list[BallDetection]],
    timestamps_ns: list[int],
    cfg: dict,
) -> list[Track]:
    bd_cfg = cfg["ball_detect"]
    dedupe_r = float(bd_cfg["dedupe_radius_mm"])
    per_frame_deduped = [_dedupe(dets, dedupe_r) for dets in per_frame_dets]

    tracks = _link(per_frame_deduped, timestamps_ns, cfg)
    _interpolate(tracks, int(cfg["tracker"]["max_gap_frames"]))
    _flag_motion(tracks, cfg)
    _classify_colors(tracks, cfg)
    return tracks
