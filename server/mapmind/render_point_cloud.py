from __future__ import annotations

import argparse
import socket
import struct
import sys
from pathlib import Path

_here = Path(__file__).resolve()
_server_root = _here.parent.parent
_project_root = _server_root.parent
sys.path.insert(0, str(_project_root))
sys.path.insert(0, str(_project_root / "proto"))
sys.path.insert(0, str(_server_root))

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

from proto import mapmind_pb2


def _read_delimited_messages(path: Path):
    with path.open("rb") as fh:
        while True:
            prefix = fh.read(4)
            if not prefix:
                break
            if len(prefix) != 4:
                raise RuntimeError("truncated protobuf length prefix")
            size = socket.ntohl(struct.unpack("I", prefix)[0])
            payload = fh.read(size)
            if len(payload) != size:
                raise RuntimeError("truncated protobuf payload")
            msg = mapmind_pb2.MapMindRecord()
            msg.ParseFromString(payload)
            yield msg


def _extract(path: Path):
    poses = []
    point_cloud = None
    for record in _read_delimited_messages(path):
        kind = record.WhichOneof("record")
        if kind == "frame":
            pose = record.frame.slam_pose
            poses.append(
                (
                    float(pose.position.x),
                    float(pose.position.y),
                    float(pose.position.z),
                    bool(record.frame.is_keyframe),
                )
            )
        elif kind == "result":
            point_cloud = record.result.point_cloud
    if point_cloud is None:
        raise RuntimeError("no MapMindResult record found")
    return np.asarray(poses, dtype=np.float32), point_cloud


def _point_arrays(point_cloud):
    xs = np.asarray(point_cloud.xs, dtype=np.float32)
    ys = np.asarray(point_cloud.ys, dtype=np.float32)
    zs = np.asarray(point_cloud.zs, dtype=np.float32)
    if xs.size == 0:
        return np.zeros((0, 3), dtype=np.float32), np.zeros((0, 3), dtype=np.float32)
    colors = np.stack(
        [
            np.asarray(point_cloud.rs, dtype=np.float32),
            np.asarray(point_cloud.gs, dtype=np.float32),
            np.asarray(point_cloud.bs, dtype=np.float32),
        ],
        axis=1,
    )
    colors = np.clip(colors / 255.0, 0.0, 1.0)
    points = np.stack([xs, ys, zs], axis=1)
    return points, colors


def render(mapmind_path: Path, output_path: Path, max_points: int):
    poses, point_cloud = _extract(mapmind_path)
    points, colors = _point_arrays(point_cloud)

    if len(points) > max_points:
        idx = np.linspace(0, len(points) - 1, max_points, dtype=np.int64)
        points = points[idx]
        colors = colors[idx]

    fig = plt.figure(figsize=(10, 8))
    ax = fig.add_subplot(111, projection="3d")

    if points.size > 0:
        ax.scatter(points[:, 0], points[:, 1], points[:, 2], c=colors, s=0.4, alpha=0.9, linewidths=0)

    if len(poses) > 0:
        ax.plot(poses[:, 0], poses[:, 1], poses[:, 2], color="black", linewidth=1.0, alpha=0.7)
        keyframes = poses[poses[:, 3] > 0.5]
        if len(keyframes) > 0:
            ax.scatter(
                keyframes[:, 0],
                keyframes[:, 1],
                keyframes[:, 2],
                c="red",
                s=8,
                alpha=0.9,
            )

    # Use point cloud bounds if available, otherwise use pose bounds.
    if points.size > 0:
        ref = points
    elif len(poses) > 0:
        ref = poses[:, :3]
    else:
        raise RuntimeError("No point cloud or poses to render")

    mins = ref.min(axis=0)
    maxs = ref.max(axis=0)
    center = (mins + maxs) / 2.0
    radius = float(np.max(maxs - mins)) / 2.0
    if radius <= 0.0:
        radius = 1.0
    ax.set_xlim(center[0] - radius, center[0] + radius)
    ax.set_ylim(center[1] - radius, center[1] + radius)
    ax.set_zlim(center[2] - radius, center[2] + radius)

    ax.set_xlabel("X")
    ax.set_ylabel("Y")
    ax.set_zlabel("Z")
    ax.set_title(mapmind_path.name)
    ax.view_init(elev=25, azim=-60)
    fig.tight_layout()
    fig.savefig(output_path, dpi=180)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("mapmind_pb", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--max-points", type=int, default=120000)
    args = parser.parse_args()

    output = args.output or args.mapmind_pb.with_suffix(".point_cloud.png")
    render(args.mapmind_pb, output, args.max_points)
    print(f"[render] wrote {output}")


if __name__ == "__main__":
    main()
