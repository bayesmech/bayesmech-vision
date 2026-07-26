from __future__ import annotations

import json
import os
import subprocess
import threading
import time
import uuid
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path
from typing import Any

from .gpu_scheduler import gpu_scheduler
from .job_events import publish_runner_job

TERMINAL_STATUSES = {"complete", "failed", "cancelled"}
DEFAULT_TOOL_ALLOWLIST = (
    "runner_health",
    "runner_capabilities",
    "runner_list_jobs",
    "runner_get_job",
    "worldgen_health",
    "worldgen_list_jobs",
    "worldgen_get_job",
)


def _atomic_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(f"{path.suffix}.tmp")
    temporary.write_text(json.dumps(value, indent=2), encoding="utf-8")
    os.replace(temporary, path)


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else None
    except (OSError, json.JSONDecodeError):
        return None


def _tail(path: Path, limit: int = 8000) -> str:
    try:
        with path.open("rb") as handle:
            size = handle.seek(0, os.SEEK_END)
            handle.seek(max(0, size - limit))
            return handle.read().decode("utf-8", errors="replace")
    except OSError:
        return ""


class GemmaJobManager:
    """Persistent Gemma jobs which borrow the runner's one FIFO GPU lease."""

    def __init__(
        self,
        data_dir: Path,
        *,
        runner_token: str = "",
        max_runtime_seconds: int = 30 * 60,
        server_root: Path | None = None,
        python_executable: str | None = None,
    ) -> None:
        self.server_root = (
            server_root or Path(__file__).resolve().parents[1]
        ).resolve()
        self.jobs_dir = data_dir.expanduser().resolve() / "gemma" / "jobs"
        self.jobs_dir.mkdir(parents=True, exist_ok=True)
        self.runner_token = runner_token
        self.max_runtime_seconds = max(1, int(max_runtime_seconds))
        configured_python = os.environ.get("GEMMA_PYTHON", "").strip()
        self.python_executable = str(
            Path(
                python_executable
                or configured_python
                or self.server_root / ".gemma-venv" / "bin" / "python"
            ).expanduser()
        )
        self.worker_script = self.server_root / "gemma" / "worker.py"
        self._executor = ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="runner-gemma"
        )
        self._lock = threading.RLock()
        self._futures: dict[str, Future[None]] = {}
        self._processes: dict[str, subprocess.Popen[bytes]] = {}
        self._recover_interrupted_jobs()

    def close(self) -> None:
        with self._lock:
            processes = list(self._processes.values())
        for process in processes:
            if process.poll() is None:
                process.terminate()
        self._executor.shutdown(wait=False, cancel_futures=True)

    def start(
        self,
        frame_files: list[tuple[str, bytes]],
        timestamps_sec: list[float],
        message: str,
        history: list[dict[str, str]] | None = None,
        *,
        request_id: str = "",
        chat_id: str = "",
        recording_path: str = "",
    ) -> dict[str, Any]:
        prompt = message.strip()
        if not prompt:
            raise ValueError("message is required")
        if len(prompt) > 32_000:
            raise ValueError("message must be at most 32000 characters")
        if not frame_files:
            raise ValueError("at least one video frame is required")
        if len(frame_files) != len(timestamps_sec):
            raise ValueError("frame timestamps do not match the uploaded frames")

        job_id = f"gemma-{uuid.uuid4().hex[:12]}"
        workspace = self.jobs_dir / job_id
        frames_dir = workspace / "frames"
        frames_dir.mkdir(parents=True, exist_ok=False)
        frame_paths: list[str] = []
        for index, (suffix, payload) in enumerate(frame_files):
            path = frames_dir / f"frame_{index:06d}{suffix}"
            path.write_bytes(payload)
            frame_paths.append(str(path))

        normalized_history = [
            {
                "role": (
                    "assistant"
                    if str(item.get("role") or "") == "assistant"
                    else "user"
                ),
                "text": str(item.get("text") or "")[:32_000],
            }
            for item in (history or [])[-24:]
            if str(item.get("text") or "").strip()
        ]
        state = self._update(
            job_id,
            status="queued",
            stage="queued",
            message="Queued Gemma video inference.",
            progress=0.0,
            current_step=0,
            max_steps=4,
            frame_count=len(frame_paths),
            result_ready=False,
            request_id=request_id,
            chat_id=chat_id,
            recording_path=recording_path,
            created_at=time.time(),
        )
        request = self._worker_request(
            frame_paths, timestamps_sec, prompt, normalized_history
        )
        _atomic_json(workspace / "request.json", request)
        try:
            os.chmod(workspace / "request.json", 0o600)
        except OSError:
            pass
        future = self._executor.submit(self._run, job_id)
        with self._lock:
            self._futures[job_id] = future
        return state

    def get(self, job_id: str) -> dict[str, Any] | None:
        if not job_id.startswith("gemma-"):
            return None
        return _read_json(self.jobs_dir / job_id / "status.json")

    def public_state(self, job_id: str) -> dict[str, Any]:
        state = self.get(job_id)
        if state is None:
            raise KeyError(job_id)
        workspace = self.jobs_dir / job_id
        return {
            **state,
            "stdout_tail": _tail(workspace / "stdout.log"),
            "stderr_tail": _tail(workspace / "stderr.log"),
        }

    def list_jobs(self, limit: int = 100) -> list[dict[str, Any]]:
        states = [
            state
            for path in self.jobs_dir.glob("gemma-*/status.json")
            if (state := _read_json(path)) is not None
        ]
        states.sort(key=lambda state: float(state.get("created_at") or 0), reverse=True)
        return states[: max(1, min(int(limit), 500))]

    def result_path(self, job_id: str) -> Path | None:
        state = self.get(job_id)
        workspace = (self.jobs_dir / job_id).resolve()
        path = (workspace / "result.json").resolve()
        if (
            not state
            or state.get("status") != "complete"
            or workspace not in path.parents
            or not path.is_file()
        ):
            return None
        return path

    def _worker_request(
        self,
        frame_paths: list[str],
        timestamps_sec: list[float],
        message: str,
        history: list[dict[str, str]],
    ) -> dict[str, Any]:
        configured_allowlist = [
            value.strip()
            for value in os.environ.get("GEMMA_TOOL_ALLOWLIST", "").split(",")
            if value.strip()
        ]
        model_id = os.environ.get("GEMMA_MODEL_ID", "google/gemma-4-12B-it")
        default_model_dir = (
            Path("~/.cache/bayesmech/models").expanduser() / model_id.rsplit("/", 1)[-1]
        )
        port = os.environ.get("RUNNER_PORT", "8787")
        return {
            "model_id": model_id,
            "model_path": os.environ.get("GEMMA_MODEL_DIR", str(default_model_dir)),
            "runner_mcp_url": os.environ.get(
                "GEMMA_RUNNER_MCP_URL", f"http://127.0.0.1:{port}/mcp/"
            ),
            "runner_token": self.runner_token,
            "tool_allowlist": configured_allowlist or list(DEFAULT_TOOL_ALLOWLIST),
            "max_tool_turns": int(os.environ.get("GEMMA_MAX_TOOL_TURNS", "6")),
            "max_new_tokens": int(os.environ.get("GEMMA_MAX_NEW_TOKENS", "768")),
            "frame_paths": frame_paths,
            "timestamps_sec": timestamps_sec,
            "message": message,
            "history": history,
        }

    def _update(self, job_id: str, **fields: Any) -> dict[str, Any]:
        path = self.jobs_dir / job_id / "status.json"
        state = _read_json(path) or {"job_id": job_id, "id": job_id}
        state.update(fields)
        state.update(
            job_id=job_id,
            id=job_id,
            type="gemma",
            title="Gemma Video Chat",
            source="agent",
            updated_at=time.time(),
        )
        _atomic_json(path, state)
        publish_runner_job(state)
        return state

    def _run(self, job_id: str) -> None:
        workspace = self.jobs_dir / job_id
        started = time.time()

        def waiting(position: int) -> None:
            self._update(
                job_id,
                status="queued",
                stage="waiting_for_gpu",
                message=f"Waiting for GPU lease at queue position {position}.",
                progress=0.01,
            )

        self._update(
            job_id,
            status="queued",
            stage="waiting_for_gpu",
            message="Waiting for the greedy GPU scheduler.",
            progress=0.01,
        )
        try:
            with gpu_scheduler.lease("gemma", job_id, on_wait=waiting):
                self._execute_worker(job_id, workspace, started)
        except Exception as exc:
            self._update(
                job_id,
                status="failed",
                stage="failed",
                message=f"Gemma inference failed: {exc}",
                error=str(exc),
                elapsed_sec=time.time() - started,
                finished_at=time.time(),
            )
        finally:
            with self._lock:
                self._futures.pop(job_id, None)
                self._processes.pop(job_id, None)

    def _execute_worker(self, job_id: str, workspace: Path, started: float) -> None:
        python_path = Path(self.python_executable)
        if not python_path.is_file():
            raise RuntimeError(
                f"Gemma environment is missing: {python_path}. "
                "Run server/runner/setup_remote.sh."
            )
        if not self.worker_script.is_file():
            raise RuntimeError(f"Gemma worker is missing: {self.worker_script}")

        self._update(
            job_id,
            status="running",
            stage="starting_worker",
            message="Starting the isolated Gemma worker.",
            progress=0.03,
            started_at=time.time(),
        )
        environment = dict(os.environ)
        environment.pop("HF_TOKEN", None)
        environment.pop("HUGGING_FACE_HUB_TOKEN", None)
        environment["PYTHONUNBUFFERED"] = "1"
        command = [
            str(python_path),
            str(self.worker_script),
            str(workspace / "request.json"),
            str(workspace / "result.json"),
        ]
        with (workspace / "stdout.log").open("wb") as stdout, (
            workspace / "stderr.log"
        ).open("wb") as stderr:
            process = subprocess.Popen(
                command,
                cwd=str(self.server_root),
                env=environment,
                stdin=subprocess.DEVNULL,
                stdout=stdout,
                stderr=stderr,
            )
            with self._lock:
                self._processes[job_id] = process
            last_progress: tuple[str, float] | None = None
            while process.poll() is None:
                if time.time() - started > self.max_runtime_seconds:
                    process.terminate()
                    raise TimeoutError(
                        f"Gemma inference exceeded {self.max_runtime_seconds} seconds"
                    )
                progress = _read_json(workspace / "progress.json")
                if progress:
                    marker = (
                        str(progress.get("stage") or ""),
                        float(progress.get("progress") or 0),
                    )
                    if marker != last_progress:
                        last_progress = marker
                        self._update(
                            job_id,
                            status="running",
                            stage=marker[0] or "generating",
                            message=str(
                                progress.get("message")
                                or "Gemma is generating a response."
                            ),
                            progress=max(0.03, min(0.98, marker[1])),
                            current_step=int(progress.get("current_step") or 1),
                        )
                time.sleep(0.25)
            exit_code = process.wait()

        if exit_code != 0:
            detail = _tail(workspace / "stderr.log")
            raise RuntimeError(
                f"Gemma worker exited with code {exit_code}"
                f"{f': {detail[-2000:]}' if detail else ''}"
            )
        result = _read_json(workspace / "result.json")
        if result is None or not str(result.get("text") or "").strip():
            raise RuntimeError("Gemma worker did not produce a usable response")
        self._update(
            job_id,
            status="complete",
            stage="complete",
            message="Gemma video response is ready.",
            progress=1.0,
            current_step=4,
            max_steps=4,
            result_ready=True,
            result_url=f"/api/v1/agent/jobs/{job_id}/result",
            sampled_frame_count=int(result.get("sampled_frame_count") or 0),
            tool_call_count=len(result.get("tool_calls") or []),
            model=str(result.get("model") or ""),
            elapsed_sec=time.time() - started,
            finished_at=time.time(),
        )

    def _recover_interrupted_jobs(self) -> None:
        for state in self.list_jobs(limit=500):
            if state.get("status") in TERMINAL_STATUSES:
                continue
            job_id = str(state.get("job_id") or state.get("id") or "")
            if job_id:
                self._update(
                    job_id,
                    status="failed",
                    stage="failed",
                    message="Runner restarted before Gemma inference completed.",
                    error="runner restarted before the job completed",
                    finished_at=time.time(),
                )
