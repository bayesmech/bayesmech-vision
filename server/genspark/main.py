#!/usr/bin/env python3
"""
Analyze a .vis.pb recording using a vision-capable AI model.

Supports three providers:
  - gemini: native video upload via Gemini Files API (best temporal understanding)
  - claude: evenly-sampled frames sent as base64 JPEG images via Anthropic API
  - openai: evenly-sampled frames sent as base64 JPEG images via OpenAI API

Usage (from project root):
    cd server
    uv run python genspark/main.py ../recordings/<name>.vis.pb
    uv run python genspark/main.py ../recordings/<name>.vis.pb --provider claude
    uv run python genspark/main.py ../recordings/<name>.vis.pb --provider openai \\
        --offset 5 --max-duration 20 --fps 8 --width 480 --height 360
"""

import argparse
import base64
import copy
import datetime
import os
import sys
import tempfile
import time
from pathlib import Path

# ── Path setup (follows motioncap/main.py convention) ────────────────────────
_server_root = Path(__file__).resolve().parent.parent
_project_root = _server_root.parent
sys.path.insert(0, str(_project_root))
sys.path.insert(0, str(_project_root / "proto"))
sys.path.insert(0, str(_server_root))

import cv2
import numpy as np
import yaml
from tqdm import tqdm

from proto import perceiver_pb2
from streamlog.protoio import ProtoIO

_frame_io = ProtoIO(perceiver_pb2.PerceiverDataFrame)

_config_path = Path(__file__).parent / "genspark_config.yaml"
with open(_config_path) as _f:
    _CONFIG = yaml.safe_load(_f)


# ── Frame decoding ────────────────────────────────────────────────────────────

def decode_frame_bgr(frame: perceiver_pb2.PerceiverDataFrame) -> np.ndarray:
    """Decode an ImageFrame to a numpy BGR array for cv2.VideoWriter."""
    img = frame.rgb_frame
    ImageFormat = perceiver_pb2.ImageFrame.ImageFormat
    if img.format == ImageFormat.JPEG:
        buf = np.frombuffer(img.data, dtype=np.uint8)
        return cv2.imdecode(buf, cv2.IMREAD_COLOR)  # BGR out of imdecode
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
    """
    Apply start offset, max duration, and fps subsampling.

    Uses timestamp_ns from frame_identifier for accurate timing.
    Returns the subset of PerceiverDataFrame protos to render.
    """
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

    # Subsample to target fps using nanosecond threshold advancement
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
    """
    Evenly sample up to max_frames from selected, returning JPEG bytes for each.

    Decodes and resizes each frame to the configured (width, height).
    """
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
    """Write selected frames as an MP4 to tmp_path using OpenCV VideoWriter."""
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


# ── Gemini ────────────────────────────────────────────────────────────────────

def run_gemini(tmp_path: str, cfg: dict) -> str:
    """
    Upload tmp_path to Gemini Files API, wait for ACTIVE, query, delete.

    Returns the model's response text.
    """
    from google import genai

    key_env = cfg["gemini"]["api_key_env"]
    api_key = os.environ.get(key_env)
    if not api_key:
        raise RuntimeError(
            f"Environment variable {key_env!r} is not set. "
            "Export your Gemini API key before running."
        )

    client = genai.Client(api_key=api_key)

    # 1. Upload video file
    print("Uploading video to Gemini Files API...")
    uploaded = client.files.upload(
        file=tmp_path,
        config={"mime_type": "video/mp4"},
    )
    print(f"  Upload accepted: {uploaded.name}  (state={uploaded.state})")

    # 2. Poll until ACTIVE
    while uploaded.state is None or uploaded.state.name != "ACTIVE":
        if uploaded.state is not None and uploaded.state.name == "FAILED":
            raise RuntimeError(
                f"Gemini file processing failed for {uploaded.name}"
            )
        print(f"  Waiting for file to become ACTIVE (current: {uploaded.state})...")
        time.sleep(5)
        uploaded = client.files.get(name=uploaded.name)

    print(f"  File is ACTIVE: {uploaded.name}")

    # 3. Load prompt
    prompt_path = Path(__file__).parent / cfg["gemini"]["prompt_file"]
    prompt_text = prompt_path.read_text(encoding="utf-8")

    # 4. Query model (video file before text prompt per Gemini best practice)
    model = cfg["gemini"]["model"]
    print(f"Querying {model}...")
    response = client.models.generate_content(
        model=model,
        contents=[uploaded, prompt_text],
    )

    # 5. Clean up uploaded file
    print(f"  Deleting uploaded file {uploaded.name}...")
    client.files.delete(name=uploaded.name)

    return response.text


# ── Claude ────────────────────────────────────────────────────────────────────

