#!/usr/bin/env python3
"""
Analyze a .vis.pb recording using Gemini's native video upload API
(agentic multi-turn loop).

After the initial video analysis the model may call tools (scene_context,
scene_emphasis, and analyzer-specific data tools). Tool results are fed back
and the conversation continues until the model returns a response with no tool
calls.
The full conversation is saved as a GensparkResponse proto.

Usage (from server/):
    uv run python genspark/main.py ../recordings/<name>.vis.pb
    uv run python genspark/main.py ../recordings/<name>.vis.pb \\
        --offset 5 --max-duration 20 --fps 8 --width 480 --height 360
"""

import argparse
import asyncio
import copy
import json
import os
import sys
import tempfile
import time
from contextlib import AsyncExitStack
from pathlib import Path

# ── Path setup ────────────────────────────────────────────────────────────────
_server_root = Path(__file__).resolve().parent.parent
_project_root = _server_root.parent
sys.path.insert(0, str(_project_root))
sys.path.insert(0, str(_project_root / "proto"))
sys.path.insert(0, str(_server_root))

from dotenv import load_dotenv
load_dotenv(_project_root / ".env")

import cv2
import numpy as np
import yaml
from tqdm import tqdm

from proto import insightgen_pb2
from proto import perceiver_pb2
from streamlog.protoio import ProtoIO

_frame_io = ProtoIO(perceiver_pb2.PerceiverDataFrame)

_config_path = Path(__file__).parent / "config.yaml"
with open(_config_path) as _f:
    _CONFIG = yaml.safe_load(_f)


# ── Tool definitions ─────────────────────────────────────────────────────────

def _tool(name: str, description: str, properties: dict | None = None, required: list[str] | None = None) -> dict:
    return {
        "name": name,
        "description": description,
        "parameters": {
            "type": "object",
            "properties": properties or {},
            "required": required or [],
        },
    }


def _string(description: str, enum: list[str] | None = None) -> dict:
    prop = {"type": "string", "description": description}
    if enum:
        prop["enum"] = enum
    return prop


def _number(description: str) -> dict:
    return {"type": "number", "description": description}


def _integer(description: str) -> dict:
    return {"type": "integer", "description": description}


def _boolean(description: str) -> dict:
    return {"type": "boolean", "description": description}


