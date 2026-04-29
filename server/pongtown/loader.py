"""Iterate (.vis.pb + .seg.pb) in lockstep and yield decoded per-frame bundles."""
from __future__ import annotations

import struct
import sys
import zlib
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

import cv2
import numpy as np

_server_root = Path(__file__).resolve().parent.parent
_project_root = _server_root.parent
sys.path.insert(0, str(_project_root))
sys.path.insert(0, str(_project_root / "proto"))
sys.path.insert(0, str(_server_root))

from proto import perceiver_pb2, segmentation_pb2, spatial_pb2  # noqa: E402
from streamlog.protoio import ProtoIO  # noqa: E402


_frame_io = ProtoIO(perceiver_pb2.PerceiverDataFrame)
_seg_io = ProtoIO(segmentation_pb2.SegmentationResponse)


def decode_mask(data: bytes) -> np.ndarray:
    h, w = struct.unpack("<II", data[:8])
    packed = np.frombuffer(zlib.decompress(data[8:]), dtype=np.uint8)
    return np.unpackbits(packed)[: h * w].reshape(h, w).astype(bool)


def _classify(label: str, label_cfg: dict[str, list[str]]) -> str | None:
    s = (label or "").strip().lower()
    if not s:
        return None
    for cls, subs in label_cfg.items():
        if any(sub.lower() in s for sub in subs):
            return cls
    return None


def _decode_rgb_bgr(frame: perceiver_pb2.PerceiverDataFrame) -> np.ndarray | None:
    """Decode rgb_frame to BGR (H, W, 3) uint8."""
    img = frame.rgb_frame
    if not img or not img.data:
        return None
    ImageFormat = perceiver_pb2.ImageFrame.ImageFormat
    if img.format == ImageFormat.JPEG:
        buf = np.frombuffer(img.data, dtype=np.uint8)
        return cv2.imdecode(buf, cv2.IMREAD_COLOR)
    if img.format == ImageFormat.BITMAP_RGB:
        raw = np.frombuffer(img.data, dtype=np.uint8)
        if img.width and img.height and raw.size == img.width * img.height * 3:
            rgb = raw.reshape(int(img.height), int(img.width), 3)
        else:
            total = len(raw) // 3
            side = int(total ** 0.5)
            rgb = raw.reshape((side, side, 3))
        return cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)
    return None


def _intrinsics_matrix(ci: perceiver_pb2.CameraIntrinsics) -> np.ndarray:
    return np.array(
        [[ci.fx, 0.0, ci.cx], [0.0, ci.fy, ci.cy], [0.0, 0.0, 1.0]],
        dtype=np.float64,
    )


def _pose_to_matrix(pose) -> np.ndarray:
    """Pose (position + quaternion xyzw) → 4x4 row-major float64."""
    t = np.array([pose.position.x, pose.position.y, pose.position.z], dtype=np.float64)
    qx, qy, qz, qw = pose.rotation.x, pose.rotation.y, pose.rotation.z, pose.rotation.w
    xx, yy, zz = qx * qx, qy * qy, qz * qz
    xy, xz, yz = qx * qy, qx * qz, qy * qz
    wx, wy, wz = qw * qx, qw * qy, qw * qz
    R = np.array(
        [
            [1 - 2 * (yy + zz),     2 * (xy - wz),     2 * (xz + wy)],
            [    2 * (xy + wz), 1 - 2 * (xx + zz),     2 * (yz - wx)],
            [    2 * (xz - wy),     2 * (yz + wx), 1 - 2 * (xx + yy)],
        ],
        dtype=np.float64,
    )
    M = np.eye(4, dtype=np.float64)
    M[:3, :3] = R
    M[:3, 3] = t
    return M


@dataclass
class FrameBundle:
    frame_idx: int
    timestamp_ns: int
    frame_number: int
    rgb: np.ndarray
    intrinsics: np.ndarray
    T_camera_to_world: np.ndarray
    table_mask: np.ndarray | None       # alias for table_top_mask
    table_top_mask: np.ndarray | None
    table_legs_mask: np.ndarray         # pixels we want OUTSIDE the quad
    net_mask: np.ndarray | None
    person_mask: np.ndarray
    bat_mask: np.ndarray
    ball_mask: np.ndarray
    geometry: spatial_pb2.InferredGeometry | None = None
    raw_frame: perceiver_pb2.PerceiverDataFrame | None = None


def iter_bundles(
    vis_path: Path,
    seg_path: Path,
    label_cfg: dict[str, list[str]],
    max_frames: int = 0,
    sample_every: int = 1,
) -> Iterator[FrameBundle]:
    """Yield one FrameBundle per (vis, seg) frame pair (matched on frame_number)."""
    seg_responses = _seg_io.read_file(seg_path)
    seg_by_fn: dict[int, segmentation_pb2.SegmentationResponse] = {
        resp.frame_identifier.frame_number: resp for resp in seg_responses
    }

    frames = _frame_io.read_file(vis_path)

    cached_K: np.ndarray | None = None
    yielded = 0

    for idx, frame in enumerate(frames):
        if frame.HasField("camera_intrinsics") and (
            frame.camera_intrinsics.fx != 0 or frame.camera_intrinsics.fy != 0
        ):
            cached_K = _intrinsics_matrix(frame.camera_intrinsics)
        if cached_K is None:
            continue

        if sample_every > 1 and (idx % sample_every) != 0:
            continue
        if max_frames and yielded >= max_frames:
            break

        rgb = _decode_rgb_bgr(frame)
        if rgb is None:
            continue
        H, W = rgb.shape[:2]

        T = _pose_to_matrix(frame.camera_pose)

        seg = seg_by_fn.get(frame.frame_identifier.frame_number)
        table_top = np.zeros((H, W), dtype=bool)
        table_legs = np.zeros((H, W), dtype=bool)
        net = np.zeros((H, W), dtype=bool)
        person = np.zeros((H, W), dtype=bool)
        bat = np.zeros((H, W), dtype=bool)
        ball = np.zeros((H, W), dtype=bool)
        any_table = any_net = False
        if seg is not None:
            for m in seg.masks:
                cls = _classify(m.label, label_cfg)
                if cls is None:
                    continue
                arr = decode_mask(m.mask_data)
                if arr.shape != (H, W):
                    arr = cv2.resize(
                        arr.astype(np.uint8), (W, H), interpolation=cv2.INTER_NEAREST
                    ).astype(bool)
                if cls == "table_top":
                    table_top |= arr
                    any_table = True
                elif cls == "table_legs":
                    table_legs |= arr
                elif cls == "net":
                    net |= arr
                    any_net = True
                elif cls == "person":
                    person |= arr
                elif cls == "bat":
                    bat |= arr
                elif cls == "ball":
                    ball |= arr

        yield FrameBundle(
            frame_idx=idx,
            timestamp_ns=int(frame.frame_identifier.timestamp_ns),
            frame_number=frame.frame_identifier.frame_number,
            rgb=rgb,
            intrinsics=cached_K,
            T_camera_to_world=T,
            table_mask=table_top if any_table else None,
            table_top_mask=table_top if any_table else None,
            table_legs_mask=table_legs,
            net_mask=net if any_net else None,
            person_mask=person,
            bat_mask=bat,
            ball_mask=ball,
            geometry=frame.inferred_geometry if frame.HasField("inferred_geometry") else None,
            raw_frame=frame,
        )
        yielded += 1
