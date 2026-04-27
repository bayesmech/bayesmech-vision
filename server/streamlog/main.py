"""
BayesMech Vision Server

Endpoints
---------
WS  /ar-stream               Android device -> push PerceiverDataFrame protos
WS  /ws/dashboard            Dashboard <- binary protobuf stream + annotations
GET /api/health              Server status
GET /api/stream              FrameStore stats
GET /api/recordings          List saved .pb recordings
GET /api/idoslam             Return IdoSlamResponse for a recording
POST /api/playback/start     Load a recording into the store
POST /api/playback/stop      Stop active replay
GET /api/playback/status     Replay status
POST /api/upload_recording   Upload .pb file and start replay
POST /api/transcribe         Proxy audio transcription to OpenAI using server credentials
POST /api/insightgen/recordings  List recordings (protobuf, with thumbnails)
GET  /api/insightgen/insight     Return GensparkSummary for a recording
GET  /api/insightgen/video       Return InsightVideoResponse (JPEG frames) for a recording
GET  /api/insightgen/chat        Return ChatHistory delta proto for a recording since a timestamp
POST /api/insightgen/chat        Follow-up chat with Gemini (bootstrapped from analysis)
/   (static)                React dashboard (dashboard/dist/)
"""

import asyncio
import json
import logging
import os
import re
import struct
import sys
import time
import yaml
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path

import aiohttp
from dotenv import load_dotenv
from fastapi import FastAPI, Request, Response, UploadFile, File, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

_server_root = Path(__file__).parent.parent
_project_root = _server_root.parent
sys.path.append(str(_project_root))
sys.path.append(str(_project_root / "proto"))
sys.path.append(str(_server_root))
from proto import perceiver_pb2
from proto import insightgen_pb2
from proto import idoslam_pb2

from streamlog.frame_store import FrameStore
from streamlog.annotator import Annotator
from streamlog.dashboard_bridge import DashboardBridge
from streamlog.video_layers import LAYER_REGISTRY, MotioncapVideoLayer
from streamlog.highlight_clipper import extract_highlights, clip_frame_indices
from streamlog.chat_manager import ChatManager
from streamlog.protoio import ProtoIO

# ── Logging ───────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(name)-20s  %(levelname)s  %(message)s",
)
logger = logging.getLogger(__name__)

# ── Config ────────────────────────────────────────────────────────────────────

load_dotenv(_project_root / ".env")

_config_path = Path(__file__).parent / "config.yaml"
with open(_config_path) as _f:
    config = yaml.safe_load(_f)

RECORDINGS_DIR = _project_root / "recordings"
RECORDINGS_DIR.mkdir(exist_ok=True)

# ── Components ────────────────────────────────────────────────────────────────

store = FrameStore()
annotator = Annotator()
bridge = DashboardBridge(store, annotator)
_idoslam_io = ProtoIO(idoslam_pb2.IdoSlamResponse)
_idoslam_io.FRAME_SIZE_LIMIT = 512 * 1024 * 1024

# Load genspark config for chat follow-up
_genspark_config_path = _server_root / "genspark" / "config.yaml"
_genspark_config: dict = {}
if _genspark_config_path.exists():
    with open(_genspark_config_path) as _f:
        _genspark_config = yaml.safe_load(_f) or {}
chat_manager = ChatManager(_genspark_config.get("gemini", {}), RECORDINGS_DIR)

# Wire: annotation results -> broadcast to dashboards
annotator.set_annotation_callback(bridge.broadcast_annotation)

# ── Application ───────────────────────────────────────────────────────────────

@asynccontextmanager
async def _lifespan(app: FastAPI):
    await annotator.connect()
    yield
    await annotator.close()


app = FastAPI(title="BayesMech Vision Server", version="3.0.0", lifespan=_lifespan)
app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"],
)


# ── WebSocket: AR stream (Android -> server) ─────────────────────────────────

@app.websocket("/ar-stream")
async def ar_stream_ws(websocket: WebSocket):
    addr = f"{websocket.client.host}:{websocket.client.port}"
    await websocket.accept()
    logger.info(f"AR client connected: {addr}")

    await store.stop_replay()
    store.clear()
    store.set_source("live")

    try:
        while True:
            raw = await websocket.receive_bytes()
            frame = perceiver_pb2.PerceiverDataFrame()
            try:
                frame.ParseFromString(raw)
            except Exception as exc:
                logger.warning(f"Proto parse error from {addr}: {exc}")
                continue
            store.push(frame)
    except WebSocketDisconnect:
        pass
    except Exception as exc:
        logger.error(f"AR stream error ({addr}): {exc}", exc_info=True)
    finally:
        logger.info(f"AR client disconnected: {addr}  (pushed {store.frame_count} frames)")
        store.set_source("none")


