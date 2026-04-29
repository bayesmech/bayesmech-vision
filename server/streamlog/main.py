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
GET /api/analysis/recordings/{name}   Discover generated analysis artifacts for a recording
GET /api/analysis/playback            Discover generated analysis artifacts for the current playback
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
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable
from urllib.parse import quote

import aiohttp
from dotenv import load_dotenv
from fastapi import (
    FastAPI,
    Request,
    Response,
    UploadFile,
    File,
    HTTPException,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

_server_root = Path(__file__).parent.parent
_project_root = _server_root.parent
sys.path.append(str(_project_root))
sys.path.append(str(_project_root / "proto"))
sys.path.append(str(_server_root))
from proto import perceiver_pb2
from proto import insightgen_pb2
from proto import idoslam_pb2
from proto import motioncap_pb2
from proto import reconstruction_pb2
from proto import segmentation_pb2
from proto import snookestown_pb2
from proto import pongtown_pb2

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

_seg_io = ProtoIO(segmentation_pb2.SegmentationResponse)
_motion_io = ProtoIO(motioncap_pb2.MotionCaptureResponse)
_recon_io = ProtoIO(reconstruction_pb2.ReconstructionResponse)
_snook_io = ProtoIO(snookestown_pb2.SnookerResponse)
_pong_io = ProtoIO(pongtown_pb2.PongtownResponse)

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
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
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
        logger.info(
            f"AR client disconnected: {addr}  (pushed {store.frame_count} frames)"
        )
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
        raise HTTPException(
            status_code=503, detail="OPENAI_API_KEY is not configured on the server"
        )

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
                logger.error(
                    "OpenAI transcription failed: %s %s",
                    openai_response.status,
                    response_text,
                )
                raise HTTPException(
                    status_code=502, detail="OpenAI transcription failed"
                )

    try:
        payload = json.loads(response_text)
    except Exception as exc:
        logger.error("Failed to parse OpenAI transcription response: %s", exc)
        raise HTTPException(
            status_code=502, detail="Invalid transcription response from OpenAI"
        ) from exc

    transcript = str(payload.get("text") or "").strip()
    if not transcript:
        raise HTTPException(
            status_code=502, detail="OpenAI returned an empty transcript"
        )

    return {"text": transcript}


_FILENAME_DATE_RE = re.compile(r"(\d{8}_\d{6})")
_RECORDING_FOLDER_RE = re.compile(r"^(\d{8}_\d{6})(?:_|$)")
_RECORDING_SUFFIX_ALIASES = {
    "segmentation.pb": ("seg.pb",),
    "motioncap.pb": ("motion.pb",),
    "motioncap.mp4": ("motion.mp4",),
}

_RANDOM_ADJECTIVES = [
    "Anxious",
    "Juicy",
    "Velvet",
    "Broken",
    "Silent",
    "Crimson",
    "Hollow",
    "Ancient",
    "Electric",
    "Frozen",
    "Golden",
    "Neon",
    "Rusty",
    "Twisted",
    "Wild",
    "Pale",
    "Vivid",
    "Bitter",
    "Calm",
    "Fierce",
    "Gentle",
    "Hungry",
    "Bright",
    "Murky",
    "Sharp",
    "Soft",
    "Sour",
    "Swift",
    "Dim",
    "Jagged",
]
_RANDOM_NOUNS = [
    "Deer",
    "Apple",
    "Falcon",
    "Glacier",
    "Lantern",
    "Mirage",
    "Phantom",
    "Raven",
    "Storm",
    "Thunder",
    "Volcano",
    "Whisper",
    "Ember",
    "Feather",
    "Horizon",
    "Marble",
    "Needle",
    "Pebble",
    "Shadow",
    "Thorn",
    "Arrow",
    "Beacon",
    "Crystal",
    "Dagger",
    "Forest",
    "Harbor",
    "Jungle",
    "Comet",
    "Dune",
    "Viper",
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


_SCENE_CLASSIFIED_RE = re.compile(
    r"Scene classified as ([a-z][a-z0-9-]+)", re.IGNORECASE
)


def _extract_genspark_metadata(
    genspark_path: Path,
) -> tuple[str | None, list[str], str]:
    """Returns (title, tags, preview_text)."""
    if not genspark_path.exists():
        return None, [], ""

    try:
        full = insightgen_pb2.GensparkResponse()
        full.ParseFromString(genspark_path.read_bytes())
    except Exception as exc:
        logger.warning(
            "Failed to parse genspark metadata from %s: %s", genspark_path, exc
        )
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
    return RECORDINGS_DIR / Path(file_name).name


def _recording_file_stem(file_name: str) -> str:
    safe_name = Path(file_name).name
    match = _RECORDING_FOLDER_RE.match(safe_name)
    if match and match.group(1) != safe_name:
        return match.group(1)
    return safe_name


def _recording_file_candidates(file_name: str, suffix: str) -> list[Path]:
    safe_name = Path(file_name).name
    folder = _recording_dir(safe_name)
    stems = [safe_name]
    timestamp_stem = _recording_file_stem(safe_name)
    if timestamp_stem != safe_name:
        stems.append(timestamp_stem)
    suffixes = (suffix, *_RECORDING_SUFFIX_ALIASES.get(suffix, ()))
    return [
        folder / f"{stem}.{candidate_suffix}"
        for candidate_suffix in suffixes
        for stem in stems
    ]


def _recording_file(file_name: str, suffix: str) -> Path:
    candidates = _recording_file_candidates(file_name, suffix)
    for path in candidates:
        if path.exists():
            return path
    safe_name = Path(file_name).name
    return _recording_dir(safe_name) / f"{_recording_file_stem(safe_name)}.{suffix}"


def _safe_recording_stem(file_name: str) -> str:
    safe_name = Path(file_name).name
    for suffix in (
        ".vis.pb",
        ".idoslam.pb",
        ".segmentation.pb",
        ".seg.pb",
        ".motioncap.pb",
        ".motion.pb",
        ".pongtown.pb",
    ):
        safe_name = safe_name.removesuffix(suffix)
    return safe_name


def _idoslam_path_for_request(file_name: str | None) -> Path | None:
    if file_name:
        safe_name = _safe_recording_stem(file_name)
        return _recording_file(safe_name, "idoslam.pb")
    if store.current_file is None:
        return None
    current = store.current_file
    stem = (
        current.name.removesuffix(".vis.pb")
        if current.name.endswith(".vis.pb")
        else current.stem
    )
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


def _load_chat_history_proto(
    chat_path: Path, file_name: str
) -> insightgen_pb2.ChatHistory:
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
            logger.warning(
                "Failed to build initial chat turn for %s: %s", file_name, exc
            )

    for turn in persisted.turns:
        if turn.timestamp_ns > since_timestamp_ns:
            response.turns.append(turn)

    return response


def _normalize_key(value: str) -> str:
    return value.strip().lower().replace("-", "_")


def _frame_timestamp_ns(message: Any) -> int | None:
    try:
        if not message.HasField("frame_identifier"):
            return None
        timestamp_ns = int(message.frame_identifier.timestamp_ns)
        return timestamp_ns if timestamp_ns > 0 else None
    except Exception:
        return None


def _frame_number(message: Any) -> int | None:
    try:
        if not message.HasField("frame_identifier"):
            return None
        return int(message.frame_identifier.frame_number)
    except Exception:
        return None


def _has_motioncap_summary(message: motioncap_pb2.MotionCaptureResponse) -> bool:
    return bool(
        message.tracks or message.segmentation_trajectories or message.total_frames
    )


def _has_snook_summary(message: snookestown_pb2.SnookerResponse) -> bool:
    return bool(message.tracks) or int(message.total_frames) > 0


def _has_pongtown_summary(message: pongtown_pb2.PongtownResponse) -> bool:
    return not message.HasField("frame_identifier")


@dataclass(frozen=True)
class AnalysisArtifactSpec:
    name: str
    title: str
    suffix: str
    media_type: str | None
    kind: str
    encoding: str
    aliases: tuple[str, ...] = ()
    is_directory: bool = False
    proto_message_type: str | None = None
    proto_io: ProtoIO | None = None
    sliceable: bool = False
    timestamp_getter: Callable[[Any], int | None] | None = None
    frame_number_getter: Callable[[Any], int | None] | None = None
    summary_predicate: Callable[[Any], bool] | None = None

    def path_for(self, recording_name: str) -> Path:
        return _recording_file(recording_name, self.suffix)


@dataclass(frozen=True)
class AnalysisViewSpec:
    name: str
    title: str
    media_type: str
    query_parameters: tuple[str, ...] = ()


@dataclass(frozen=True)
class AnalysisSpec:
    name: str
    title: str
    artifacts: tuple[AnalysisArtifactSpec, ...]
    aliases: tuple[str, ...] = ()
    views: tuple[AnalysisViewSpec, ...] = ()


_ANALYSIS_SPECS: tuple[AnalysisSpec, ...] = (
    AnalysisSpec(
        name="segmentation",
        title="Segmentation",
        artifacts=(
            AnalysisArtifactSpec(
                name="proto",
                title="Segmentation Protobuf",
                suffix="segmentation.pb",
                media_type="application/x-protobuf",
                kind="protobuf",
                encoding="length-delimited-protobuf",
                aliases=("pb",),
                proto_message_type="bayesmech.vision.SegmentationResponse",
                proto_io=_seg_io,
                sliceable=True,
                timestamp_getter=_frame_timestamp_ns,
                frame_number_getter=_frame_number,
            ),
        ),
    ),
    AnalysisSpec(
        name="motioncap",
        title="Motion Capture",
        artifacts=(
            AnalysisArtifactSpec(
                name="proto",
                title="Motion Capture Protobuf",
                suffix="motioncap.pb",
                media_type="application/x-protobuf",
                kind="protobuf",
                encoding="length-delimited-protobuf",
                aliases=("pb",),
                proto_message_type="bayesmech.vision.MotionCaptureResponse",
                proto_io=_motion_io,
                sliceable=True,
                timestamp_getter=_frame_timestamp_ns,
                frame_number_getter=_frame_number,
                summary_predicate=_has_motioncap_summary,
            ),
            AnalysisArtifactSpec(
                name="video",
                title="Motion Capture Video",
                suffix="motioncap.mp4",
                media_type="video/mp4",
                kind="video",
                encoding="binary",
            ),
        ),
        aliases=("motion_cap",),
        views=(
            AnalysisViewSpec(
                name="tracks",
                title="Track Legend",
                media_type="application/json",
            ),
            AnalysisViewSpec(
                name="heatmap",
                title="Rendered Heatmap",
                media_type="image/png",
                query_parameters=("frame_index", "timestamp_ns"),
            ),
        ),
    ),
    AnalysisSpec(
        name="idoslam",
        title="Localization and Mapping",
        artifacts=(
            AnalysisArtifactSpec(
                name="proto",
                title="IdoSlam Response",
                suffix="idoslam.pb",
                media_type="application/x-protobuf",
                kind="protobuf",
                encoding="length-delimited-protobuf",
                aliases=("pb", "slam"),
                proto_message_type="bayesmech.vision.IdoSlamResponse",
                proto_io=_idoslam_io,
            ),
        ),
        aliases=("slam", "localization_mapping"),
    ),
    AnalysisSpec(
        name="genspark",
        title="AI Analysis",
        artifacts=(
            AnalysisArtifactSpec(
                name="proto",
                title="Genspark Response",
                suffix="genspark.pb",
                media_type="application/x-protobuf",
                kind="protobuf",
                encoding="protobuf",
                aliases=("pb",),
                proto_message_type="bayesmech.vision.GensparkResponse",
            ),
        ),
        aliases=("insightgen",),
    ),
    AnalysisSpec(
        name="chat",
        title="Follow-up Chat",
        artifacts=(
            AnalysisArtifactSpec(
                name="proto",
                title="Chat History",
                suffix="chat.pb",
                media_type="application/x-protobuf",
                kind="protobuf",
                encoding="protobuf",
                aliases=("pb",),
                proto_message_type="bayesmech.vision.ChatHistory",
            ),
        ),
    ),
    AnalysisSpec(
        name="reconstruction",
        title="3D Reconstruction",
        artifacts=(
            AnalysisArtifactSpec(
                name="proto",
                title="Reconstruction Summary",
                suffix="recon.pb",
                media_type="application/x-protobuf",
                kind="protobuf",
                encoding="length-delimited-protobuf",
                aliases=("pb",),
                proto_message_type="bayesmech.vision.ReconstructionResponse",
                proto_io=_recon_io,
            ),
            AnalysisArtifactSpec(
                name="splat",
                title="Gaussian Splat PLY",
                suffix="splat.ply",
                media_type="application/octet-stream",
                kind="point-cloud",
                encoding="ply",
                aliases=("splat_ply",),
            ),
            AnalysisArtifactSpec(
                name="workspace",
                title="Reconstruction Workspace",
                suffix="recon",
                media_type=None,
                kind="workspace",
                encoding="directory",
                is_directory=True,
            ),
        ),
        aliases=("reconstruct",),
    ),
    AnalysisSpec(
        name="snookestown",
        title="Snookestown",
        artifacts=(
            AnalysisArtifactSpec(
                name="proto",
                title="Snookestown Protobuf",
                suffix="snook.pb",
                media_type="application/x-protobuf",
                kind="protobuf",
                encoding="length-delimited-protobuf",
                aliases=("pb",),
                proto_message_type="bayesmech.vision.SnookerResponse",
                proto_io=_snook_io,
                sliceable=True,
                timestamp_getter=_frame_timestamp_ns,
                frame_number_getter=_frame_number,
                summary_predicate=_has_snook_summary,
            ),
            AnalysisArtifactSpec(
                name="video",
                title="Top-down Video",
                suffix="snook.mp4",
                media_type="video/mp4",
                kind="video",
                encoding="binary",
            ),
            AnalysisArtifactSpec(
                name="segmentation_video",
                title="Segmentation Overlay Video",
                suffix="snook.segoverlay.mp4",
                media_type="video/mp4",
                kind="video",
                encoding="binary",
                aliases=("segoverlay", "overlay_video"),
            ),
            AnalysisArtifactSpec(
                name="workspace",
                title="Snookestown Workspace",
                suffix="snook",
                media_type=None,
                kind="workspace",
                encoding="directory",
                is_directory=True,
            ),
        ),
        aliases=("snooker",),
    ),
    AnalysisSpec(
        name="pongtown",
        title="Pongtown",
        artifacts=(
            AnalysisArtifactSpec(
                name="proto",
                title="Pongtown Protobuf",
                suffix="pongtown.pb",
                media_type="application/x-protobuf",
                kind="protobuf",
                encoding="length-delimited-protobuf",
                aliases=("pb",),
                proto_message_type="bayesmech.vision.PongtownResponse",
                proto_io=_pong_io,
                sliceable=True,
                timestamp_getter=_frame_timestamp_ns,
                frame_number_getter=_frame_number,
                summary_predicate=_has_pongtown_summary,
            ),
        ),
        aliases=("table_tennis", "ping_pong", "sport_understanding"),
    ),
)

_ANALYSIS_BY_KEY: dict[str, AnalysisSpec] = {}
for _spec in _ANALYSIS_SPECS:
    for _key in (_spec.name, *_spec.aliases):
        _ANALYSIS_BY_KEY[_normalize_key(_key)] = _spec


def _analysis_scope_prefix(recording_name: str | None = None) -> str:
    if recording_name is None:
        return "/api/analysis/playback"
    return f"/api/analysis/recordings/{quote(recording_name)}"


def _recording_exists(recording_name: str) -> bool:
    return _recording_file(recording_name, "vis.pb").exists()


def _ensure_recording_exists(recording_name: str) -> str:
    safe_name = Path(recording_name).name
    if not _recording_exists(safe_name):
        raise HTTPException(status_code=404, detail=f"Recording not found: {safe_name}")
    return safe_name


def _current_recording_name() -> str | None:
    current_file = store.current_file
    if current_file is None or store.source != "file":
        return None
    try:
        if current_file.parent.parent == RECORDINGS_DIR:
            return current_file.parent.name
    except Exception:
        pass
    return current_file.name.removesuffix(".vis.pb")


def _resolve_analysis_spec(analysis_name: str) -> AnalysisSpec:
    spec = _ANALYSIS_BY_KEY.get(_normalize_key(Path(analysis_name).name))
    if spec is None:
        raise HTTPException(
            status_code=404, detail=f"Unknown analysis: {analysis_name}"
        )
    return spec


def _resolve_artifact_spec(
    analysis_spec: AnalysisSpec, artifact_name: str
) -> AnalysisArtifactSpec:
    key = _normalize_key(Path(artifact_name).name)
    for artifact in analysis_spec.artifacts:
        if key == _normalize_key(artifact.name):
            return artifact
        if any(key == _normalize_key(alias) for alias in artifact.aliases):
            return artifact
    raise HTTPException(
        status_code=404,
        detail=f"Unknown artifact {artifact_name!r} for analysis {analysis_spec.name!r}",
    )


def _build_view_metadata(
    scope_prefix: str, analysis_spec: AnalysisSpec
) -> list[dict[str, Any]]:
    return [
        {
            "name": view.name,
            "title": view.title,
            "media_type": view.media_type,
            "query_parameters": list(view.query_parameters),
            "url": f"{scope_prefix}/analyses/{analysis_spec.name}/views/{view.name}",
        }
        for view in analysis_spec.views
    ]


def _build_artifact_metadata(
    recording_name: str,
    analysis_spec: AnalysisSpec,
    artifact_spec: AnalysisArtifactSpec,
    scope_prefix: str,
) -> dict[str, Any]:
    path = artifact_spec.path_for(recording_name)
    available = path.exists()
    metadata: dict[str, Any] = {
        "name": artifact_spec.name,
        "title": artifact_spec.title,
        "available": available,
        "kind": artifact_spec.kind,
        "encoding": artifact_spec.encoding,
        "media_type": artifact_spec.media_type,
        "is_directory": artifact_spec.is_directory,
        "downloadable": available and not artifact_spec.is_directory,
        "sliceable": artifact_spec.sliceable,
        "proto_message_type": artifact_spec.proto_message_type,
        "relative_path": str(path.relative_to(RECORDINGS_DIR)) if available else None,
        "size_bytes": path.stat().st_size if available and path.is_file() else None,
        "download_url": None,
        "records_url": None,
    }
    if available and not artifact_spec.is_directory:
        metadata["download_url"] = (
            f"{scope_prefix}/analyses/{analysis_spec.name}/artifacts/{artifact_spec.name}"
        )
    if available and artifact_spec.sliceable:
        metadata["records_url"] = (
            f"{scope_prefix}/analyses/{analysis_spec.name}/records?artifact={artifact_spec.name}"
        )
    if available and artifact_spec.is_directory:
        try:
            metadata["entry_count"] = sum(1 for _ in path.iterdir())
        except OSError:
            metadata["entry_count"] = None
    return metadata


def _build_analysis_metadata(
    recording_name: str,
    analysis_spec: AnalysisSpec,
    scope_prefix: str,
) -> dict[str, Any]:
    artifacts = [
        _build_artifact_metadata(
            recording_name, analysis_spec, artifact_spec, scope_prefix
        )
        for artifact_spec in analysis_spec.artifacts
    ]
    return {
        "name": analysis_spec.name,
        "title": analysis_spec.title,
        "available": any(artifact["available"] for artifact in artifacts),
        "artifacts": artifacts,
        "views": _build_view_metadata(scope_prefix, analysis_spec),
    }


def _build_recording_analysis_index(
    recording_name: str, *, playback: bool = False
) -> dict[str, Any]:
    scope_prefix = _analysis_scope_prefix(None if playback else recording_name)
    analyses = [
        _build_analysis_metadata(recording_name, analysis_spec, scope_prefix)
        for analysis_spec in _ANALYSIS_SPECS
    ]
    return {
        "recording": recording_name,
        "source": "playback" if playback else "recording",
        "analyses": analyses,
    }


def _encode_record_slice(
    artifact_spec: AnalysisArtifactSpec,
    path: Path,
    *,
    start_timestamp_ns: int | None = None,
    end_timestamp_ns: int | None = None,
    start_frame_number: int | None = None,
    end_frame_number: int | None = None,
    limit: int | None = None,
    include_summary: bool = False,
) -> tuple[bytes, int]:
    if artifact_spec.proto_io is None or not artifact_spec.sliceable:
        raise HTTPException(
            status_code=400,
            detail=f"Artifact {artifact_spec.name!r} does not support record slicing",
        )

    selected: list[Any] = []
    summaries: list[Any] = []
    summary_predicate = artifact_spec.summary_predicate

    for record in artifact_spec.proto_io.read_file(path):
        if summary_predicate is not None and summary_predicate(record):
            if include_summary:
                summaries.append(record)
            continue

        timestamp_ns = (
            artifact_spec.timestamp_getter(record)
            if artifact_spec.timestamp_getter is not None
            else None
        )
        frame_number = (
            artifact_spec.frame_number_getter(record)
            if artifact_spec.frame_number_getter is not None
            else None
        )

        if start_timestamp_ns is not None and (
            timestamp_ns is None or timestamp_ns < start_timestamp_ns
        ):
            continue
        if end_timestamp_ns is not None and (
            timestamp_ns is None or timestamp_ns > end_timestamp_ns
        ):
            continue
        if start_frame_number is not None and (
            frame_number is None or frame_number < start_frame_number
        ):
            continue
        if end_frame_number is not None and (
            frame_number is None or frame_number > end_frame_number
        ):
            continue

        selected.append(record)
        if limit is not None and len(selected) >= limit:
            break

    if include_summary:
        selected.extend(summaries)

    return artifact_spec.proto_io.encode(selected), len(selected)


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
                if not frame.HasField("frame_identifier") or not frame.HasField(
                    "rgb_frame"
                ):
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
        vis = _recording_file(d.name, "vis.pb")
        if not vis.exists():
            continue
        available_analyses = [
            analysis_spec.name
            for analysis_spec in _ANALYSIS_SPECS
            if any(
                artifact_spec.path_for(d.name).exists()
                for artifact_spec in analysis_spec.artifacts
            )
        ]
        recordings.append(
            {
                "name": d.name,
                "title": _parse_title(d.name),
                "size_mb": round(vis.stat().st_size / (1024**2), 2),
                "recorded_at": _parse_recording_timestamp(d.name, d.stat().st_mtime),
                "has_segmentation": _recording_file(d.name, "segmentation.pb").exists(),
                "has_idoslam": _recording_file(d.name, "idoslam.pb").exists(),
                "has_motioncap": (
                    _recording_file(d.name, "motioncap.pb").exists()
                    or _recording_file(d.name, "motioncap.mp4").exists()
                ),
                "has_pongtown": _recording_file(d.name, "pongtown.pb").exists(),
                "available_analyses": available_analyses,
                "analysis_url": f"/api/analysis/recordings/{quote(d.name)}",
            }
        )
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


def _resolve_recording_analysis(
    recording_name: str,
    analysis_name: str,
) -> tuple[str, AnalysisSpec]:
    safe_name = _ensure_recording_exists(recording_name)
    analysis_spec = _resolve_analysis_spec(analysis_name)
    return safe_name, analysis_spec


def _resolve_recording_artifact(
    recording_name: str,
    analysis_name: str,
    artifact_name: str,
) -> tuple[str, AnalysisSpec, AnalysisArtifactSpec, Path]:
    safe_name, analysis_spec = _resolve_recording_analysis(
        recording_name, analysis_name
    )
    artifact_spec = _resolve_artifact_spec(analysis_spec, artifact_name)
    path = artifact_spec.path_for(safe_name)
    if not path.exists():
        raise HTTPException(
            status_code=404,
            detail=(
                f"Artifact {artifact_spec.name!r} for analysis "
                f"{analysis_spec.name!r} is not available for recording {safe_name!r}"
            ),
        )
    return safe_name, analysis_spec, artifact_spec, path


def _resolve_playback_analysis(analysis_name: str) -> tuple[str, AnalysisSpec]:
    recording_name = _current_recording_name()
    if recording_name is None:
        raise HTTPException(
            status_code=404,
            detail="Playback is not currently bound to a recording",
        )
    return recording_name, _resolve_analysis_spec(analysis_name)


def _resolve_playback_artifact(
    analysis_name: str,
    artifact_name: str,
) -> tuple[str, AnalysisSpec, AnalysisArtifactSpec, Path]:
    recording_name, analysis_spec = _resolve_playback_analysis(analysis_name)
    artifact_spec = _resolve_artifact_spec(analysis_spec, artifact_name)
    path = artifact_spec.path_for(recording_name)
    if not path.exists():
        raise HTTPException(
            status_code=404,
            detail=(
                f"Artifact {artifact_spec.name!r} for analysis "
                f"{analysis_spec.name!r} is not available for the current playback"
            ),
        )
    return recording_name, analysis_spec, artifact_spec, path


def _download_artifact_response(
    path: Path, artifact_spec: AnalysisArtifactSpec
) -> FileResponse:
    if artifact_spec.is_directory:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Artifact {artifact_spec.name!r} is a directory workspace and "
                "cannot be downloaded directly"
            ),
        )
    return FileResponse(
        path,
        media_type=artifact_spec.media_type or "application/octet-stream",
        filename=path.name,
    )


