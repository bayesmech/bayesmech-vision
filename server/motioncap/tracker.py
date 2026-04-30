"""
Persistent object tracker for motion heatmap sequences.

Algorithm overview
------------------
1. Temporal consistency filter — for each frame, look back over the past W
   frames.  Blur each past heatmap spatially so that nearby pixels count as
   evidence.  The fraction of past frames where the blurred value exceeded a
   threshold gives a per-pixel "temporal support" score T ∈ [0,1].
   Combined map: C = h_current × (β + (1−β)·T)
   Sporadic noise (T≈0) is suppressed to ≤25% of its raw value, while a
   persistently-moving object (T≈1) keeps its full value.

2. Blob detection — threshold C, morphological close, label connected
   components, filter by area, extract centroid + bbox per blob.

3. Feature-aware tracker — associates blobs to existing tracks using a
   combined spatial + appearance cost.  Tracks survive up to max_gap_frames
   missing detections.  When a track dies its mean feature descriptor is
   stored in a lost-banks list, enabling re-identification of the same object
   when it reappears after a long gap (e.g. pendulum stationary at extremes).

4. Persistence filter — discard tracks that cover fewer than
   min_presence_fraction × total_frames.

5. Gap interpolation — linearly interpolate centroid positions for frames
   where the track was not detected.
"""

import struct
import zlib
from collections import deque
from dataclasses import dataclass, field

import cv2
import numpy as np
import scipy.ndimage as ndi

# ── Data classes ──────────────────────────────────────────────────────────────


@dataclass
class RichDetection:
    """A single blob detection, optionally augmented with RAFT features."""

    centroid: tuple[float, float]  # (cy, cx) — row, col
    area: int
    bbox: tuple[int, int, int, int]  # (r0, c0, r1, c1)
    # Optional fields — populated during the feature-extraction pass (Pass 3).
    # None when running without RAFT (regenerate_raft: false).
    flow_vec: tuple[float, float] | None = None  # (dy, dx) raw RAFT flow at centroid
    appearance: np.ndarray | None = None  # (256,) float32, L2-normalised


# Backward-compat alias — external callers using Detection still work.
Detection = RichDetection


@dataclass
class TrackPoint:
    cx: float
    cy: float
    area: int
    interpolated: bool = False


@dataclass
class Track:
    track_id: int
    label: str = ""
    positions: dict[int, TrackPoint] = field(
        default_factory=dict
    )  # frame_idx → TrackPoint

    @property
    def presence_fraction(self) -> float:
        return 0.0  # computed externally after total_frames is known

    def last_centroid(self) -> tuple[float, float] | None:
        if not self.positions:
            return None
        last = max(self.positions)
        p = self.positions[last]
        return (p.cy, p.cx)


# ── Heatmap decoding ──────────────────────────────────────────────────────────


def decode_heatmap(data: bytes) -> np.ndarray:
    """Decode [h:u32le][w:u32le][zlib(uint8 array)] → uint8 ndarray (H, W)."""
    h, w = struct.unpack("<II", data[:8])
    raw = np.frombuffer(zlib.decompress(data[8:]), dtype=np.uint8)
    return raw[: h * w].reshape(h, w)


# ── Temporal consistency filter ───────────────────────────────────────────────


class TemporalFilter:
    """Maintains a sliding window of past heatmaps and computes per-pixel
    temporal support for the current frame."""

    def __init__(
        self,
        window: int = 5,
        sigma: float = 15.0,
        threshold_norm: float = 0.08,
        base_weight: float = 0.25,
    ) -> None:
        self.window = window
        self.sigma = sigma
        self.threshold_norm = threshold_norm
        self.base_weight = base_weight
        self._past: deque[np.ndarray] = deque(maxlen=window)

    def update(self, heatmap_u8: np.ndarray) -> np.ndarray:
        """Push new heatmap, return combined motion map (float32, 0–1)."""
        h_norm = heatmap_u8.astype(np.float32) / 255.0

        if self._past:
            k = int(self.sigma * 3) * 2 + 1  # 3σ rule, must be odd
            past_binary = []
            for prev in self._past:
                blurred = cv2.GaussianBlur(
                    prev.astype(np.float32) / 255.0, (k, k), self.sigma
                )
                past_binary.append((blurred > self.threshold_norm).astype(np.float32))
            temporal_support = np.mean(past_binary, axis=0)
        else:
            temporal_support = np.zeros_like(h_norm)

        combined = h_norm * (
            self.base_weight + (1.0 - self.base_weight) * temporal_support
        )
        self._past.append(heatmap_u8)
        return combined


