from __future__ import annotations

import json
import logging
import math
import os
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
JOBS_DIR = Path(os.environ.get("WORLDGEN_SPLAT_JOBS_DIR", str(ROOT / "outputs" / "splat_jobs")))

log = logging.getLogger(__name__)

_SH_C0 = 0.28209479177387814
_jobs: dict[str, dict[str, Any]] = {}
_jobs_lock = threading.Lock()
_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="worldgen-splat")


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


def splat_config() -> dict[str, Any]:
    return {
        "max_steps": max(1, _env_int("WORLDGEN_SPLAT_STEPS", 30000)),
        "max_gaussians": max(1000, _env_int("WORLDGEN_SPLAT_MAX_GAUSSIANS", 1000000)),
        "max_init_points": max(1000, _env_int("WORLDGEN_SPLAT_MAX_INIT_POINTS", 180000)),
        "max_points_per_frame": max(1000, _env_int("WORLDGEN_SPLAT_MAX_POINTS_PER_FRAME", 60000)),
        "min_confidence": max(0.0, _env_float("WORLDGEN_SPLAT_MIN_CONFIDENCE", 0.55)),
        "outlier_quantile": min(1.0, max(0.90, _env_float("WORLDGEN_SPLAT_OUTLIER_QUANTILE", 0.995))),
        "preview_points": max(1000, _env_int("WORLDGEN_SPLAT_PREVIEW_POINTS", 100000)),
        "sh_degree": max(0, min(3, _env_int("WORLDGEN_SPLAT_SH_DEGREE", 3))),
        "data_factor": max(1, _env_int("WORLDGEN_SPLAT_DATA_FACTOR", 1)),
    }


def _job_path(job_id: str) -> Path:
    return JOBS_DIR / job_id


def _write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _update_job(job_id: str, **fields) -> dict[str, Any]:
    with _jobs_lock:
        state = _jobs.setdefault(job_id, {"job_id": job_id})
        state.update(fields)
        state["updated_at"] = time.time()
        serializable = {k: v for k, v in state.items() if k != "_future"}
    _write_json(_job_path(job_id) / "status.json", serializable)
    return serializable


def get_splat_job(job_id: str, include_preview: bool = False) -> dict[str, Any] | None:
    with _jobs_lock:
        state = _jobs.get(job_id)
        if state is not None:
            out = {k: v for k, v in state.items() if k != "_future"}
        else:
            out = _read_json(_job_path(job_id) / "status.json")
    if out is None:
        return None
    if include_preview and out.get("status") == "complete":
        preview = _read_json(_job_path(job_id) / "preview.json")
        if preview:
            out["preview"] = preview
    return out


def start_splat_job(frames: list[dict[str, Any]], metadata: dict[str, Any], gpu_lock: threading.Lock | None = None) -> dict[str, Any]:
    job_id = f"splat-{uuid.uuid4().hex[:12]}"
    workspace = _job_path(job_id)
    frame_dir = workspace / "images"
    point_dir = workspace / "pointclouds"
    frame_dir.mkdir(parents=True, exist_ok=True)
    point_dir.mkdir(parents=True, exist_ok=True)

    image_paths: list[str] = []
    for idx, frame in enumerate(frames):
        image_path = frame_dir / f"frame_{idx:06d}.jpg"
        image_rgb = np.asarray(frame["image_rgb"], dtype=np.uint8)
        cv2.imwrite(str(image_path), cv2.cvtColor(image_rgb, cv2.COLOR_RGB2BGR), [cv2.IMWRITE_JPEG_QUALITY, 96])
        np.savez_compressed(
            point_dir / f"frame_{idx:06d}.npz",
            xyz=np.asarray(frame["xyz"], dtype=np.float32),
            rgb=np.asarray(frame["rgb"], dtype=np.float32),
            conf=np.asarray(frame["conf"], dtype=np.float32),
            c2w=np.asarray(frame["camera_to_world"], dtype=np.float64),
            K=np.asarray(frame["intrinsics"], dtype=np.float64),
            source_frame_index=np.asarray([int(frame.get("source_frame_index", idx))], dtype=np.int64),
        )
        image_paths.append(str(image_path))

    cfg = splat_config()
    _write_json(workspace / "metadata.json", {"metadata": metadata, "config": cfg, "image_paths": image_paths})
    state = _update_job(
        job_id,
        status="queued",
        stage="queued",
        message="Queued Gaussian splat optimization.",
        progress=0.0,
        current_step=0,
        max_steps=cfg["max_steps"],
        gaussian_count=0,
        elapsed_sec=0.0,
        workspace=str(workspace),
        ply_path=str(workspace / "model.splat.ply"),
        preview_json_path=str(workspace / "preview.json"),
        created_at=time.time(),
    )
    future = _executor.submit(_run_splat_job, job_id, gpu_lock)
    with _jobs_lock:
        _jobs[job_id]["_future"] = future
    return state