def _resolve_motioncap_frame_index(
    motion_path: Path,
    *,
    frame_index: int | None,
    frame_number: int | None,
    timestamp_ns: int | None,
) -> int:
    provided = sum(
        value is not None for value in (frame_index, frame_number, timestamp_ns)
    )
    if provided > 1:
        raise HTTPException(
            status_code=400,
            detail="Provide only one of frame_index, frame_number, or timestamp_ns",
        )
    if provided == 0:
        raise HTTPException(
            status_code=400,
            detail="Provide one of frame_index, frame_number, or timestamp_ns",
        )
    if frame_index is not None:
        if frame_index < 0:
            raise HTTPException(status_code=400, detail="frame_index must be >= 0")
        return frame_index
    if frame_number is not None and frame_number < 0:
        raise HTTPException(status_code=400, detail="frame_number must be >= 0")

    heatmap_index = 0
    for record in _motion_io.read_file(motion_path):
        if _has_motioncap_summary(record):
            continue
        if frame_number is not None and int(
            record.frame_identifier.frame_number
        ) == int(frame_number):
            return heatmap_index
        if timestamp_ns is not None and int(
            record.frame_identifier.timestamp_ns
        ) == int(timestamp_ns):
            return heatmap_index
        heatmap_index += 1

    if frame_number is not None:
        raise HTTPException(
            status_code=404,
            detail=f"No motion capture frame found for frame number {frame_number}",
        )
    raise HTTPException(
        status_code=404,
        detail=f"No motion capture frame found for timestamp {timestamp_ns}",
    )