# ── Blob detector ─────────────────────────────────────────────────────────────


def detect_blobs(
    combined: np.ndarray,
    persistence_threshold: float = 0.08,
    close_size: int = 15,
    min_area: int = 150,
    max_area: int = 50_000,
) -> list[RichDetection]:
    """Threshold → morphological close → label → filter by area → centroids."""
    binary = (combined > persistence_threshold).astype(np.uint8)
    if binary.sum() == 0:
        return []

    if close_size > 1:
        k = int(close_size) | 1
        kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k, k))
        binary = cv2.morphologyEx(binary, cv2.MORPH_CLOSE, kernel)

    labeled, n = ndi.label(binary)
    if n == 0:
        return []

    detections: list[RichDetection] = []
    for label_id in range(1, n + 1):
        mask = labeled == label_id
        area = int(mask.sum())
        if area < min_area or area > max_area:
            continue
        cy, cx = ndi.center_of_mass(mask)
        rows, cols = np.where(mask)
        r0, r1 = int(rows.min()), int(rows.max())
        c0, c1 = int(cols.min()), int(cols.max())
        detections.append(
            RichDetection(centroid=(cy, cx), area=area, bbox=(r0, c0, r1, c1))
        )

    return detections


# ── Tracker ───────────────────────────────────────────────────────────────────