def _run_splat_job(job_id: str, gpu_lock: threading.Lock | None) -> None:
    lock = gpu_lock or threading.Lock()
    with lock:
        _train_splat_job(job_id)


def _train_splat_job(job_id: str) -> None:
    started = time.time()
    workspace = _job_path(job_id)
    try:
        import torch
        import torch.nn.functional as F
        from gsplat import rasterization
        from gsplat.strategy import MCMCStrategy
        from plyfile import PlyData, PlyElement
    except Exception as exc:
        _update_job(job_id, status="failed", stage="import", error=str(exc), message=f"Splat imports failed: {exc}")
        return

    try:
        cfg = (_read_json(workspace / "metadata.json") or {}).get("config", splat_config())
        _update_job(job_id, status="running", stage="preparing", message="Preparing splat training data.")
        dataset = _load_dataset(workspace, cfg)
        device = "cuda" if torch.cuda.is_available() else "cpu"
        points = torch.from_numpy(dataset["points"]).float().to(device)
        colors = torch.from_numpy(dataset["colors"]).float().to(device) / 255.0
        splats = _init_gaussians(points, colors, device, cfg["sh_degree"], torch)
        optimizers = _optimizers(splats, _scene_scale(dataset["points"]), torch)

        strategy = None
        strategy_state = {}
        try:
            strategy = MCMCStrategy(
                cap_max=cfg["max_gaussians"],
                min_opacity=0.005,
                refine_start_iter=500,
                refine_stop_iter=cfg["max_steps"],
                refine_every=100,
            )
            strategy.check_sanity(splats, optimizers)
            strategy_state = strategy.initialize_state()
        except Exception as exc:
            log.warning("Splat job %s: MCMC disabled: %s", job_id, exc)
            strategy = None

        views = _load_views(dataset, cfg["data_factor"], device, torch)
        if not views:
            raise RuntimeError("No training views could be loaded.")

        max_steps = cfg["max_steps"]
        sh_degree = cfg["sh_degree"]
        _update_job(
            job_id,
            status="running",
            stage="training",
            message=f"Optimizing Gaussian splat from {len(points)} initial points and {len(views)} views.",
            init_point_count=int(len(points)),
            training_frame_count=int(len(views)),
            max_steps=int(max_steps),
            gaussian_count=int(len(splats["means"])),
        )

        for step in range(1, max_steps + 1):
            view = views[(step - 1) % len(views)]
            camtoworld = view["camtoworld"].unsqueeze(0)
            K = view["K"].unsqueeze(0)
            pixels = view["pixels"]
            sh_active = min(sh_degree, step // (max_steps // (sh_degree + 1) + 1))
            sh_coeffs = torch.cat([splats["sh0"], splats["shN"]], dim=1)

            renders, _alphas, info = rasterization(
                means=splats["means"],
                quats=splats["quats"] / splats["quats"].norm(dim=-1, keepdim=True),
                scales=torch.exp(splats["scales"]),
                opacities=torch.sigmoid(splats["opacities"]),
                colors=sh_coeffs,
                viewmats=torch.linalg.inv(camtoworld),
                Ks=K,
                width=view["width"],
                height=view["height"],
                sh_degree=sh_active,
                near_plane=0.01,
                far_plane=1000.0,
                packed=True,
            )
            loss = F.l1_loss(renders.squeeze(0), pixels)
            if strategy is not None:
                try:
                    strategy.step_pre_backward(params=splats, optimizers=optimizers, state=strategy_state, step=step, info=info)
                except Exception:
                    strategy = None
            loss.backward()
            for opt in optimizers.values():
                opt.step()
                opt.zero_grad(set_to_none=True)
            if strategy is not None:
                try:
                    strategy.step_post_backward(
                        params=splats,
                        optimizers=optimizers,
                        state=strategy_state,
                        step=step,
                        info=info,
                        lr=optimizers["means"].param_groups[0]["lr"],
                    )
                except Exception:
                    strategy = None

            if step == 1 or step % 100 == 0 or step == max_steps:
                _update_job(
                    job_id,
                    status="running",
                    stage="training",
                    message=f"Optimizing Gaussian splat: step {step}/{max_steps}, loss {loss.item():.4f}.",
                    current_step=int(step),
                    max_steps=int(max_steps),
                    progress=float(step / max_steps),
                    gaussian_count=int(len(splats["means"])),
                    loss=float(loss.item()),
                    elapsed_sec=float(time.time() - started),
                )

        _update_job(job_id, status="running", stage="exporting", message="Exporting Gaussian splat preview and PLY.")
        ply_path = workspace / "model.splat.ply"
        _export_ply(splats, ply_path, torch, PlyData, PlyElement)
        preview = _write_preview(splats, workspace / "preview.json", cfg["preview_points"], torch)
        _update_job(
            job_id,
            status="complete",
            stage="complete",
            message="Gaussian splat optimization complete.",
            current_step=int(max_steps),
            max_steps=int(max_steps),
            progress=1.0,
            gaussian_count=int(preview["gaussian_count"]),
            preview_point_count=int(preview["preview_point_count"]),
            elapsed_sec=float(time.time() - started),
            ply_path=str(ply_path),
            preview_json_path=str(workspace / "preview.json"),
        )
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
    except Exception as exc:
        log.exception("Splat job %s failed", job_id)
        _update_job(
            job_id,
            status="failed",
            stage="failed",
            message=f"Gaussian splat optimization failed: {exc}",
            error=str(exc),
            elapsed_sec=float(time.time() - started),
        )
        if "torch" in locals() and torch.cuda.is_available():
            torch.cuda.empty_cache()


def _load_dataset(workspace: Path, cfg: dict[str, Any]) -> dict[str, Any]:
    rng = np.random.default_rng(7)
    points_all = []
    colors_all = []
    conf_all = []
    image_paths = []
    camtoworlds = []
    intrinsics = []
    for point_path in sorted((workspace / "pointclouds").glob("frame_*.npz")):
        data = np.load(point_path)
        xyz = np.asarray(data["xyz"], dtype=np.float32)
        rgb = np.asarray(data["rgb"], dtype=np.float32)
        conf = np.asarray(data["conf"], dtype=np.float32)
        finite = np.isfinite(xyz).all(axis=1) & np.isfinite(rgb).all(axis=1) & np.isfinite(conf)
        keep = finite & (conf >= cfg["min_confidence"])
        if keep.sum() < min(1000, max(1, finite.sum() // 20)):
            keep = finite & (conf >= max(0.05, cfg["min_confidence"] * 0.5))
        xyz = xyz[keep]
        rgb = rgb[keep]
        conf = conf[keep]
        if len(xyz):
            selected = _sample_indices(conf, cfg["max_points_per_frame"], rng)
            points_all.append(xyz[selected])
            colors_all.append(np.round(np.clip(rgb[selected], 0.0, 1.0) * 255.0).astype(np.uint8))
            conf_all.append(conf[selected])
        frame_name = point_path.stem + ".jpg"
        image_paths.append(workspace / "images" / frame_name)
        camtoworlds.append(np.asarray(data["c2w"], dtype=np.float64))
        intrinsics.append(np.asarray(data["K"], dtype=np.float64))

    if not points_all:
        raise RuntimeError("No VGGT points survived confidence filtering.")
    points = np.concatenate(points_all, axis=0)
    colors = np.concatenate(colors_all, axis=0)
    conf = np.concatenate(conf_all, axis=0)
    points, colors, conf = _filter_outliers(points, colors, conf, cfg["outlier_quantile"])
    if len(points) > cfg["max_init_points"]:
        selected = _sample_indices(conf, cfg["max_init_points"], rng)
        points = points[selected]
        colors = colors[selected]
    if len(points) < 1000:
        raise RuntimeError(f"Only {len(points)} VGGT points survived filtering.")
    return {
        "points": points.astype(np.float32),
        "colors": colors.astype(np.uint8),
        "camtoworlds": np.stack(camtoworlds).astype(np.float64),
        "Ks": np.stack(intrinsics).astype(np.float64),
        "image_paths": image_paths,
    }


def _sample_indices(weights: np.ndarray, limit: int, rng: np.random.Generator) -> np.ndarray:
    count = len(weights)
    if limit <= 0 or count <= limit:
        return np.arange(count, dtype=np.int64)
    probs = np.clip(weights.astype(np.float64), 0.0, None) + 1e-4
    probs /= probs.sum()
    return np.sort(rng.choice(count, size=limit, replace=False, p=probs))


def _filter_outliers(points, colors, conf, quantile):
    if len(points) < 5000 or quantile >= 1.0:
        return points, colors, conf
    center = np.median(points, axis=0)
    dist = np.linalg.norm(points - center, axis=1)
    keep = dist <= np.quantile(dist, quantile)
    return points[keep], colors[keep], conf[keep]


def _load_views(dataset, data_factor: int, device: str, torch):
    views = []
    for idx, image_path in enumerate(dataset["image_paths"]):
        raw = cv2.imread(str(image_path))
        if raw is None:
            continue
        img = cv2.cvtColor(raw, cv2.COLOR_BGR2RGB)
        K = dataset["Ks"][idx].copy()
        if data_factor > 1:
            h, w = img.shape[:2]
            img = cv2.resize(img, (w // data_factor, h // data_factor), interpolation=cv2.INTER_AREA)
            K[0, :] /= data_factor
            K[1, :] /= data_factor
        pixels = torch.from_numpy(img).float().to(device) / 255.0
        views.append({
            "pixels": pixels,
            "camtoworld": torch.from_numpy(dataset["camtoworlds"][idx]).float().to(device),
            "K": torch.from_numpy(K).float().to(device),
            "height": int(pixels.shape[0]),
            "width": int(pixels.shape[1]),
        })
    return views


def _scene_scale(points: np.ndarray) -> float:
    extents = points.max(0) - points.min(0)
    return float(max(extents.max(), 1.0))


def _init_gaussians(points, colors, device: str, sh_degree: int, torch):
    n = len(points)
    dist = _knn_dist(points, torch)
    neighbour = dist[:, 1:] if dist.shape[1] > 1 else dist
    scales = torch.log(torch.sqrt(neighbour.mean(dim=-1, keepdim=True).clamp_min(1e-6))).repeat(1, 3)
    quats = torch.zeros(n, 4, device=device)
    quats[:, 0] = 1.0
    opacities = torch.logit(torch.full((n,), 0.1, device=device))
    sh = torch.zeros(n, (sh_degree + 1) ** 2, 3, device=device)
    sh[:, 0, :] = (colors - 0.5) / _SH_C0
    return {
        "means": torch.nn.Parameter(points.clone()),
        "scales": torch.nn.Parameter(scales.to(device)),
        "quats": torch.nn.Parameter(quats),
        "opacities": torch.nn.Parameter(opacities),
        "sh0": torch.nn.Parameter(sh[:, :1, :]),
        "shN": torch.nn.Parameter(sh[:, 1:, :]),
    }


def _knn_dist(points, torch):
    pts = points.float()
    if len(pts) <= 1:
        return torch.ones((len(pts), 1), device=points.device, dtype=points.dtype) * 0.01
    k = min(4, len(pts))
    try:
        from sklearn.neighbors import NearestNeighbors
        pts_np = pts.detach().cpu().numpy()
        nn = NearestNeighbors(n_neighbors=k, algorithm="auto", n_jobs=-1)
        nn.fit(pts_np)
        dist, _idx = nn.kneighbors(pts_np, return_distance=True)
        return torch.from_numpy(dist).to(device=points.device, dtype=points.dtype)
    except Exception:
        batch = min(4096, len(pts))
        dists = []
        for i in range(0, len(pts), batch):
            d = torch.cdist(pts[i:i + batch], pts)
            d_sorted, _ = d.sort(dim=-1)
            dists.append(d_sorted[:, :k])
        return torch.cat(dists, dim=0)


def _optimizers(splats, scene_scale: float, torch):
    from torch.optim import Adam
    return {
        "means": Adam([splats["means"]], lr=1.6e-4 * scene_scale),
        "scales": Adam([splats["scales"]], lr=5e-3),
        "quats": Adam([splats["quats"]], lr=1e-3),
        "opacities": Adam([splats["opacities"]], lr=5e-2),
        "sh0": Adam([splats["sh0"]], lr=2.5e-3),
        "shN": Adam([splats["shN"]], lr=2.5e-4 / 20),
    }


def _export_ply(splats, output_ply: Path, torch, PlyData, PlyElement) -> None:
    output_ply.parent.mkdir(parents=True, exist_ok=True)
    means = splats["means"].detach().cpu().numpy().astype(np.float32)
    scales = splats["scales"].detach().cpu().numpy().astype(np.float32)
    quats = splats["quats"].detach().cpu().numpy().astype(np.float32)
    opacities = splats["opacities"].detach().cpu().numpy().astype(np.float32)
    sh0 = splats["sh0"].detach().cpu().permute(0, 2, 1).reshape(len(means), -1).numpy().astype(np.float32)
    shn = splats["shN"].detach().cpu().permute(0, 2, 1).reshape(len(means), -1).numpy().astype(np.float32)
    fields = [
        ("x", "f4"), ("y", "f4"), ("z", "f4"),
        ("nx", "f4"), ("ny", "f4"), ("nz", "f4"),
    ]
    fields += [(f"f_dc_{i}", "f4") for i in range(sh0.shape[1])]
    fields += [(f"f_rest_{i}", "f4") for i in range(shn.shape[1])]
    fields += [("opacity", "f4")]
    fields += [(f"scale_{i}", "f4") for i in range(scales.shape[1])]
    fields += [(f"rot_{i}", "f4") for i in range(quats.shape[1])]
    rows = np.empty(len(means), dtype=fields)
    rows["x"], rows["y"], rows["z"] = means[:, 0], means[:, 1], means[:, 2]
    rows["nx"], rows["ny"], rows["nz"] = 0, 0, 0
    for i in range(sh0.shape[1]):
        rows[f"f_dc_{i}"] = sh0[:, i]
    for i in range(shn.shape[1]):
        rows[f"f_rest_{i}"] = shn[:, i]
    rows["opacity"] = opacities
    for i in range(scales.shape[1]):
        rows[f"scale_{i}"] = scales[:, i]
    for i in range(quats.shape[1]):
        rows[f"rot_{i}"] = quats[:, i]
    PlyData([PlyElement.describe(rows, "vertex")], text=False).write(str(output_ply))


def _write_preview(splats, output_json: Path, max_points: int, torch) -> dict[str, Any]:
    means = splats["means"].detach().cpu().numpy()
    scales = np.exp(splats["scales"].detach().cpu().numpy()).mean(axis=1)
    opacities = 1.0 / (1.0 + np.exp(-splats["opacities"].detach().cpu().numpy()))
    sh0 = splats["sh0"].detach().cpu().numpy()[:, 0, :]
    colors = np.clip(sh0 * _SH_C0 + 0.5, 0.0, 1.0)
    rng = np.random.default_rng(11)
    keep = _sample_indices(opacities.astype(np.float32), max_points, rng)
    points = [
        {
            "x": float(means[i, 0]), "y": float(means[i, 1]), "z": float(means[i, 2]),
            "r": float(colors[i, 0]), "g": float(colors[i, 1]), "b": float(colors[i, 2]),
            "opacity": float(opacities[i]), "scale": float(scales[i]),
        }
        for i in keep
    ]
    data = {
        "status": "complete",
        "gaussian_count": int(len(means)),
        "preview_point_count": int(len(points)),
        "points": points,
    }
    _write_json(output_json, data)
    return data
