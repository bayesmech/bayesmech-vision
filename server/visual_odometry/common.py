from __future__ import annotations

import math
import struct
import sys
import zlib
from pathlib import Path
from typing import Iterator

import cv2
import matplotlib
import numpy as np

matplotlib.use("Agg")
import matplotlib.pyplot as plt

_server_root = Path(__file__).resolve().parent.parent
_project_root = _server_root.parent
for _p in (str(_project_root), str(_project_root / "proto"), str(_server_root)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from proto import perceiver_pb2, segmentation_pb2


def seg_path(recording_path: Path) -> Path:
    if recording_path.name.endswith(".vis.pb"):
        return recording_path.parent / (recording_path.name.removesuffix(".vis.pb") + ".seg.pb")
    return recording_path.with_suffix(".seg.pb")


def iter_messages(path: Path, msg_type) -> Iterator:
    with path.open("rb") as f:
        while True:
            header = f.read(4)
            if len(header) < 4:
                return
            (length,) = struct.unpack(">I", header)
            if length <= 0:
                return
            data = f.read(length)
            if len(data) < length:
                return
            msg = msg_type()
            msg.ParseFromString(data)
            yield msg


def decode_rgb(frame: perceiver_pb2.PerceiverDataFrame) -> np.ndarray:
    buf = np.frombuffer(frame.rgb_frame.data, dtype=np.uint8)
    bgr = cv2.imdecode(buf, cv2.IMREAD_COLOR)
    if bgr is None:
        raise ValueError("Failed to decode JPEG frame")
    return bgr


def decode_mask(mask_data: bytes) -> np.ndarray:
    if len(mask_data) < 8:
        raise ValueError("Compressed mask payload too short")
    h, w = struct.unpack("<II", mask_data[:8])
    packed = zlib.decompress(mask_data[8:])
    bits = np.unpackbits(np.frombuffer(packed, dtype=np.uint8))[: h * w]
    return (bits.reshape(h, w) * 255).astype(np.uint8)


def load_segmentation_index(seg_file: Path) -> tuple[dict[tuple[int, int], segmentation_pb2.SegmentationResponse], dict[str, int]]:
    frames: dict[tuple[int, int], segmentation_pb2.SegmentationResponse] = {}
    label_counts: dict[str, int] = {}
    for msg in iter_messages(seg_file, segmentation_pb2.SegmentationResponse):
        key = (msg.frame_identifier.frame_number, msg.frame_identifier.timestamp_ns)
        frames[key] = msg
        for mask in msg.masks:
            label_counts[mask.label] = label_counts.get(mask.label, 0) + 1
    return frames, label_counts


def bike_mask_for_frame(
    seg_frame: segmentation_pb2.SegmentationResponse | None,
    image_shape: tuple[int, int],
    label: str,
    dilate_radius: int,
    bottom_border: int,
) -> tuple[np.ndarray, int]:
    h, w = image_shape
    mask = np.zeros((h, w), dtype=np.uint8)
    if seg_frame is not None:
        for seg_mask in seg_frame.masks:
            if seg_mask.label != label:
                continue
            decoded = decode_mask(seg_mask.mask_data)
            if decoded.shape != mask.shape:
                decoded = cv2.resize(decoded, (w, h), interpolation=cv2.INTER_NEAREST)
            mask = cv2.bitwise_or(mask, decoded)
    if dilate_radius > 0 and np.any(mask):
        k = 2 * dilate_radius + 1
        kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k, k))
        mask = cv2.dilate(mask, kernel, iterations=1)
    if bottom_border > 0:
        mask[h - min(bottom_border, h) :, :] = 255
    return mask, int(np.count_nonzero(mask))


def camera_from_first_frame(
    frame: perceiver_pb2.PerceiverDataFrame,
    width: int,
    height: int,
    bottom_border: int,
) -> dict[str, float]:
    intr = frame.camera_intrinsics
    scale_x = float(width) / float(intr.image_width or width)
    scale_y = float(height) / float(intr.image_height or height)
    return {
        "fx": float(intr.fx) * scale_x,
        "fy": float(intr.fy) * scale_y,
        "cx": float(intr.cx) * scale_x,
        "cy": float(intr.cy) * scale_y,
        "scale_x": scale_x,
        "scale_y": scale_y,
        "recorded_intrinsics_width": float(intr.image_width),
        "recorded_intrinsics_height": float(intr.image_height),
        "decoded_width": float(width),
        "decoded_height": float(height),
        "bottom_border": float(bottom_border),
    }