class NearestNeighbourTracker:
    """Feature-aware greedy tracker with flow-predicted motion and re-ID.

    Matching cost per (track, detection) pair:
        cost = λ_s × (spatial_dist / max_dist)
             + λ_a × (1 − cosine_sim(track_mean_desc, det.appearance))

    Setting λ_a = 0 or omitting appearance descriptors degrades to the
    original pure-spatial tracker behaviour.

    When a track dies it leaves a feature bank in _lost_banks.  Unmatched
    detections are checked against those banks before spawning a new track,
    allowing the same object to be re-identified after a long invisible period.
    """

    _VEL_ALPHA = 0.4  # EWM weight for velocity smoothing

    def __init__(
        self,
        max_distance: float = 200.0,
        max_gap: int = 25,
        lambda_spatial: float = 1.0,
        lambda_appearance: float = 0.4,
        bank_size: int = 20,
        reid_threshold: float = 0.75,
        max_lost_banks: int = 32,
    ) -> None:
        self.max_distance = max_distance
        self.max_gap = max_gap
        self.lambda_spatial = lambda_spatial
        self.lambda_appearance = lambda_appearance
        self.bank_size = bank_size
        self.reid_threshold = reid_threshold
        self.max_lost_banks = max_lost_banks

        self._tracks: dict[int, Track] = {}
        self._missed: dict[int, int] = {}
        self._last_centroid: dict[int, tuple[float, float]] = {}
        self._velocity: dict[int, tuple[float, float]] = {}
        self._last_frame: dict[int, int] = {}
        self._last_flow: dict[int, tuple[float, float] | None] = {}
        self._feature_bank: dict[int, deque] = {}
        self._lost_banks: list[tuple[int, np.ndarray]] = []
        self._next_id: int = 0

    # ── Feature helpers ───────────────────────────────────────────────────────

    def _mean_desc(self, tid: int) -> np.ndarray | None:
        bank = self._feature_bank.get(tid)
        if not bank:
            return None
        mean = np.mean(bank, axis=0).astype(np.float32)
        norm = float(np.linalg.norm(mean))
        return (mean / norm) if norm > 1e-8 else None

    # ── Prediction ────────────────────────────────────────────────────────────

    def _predicted(self, tid: int, current_frame: int) -> tuple[float, float]:
        """Return predicted centroid (cy, cx) using flow-vec or EWM velocity."""
        cy, cx = self._last_centroid[tid]
        gap = current_frame - self._last_frame.get(tid, current_frame)

        # Priority 1: use RAFT flow vector stored from last matched detection
        last_flow = self._last_flow.get(tid)
        if last_flow is not None:
            fy, fx = last_flow
            return (cy + fy * gap, cx + fx * gap)

        # Priority 2: exponentially-weighted velocity (current behaviour)
        vy, vx = self._velocity.get(tid, (0.0, 0.0))
        return (cy + vy * gap, cx + vx * gap)

    # ── Matching cost ─────────────────────────────────────────────────────────

    def _cost(
        self,
        pred: tuple[float, float],
        det: RichDetection,
        mean_desc: np.ndarray | None,
    ) -> float:
        dy = pred[0] - det.centroid[0]
        dx = pred[1] - det.centroid[1]
        spatial = float(np.sqrt(dy * dy + dx * dx)) / self.max_distance

        if mean_desc is None or det.appearance is None or self.lambda_appearance == 0.0:
            return self.lambda_spatial * spatial

        cosine_sim = float(np.dot(mean_desc, det.appearance))
        appearance = 1.0 - cosine_sim  # 0 = perfect match, 2 = opposite
        return self.lambda_spatial * spatial + self.lambda_appearance * appearance

    # ── Update ────────────────────────────────────────────────────────────────

    def update(self, frame_idx: int, detections: list[RichDetection]) -> None:
        active_ids = [tid for tid in self._tracks if tid in self._last_centroid]

        if not active_ids:
            for det in detections:
                if not self._try_reid(frame_idx, det):
                    self._new_track(frame_idx, det)
            return

        if not detections:
            for tid in active_ids:
                self._missed[tid] = self._missed.get(tid, 0) + 1
            self._prune()
            return

        predicted = [self._predicted(tid, frame_idx) for tid in active_ids]

        # Build candidate pairs within the spatial gate, sorted by combined cost
        pairs: list[tuple[float, int, int]] = []
        for ti, tid in enumerate(active_ids):
            mean_desc = self._mean_desc(tid)
            pred = predicted[ti]
            for di, det in enumerate(detections):
                dy = pred[0] - det.centroid[0]
                dx = pred[1] - det.centroid[1]
                if float(np.sqrt(dy * dy + dx * dx)) > self.max_distance:
                    continue  # hard spatial gate
                pairs.append((self._cost(pred, det, mean_desc), ti, di))

        pairs.sort(key=lambda x: x[0])

        matched_tracks: set[int] = set()
        matched_dets: set[int] = set()

        for _, ti, di in pairs:
            if ti in matched_tracks or di in matched_dets:
                continue
            tid = active_ids[ti]
            det = detections[di]
            new_cy, new_cx = det.centroid
            old_cy, old_cx = self._last_centroid[tid]
            gap = max(frame_idx - self._last_frame.get(tid, frame_idx - 1), 1)
            raw_vy = (new_cy - old_cy) / gap
            raw_vx = (new_cx - old_cx) / gap
            old_vy, old_vx = self._velocity.get(tid, (raw_vy, raw_vx))
            a = self._VEL_ALPHA
            self._velocity[tid] = (
                a * raw_vy + (1 - a) * old_vy,
                a * raw_vx + (1 - a) * old_vx,
            )
            self._last_flow[tid] = det.flow_vec
            self._tracks[tid].positions[frame_idx] = TrackPoint(
                cx=new_cx,
                cy=new_cy,
                area=det.area,
            )
            self._last_centroid[tid] = det.centroid
            self._last_frame[tid] = frame_idx
            self._missed[tid] = 0
            if det.appearance is not None:
                bank = self._feature_bank.setdefault(tid, deque(maxlen=self.bank_size))
                bank.append(det.appearance)
            matched_tracks.add(ti)
            matched_dets.add(di)

        for ti, tid in enumerate(active_ids):
            if ti not in matched_tracks:
                self._missed[tid] = self._missed.get(tid, 0) + 1

        for di, det in enumerate(detections):
            if di not in matched_dets:
                if not self._try_reid(frame_idx, det):
                    self._new_track(frame_idx, det)

        self._prune()

    # ── Track lifecycle ───────────────────────────────────────────────────────

    def _new_track(self, frame_idx: int, det: RichDetection) -> None:
        tid = self._next_id
        self._next_id += 1
        self._tracks[tid] = Track(track_id=tid)
        self._tracks[tid].positions[frame_idx] = TrackPoint(
            cx=det.centroid[1],
            cy=det.centroid[0],
            area=det.area,
        )
        self._last_centroid[tid] = det.centroid
        self._last_frame[tid] = frame_idx
        self._velocity[tid] = (0.0, 0.0)
        self._last_flow[tid] = det.flow_vec
        self._missed[tid] = 0
        if det.appearance is not None:
            self._feature_bank[tid] = deque([det.appearance], maxlen=self.bank_size)

    def _prune(self) -> None:
        dead = [tid for tid, m in self._missed.items() if m > self.max_gap]
        for tid in dead:
            mean_desc = self._mean_desc(tid)
            if mean_desc is not None:
                self._lost_banks.append((tid, mean_desc))
                if len(self._lost_banks) > self.max_lost_banks:
                    self._lost_banks.pop(0)
            del self._missed[tid]
            del self._last_centroid[tid]
            self._velocity.pop(tid, None)
            self._last_frame.pop(tid, None)
            self._last_flow.pop(tid, None)
            self._feature_bank.pop(tid, None)

    def _try_reid(self, frame_idx: int, det: RichDetection) -> bool:
        """Try to revive a lost track that matches det's appearance.
        Returns True and restores the track's active state if successful.
        """
        if det.appearance is None or not self._lost_banks:
            return False

        best_sim = self.reid_threshold  # must strictly exceed this
        best_idx = None
        for li, (_, mean_desc) in enumerate(self._lost_banks):
            sim = float(np.dot(mean_desc, det.appearance))
            if sim > best_sim:
                best_sim = sim
                best_idx = li

        if best_idx is None:
            return False

        orig_tid, _ = self._lost_banks[best_idx]
        if orig_tid not in self._tracks:
            self._lost_banks.pop(best_idx)
            return False

        # Restore active state for the revived track
        self._last_centroid[orig_tid] = det.centroid
        self._last_frame[orig_tid] = frame_idx
        self._velocity[orig_tid] = (0.0, 0.0)
        self._last_flow[orig_tid] = det.flow_vec
        self._missed[orig_tid] = 0
        self._tracks[orig_tid].positions[frame_idx] = TrackPoint(
            cx=det.centroid[1],
            cy=det.centroid[0],
            area=det.area,
        )
        if det.appearance is not None:
            self._feature_bank[orig_tid] = deque(
                [det.appearance], maxlen=self.bank_size
            )
        self._lost_banks.pop(best_idx)
        return True

    # ── Output ────────────────────────────────────────────────────────────────

    def all_tracks(self) -> list[Track]:
        return list(self._tracks.values())

    def all_tracks_with_descriptors(self) -> tuple[list[Track], dict[int, np.ndarray]]:
        """Return all tracks plus a mapping of track_id → mean descriptor."""
        descs: dict[int, np.ndarray] = {}
        for tid in self._tracks:
            d = self._mean_desc(tid)
            if d is not None:
                descs[tid] = d
        return list(self._tracks.values()), descs


