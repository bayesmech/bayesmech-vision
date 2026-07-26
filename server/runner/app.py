from __future__ import annotations

import asyncio
import hmac
import ipaddress
import json
import os
import platform
import shutil
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from fastapi import (
    FastAPI,
    File,
    Form,
    Header,
    HTTPException,
    Query,
    Request,
    UploadFile,
)
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse

from .gemma_jobs import GemmaJobManager
from .gpu_scheduler import gpu_scheduler
from .job_events import job_events
from .manager import JobManager, safe_filename, validate_arguments
from .mcp_server import create_mcp_server
from .registry import builtin_job_registry

SERVER_ROOT = Path(__file__).resolve().parents[1]
RUNNER_VERSION = "0.4.0"


def _env_bool(name: str, default: bool) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() not in {"0", "false", "no", "off"}


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


def _is_loopback(value: str | None) -> bool:
    if not value:
        return False
    if value.lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(value).is_loopback
    except ValueError:
        return False


@dataclass(frozen=True)
class RunnerSettings:
    token: str
    data_dir: Path
    max_workers: int
    max_upload_bytes: int
    max_runtime_seconds: int
    allow_insecure_local: bool

    @classmethod
    def from_env(cls) -> "RunnerSettings":
        return cls(
            token=os.environ.get("RUNNER_TOKEN", "").strip(),
            data_dir=Path(
                os.environ.get("RUNNER_DATA_DIR", "~/.bayesmech/runner")
            ).expanduser(),
            max_workers=_env_int("RUNNER_MAX_WORKERS", 1),
            max_upload_bytes=_env_int(
                "RUNNER_MAX_UPLOAD_BYTES", 50 * 1024 * 1024 * 1024
            ),
            max_runtime_seconds=_env_int("RUNNER_MAX_RUNTIME_SECONDS", 24 * 60 * 60),
            allow_insecure_local=_env_bool("RUNNER_ALLOW_INSECURE_LOCAL", True),
        )


class LazyWorldgenApp:
    """Load the CUDA-heavy VGGT application only on its first request."""

    def __init__(self) -> None:
        self._app = None

    def _load(self):
        if self._app is None:
            from worldgen.services import vggt_api

            # The runner middleware has already authenticated the request.
            # Avoid requiring a second, potentially different legacy token.
            vggt_api.API_TOKEN = ""
            self._app = vggt_api.app
        return self._app

    async def __call__(self, scope, receive, send):
        await self._load()(scope, receive, send)


