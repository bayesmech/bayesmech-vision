from __future__ import annotations

import hashlib
import hmac
import json
import os
import signal
import subprocess
import sys
import threading
import time
import uuid
from concurrent.futures import Future, ThreadPoolExecutor
from contextlib import nullcontext
from dataclasses import dataclass
from pathlib import Path
from typing import Any, BinaryIO

from .registry import JobDefinition
from .gpu_scheduler import gpu_scheduler
from .job_events import publish_runner_job

TERMINAL_STATUSES = {"succeeded", "failed", "cancelled"}
INTERNAL_FILENAMES = {"job.json", "stdout.log", "stderr.log"}


def utc_timestamp() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()


def safe_filename(value: str) -> str:
    name = Path(value.replace("\\", "/")).name.strip()
    if not name or name in {".", ".."} or "\x00" in name:
        raise ValueError("invalid input filename")
    return name


def validate_arguments(value: Any) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list) or len(value) > 128:
        raise ValueError(
            "parameters.arguments must be an array with at most 128 entries"
        )
    arguments: list[str] = []
    for item in value:
        if not isinstance(item, str) or "\x00" in item or len(item) > 4096:
            raise ValueError(
                "every job argument must be a string of at most 4096 characters"
            )
        arguments.append(item)
    return arguments


def _atomic_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(f"{path.suffix}.tmp")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True), encoding="utf-8")
    os.replace(temporary, path)


def _tail(path: Path, limit: int = 8000) -> str:
    try:
        with path.open("rb") as handle:
            size = handle.seek(0, os.SEEK_END)
            handle.seek(max(0, size - limit))
            return handle.read().decode("utf-8", errors="replace")
    except OSError:
        return ""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


@dataclass(frozen=True)
class PreparedJob:
    job_id: str
    workspace: Path
    input_dir: Path


