from __future__ import annotations

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

from fastapi import FastAPI, File, Form, Header, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse

from .manager import JobManager, safe_filename, validate_arguments
from .registry import builtin_job_registry


SERVER_ROOT = Path(__file__).resolve().parents[1]
RUNNER_VERSION = "0.1.0"


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
            data_dir=Path(os.environ.get("RUNNER_DATA_DIR", "~/.bayesmech/runner")).expanduser(),
            max_workers=_env_int("RUNNER_MAX_WORKERS", 1),
            max_upload_bytes=_env_int("RUNNER_MAX_UPLOAD_BYTES", 50 * 1024 * 1024 * 1024),
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
) -> FastAPI:
    settings = settings or RunnerSettings.from_env()
    manager = manager or JobManager(
        settings.data_dir,
        builtin_job_registry(SERVER_ROOT),
        max_workers=settings.max_workers,
        max_upload_bytes=settings.max_upload_bytes,
        max_runtime_seconds=settings.max_runtime_seconds,
    )

    @asynccontextmanager
    async def lifespan(_application: FastAPI):
        yield
        manager.close()

    application = FastAPI(title="BayesMech Runner", version=RUNNER_VERSION, lifespan=lifespan)
    application.state.runner_settings = settings
    application.state.job_manager = manager

    @application.middleware("http")
    async def authenticate(request: Request, call_next):
        if request.url.path == "/health":
            return await call_next(request)
        if settings.token:
            authorization = request.headers.get("authorization", "")
            scheme, _, value = authorization.partition(" ")
            valid = scheme.lower() == "bearer" and hmac.compare_digest(value, settings.token)
            if not valid:
                return JSONResponse(status_code=401, content={"detail": "missing or invalid runner token"})
        else:
            client_host = request.client.host if request.client else None
            if not settings.allow_insecure_local or not _is_loopback(client_host):
                return JSONResponse(
                    status_code=503,
                    content={"detail": "RUNNER_TOKEN is required for non-loopback access"},
                )
        if request.method == "POST" and request.url.path.startswith("/api/v1/jobs"):
            content_length = request.headers.get("content-length", "")
            if content_length.isdigit() and int(content_length) > settings.max_upload_bytes:
                return JSONResponse(
                    status_code=413,
                    content={"detail": f"request exceeds the {settings.max_upload_bytes}-byte upload limit"},
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
            "disk_free_bytes": disk.free,
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
                }
            ],
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
            raise HTTPException(status_code=404, detail=f"unknown runner job: {job_type}") from None
        except (ValueError, FileNotFoundError, json.JSONDecodeError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        saved: list[dict[str, Any]] = []
        try:
            for upload in files:
                saved.append(manager.save_stream(prepared, upload.filename or "input.bin", upload.file))
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
            raise HTTPException(status_code=404, detail=f"unknown runner job: {job_type}") from None
        except (ValueError, FileNotFoundError, json.JSONDecodeError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        destination = prepared.input_dir / name
        expected_header = request.headers.get("content-length")
        expected_size = int(expected_header) if expected_header and expected_header.isdigit() else None
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
                        raise ValueError(f"input exceeds the {manager.max_upload_bytes}-byte upload limit")
                    output.write(chunk)
                    digest.update(chunk)
            if expected_size is not None and size != expected_size:
                raise ValueError(f"incomplete upload: expected {expected_size} bytes, received {size}")
            item = {"name": name, "size": size, "sha256": digest.hexdigest()}
            return manager.finish_upload(prepared, [item])
        except Exception as exc:
            destination.unlink(missing_ok=True)
            manager.fail_upload(prepared.job_id, str(exc))
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @application.get("/api/v1/jobs/{job_id}")
    async def job_status(job_id: str):
        try:
            return manager.public_state(job_id)
        except (KeyError, ValueError):
            raise HTTPException(status_code=404, detail="runner job not found") from None

    @application.post("/api/v1/jobs/{job_id}/cancel")
    async def cancel_job(job_id: str):
        try:
            return manager.cancel(job_id)
        except (KeyError, ValueError):
            raise HTTPException(status_code=404, detail="runner job not found") from None

    @application.get("/api/v1/jobs/{job_id}/artifacts/{artifact_id}")
    async def download_artifact(job_id: str, artifact_id: str):
        try:
            path, artifact = manager.artifact_path(job_id, artifact_id)
        except (KeyError, ValueError):
            raise HTTPException(status_code=404, detail="runner artifact not found") from None
        return FileResponse(
            path,
            filename=str(artifact["name"]),
            headers={"X-Artifact-SHA256": str(artifact["sha256"])},
        )

    application.mount("/api/v1/worldgen", LazyWorldgenApp())
    return application


app = create_app()