def _motioncap_layer() -> MotioncapVideoLayer:
    return MotioncapVideoLayer(
        use_prerendered_video=False,
        show_track_markers=False,
        show_track_labels=False,
    )


async def _motioncap_tracks_response(motion_path: Path | None) -> dict[str, Any]:
    if motion_path is None:
        return {"available": False, "tracks": [], "segmentation_trajectories": []}
    layer = _motioncap_layer()
    tracks = await asyncio.to_thread(layer.track_legend, motion_path)
    segmentation_trajectories = await asyncio.to_thread(
        layer.segmentation_trajectory_legend,
        motion_path,
    )
    return {
        "available": True,
        "tracks": tracks,
        "segmentation_trajectories": segmentation_trajectories,
    }


async def _motioncap_heatmap_response(
    motion_path: Path | None,
    *,
    frame_index: int | None,
    frame_number: int | None,
    timestamp_ns: int | None,
) -> Response:
    if motion_path is None:
        raise HTTPException(status_code=404, detail="Motion capture is not available")

    resolved_index = await asyncio.to_thread(
        _resolve_motioncap_frame_index,
        motion_path,
        frame_index=frame_index,
        frame_number=frame_number,
        timestamp_ns=timestamp_ns,
    )
    png_bytes = await asyncio.to_thread(
        _motioncap_layer().heatmap_png, motion_path, resolved_index
    )
    if png_bytes is None:
        raise HTTPException(
            status_code=404,
            detail=f"No motion capture heatmap found for frame index {resolved_index}",
        )
    return Response(content=png_bytes, media_type="image/png")