class JobManager:
    def __init__(
        self,
        data_dir: Path,
        registry: dict[str, JobDefinition],
        *,
        max_workers: int = 1,
        max_upload_bytes: int = 50 * 1024 * 1024 * 1024,
        max_runtime_seconds: int = 24 * 60 * 60,
        python_executable: str = sys.executable,
    ) -> None:
        self.data_dir = data_dir.expanduser().resolve()
        self.jobs_dir = self.data_dir / "jobs"
        self.jobs_dir.mkdir(parents=True, exist_ok=True)
        self.registry = registry
        self.max_upload_bytes = max(1, int(max_upload_bytes))
        self.max_runtime_seconds = max(1, int(max_runtime_seconds))
        self.python_executable = python_executable
        self._executor = ThreadPoolExecutor(
            max_workers=max(1, int(max_workers)), thread_name_prefix="runner-job"
        )
        self._lock = threading.RLock()
        self._futures: dict[str, Future[None]] = {}
        self._processes: dict[str, subprocess.Popen[bytes]] = {}
        self._cancel_requested: set[str] = set()
        self._recover_interrupted_jobs()

    def close(self) -> None:
        with self._lock:
            job_ids = list(self._processes)
        for job_id in job_ids:
            self.cancel(job_id)
        self._executor.shutdown(wait=False, cancel_futures=True)

    def capabilities(self) -> list[dict[str, object]]:
        return [self.registry[name].public_dict() for name in sorted(self.registry)]

    def definition(self, job_type: str) -> JobDefinition:
        definition = self.registry.get(job_type)
        if definition is None:
            raise KeyError(job_type)
        if not definition.entrypoint.is_file():
            raise FileNotFoundError(
                f"job entrypoint is missing: {definition.entrypoint}"
            )
        return definition

    def prepare(
        self, job_type: str, parameters: dict[str, Any] | None = None
    ) -> PreparedJob:
        self.definition(job_type)
        parameters = dict(parameters or {})
        parameters["arguments"] = validate_arguments(parameters.get("arguments"))
        job_id = f"job-{uuid.uuid4().hex[:16]}"
        workspace = self.jobs_dir / job_id
        input_dir = workspace / "inputs"
        input_dir.mkdir(parents=True)
        state = {
            "id": job_id,
            "type": job_type,
            "status": "uploading",
            "created_at": utc_timestamp(),
            "started_at": None,
            "finished_at": None,
            "parameters": parameters,
            "inputs": [],
            "artifacts": [],
            "exit_code": None,
            "error": "",
        }
        self._write(job_id, state)
        return PreparedJob(job_id, workspace, input_dir)

    def save_stream(
        self,
        prepared: PreparedJob,
        filename: str,
        stream: BinaryIO,
        *,
        expected_size: int | None = None,
    ) -> dict[str, Any]:
        name = safe_filename(filename)
        destination = prepared.input_dir / name
        if destination.exists():
            raise ValueError(f"duplicate input filename: {name}")
        digest = hashlib.sha256()
        size = 0
        try:
            with destination.open("xb") as output:
                while True:
                    chunk = stream.read(1024 * 1024)
                    if not chunk:
                        break
                    size += len(chunk)
                    if size > self.max_upload_bytes:
                        raise ValueError(
                            f"input exceeds the {self.max_upload_bytes}-byte upload limit"
                        )
                    output.write(chunk)
                    digest.update(chunk)
            if expected_size is not None and size != expected_size:
                raise ValueError(
                    f"incomplete upload: expected {expected_size} bytes, received {size}"
                )
        except Exception:
            destination.unlink(missing_ok=True)
            raise
        return {"name": name, "size": size, "sha256": digest.hexdigest()}

    def finish_upload(
        self, prepared: PreparedJob, inputs: list[dict[str, Any]]
    ) -> dict[str, Any]:
        if not inputs:
            self.fail_upload(prepared.job_id, "at least one input file is required")
            raise ValueError("at least one input file is required")
        state = self._read_required(prepared.job_id)
        definition = self.definition(str(state["type"]))
        recording_name = str(state.get("parameters", {}).get("recording") or "")
        available_names = {str(item["name"]) for item in inputs}
        if recording_name:
            recording_name = safe_filename(recording_name)
            if recording_name not in available_names:
                raise ValueError(f"recording input was not uploaded: {recording_name}")
        else:
            recording_name = next(
                (
                    str(item["name"])
                    for item in inputs
                    if any(
                        str(item["name"]).endswith(suffix)
                        for suffix in definition.input_suffixes
                    )
                ),
                "",
            )
        if not recording_name:
            expected = ", ".join(definition.input_suffixes)
            raise ValueError(
                f"{definition.name} requires an input ending in {expected}"
            )
        state["parameters"]["recording"] = recording_name
        state["inputs"] = inputs
        state["status"] = "queued"
        self._write(prepared.job_id, state)
        future = self._executor.submit(self._run, prepared.job_id)
        with self._lock:
            self._futures[prepared.job_id] = future
        return self.public_state(prepared.job_id)

    def fail_upload(self, job_id: str, message: str) -> None:
        try:
            state = self._read_required(job_id)
        except KeyError:
            return
        state.update(status="failed", finished_at=utc_timestamp(), error=message)
        self._write(job_id, state)

    def get(self, job_id: str) -> dict[str, Any] | None:
        path = self.jobs_dir / safe_filename(job_id) / "job.json"
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, ValueError):
            return None

    def public_state(self, job_id: str) -> dict[str, Any]:
        state = self._read_required(job_id)
        workspace = self.jobs_dir / job_id
        state["stdout_tail"] = _tail(workspace / "stdout.log")
        state["stderr_tail"] = _tail(workspace / "stderr.log")
        return state

    def list_jobs(self, limit: int = 100) -> list[dict[str, Any]]:
        states: list[dict[str, Any]] = []
        for path in self.jobs_dir.glob("job-*/job.json"):
            try:
                states.append(json.loads(path.read_text(encoding="utf-8")))
            except (OSError, json.JSONDecodeError):
                continue
        states.sort(key=lambda item: str(item.get("created_at", "")), reverse=True)
        return states[: max(1, min(int(limit), 500))]

    def artifact_path(
        self, job_id: str, artifact_id: str
    ) -> tuple[Path, dict[str, Any]]:
        state = self._read_required(job_id)
        artifact = next(
            (
                item
                for item in state.get("artifacts", [])
                if item.get("id") == artifact_id
            ),
            None,
        )
        if artifact is None:
            raise KeyError(artifact_id)
        workspace = (self.jobs_dir / job_id).resolve()
        path = (workspace / str(artifact["relative_path"])).resolve()
        if workspace not in path.parents or not path.is_file():
            raise KeyError(artifact_id)
        return path, artifact

    def cancel(self, job_id: str) -> dict[str, Any]:
        state = self._read_required(job_id)
        if state.get("status") in TERMINAL_STATUSES:
            return self.public_state(job_id)
        with self._lock:
            self._cancel_requested.add(job_id)
            future = self._futures.get(job_id)
            process = self._processes.get(job_id)
        if state.get("status") == "uploading" or (
            future is not None and future.cancel()
        ):
            state.update(
                status="cancelled",
                finished_at=utc_timestamp(),
                error="cancelled before execution",
            )
            self._write(job_id, state)
        if process is not None and process.poll() is None:
            self._terminate_process(process)
        return self.public_state(job_id)

    def _run(self, job_id: str) -> None:
        state = self._read_required(job_id)
        definition = self.definition(str(state["type"]))
        workspace = self.jobs_dir / job_id
        recording = (
            workspace / "inputs" / safe_filename(str(state["parameters"]["recording"]))
        )
        arguments = validate_arguments(state.get("parameters", {}).get("arguments"))
        command = [
            self.python_executable,
            str(definition.entrypoint),
            str(recording),
            *arguments,
        ]
        if definition.requires_gpu:
            state.update(
                status="queued",
                stage="waiting_for_gpu",
                message="Waiting for the greedy GPU scheduler.",
                progress=0.01,
            )
        self._write(job_id, state)

        stdout_path = workspace / "stdout.log"
        stderr_path = workspace / "stderr.log"
        environment = {
            **os.environ,
            "RUNNER_JOB_ID": job_id,
            "RUNNER_JOB_DIR": str(workspace),
            "PYTHONUNBUFFERED": "1",
        }
        started = time.monotonic()
        try:
            lease = (
                gpu_scheduler.lease(str(state["type"]), job_id)
                if definition.requires_gpu
                else nullcontext()
            )
            with lease:
                state = self._read_required(job_id)
                state.update(
                    status="running",
                    stage="running",
                    message=f"Running {definition.title}.",
                    progress=0.05,
                    started_at=utc_timestamp(),
                    command=[
                        self.python_executable,
                        str(definition.entrypoint.name),
                        recording.name,
                        *arguments,
                    ],
                )
                self._write(job_id, state)
                with stdout_path.open("wb") as stdout, stderr_path.open("wb") as stderr:
                    process = subprocess.Popen(
                        command,
                        cwd=str(definition.entrypoint.parents[1]),
                        env=environment,
                        stdin=subprocess.DEVNULL,
                        stdout=stdout,
                        stderr=stderr,
                        start_new_session=True,
                    )
                    with self._lock:
                        self._processes[job_id] = process
                    while process.poll() is None:
                        with self._lock:
                            cancelled = job_id in self._cancel_requested
                        if cancelled:
                            self._terminate_process(process)
                            break
                        if time.monotonic() - started > self.max_runtime_seconds:
                            self._terminate_process(process)
                            raise TimeoutError(
                                f"job exceeded {self.max_runtime_seconds} seconds"
                            )
                        time.sleep(0.2)
                    exit_code = process.wait()
        except Exception as exc:
            state = self._read_required(job_id)
            state.update(
                status="failed",
                finished_at=utc_timestamp(),
                error=str(exc),
                artifacts=self._collect_artifacts(workspace, state),
            )
            self._write(job_id, state)
            return
        finally:
            with self._lock:
                self._processes.pop(job_id, None)
                self._futures.pop(job_id, None)

        state = self._read_required(job_id)
        with self._lock:
            cancelled = job_id in self._cancel_requested
            self._cancel_requested.discard(job_id)
        status = (
            "cancelled" if cancelled else ("succeeded" if exit_code == 0 else "failed")
        )
        error = (
            "cancelled"
            if cancelled
            else ("" if exit_code == 0 else f"process exited with code {exit_code}")
        )
        state.update(
            status=status,
            finished_at=utc_timestamp(),
            exit_code=exit_code,
            error=error,
            artifacts=self._collect_artifacts(workspace, state),
        )
        self._write(job_id, state)

    def _collect_artifacts(
        self, workspace: Path, state: dict[str, Any]
    ) -> list[dict[str, Any]]:
        uploaded = {
            f"inputs/{item['name']}": str(item.get("sha256", ""))
            for item in state.get("inputs", [])
        }
        artifacts: list[dict[str, Any]] = []
        used_ids: set[str] = set()
        for path in sorted(workspace.rglob("*")):
            if not path.is_file():
                continue
            relative = path.relative_to(workspace).as_posix()
            if path.name in INTERNAL_FILENAMES:
                continue
            digest = _sha256(path)
            if relative in uploaded and hmac.compare_digest(digest, uploaded[relative]):
                continue
            artifact_id = digest[:16]
            if artifact_id in used_ids:
                artifact_id = f"{artifact_id}-{len(used_ids)}"
            used_ids.add(artifact_id)
            artifacts.append(
                {
                    "id": artifact_id,
                    "name": path.name,
                    "relative_path": relative,
                    "size": path.stat().st_size,
                    "sha256": digest,
                }
            )
        return artifacts

    def _recover_interrupted_jobs(self) -> None:
        for path in self.jobs_dir.glob("job-*/job.json"):
            try:
                state = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if state.get("status") in {"uploading", "queued", "running"}:
                state.update(
                    status="failed",
                    finished_at=utc_timestamp(),
                    error="runner restarted before the job completed",
                )
                _atomic_json(path, state)

    def _read_required(self, job_id: str) -> dict[str, Any]:
        state = self.get(job_id)
        if state is None:
            raise KeyError(job_id)
        return state

    def _write(self, job_id: str, state: dict[str, Any]) -> None:
        with self._lock:
            _atomic_json(self.jobs_dir / job_id / "job.json", state)
        publish_runner_job(state)

    @staticmethod
    def _terminate_process(process: subprocess.Popen[bytes]) -> None:
        if process.poll() is not None:
            return
        try:
            os.killpg(process.pid, signal.SIGTERM)
            process.wait(timeout=5)
        except (OSError, subprocess.TimeoutExpired):
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except OSError:
                process.kill()
