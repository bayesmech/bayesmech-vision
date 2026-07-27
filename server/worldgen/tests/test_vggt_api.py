from __future__ import annotations

import asyncio
import time
from pathlib import Path

import httpx
import torch
from fastapi import FastAPI

from worldgen.services import vggt_api
from worldgen.services import vggt_jobs
from worldgen.scripts import infer_vggt_omega_video


async def _request(app: FastAPI, **kwargs) -> httpx.Response:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://worldgen.test"
    ) as client:
        return await client.post("/infer", **kwargs)


async def _request_path(
    app: FastAPI, method: str, path: str, **kwargs
) -> httpx.Response:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://worldgen.test"
    ) as client:
        return await client.request(method, path, **kwargs)


def test_infer_accepts_repeated_frame_uploads(monkeypatch, tmp_path: Path) -> None:
    received: dict[str, object] = {}

    def fake_inference(frame_paths, frame_indices, timestamps_sec, **kwargs):
        received["names"] = [path.name for path in frame_paths]
        received["indices"] = frame_indices
        received["timestamps"] = timestamps_sec
        received["kwargs"] = kwargs
        return vggt_api.JSONResponse({"ok": True})

    monkeypatch.setattr(vggt_api, "_run_inference", fake_inference)
    response = asyncio.run(
        _request(
            vggt_api.app,
            data={"fps": "20", "max_frames": "0", "start_splat": "false"},
            files=[
                ("frames", ("frame_000000.jpg", b"first", "image/jpeg")),
                ("frames", ("frame_000020.jpg", b"second", "image/jpeg")),
            ],
        )
    )

    assert response.status_code == 200, response.text
    assert response.json() == {"ok": True}
    assert received["names"] == ["frame_000000.jpg", "frame_000001.jpg"]
    assert received["indices"] == [0, 1]
    assert received["timestamps"] == [0.0, 0.05]
    assert received["kwargs"]["start_splat"] is False


def test_model_download_uses_official_omega_repository_and_filename(
    monkeypatch, tmp_path: Path
) -> None:
    import huggingface_hub

    monkeypatch.syspath_prepend(str(vggt_api.ROOT / "vendor" / "vggt_omega"))
    import vggt_omega.models

    checkpoint = tmp_path / "checkpoint.pt"
    checkpoint.write_bytes(b"checkpoint")
    requested: dict[str, str] = {}

    class FakeModel:
        def load_state_dict(self, state) -> None:
            assert state == {"weights": True}

        def to(self, _device):
            return self

        def eval(self):
            return self

    def fake_download(*, repo_id: str, filename: str) -> str:
        requested.update(repo_id=repo_id, filename=filename)
        return str(checkpoint)

    monkeypatch.setattr(vggt_omega.models, "VGGTOmega", FakeModel)
    monkeypatch.setattr(huggingface_hub, "hf_hub_download", fake_download)
    monkeypatch.setattr(torch, "load", lambda *_args, **_kwargs: {"weights": True})

    model = infer_vggt_omega_video.load_model(
        None,
        "facebook/VGGT-Omega",
        torch.device("cpu"),
    )

    assert isinstance(model, FakeModel)
    assert requested == {
        "repo_id": "facebook/VGGT-Omega",
        "filename": "vggt_omega_1b_512.pt",
    }