def run_claude(frame_jpegs: list, cfg: dict) -> str:
    """Send sampled frames as base64 JPEG images to the Anthropic API."""
    import anthropic

    key_env = cfg["claude"]["api_key_env"]
    api_key = os.environ.get(key_env)
    if not api_key:
        raise RuntimeError(
            f"Environment variable {key_env!r} is not set. "
            "Export your Anthropic API key before running."
        )

    prompt_path = Path(__file__).parent / cfg["claude"]["prompt_file"]
    prompt_text = prompt_path.read_text(encoding="utf-8")

    content = []
    for jpeg_bytes in frame_jpegs:
        content.append({
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": "image/jpeg",
                "data": base64.b64encode(jpeg_bytes).decode("utf-8"),
            },
        })
    content.append({"type": "text", "text": prompt_text})

    model = cfg["claude"]["model"]
    print(f"Querying {model} with {len(frame_jpegs)} frames...")
    client = anthropic.Anthropic(api_key=api_key)
    response = client.messages.create(
        model=model,
        max_tokens=4096,
        messages=[{"role": "user", "content": content}],
    )

    return response.content[0].text


# ── OpenAI ────────────────────────────────────────────────────────────────────

def run_openai(frame_jpegs: list, cfg: dict) -> str:
    """Send sampled frames as base64 JPEG images to the OpenAI API."""
    import openai

    key_env = cfg["openai"]["api_key_env"]
    api_key = os.environ.get(key_env)
    if not api_key:
        raise RuntimeError(
            f"Environment variable {key_env!r} is not set. "
            "Export your OpenAI API key before running."
        )

    prompt_path = Path(__file__).parent / cfg["openai"]["prompt_file"]
    prompt_text = prompt_path.read_text(encoding="utf-8")

    content = []
    for jpeg_bytes in frame_jpegs:
        b64 = base64.b64encode(jpeg_bytes).decode("utf-8")
        content.append({
            "type": "image_url",
            "image_url": {"url": f"data:image/jpeg;base64,{b64}"},
        })
    content.append({"type": "text", "text": prompt_text})

    model = cfg["openai"]["model"]
    print(f"Querying {model} with {len(frame_jpegs)} frames...")
    client = openai.OpenAI(api_key=api_key)
    response = client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": content}],
    )

    return response.choices[0].message.content


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    cfg_video = _CONFIG.get("video", {})
    default_provider = _CONFIG.get("provider", "gemini")

    parser = argparse.ArgumentParser(
        description="Analyze a .vis.pb recording with a vision AI model"
    )
    parser.add_argument("recording", help="Path to .vis.pb recording file")
    parser.add_argument(
        "--provider", choices=["gemini", "claude", "openai"], default=None,
        help=f"AI provider to use (default: {default_provider})"
    )
    parser.add_argument(
        "--config", default=None,
        help="Path to alternate genspark_config.yaml (default: built-in)"
    )
    parser.add_argument(
        "--offset", type=float, default=None,
        metavar="SECONDS",
        help=f"Start offset in seconds (default: {cfg_video.get('start_offset_seconds', 0)})"
    )
    parser.add_argument(
        "--max-duration", type=float, default=None,
        metavar="SECONDS",
        help=f"Max duration in seconds, 0=no limit (default: {cfg_video.get('max_duration_seconds', 30)})"
    )
    parser.add_argument(
        "--width", type=int, default=None,
        help=f"Output frame width (default: {cfg_video.get('width', 640)})"
    )
    parser.add_argument(
        "--height", type=int, default=None,
        help=f"Output frame height (default: {cfg_video.get('height', 480)})"
    )
    parser.add_argument(
        "--fps", type=float, default=None,
        help=f"Target output fps (default: {cfg_video.get('fps', 10)})"
    )
    args = parser.parse_args()

    # Load config
    if args.config:
        with open(args.config) as f:
            cfg = yaml.safe_load(f)
    else:
        cfg = copy.deepcopy(_CONFIG)

    # Apply CLI overrides
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

    # Run inference
    if provider == "gemini":
        tmp_file = tempfile.NamedTemporaryFile(suffix=".mp4", delete=False)
        tmp_path = tmp_file.name
        tmp_file.close()
        try:
            build_video(selected, cfg, tmp_path)
            print(f"Temporary video written: {tmp_path}")
            result = run_gemini(tmp_path, cfg)
        finally:
            if Path(tmp_path).exists():
                Path(tmp_path).unlink()
                print(f"Deleted temp file: {tmp_path}")

    elif provider == "claude":
        frame_jpegs = sample_frames_jpeg(selected, cfg["claude"]["max_frames"], cfg)
        result = run_claude(frame_jpegs, cfg)

    elif provider == "openai":
        frame_jpegs = sample_frames_jpeg(selected, cfg["openai"]["max_frames"], cfg)
        result = run_openai(frame_jpegs, cfg)

    else:
        print(f"Error: unknown provider {provider!r}", file=sys.stderr)
        sys.exit(1)

    # Print and save result
    print("\n" + "=" * 60)
    print(f"{provider.capitalize()} Analysis")
    print("=" * 60)
    print(result)

    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    log_path = rec_path.with_name(
        f"{rec_path.stem}.genspark.{provider}.{timestamp}.txt"
    )
    log_path.write_text(result, encoding="utf-8")
    print(f"\nResponse saved to: {log_path}")


if __name__ == "__main__":
    main()