TOOLS = [
    _tool(
        "scene_context",
        (
            "Tag this recording with a scene type classification. Always call "
            "this once after identifying the scene type. Returns scene-specific "
            "follow-up analysis instructions."
        ),
        {
            "type": _string(
                "The scene category that best describes this recording.",
                ["sport-karting", "sport-running", "sport-chess", "experiment-pendulum"],
            )
        },
        ["type"],
    ),
    _tool(
        "scene_emphasis",
        (
            "Mark a temporal highlight segment within the recording. Each "
            "segment should be between 2 and 10 seconds long. Returns a "
            "confirmation string."
        ),
        {
            "start_time": _number("Start of the highlight in seconds (>= 0.0)."),
            "end_time": _number("End of the highlight in seconds (> start_time, <= start_time + 10)."),
            "description": _string("What is happening and why this moment is notable."),
        },
        ["start_time", "end_time", "description"],
    ),
    _tool(
        "list_available_analyses",
        (
            "List which analyzer outputs are available for the recording. Use "
            "before analyzer-specific calls to check segmentation, motioncap, "
            "Pongtown, and SLAM availability."
        ),
    ),
    _tool(
        "get_recording_metadata",
        "Return recording duration, frame count, FPS estimate, and frame dimensions.",
    ),
    _tool(
        "segmentation_list_identified_objects",
        "List persistent segmented objects with labels, time spans, confidence, and average mask size.",
    ),
    _tool(
        "segmentation_get_objects_at_time",
        "Return segmented objects in the frame nearest a requested time.",
        {"t": _number("Time in seconds from the start of the recording.")},
        ["t"],
    ),
    _tool(
        "segmentation_get_object_track",
        "Return a segmentation centroid track for one object ID in image-space pixels.",
        {
            "object_id": _integer("Persistent segmentation object ID."),
            "from_time": _number("Optional window start time in seconds."),
            "to_time": _number("Optional window end time in seconds."),
            "limit": _integer("Maximum returned positions. Use 0 for no limit."),
        },
        ["object_id"],
    ),
    _tool(
        "segmentation_get_relative_position",
        (
            "Return dx, dy, distance, and qualitative relation between two "
            "segmented objects. Object arguments can be IDs or exact labels."
        ),
        {
            "object_1": _string("First object ID or exact label."),
            "object_2": _string("Second object ID or exact label."),
            "t": _number("Optional time in seconds. If omitted, uses the first frame where both are visible."),
        },
        ["object_1", "object_2"],
    ),
    _tool(
        "motioncap_list_tracks",
        "List available motion tracks and summaries.",
        {
            "segmentation": _boolean(
                "false for RAFT/motioncap tracks; true for tracks derived from segmentation centroids."
            )
        },
    ),
    _tool(
        "motioncap_get_track_summary",
        "Summarize one motion track's displacement, path distance, duration, and speed.",
        {
            "track_id": _integer("Motion track ID."),
            "segmentation": _boolean("Use segmentation-derived tracks instead of RAFT/motioncap tracks."),
            "from_time": _number("Optional window start time in seconds."),
            "to_time": _number("Optional window end time in seconds."),
        },
        ["track_id"],
    ),
    _tool(
        "motioncap_get_moving_objects",
        "Return tracks that move at least min_distance_px in a time window.",
        {
            "from_time": _number("Window start time in seconds."),
            "to_time": _number("Window end time in seconds."),
            "min_distance_px": _number("Minimum path distance in pixels."),
            "segmentation": _boolean("Use segmentation-derived tracks instead of RAFT/motioncap tracks."),
        },
        ["from_time", "to_time"],
    ),
    _tool(
        "motioncap_find_extrema",
        "Find local extrema in a track's x or y centroid coordinate.",
        {
            "track_id": _integer("Motion track ID."),
            "axis": _string("Coordinate axis to analyze.", ["x", "y"]),
            "from_time": _number("Optional window start time in seconds."),
            "to_time": _number("Optional window end time in seconds."),
            "segmentation": _boolean("Use segmentation-derived tracks instead of RAFT/motioncap tracks."),
        },
        ["track_id"],
    ),
    _tool(
        "motioncap_estimate_period",
        "Estimate oscillation period from same-kind extrema in one motion track.",
        {
            "track_id": _integer("Motion track ID."),
            "axis": _string("Coordinate axis to analyze.", ["x", "y"]),
            "from_time": _number("Optional window start time in seconds."),
            "to_time": _number("Optional window end time in seconds."),
            "segmentation": _boolean("Use segmentation-derived tracks instead of RAFT/motioncap tracks."),
        },
        ["track_id"],
    ),
    _tool(
        "get_motioncap_tracks",
        (
            "Return motion tracking data for a time window. Track positions are "
            "(cx, cy) pixel coordinates. Set segmentation=true to use semantic "
            "segmentation centroid tracks instead of RAFT/motioncap tracks."
        ),
        {
            "start_time": _number("Start of the window in seconds (>= 0.0)."),
            "end_time": _number("End of the window in seconds (> start_time)."),
            "segmentation": _boolean("Use segmentation-derived tracks instead of RAFT/motioncap tracks."),
            "limit_per_track": _integer("Maximum returned positions per track. Use 0 for no limit."),
        },
        ["start_time", "end_time"],
    ),
    _tool(
        "pongtown_get_table_pose_at_time",
        "Return ping-pong table and net pose/reprojection nearest a requested time.",
        {"t": _number("Time in seconds from the start of the Pongtown output.")},
        ["t"],
    ),
    _tool(
        "pongtown_get_ball_trajectory",
        "Return ping-pong ball trajectory in image and table coordinates.",
        {
            "from_time": _number("Optional window start time in seconds."),
            "to_time": _number("Optional window end time in seconds."),
            "limit": _integer("Maximum returned trajectory points. Use 0 for no limit."),
            "responder_pov": _string("Player point of view for table coordinates.", ["near", "far"]),
        },
    ),
    _tool(
        "pongtown_get_table_bounces",
        "Return detected table bounce candidates in a time window with responder-relative side labels.",
        {
            "from_time": _number("Optional window start time in seconds."),
            "to_time": _number("Optional window end time in seconds."),
            "responder_pov": _string("Player point of view for self/opponent side mapping.", ["near", "far"]),
        },
    ),
    _tool(
        "pongtown_get_ball_speed",
        "Return ping-pong ball speed statistics over trajectory segments.",
        {
            "from_time": _number("Window start time in seconds."),
            "to_time": _number("Window end time in seconds."),
            "space": _string("Coordinate space for speed.", ["table", "image"]),
        },
        ["from_time", "to_time"],
    ),
    _tool(
        "slam_get_map",
        "Return SLAM camera trajectory as world positions per frame.",
        {"limit": _integer("Maximum returned poses. Use 0 for no limit.")},
    ),
    _tool(
        "slam_get_pose_at_time",
        "Return camera pose nearest a requested time.",
        {"t": _number("Time in seconds from the start of SLAM poses.")},
        ["t"],
    ),
    _tool(
        "slam_get_velocity_at_time",
        "Estimate camera velocity around a requested time from neighboring SLAM poses.",
        {
            "t": _number("Time in seconds from the start of SLAM poses."),
            "window_s": _number("Time window in seconds used for the estimate."),
        },
        ["t"],
    ),
    _tool(
        "slam_get_position_on_road",
        "Return lateral road position nearest a time as a 0..1 fraction from the left edge.",
        {"t": _number("Time in seconds from the start of canonical road tracks.")},
        ["t"],
    ),
    _tool(
        "slam_get_road_width_at_time",
        "Return nearest road-width estimate at a requested time.",
        {"t": _number("Time in seconds from the start of road-width estimates.")},
        ["t"],
    ),
    _tool(
        "slam_get_lap_progress",
        "Return canonical track/lap progress nearest a requested time.",
        {"t": _number("Time in seconds from the start of canonical road tracks.")},
        ["t"],
    ),
    _tool(
        "slam_get_motion_between",
        "Summarize SLAM camera displacement and path distance between two times.",
        {
            "from_time": _number("Window start time in seconds."),
            "to_time": _number("Window end time in seconds."),
        },
        ["from_time", "to_time"],
    ),
]