@app.get("/api/analysis/recordings/{recording_name}")
async def analysis_recording_index(recording_name: str):
    safe_name = _ensure_recording_exists(recording_name)
    return _build_recording_analysis_index(safe_name)


@app.get("/api/analysis/playback")
async def analysis_playback_index():
    recording_name = _current_recording_name()
    if recording_name is None:
        return {
            "recording": None,
            "source": store.source,
            "analyses": [],
        }
    payload = _build_recording_analysis_index(recording_name, playback=True)
    payload["source"] = store.source
    return payload


@app.get("/api/analysis/recordings/{recording_name}/analyses/{analysis_name}")
async def analysis_recording_detail(recording_name: str, analysis_name: str):
    safe_name, analysis_spec = _resolve_recording_analysis(
        recording_name, analysis_name
    )
    return _build_analysis_metadata(
        safe_name,
        analysis_spec,
        _analysis_scope_prefix(safe_name),
    )


@app.get("/api/analysis/playback/analyses/{analysis_name}")
async def analysis_playback_detail(analysis_name: str):
    recording_name, analysis_spec = _resolve_playback_analysis(analysis_name)
    return _build_analysis_metadata(
        recording_name,
        analysis_spec,
        _analysis_scope_prefix(),
    )


@app.get(
    "/api/analysis/recordings/{recording_name}/analyses/{analysis_name}/artifacts/{artifact_name}"
)
async def analysis_recording_artifact_download(
    recording_name: str,
    analysis_name: str,
    artifact_name: str,
):
    _, _, artifact_spec, path = _resolve_recording_artifact(
        recording_name, analysis_name, artifact_name
    )
    return _download_artifact_response(path, artifact_spec)