# ── Track merging ─────────────────────────────────────────────────────────────


def merge_tracks(
    tracks: list[Track],
    max_merge_gap: int = 120,
    max_merge_dist: float = 250.0,
    track_descriptors: dict[int, np.ndarray] | None = None,
    appearance_threshold: float = 0.0,
) -> list[Track]:
    """
    Merge pairs of tracks that are likely the same object split by a long
    invisible period.  Requirements for a merge:
      - track A ends before track B starts
      - temporal gap ≤ max_merge_gap
      - spatial distance ≤ max_merge_dist
      - (if appearance_threshold > 0 and descriptors available)
        cosine_sim(desc_A, desc_B) ≥ appearance_threshold
    """

    def _last(t: Track):
        f = max(t.positions)
        p = t.positions[f]
        return f, (p.cy, p.cx)

    def _first(t: Track):
        f = min(t.positions)
        p = t.positions[f]
        return f, (p.cy, p.cx)

    changed = True
    while changed:
        changed = False
        tracks.sort(key=lambda t: min(t.positions))
        merged_ids: set[int] = set()
        result: list[Track] = []

        for i, t1 in enumerate(tracks):
            if id(t1) in merged_ids:
                continue
            f1_end, pos1_end = _last(t1)

            best_j: int | None = None
            best_dist: float = float("inf")

            for j, t2 in enumerate(tracks):
                if j <= i or id(t2) in merged_ids:
                    continue
                f2_start, pos2_start = _first(t2)
                gap = f2_start - f1_end
                if gap < 0 or gap > max_merge_gap:
                    continue
                dist = float(
                    np.hypot(pos1_end[0] - pos2_start[0], pos1_end[1] - pos2_start[1])
                )
                if dist > max_merge_dist or dist >= best_dist:
                    continue
                # Optional appearance gate
                if appearance_threshold > 0.0 and track_descriptors is not None:
                    d1 = track_descriptors.get(t1.track_id)
                    d2 = track_descriptors.get(t2.track_id)
                    if d1 is not None and d2 is not None:
                        if float(np.dot(d1, d2)) < appearance_threshold:
                            continue
                best_dist = dist
                best_j = j

            if best_j is not None:
                t2 = tracks[best_j]
                t1.positions.update(t2.positions)
                merged_ids.add(id(t2))
                changed = True

            result.append(t1)

        tracks = [t for t in result if id(t) not in merged_ids]

    return tracks


