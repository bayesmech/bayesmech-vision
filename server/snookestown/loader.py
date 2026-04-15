"""Read .vis.pb + sibling .seg.pb, group masks per frame by label."""
from __future__ import annotations

import struct
import sys
import zlib
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

_server_root = Path(__file__).resolve().parent.parent
_project_root = _server_root.parent
sys.path.insert(0, str(_project_root))
sys.path.insert(0, str(_project_root / "proto"))
sys.path.insert(0, str(_server_root))

from proto import perceiver_pb2, segmentation_pb2
from streamlog.protoio import ProtoIO

_frame_io = ProtoIO(perceiver_pb2.PerceiverDataFrame)
_seg_io = ProtoIO(segmentation_pb2.SegmentationResponse)


# ── Mask decoding ─────────────────────────────────────────────────────────────

def decode_mask(data: bytes) -> np.ndarray:
    """Decode SegmentationMask.mask_data → bool array (H, W).

    Format: [h: u32 LE][w: u32 LE][zlib(packbits(mask.flatten()))].
    """
    h, w = struct.unpack("<II", data[:8])
    packed = np.frombuffer(zlib.decompress(data[8:]), dtype=np.uint8)
    flat = np.unpackbits(packed)[: h * w]
    return flat.reshape(h, w).astype(bool)


# ── Label normalisation ──────────────────────────────────────────────────────

_LABEL_MAP = {
    # canonical → list of recognised substrings (lowercased)
    "table":  ["table", "snooker table", "billiard table", "pool table"],
    "ball":   ["ball"],
    "cue":    ["cue"],
    "person": ["person", "player", "human"],
}


def canonical_label(raw: str) -> str | None:
    """Map SAM3 per-mask label to one of {table, ball, cue, person} or None."""
    s = (raw or "").strip().lower()
    if not s:
        return None
    # Cue before ball: "cue ball" should match cue? No — cue ball IS a ball.
    # Check in priority order where "ball" wins over "cue" when both appear.
    if "ball" in s:
        return "ball"
    if "cue" in s:
        return "cue"
    if any(tok in s for tok in _LABEL_MAP["person"]):
        return "person"
    if any(tok in s for tok in _LABEL_MAP["table"]):
        return "table"
    return None


# ── Per-frame bundle ──────────────────────────────────────────────────────────

@dataclass
class FrameMasks:
    """All decoded masks for one frame, grouped by canonical label.

    Each entry is a list of (object_id, confidence, mask_bool_hw).
    """
    table: list[tuple[int, float, np.ndarray]] = field(default_factory=list)
    balls: list[tuple[int, float, np.ndarray]] = field(default_factory=list)
    cues: list[tuple[int, float, np.ndarray]] = field(default_factory=list)
    persons: list[tuple[int, float, np.ndarray]] = field(default_factory=list)

    def largest_table_mask_raw(self) -> np.ndarray | None:
        """Largest table mask with NO occluder subtraction — used for hull computation."""
        if not self.table:
            return None
        return max(self.table, key=lambda t: int(t[2].sum()))[2]

    def largest_table_mask(self) -> np.ndarray | None:
        if not self.table:
            return None
        tm = max(self.table, key=lambda t: int(t[2].sum()))[2].copy()
        # Subtract person/cue pixels occluding the table so the boundary
        # detector doesn't mistake an arm or cue for a cushion concavity.
        for _, _, m in self.persons:
            if m.shape == tm.shape:
                tm &= ~m
        for _, _, m in self.cues:
            if m.shape == tm.shape:
                tm &= ~m
        return tm


@dataclass
class LoadedRecording:
    frames: list[perceiver_pb2.PerceiverDataFrame]
    # Per frame_index → FrameMasks (only frames with at least one mask).
    masks_by_index: dict[int, FrameMasks]
    # For debug / reference.
    seg_path: Path


def _seg_path(vis_path: Path) -> Path:
    name = vis_path.name
    if name.endswith(".vis.pb"):
        return vis_path.parent / (name.removesuffix(".vis.pb") + ".seg.pb")
    return vis_path.with_suffix(".seg.pb")


def load_recording(
    vis_path: Path,
    max_frames: int = 0,
    sample_every: int = 1,
) -> LoadedRecording:
    """Read frames + masks. Applies sample_every then max_frames (in that order).

    Indices returned in `masks_by_index` are positions into the returned
    `frames` list (post-sampling).
    """
    frames_all = _frame_io.read_file(vis_path)
    if not frames_all:
        raise RuntimeError(f"No frames in {vis_path}")

    if sample_every > 1:
        frames_all = frames_all[::sample_every]
    if max_frames and len(frames_all) > max_frames:
        frames_all = frames_all[:max_frames]

    # Build (timestamp_ns, frame_number) → index lookup.
    key_to_idx: dict[tuple[int, int], int] = {}
    for i, f in enumerate(frames_all):
        fid = f.frame_identifier
        key_to_idx[(fid.timestamp_ns, fid.frame_number)] = i

    seg_path = _seg_path(vis_path)
    masks_by_index: dict[int, FrameMasks] = {}

    if seg_path.exists():
        seg_responses = _seg_io.read_file(seg_path)
        for resp in seg_responses:
            fid = resp.frame_identifier
            idx = key_to_idx.get((fid.timestamp_ns, fid.frame_number))
            if idx is None:
                continue  # seg frame not in our sampled set
            bundle = FrameMasks()
            for m in resp.masks:
                label = canonical_label(m.label)
                if label is None:
                    continue
                try:
                    mask = decode_mask(m.mask_data)
                except Exception:
                    continue
                entry = (int(m.object_id), float(m.confidence), mask)
                if label == "table":
                    bundle.table.append(entry)
                elif label == "ball":
                    bundle.balls.append(entry)
                elif label == "cue":
                    bundle.cues.append(entry)
                elif label == "person":
                    bundle.persons.append(entry)
            if bundle.table or bundle.balls or bundle.cues or bundle.persons:
                masks_by_index[idx] = bundle

    return LoadedRecording(
        frames=frames_all,
        masks_by_index=masks_by_index,
        seg_path=seg_path,
    )