@app.get("/api/analysis/playback/analyses/{analysis_name}/artifacts/{artifact_name}")
async def analysis_playback_artifact_download(
    analysis_name: str,
    artifact_name: str,
):
    _, _, artifact_spec, path = _resolve_playback_artifact(analysis_name, artifact_name)
    return _download_artifact_response(path, artifact_spec)


@app.get("/api/analysis/recordings/{recording_name}/analyses/{analysis_name}/records")
async def analysis_recording_records(
    recording_name: str,
    analysis_name: str,
    artifact: str = "proto",
    start_timestamp_ns: int | None = None,
    end_timestamp_ns: int | None = None,
    start_frame_number: int | None = None,
    end_frame_number: int | None = None,
    limit: int | None = None,
    include_summary: bool = False,
):
    _, _, artifact_spec, path = _resolve_recording_artifact(
        recording_name, analysis_name, artifact
    )
    if limit is not None and limit < 1:
        raise HTTPException(status_code=400, detail="limit must be >= 1")
    if (
        start_timestamp_ns is not None
        and end_timestamp_ns is not None
        and start_timestamp_ns > end_timestamp_ns
    ):
        raise HTTPException(
            status_code=400,
            detail="start_timestamp_ns must be <= end_timestamp_ns",
        )
    if (
        start_frame_number is not None
        and end_frame_number is not None
        and start_frame_number > end_frame_number
    ):
        raise HTTPException(
            status_code=400,
            detail="start_frame_number must be <= end_frame_number",
        )

    payload, record_count = await asyncio.to_thread(
        _encode_record_slice,
        artifact_spec,
        path,
        start_timestamp_ns=start_timestamp_ns,
        end_timestamp_ns=end_timestamp_ns,
        start_frame_number=start_frame_number,
        end_frame_number=end_frame_number,
        limit=limit,
        include_summary=include_summary,
    )
    return Response(
        content=payload,
        media_type=artifact_spec.media_type or "application/octet-stream",
        headers={
            "X-Bayesmech-Record-Count": str(record_count),
            "X-Bayesmech-Encoding": artifact_spec.encoding,
        },
    )


