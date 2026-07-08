from __future__ import annotations

import asyncio
import gc
import io
import json
import os
import shutil
import sys
import tempfile
import threading
import time
import zipfile
from pathlib import Path
from typing import Literal

import numpy as np
from fastapi import FastAPI, File, Form, Header, HTTPException, Query, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.infer_vggt_omega_video import (  # noqa: E402
    camera_to_world,
    depth_to_pointcloud,
    extract_video_frames,
    load_model,
    run_vggt_window,
)
from services.splat_jobs import get_splat_job, start_splat_job  # noqa: E402

import torch  # noqa: E402

DEFAULT_CKPT = ROOT / "checkpoints" / "vggt_omega" / "vggt_omega_1b_512.pt"
MODEL_ID = os.environ.get("VGGT_MODEL_ID", "facebook/VGGT-Omega-1B-512")
CKPT = Path(os.environ.get("VGGT_CKPT", str(DEFAULT_CKPT))) if os.environ.get("VGGT_CKPT", str(DEFAULT_CKPT)) else None
DEVICE_NAME = os.environ.get("VGGT_DEVICE", "cuda" if torch.cuda.is_available() else "cpu")

app = FastAPI(title="BayesMech VGGT-Omega Inference", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

_model = None
_model_lock = threading.Lock()
_infer_lock = threading.Lock()
API_TOKEN = os.environ.get("VGGT_API_TOKEN", "").strip()


def _authorize(authorization: str | None = None, x_vggt_token: str | None = None) -> None:
    if not API_TOKEN:
        return
    candidates: list[str] = []
    if authorization:
        scheme, _, value = authorization.partition(" ")
        candidates.append(value if scheme.lower() == "bearer" and value else authorization)
    if x_vggt_token:
        candidates.append(x_vggt_token)
    if API_TOKEN not in candidates:
        raise HTTPException(status_code=401, detail="missing or invalid VGGT API token")


def _device() -> torch.device:
    return torch.device(DEVICE_NAME)


def get_model():
    global _model
    if _model is not None:
        return _model
    with _model_lock:
        if _model is None:
            ckpt = str(CKPT) if CKPT and CKPT.exists() else None
            _model = load_model(ckpt, MODEL_ID, _device())
        return _model


def unload_model() -> None:
    global _model
    with _model_lock:
        _model = None
    gc.collect()
    if torch.cuda.is_available():
        torch.cuda.empty_cache()
        try:
            torch.cuda.ipc_collect()
        except Exception:
            pass


def _finite_float(value: float) -> float | None:
    value = float(value)
    return value if np.isfinite(value) else None


def _sample_indices(count: int, max_count: int) -> np.ndarray:
    if max_count <= 0 or count <= max_count:
        return np.arange(count, dtype=np.int64)
    return np.linspace(0, count - 1, max_count, dtype=np.int64)


def _npz_bytes(**arrays) -> bytes:
    buffer = io.BytesIO()
    np.savez_compressed(buffer, **arrays)
    return buffer.getvalue()


def _run_inference(
    frame_paths: list[Path],
    frame_indices: list[int],
    timestamps_sec: list[float],
    *,
    resolution: int,
    preprocess_mode: Literal["balanced", "max_size"],
    conf_thresh: float,
    window: int,
    max_points_per_frame: int,
    response_format: Literal["json", "npz"],
    start_splat: bool,
) -> Response:
    if resolution <= 0:
        raise ValueError("resolution must be positive")
    if conf_thresh < 0:
        raise ValueError("conf_thresh must be >= 0")
    if window < 0:
        raise ValueError("window must be >= 0")
    if not frame_paths:
        raise ValueError("no frames provided")

    started = time.time()
    model = get_model()
    device = _device()
    window_size = window or len(frame_paths)

    camera_extrinsics: list[np.ndarray] = []
    camera_intrinsics: list[np.ndarray] = []
    camera_to_world_out: list[np.ndarray] = []
    camera_centers: list[np.ndarray] = []
    image_sizes: list[tuple[int, int]] = []
    pointclouds_json = []
    pointcloud_npz: list[tuple[str, dict[str, np.ndarray]]] = []
    splat_frames: list[dict] = []

    with _infer_lock:
        for start in range(0, len(frame_paths), window_size):
            end = min(start + window_size, len(frame_paths))
            result = run_vggt_window(model, frame_paths[start:end], resolution, preprocess_mode, device)

            for local_idx in range(end - start):
                global_idx = start + local_idx
                pc = depth_to_pointcloud(
                    result["depth"][local_idx],
                    result["intrinsics"][local_idx],
                    result["extrinsics"][local_idx],
                    conf=result["depth_conf"][local_idx],
                    conf_thresh=conf_thresh,
                )
                rgb = result["images"][local_idx].permute(1, 2, 0).reshape(-1, 3).numpy()[pc["valid_mask"]].astype(np.float32)
                conf = pc["conf"].reshape(-1)[pc["valid_mask"]].astype(np.float32) if pc["conf"] is not None else None
                if conf is None:
                    conf = np.ones(len(pc["xyz"]), dtype=np.float32)

                extrinsic = result["extrinsics"][local_idx].numpy().astype(np.float32)
                intrinsic = result["intrinsics"][local_idx].numpy().astype(np.float32)
                c2w = camera_to_world(extrinsic)
                camera_extrinsics.append(extrinsic)
                camera_intrinsics.append(intrinsic)
                camera_to_world_out.append(c2w)
                camera_centers.append(c2w[:3, 3])
                image_sizes.append(tuple(int(v) for v in result["image_size"]))

                frame_name = f"frame_{global_idx:06d}"
                arrays = {
                    "xyz": pc["xyz"].astype(np.float32),
                    "rgb": rgb,
                    "uv": pc["uv"].astype(np.float32),
                    "flat_indices": pc["flat_indices"].astype(np.int64),
                    "depth": pc["depth"].astype(np.float32),
                }
                if conf is not None:
                    arrays["conf"] = conf
                pointcloud_npz.append((frame_name, arrays))
                if start_splat:
                    splat_frames.append(
                        {
                            "source_frame_index": int(frame_indices[global_idx]),
                            "timestamp_sec": _finite_float(timestamps_sec[global_idx]),
                            "image_rgb": np.clip(result["images"][local_idx].permute(1, 2, 0).numpy() * 255.0, 0, 255).astype(np.uint8),
                            "xyz": arrays["xyz"],
                            "rgb": arrays["rgb"],
                            "conf": arrays["conf"],
                            "camera_to_world": c2w,
                            "intrinsics": intrinsic,
                        }
                    )

                keep = _sample_indices(len(arrays["xyz"]), max_points_per_frame)
                pointclouds_json.append(
                    {
                        "frame_index": int(frame_indices[global_idx]),
                        "sampled_frame_index": global_idx,
                        "timestamp_sec": _finite_float(timestamps_sec[global_idx]),
                        "num_points": int(len(arrays["xyz"])),
                        "returned_points": int(len(keep)),
                        "xyz": arrays["xyz"][keep].tolist(),
                        "rgb": arrays["rgb"][keep].tolist(),
                        "uv": arrays["uv"][keep].tolist(),
                        "conf": arrays["conf"][keep].tolist() if "conf" in arrays else None,
                    }
                )

        if torch.cuda.is_available():
            torch.cuda.empty_cache()

    if start_splat:
        model = None
        unload_model()

    metadata = {
        "model": "VGGT-Omega",
        "checkpoint": str(CKPT) if CKPT else None,
        "device": str(device),
        "num_frames": len(frame_paths),
        "frame_indices": [int(v) for v in frame_indices],
        "timestamps_sec": [_finite_float(v) for v in timestamps_sec],
        "image_sizes_hw": image_sizes,
        "resolution": resolution,
        "preprocess_mode": preprocess_mode,
        "conf_thresh": conf_thresh,
        "window": window_size,
        "elapsed_sec": time.time() - started,
        "camera_convention": "extrinsics are camera-from-world [R|t], OpenCV coordinates",
        "pointcloud_space": "world coordinates in VGGT-Omega model frame",
        "pointcloud_npz_keys": ["xyz", "rgb", "uv", "flat_indices", "depth", "conf"],
    }

    extrinsics = np.stack(camera_extrinsics).astype(np.float32)
    intrinsics = np.stack(camera_intrinsics).astype(np.float32)
    c2w = np.stack(camera_to_world_out).astype(np.float32)
    centers = np.stack(camera_centers).astype(np.float32)

    if response_format == "npz":
        archive = io.BytesIO()
        with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("metadata.json", json.dumps(metadata, indent=2))
            zf.writestr(
                "camera_trajectory.npz",
                _npz_bytes(
                    extrinsics=extrinsics,
                    intrinsics=intrinsics,
                    camera_to_world=c2w,
                    camera_centers=centers,
                    source_frame_indices=np.asarray(frame_indices, dtype=np.int64),
                    timestamps_sec=np.asarray(timestamps_sec, dtype=np.float32),
                ),
            )
            for frame_name, arrays in pointcloud_npz:
                zf.writestr(f"pointclouds/{frame_name}.npz", _npz_bytes(**arrays))
        return Response(
            archive.getvalue(),
            media_type="application/zip",
            headers={"Content-Disposition": "attachment; filename=vggt_omega_result.zip"},
        )

    payload = {
        "metadata": metadata,
        "camera": {
            "extrinsics": extrinsics.tolist(),
            "intrinsics": intrinsics.tolist(),
            "camera_to_world": c2w.tolist(),
            "camera_centers": centers.tolist(),
        },
        "point_clouds": pointclouds_json,
    }
    if start_splat:
        job = start_splat_job(splat_frames, metadata, _infer_lock)
        payload["splat_job"] = job
    return JSONResponse(payload)


def _safe_suffix(name: str | None, fallback: str = ".mp4") -> str:
    suffix = Path(name or "").suffix.lower()
    return suffix if suffix in {".mp4", ".mov", ".webm", ".mkv", ".avi", ".jpg", ".jpeg", ".png", ".webp"} else fallback


@app.get("/health")
def health():
    return {
        "ok": True,
        "device": DEVICE_NAME,
        "cuda_available": torch.cuda.is_available(),
        "cuda_device": torch.cuda.get_device_name(0) if torch.cuda.is_available() else None,
        "checkpoint_exists": bool(CKPT and CKPT.exists()),
        "model_loaded": _model is not None,
        "auth_required": bool(API_TOKEN),
    }


@app.post("/infer")
async def infer_multipart(
    video: UploadFile | None = File(default=None),
    frames: list[UploadFile] | None = File(default=None),
    every_n: int = Form(default=1),
    max_frames: int | None = Form(default=16),
    fps: float | None = Form(default=None),
    resolution: int = Form(default=512),
    preprocess_mode: Literal["balanced", "max_size"] = Form(default="balanced"),
    conf_thresh: float = Form(default=0.5),
    window: int = Form(default=0),
    max_points_per_frame: int = Form(default=20000),
    response_format: Literal["json", "npz"] = Form(default="json"),
    start_splat: bool = Form(default=False),
    authorization: str | None = Header(default=None),
    x_vggt_token: str | None = Header(default=None),
):
    _authorize(authorization, x_vggt_token)
    if video is None and not frames:
        raise HTTPException(status_code=400, detail="Upload either a video file field named 'video' or repeated image fields named 'frames'.")
    if every_n < 1:
        raise HTTPException(status_code=400, detail="every_n must be >= 1")

    temp_dir = Path(tempfile.mkdtemp(prefix="vggt_api_"))
    try:
        if video is not None:
            video_path = temp_dir / f"input{_safe_suffix(video.filename, '.mp4')}"
            video_path.write_bytes(await video.read())
            frame_dir = temp_dir / "frames"
            frame_dir.mkdir()
            frame_paths, frame_indices, timestamps_sec = extract_video_frames(video_path, frame_dir, every_n=every_n, max_frames=max_frames)
        else:
            frame_dir = temp_dir / "frames"
            frame_dir.mkdir()
            selected = list(frames or [])[: max_frames or None]
            frame_paths = []
            for idx, upload in enumerate(selected):
                path = frame_dir / f"frame_{idx:06d}{_safe_suffix(upload.filename, '.png')}"
                path.write_bytes(await upload.read())
                frame_paths.append(path)
            frame_indices = list(range(len(frame_paths)))
            timestamps_sec = [idx / fps if fps and fps > 0 else float("nan") for idx in frame_indices]

        return await asyncio.to_thread(
            _run_inference,
            frame_paths,
            frame_indices,
            timestamps_sec,
            resolution=resolution,
            preprocess_mode=preprocess_mode,
            conf_thresh=conf_thresh,
            window=window,
            max_points_per_frame=max_points_per_frame,
            response_format=response_format,
            start_splat=start_splat and response_format == "json",
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


@app.post("/infer/video-bytes")
async def infer_video_bytes(
    request: Request,
    filename: str = Query(default="input.mp4"),
    every_n: int = Query(default=1),
    max_frames: int | None = Query(default=16),
    resolution: int = Query(default=512),
    preprocess_mode: Literal["balanced", "max_size"] = Query(default="balanced"),
    conf_thresh: float = Query(default=0.5),
    window: int = Query(default=0),
    max_points_per_frame: int = Query(default=20000),
    response_format: Literal["json", "npz"] = Query(default="json"),
    start_splat: bool = Query(default=False),
    authorization: str | None = Header(default=None),
    x_vggt_token: str | None = Header(default=None),
):
    _authorize(authorization, x_vggt_token)
    body = await request.body()
    if not body:
        raise HTTPException(status_code=400, detail="empty request body")

    temp_dir = Path(tempfile.mkdtemp(prefix="vggt_api_"))
    try:
        video_path = temp_dir / f"input{_safe_suffix(filename, '.mp4')}"
        video_path.write_bytes(body)
        frame_dir = temp_dir / "frames"
        frame_dir.mkdir()
        frame_paths, frame_indices, timestamps_sec = extract_video_frames(video_path, frame_dir, every_n=every_n, max_frames=max_frames)
        return await asyncio.to_thread(
            _run_inference,
            frame_paths,
            frame_indices,
            timestamps_sec,
            resolution=resolution,
            preprocess_mode=preprocess_mode,
            conf_thresh=conf_thresh,
            window=window,
            max_points_per_frame=max_points_per_frame,
            response_format=response_format,
            start_splat=start_splat and response_format == "json",
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


@app.get("/splat/{job_id}")
def splat_status(
    job_id: str,
    include_preview: bool = Query(default=False),
    authorization: str | None = Header(default=None),
    x_vggt_token: str | None = Header(default=None),
):
    _authorize(authorization, x_vggt_token)
    job = get_splat_job(job_id, include_preview=include_preview)
    if job is None:
        raise HTTPException(status_code=404, detail="splat job not found")
    return job