def test_vggt_background_job_reports_progress_and_result(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(vggt_jobs, "JOBS_DIR", tmp_path / "vggt-jobs")
    with vggt_jobs._jobs_lock:
        vggt_jobs._jobs.clear()

    def fake_inference(frame_paths, frame_indices, timestamps_sec, **kwargs):
        assert len(frame_paths) == 2
        assert frame_indices == [0, 20]
        assert timestamps_sec == [0.0, 1.0]
        assert kwargs["parent_job_id"].startswith("vggt-")
        kwargs["progress_callback"](
            "reconstructing", 0.5, 1, 2, "Reconstructed frame 1/2."
        )
        return vggt_api.JSONResponse(
            {
                "metadata": {"num_frames": 2},
                "camera": {},
                "point_clouds": [
                    {
                        "frame_index": 0,
                        "sampled_frame_index": 0,
                        "num_points": 1,
                        "returned_points": 1,
                        "xyz": [[0.0, 0.0, 0.0]],
                        "rgb": [[1.0, 0.0, 0.0]],
                        "uv": [[0.0, 0.0]],
                        "conf": [1.0],
                    },
                    {
                        "frame_index": 20,
                        "sampled_frame_index": 1,
                        "num_points": 1,
                        "returned_points": 1,
                        "xyz": [[1.0, 0.0, 0.0]],
                        "rgb": [[0.0, 1.0, 0.0]],
                        "uv": [[0.0, 0.0]],
                        "conf": [1.0],
                    },
                ],
                "splat_job": {
                    "job_id": "splat-test-child",
                    "status": "queued",
                    "progress": 0.0,
                },
            }
        )

    monkeypatch.setattr(vggt_api, "_run_inference", fake_inference)
    submitted = asyncio.run(
        _request_path(
            vggt_api.app,
            "POST",
            "/jobs/vggt",
            data={
                "fps": "20",
                "start_splat": "true",
                "request_id": "request-test",
                "marker_start": "A",
                "marker_end": "B",
            },
            files=[
                ("frames", ("frame_000000.jpg", b"first", "image/jpeg")),
                ("frames", ("frame_000020.jpg", b"second", "image/jpeg")),
            ],
        )
    )
    assert submitted.status_code == 202, submitted.text
    job_id = submitted.json()["job_id"]

    state = submitted.json()
    deadline = time.monotonic() + 5
    while state["status"] not in {"complete", "failed"}:
        assert time.monotonic() < deadline
        time.sleep(0.02)
        response = asyncio.run(_request_path(vggt_api.app, "GET", f"/jobs/{job_id}"))
        assert response.status_code == 200
        state = response.json()

    assert state["status"] == "complete", state
    assert state["progress"] == 1.0
    assert state["child_job_ids"] == ["splat-test-child"]
    assert state["request_id"] == "request-test"
    result = asyncio.run(_request_path(vggt_api.app, "GET", f"/jobs/{job_id}/result"))
    assert result.status_code == 200
    assert result.json()["metadata"]["num_frames"] == 2
    manifest = asyncio.run(
        _request_path(vggt_api.app, "GET", f"/jobs/{job_id}/result/manifest")
    )
    assert manifest.status_code == 200
    assert manifest.json()["frame_count"] == 2
    assert [frame["frame_index"] for frame in manifest.json()["frames"]] == [0, 20]
    first_frame = asyncio.run(
        _request_path(vggt_api.app, "GET", f"/jobs/{job_id}/result/frames/0")
    )
    assert first_frame.status_code == 200
    assert len(first_frame.json()["point_clouds"]) == 1
    assert first_frame.json()["result_frame_index"] == 0
    assert first_frame.json()["result_frame_count"] == 2
    assert first_frame.json()["result_complete"] is False
    sampled_frame = asyncio.run(
        _request_path(
            vggt_api.app,
            "GET",
            f"/jobs/{job_id}/result/frames/0?max_points=1",
        )
    )
    assert sampled_frame.status_code == 200
    assert sampled_frame.json()["point_clouds"][0]["returned_points"] == 1
    assert len(sampled_frame.json()["point_clouds"][0]["xyz"]) == 1
    with vggt_jobs._jobs_lock:
        vggt_jobs._jobs.clear()
    recovered_jobs = asyncio.run(_request_path(vggt_api.app, "GET", "/jobs"))
    assert recovered_jobs.status_code == 200
    assert any(job["job_id"] == job_id for job in recovered_jobs.json()["jobs"])


def test_vggt_batches_more_than_96_frames_instead_of_rejecting(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(vggt_jobs, "JOBS_DIR", tmp_path / "vggt-jobs")
    with vggt_jobs._jobs_lock:
        vggt_jobs._jobs.clear()
    received: dict[str, object] = {}

    def fake_inference(frame_paths, frame_indices, timestamps_sec, **kwargs):
        received["frame_count"] = len(frame_paths)
        received["indices"] = frame_indices
        received["timestamps"] = timestamps_sec
        return vggt_api.JSONResponse(
            {
                "metadata": {
                    "num_frames": len(frame_paths),
                    "chunk_count": len(
                        vggt_api._inference_chunk_ranges(
                            len(frame_paths), kwargs["window"]
                        )
                    ),
                },
                "camera": {},
                    "point_clouds": [
                        {
                            "frame_index": index,
                            "sampled_frame_index": index,
                            "num_points": 1,
                            "returned_points": 1,
                            "xyz": [[float(index), 0.0, 0.0]],
                            "rgb": [[1.0, 1.0, 1.0]],
                            "uv": [[0.0, 0.0]],
                            "conf": [1.0],
                        }
                        for index in range(len(frame_paths))
                    ],
            }
        )

    monkeypatch.setattr(vggt_api, "_run_inference", fake_inference)
    frame_count = 193
    submitted = asyncio.run(
        _request_path(
            vggt_api.app,
            "POST",
            "/jobs/vggt",
            data={"fps": "20", "window": "0", "start_splat": "false"},
            files=[
                (
                    "frames",
                    (f"frame_{index:06d}.jpg", f"frame-{index}".encode(), "image/jpeg"),
                )
                for index in range(frame_count)
            ],
        )
    )

    assert submitted.status_code == 202, submitted.text
    assert vggt_api._inference_chunk_ranges(frame_count, 0) == [
        (0, 96),
        (96, 192),
        (192, 193),
    ]
    assert vggt_api._inference_chunk_ranges(frame_count, 200) == [
        (0, 96),
        (96, 192),
        (192, 193),
    ]
    assert vggt_api._inference_chunk_ranges(100, 32) == [
        (0, 32),
        (32, 64),
        (64, 96),
        (96, 100),
    ]

    job_id = submitted.json()["job_id"]
    state = submitted.json()
    deadline = time.monotonic() + 5
    while state["status"] not in {"complete", "failed"}:
        assert time.monotonic() < deadline
        time.sleep(0.02)
        state = asyncio.run(
            _request_path(vggt_api.app, "GET", f"/jobs/{job_id}")
        ).json()

    assert state["status"] == "complete", state
    assert received["frame_count"] == frame_count
    assert received["indices"] == list(range(frame_count))
    result = asyncio.run(_request_path(vggt_api.app, "GET", f"/jobs/{job_id}/result"))
    assert result.status_code == 200
    assert result.json()["metadata"]["chunk_count"] == 3