def create_app(
    settings: RunnerSettings | None = None,
    manager: JobManager | None = None,
    gemma_manager: GemmaJobManager | None = None,
) -> FastAPI:
    settings = settings or RunnerSettings.from_env()
    os.environ.setdefault(
        "WORLDGEN_JOBS_DIR", str(settings.data_dir / "worldgen" / "vggt_jobs")
    )
    os.environ.setdefault(
        "WORLDGEN_SPLAT_JOBS_DIR",
        str(settings.data_dir / "worldgen" / "splat_jobs"),
    )
    manager = manager or JobManager(
        settings.data_dir,
        builtin_job_registry(SERVER_ROOT),
        max_workers=settings.max_workers,
        max_upload_bytes=settings.max_upload_bytes,
        max_runtime_seconds=settings.max_runtime_seconds,
    )
    gemma_manager = gemma_manager or GemmaJobManager(
        settings.data_dir,
        runner_token=settings.token,
        max_runtime_seconds=settings.max_runtime_seconds,
    )
    mcp_server = create_mcp_server(
        settings, manager, RUNNER_VERSION, gemma_manager=gemma_manager
    )
    mcp_app = mcp_server.http_app(path="/", json_response=True)

    @asynccontextmanager
    async def lifespan(application: FastAPI):
        try:
            async with mcp_app.lifespan(application):
                yield
        finally:
            gemma_manager.close()
            manager.close()

    application = FastAPI(
        title="BayesMech Runner", version=RUNNER_VERSION, lifespan=lifespan
    )
    application.state.runner_settings = settings
    application.state.job_manager = manager
    application.state.gemma_job_manager = gemma_manager

    @application.middleware("http")
    async def authenticate(request: Request, call_next):
        if request.url.path == "/health":
            return await call_next(request)
        if settings.token:
            authorization = request.headers.get("authorization", "")
            scheme, _, value = authorization.partition(" ")
            valid = scheme.lower() == "bearer" and hmac.compare_digest(
                value, settings.token
            )
            if not valid:
                return JSONResponse(
                    status_code=401,
                    content={"detail": "missing or invalid runner token"},
                )
        else:
            client_host = request.client.host if request.client else None
            if not settings.allow_insecure_local or not _is_loopback(client_host):
                return JSONResponse(
                    status_code=503,
                    content={
                        "detail": "RUNNER_TOKEN is required for non-loopback access"
                    },
                )
        if request.method == "POST" and (
            request.url.path.startswith("/api/v1/jobs")
            or request.url.path.startswith("/api/v1/agent/jobs")
        ):
            content_length = request.headers.get("content-length", "")
            if (
                content_length.isdigit()
                and int(content_length) > settings.max_upload_bytes
            ):
                return JSONResponse(
                    status_code=413,
                    content={
                        "detail": f"request exceeds the {settings.max_upload_bytes}-byte upload limit"
                    },
                )
        return await call_next(request)

    @application.get("/health")
    async def public_health():
        return {
            "ok": True,
            "service": "bayesmech-runner",
            "version": RUNNER_VERSION,
            "auth_required": bool(settings.token),
        }

    @application.get("/api/v1/health")
    async def detailed_health():
        disk = shutil.disk_usage(settings.data_dir)
        return {
            "ok": True,
            "service": "bayesmech-runner",
            "version": RUNNER_VERSION,
            "hostname": platform.node(),
            "auth_required": bool(settings.token),
            "max_workers": settings.max_workers,
            "jobs": len(manager.list_jobs(limit=500)),
            "gemma_jobs": len(gemma_manager.list_jobs(limit=500)),
            "disk_free_bytes": disk.free,
            "gpu_scheduler": gpu_scheduler.snapshot(),
        }

    @application.get("/api/v1/capabilities")
    async def capabilities():
        return {
            "runner_version": RUNNER_VERSION,
            "jobs": manager.capabilities(),
            "services": [
                {
                    "name": "worldgen",
                    "title": "World Modeling",
                    "endpoint": "/api/v1/worldgen",
                },
                {
                    "name": "gemma",
                    "title": "Gemma Video Agent",
                    "endpoint": "/api/v1/agent",
                },
            ],
            "mcp": {
                "transport": "streamable-http",
                "endpoint": "/mcp/",
            },
        }

    @application.get("/api/v1/jobs")
    async def list_jobs(limit: int = Query(default=100, ge=1, le=500)):
        return {"jobs": manager.list_jobs(limit)}

    @application.post("/api/v1/jobs", status_code=202)
    async def submit_multipart_job(
        job_type: str = Form(...),
        parameters: str = Form(default="{}"),
        files: list[UploadFile] = File(...),
    ):
        try:
            parsed = json.loads(parameters)
            if not isinstance(parsed, dict):
                raise ValueError("parameters must be a JSON object")
            parsed["arguments"] = validate_arguments(parsed.get("arguments"))
            prepared = manager.prepare(job_type, parsed)
        except KeyError:
            raise HTTPException(
                status_code=404, detail=f"unknown runner job: {job_type}"
            ) from None
        except (ValueError, FileNotFoundError, json.JSONDecodeError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        saved: list[dict[str, Any]] = []
        try:
            for upload in files:
                saved.append(
                    manager.save_stream(
                        prepared, upload.filename or "input.bin", upload.file
                    )
                )
            return manager.finish_upload(prepared, saved)
        except Exception as exc:
            manager.fail_upload(prepared.job_id, str(exc))
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        finally:
            for upload in files:
                await upload.close()

    @application.post("/api/v1/jobs/{job_type}/recording", status_code=202)
    async def submit_recording_job(
        job_type: str,
        request: Request,
        filename: str = Header(..., alias="X-Runner-Filename"),
        arguments: str = Query(default="[]"),
    ):
        try:
            parsed_arguments = json.loads(arguments)
            parameters = {"arguments": validate_arguments(parsed_arguments)}
            prepared = manager.prepare(job_type, parameters)
            name = safe_filename(filename)
        except KeyError:
            raise HTTPException(
                status_code=404, detail=f"unknown runner job: {job_type}"
            ) from None
        except (ValueError, FileNotFoundError, json.JSONDecodeError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        destination = prepared.input_dir / name
        expected_header = request.headers.get("content-length")
        expected_size = (
            int(expected_header)
            if expected_header and expected_header.isdigit()
            else None
        )
        size = 0
        import hashlib

        digest = hashlib.sha256()
        try:
            with destination.open("xb") as output:
                async for chunk in request.stream():
                    if not chunk:
                        continue
                    size += len(chunk)
                    if size > manager.max_upload_bytes:
                        raise ValueError(
                            f"input exceeds the {manager.max_upload_bytes}-byte upload limit"
                        )
                    output.write(chunk)
                    digest.update(chunk)
            if expected_size is not None and size != expected_size:
                raise ValueError(
                    f"incomplete upload: expected {expected_size} bytes, received {size}"
                )
            item = {"name": name, "size": size, "sha256": digest.hexdigest()}
            return manager.finish_upload(prepared, [item])
        except Exception as exc:
            destination.unlink(missing_ok=True)
            manager.fail_upload(prepared.job_id, str(exc))
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @application.get("/api/v1/jobs/events")
    async def stream_job_events(
        request: Request,
        after: int = Query(default=0, ge=0),
        last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
    ):
        try:
            cursor = max(after, int(last_event_id or 0))
        except ValueError:
            cursor = after

        async def events():
            nonlocal cursor
            if cursor == 0:
                for state in reversed(job_events.snapshot()):
                    revision = int(state.get("revision") or 0)
                    cursor = max(cursor, revision)
                    yield (
                        f"id: {revision}\n"
                        "event: job\n"
                        f"data: {json.dumps(state, separators=(',', ':'))}\n\n"
                    )
            while not await request.is_disconnected():
                pending = await asyncio.to_thread(
                    job_events.wait_for_events, cursor, 15.0
                )
                if not pending:
                    yield ": keepalive\n\n"
                    continue
                for state in pending:
                    revision = int(state.get("revision") or 0)
                    cursor = max(cursor, revision)
                    yield (
                        f"id: {revision}\n"
                        "event: job\n"
                        f"data: {json.dumps(state, separators=(',', ':'))}\n\n"
                    )

        return StreamingResponse(
            events(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    @application.get("/api/v1/jobs/state")
    async def background_job_state():
        return {"jobs": job_events.snapshot()}

    @application.get("/api/v1/gpu")
    async def gpu_state():
        return gpu_scheduler.snapshot()

    @application.post("/api/v1/agent/jobs", status_code=202)
    async def submit_gemma_job(
        frames: list[UploadFile] = File(...),
        message: str = Form(...),
        timestamps_sec: str = Form(default="[]"),
        history: str = Form(default="[]"),
        request_id: str = Form(default=""),
        chat_id: str = Form(default=""),
        recording_path: str = Form(default=""),
    ):
        if len(frames) > 32:
            raise HTTPException(
                status_code=400, detail="at most 32 sampled frames may be uploaded"
            )
        try:
            parsed_timestamps = json.loads(timestamps_sec)
            if not isinstance(parsed_timestamps, list):
                raise ValueError("timestamps_sec must be a JSON array")
            timestamps = [float(value) for value in parsed_timestamps]
            parsed_history = json.loads(history)
            if not isinstance(parsed_history, list):
                raise ValueError("history must be a JSON array")
            normalized_history: list[dict[str, str]] = []
            for item in parsed_history[-24:]:
                if not isinstance(item, dict):
                    raise ValueError("every history item must be an object")
                normalized_history.append(
                    {
                        "role": (
                            "assistant"
                            if str(item.get("role") or "") == "assistant"
                            else "user"
                        ),
                        "text": str(item.get("text") or ""),
                    }
                )
            if len(timestamps) != len(frames):
                raise ValueError(
                    "timestamps_sec must contain one value per uploaded frame"
                )

            frame_files: list[tuple[str, bytes]] = []
            total_bytes = 0
            for upload in frames:
                suffix = Path(upload.filename or "").suffix.lower()
                if suffix not in {".jpg", ".jpeg", ".png", ".webp"}:
                    suffix = ".jpg"
                payload = await upload.read(settings.max_upload_bytes + 1)
                total_bytes += len(payload)
                if total_bytes > settings.max_upload_bytes:
                    raise ValueError(
                        f"frames exceed the {settings.max_upload_bytes}-byte upload limit"
                    )
                if not payload:
                    raise ValueError("uploaded frames must not be empty")
                frame_files.append((suffix, payload))
            return gemma_manager.start(
                frame_files,
                timestamps,
                message,
                normalized_history,
                request_id=request_id,
                chat_id=chat_id,
                recording_path=recording_path,
            )
        except (ValueError, json.JSONDecodeError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        finally:
            for upload in frames:
                await upload.close()

    @application.get("/api/v1/agent/jobs")
    async def list_gemma_jobs(limit: int = Query(default=100, ge=1, le=500)):
        return {"jobs": gemma_manager.list_jobs(limit)}

    @application.get("/api/v1/agent/jobs/{job_id}")
    async def gemma_job_status(job_id: str):
        try:
            return gemma_manager.public_state(job_id)
        except (KeyError, ValueError):
            raise HTTPException(status_code=404, detail="Gemma job not found") from None

    @application.get("/api/v1/agent/jobs/{job_id}/result")
    async def gemma_job_result(job_id: str):
        path = gemma_manager.result_path(job_id)
        if path is None:
            state = gemma_manager.get(job_id)
            if state is None:
                raise HTTPException(status_code=404, detail="Gemma job not found")
            raise HTTPException(status_code=409, detail="Gemma result is not ready")
        return FileResponse(path, media_type="application/json")

    @application.get("/api/v1/jobs/{job_id}")
    async def job_status(job_id: str):
        try:
            return manager.public_state(job_id)
        except (KeyError, ValueError):
            raise HTTPException(
                status_code=404, detail="runner job not found"
            ) from None

    @application.post("/api/v1/jobs/{job_id}/cancel")
    async def cancel_job(job_id: str):
        try:
            return manager.cancel(job_id)
        except (KeyError, ValueError):
            raise HTTPException(
                status_code=404, detail="runner job not found"
            ) from None

    @application.get("/api/v1/jobs/{job_id}/artifacts/{artifact_id}")
    async def download_artifact(job_id: str, artifact_id: str):
        try:
            path, artifact = manager.artifact_path(job_id, artifact_id)
        except (KeyError, ValueError):
            raise HTTPException(
                status_code=404, detail="runner artifact not found"
            ) from None
        return FileResponse(
            path,
            filename=str(artifact["name"]),
            headers={"X-Artifact-SHA256": str(artifact["sha256"])},
        )

    application.mount("/api/v1/worldgen", LazyWorldgenApp())
    application.mount("/mcp", mcp_app)
    return application


app = create_app()