@app.get("/api/analysis/playback/analyses/{analysis_name}/records")
async def analysis_playback_records(
    analysis_name: str,
    artifact: str = "proto",
    start_timestamp_ns: int | None = None,
    end_timestamp_ns: int | None = None,
    start_frame_number: int | None = None,
    end_frame_number: int | None = None,
    limit: int | None = None,
    include_summary: bool = False,
):
    _, _, artifact_spec, path = _resolve_playback_artifact(analysis_name, artifact)
    if limit is not None and limit < 1:
        raise HTTPException(status_code=400, detail="limit must be >= 1")
    if (
        start_timestamp_ns is not None
        and end_timestamp_ns is not None
        and start_timestamp_ns > end_timestamp_ns
    ):
        raise HTTPException(
            status_code=400,
            detail="start_timestamp_ns must be <= end_timestamp_ns",
        )
    if (
        start_frame_number is not None
        and end_frame_number is not None
        and start_frame_number > end_frame_number
    ):
        raise HTTPException(
            status_code=400,
            detail="start_frame_number must be <= end_frame_number",
        )

    payload, record_count = await asyncio.to_thread(
        _encode_record_slice,
        artifact_spec,
        path,
        start_timestamp_ns=start_timestamp_ns,
        end_timestamp_ns=end_timestamp_ns,
        start_frame_number=start_frame_number,
        end_frame_number=end_frame_number,
        limit=limit,
        include_summary=include_summary,
    )
    return Response(
        content=payload,
        media_type=artifact_spec.media_type or "application/octet-stream",
        headers={
            "X-Bayesmech-Record-Count": str(record_count),
            "X-Bayesmech-Encoding": artifact_spec.encoding,
        },
    )