# ── MCP tool runner ───────────────────────────────────────────────────────────

class McpToolRunner:
    """
    Starts genspark/server.py as a subprocess and exposes a synchronous
    interface for calling its tools via the MCP stdio transport.

    Guarantees:
    - All REQUIRED_TOOLS must be present on the server at startup; otherwise
      the process exits with a clear error message.
    - If a tool call fails because the server has crashed or become unreachable,
      the process exits immediately (no silent fallback).
    """

    REQUIRED_TOOLS = frozenset(t["name"] for t in TOOLS)

    def __init__(self, rec_path: Path) -> None:
        self._rec_path = rec_path
        self._loop = asyncio.new_event_loop()
        self._session = None
        self._stack: AsyncExitStack | None = None

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    def start(self) -> None:
        """Start the MCP server subprocess and verify all required tools are present.
        Calls sys.exit(1) on any failure so the outer process crashes loudly."""
        try:
            self._loop.run_until_complete(self._connect())
        except SystemExit:
            raise
        except Exception as exc:
            print(
                f"\n[MCP] ERROR: Failed to start MCP server: {exc}\n"
                f"[MCP] Make sure genspark/server.py is runnable and has no import errors.",
                file=sys.stderr,
            )
            sys.exit(1)

    async def _connect(self) -> None:
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client

        server_script = Path(__file__).parent / "server.py"
        params = StdioServerParameters(
            command=sys.executable,
            args=[str(server_script), str(self._rec_path)],
        )

        self._stack = AsyncExitStack()
        read, write = await self._stack.enter_async_context(stdio_client(params))
        session = ClientSession(read, write)
        self._session = await self._stack.enter_async_context(session)
        await self._session.initialize()

        # Verify all required tools are present before any LLM interaction
        tools_resp = await self._session.list_tools()
        available = {t.name for t in tools_resp.tools}
        missing = self.REQUIRED_TOOLS - available
        if missing:
            print(
                f"\n[MCP] ERROR: Required tools are missing from the MCP server: {sorted(missing)}\n"
                f"[MCP] Tools found: {sorted(available)}\n"
                f"[MCP] Aborting before any LLM interaction.",
                file=sys.stderr,
            )
            sys.exit(1)
        print(f"[MCP] Server ready. Tools: {sorted(available)}", file=sys.stderr)

    def stop(self) -> None:
        """Shut down the MCP server subprocess cleanly."""
        if self._stack and not self._loop.is_closed():
            try:
                self._loop.run_until_complete(self._stack.aclose())
            except Exception:
                pass
        if not self._loop.is_closed():
            self._loop.close()

    # ── Tool dispatch ─────────────────────────────────────────────────────────

    def call_tool(self, name: str, args: dict) -> str:
        """Synchronously dispatch a tool call to the MCP server.
        Crashes (sys.exit(1)) if the server is not running or the call fails."""
        if self._session is None:
            print(
                f"\n[MCP] ERROR: MCP server is not running; cannot execute '{name}'. Aborting.",
                file=sys.stderr,
            )
            sys.exit(1)
        try:
            result = self._loop.run_until_complete(self._session.call_tool(name, args))
            return "\n".join(c.text for c in result.content if hasattr(c, "text"))
        except Exception as exc:
            print(
                f"\n[MCP] ERROR: Tool call '{name}' failed — server may have crashed: {exc}\n"
                f"[MCP] Aborting.",
                file=sys.stderr,
            )
            sys.exit(1)


