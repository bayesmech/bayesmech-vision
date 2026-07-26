from __future__ import annotations

import asyncio
import base64
import json
import sys
import time
from pathlib import Path

import httpx
from fastmcp import Client

from runner.app import RunnerSettings, create_app
from runner.gemma_jobs import GemmaJobManager
from runner.job_events import job_events
from runner.manager import JobManager
from runner.mcp_server import create_mcp_server
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
    async with httpx.AsyncClient(
        transport=transport, base_url="http://runner.test"
    ) as client:
        return await client.request(method, path, **kwargs)


def test_health_is_public_but_capabilities_require_auth(tmp_path: Path) -> None:
    app = create_app(_settings(tmp_path))

    health = asyncio.run(_request(app, "GET", "/health"))
    assert health.status_code == 200
    assert health.json()["service"] == "bayesmech-runner"

    denied = asyncio.run(_request(app, "GET", "/api/v1/capabilities"))
    assert denied.status_code == 401
    denied_mcp = asyncio.run(_request(app, "POST", "/mcp/"))
    assert denied_mcp.status_code == 401

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
    artifact = next(
        item for item in state["artifacts"] if item["name"] == "sample.result.pb"
    )
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


def test_multipart_job_receives_sidecars_and_returns_changed_input(
    tmp_path: Path,
) -> None:
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
                "parameters": json.dumps(
                    {"recording": "sample.vis.pb", "arguments": []}
                ),
            },
            files=[
                ("files", ("sample.vis.pb", b"video", "application/octet-stream")),
                (
                    "files",
                    ("sample.segmentation.pb", b"old", "application/octet-stream"),
                ),
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
    changed = next(
        item for item in state["artifacts"] if item["name"] == "sample.segmentation.pb"
    )
    downloaded = asyncio.run(
        _request(
            app,
            "GET",
            f"/api/v1/jobs/{job_id}/artifacts/{changed['id']}",
            headers={"Authorization": f"Bearer {TOKEN}"},
        )
    )
    assert downloaded.content == b"videoold"


def test_mcp_discovers_runner_and_worldgen_tools(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    manager = JobManager(
        settings.data_dir,
        {},
        max_workers=1,
        max_upload_bytes=settings.max_upload_bytes,
        max_runtime_seconds=settings.max_runtime_seconds,
        python_executable=sys.executable,
    )
    mcp = create_mcp_server(settings, manager)

    async def exercise():
        async with Client(mcp) as client:
            tools = await client.list_tools()
            names = {tool.name for tool in tools}
            assert {
                "runner_health",
                "runner_capabilities",
                "runner_submit_job",
                "runner_artifact",
                "worldgen_health",
                "worldgen_reconstruct_frames",
                "worldgen_list_jobs",
                "worldgen_get_job",
                "worldgen_splat_status",
                "worldgen_splat_artifact",
                "gpu_scheduler_state",
                "gemma_list_jobs",
                "gemma_get_job",
            } <= names
            health = await client.call_tool("runner_health")
            assert health.data["service"] == "bayesmech-runner"
            assert health.data["mcp_endpoint"] == "/mcp/"
            capabilities = await client.call_tool("runner_capabilities")
            assert capabilities.data["services"][0]["mcp_tools"][0] == "worldgen_health"

    try:
        asyncio.run(exercise())
    finally:
        manager.close()


def test_gemma_video_job_runs_in_fifo_worker_and_returns_result(
    tmp_path: Path,
) -> None:
    server_root = tmp_path / "server"
    worker = server_root / "gemma" / "worker.py"
    worker.parent.mkdir(parents=True)
    worker.write_text(
        "import json, sys\n"
        "from pathlib import Path\n"
        "request = json.loads(Path(sys.argv[1]).read_text())\n"
        "Path(sys.argv[2]).write_text(json.dumps({"
        "'text': 'saw the sampled frame', "
        "'model': request['model_id'], "
        "'tool_calls': [], "
        "'sampled_frame_count': len(request['frame_paths'])"
        "}))\n",
        encoding="utf-8",
    )
    settings = _settings(tmp_path)
    manager = JobManager(
        settings.data_dir,
        {},
        max_workers=1,
        max_upload_bytes=settings.max_upload_bytes,
        max_runtime_seconds=settings.max_runtime_seconds,
        python_executable=sys.executable,
    )
    gemma_manager = GemmaJobManager(
        settings.data_dir,
        runner_token=TOKEN,
        max_runtime_seconds=30,
        server_root=server_root,
        python_executable=sys.executable,
    )
    app = create_app(settings, manager, gemma_manager)
    headers = {"Authorization": f"Bearer {TOKEN}"}
    submitted = asyncio.run(
        _request(
            app,
            "POST",
            "/api/v1/agent/jobs",
            headers=headers,
            data={
                "message": "What happens?",
                "timestamps_sec": "[1.25]",
                "history": json.dumps([{"role": "user", "text": "Earlier question"}]),
                "request_id": "request-test",
                "chat_id": "chat-test",
            },
            files=[
                ("frames", ("frame.jpg", b"jpeg-bytes", "image/jpeg")),
            ],
        )
    )
    assert submitted.status_code == 202, submitted.text
    job_id = submitted.json()["job_id"]

    state = submitted.json()
    deadline = time.monotonic() + 10
    while state["status"] not in {"complete", "failed"}:
        assert time.monotonic() < deadline
        time.sleep(0.05)
        response = asyncio.run(
            _request(
                app,
                "GET",
                f"/api/v1/agent/jobs/{job_id}",
                headers=headers,
            )
        )
        assert response.status_code == 200
        state = response.json()
    assert state["status"] == "complete", state
    assert state["sampled_frame_count"] == 1

    response = asyncio.run(
        _request(
            app,
            "GET",
            f"/api/v1/agent/jobs/{job_id}/result",
            headers=headers,
        )
    )
    assert response.status_code == 200
    assert response.json()["text"] == "saw the sampled frame"
    assert response.json()["sampled_frame_count"] == 1
    gemma_manager.close()
    manager.close()


def test_runner_job_state_endpoint_returns_broker_snapshot(tmp_path: Path) -> None:
    job_events.clear()
    app = create_app(_settings(tmp_path))
    job_events.publish(
        {
            "job_id": "vggt-test",
            "type": "vggt",
            "title": "VGGT Reconstruction",
            "status": "running",
            "progress": 0.42,
        }
    )
    response = asyncio.run(
        _request(
            app,
            "GET",
            "/api/v1/jobs/state",
            headers={"Authorization": f"Bearer {TOKEN}"},
        )
    )
    assert response.status_code == 200
    [state] = response.json()["jobs"]
    assert state["job_id"] == "vggt-test"
    assert state["progress"] == 0.42
    assert state["revision"] == 1


def test_mcp_submits_job_and_returns_inline_artifact(tmp_path: Path) -> None:
    script = tmp_path / "mcp_copy_job.py"
    script.write_text(
        "from pathlib import Path\n"
        "import sys\n"
        "source = Path(sys.argv[1])\n"
        "source.with_name('mcp.result.pb').write_bytes(source.read_bytes()[::-1])\n",
        encoding="utf-8",
    )
    settings = _settings(tmp_path)
    manager = JobManager(
        settings.data_dir,
        {
            "test-mcp": JobDefinition(
                "test-mcp", "Test MCP", "Test MCP submission.", script
            )
        },
        max_workers=1,
        max_upload_bytes=settings.max_upload_bytes,
        max_runtime_seconds=settings.max_runtime_seconds,
        python_executable=sys.executable,
    )
    mcp = create_mcp_server(settings, manager)

    async def exercise():
        async with Client(mcp) as client:
            submitted = await client.call_tool(
                "runner_submit_job",
                {
                    "job_type": "test-mcp",
                    "recording_filename": "sample.vis.pb",
                    "files": [
                        {
                            "filename": "sample.vis.pb",
                            "data_base64": base64.b64encode(b"through-mcp").decode(
                                "ascii"
                            ),
                        }
                    ],
                },
            )
            job_id = submitted.data["id"]
            state = submitted.data
            deadline = time.monotonic() + 10
            while state["status"] not in {"succeeded", "failed", "cancelled"}:
                assert time.monotonic() < deadline
                await asyncio.sleep(0.05)
                state = (
                    await client.call_tool("runner_get_job", {"job_id": job_id})
                ).data
            assert state["status"] == "succeeded", state
            artifact = next(
                item for item in state["artifacts"] if item["name"] == "mcp.result.pb"
            )
            result = await client.call_tool(
                "runner_artifact",
                {
                    "job_id": job_id,
                    "artifact_id": artifact["id"],
                    "include_base64": True,
                },
            )
            assert base64.b64decode(result.data["data_base64"]) == b"through-mcp"[::-1]
            assert result.data["download_url"].endswith(f"/artifacts/{artifact['id']}")

    try:
        asyncio.run(exercise())
    finally:
        manager.close()
