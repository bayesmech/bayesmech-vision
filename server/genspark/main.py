#!/usr/bin/env python3
"""
Analyze a .vis.pb recording using a vision-capable AI model (agentic multi-turn loop).

Supports three providers:
  - gemini: native video upload via Gemini Files API (best temporal understanding)
  - claude: evenly-sampled frames sent as base64 JPEG images via Anthropic API
  - openai: evenly-sampled frames sent as base64 JPEG images via OpenAI API

After the initial video analysis the model may call tools (scene_context,
scene_emphasis, get_motioncap_tracks).  Tool results are fed back and the
conversation continues until the model returns a response with no tool calls.
The full conversation is saved as a GensparkResponse proto.

Usage (from server/):
    uv run python genspark/main.py ../recordings/<name>.vis.pb
    uv run python genspark/main.py ../recordings/<name>.vis.pb --provider claude
    uv run python genspark/main.py ../recordings/<name>.vis.pb --provider openai \\
        --offset 5 --max-duration 20 --fps 8 --width 480 --height 360
"""

import argparse
import asyncio
import base64
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


# ── Tool definitions (provider-agnostic JSON schema) ─────────────────────────

TOOLS = [
    {
        "name": "scene_context",
        "description": (
            "Tag this recording with a scene type classification. "
            "Always call this once after identifying the scene type. "
            "Returns scene-specific follow-up analysis instructions."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "type": {
                    "type": "string",
                    "enum": ["sport-karting", "sport-running", "sport-chess", "experiment-pendulum"],
                    "description": "The scene category that best describes this recording.",
                }
            },
            "required": ["type"],
        },
    },
    {
        "name": "scene_emphasis",
        "description": (
            "Mark a temporal highlight segment within the recording. "
            "Each segment should be between 2 and 10 seconds long. "
            "Returns a confirmation string."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "start_time": {"type": "number", "description": "Start of the highlight in seconds (>= 0.0)."},
                "end_time":   {"type": "number", "description": "End of the highlight in seconds (> start_time, <= start_time + 10)."},
                "description": {"type": "string", "description": "What is happening and why this moment is notable."},
            },
            "required": ["start_time", "end_time", "description"],
        },
    },
    {
        "name": "get_motioncap_tracks",
        "description": (
            "Return motion tracking data for a time window. "
            "Track positions are (cx, cy) pixel coordinates (origin top-left). "
            "Use to analyze object motion: oscillation, trajectory, speed, etc."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "start_time": {"type": "number", "description": "Start of the window in seconds (>= 0.0)."},
                "end_time":   {"type": "number", "description": "End of the window in seconds (> start_time)."},
            },
            "required": ["start_time", "end_time"],
        },
    },
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

    REQUIRED_TOOLS = frozenset({"scene_context", "scene_emphasis", "get_motioncap_tracks"})

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


# ── Frame sampling for image-based providers ──────────────────────────────────

def sample_frames_jpeg(selected: list, max_frames: int, cfg: dict) -> list:
    w = cfg["video"]["width"]
    h = cfg["video"]["height"]
    n = len(selected)

    if n <= max_frames:
        indices = list(range(n))
    else:
        indices = [int(i * (n - 1) / (max_frames - 1)) for i in range(max_frames)]

    result = []
    with tqdm(total=len(indices), desc="Sampling frames", unit="frame") as bar:
        for i in indices:
            bgr = decode_frame_bgr(selected[i])
            resized = cv2.resize(bgr, (w, h), interpolation=cv2.INTER_AREA)
            _, buf = cv2.imencode(".jpg", resized, [cv2.IMWRITE_JPEG_QUALITY, 85])
            result.append(buf.tobytes())
            bar.update(1)

    return result


# ── Video assembly (Gemini only) ──────────────────────────────────────────────

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


# ── Claude (agentic multi-turn) ───────────────────────────────────────────────

def run_claude(frame_jpegs: list, cfg: dict, runner: McpToolRunner) -> list:
    """Send sampled frames, then run a multi-turn agentic loop with tool support."""
    import anthropic

    key_env = cfg["claude"]["api_key_env"]
    api_key = os.environ.get(key_env)
    if not api_key:
        raise RuntimeError(f"Environment variable {key_env!r} is not set.")

    prompt_text = (Path(__file__).parent / cfg["claude"]["prompt_file"]).read_text("utf-8")
    model = cfg["claude"]["model"]

    # Build tool schemas for Anthropic
    claude_tools = [
        {
            "name": t["name"],
            "description": t["description"],
            "input_schema": t["parameters"],
        }
        for t in TOOLS
    ]

    # Initial message content: images + prompt
    initial_content = []
    for jpeg_bytes in frame_jpegs:
        initial_content.append({
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": "image/jpeg",
                "data": base64.b64encode(jpeg_bytes).decode("utf-8"),
            },
        })
    initial_content.append({"type": "text", "text": prompt_text})

    messages = [{"role": "user", "content": initial_content}]
    client = anthropic.Anthropic(api_key=api_key)
    turns = []
    turn_num = 0

    while True:
        turn_num += 1
        print(f"\n[Turn {turn_num}] Querying {model} with {len(messages)} messages...")
        response = client.messages.create(
            model=model,
            max_tokens=4096,
            tools=claude_tools,
            messages=messages,
        )

        text_blocks = [b.text for b in response.content if b.type == "text"]
        tool_use_blocks = [b for b in response.content if b.type == "tool_use"]

        text = "\n".join(text_blocks)
        turn = insightgen_pb2.GensparkTurn(text=text)

        if text:
            print(text[:500] + ("..." if len(text) > 500 else ""))

        if response.stop_reason != "tool_use" or not tool_use_blocks:
            turns.append(turn)
            break

        # Add assistant turn to messages
        messages.append({"role": "assistant", "content": response.content})

        # Execute tools and build tool_result content
        tool_results = []
        for block in tool_use_blocks:
            args = block.input
            print(f"  [tool call] {block.name}({json.dumps(args)})")
            result = call_tool(runner, block.name, args)
            print(f"  [tool result] {result[:200]}")
            turn.tool_calls.append(insightgen_pb2.GensparkToolCall(
                tool_name=block.name,
                arguments_json=json.dumps(args),
                result=result,
            ))
            tool_results.append({
                "type": "tool_result",
                "tool_use_id": block.id,
                "content": result,
            })
        turns.append(turn)
        messages.append({"role": "user", "content": tool_results})

    return turns


