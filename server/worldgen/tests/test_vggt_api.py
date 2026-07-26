from __future__ import annotations

import asyncio
from pathlib import Path

import httpx
import torch
from fastapi import FastAPI

from worldgen.services import vggt_api
from worldgen.scripts import infer_vggt_omega_video


async def _request(app: FastAPI, **kwargs) -> httpx.Response:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://worldgen.test") as client:
        return await client.post("/infer", **kwargs)


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


def test_model_download_uses_official_omega_repository_and_filename(monkeypatch, tmp_path: Path) -> None:
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
