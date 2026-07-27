from __future__ import annotations

import json
import gzip
import os
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Callable

from runner.job_events import publish_runner_job

ROOT = Path(__file__).resolve().parents[1]
JOBS_DIR = Path(
    os.environ.get("WORLDGEN_JOBS_DIR", str(ROOT / "outputs" / "vggt_jobs"))
)

_jobs: dict[str, dict[str, Any]] = {}
_jobs_lock = threading.Lock()
_result_bundle_lock = threading.Lock()
_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="worldgen-vggt")
RESULT_POINT_BUDGET = max(
    1, int(os.environ.get("WORLDGEN_RESULT_POINT_BUDGET", "1000000"))
)


def _job_path(job_id: str) -> Path:
    return JOBS_DIR / job_id


def _write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(f"{path.suffix}.tmp")
    temporary.write_text(json.dumps(data), encoding="utf-8")
    os.replace(temporary, path)


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _public_state(state: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in state.items() if key != "_future"}


def _update_job(job_id: str, **fields: Any) -> dict[str, Any]:
    with _jobs_lock:
        state = _jobs.setdefault(job_id, {"job_id": job_id})
        state.update(fields)
        state.update(
            job_id=job_id,
            id=job_id,
            type="vggt",
            title="VGGT Reconstruction",
            source="worldgen",
            updated_at=time.time(),
        )
        serializable = _public_state(state)
    _write_json(_job_path(job_id) / "status.json", serializable)
    publish_runner_job(serializable)
    return serializable


def get_vggt_job(job_id: str) -> dict[str, Any] | None:
    with _jobs_lock:
        state = _jobs.get(job_id)
        if state is not None:
            return _public_state(state)
    return _read_json(_job_path(job_id) / "status.json")


def list_vggt_jobs(limit: int = 100) -> list[dict[str, Any]]:
    states: dict[str, dict[str, Any]] = {}
    for path in JOBS_DIR.glob("vggt-*/status.json"):
        state = _read_json(path)
        if state and state.get("job_id"):
            states[str(state["job_id"])] = state
    with _jobs_lock:
        for job_id, state in _jobs.items():
            states[job_id] = _public_state(state)
    ordered = sorted(
        states.values(),
        key=lambda state: float(state.get("created_at") or 0),
        reverse=True,
    )
    return ordered[: max(1, min(int(limit), 500))]


def vggt_result_path(job_id: str) -> Path | None:
    state = get_vggt_job(job_id)
    if not state or state.get("status") != "complete":
        return None
    path = (_job_path(job_id) / "result.json").resolve()
    workspace = _job_path(job_id).resolve()
    if workspace not in path.parents or not path.is_file():
        return None
    return path


def _even_sample_indices(count: int, limit: int) -> list[int]:
    if count <= limit:
        return list(range(count))
    if limit <= 1:
        return [0]
    return [
        min(count - 1, round(index * (count - 1) / (limit - 1)))
        for index in range(limit)
    ]


def _write_gzip_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(f"{path.suffix}.tmp")
    temporary.write_bytes(
        gzip.compress(
            json.dumps(payload, separators=(",", ":")).encode("utf-8"),
            compresslevel=6,
        )
    )
    os.replace(temporary, path)


