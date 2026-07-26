from __future__ import annotations

import asyncio
import base64
import binascii
import hashlib
import io
import json
import math
import platform
import shutil
import tempfile
from pathlib import Path
from typing import Any, Literal

from fastmcp import FastMCP
from pydantic import BaseModel, Field

from .manager import JobManager, safe_filename, validate_arguments


class EncodedFile(BaseModel):
    """A file transported in an MCP JSON request."""

    filename: str = Field(
        description="Basename for the uploaded file, including its extension."
    )
    data_base64: str = Field(description="Standard base64-encoded file bytes.")


def _decode_files(files: list[EncodedFile], max_bytes: int) -> list[tuple[str, bytes]]:
    decoded: list[tuple[str, bytes]] = []
    total = 0
    seen: set[str] = set()
    for item in files:
        name = safe_filename(item.filename)
        if name in seen:
            raise ValueError(f"duplicate input filename: {name}")
        seen.add(name)
        try:
            payload = base64.b64decode(item.data_base64, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise ValueError(f"{name} is not valid base64") from exc
        total += len(payload)
        if total > max_bytes:
            raise ValueError(
                f"decoded inputs exceed the {max_bytes}-byte runner upload limit"
            )
        decoded.append((name, payload))
    if not decoded:
        raise ValueError("at least one input file is required")
    return decoded


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def create_mcp_server(
    settings: Any, manager: JobManager, version: str = "0.2.0"
) -> FastMCP:
    """Create the discoverable MCP facade over one runner instance."""

    mcp = FastMCP(
        "BayesMech Remote Runner",
        version=version,
        instructions=(
            "Run allow-listed BayesMech processing jobs and World Modeling on this machine. "
            "Use MCP for discovery, control, JSON results, and small base64 frame sets. For "
            "large videos or artifacts, use the streaming REST URL returned by the tools."
        ),
    )

    @mcp.tool()
    def runner_health() -> dict[str, Any]:
        """Check runner availability, capacity, storage, and authentication mode."""

        disk = shutil.disk_usage(settings.data_dir)
        return {
            "ok": True,
            "service": "bayesmech-runner",
            "version": version,
            "hostname": platform.node(),
            "auth_required": bool(settings.token),
            "max_workers": settings.max_workers,
            "jobs": len(manager.list_jobs(limit=500)),
            "disk_free_bytes": disk.free,
            "mcp_endpoint": "/mcp/",
        }

    @mcp.tool()
    def runner_capabilities() -> dict[str, Any]:
        """List allow-listed server jobs and the bulk-transfer REST routes."""

        return {
            "runner_version": version,
            "jobs": manager.capabilities(),
            "services": [
                {
                    "name": "worldgen",
                    "title": "World Modeling",
                    "rest_endpoint": "/api/v1/worldgen",
                    "mcp_tools": [
                        "worldgen_health",
                        "worldgen_reconstruct_frames",
                        "worldgen_list_jobs",
                        "worldgen_get_job",
                        "worldgen_splat_status",
                        "worldgen_splat_artifact",
                    ],
                }
            ],
            "bulk_transfer": {
                "submit_job": "POST /api/v1/jobs",
                "submit_recording": "POST /api/v1/jobs/{job_type}/recording",
                "download_artifact": "GET /api/v1/jobs/{job_id}/artifacts/{artifact_id}",
            },
        }

    @mcp.tool()
    def runner_list_jobs(limit: int = 100) -> dict[str, Any]:
        """List recent remote jobs, newest first."""

        if limit < 1 or limit > 500:
            raise ValueError("limit must be between 1 and 500")
        return {"jobs": manager.list_jobs(limit)}

    @mcp.tool()
    def runner_get_job(job_id: str) -> dict[str, Any]:
        """Read status, logs, inputs, and artifact metadata for a remote job."""

        try:
            return manager.public_state(job_id)
        except (KeyError, ValueError) as exc:
            raise ValueError("runner job not found") from exc

    @mcp.tool()
    def runner_cancel_job(job_id: str) -> dict[str, Any]:
        """Cancel a queued or running remote job."""

        try:
            return manager.cancel(job_id)
        except (KeyError, ValueError) as exc:
            raise ValueError("runner job not found") from exc

    @mcp.tool()
    def runner_submit_job(
        job_type: str,
        files: list[EncodedFile],
        arguments: list[str] | None = None,
        recording_filename: str | None = None,
    ) -> dict[str, Any]:
        """Submit an allow-listed server job with small base64-encoded inputs.

        For multi-gigabyte recordings, use the streaming POST routes reported by
        runner_capabilities instead of expanding the recording as base64.
        """

        decoded = _decode_files(files, manager.max_upload_bytes)
        parameters = {
            "arguments": validate_arguments(arguments),
            "recording": (
                safe_filename(recording_filename) if recording_filename else ""
            ),
        }
        try:
            prepared = manager.prepare(job_type, parameters)
        except KeyError as exc:
            raise ValueError(f"unknown runner job: {job_type}") from exc
        try:
            saved = [
                manager.save_stream(prepared, filename, io.BytesIO(payload))
                for filename, payload in decoded
            ]
            return manager.finish_upload(prepared, saved)
        except Exception as exc:
            manager.fail_upload(prepared.job_id, str(exc))
            raise

    @mcp.tool()
    def runner_artifact(
        job_id: str,
        artifact_id: str,
        include_base64: bool = False,
    ) -> dict[str, Any]:
        """Describe a job artifact and optionally include small artifact bytes.

        The returned download_url is the preferred route for large artifacts.
        include_base64 is rejected for files larger than 16 MiB.
        """

        try:
            path, artifact = manager.artifact_path(job_id, artifact_id)
        except (KeyError, ValueError) as exc:
            raise ValueError("runner artifact not found") from exc
        result = {
            **artifact,
            "download_url": f"/api/v1/jobs/{job_id}/artifacts/{artifact_id}",
            "media_type": "application/octet-stream",
        }
        if include_base64:
            if path.stat().st_size > 16 * 1024 * 1024:
                raise ValueError(
                    "artifact is larger than the 16 MiB MCP inline limit; use download_url"
                )
            result["data_base64"] = base64.b64encode(path.read_bytes()).decode("ascii")
        return result

    @mcp.tool()
    def worldgen_health() -> dict[str, Any]:
        """Check CUDA, the VGGT-Omega checkpoint, and model load state."""

        from worldgen.services import vggt_api

        health = dict(vggt_api.health())
        health["gaussian_splatting_available"] = False
        try:
            from gsplat.cuda._backend import _C

            _C
            health["gaussian_splatting_available"] = True
        except Exception as exc:
            health["gaussian_splatting_error"] = str(exc)
        return health

    @mcp.tool()
    async def worldgen_reconstruct_frames(
        frames: list[EncodedFile],
        fps: float | None = None,
        resolution: int = 512,
        preprocess_mode: Literal["balanced", "max_size"] = "balanced",
        confidence_threshold: float = 0.5,
        window: int = 0,
        max_points_per_frame: int = 20_000,
        start_splat: bool = False,
    ) -> dict[str, Any]:
        """Run VGGT-Omega on base64 image frames and optionally start Gaussian splatting.

        The result contains camera poses, point clouds, and, when requested, a
        splat job identifier that can be polled with worldgen_splat_status.
        """

        if len(frames) > 96:
            raise ValueError("at most 96 frames may be reconstructed in one MCP call")
        if fps is not None and (not math.isfinite(fps) or fps <= 0):
            raise ValueError("fps must be positive")
        if resolution <= 0 or resolution > 2048:
            raise ValueError("resolution must be between 1 and 2048")
        if confidence_threshold < 0 or not math.isfinite(confidence_threshold):
            raise ValueError("confidence_threshold must be finite and non-negative")
        if window < 0:
            raise ValueError("window must be non-negative")
        if max_points_per_frame < 0 or max_points_per_frame > 1_000_000:
            raise ValueError("max_points_per_frame must be between 0 and 1000000")

        decoded = _decode_files(frames, manager.max_upload_bytes)
        from worldgen.services import vggt_api

        temporary = Path(tempfile.mkdtemp(prefix="runner_mcp_vggt_"))
        try:
            frame_paths: list[Path] = []
            for index, (filename, payload) in enumerate(decoded):
                suffix = vggt_api._safe_suffix(filename, ".png")
                path = temporary / f"frame_{index:06d}{suffix}"
                path.write_bytes(payload)
                frame_paths.append(path)
            frame_indices = list(range(len(frame_paths)))
            timestamps = [
                index / fps if fps is not None else float("nan")
                for index in frame_indices
            ]
            response = await asyncio.to_thread(
                vggt_api._run_inference,
                frame_paths,
                frame_indices,
                timestamps,
                resolution=resolution,
                preprocess_mode=preprocess_mode,
                conf_thresh=confidence_threshold,
                window=window,
                max_points_per_frame=max_points_per_frame,
                response_format="json",
                start_splat=start_splat,
            )
            return json.loads(bytes(response.body))
        finally:
            shutil.rmtree(temporary, ignore_errors=True)

    @mcp.tool()
    def worldgen_splat_status(
        job_id: str, include_preview: bool = False
    ) -> dict[str, Any]:
        """Read Gaussian splat optimization progress and optional point preview."""

        from worldgen.services.splat_jobs import get_splat_job

        job = get_splat_job(job_id, include_preview=include_preview)
        if job is None:
            raise ValueError("Gaussian splat job not found")
        return job

    @mcp.tool()
    def worldgen_list_jobs(limit: int = 100) -> dict[str, Any]:
        """List VGGT and Gaussian Splatting jobs with their current progress."""

        if limit < 1 or limit > 500:
            raise ValueError("limit must be between 1 and 500")
        from worldgen.services.splat_jobs import list_splat_jobs
        from worldgen.services.vggt_jobs import list_vggt_jobs

        jobs = [*list_vggt_jobs(limit), *list_splat_jobs(limit)]
        jobs.sort(
            key=lambda state: float(state.get("created_at") or 0), reverse=True
        )
        return {"jobs": jobs[:limit]}

    @mcp.tool()
    def worldgen_get_job(job_id: str) -> dict[str, Any]:
        """Read live progress for one VGGT or Gaussian Splatting job."""

        from worldgen.services.splat_jobs import get_splat_job
        from worldgen.services.vggt_jobs import get_vggt_job

        job = (
            get_vggt_job(job_id)
            if job_id.startswith("vggt-")
            else get_splat_job(job_id, include_preview=False)
        )
        if job is None:
            raise ValueError("World Modeling job not found")
        return job

    @mcp.tool()
    def worldgen_splat_artifact(
        job_id: str,
        artifact_kind: Literal["ply", "preview"],
        include_base64: bool = False,
    ) -> dict[str, Any]:
        """Describe a completed splat PLY or preview and return its REST download URL."""

        from worldgen.services.splat_jobs import get_splat_job

        job = get_splat_job(job_id, include_preview=False)
        if job is None:
            raise ValueError("Gaussian splat job not found")
        field = "ply_path" if artifact_kind == "ply" else "preview_json_path"
        workspace = Path(str(job.get("workspace") or "")).resolve()
        raw_path = str(job.get(field) or "")
        path = Path(raw_path).resolve() if raw_path else None
        if path is None or workspace not in path.parents or not path.is_file():
            raise ValueError(
                f"Gaussian splat {artifact_kind} artifact is not available"
            )
        result = {
            "job_id": job_id,
            "artifact_kind": artifact_kind,
            "name": path.name,
            "size": path.stat().st_size,
            "sha256": _sha256(path),
            "media_type": (
                "application/octet-stream"
                if artifact_kind == "ply"
                else "application/json"
            ),
            "download_url": f"/api/v1/worldgen/splat/{job_id}/artifact/{artifact_kind}",
        }
        if include_base64:
            if path.stat().st_size > 16 * 1024 * 1024:
                raise ValueError(
                    "artifact is larger than the 16 MiB MCP inline limit; use download_url"
                )
            result["data_base64"] = base64.b64encode(path.read_bytes()).decode("ascii")
        return result

    return mcp