@app.get("/api/analysis/recordings/{recording_name}/analyses/motioncap/views/tracks")
async def analysis_recording_motioncap_tracks(recording_name: str):
    safe_name = _ensure_recording_exists(recording_name)
    motion_path = _resolve_artifact_spec(
        _resolve_analysis_spec("motioncap"), "proto"
    ).path_for(safe_name)
    return await _motioncap_tracks_response(
        motion_path if motion_path.exists() else None
    )


@app.get("/api/analysis/playback/analyses/motioncap/views/tracks")
async def analysis_playback_motioncap_tracks():
    return await _motioncap_tracks_response(_current_motioncap_path())


@app.get("/api/analysis/recordings/{recording_name}/analyses/motioncap/views/heatmap")
async def analysis_recording_motioncap_heatmap(
    recording_name: str,
    frame_index: int | None = None,
    frame_number: int | None = None,
    timestamp_ns: int | None = None,
):
    safe_name = _ensure_recording_exists(recording_name)
    motion_path = _resolve_artifact_spec(
        _resolve_analysis_spec("motioncap"), "proto"
    ).path_for(safe_name)
    return await _motioncap_heatmap_response(
        motion_path if motion_path.exists() else None,
        frame_index=frame_index,
        frame_number=frame_number,
        timestamp_ns=timestamp_ns,
    )


