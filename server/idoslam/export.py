from __future__ import annotations

import csv
import json
import math
import sys
from collections import defaultdict
from pathlib import Path

_server_root = Path(__file__).resolve().parent.parent
_project_root = _server_root.parent
for _p in (str(_project_root), str(_project_root / "proto"), str(_server_root)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from proto import idoslam_pb2, perceiver_pb2
from streamlog.protoio import ProtoIO
from idoslam.common import (
    idoslam_proto_path,
    iter_messages,
    pairwise_trajectory_csv_path,
    refined_trajectory_csv_path,
    triangulated_correspondences_csv_path,
    triangulated_ground_points_csv_path,
    triangulated_pair_logs_path,
)

_idoslam_io = ProtoIO(idoslam_pb2.IdoSlamResponse)


def quaternion_xyzw_to_pitch_roll_yaw_deg(
    qx: float,
    qy: float,
    qz: float,
    qw: float,
) -> tuple[float, float, float]:
    sinr_cosp = 2.0 * (qw * qx + qy * qz)
    cosr_cosp = 1.0 - 2.0 * (qx * qx + qy * qy)
    roll = math.atan2(sinr_cosp, cosr_cosp)

    sinp = 2.0 * (qw * qy - qz * qx)
    sinp = max(-1.0, min(1.0, sinp))
    pitch = math.asin(sinp)

    siny_cosp = 2.0 * (qw * qz + qx * qy)
    cosy_cosp = 1.0 - 2.0 * (qy * qy + qz * qz)
    yaw = math.atan2(siny_cosp, cosy_cosp)

    return (
        float(math.degrees(pitch)),
        float(math.degrees(roll)),
        float(math.degrees(yaw)),
    )


def write_idoslam_pb(
    recording: Path,
    segmentation: Path,
    output_path: Path | None = None,
    trajectory_csv: Path | None = None,
    ground_points_csv: Path | None = None,
    correspondences_csv: Path | None = None,
    pair_logs_path: Path | None = None,
    workspace: Path | None = None,
) -> Path:
    recording = recording.resolve()
    segmentation = segmentation.resolve()
    output_path = (output_path.resolve() if output_path else idoslam_proto_path(recording))

    if trajectory_csv is None:
        preferred_csv = refined_trajectory_csv_path(recording)
        trajectory_csv = preferred_csv if preferred_csv.exists() else pairwise_trajectory_csv_path(recording)
    else:
        trajectory_csv = trajectory_csv.resolve()
    if not trajectory_csv.exists():
        raise FileNotFoundError(f"Missing trajectory CSV: {trajectory_csv}")

    first_frame = next(iter_messages(recording, perceiver_pb2.PerceiverDataFrame), None)
    if first_frame is None:
        raise RuntimeError("Recording is empty")

    resp = idoslam_pb2.IdoSlamResponse()
    resp.first_frame_id.CopyFrom(first_frame.frame_identifier)
    resp.recording_path = str(recording)
    resp.segmentation_path = str(segmentation)
    if workspace is not None:
        resp.workspace_path = str(workspace.resolve())

    device_id = first_frame.frame_identifier.device_id

    with trajectory_csv.open() as f:
        for row in csv.DictReader(f):
            pose = resp.frame_poses.add()
            pose.frame_id.frame_number = int(row["frame_number"])
            pose.frame_id.timestamp_ns = int(row["timestamp_ns"])
            if device_id:
                pose.frame_id.device_id = device_id
            pose.frame_index = int(row["frame_index"])
            pose.world_pose.position.x = float(row["x"])
            pose.world_pose.position.y = float(row["y"])
            pose.world_pose.position.z = float(row["z"])
            pose.world_pose.rotation.x = float(row["qx"])
            pose.world_pose.rotation.y = float(row["qy"])
            pose.world_pose.rotation.z = float(row["qz"])
            pose.world_pose.rotation.w = float(row["qw"])

            pitch, roll, yaw = quaternion_xyzw_to_pitch_roll_yaw_deg(
                float(row["qx"]),
                float(row["qy"]),
                float(row["qz"]),
                float(row["qw"]),
            )
            pose.euler_degrees.x = pitch
            pose.euler_degrees.y = roll
            pose.euler_degrees.z = yaw

    ground_points_csv = ground_points_csv.resolve() if ground_points_csv else triangulated_ground_points_csv_path(recording)
    if ground_points_csv.exists():
        with ground_points_csv.open() as f:
            for row in csv.DictReader(f):
                point = resp.ground_points.add()
                point.frame_index = int(row["frame_index"])
                point.paired_frame_index = int(row["paired_frame_index"])
                point.point.x = float(row["world_x"])
                point.point.y = float(row["world_y"])
                point.point.z = float(row["world_z"])
                point.side = row.get("side", "")

    grouped_corr: dict[tuple[int, int], list[dict[str, str]]] = defaultdict(list)
    correspondences_csv = (
        correspondences_csv.resolve() if correspondences_csv else triangulated_correspondences_csv_path(recording)
    )
    if correspondences_csv.exists():
        with correspondences_csv.open() as f:
            for row in csv.DictReader(f):
                key = (int(row["frame_index"]), int(row["paired_frame_index"]))
                grouped_corr[key].append(row)

    pair_logs_path = pair_logs_path.resolve() if pair_logs_path else triangulated_pair_logs_path(recording)
    if pair_logs_path.exists():
        pair_logs = json.loads(pair_logs_path.read_text())
        for row in pair_logs:
            frame_index = int(row["frame_index"])
            paired_frame_index = int(row["paired_frame_index"])
            debug = resp.pair_debug.add()
            debug.frame_index = frame_index
            debug.paired_frame_index = paired_frame_index
            debug.status = str(row.get("status", ""))
            debug.good_match_count = int(row.get("good_match_count", 0))
            debug.inlier_count = int(row.get("inlier_count", 0))
            debug.triangulated_left = int(row.get("triangulated_left", 0))
            debug.triangulated_right = int(row.get("triangulated_right", 0))
            for corr_row in grouped_corr.get((frame_index, paired_frame_index), []):
                corr = debug.correspondences.add()
                corr.source_x = float(corr_row["source_x"])
                corr.source_y = float(corr_row["source_y"])
                corr.target_x = float(corr_row["target_x"])
                corr.target_y = float(corr_row["target_y"])
                corr.world_point.x = float(corr_row["world_x"])
                corr.world_point.y = float(corr_row["world_y"])
                corr.world_point.z = float(corr_row["world_z"])
                corr.side = corr_row.get("side", "")

    if output_path.exists():
        output_path.unlink()
    _idoslam_io.write_file(output_path, [resp])
    return output_path