def _materialize_vggt_result_bundle(
    job_id: str, payload: dict[str, Any] | None = None
) -> tuple[Path, list[Path]]:
    workspace = _job_path(job_id)
    manifest_path = workspace / "result_manifest.json"
    frames_dir = workspace / "result_frames"
    existing_manifest = _read_json(manifest_path)
    if existing_manifest is not None:
        frame_count = int(existing_manifest.get("frame_count") or 0)
        frame_paths = [
            frames_dir / f"frame_{position:06d}.json.gz"
            for position in range(frame_count)
        ]
        if frame_count > 0 and all(path.is_file() for path in frame_paths):
            return manifest_path, frame_paths

    with _result_bundle_lock:
        existing_manifest = _read_json(manifest_path)
        if existing_manifest is not None:
            frame_count = int(existing_manifest.get("frame_count") or 0)
            frame_paths = [
                frames_dir / f"frame_{position:06d}.json.gz"
                for position in range(frame_count)
            ]
            if frame_count > 0 and all(path.is_file() for path in frame_paths):
                return manifest_path, frame_paths

        if payload is None:
            result_path = vggt_result_path(job_id)
            if result_path is None:
                raise ValueError("VGGT result is not ready")
            payload = _read_json(result_path)
        if not isinstance(payload, dict):
            raise ValueError("VGGT result is invalid")

        metadata = payload.get("metadata")
        metadata = metadata if isinstance(metadata, dict) else {}
        camera = payload.get("camera")
        camera = camera if isinstance(camera, dict) else {}
        point_clouds = payload.get("point_clouds")
        point_clouds = point_clouds if isinstance(point_clouds, list) else []
        if not point_clouds:
            raise ValueError("VGGT result has no point-cloud frames")

        per_frame_limit = max(1, RESULT_POINT_BUDGET // len(point_clouds))
        manifest_frames = []
        frame_paths = []
        for position, raw_cloud in enumerate(point_clouds):
            point_cloud = dict(raw_cloud) if isinstance(raw_cloud, dict) else {}
            xyz = point_cloud.get("xyz")
            if isinstance(xyz, list) and len(xyz) > per_frame_limit:
                keep = _even_sample_indices(len(xyz), per_frame_limit)
                for key in ("xyz", "rgb", "uv", "conf"):
                    values = point_cloud.get(key)
                    if isinstance(values, list):
                        point_cloud[key] = [values[index] for index in keep]
                point_cloud["returned_points"] = len(keep)

            frame_camera = {}
            for key in (
                "extrinsics",
                "intrinsics",
                "camera_to_world",
                "camera_centers",
            ):
                values = camera.get(key)
                frame_camera[key] = (
                    [values[position]]
                    if isinstance(values, list) and position < len(values)
                    else []
                )

            frame_payload = {
                "metadata": metadata,
                "camera": frame_camera,
                "point_clouds": [point_cloud],
                "splat_job": payload.get("splat_job"),
                "result_frame_index": position,
                "result_frame_count": len(point_clouds),
                "result_complete": position + 1 == len(point_clouds),
            }
            frame_path = frames_dir / f"frame_{position:06d}.json.gz"
            _write_gzip_json(frame_path, frame_payload)
            frame_paths.append(frame_path)
            manifest_frames.append(
                {
                    "position": position,
                    "frame_index": int(point_cloud.get("frame_index", position)),
                    "sampled_frame_index": int(
                        point_cloud.get("sampled_frame_index", position)
                    ),
                    "timestamp_sec": point_cloud.get("timestamp_sec"),
                    "num_points": int(point_cloud.get("num_points", 0)),
                    "returned_points": int(point_cloud.get("returned_points", 0)),
                }
            )

        _write_json(
            manifest_path,
            {
                "job_id": job_id,
                "request_id": str(metadata.get("request_id") or ""),
                "metadata": metadata,
                "frame_count": len(manifest_frames),
                "frames": manifest_frames,
                "splat_job": payload.get("splat_job"),
            },
        )
        return manifest_path, frame_paths


def vggt_result_manifest_path(job_id: str) -> Path | None:
    try:
        manifest_path, _ = _materialize_vggt_result_bundle(job_id)
        return manifest_path
    except ValueError:
        return None


def vggt_result_frame_path(job_id: str, position: int) -> Path | None:
    try:
        _, frame_paths = _materialize_vggt_result_bundle(job_id)
    except ValueError:
        return None
    if position < 0 or position >= len(frame_paths):
        return None
    path = frame_paths[position]
    return path if path.is_file() else None


def start_vggt_job(
    frame_files: list[tuple[str, bytes]],
    frame_indices: list[int],
    timestamps_sec: list[float],
    *,
    inference: Callable[..., Any],
    resolution: int,
    preprocess_mode: str,
    conf_thresh: float,
    window: int,
    max_points_per_frame: int,
    start_splat: bool,
    request_id: str = "",
    marker_start: str = "",
    marker_end: str = "",
    recording_path: str = "",
) -> dict[str, Any]:
    if not frame_files:
        raise ValueError("at least one frame is required")
    if len(frame_files) != len(frame_indices) or len(frame_files) != len(
        timestamps_sec
    ):
        raise ValueError("frame metadata length does not match uploaded frames")

    job_id = f"vggt-{uuid.uuid4().hex[:12]}"
    workspace = _job_path(job_id)
    frames_dir = workspace / "frames"
    frames_dir.mkdir(parents=True, exist_ok=False)
    frame_paths: list[Path] = []
    for index, (suffix, payload) in enumerate(frame_files):
        path = frames_dir / f"frame_{index:06d}{suffix}"
        path.write_bytes(payload)
        frame_paths.append(path)

    state = _update_job(
        job_id,
        status="queued",
        stage="queued",
        message="Queued VGGT reconstruction.",
        progress=0.0,
        current_step=0,
        max_steps=len(frame_paths),
        frame_count=len(frame_paths),
        frame_indices=[int(value) for value in frame_indices],
        start_frame_index=min(frame_indices),
        end_frame_index=max(frame_indices),
        child_job_ids=[],
        result_ready=False,
        request_id=request_id,
        marker_start=marker_start,
        marker_end=marker_end,
        recording_path=recording_path,
        created_at=time.time(),
    )
    future = _executor.submit(
        _run_vggt_job,
        job_id,
        frame_paths,
        frame_indices,
        timestamps_sec,
        inference,
        resolution,
        preprocess_mode,
        conf_thresh,
        window,
        max_points_per_frame,
        start_splat,
    )
    with _jobs_lock:
        _jobs[job_id]["_future"] = future
    return state


def _run_vggt_job(
    job_id: str,
    frame_paths: list[Path],
    frame_indices: list[int],
    timestamps_sec: list[float],
    inference: Callable[..., Any],
    resolution: int,
    preprocess_mode: str,
    conf_thresh: float,
    window: int,
    max_points_per_frame: int,
    start_splat: bool,
) -> None:
    started = time.time()

    def progress(stage: str, value: float, current: int, total: int, message: str):
        _update_job(
            job_id,
            status="running",
            stage=stage,
            message=message,
            progress=value,
            current_step=current,
            max_steps=total,
            elapsed_sec=time.time() - started,
        )

    try:
        submitted = get_vggt_job(job_id) or {}
        _update_job(
            job_id,
            status="running",
            stage="loading_model",
            message="Loading VGGT-Omega.",
            progress=0.01,
            started_at=time.time(),
        )
        response = inference(
            frame_paths,
            frame_indices,
            timestamps_sec,
            resolution=resolution,
            preprocess_mode=preprocess_mode,
            conf_thresh=conf_thresh,
            window=window,
            max_points_per_frame=max_points_per_frame,
            response_format="json",
            start_splat=start_splat,
            parent_job_id=job_id,
            job_context={
                key: submitted.get(key)
                for key in (
                    "request_id",
                    "marker_start",
                    "marker_end",
                    "recording_path",
                )
            },
            progress_callback=progress,
        )
        payload = json.loads(bytes(response.body))
        _write_json(_job_path(job_id) / "result.json", payload)
        _materialize_vggt_result_bundle(job_id, payload)
        splat = payload.get("splat_job") or {}
        child_ids = [str(splat["job_id"])] if splat.get("job_id") else []
        _update_job(
            job_id,
            status="complete",
            stage="complete",
            message=f"VGGT reconstruction complete for {len(frame_paths)} frames.",
            progress=1.0,
            current_step=len(frame_paths),
            max_steps=len(frame_paths),
            child_job_ids=child_ids,
            result_ready=True,
            result_url=f"/api/v1/worldgen/jobs/{job_id}/result",
            elapsed_sec=time.time() - started,
            finished_at=time.time(),
        )
    except Exception as exc:
        _update_job(
            job_id,
            status="failed",
            stage="failed",
            message=f"VGGT reconstruction failed: {exc}",
            error=str(exc),
            elapsed_sec=time.time() - started,
            finished_at=time.time(),
        )


def recover_interrupted_vggt_jobs() -> None:
    for path in JOBS_DIR.glob("vggt-*/status.json"):
        state = _read_json(path)
        if not state or state.get("status") not in {"queued", "running"}:
            continue
        job_id = str(state.get("job_id") or path.parent.name)
        with _jobs_lock:
            _jobs[job_id] = state
        _update_job(
            job_id,
            status="failed",
            stage="failed",
            message="Runner restarted before VGGT reconstruction completed.",
            error="runner restarted before the job completed",
            finished_at=time.time(),
        )


recover_interrupted_vggt_jobs()