def call_tool(runner: McpToolRunner, name: str, args: dict) -> str:
    return runner.call_tool(name, args)


# ── Frame decoding ────────────────────────────────────────────────────────────

def decode_frame_bgr(frame: perceiver_pb2.PerceiverDataFrame) -> np.ndarray:
    img = frame.rgb_frame
    ImageFormat = perceiver_pb2.ImageFrame.ImageFormat
    if img.format == ImageFormat.JPEG:
        buf = np.frombuffer(img.data, dtype=np.uint8)
        return cv2.imdecode(buf, cv2.IMREAD_COLOR)
    elif img.format == ImageFormat.BITMAP_RGB:
        raw = np.frombuffer(img.data, dtype=np.uint8)
        total_pixels = len(raw) // 3
        side = int(total_pixels ** 0.5)
        rgb = raw.reshape((side, side, 3))
        return cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)
    else:
        raise ValueError(f"Unsupported image format: {img.format}")


# ── Frame selection ───────────────────────────────────────────────────────────

def select_frames(frames: list, cfg: dict) -> list:
    if not frames:
        return []

    t_start_ns = frames[0].frame_identifier.timestamp_ns
    offset_ns = int(cfg["video"]["start_offset_seconds"] * 1e9)
    max_dur_s = cfg["video"]["max_duration_seconds"]
    max_dur_ns = int(max_dur_s * 1e9) if max_dur_s > 0 else None

    window_start_ns = t_start_ns + offset_ns
    window_end_ns = (
        window_start_ns + max_dur_ns if max_dur_ns is not None else float("inf")
    )

    windowed = [
        f for f in frames
        if window_start_ns <= f.frame_identifier.timestamp_ns < window_end_ns
    ]
    if not windowed:
        return []

    target_fps = cfg["video"]["fps"]
    interval_ns = 1e9 / target_fps
    selected = []
    next_emit_ns = windowed[0].frame_identifier.timestamp_ns

    for f in windowed:
        t = f.frame_identifier.timestamp_ns
        if t >= next_emit_ns:
            selected.append(f)
            next_emit_ns = t + interval_ns

    return selected


# ── Video assembly ────────────────────────────────────────────────────────────

