"""Build motioncap trajectories from segmentation masks gated by heatmaps."""

from __future__ import annotations

import struct
import zlib
from dataclasses import dataclass, field

import cv2
import numpy as np

from proto import segmentation_pb2

from motioncap.tracker import Track, TrackPoint


@dataclass
class _SegmentationTrackAccum:
    track_id: int
    label: str
    positions: dict[int, TrackPoint] = field(default_factory=dict)
    observations: int = 0
    motion_observations: int = 0
    max_heatmap_value: float = 0.0


def decode_segmentation_mask(data: bytes) -> np.ndarray:
    """Decode [h:u32le][w:u32le][zlib(packbits(bool mask))] into a bool mask."""
    if len(data) < 8:
        raise ValueError("Segmentation mask payload is too small")
    h, w = struct.unpack("<II", data[:8])
    packed = np.frombuffer(zlib.decompress(data[8:]), dtype=np.uint8)
    raw = np.unpackbits(packed, count=h * w)
    return raw.reshape(h, w).astype(bool)


def _resize_mask_to_heatmap(
    mask: np.ndarray, heatmap_shape: tuple[int, int]
) -> np.ndarray:
    hm_h, hm_w = heatmap_shape
    if mask.shape == (hm_h, hm_w):
        return mask.astype(bool, copy=False)
    resized = cv2.resize(
        mask.astype(np.uint8, copy=False),
        (hm_w, hm_h),
        interpolation=cv2.INTER_NEAREST,
    )
    return resized.astype(bool)


def _centroid(mask: np.ndarray) -> tuple[float, float] | None:
    ys, xs = np.nonzero(mask)
    if len(xs) == 0:
        return None
    return float(xs.mean()), float(ys.mean())


def _heatmap_motion_stats(
    heatmap: np.ndarray,
    mask: np.ndarray,
    *,
    percentile: float,
    value_threshold: float,
) -> tuple[float, float]:
    values = heatmap[mask]
    if values.size == 0:
        return 0.0, 0.0
    pct_value = float(np.percentile(values, percentile))
    high_fraction = float(np.mean(values >= value_threshold))
    return pct_value, high_fraction


def _interpolate_track(track: Track, max_gap_frames: int) -> None:
    if max_gap_frames <= 1:
        return
    detected = sorted(track.positions)
    for i in range(len(detected) - 1):
        f0, f1 = detected[i], detected[i + 1]
        gap = f1 - f0
        if gap <= 1 or gap > max_gap_frames:
            continue
        p0 = track.positions[f0]
        p1 = track.positions[f1]
        for frame_idx in range(f0 + 1, f1):
            alpha = (frame_idx - f0) / gap
            track.positions[frame_idx] = TrackPoint(
                cx=p0.cx + alpha * (p1.cx - p0.cx),
                cy=p0.cy + alpha * (p1.cy - p0.cy),
                area=int(p0.area + alpha * (p1.area - p0.area)),
                interpolated=True,
            )


def _segmentation_frame_to_heatmap_index(
    response: segmentation_pb2.SegmentationResponse,
    by_frame_number: dict[int, int],
    by_timestamp: dict[int, int],
) -> int | None:
    frame_identifier = response.frame_identifier
    frame_number = int(frame_identifier.frame_number)
    if frame_number in by_frame_number:
        return by_frame_number[frame_number]
    timestamp_ns = int(frame_identifier.timestamp_ns)
    return by_timestamp.get(timestamp_ns)


def build_segmentation_trajectories(
    segmentation_records: list[segmentation_pb2.SegmentationResponse],
    heatmaps: list[np.ndarray],
    frame_ids: list[int],
    timestamps: list[int],
    cfg: dict,
) -> list[Track]:
    """Track segmented objects whose masks overlap enough heatmap motion."""
    if not heatmaps:
        return []

    tc = cfg.get("segmentation_tracking", {})
    if not tc.get("enabled", True):
        return []

    heatmap_percentile = float(tc.get("heatmap_percentile", 90))
    value_threshold = float(tc.get("motion_value_threshold", 48))
    min_motion_fraction = float(tc.get("min_motion_pixel_fraction", 0.02))
    min_motion_observations = int(tc.get("min_motion_observations", 2))
    min_motion_observation_fraction = float(
        tc.get("min_motion_observation_fraction", 0.05)
    )
    min_presence_fraction = float(tc.get("min_presence_fraction", 0.02))
    min_mask_area = int(tc.get("min_mask_area", 50))
    max_interpolation_gap = int(tc.get("max_interpolation_gap_frames", 30))

    by_frame_number = {int(frame_id): idx for idx, frame_id in enumerate(frame_ids)}
    by_timestamp = {int(ts): idx for idx, ts in enumerate(timestamps)}
    accum: dict[int, _SegmentationTrackAccum] = {}

    for response in segmentation_records:
        heatmap_idx = _segmentation_frame_to_heatmap_index(
            response,
            by_frame_number,
            by_timestamp,
        )
        if heatmap_idx is None or heatmap_idx < 0 or heatmap_idx >= len(heatmaps):
            continue

        heatmap = heatmaps[heatmap_idx]
        for seg_mask in response.masks:
            if int(seg_mask.pixel_count) < min_mask_area:
                continue
            try:
                mask = decode_segmentation_mask(seg_mask.mask_data)
            except Exception:
                continue
            mask_hm = _resize_mask_to_heatmap(mask, heatmap.shape[:2])
            centroid = _centroid(mask_hm)
            if centroid is None:
                continue

            pct_value, high_fraction = _heatmap_motion_stats(
                heatmap,
                mask_hm,
                percentile=heatmap_percentile,
                value_threshold=value_threshold,
            )
            is_motion = (
                pct_value >= value_threshold or high_fraction >= min_motion_fraction
            )

            track_id = int(seg_mask.object_id)
            item = accum.setdefault(
                track_id,
                _SegmentationTrackAccum(
                    track_id=track_id,
                    label=seg_mask.label,
                ),
            )
            if not item.label and seg_mask.label:
                item.label = seg_mask.label
            item.observations += 1
            if is_motion:
                item.motion_observations += 1
            item.max_heatmap_value = max(item.max_heatmap_value, pct_value)
            cx, cy = centroid
            item.positions[heatmap_idx] = TrackPoint(
                cx=cx,
                cy=cy,
                area=int(mask_hm.sum()),
            )

    total_frames = len(heatmaps)
    tracks: list[Track] = []
    for item in accum.values():
        if not item.positions:
            continue
        if item.motion_observations < min_motion_observations:
            continue
        if (
            item.motion_observations / max(item.observations, 1)
            < min_motion_observation_fraction
        ):
            continue
        detected_fraction = len(item.positions) / max(total_frames, 1)
        if detected_fraction < min_presence_fraction:
            continue

        track = Track(track_id=item.track_id, label=item.label)
        track.positions.update(item.positions)
        _interpolate_track(track, max_interpolation_gap)
        tracks.append(track)

    tracks.sort(key=lambda track: (min(track.positions), track.track_id))
    return tracks