# ── WebSocket: Dashboard (server -> browser) ─────────────────────────────────

@app.websocket("/ws/dashboard")
async def dashboard_ws(websocket: WebSocket):
    await bridge.handle_connection(websocket)


# ── REST API ──────────────────────────────────────────────────────────────────

@app.get("/api/health")
async def health():
    return {
        "status": "running",
        "version": "3.0.0",
        "dashboard_connections": bridge.connection_count,
        **store.stats(),
    }


@app.get("/api/stream")
async def get_stream_stats():
    return store.stats()


@app.post("/api/transcribe")
async def transcribe_audio(file: UploadFile = File(...)):
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise HTTPException(status_code=503, detail="OPENAI_API_KEY is not configured on the server")

    audio_bytes = await file.read()
    if not audio_bytes:
        raise HTTPException(status_code=400, detail="Uploaded audio file is empty")

    filename = file.filename or "audio.m4a"
    content_type = file.content_type or "audio/mp4"

    form = aiohttp.FormData()
    form.add_field("file", audio_bytes, filename=filename, content_type=content_type)
    form.add_field("model", "gpt-4o-mini-transcribe")
    form.add_field("response_format", "json")

    async with aiohttp.ClientSession() as session:
        async with session.post(
            "https://api.openai.com/v1/audio/transcriptions",
            headers={"Authorization": f"Bearer {api_key}"},
            data=form,
        ) as openai_response:
            response_text = await openai_response.text()
            if openai_response.status >= 400:
                logger.error("OpenAI transcription failed: %s %s", openai_response.status, response_text)
                raise HTTPException(status_code=502, detail="OpenAI transcription failed")

    try:
        payload = json.loads(response_text)
    except Exception as exc:
        logger.error("Failed to parse OpenAI transcription response: %s", exc)
        raise HTTPException(status_code=502, detail="Invalid transcription response from OpenAI") from exc

    transcript = str(payload.get("text") or "").strip()
    if not transcript:
        raise HTTPException(status_code=502, detail="OpenAI returned an empty transcript")

    return {"text": transcript}


_FILENAME_DATE_RE = re.compile(r"(\d{8}_\d{6})")

_RANDOM_ADJECTIVES = [
    "Anxious", "Juicy", "Velvet", "Broken", "Silent", "Crimson", "Hollow",
    "Ancient", "Electric", "Frozen", "Golden", "Neon", "Rusty", "Twisted",
    "Wild", "Pale", "Vivid", "Bitter", "Calm", "Fierce", "Gentle", "Hungry",
    "Bright", "Murky", "Sharp", "Soft", "Sour", "Swift", "Dim", "Jagged",
]
_RANDOM_NOUNS = [
    "Deer", "Apple", "Falcon", "Glacier", "Lantern", "Mirage", "Phantom",
    "Raven", "Storm", "Thunder", "Volcano", "Whisper", "Ember", "Feather",
    "Horizon", "Marble", "Needle", "Pebble", "Shadow", "Thorn", "Arrow",
    "Beacon", "Crystal", "Dagger", "Forest", "Harbor", "Jungle", "Comet",
    "Dune", "Viper",
]