def build_video(frames: list, cfg: dict, tmp_path: str) -> None:
    w = cfg["video"]["width"]
    h = cfg["video"]["height"]
    fps = cfg["video"]["fps"]
    fourcc = cv2.VideoWriter_fourcc(*cfg["video"]["codec"])

    writer = cv2.VideoWriter(tmp_path, fourcc, fps, (w, h))
    if not writer.isOpened():
        raise RuntimeError(f"cv2.VideoWriter failed to open: {tmp_path}")

    try:
        with tqdm(total=len(frames), desc="Writing video", unit="frame") as bar:
            for frame in frames:
                bgr = decode_frame_bgr(frame)
                resized = cv2.resize(bgr, (w, h), interpolation=cv2.INTER_AREA)
                writer.write(resized)
                bar.update(1)
    finally:
        writer.release()


# ── Gemini (agentic multi-turn) ───────────────────────────────────────────────

def run_gemini(tmp_path: str, cfg: dict, runner: McpToolRunner) -> list:
    """Upload video, then run a multi-turn agentic loop with tool support."""
    from google import genai
    from google.genai import types as gtypes

    key_env = cfg["gemini"]["api_key_env"]
    api_key = os.environ.get(key_env)
    if not api_key:
        raise RuntimeError(f"Environment variable {key_env!r} is not set.")

    client = genai.Client(api_key=api_key)

    # Upload video
    print("Uploading video to Gemini Files API...")
    uploaded = client.files.upload(file=tmp_path, config={"mime_type": "video/mp4"})
    print(f"  Upload accepted: {uploaded.name}  (state={uploaded.state})")
    while uploaded.state is None or uploaded.state.name != "ACTIVE":
        if uploaded.state is not None and uploaded.state.name == "FAILED":
            raise RuntimeError(f"Gemini file processing failed for {uploaded.name}")
        print(f"  Waiting for ACTIVE (current: {uploaded.state})...")
        time.sleep(5)
        uploaded = client.files.get(name=uploaded.name)
    print(f"  File is ACTIVE: {uploaded.name}")

    prompt_text = (Path(__file__).parent / cfg["gemini"]["prompt_file"]).read_text("utf-8")
    model = cfg["gemini"]["model"]

    # Build Gemini function declarations from TOOLS
    function_declarations = []
    for t in TOOLS:
        params = t["parameters"]
        props = {}
        for pname, pschema in params.get("properties", {}).items():
            prop = {"type": pschema["type"]}
            if "description" in pschema:
                prop["description"] = pschema["description"]
            if "enum" in pschema:
                prop["enum"] = pschema["enum"]
            props[pname] = prop
        function_declarations.append(
            gtypes.FunctionDeclaration(
                name=t["name"],
                description=t["description"],
                parameters=gtypes.Schema(
                    type=gtypes.Type.OBJECT,
                    properties={k: gtypes.Schema(**v) for k, v in props.items()},
                    required=params.get("required", []),
                ),
            )
        )
    gemini_tools = gtypes.Tool(function_declarations=function_declarations)
    gen_config = gtypes.GenerateContentConfig(tools=[gemini_tools])

    contents = [uploaded, prompt_text]
    turns = []
    turn_num = 0

    try:
        while True:
            turn_num += 1
            print(f"\n[Turn {turn_num}] Querying {model}...")
            response = client.models.generate_content(
                model=model, contents=contents, config=gen_config
            )

            candidate = response.candidates[0]
            parts = candidate.content.parts if (candidate.content and candidate.content.parts) else []
            text_parts = [p.text for p in parts if hasattr(p, "text") and p.text]
            fc_parts = [p.function_call for p in parts if p.function_call]

            text = "\n".join(text_parts)
            turn = insightgen_pb2.GensparkTurn(text=text)

            if text:
                print(text[:500] + ("..." if len(text) > 500 else ""))

            if not fc_parts:
                turns.append(turn)
                break

            # Execute tools
            contents.append(candidate.content)
            function_responses = []
            for fc in fc_parts:
                args = dict(fc.args)
                print(f"  [tool call] {fc.name}({json.dumps(args)})")
                result = call_tool(runner, fc.name, args)
                print(f"  [tool result] {result[:200]}")
                turn.tool_calls.append(insightgen_pb2.GensparkToolCall(
                    tool_name=fc.name,
                    arguments_json=json.dumps(args),
                    result=result,
                ))
                function_responses.append(gtypes.Part(
                    function_response=gtypes.FunctionResponse(
                        name=fc.name,
                        response={"result": result},
                    )
                ))
            turns.append(turn)
            contents.append(gtypes.Content(role="user", parts=function_responses))
    finally:
        print(f"\nDeleting uploaded file {uploaded.name}...")
        client.files.delete(name=uploaded.name)

    return turns


