#!/usr/bin/env python3
"""
MCP server exposing scene analysis tools for the GenSpark pipeline.

Run (from server/):
    uv run python genspark/server.py                                     # stdio, no recording context
    uv run python genspark/server.py ../recordings/NAME.vis.pb           # with recording for get_motioncap_tracks
    fastmcp dev genspark/server.py -- ../recordings/NAME.vis.pb          # interactive inspector
"""

import enum
import json
import sys
from pathlib import Path

# ── Path setup ────────────────────────────────────────────────────────────────
_server_root = Path(__file__).resolve().parent.parent
_project_root = _server_root.parent
sys.path.insert(0, str(_project_root))
sys.path.insert(0, str(_project_root / "proto"))  # needed for primitives_pb2 absolute imports
sys.path.insert(0, str(_server_root))

from fastmcp import FastMCP
from fastmcp.exceptions import ToolError

from proto import motioncap_pb2
from streamlog.protoio import ProtoIO

mcp = FastMCP(name="genspark")

_motion_io = ProtoIO(motioncap_pb2.MotionCaptureResponse)

# ── Recording context (set via CLI arg on startup) ────────────────────────────

_recording_path: Path | None = None


def set_recording(path: Path) -> None:
    global _recording_path
    _recording_path = path


# ── Helpers ───────────────────────────────────────────────────────────────────

def _format_tracks(motion_path: Path, start_time: float, end_time: float) -> str:
    records = _motion_io.read_file(motion_path)
    if not records:
        return "Motion capture file is empty."

    # Separate per-frame records from the trailing summary record
    frame_records = [r for r in records if not r.tracks]
    summary_records = [r for r in records if r.tracks]
    if not summary_records:
        return "No track summary found in motion capture file."

    summary = summary_records[0]

    # Build frame_idx → elapsed_seconds mapping
    if not frame_records:
        return "No frame records found; cannot map frame indices to timestamps."

    t0_ns = frame_records[0].frame_identifier.timestamp_ns
    ts = {}
    for i, r in enumerate(frame_records):
        ts[i] = (r.frame_identifier.timestamp_ns - t0_ns) / 1e9

    total_duration = max(ts.values()) if ts else 0.0

    lines = [
        f"Motion tracks in [{start_time:.2f}s – {end_time:.2f}s] "
        f"(recording duration: {total_duration:.2f}s):"
    ]
    found = 0
    for track in summary.tracks:
        positions_in_window = [
            p for p in track.positions
            if p.frame_idx in ts and start_time <= ts[p.frame_idx] <= end_time
        ]
        if not positions_in_window:
            continue
        found += 1
        label = f"T{found}"
        lines.append(
            f"\n{label} (track_id={track.track_id}): "
            f"{len(positions_in_window)} positions in window, "
            f"presence={track.presence_fraction * 100:.1f}% overall"
        )
        for p in positions_in_window:
            t = ts.get(p.frame_idx, 0.0)
            interp = " [interp]" if p.interpolated else ""
            lines.append(f"  t={t:.3f}s  cx={p.cx:.1f}  cy={p.cy:.1f}{interp}")

    if found == 0:
        lines.append("No tracks found in this time window.")

    return "\n".join(lines)


# ── Scene types ───────────────────────────────────────────────────────────────

class SceneType(str, enum.Enum):
    """Recognized scene categories for BayesMech Vision recordings."""
    SPORT_KARTING = "sport-karting"
    SPORT_RUNNING = "sport-running"
    SPORT_CHESS = "sport-chess"
    EXPERIMENT_PENDULUM = "experiment-pendulum"


# ── Tools ─────────────────────────────────────────────────────────────────────

@mcp.tool()
def scene_context(type: SceneType) -> str:
    """
    Tag this recording with a scene type classification and receive scene-specific
    analysis instructions. Always call this once after identifying the scene type.

    Args:
        type: The scene category that best describes this recording.

    Returns:
        Scene-specific follow-up instructions to carry out.
    """
    if type == SceneType.EXPERIMENT_PENDULUM:
        return (
            "Scene classified as experiment-pendulum. Motion tracking data is available for "
            "this recording. Please do the following:\n"
            "1. Call get_motioncap_tracks(0, <total_recording_duration>) to retrieve all track data.\n"
            "2. Identify which track(s) represent the pendulum — look for oscillatory (back-and-forth) "
            "motion in the cx (horizontal) or cy (vertical) coordinate. Multiple tracks may represent "
            "the same pendulum if tracking was lost and later recovered.\n"
            "3. For each pendulum track, determine:\n"
            "   - Oscillation period: time between successive extrema of the same type (peak-to-peak or trough-to-trough)\n"
            "   - Exact timestamps when the pendulum is at an extremum (local maximum or minimum in cx or cy)\n"
            "   - Amplitude at each extremum (distance from the equilibrium position)\n"
            "   - Whether the amplitude is decaying (damped oscillation) and estimate the decay rate\n"
            "4. Call scene_emphasis() to mark a 2–5s window around each identified extremum.\n"
            "5. Provide a final numerical summary: period(s), extremum times, amplitudes, and decay rate."
        )
    return f"Scene classified as {type.value}. No additional analysis required."


@mcp.tool()
def scene_emphasis(start_time: float, end_time: float, description: str) -> str:
    """
    Mark a temporal highlight segment within the recording.

    Each segment should be between 2 and 10 seconds long. Provide a description
    explaining what is happening and why this moment is notable.

    Args:
        start_time:  Start of the highlight in seconds (>= 0.0).
        end_time:    End of the highlight in seconds (> start_time).
        description: What is happening in this segment and why it is notable.
    """
    duration = end_time - start_time
    if duration > 10.0:
        raise ToolError(
            f"Highlight segment must be 10.0 seconds or shorter, "
            f"but got {duration:.2f}s ({start_time}s – {end_time}s)."
        )
    return f"Marked emphasis [{start_time}s – {end_time}s]: {description}"


@mcp.tool()
def get_motioncap_tracks(start_time: float, end_time: float) -> str:
    """
    Return motion tracking data for the time window [start_time, end_time] (in seconds).

    Track positions are (cx, cy) pixel coordinates in the video frame (origin top-left).
    Use this to analyze object motion: oscillation, speed, trajectory, etc.

    Args:
        start_time: Start of the window in seconds (>= 0.0).
        end_time:   End of the window in seconds (> start_time).
    """
    if _recording_path is None:
        return (
            "No recording context loaded. Start the server with a recording path:\n"
            "  uv run python genspark/server.py ../recordings/NAME.vis.pb"
        )
    base = _recording_path.name
    if base.endswith(".vis.pb"):
        base = base[: -len(".vis.pb")]
    motion_path = _recording_path.parent / f"{base}.motion.pb"
    if not motion_path.exists():
        return f"No motion capture file found ({motion_path.name}). Run motioncap first."
    return _format_tracks(motion_path, start_time, end_time)


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="Genspark MCP server — exposes scene analysis tools"
    )
    parser.add_argument(
        "recording", nargs="?",
        help="Path to .vis.pb recording file (enables get_motioncap_tracks)"
    )
    args = parser.parse_args()

    if args.recording:
        set_recording(Path(args.recording).resolve())
        print(f"[genspark-server] Recording context: {_recording_path}", file=sys.stderr)

    mcp.run()