# ── Persistence filter + gap interpolation ────────────────────────────────────


def filter_and_interpolate(
    tracks: list[Track],
    total_frames: int,
    min_fraction: float = 0.05,
) -> list[Track]:
    """
    Keep only tracks with presence >= min_fraction (detected frames / total),
    then linearly interpolate centroid for any gaps between detected frames.
    """
    kept: list[Track] = []
    for track in tracks:
        detected = sorted(track.positions)
        if not detected:
            continue
        fraction = len(detected) / max(total_frames, 1)
        if fraction < min_fraction:
            continue

        for i in range(len(detected) - 1):
            f0, f1 = detected[i], detected[i + 1]
            if f1 - f0 <= 1:
                continue
            p0 = track.positions[f0]
            p1 = track.positions[f1]
            for f in range(f0 + 1, f1):
                alpha = (f - f0) / (f1 - f0)
                track.positions[f] = TrackPoint(
                    cx=p0.cx + alpha * (p1.cx - p0.cx),
                    cy=p0.cy + alpha * (p1.cy - p0.cy),
                    area=int(p0.area + alpha * (p1.area - p0.area)),
                    interpolated=True,
                )

        kept.append(track)

    kept.sort(key=lambda t: min(t.positions))
    for new_id, t in enumerate(kept):
        t.track_id = new_id

    return kept


# ── High-level entry point ────────────────────────────────────────────────────


def run_tracker(
    heatmaps: list[np.ndarray],
    cfg: dict,
    rich_detections: list[list[RichDetection]] | None = None,
) -> list[Track]:
    """
    Run the full tracking pipeline.

    When rich_detections is provided (from Pass 3 feature extraction), those
    pre-computed detections are used directly and TemporalFilter / detect_blobs
    are skipped (they were already applied in Pass 3).

    When rich_detections is None (regenerate_raft=false path), blob detection
    is run from heatmaps only — same as the original behaviour.
    """
    tc = cfg.get("tracking", {})

    tracker = NearestNeighbourTracker(
        max_distance=tc.get("max_tracking_distance", 200),
        max_gap=tc.get("max_gap_frames", 25),
        lambda_spatial=tc.get("lambda_spatial", 1.0),
        lambda_appearance=tc.get("lambda_appearance", 0.4),
        bank_size=tc.get("feature_bank_size", 20),
        reid_threshold=tc.get("reid_similarity_threshold", 0.75),
        max_lost_banks=tc.get("max_lost_banks", 32),
    )

    if rich_detections is not None:
        for frame_idx, dets in enumerate(rich_detections):
            tracker.update(frame_idx, dets)
    else:
        filt = TemporalFilter(
            window=tc.get("temporal_window", 5),
            sigma=tc.get("neighborhood_sigma", 15),
            threshold_norm=tc.get("motion_threshold_norm", 0.08),
            base_weight=tc.get("combined_base_weight", 0.25),
        )
        for frame_idx, hm in enumerate(heatmaps):
            combined = filt.update(hm)
            detections = detect_blobs(
                combined,
                persistence_threshold=tc.get("persistence_threshold", 0.08),
                close_size=tc.get("morphology_close_size", 15),
                min_area=tc.get("min_blob_area", 150),
                max_area=tc.get("max_blob_area", 50_000),
            )
            tracker.update(frame_idx, detections)

    raw_tracks, track_descs = tracker.all_tracks_with_descriptors()

    merged = merge_tracks(
        raw_tracks,
        max_merge_gap=tc.get("max_gap_frames", 25) * 4,
        max_merge_dist=tc.get("max_tracking_distance", 200),
        track_descriptors=track_descs,
        appearance_threshold=tc.get("merge_appearance_threshold", 0.0),
    )
    return filter_and_interpolate(
        merged,
        total_frames=len(heatmaps),
        min_fraction=tc.get("min_presence_fraction", 0.05),
    )