def gps_to_local_xy(latitudes: np.ndarray, longitudes: np.ndarray) -> np.ndarray:
    r = 6378137.0
    lat0 = math.radians(float(latitudes[0]))
    lon0 = math.radians(float(longitudes[0]))
    lats = np.radians(latitudes)
    lons = np.radians(longitudes)
    x = (lons - lon0) * math.cos(lat0) * r
    y = (lats - lat0) * r
    return np.column_stack([x, y])


def project_track_to_2d(points_xyz: np.ndarray) -> np.ndarray:
    centered = points_xyz - points_xyz.mean(axis=0, keepdims=True)
    _, _, vh = np.linalg.svd(centered, full_matrices=False)
    return centered @ vh[:2].T


def fit_similarity(src: np.ndarray, dst: np.ndarray) -> tuple[float, np.ndarray, np.ndarray]:
    src_mean = src.mean(axis=0)
    dst_mean = dst.mean(axis=0)
    src_center = src - src_mean
    dst_center = dst - dst_mean
    cov = src_center.T @ dst_center / max(len(src), 1)
    u, _, vt = np.linalg.svd(cov)
    rot = vt.T @ u.T
    if np.linalg.det(rot) < 0:
        vt[-1, :] *= -1
        rot = vt.T @ u.T
    src_var = np.mean(np.sum(src_center * src_center, axis=1))
    scale = 1.0 if src_var <= 1e-12 else float(np.trace(np.diag(np.linalg.svd(cov, compute_uv=False))) / src_var)
    trans = dst_mean - scale * (rot @ src_mean)
    return scale, rot, trans


def write_track_plot(
    out_path: Path,
    slam_rows: list[dict[str, float]],
    gps_rows: list[dict[str, float]],
) -> None:
    if not slam_rows or not gps_rows:
        return
    slam_by_ts = {
        int(row["timestamp_ns"]): np.array([row["x"], row["y"], row["z"]], dtype=np.float64)
        for row in slam_rows
    }
    gps_by_ts = {
        int(row["timestamp_ns"]): np.array([row["latitude"], row["longitude"]], dtype=np.float64)
        for row in gps_rows
    }
    common_ts = sorted(set(slam_by_ts) & set(gps_by_ts))
    if len(common_ts) < 20:
        return
    slam_xyz = np.vstack([slam_by_ts[ts] for ts in common_ts])
    gps_latlon = np.vstack([gps_by_ts[ts] for ts in common_ts])
    slam_2d = project_track_to_2d(slam_xyz)
    gps_2d = gps_to_local_xy(gps_latlon[:, 0], gps_latlon[:, 1])
    scale, rot, trans = fit_similarity(slam_2d, gps_2d)
    aligned = (scale * (rot @ slam_2d.T)).T + trans

    fig, axes = plt.subplots(1, 3, figsize=(15, 5))
    axes[0].plot(slam_2d[:, 0], slam_2d[:, 1], color="#c28f00", linewidth=2)
    axes[0].set_title("VO PCA Projection")
    axes[1].plot(gps_2d[:, 0], gps_2d[:, 1], color="#2f7f3f", linewidth=2)
    axes[1].set_title("GPS Local ENU")
    axes[2].plot(gps_2d[:, 0], gps_2d[:, 1], color="#2f7f3f", linewidth=2, label="GPS")
    axes[2].plot(aligned[:, 0], aligned[:, 1], color="#c28f00", linewidth=2, label="VO aligned")
    axes[2].legend()
    axes[2].set_title("Overlay")
    for ax in axes:
        ax.set_aspect("equal", adjustable="box")
        ax.grid(True, alpha=0.3)
    fig.tight_layout()
    fig.savefig(out_path, dpi=160)
    plt.close(fig)
