from __future__ import annotations

import asyncio
import json
import sys
import time
from pathlib import Path

import httpx

from runner.app import RunnerSettings, create_app
from runner.manager import JobManager
from runner.registry import JobDefinition


TOKEN = "test-runner-token"


def _settings(tmp_path: Path) -> RunnerSettings:
    return RunnerSettings(
        token=TOKEN,
        data_dir=tmp_path / "runner-data",
        max_workers=1,
        max_upload_bytes=1024 * 1024,
        max_runtime_seconds=30,
        allow_insecure_local=False,
    )


async def _request(app, method: str, path: str, **kwargs):
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://runner.test") as client:
        return await client.request(method, path, **kwargs)


def test_health_is_public_but_capabilities_require_auth(tmp_path: Path) -> None:
    app = create_app(_settings(tmp_path))

    health = asyncio.run(_request(app, "GET", "/health"))
    assert health.status_code == 200
    assert health.json()["service"] == "bayesmech-runner"

    denied = asyncio.run(_request(app, "GET", "/api/v1/capabilities"))
    assert denied.status_code == 401

    allowed = asyncio.run(
        _request(
            app,
            "GET",
            "/api/v1/capabilities",
            headers={"Authorization": f"Bearer {TOKEN}"},
        )
    )
    assert allowed.status_code == 200
    assert allowed.json()["services"][0]["name"] == "worldgen"


def test_registered_job_runs_and_returns_artifact(tmp_path: Path) -> None:
    script = tmp_path / "copy_job.py"
    script.write_text(
        "from pathlib import Path\n"
        "import sys\n"
        "source = Path(sys.argv[1])\n"
        "source.with_name(source.name.removesuffix('.vis.pb') + '.result.pb').write_bytes(source.read_bytes()[::-1])\n",
        encoding="utf-8",
    )
    definition = JobDefinition(
        "test-copy",
        "Test copy",
        "Test-only registered job.",
        script,
    )
    settings = _settings(tmp_path)
    manager = JobManager(
        settings.data_dir,
        {"test-copy": definition},
        max_workers=1,
        max_upload_bytes=settings.max_upload_bytes,
        max_runtime_seconds=settings.max_runtime_seconds,
        python_executable=sys.executable,
    )
    app = create_app(settings, manager)
    headers = {
        "Authorization": f"Bearer {TOKEN}",
        "X-Runner-Filename": "sample.vis.pb",
        "Content-Type": "application/octet-stream",
    }

    submitted = asyncio.run(
        _request(
            app,
            "POST",
            f"/api/v1/jobs/test-copy/recording?arguments={json.dumps([])}",
            headers=headers,
            content=b"bayesmech",
        )
    )
    assert submitted.status_code == 202, submitted.text
    job_id = submitted.json()["id"]

    state = submitted.json()
    deadline = time.monotonic() + 10
    while state["status"] not in {"succeeded", "failed", "cancelled"}:
        assert time.monotonic() < deadline
        time.sleep(0.05)
        response = asyncio.run(
            _request(
                app,
                "GET",
                f"/api/v1/jobs/{job_id}",
                headers={"Authorization": f"Bearer {TOKEN}"},
            )
        )
        assert response.status_code == 200
        state = response.json()

    assert state["status"] == "succeeded", state
    artifact = next(item for item in state["artifacts"] if item["name"] == "sample.result.pb")
    downloaded = asyncio.run(
        _request(
            app,
            "GET",
            f"/api/v1/jobs/{job_id}/artifacts/{artifact['id']}",
            headers={"Authorization": f"Bearer {TOKEN}"},
        )
    )
    assert downloaded.status_code == 200
    assert downloaded.content == b"bayesmech"[::-1]


def test_unknown_job_is_rejected_without_execution(tmp_path: Path) -> None:
    app = create_app(_settings(tmp_path))
    response = asyncio.run(
        _request(
            app,
            "POST",
            "/api/v1/jobs/not-registered/recording",
            headers={
                "Authorization": f"Bearer {TOKEN}",
                "X-Runner-Filename": "sample.vis.pb",
            },
            content=b"input",
        )
    )
    assert response.status_code == 404


def test_multipart_job_receives_sidecars_and_returns_changed_input(tmp_path: Path) -> None:
    script = tmp_path / "sidecar_job.py"
    script.write_text(
        "from pathlib import Path\n"
        "import sys\n"
        "recording = Path(sys.argv[1])\n"
        "sidecar = recording.with_name(recording.name.removesuffix('.vis.pb') + '.segmentation.pb')\n"
        "sidecar.write_bytes(recording.read_bytes() + sidecar.read_bytes())\n",
        encoding="utf-8",
    )
    definition = JobDefinition(
        "test-sidecar",
        "Test sidecar",
        "Test-only sidecar job.",
        script,
    )
    settings = _settings(tmp_path)
    manager = JobManager(
        settings.data_dir,
        {"test-sidecar": definition},
        max_workers=1,
        max_upload_bytes=settings.max_upload_bytes,
        max_runtime_seconds=settings.max_runtime_seconds,
        python_executable=sys.executable,
    )
    app = create_app(settings, manager)
    response = asyncio.run(
        _request(
            app,
            "POST",
            "/api/v1/jobs",
            headers={"Authorization": f"Bearer {TOKEN}"},
            data={
                "job_type": "test-sidecar",
                "parameters": json.dumps({"recording": "sample.vis.pb", "arguments": []}),
            },
            files=[
                ("files", ("sample.vis.pb", b"video", "application/octet-stream")),
                ("files", ("sample.segmentation.pb", b"old", "application/octet-stream")),
            ],
        )
    )
    assert response.status_code == 202, response.text
    job_id = response.json()["id"]

    state = response.json()
    deadline = time.monotonic() + 10
    while state["status"] not in {"succeeded", "failed", "cancelled"}:
        assert time.monotonic() < deadline
        time.sleep(0.05)
        state_response = asyncio.run(
            _request(
                app,
                "GET",
                f"/api/v1/jobs/{job_id}",
                headers={"Authorization": f"Bearer {TOKEN}"},
            )
        )
        state = state_response.json()

    assert state["status"] == "succeeded", state
    changed = next(item for item in state["artifacts"] if item["name"] == "sample.segmentation.pb")
    downloaded = asyncio.run(
        _request(
            app,
            "GET",
            f"/api/v1/jobs/{job_id}/artifacts/{changed['id']}",
            headers={"Authorization": f"Bearer {TOKEN}"},
        )
    )
    assert downloaded.content == b"videoold"