# ── OpenAI (agentic multi-turn) ───────────────────────────────────────────────

def run_openai(frame_jpegs: list, cfg: dict, runner: McpToolRunner) -> list:
    """Send sampled frames, then run a multi-turn agentic loop with tool support."""
    import openai

    key_env = cfg["openai"]["api_key_env"]
    api_key = os.environ.get(key_env)
    if not api_key:
        raise RuntimeError(f"Environment variable {key_env!r} is not set.")

    prompt_text = (Path(__file__).parent / cfg["openai"]["prompt_file"]).read_text("utf-8")
    model = cfg["openai"]["model"]

    # Build tool schemas for OpenAI
    openai_tools = [
        {
            "type": "function",
            "function": {
                "name": t["name"],
                "description": t["description"],
                "parameters": t["parameters"],
            },
        }
        for t in TOOLS
    ]

    # Initial message
    initial_content = []
    for jpeg_bytes in frame_jpegs:
        b64 = base64.b64encode(jpeg_bytes).decode("utf-8")
        initial_content.append({
            "type": "image_url",
            "image_url": {"url": f"data:image/jpeg;base64,{b64}"},
        })
    initial_content.append({"type": "text", "text": prompt_text})

    messages = [{"role": "user", "content": initial_content}]
    client = openai.OpenAI(api_key=api_key)
    turns = []
    turn_num = 0

    while True:
        turn_num += 1
        print(f"\n[Turn {turn_num}] Querying {model} with {len(messages)} messages...")
        response = client.chat.completions.create(
            model=model,
            tools=openai_tools,
            messages=messages,
        )

        choice = response.choices[0]
        msg = choice.message
        text = msg.content or ""
        turn = insightgen_pb2.GensparkTurn(text=text)

        if text:
            print(text[:500] + ("..." if len(text) > 500 else ""))

        if choice.finish_reason != "tool_calls" or not msg.tool_calls:
            turns.append(turn)
            break

        # Add assistant message
        messages.append(msg)

        # Execute tools
        for tc in msg.tool_calls:
            args = json.loads(tc.function.arguments)
            print(f"  [tool call] {tc.function.name}({json.dumps(args)})")
            result = call_tool(runner, tc.function.name, args)
            print(f"  [tool result] {result[:200]}")
            turn.tool_calls.append(insightgen_pb2.GensparkToolCall(
                tool_name=tc.function.name,
                arguments_json=tc.function.arguments,
                result=result,
            ))
            messages.append({
                "role": "tool",
                "tool_call_id": tc.id,
                "content": result,
            })
        turns.append(turn)

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
            # Truncate long track data so the summary prompt doesn't balloon
            result = tc.result
            if tc.tool_name == "get_motioncap_tracks" and len(result) > 800:
                lines = result.splitlines()
                header = next((l for l in lines if l.startswith("Motion")), lines[0])
                result = header + f"\n  [...{len(lines) - 1} coordinate lines omitted...]"
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
    default_provider = _CONFIG.get("provider", "gemini")

    parser = argparse.ArgumentParser(
        description="Analyze a .vis.pb recording with a vision AI model (agentic loop)"
    )
    parser.add_argument("recording", help="Path to .vis.pb recording file")
    parser.add_argument(
        "--provider", choices=["gemini", "claude", "openai"], default=None,
        help=f"AI provider to use (default: {default_provider})"
    )
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

    provider = args.provider if args.provider is not None else cfg.get("provider", "gemini")
    cfg["provider"] = provider
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
        f"→ {video_duration:.1f}s at {cfg['video']['fps']} fps  [provider={provider}]"
    )

    # Start the MCP server — crashes with a clear error if it fails or is missing tools
    runner = McpToolRunner(rec_path)
    runner.start()

    try:
        # Run inference
        if provider == "gemini":
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

        elif provider == "claude":
            frame_jpegs = sample_frames_jpeg(selected, cfg["claude"]["max_frames"], cfg)
            turns = run_claude(frame_jpegs, cfg, runner)

        elif provider == "openai":
            frame_jpegs = sample_frames_jpeg(selected, cfg["openai"]["max_frames"], cfg)
            turns = run_openai(frame_jpegs, cfg, runner)

        else:
            print(f"Error: unknown provider {provider!r}", file=sys.stderr)
            sys.exit(1)

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
