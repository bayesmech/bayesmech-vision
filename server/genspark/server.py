#!/usr/bin/env python3
"""
MCP server exposing scene analysis tools for the GenSpark pipeline.

Tools are intended to be called by an LLM (e.g. Claude) after it has
analyzed a recording via main.py. The tools are currently stubs; their
side-effects (scene tagging and temporal emphasis) are handled by the
calling agent infrastructure.

Run:
    cd server
    uv run python genspark/server.py          # stdio transport (default)
    fastmcp dev genspark/server.py            # interactive MCP inspector
"""

import enum

from fastmcp import FastMCP
from fastmcp.exceptions import ToolError

mcp = FastMCP(name="genspark")


class SceneType(str, enum.Enum):
    """Recognized scene categories for BayesMech Vision recordings."""
    SPORT_KARTING = "sport-karting"
    SPORT_RUNNING = "sport-running"
    SPORT_CHESS = "sport-chess"
    EXPERIMENT_PENDULUM = "experiment-pendulum"


@mcp.tool
def scene_context(type: SceneType) -> None:
    """
    Tag this recording with a scene type classification.

    Call this once after analyzing the video to label the overall activity
    captured in the recording. Only call this tool if the recording clearly
    matches one of the recognized scene categories; do not call it if the
    scene type is ambiguous or does not match any category.

    Args:
        type: The scene category that best describes this recording.
    """
    pass


@mcp.tool
def scene_emphasis(start_time: float, end_time: float) -> None:
    """
    Mark a temporal highlight segment within the recording.

    Identifies a time window (in seconds from the start of the video)
    where something notable, interesting, or transitionally significant
    occurs. Each segment must be 10 seconds or shorter. Only call this
    tool for segments that belong to the identified scene context type.

    Args:
        start_time: Start of the highlight in seconds (>= 0.0).
        end_time:   End of the highlight in seconds (> start_time).
    """
    duration = end_time - start_time
    if duration > 10.0:
        raise ToolError(
            f"Highlight segment must be 10.0 seconds or shorter, "
            f"but got {duration:.2f}s ({start_time}s \u2013 {end_time}s)."
        )
    pass


if __name__ == "__main__":
    mcp.run()
