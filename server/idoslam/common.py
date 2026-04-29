from __future__ import annotations

import math
import struct
import sys
import zlib
from pathlib import Path
from typing import Iterator

import cv2
import numpy as np

_server_root = Path(__file__).resolve().parent.parent
_project_root = _server_root.parent
for _p in (str(_project_root), str(_project_root / "proto"), str(_server_root)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from proto import perceiver_pb2, segmentation_pb2


def recording_stem(recording_path: Path) -> str:
    if recording_path.name.endswith(".vis.pb"):
        return recording_path.name.removesuffix(".vis.pb")
    return recording_path.stem


def workspace_path(recording_path: Path) -> Path:
    return recording_path.parent / f"{recording_stem(recording_path)}.idoslam"


def pairwise_output_dir(recording_path: Path) -> Path:
    return workspace_path(recording_path) / "pairwise"


def pairwise_trajectory_csv_path(recording_path: Path) -> Path:
    return pairwise_output_dir(recording_path) / "trajectory_pairwise_sift.csv"


def pairwise_motion_csv_path(recording_path: Path) -> Path:
    return pairwise_output_dir(recording_path) / "pairwise_sift_motion.csv"


def pose_refine_output_dir(recording_path: Path) -> Path:
    return workspace_path(recording_path) / "pose_refined"


def refined_trajectory_csv_path(recording_path: Path) -> Path:
    return pose_refine_output_dir(recording_path) / "trajectory_pairwise_sift_refined.csv"


def preferred_trajectory_csv_path(recording_path: Path) -> Path:
    refined = refined_trajectory_csv_path(recording_path)
    if refined.exists():
        return refined
    return pairwise_trajectory_csv_path(recording_path)


def plane_output_dir(recording_path: Path) -> Path:
    return workspace_path(recording_path) / "track_width_plane"


def plane_width_csv_path(recording_path: Path) -> Path:
    return plane_output_dir(recording_path) / "track_width_estimates.csv"


def triangulated_output_dir(recording_path: Path) -> Path:
    return workspace_path(recording_path) / "triangulated"


def triangulated_width_csv_path(recording_path: Path) -> Path:
    return triangulated_output_dir(recording_path) / "track_width_estimates.csv"


def triangulated_ground_points_csv_path(recording_path: Path) -> Path:
    return triangulated_output_dir(recording_path) / "ground_points.csv"


def triangulated_correspondences_csv_path(recording_path: Path) -> Path:
    return triangulated_output_dir(recording_path) / "point_correspondences.csv"


def triangulated_pair_logs_path(recording_path: Path) -> Path:
    return triangulated_output_dir(recording_path) / "pair_logs.json"


def canonical_output_dir(recording_path: Path) -> Path:
    return workspace_path(recording_path) / "canonical"


def canonical_centerline_csv_path(recording_path: Path) -> Path:
    return canonical_output_dir(recording_path) / "canonical_centerline.csv"


def canonical_lap_trajectories_csv_path(recording_path: Path) -> Path:
    return canonical_output_dir(recording_path) / "lap_trajectories.csv"


def visual_pairs_output_dir(recording_path: Path) -> Path:
    return workspace_path(recording_path) / "visual_pairs"


def pair_pose_output_dir(recording_path: Path, frame_a: int, frame_b: int) -> Path:
    return workspace_path(recording_path) / f"pair_pose_{frame_a}_{frame_b}"


def idoslam_proto_path(recording_path: Path) -> Path:
    return recording_path.parent / f"{recording_stem(recording_path)}.idoslam.pb"


def seg_path(recording_path: Path) -> Path:
    if recording_path.name.endswith(".vis.pb"):
        stem = recording_path.name.removesuffix(".vis.pb")
        primary = recording_path.parent / f"{stem}.segmentation.pb"
        legacy = recording_path.parent / f"{stem}.seg.pb"
    else:
        primary = recording_path.with_suffix(".segmentation.pb")
        legacy = recording_path.with_suffix(".seg.pb")
    return primary if primary.exists() or not legacy.exists() else legacy


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


def rotation_matrix_2d(theta_rad: float) -> np.ndarray:
    c = math.cos(theta_rad)
    s = math.sin(theta_rad)
    return np.array([[c, -s], [s, c]], dtype=np.float64)


def estimate_pairwise_global_alignment(
    visual_xy: np.ndarray,
    gps_xy: np.ndarray,
) -> dict[str, object]:
    if len(visual_xy) != len(gps_xy):
        raise ValueError("visual_xy and gps_xy must have the same length")
    if len(visual_xy) < 2:
        rot = np.eye(2, dtype=np.float64)
        trans = gps_xy.mean(axis=0) - visual_xy.mean(axis=0) if len(visual_xy) else np.zeros(2, dtype=np.float64)
        return {
            "scale": 1.0,
            "scale_divisor": 1.0,
            "visual_to_gps_scale": 1.0,
            "theta_rad": 0.0,
            "theta_deg": 0.0,
            "rotation": rot,
            "translation": trans,
            "pair_count": 0,
        }

    gps_frame_delta = gps_xy[1:] - gps_xy[:-1]
    gps_frame_delta_norm = np.linalg.norm(gps_frame_delta, axis=1)
    change_idx = np.concatenate(([0], np.flatnonzero(gps_frame_delta_norm > 1e-6) + 1))
    if len(change_idx) < 2:
        change_idx = np.arange(len(gps_xy), dtype=np.int64)

    visual_delta = visual_xy[change_idx[1:]] - visual_xy[change_idx[:-1]]
    gps_delta = gps_xy[change_idx[1:]] - gps_xy[change_idx[:-1]]
    visual_norm = np.linalg.norm(visual_delta, axis=1)
    gps_norm = np.linalg.norm(gps_delta, axis=1)
    valid = (visual_norm > 1e-9) & (gps_norm > 1e-9)
    if not np.any(valid):
        rot = np.eye(2, dtype=np.float64)
        trans = gps_xy.mean(axis=0) - visual_xy.mean(axis=0)
        return {
            "scale": 1.0,
            "scale_divisor": 1.0,
            "visual_to_gps_scale": 1.0,
            "theta_rad": 0.0,
            "theta_deg": 0.0,
            "rotation": rot,
            "translation": trans,
            "pair_count": 0,
        }

    v = visual_delta[valid]
    g = gps_delta[valid]
    angles = np.arctan2(
        v[:, 0] * g[:, 1] - v[:, 1] * g[:, 0],
        np.sum(v * g, axis=1),
    )
    theta = float(math.atan2(float(np.mean(np.sin(angles))), float(np.mean(np.cos(angles)))))
    scale_divisor = float(np.mean(visual_norm[valid] / gps_norm[valid]))
    visual_to_gps_scale = 1.0 / scale_divisor if scale_divisor > 1e-12 else 1.0
    rot = rotation_matrix_2d(theta)
    transformed = ((rot @ visual_xy.T).T) / scale_divisor
    trans = np.mean(gps_xy - transformed, axis=0)
    return {
        "scale": scale_divisor,
        "scale_divisor": scale_divisor,
        "visual_to_gps_scale": visual_to_gps_scale,
        "theta_rad": theta,
        "theta_deg": math.degrees(theta),
        "rotation": rot,
        "translation": trans,
        "pair_count": int(np.count_nonzero(valid)),
        "gps_change_index_count": int(len(change_idx)),
    }