@app.get("/api/analysis/playback/analyses/motioncap/views/heatmap")
async def analysis_playback_motioncap_heatmap(
    frame_index: int | None = None,
    frame_number: int | None = None,
    timestamp_ns: int | None = None,
):
    return await _motioncap_heatmap_response(
        _current_motioncap_path(),
        frame_index=frame_index,
        frame_number=frame_number,
        timestamp_ns=timestamp_ns,
    )


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
        vis = _recording_file(d.name, "vis.pb")
        if not vis.exists():
            continue
        base_name = d.name
        thumb = _extract_thumbnail(vis)
        genspark_path = _recording_file(base_name, "genspark.pb")
        chat_path = _recording_file(base_name, "chat.pb")
        genspark_title, genspark_tags, preview_text = _extract_genspark_metadata(
            genspark_path
        )
        parsed = _parse_title(base_name)
        title = genspark_title or (
            parsed if parsed != base_name else _generate_random_name(base_name)
        )
        chat_count = 0
        if chat_path.exists():
            try:
                history = _load_chat_history_proto(chat_path, base_name)
                chat_count = len(history.turns)
            except Exception:
                pass
        dl = insightgen_pb2.DataList(
            file_name=base_name,
            is_segmentation_available=_recording_file(
                base_name, "segmentation.pb"
            ).exists(),
            is_genspark_available=genspark_path.exists(),
            is_motioncap_available=(
                _recording_file(base_name, "motioncap.pb").exists()
                or _recording_file(base_name, "motioncap.mp4").exists()
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
    return Response(
        content=resp.SerializeToString(), media_type="application/x-protobuf"
    )


@app.get("/api/insightgen/insight")
async def insightgen_insight(file: str):
    """Return the GensparkSummary for a specific recording (file=YYYYMMDD_HHMMSS)."""
    # Sanitise: only allow the base filename, no path traversal
    safe_name = Path(file).name
    pb_path = _recording_file(safe_name, "genspark.pb")
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
    vis_path = _recording_file(safe_name, "vis.pb")
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
        genspark_path = _recording_file(safe_name, "genspark.pb")
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
            lambda: layer_obj.render_frames(
                vis_path, frame_indices, jpeg_quality, max_width
            ),
        )

        # Build response proto
        resp = insightgen_pb2.InsightVideoResponse(fps=fps)
        for ts, jpeg in rendered:
            resp.frames.append(
                insightgen_pb2.VideoFrame(timestamp_ns=ts, jpeg_data=jpeg)
            )
        resp.segments.extend(highlights)

        logger.info(
            f"insightgen_video: {safe_name} layer={layer_key} "
            f"frames={len(rendered)} fps={fps} highlights={len(highlights)}"
        )
        return Response(
            content=resp.SerializeToString(), media_type="application/x-protobuf"
        )

    except Exception as exc:
        logger.error(
            f"insightgen_video error ({safe_name}, {layer}): {exc}", exc_info=True
        )
        return Response(status_code=500)


@app.get("/api/insightgen/chat")
async def insightgen_chat_history(file: str, since_timestamp_ns: int = 0):
    """Return the chat-thread delta for a recording since the given timestamp."""
    safe_name = Path(file).name
    genspark_path = _recording_file(safe_name, "genspark.pb")
    chat_path = _recording_file(safe_name, "chat.pb")
    history = _build_chat_history_delta(
        safe_name, genspark_path, chat_path, since_timestamp_ns
    )
    return Response(
        content=history.SerializeToString(), media_type="application/x-protobuf"
    )


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
    genspark_path = _recording_file(safe_name, "genspark.pb")
    if not genspark_path.exists():
        raise HTTPException(
            status_code=404, detail="No analysis found for this recording"
        )

    try:
        (
            response_text,
            session_id,
            user_turn,
            model_turn,
        ) = await asyncio.get_event_loop().run_in_executor(
            None,
            lambda: chat_manager.handle_message(
                genspark_path,
                safe_name,
                message,
                session_id,
                chat_path=_recording_file(safe_name, "chat.pb"),
            ),
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
    path = _recording_file(safe_name, "vis.pb")
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
        frame_keys = {
            (f.frame_identifier.timestamp_ns, f.frame_identifier.frame_number)
            for f in frames
        }
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


def _current_motioncap_path() -> Path | None:
    recording_name = _current_recording_name()
    if recording_name is None:
        return None
    motion_path = _resolve_artifact_spec(
        _resolve_analysis_spec("motioncap"), "proto"
    ).path_for(recording_name)
    return motion_path if motion_path.exists() else None


@app.get("/api/playback/motioncap/tracks")
async def playback_motioncap_tracks():
    return await _motioncap_tracks_response(_current_motioncap_path())


@app.get("/api/playback/motioncap/heatmap")
async def playback_motioncap_heatmap(index: int):
    return await _motioncap_heatmap_response(
        _current_motioncap_path(),
        frame_index=index,
        frame_number=None,
        timestamp_ns=None,
    )


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
        frame_keys = {
            (f.frame_identifier.timestamp_ns, f.frame_identifier.frame_number)
            for f in frames
        }
        ann_keys = set(annotator._annotations.keys())
        overlap = frame_keys & ann_keys
        logger.info(
            f"DIAG {count} frames, {ann_count} annotations, {len(overlap)} keys overlap"
        )
    else:
        logger.info(f"DIAG {count} frames loaded, {ann_count} annotations loaded")

    annotator.annotate_recording(frames)

    return {
        "status": "uploaded_and_playing",
        "name": recording_name,
        "size": len(content),
        "frames": count,
    }


# ── Static files ──────────────────────────────────────────────────────────────

_dashboard_dist = _project_root / "analysis" / "dashboard" / "dist"
if _dashboard_dist.exists():
    app.mount(
        "/", StaticFiles(directory=str(_dashboard_dist), html=True), name="dashboard"
    )
else:
    logger.warning(
        f"Dashboard build not found at {_dashboard_dist}. Run 'npm run build' in dashboard/"
    )

# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        app,
        host=config["server"]["host"],
        port=config["server"]["port"],
        log_level="info",
    )