def _generate_random_name(seed: str) -> str:
    import hashlib
    h = int(hashlib.md5(seed.encode()).hexdigest(), 16)
    adj = _RANDOM_ADJECTIVES[h % len(_RANDOM_ADJECTIVES)]
    noun = _RANDOM_NOUNS[(h // len(_RANDOM_ADJECTIVES)) % len(_RANDOM_NOUNS)]
    return f"Unborn {adj} {noun}"


def _parse_recording_timestamp(name: str, fallback_mtime: float) -> float:
    """Extract epoch seconds from a YYYYMMDD_HHMMSS pattern in the filename."""
    m = _FILENAME_DATE_RE.search(name)
    if m:
        try:
            return datetime.strptime(m.group(1), "%Y%m%d_%H%M%S").timestamp()
        except ValueError:
            pass
    return fallback_mtime

def _parse_title(folder_name: str) -> str:
    """Extract human-readable title from a YYYYMMDD_HHMMSS_some_random_text folder name.

    Examples:
        "20260302_191856_karting_practice" -> "Karting practice"
        "20260302_191856"                  -> "20260302_191856"  (no suffix, return as-is)
    """
    parts = folder_name.split("_")
    if len(parts) > 2:
        text = " ".join(parts[2:])
        return text[0].upper() + text[1:] if text else folder_name
    return folder_name


def _format_scene_tag(scene_type: str) -> str:
    return scene_type.replace("-", " ").strip().title()


def _format_parameter_tag(param: insightgen_pb2.GensparkParameter) -> str | None:
    name = str(param.name or "").strip()
    value = str(param.value or "").strip()
    unit = str(param.unit or "").strip()
    if not name or not value:
        return None
    suffix = f" {unit}" if unit else ""
    return f"{name}: {value}{suffix}"


_SCENE_CLASSIFIED_RE = re.compile(r"Scene classified as ([a-z][a-z0-9-]+)", re.IGNORECASE)


def _extract_genspark_metadata(genspark_path: Path) -> tuple[str | None, list[str], str]:
    """Returns (title, tags, preview_text)."""
    if not genspark_path.exists():
        return None, [], ""

    try:
        full = insightgen_pb2.GensparkResponse()
        full.ParseFromString(genspark_path.read_bytes())
    except Exception as exc:
        logger.warning("Failed to parse genspark metadata from %s: %s", genspark_path, exc)
        return None, [], ""

    title = full.summary.title.strip() or None
    tags: list[str] = []

    for turn in full.turns:
        for tool_call in turn.tool_calls:
            if tool_call.tool_name != "scene_context":
                continue
            m = _SCENE_CLASSIFIED_RE.search(tool_call.result or "")
            if m:
                for part in m.group(1).split("-"):
                    part = part.strip()
                    if part:
                        tags.append(f"#{part}")

    deduped_tags: list[str] = []
    seen: set[str] = set()
    for tag in tags:
        normalized = tag.casefold()
        if normalized in seen:
            continue
        seen.add(normalized)
        deduped_tags.append(tag)

    # Build preview from summary text (strip markdown, take first non-empty line)
    raw = re.sub(r"^#+\s+", "", full.summary.text.strip(), flags=re.MULTILINE)
    raw = re.sub(r"\*+(.+?)\*+", r"\1", raw)
    preview = next((ln.strip() for ln in raw.splitlines() if ln.strip()), "")
    if len(preview) > 160:
        preview = preview[:160].rstrip() + "…"

    return title, deduped_tags, preview


def _recording_dir(file_name: str) -> Path:
    return RECORDINGS_DIR / file_name


def _recording_file(file_name: str, suffix: str) -> Path:
    return _recording_dir(file_name) / f"{file_name}.{suffix}"


def _safe_recording_stem(file_name: str) -> str:
    safe_name = Path(file_name).name
    for suffix in (".vis.pb", ".idoslam.pb", ".seg.pb"):
        safe_name = safe_name.removesuffix(suffix)
    return safe_name


def _idoslam_path_for_request(file_name: str | None) -> Path | None:
    if file_name:
        safe_name = _safe_recording_stem(file_name)
        return _recording_file(safe_name, "idoslam.pb")
    if store.current_file is None:
        return None
    current = store.current_file
    stem = current.name.removesuffix(".vis.pb") if current.name.endswith(".vis.pb") else current.stem
    return current.parent / f"{stem}.idoslam.pb"


def _build_summary_markdown(summary: insightgen_pb2.GensparkSummary) -> str:
    parts: list[str] = []
    if summary.title.strip():
        parts.append(f"## {summary.title.strip()}")
    if summary.text.strip():
        parts.append(summary.text.strip())
    if summary.parameters:
        table = ["| Parameter | Value | Unit |", "|:---|:---|:---|"]
        for param in summary.parameters:
            table.append(f"| {param.name} | {param.value} | {param.unit} |")
        parts.append("\n".join(table))
    return "\n\n".join(part for part in parts if part).strip()


def _thread_created_timestamp_ns(file_name: str, genspark_path: Path) -> int:
    try:
        if genspark_path.exists():
            return genspark_path.stat().st_mtime_ns
    except OSError:
        pass
    return int(_parse_recording_timestamp(file_name, time.time()) * 1_000_000_000)


def _load_chat_history_proto(chat_path: Path, file_name: str) -> insightgen_pb2.ChatHistory:
    history = insightgen_pb2.ChatHistory(file_name=file_name)
    source_path = chat_path
    if not source_path.exists():
        legacy_path = chat_path.parent.parent / f"{file_name}.chat.pb"
        source_path = legacy_path if legacy_path.exists() else chat_path
    if not source_path.exists():
        return history
    try:
        history.ParseFromString(source_path.read_bytes())
    except Exception as exc:
        logger.warning("Failed to parse chat history for %s: %s", file_name, exc)
        return insightgen_pb2.ChatHistory(file_name=file_name)
    if not history.file_name:
        history.file_name = file_name
    return history


def _build_chat_history_delta(
    file_name: str,
    genspark_path: Path,
    chat_path: Path,
    since_timestamp_ns: int,
) -> insightgen_pb2.ChatHistory:
    persisted = _load_chat_history_proto(chat_path, file_name)
    thread_created_timestamp_ns = _thread_created_timestamp_ns(file_name, genspark_path)

    response = insightgen_pb2.ChatHistory(
        file_name=file_name,
        gemini_cache_name=persisted.gemini_cache_name,
        thread_created_timestamp_ns=thread_created_timestamp_ns,
    )

    if since_timestamp_ns < thread_created_timestamp_ns and genspark_path.exists():
        try:
            full = insightgen_pb2.GensparkResponse()
            full.ParseFromString(genspark_path.read_bytes())
            summary_markdown = _build_summary_markdown(full.summary)
            if summary_markdown:
                response.initial_turn.CopyFrom(
                    insightgen_pb2.ChatTurn(
                        role="model",
                        text=summary_markdown,
                        timestamp_ns=thread_created_timestamp_ns,
                    )
                )
        except Exception as exc:
            logger.warning("Failed to build initial chat turn for %s: %s", file_name, exc)

    for turn in persisted.turns:
        if turn.timestamp_ns > since_timestamp_ns:
            response.turns.append(turn)

    return response

def _extract_thumbnail(pb_path: Path) -> bytes | None:
    """Return RGB bytes of the frame at ~20 s into the recording (best-effort)."""
    target_ns = 20 * 1_000_000_000
    start_ns = None
    last_frame_data = None
    try:
        with open(pb_path, "rb") as f:
            while True:
                header = f.read(4)
                if len(header) < 4:
                    break
                (length,) = struct.unpack(">I", header)
                if length == 0 or length > 10 * 1024 * 1024:
                    f.seek(f.tell() - 3)
                    continue
                data = f.read(length)
                if len(data) < length:
                    break
                frame = perceiver_pb2.PerceiverDataFrame()
                try:
                    frame.ParseFromString(data)
                except Exception:
                    continue
                if not frame.HasField("frame_identifier") or not frame.HasField("rgb_frame"):
                    continue
                ts = frame.frame_identifier.timestamp_ns
                if start_ns is None:
                    start_ns = ts
                last_frame_data = frame.rgb_frame.data
                if ts - start_ns >= target_ns:
                    return last_frame_data
    except Exception as e:
        logger.warning(f"Thumbnail extraction failed for {pb_path}: {e}")
    return last_frame_data


@app.get("/api/recordings")
async def list_recordings():
    dirs = sorted(
        [d for d in RECORDINGS_DIR.iterdir() if d.is_dir()],
        key=lambda d: d.stat().st_mtime,
        reverse=True,
    )
    recordings = []
    for d in dirs:
        vis = d / f"{d.name}.vis.pb"
        if not vis.exists():
            continue
        recordings.append({
            "name": d.name,
            "title": _parse_title(d.name),
            "size_mb": round(vis.stat().st_size / (1024 ** 2), 2),
            "recorded_at": _parse_recording_timestamp(d.name, d.stat().st_mtime),
            "has_segmentation": (d / f"{d.name}.seg.pb").exists(),
            "has_idoslam": (d / f"{d.name}.idoslam.pb").exists(),
        })
    return {"recordings": recordings}


@app.get("/api/idoslam")
async def idoslam_data(file: str | None = None):
    """
    Return the latest IdoSlamResponse for a recording.

    The on-disk checkpoint uses the repository's length-delimited protobuf
    format; this endpoint unwraps the latest record and returns the raw message
    bytes for direct browser-side protobuf decoding.
    """
    pb_path = _idoslam_path_for_request(file)
    if pb_path is None or not pb_path.exists():
        return Response(status_code=404)
    try:
        records = await asyncio.get_event_loop().run_in_executor(
            None,
            _idoslam_io.read_file,
            pb_path,
        )
        if not records:
            return Response(status_code=404)
        return Response(
            content=records[-1].SerializeToString(),
            media_type="application/x-protobuf",
        )
    except Exception as exc:
        logger.error("idoslam_data error for %s: %s", pb_path, exc, exc_info=True)
        return Response(status_code=500)


@app.post("/api/insightgen/recordings")
async def insightgen_list_recordings(request: Request):
    body = await request.body()
    req = insightgen_pb2.ListRecordingsRequest()
    if body:
        try:
            req.ParseFromString(body)
        except Exception:
            pass
    logger.info(f"insightgen: fetching recordings for user={req.username!r}")

    recordings_map: dict[str, insightgen_pb2.DataList] = {}

    for d in RECORDINGS_DIR.iterdir():
        if not d.is_dir():
            continue
        vis = d / f"{d.name}.vis.pb"
        if not vis.exists():
            continue
        base_name = d.name
        thumb = _extract_thumbnail(vis)
        genspark_path = d / f"{base_name}.genspark.pb"
        chat_path = d / f"{base_name}.chat.pb"
        genspark_title, genspark_tags, preview_text = _extract_genspark_metadata(genspark_path)
        parsed = _parse_title(base_name)
        title = genspark_title or (parsed if parsed != base_name else _generate_random_name(base_name))
        chat_count = 0
        if chat_path.exists():
            try:
                history = _load_chat_history_proto(chat_path, base_name)
                chat_count = len(history.turns)
            except Exception:
                pass
        dl = insightgen_pb2.DataList(
            file_name=base_name,
            is_segmentation_available=(d / f"{base_name}.seg.pb").exists(),
            is_genspark_available=genspark_path.exists(),
            is_motioncap_available=(
                (d / f"{base_name}.motion.pb").exists()
                or (d / f"{base_name}.motion.mp4").exists()
            ),
            title=title,
            chat_message_count=chat_count,
            preview_text=preview_text,
        )
        if thumb:
            dl.image_frame = thumb
        dl.tags.extend(genspark_tags)
        recordings_map[base_name] = dl

    resp = insightgen_pb2.ListRecordingsResponse()
    resp.recordings.extend(
        sorted(recordings_map.values(), key=lambda r: r.file_name, reverse=True)
    )
    return Response(content=resp.SerializeToString(), media_type="application/x-protobuf")


@app.get("/api/insightgen/insight")
async def insightgen_insight(file: str):
    """Return the GensparkSummary for a specific recording (file=YYYYMMDD_HHMMSS)."""
    # Sanitise: only allow the base filename, no path traversal
    safe_name = Path(file).name
    pb_path = RECORDINGS_DIR / safe_name / f"{safe_name}.genspark.pb"
    if not pb_path.exists():
        return Response(status_code=404)
    try:
        raw = pb_path.read_bytes()
        full = insightgen_pb2.GensparkResponse()
        full.ParseFromString(raw)
        summary_bytes = full.summary.SerializeToString()
        if not summary_bytes:
            # Summary not yet generated — run genspark/main.py to produce it
            return Response(status_code=404)
        return Response(content=summary_bytes, media_type="application/x-protobuf")
    except Exception as e:
        logger.error(f"insightgen_insight error for {safe_name}: {e}")
        return Response(status_code=500)


@app.get("/api/insightgen/video")
async def insightgen_video(file: str, layer: str = "raw"):
    """
    Return an InsightVideoResponse (protobuf) containing JPEG frames for a recording.

    Parameters
    ----------
    file  : base filename, e.g. 20260302_191856
    layer : "raw" (default) or "understanding" (motioncap overlay)

    The set of frames returned is controlled by insightgen.video.highlights_only
    in config.yaml.  When true, only frames within scene_emphasis windows are
    included; when false, all frames are returned.

    Both layers return the same frame indices so that Video / Understanding tabs
    in the app are frame-aligned and seek in sync.
    """
    import asyncio

    safe_name = Path(file).name
    vis_path = RECORDINGS_DIR / safe_name / f"{safe_name}.vis.pb"
    if not vis_path.exists():
        return Response(status_code=404)

    layer_key = layer.lower()
    if layer_key not in LAYER_REGISTRY:
        raise HTTPException(status_code=400, detail=f"Unknown layer: {layer!r}")

    video_cfg = config.get("insightgen", {}).get("video", {})
    highlights_only: bool = video_cfg.get("highlights_only", True)
    jpeg_quality: int = video_cfg.get("jpeg_quality", 75)
    max_width: int = video_cfg.get("max_width", 480)
    overlay_alpha: float = video_cfg.get("overlay_alpha", 0.5)
    tail_length: int = video_cfg.get("tail_length", 30)

    try:
        # Determine which frames to include
        from streamlog.protoio import ProtoIO
        from proto import perceiver_pb2 as _pb2
        _vis_io = ProtoIO(_pb2.PerceiverDataFrame)
        vis_frames = await asyncio.get_event_loop().run_in_executor(
            None, _vis_io.read_file, vis_path
        )

        timestamps_ns = [f.frame_identifier.timestamp_ns for f in vis_frames]

        # Compute FPS from timestamps (fall back to 30)
        fps = 30.0
        if len(timestamps_ns) >= 2:
            total_s = (timestamps_ns[-1] - timestamps_ns[0]) / 1e9
            if total_s > 0:
                fps = round((len(timestamps_ns) - 1) / total_s, 2)

        # Highlight clipping
        genspark_path = RECORDINGS_DIR / safe_name / f"{safe_name}.genspark.pb"
        highlights = extract_highlights(genspark_path)
        if highlights_only:
            frame_indices = clip_frame_indices(timestamps_ns, highlights)
        else:
            frame_indices = list(range(len(timestamps_ns)))

        # Build the video layer
        if layer_key == "understanding":
            layer_obj = MotioncapVideoLayer(
                overlay_alpha=overlay_alpha, tail_length=tail_length
            )
        else:
            layer_obj = LAYER_REGISTRY[layer_key]()

        rendered = await asyncio.get_event_loop().run_in_executor(
            None,
            lambda: layer_obj.render_frames(vis_path, frame_indices, jpeg_quality, max_width),
        )

        # Build response proto
        resp = insightgen_pb2.InsightVideoResponse(fps=fps)
        for ts, jpeg in rendered:
            resp.frames.append(insightgen_pb2.VideoFrame(timestamp_ns=ts, jpeg_data=jpeg))
        resp.segments.extend(highlights)

        logger.info(
            f"insightgen_video: {safe_name} layer={layer_key} "
            f"frames={len(rendered)} fps={fps} highlights={len(highlights)}"
        )
        return Response(content=resp.SerializeToString(), media_type="application/x-protobuf")

    except Exception as exc:
        logger.error(f"insightgen_video error ({safe_name}, {layer}): {exc}", exc_info=True)
        return Response(status_code=500)


@app.get("/api/insightgen/chat")
async def insightgen_chat_history(file: str, since_timestamp_ns: int = 0):
    """Return the chat-thread delta for a recording since the given timestamp."""
    safe_name = Path(file).name
    genspark_path = _recording_file(safe_name, "genspark.pb")
    chat_path = _recording_file(safe_name, "chat.pb")
    history = _build_chat_history_delta(safe_name, genspark_path, chat_path, since_timestamp_ns)
    return Response(content=history.SerializeToString(), media_type="application/x-protobuf")


@app.post("/api/insightgen/chat")
async def insightgen_chat(request: Request):
    """
    Follow-up chat with Gemini, bootstrapped from the existing analysis.

    Body (JSON): {"file": "20260302_191856", "message": "...", "session_id": "..."}
    Response (JSON): {"response": "...", "session_id": "..."}
    """
    body = await request.json()
    file_name = body.get("file", "")
    message = body.get("message", "")
    session_id = body.get("session_id")

    if not file_name or not message:
        raise HTTPException(status_code=400, detail="Missing file or message")

    safe_name = Path(file_name).name
    genspark_path = RECORDINGS_DIR / safe_name / f"{safe_name}.genspark.pb"
    if not genspark_path.exists():
        raise HTTPException(status_code=404, detail="No analysis found for this recording")

    try:
        response_text, session_id, user_turn, model_turn = await asyncio.get_event_loop().run_in_executor(
            None,
            lambda: chat_manager.handle_message(genspark_path, safe_name, message, session_id),
        )
        return {
            "response": response_text,
            "session_id": session_id,
            "user_timestamp_ns": user_turn.timestamp_ns,
            "response_timestamp_ns": model_turn.timestamp_ns,
        }
    except Exception as exc:
        logger.error(f"insightgen_chat error ({safe_name}): {exc}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(exc))


@app.post("/api/playback/start")
async def start_playback(request: dict):
    name = request.get("name")
    if not name:
        raise HTTPException(status_code=400, detail="Missing name")
    safe_name = Path(name).name  # prevent path traversal
    path = RECORDINGS_DIR / safe_name / f"{safe_name}.vis.pb"
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"Recording not found: {safe_name}")

    await store.stop_replay()
    count = store.load_recording(path)
    ann_count = annotator.load_annotations(path)

    # Diagnostic: compare frame keys vs annotation keys
    frames = store.all_frames()
    if frames and ann_count > 0:
        sample_frame = frames[0].frame_identifier
        sample_ann_key = next(iter(annotator._annotations))
        logger.info(
            f"DIAG frame[0] key: ts={sample_frame.timestamp_ns} fn={sample_frame.frame_number} | "
            f"annotation[0] key: ts={sample_ann_key[0]} fn={sample_ann_key[1]}"
        )
        frame_keys = {(f.frame_identifier.timestamp_ns, f.frame_identifier.frame_number) for f in frames}
        ann_keys = set(annotator._annotations.keys())
        overlap = frame_keys & ann_keys
        logger.info(
            f"DIAG {count} frames, {ann_count} annotations, {len(overlap)} keys overlap"
        )
    else:
        logger.info(f"DIAG {count} frames loaded, {ann_count} annotations loaded")

    annotator.annotate_recording(frames)

    return {"status": "started", "name": safe_name, "frames": count}


@app.post("/api/playback/stop")
async def stop_playback():
    await store.stop_replay()
    await annotator.stop()
    return {"status": "stopped"}


@app.post("/api/playback/live")
async def switch_to_live():
    await store.stop_replay()
    store.clear()
    store.set_source("live")
    await annotator.stop()
    return {"status": "live"}


@app.get("/api/playback/status")
async def playback_status():
    return {
        "is_replaying": store.is_replaying,
        "source": store.source,
    }


@app.post("/api/upload_recording")
async def upload_recording(file: UploadFile = File(...)):
    if not file.filename.endswith(".vis.pb"):
        raise HTTPException(status_code=400, detail="Expected a .vis.pb file")
    recording_name = file.filename.removesuffix(".vis.pb")
    folder = RECORDINGS_DIR / recording_name
    folder.mkdir(parents=True, exist_ok=True)
    dest = folder / file.filename
    content = await file.read()
    dest.write_bytes(content)
    logger.info(f"Uploaded {file.filename} → {dest} ({len(content) / 1024:.1f} KB)")

    count = store.load_recording(dest)
    ann_count = annotator.load_annotations(dest)

    frames = store.all_frames()
    if frames and ann_count > 0:
        sample_frame = frames[0].frame_identifier
        sample_ann_key = next(iter(annotator._annotations))
        logger.info(
            f"DIAG frame[0] key: ts={sample_frame.timestamp_ns} fn={sample_frame.frame_number} | "
            f"annotation[0] key: ts={sample_ann_key[0]} fn={sample_ann_key[1]}"
        )
        frame_keys = {(f.frame_identifier.timestamp_ns, f.frame_identifier.frame_number) for f in frames}
        ann_keys = set(annotator._annotations.keys())
        overlap = frame_keys & ann_keys
        logger.info(
            f"DIAG {count} frames, {ann_count} annotations, {len(overlap)} keys overlap"
        )
    else:
        logger.info(f"DIAG {count} frames loaded, {ann_count} annotations loaded")

    annotator.annotate_recording(frames)

    return {"status": "uploaded_and_playing", "name": recording_name, "size": len(content), "frames": count}


# ── Static files ──────────────────────────────────────────────────────────────

_dashboard_dist = _project_root / "analysis" / "dashboard" / "dist"
if _dashboard_dist.exists():
    app.mount("/", StaticFiles(directory=str(_dashboard_dist), html=True), name="dashboard")
else:
    logger.warning(f"Dashboard build not found at {_dashboard_dist}. Run 'npm run build' in dashboard/")

# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        app,
        host=config["server"]["host"],
        port=config["server"]["port"],
        log_level="info",
    )
