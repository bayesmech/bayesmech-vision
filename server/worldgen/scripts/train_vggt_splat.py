#!/usr/bin/env python3
"""
Train a 3D Gaussian Splatting model from a saved VGGT inference response.

Inputs:
  - VggtInferenceResponse length-delimited protobuf from the Electron
    /worldgen command.
  - The source .vis.pb recording referenced by that response.

The trainer uses VGGT's world-space point clouds for initialization and VGGT's
camera poses/intrinsics for photometric training against the same center-crop
and resize preprocessing used by VGGT-Omega.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import struct
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image

_server_root = Path(__file__).resolve().parents[2]
_project_root = _server_root.parent
sys.path.insert(0, str(_project_root))
sys.path.insert(0, str(_project_root / "proto"))
sys.path.insert(0, str(_server_root))

import perceiver_pb2  # noqa: E402
try:
    import vggt_pb2  # noqa: E402
except ModuleNotFoundError:
    subprocess.run([str(_project_root / "proto" / "generate_proto.sh")], cwd=_project_root / "proto", check=True)
    import vggt_pb2  # noqa: E402

from motioncap.geometry import decode_frame_rgb  # noqa: E402
from reconstruct.gsplat_trainer import _load_ply_splats, train_splat_dataset  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(levelname)s  %(message)s")
log = logging.getLogger(__name__)

_SH_C0 = 0.28209479177387814


@dataclass
class VggtSplatDataset:
    points: np.ndarray
    points_rgb: np.ndarray
    camtoworlds: np.ndarray
    Ks: np.ndarray
    image_paths: list[Path]
    train_indices: list[int]


def _env_int(name: str, default: int) -> int:
    value = os.environ.get(name)
    if value is None or value.strip() == "":
        return default
    try:
        return int(value)
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    value = os.environ.get(name)
    if value is None or value.strip() == "":
        return default
    try:
        return float(value)
    except ValueError:
        return default


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Train 3DGS from VGGT protobuf output.")
    parser.add_argument("--vggt-pb", required=True, type=Path, help="Length-delimited VggtInferenceResponse .pb path.")
    parser.add_argument("--recording", type=Path, default=None, help="Source .vis.pb recording. Defaults to the path in the VGGT response.")
    parser.add_argument("--workspace", type=Path, default=None, help="Working directory for preprocessed frames.")
    parser.add_argument("--output-ply", type=Path, default=None, help="Destination trained Gaussian Splatting .ply.")
    parser.add_argument("--preview-json", type=Path, default=None, help="Destination lightweight viewer preview JSON.")
    parser.add_argument("--max-steps", type=int, default=_env_int("WORLDGEN_SPLAT_STEPS", 30000))
    parser.add_argument("--data-factor", type=int, default=_env_int("WORLDGEN_SPLAT_DATA_FACTOR", 1))
    parser.add_argument("--max-gaussians", type=int, default=_env_int("WORLDGEN_SPLAT_MAX_GAUSSIANS", 1000000))
    parser.add_argument("--max-init-points", type=int, default=_env_int("WORLDGEN_SPLAT_MAX_INIT_POINTS", 180000))
    parser.add_argument("--max-points-per-frame", type=int, default=_env_int("WORLDGEN_SPLAT_MAX_POINTS_PER_FRAME", 60000))
    parser.add_argument("--min-confidence", type=float, default=_env_float("WORLDGEN_SPLAT_MIN_CONFIDENCE", 0.55))
    parser.add_argument("--outlier-quantile", type=float, default=_env_float("WORLDGEN_SPLAT_OUTLIER_QUANTILE", 0.995))
    parser.add_argument("--preview-points", type=int, default=_env_int("WORLDGEN_SPLAT_PREVIEW_POINTS", 100000))
    parser.add_argument("--image-quality", type=int, default=_env_int("WORLDGEN_SPLAT_IMAGE_QUALITY", 96))
    return parser.parse_args()


def _default_output_paths(vggt_pb: Path) -> tuple[Path, Path, Path]:
    name = vggt_pb.name
    stem = name[: -len(".vggt.pb")] if name.endswith(".vggt.pb") else vggt_pb.stem
    return (
        vggt_pb.with_name(f"{stem}.splat-workspace"),
        vggt_pb.with_name(f"{stem}.splat.ply"),
        vggt_pb.with_name(f"{stem}.splat.preview.json"),
    )


def read_vggt_response(path: Path) -> vggt_pb2.VggtInferenceResponse:
    with open(path, "rb") as handle:
        header = handle.read(4)
        if len(header) != 4:
            raise ValueError(f"{path} is not a length-delimited protobuf file")
        (length,) = struct.unpack(">I", header)
        payload = handle.read(length)
        if len(payload) != length:
            raise ValueError(f"{path} ended before the declared VGGT payload length")
    message = vggt_pb2.VggtInferenceResponse()
    message.ParseFromString(payload)
    return message


def read_recording_frames(recording: Path, wanted_indices: set[int]) -> dict[int, perceiver_pb2.PerceiverDataFrame]:
    frames: dict[int, perceiver_pb2.PerceiverDataFrame] = {}
    if not wanted_indices:
        return frames

    with open(recording, "rb") as handle:
        frame_index = 0
        while len(frames) < len(wanted_indices):
            header = handle.read(4)
            if len(header) < 4:
                break
            (length,) = struct.unpack(">I", header)
            if length == 0 or length > 50 * 1024 * 1024:
                raise ValueError(f"Suspicious frame length {length} at frame {frame_index}")
            payload = handle.read(length)
            if len(payload) != length:
                break
            if frame_index in wanted_indices:
                frame = perceiver_pb2.PerceiverDataFrame()
                frame.ParseFromString(payload)
                frames[frame_index] = frame
            frame_index += 1

    missing = sorted(wanted_indices - frames.keys())
    if missing:
        raise ValueError(f"Recording is missing {len(missing)} requested frames, first missing index {missing[0]}")
    return frames


def _float_matrix(values, rows: int, cols: int) -> np.ndarray | None:
    flat = np.asarray(values, dtype=np.float64)
    if flat.size != rows * cols or not np.isfinite(flat).all():
        return None
    return flat.reshape(rows, cols)


def _float_blob(blob: bytes, cols: int) -> np.ndarray:
    if not blob:
        return np.empty((0, cols), dtype=np.float32)
    data = np.frombuffer(blob, dtype="<f4")
    count = data.size // cols
    if count <= 0:
        return np.empty((0, cols), dtype=np.float32)
    return data[: count * cols].reshape(count, cols)


def _confidence_blob(blob: bytes, count: int) -> np.ndarray:
    if not blob:
        return np.ones(count, dtype=np.float32)
    conf = np.frombuffer(blob, dtype="<f4")
    if conf.size < count:
        out = np.ones(count, dtype=np.float32)
        out[: conf.size] = conf.astype(np.float32)
        return out
    return conf[:count].astype(np.float32)


def _sample_indices(conf: np.ndarray, limit: int, rng: np.random.Generator) -> np.ndarray:
    count = len(conf)
    if limit <= 0 or count <= limit:
        return np.arange(count, dtype=np.int64)
    weights = np.clip(conf.astype(np.float64), 0.0, None) + 1e-4
    weights_sum = weights.sum()
    if not np.isfinite(weights_sum) or weights_sum <= 0:
        return np.sort(rng.choice(count, size=limit, replace=False))
    return np.sort(rng.choice(count, size=limit, replace=False, p=weights / weights_sum))


def _select_cloud_points(
    cloud: vggt_pb2.VggtPointCloudFrame,
    args: argparse.Namespace,
    rng: np.random.Generator,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    xyz = _float_blob(cloud.xyz_f32_le, 3)
    if len(xyz) == 0:
        return (
            np.empty((0, 3), dtype=np.float32),
            np.empty((0, 3), dtype=np.uint8),
            np.empty((0,), dtype=np.float32),
        )

    rgb = _float_blob(cloud.rgb_f32_le, 3)
    if len(rgb) != len(xyz):
        rgb = np.full_like(xyz, 0.7, dtype=np.float32)
    conf = _confidence_blob(cloud.confidence_f32_le, len(xyz))

    finite = np.isfinite(xyz).all(axis=1) & np.isfinite(rgb).all(axis=1) & np.isfinite(conf)
    keep = finite & (conf >= args.min_confidence)
    if keep.sum() < min(1000, max(1, finite.sum() // 20)):
        keep = finite & (conf >= max(0.05, args.min_confidence * 0.5))

    xyz = xyz[keep]
    rgb = rgb[keep]
    conf = conf[keep]
    if len(xyz) == 0:
        return (
            np.empty((0, 3), dtype=np.float32),
            np.empty((0, 3), dtype=np.uint8),
            np.empty((0,), dtype=np.float32),
        )

    selected = _sample_indices(conf, args.max_points_per_frame, rng)
    xyz = xyz[selected].astype(np.float32, copy=False)
    rgb_u8 = np.clip(rgb[selected], 0.0, 1.0)
    rgb_u8 = np.round(rgb_u8 * 255.0).astype(np.uint8)
    conf = conf[selected].astype(np.float32, copy=False)
    return xyz, rgb_u8, conf


def _filter_scene_outliers(
    points: np.ndarray,
    colors: np.ndarray,
    conf: np.ndarray,
    quantile: float,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    if len(points) < 5000 or quantile >= 1.0:
        return points, colors, conf
    quantile = min(max(quantile, 0.90), 1.0)
    center = np.median(points, axis=0)
    distance = np.linalg.norm(points - center, axis=1)
    limit = np.quantile(distance, quantile)
    keep = distance <= limit
    return points[keep], colors[keep], conf[keep]


def _crop_to_supported_aspect_ratio(image: Image.Image) -> Image.Image:
    width, height = image.size
    aspect_ratio = height / max(width, 1)
    if aspect_ratio < 0.5:
        crop_width = min(width, max(1, int(round(height / 0.5))))
        left = max((width - crop_width) // 2, 0)
        return image.crop((left, 0, left + crop_width, height))
    if aspect_ratio > 2.0:
        crop_height = min(height, max(1, int(round(width * 2.0))))
        top = max((height - crop_height) // 2, 0)
        return image.crop((0, top, width, top + crop_height))
    return image


def _preprocess_frame(frame: perceiver_pb2.PerceiverDataFrame, width: int, height: int) -> Image.Image:
    rgb = decode_frame_rgb(frame)
    image = Image.fromarray(rgb, mode="RGB")
    image = _crop_to_supported_aspect_ratio(image)
    return image.resize((width, height), Image.Resampling.BICUBIC)


def _image_size_for(cloud: vggt_pb2.VggtPointCloudFrame | None, K: np.ndarray) -> tuple[int, int]:
    if cloud is not None and cloud.image_width > 0 and cloud.image_height > 0:
        return int(cloud.image_width), int(cloud.image_height)
    width = int(round(float(K[0, 2]) * 2.0))
    height = int(round(float(K[1, 2]) * 2.0))
    return max(16, width), max(16, height)


def build_dataset(
    response: vggt_pb2.VggtInferenceResponse,
    recording: Path,
    workspace: Path,
    args: argparse.Namespace,
) -> tuple[VggtSplatDataset, dict]:
    rng = np.random.default_rng(7)
    cloud_by_sample = {int(cloud.sampled_frame_index): cloud for cloud in response.point_clouds}
    cameras = sorted(response.cameras, key=lambda item: int(item.sampled_frame_index))
    if not cameras:
        raise ValueError("VGGT response has no camera frames")

    wanted_indices = {int(camera.source_frame_index) for camera in cameras}
    frames = read_recording_frames(recording, wanted_indices)

    images_dir = workspace / "images"
    images_dir.mkdir(parents=True, exist_ok=True)

    image_paths: list[Path] = []
    camtoworlds: list[np.ndarray] = []
    intrinsics: list[np.ndarray] = []
    used_cameras = 0

    for view_index, camera in enumerate(cameras):
        c2w = _float_matrix(camera.camera_to_world, 4, 4)
        K = _float_matrix(camera.intrinsics, 3, 3)
        if c2w is None or K is None:
            log.warning("Skipping camera %d with invalid pose or intrinsics", view_index)
            continue
        frame = frames.get(int(camera.source_frame_index))
        if frame is None:
            continue
        cloud = cloud_by_sample.get(int(camera.sampled_frame_index))
        width, height = _image_size_for(cloud, K)
        image = _preprocess_frame(frame, width, height)
        image_path = images_dir / f"frame_{int(camera.source_frame_index):06d}.jpg"
        image.save(image_path, quality=args.image_quality)
        image_paths.append(image_path)
        camtoworlds.append(c2w)
        intrinsics.append(K)
        used_cameras += 1

    if not image_paths:
        raise ValueError("No usable camera/image pairs were available for splat training")

    point_batches = []
    color_batches = []
    conf_batches = []
    for cloud in response.point_clouds:
        xyz, rgb, conf = _select_cloud_points(cloud, args, rng)
        if len(xyz) == 0:
            continue
        point_batches.append(xyz)
        color_batches.append(rgb)
        conf_batches.append(conf)

    if not point_batches:
        raise ValueError("No VGGT points survived confidence filtering")

    points = np.concatenate(point_batches, axis=0)
    colors = np.concatenate(color_batches, axis=0)
    conf = np.concatenate(conf_batches, axis=0)
    before_outlier = len(points)
    points, colors, conf = _filter_scene_outliers(points, colors, conf, args.outlier_quantile)

    if len(points) > args.max_init_points > 0:
        selected = _sample_indices(conf, args.max_init_points, rng)
        points = points[selected]
        colors = colors[selected]
        conf = conf[selected]

    if len(points) < 1000:
        raise ValueError(f"Only {len(points)} VGGT points survived filtering; refusing to train an unstable splat")

    dataset = VggtSplatDataset(
        points=points.astype(np.float32, copy=False),
        points_rgb=colors.astype(np.uint8, copy=False),
        camtoworlds=np.stack(camtoworlds).astype(np.float64),
        Ks=np.stack(intrinsics).astype(np.float64),
        image_paths=image_paths,
        train_indices=list(range(len(image_paths))),
    )
    stats = {
        "init_point_count": int(len(points)),
        "points_before_outlier_filter": int(before_outlier),
        "training_frame_count": int(used_cameras),
    }
    return dataset, stats


def _sigmoid(value: np.ndarray) -> np.ndarray:
    return 1.0 / (1.0 + np.exp(-value))


def write_preview_json(
    ply_path: Path,
    preview_json: Path,
    max_points: int,
    metadata: dict,
) -> dict:
    splats = _load_ply_splats(ply_path, "cpu")
    if splats is None:
        raise ValueError(f"Could not load trained splat PLY from {ply_path}")

    means = splats["means"].detach().cpu().numpy()
    scales = np.exp(splats["scales"].detach().cpu().numpy()).mean(axis=1)
    opacities = _sigmoid(splats["opacities"].detach().cpu().numpy())
    sh0 = splats["sh0"].detach().cpu().numpy()[:, 0, :]
    colors = np.clip(sh0 * _SH_C0 + 0.5, 0.0, 1.0)

    rng = np.random.default_rng(11)
    weights = np.clip(opacities, 0.0, None) + 1e-4
    keep = _sample_indices(weights.astype(np.float32), max_points, rng)

    preview_points = [
        {
            "x": float(means[i, 0]),
            "y": float(means[i, 1]),
            "z": float(means[i, 2]),
            "r": float(colors[i, 0]),
            "g": float(colors[i, 1]),
            "b": float(colors[i, 2]),
            "opacity": float(opacities[i]),
            "scale": float(scales[i]),
        }
        for i in keep
    ]

    bounds_min = means.min(axis=0).tolist() if len(means) else [0, 0, 0]
    bounds_max = means.max(axis=0).tolist() if len(means) else [0, 0, 0]
    data = {
        **metadata,
        "status": "complete",
        "ply_path": str(ply_path),
        "preview_json_path": str(preview_json),
        "gaussian_count": int(len(means)),
        "preview_point_count": int(len(preview_points)),
        "bounds_min": [float(v) for v in bounds_min],
        "bounds_max": [float(v) for v in bounds_max],
        "points": preview_points,
    }
    preview_json.parent.mkdir(parents=True, exist_ok=True)
    preview_json.write_text(json.dumps(data), encoding="utf-8")
    return data


def main() -> None:
    args = parse_args()
    vggt_pb = args.vggt_pb.resolve()
    default_workspace, default_ply, default_preview = _default_output_paths(vggt_pb)
    workspace = (args.workspace or default_workspace).resolve()
    output_ply = (args.output_ply or default_ply).resolve()
    preview_json = (args.preview_json or default_preview).resolve()

    response = read_vggt_response(vggt_pb)
    recording = (args.recording or Path(response.source_recording_path)).resolve()
    if not recording.exists():
        raise FileNotFoundError(f"Source recording not found: {recording}")

    cfg = {
        "gaussian_splatting": {
            "data_factor": max(1, int(args.data_factor)),
            "max_steps": max(1, int(args.max_steps)),
            "mcmc_max_gaussians": max(1000, int(args.max_gaussians)),
            "sh_degree": 3,
            "save_every_n_steps": 0,
        }
    }

    started = time.time()
    log.info("Preparing VGGT splat dataset from %s", vggt_pb)
    dataset, stats = build_dataset(response, recording, workspace, args)
    log.info(
        "Training splat with %d init points and %d frames",
        len(dataset.points),
        len(dataset.train_indices),
    )

    train_splat_dataset(dataset, output_ply, cfg)
    elapsed = time.time() - started

    preview = write_preview_json(
        output_ply,
        preview_json,
        args.preview_points,
        {
            "trainer": "server/worldgen/scripts/train_vggt_splat.py",
            "elapsed_sec": elapsed,
            "max_steps": int(args.max_steps),
            "max_gaussians": int(args.max_gaussians),
            **stats,
        },
    )
    print(json.dumps({key: value for key, value in preview.items() if key != "points"}))


if __name__ == "__main__":
    main()
