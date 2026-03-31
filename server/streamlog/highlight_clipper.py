"""
highlight_clipper.py — Extract scene_emphasis windows from a .genspark.pb
and clip a list of frame timestamps to those windows.

Abstraction boundary: callers only know about "clip frame indices" — they
don't need to know that highlights come from scene_emphasis tool calls.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

# ── Proto imports ─────────────────────────────────────────────────────────────

import sys

_file = Path(__file__).resolve()
_server_root = _file.parent.parent
_project_root = _server_root.parent
for _p in (str(_project_root), str(_project_root / "proto")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from proto import insightgen_pb2


# ── Highlight extraction ──────────────────────────────────────────────────────

def extract_highlights(genspark_path: Path) -> list[insightgen_pb2.HighlightSegment]:
    """
    Parse a .genspark.pb file and return HighlightSegment protos for every
    scene_emphasis tool call found in the conversation turns.

    Returns an empty list if the file is absent, corrupt, or contains no
    scene_emphasis calls.
    """
    if not genspark_path.exists():
        return []
    try:
        raw = genspark_path.read_bytes()
        resp = insightgen_pb2.GensparkResponse()
        resp.ParseFromString(raw)
    except Exception as exc:
        logger.warning(f"highlight_clipper: failed to parse {genspark_path}: {exc}")
        return []

    segments: list[insightgen_pb2.HighlightSegment] = []
    for turn in resp.turns:
        for tc in turn.tool_calls:
            if tc.tool_name != "scene_emphasis":
                continue
            try:
                args = json.loads(tc.arguments_json)
                seg = insightgen_pb2.HighlightSegment(
                    start_time=float(args["start_time"]),
                    end_time=float(args["end_time"]),
                    description=args.get("description", ""),
                )
                segments.append(seg)
            except Exception as exc:
                logger.debug(f"highlight_clipper: skipping malformed scene_emphasis call: {exc}")

    return segments


# ── Frame index clipping ──────────────────────────────────────────────────────

def clip_frame_indices(
    timestamps_ns: list[int],
    highlights: list[insightgen_pb2.HighlightSegment],
) -> list[int]:
    """
    Return the subset of frame indices whose timestamps fall within at least
    one highlight window.

    ``timestamps_ns`` is the list of per-frame timestamp_ns values from the
    .vis.pb recording (index corresponds to vis frame index).

    If ``highlights`` is empty, all indices are returned (full recording).
    """
    if not highlights or not timestamps_ns:
        return list(range(len(timestamps_ns)))

    t0_ns = timestamps_ns[0]

    def _in_any_window(ts_ns: int) -> bool:
        t_s = (ts_ns - t0_ns) / 1e9
        for seg in highlights:
            if seg.start_time <= t_s <= seg.end_time:
                return True
        return False

    return [i for i, ts in enumerate(timestamps_ns) if _in_any_window(ts)]