# ── Summary generation ───────────────────────────────────────────────────────

_SUMMARY_PROMPT_BASE = """\
You are summarizing the results of an AI video analysis session.
Below is the full conversation between the analyzer and the model.
Produce a concise, well-formatted summary of the findings.

Return a JSON object with exactly these fields:
{
  "title": "<short descriptive title for this recording>",
  "text": "<markdown body — paragraphs, bullet points, etc.  Do NOT include a heading.>",
  "parameters": [
    {"name": "<parameter name>", "value": "<value>", "unit": "<unit or empty string>"},
    ...
  ]
}

Rules:
- "parameters" must be a flat list of key measurements or facts extracted from the analysis.
- For experiment-pendulum scenes, "parameters" MUST include at minimum:
    {"name": "Oscillation Period", "value": "...", "unit": "seconds"}
    {"name": "Amplitude", "value": "...", "unit": "pixels"}
  Include additional entries for decay rate, extremum timestamps, etc. if available.
- For other scene types, include any quantitative values or notable facts as parameters.
- "text" is valid Markdown, no H1/H2 headings, suitable for display on a mobile screen.
- Keep "text" under 300 words.

Conversation:
"""


def _build_conversation_text(turns: list) -> str:
    parts = []
    for i, turn in enumerate(turns):
        parts.append(f"[Turn {i + 1}]")
        if turn.text:
            parts.append(turn.text)
        for tc in turn.tool_calls:
            parts.append(f"  tool_call: {tc.tool_name}({tc.arguments_json})")
            result = tc.result
            if len(result) > 6000:
                result = result[:6000] + f"\n  [...{len(tc.result) - 6000} chars omitted...]"
            parts.append(f"  tool_result: {result}")
    return "\n".join(parts)


def generate_summary(turns: list, cfg: dict) -> insightgen_pb2.GensparkSummary:
    """Call Gemini (text-only) to produce a structured JSON summary of the conversation."""
    from google import genai
    from google.genai import types as gtypes

    api_key = os.environ.get(cfg["gemini"]["api_key_env"])
    if not api_key:
        print("Warning: GEMINI_API_KEY not set; skipping summary generation.", file=sys.stderr)
        return insightgen_pb2.GensparkSummary(title="Analysis Summary", text="Summary unavailable.")

    conversation_text = _build_conversation_text(turns)
    prompt = _SUMMARY_PROMPT_BASE + conversation_text

    client = genai.Client(api_key=api_key)
    model = cfg["gemini"]["model"]
    print(f"\n[Summary] Querying {model} for structured summary...")

    response = client.models.generate_content(
        model=model,
        contents=prompt,
        config=gtypes.GenerateContentConfig(
            response_mime_type="application/json",
        ),
    )

    try:
        data = json.loads(response.text)
    except Exception as e:
        print(f"Warning: could not parse summary JSON: {e}", file=sys.stderr)
        return insightgen_pb2.GensparkSummary(title="Analysis Summary", text=response.text)

    parameters = [
        insightgen_pb2.GensparkParameter(
            name=str(p.get("name", "")),
            value=str(p.get("value", "")),
            unit=str(p.get("unit", "")),
        )
        for p in data.get("parameters", [])
    ]
    return insightgen_pb2.GensparkSummary(
        title=data.get("title", "Analysis Summary"),
        text=data.get("text", ""),
        parameters=parameters,
    )


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    cfg_video = _CONFIG.get("video", {})

    parser = argparse.ArgumentParser(
        description="Analyze a .vis.pb recording with Gemini native video upload (agentic loop)"
    )
    parser.add_argument("recording", help="Path to .vis.pb recording file")
    parser.add_argument("--config", default=None,
                        help="Path to alternate config.yaml")
    parser.add_argument("--offset", type=float, default=None, metavar="SECONDS",
                        help=f"Start offset in seconds (default: {cfg_video.get('start_offset_seconds', 0)})")
    parser.add_argument("--max-duration", type=float, default=None, metavar="SECONDS",
                        help=f"Max duration in seconds, 0=no limit (default: {cfg_video.get('max_duration_seconds', 30)})")
    parser.add_argument("--width", type=int, default=None,
                        help=f"Output frame width (default: {cfg_video.get('width', 640)})")
    parser.add_argument("--height", type=int, default=None,
                        help=f"Output frame height (default: {cfg_video.get('height', 480)})")
    parser.add_argument("--fps", type=float, default=None,
                        help=f"Target output fps (default: {cfg_video.get('fps', 10)})")
    args = parser.parse_args()

    if args.config:
        with open(args.config) as f:
            cfg = yaml.safe_load(f)
    else:
        cfg = copy.deepcopy(_CONFIG)

    if args.offset is not None:
        cfg["video"]["start_offset_seconds"] = args.offset
    if args.max_duration is not None:
        cfg["video"]["max_duration_seconds"] = args.max_duration
    if args.width is not None:
        cfg["video"]["width"] = args.width
    if args.height is not None:
        cfg["video"]["height"] = args.height
    if args.fps is not None:
        cfg["video"]["fps"] = args.fps

    rec_path = Path(args.recording).resolve()
    if not rec_path.exists():
        print(f"Error: file not found: {rec_path}", file=sys.stderr)
        sys.exit(1)

    # Load frames
    print(f"Loading {rec_path.name}...")
    t0 = time.time()
    frames = _frame_io.read_file(rec_path)
    if not frames:
        print("Error: no frames in recording", file=sys.stderr)
        sys.exit(1)
    print(f"Loaded {len(frames)} frames in {time.time() - t0:.1f}s")

    # Select frames
    selected = select_frames(frames, cfg)
    if not selected:
        print("Error: no frames remain after applying offset/duration filter", file=sys.stderr)
        sys.exit(1)

    video_duration = len(selected) / cfg["video"]["fps"]
    print(
        f"Selected {len(selected)} frames "
        f"→ {video_duration:.1f}s at {cfg['video']['fps']} fps"
    )

    # Start the MCP server — crashes with a clear error if it fails or is missing tools
    runner = McpToolRunner(rec_path)
    runner.start()

    try:
        tmp_file = tempfile.NamedTemporaryFile(suffix=".mp4", delete=False)
        tmp_path = tmp_file.name
        tmp_file.close()
        try:
            build_video(selected, cfg, tmp_path)
            print(f"Temporary video written: {tmp_path}")
            turns = run_gemini(tmp_path, cfg, runner)
        finally:
            if Path(tmp_path).exists():
                Path(tmp_path).unlink()
                print(f"Deleted temp file: {tmp_path}")
    finally:
        runner.stop()
        print("[MCP] Server stopped.")

    # Print summary
    print("\n" + "=" * 60)
    print(f"Conversation complete — {len(turns)} turn(s)")
    print("=" * 60)
    for i, turn in enumerate(turns):
        print(f"\n--- Turn {i + 1} ---")
        if turn.text:
            print(turn.text[:800] + ("..." if len(turn.text) > 800 else ""))
        for tc in turn.tool_calls:
            print(f"  [tool] {tc.tool_name}({tc.arguments_json}) → {tc.result[:120]}")

    # Save proto
    rec_name = rec_path.name
    base_name = rec_name[: -len(".vis.pb")] if rec_name.endswith(".vis.pb") else rec_path.stem
    out_path = rec_path.parent / f"{base_name}.genspark.pb"

    summary = generate_summary(turns, cfg)
    print(f"\n[Summary] Title: {summary.title}")
    print(f"[Summary] Parameters: {[(p.name, p.value, p.unit) for p in summary.parameters]}")

    pb = insightgen_pb2.GensparkResponse(turns=turns, summary=summary)
    out_path.write_bytes(pb.SerializeToString())
    print(f"\nSaved to: {out_path}")


if __name__ == "__main__":
    main()
