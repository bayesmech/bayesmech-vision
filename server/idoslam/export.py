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
    canonical_centerline_csv_path,
    canonical_lap_trajectories_csv_path,
    canonical_output_dir,
    idoslam_proto_path,
    iter_messages,
    pairwise_motion_csv_path,
    pairwise_track_plot_path,
    pairwise_trajectory_csv_path,
    plane_output_dir,
    plane_width_csv_path,
    refined_trajectory_csv_path,
    refined_track_plot_path,
    seg_path,
    triangulated_correspondences_csv_path,
    triangulated_ground_points_csv_path,
    triangulated_pair_logs_path,
    triangulated_output_dir,
    triangulated_width_csv_path,
    workspace_path,
    write_track_plot,
)

_idoslam_io = ProtoIO(idoslam_pb2.IdoSlamResponse)
_idoslam_io.FRAME_SIZE_LIMIT = 512 * 1024 * 1024


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


def read_idoslam_pb(path: Path) -> idoslam_pb2.IdoSlamResponse:
    records = _idoslam_io.read_file(path.resolve())
    if not records:
        raise RuntimeError(f"No IdoSlamResponse records found in {path}")
    return records[-1]


def _load_gps_rows(recording: Path) -> list[dict[str, float]]:
    gps_rows: list[dict[str, float]] = []
    for frame_index, frame in enumerate(iter_messages(recording, perceiver_pb2.PerceiverDataFrame)):
        if not frame.HasField("gps_location"):
            continue
        gps_rows.append(
            {
                "frame_index": frame_index,
                "frame_number": int(frame.frame_identifier.frame_number),
                "timestamp_ns": int(frame.frame_identifier.timestamp_ns),
                "latitude": float(frame.gps_location.latitude),
                "longitude": float(frame.gps_location.longitude),
                "altitude": float(frame.gps_location.altitude),
                "accuracy": float(frame.gps_location.accuracy),
            }
        )
    return gps_rows


def _frame_pose_rows_from_proto(
    poses: list[idoslam_pb2.IdoSlamFramePose],
) -> list[dict[str, float | int]]:
    rows: list[dict[str, float | int]] = []
    for pose in poses:
        rows.append(
            {
                "frame_index": int(pose.frame_index),
                "frame_number": int(pose.frame_id.frame_number),
                "timestamp_ns": int(pose.frame_id.timestamp_ns),
                "x": float(pose.world_pose.position.x),
                "y": float(pose.world_pose.position.y),
                "z": float(pose.world_pose.position.z),
                "qx": float(pose.world_pose.rotation.x),
                "qy": float(pose.world_pose.rotation.y),
                "qz": float(pose.world_pose.rotation.z),
                "qw": float(pose.world_pose.rotation.w),
            }
        )
    return rows


def _write_frame_pose_csv(path: Path, poses: list[idoslam_pb2.IdoSlamFramePose]) -> None:
    rows = _frame_pose_rows_from_proto(poses)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=["frame_index", "frame_number", "timestamp_ns", "x", "y", "z", "qx", "qy", "qz", "qw"],
        )
        writer.writeheader()
        writer.writerows(rows)


def _write_pairwise_motion_csv(path: Path, pairwise_motion: list[idoslam_pb2.IdoSlamPairwiseMotion]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=[
                "prev_frame_index",
                "frame_index",
                "prev_timestamp_ns",
                "timestamp_ns",
                "status",
                "keypoints_prev",
                "keypoints",
                "good_match_count",
                "essential_inlier_count",
                "essential_inlier_ratio",
                "translation_magnitude",
                "rotation_deg",
                "dx",
                "dy",
                "dz",
                "qx",
                "qy",
                "qz",
                "qw",
                "mask_pixels_prev",
                "mask_pixels",
            ],
        )
        writer.writeheader()
        for row in pairwise_motion:
            writer.writerow(
                {
                    "prev_frame_index": int(row.prev_frame_index),
                    "frame_index": int(row.frame_index),
                    "prev_timestamp_ns": int(row.prev_timestamp_ns),
                    "timestamp_ns": int(row.timestamp_ns),
                    "status": str(row.status),
                    "keypoints_prev": int(row.keypoints_prev),
                    "keypoints": int(row.keypoints),
                    "good_match_count": int(row.good_match_count),
                    "essential_inlier_count": int(row.essential_inlier_count),
                    "essential_inlier_ratio": float(row.essential_inlier_ratio),
                    "translation_magnitude": float(row.translation_magnitude),
                    "rotation_deg": float(row.rotation_deg),
                    "dx": float(row.dx),
                    "dy": float(row.dy),
                    "dz": float(row.dz),
                    "qx": float(row.qx),
                    "qy": float(row.qy),
                    "qz": float(row.qz),
                    "qw": float(row.qw),
                    "mask_pixels_prev": int(row.mask_pixels_prev),
                    "mask_pixels": int(row.mask_pixels),
                }
            )


def _add_track_width_estimates(dest, csv_path: Path) -> None:
    if not csv_path.exists():
        return
    with csv_path.open() as f:
        for row in csv.DictReader(f):
            estimate = dest.add()
            estimate.frame_index = int(row["frame_index"])
            estimate.frame_number = int(row["frame_number"])
            estimate.timestamp_ns = int(row["timestamp_ns"])
            estimate.latitude = float(row.get("latitude", 0.0) or 0.0)
            estimate.longitude = float(row.get("longitude", 0.0) or 0.0)
            estimate.width_m = float(row.get("width_m", 0.0) or 0.0)
            estimate.left_offset_m = float(row.get("left_offset_m", 0.0) or 0.0)
            estimate.right_offset_m = float(row.get("right_offset_m", 0.0) or 0.0)
            estimate.bike_fraction = float(row.get("bike_fraction", 0.0) or 0.0)
            estimate.method = str(row.get("method", ""))


def _write_track_width_estimates_csv(
    path: Path,
    rows,
    *,
    include_bike_fraction: bool,
    include_method: bool,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "frame_index",
        "frame_number",
        "timestamp_ns",
        "latitude",
        "longitude",
        "width_m",
        "left_offset_m",
        "right_offset_m",
    ]
    if include_bike_fraction:
        fieldnames.append("bike_fraction")
    if include_method:
        fieldnames.append("method")
    with path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            out = {
                "frame_index": int(row.frame_index),
                "frame_number": int(row.frame_number),
                "timestamp_ns": int(row.timestamp_ns),
                "latitude": float(row.latitude),
                "longitude": float(row.longitude),
                "width_m": float(row.width_m),
                "left_offset_m": float(row.left_offset_m),
                "right_offset_m": float(row.right_offset_m),
            }
            if include_bike_fraction:
                out["bike_fraction"] = float(row.bike_fraction)
            if include_method:
                out["method"] = str(row.method)
            writer.writerow(out)


def _add_canonical_centerline(dest, csv_path: Path) -> None:
    if not csv_path.exists():
        return
    with csv_path.open() as f:
        for row in csv.DictReader(f):
            point = dest.add()
            point.bin_index = int(row["bin_index"])
            point.progress_m = float(row["progress_m"])
            point.center_x = float(row["center_x"])
            point.center_y = float(row["center_y"])
            point.width_m = float(row["width_m"])
            point.left_x = float(row["left_x"])
            point.left_y = float(row["left_y"])
            point.right_x = float(row["right_x"])
            point.right_y = float(row["right_y"])


def _write_canonical_centerline_csv(path: Path, rows) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=["bin_index", "progress_m", "center_x", "center_y", "width_m", "left_x", "left_y", "right_x", "right_y"],
        )
        writer.writeheader()
        for row in rows:
            writer.writerow(
                {
                    "bin_index": int(row.bin_index),
                    "progress_m": float(row.progress_m),
                    "center_x": float(row.center_x),
                    "center_y": float(row.center_y),
                    "width_m": float(row.width_m),
                    "left_x": float(row.left_x),
                    "left_y": float(row.left_y),
                    "right_x": float(row.right_x),
                    "right_y": float(row.right_y),
                }
            )


def _add_canonical_frame_tracks(dest, csv_path: Path) -> None:
    if not csv_path.exists():
        return
    with csv_path.open() as f:
        for row in csv.DictReader(f):
            track = dest.add()
            track.frame_index = int(row["frame_index"])
            track.frame_number = int(row["frame_number"])
            track.timestamp_ns = int(row["timestamp_ns"])
            track.lap_id = int(row["lap_id"])
            track.is_partial_lap = str(row["is_partial_lap"]).strip().lower() == "true"
            track.progress_m = float(row["progress_m"])
            track.progress_fraction = float(row["progress_fraction"])
            track.gps_x = float(row["gps_x"])
            track.gps_y = float(row["gps_y"])
            track.canonical_x = float(row["canonical_x"])
            track.canonical_y = float(row["canonical_y"])
            track.lateral_offset_m = float(row["lateral_offset_m"])
            image_lateral = str(row.get("image_lateral_m", "")).strip()
            if image_lateral:
                track.image_lateral_m = float(image_lateral)
                track.has_image_lateral_m = True
            track.trajectory_lateral_m = float(row["trajectory_lateral_m"])
            track.trajectory_x = float(row["trajectory_x"])
            track.trajectory_y = float(row["trajectory_y"])
            track.width_m = float(row["width_m"])
            track.half_width_m = float(row["half_width_m"])


def _write_canonical_frame_tracks_csv(path: Path, rows) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=[
                "frame_index",
                "frame_number",
                "timestamp_ns",
                "lap_id",
                "is_partial_lap",
                "progress_m",
                "progress_fraction",
                "gps_x",
                "gps_y",
                "canonical_x",
                "canonical_y",
                "lateral_offset_m",
                "image_lateral_m",
                "trajectory_lateral_m",
                "trajectory_x",
                "trajectory_y",
                "width_m",
                "half_width_m",
            ],
        )
        writer.writeheader()
        for row in rows:
            writer.writerow(
                {
                    "frame_index": int(row.frame_index),
                    "frame_number": int(row.frame_number),
                    "timestamp_ns": int(row.timestamp_ns),
                    "lap_id": int(row.lap_id),
                    "is_partial_lap": bool(row.is_partial_lap),
                    "progress_m": float(row.progress_m),
                    "progress_fraction": float(row.progress_fraction),
                    "gps_x": float(row.gps_x),
                    "gps_y": float(row.gps_y),
                    "canonical_x": float(row.canonical_x),
                    "canonical_y": float(row.canonical_y),
                    "lateral_offset_m": float(row.lateral_offset_m),
                    "image_lateral_m": float(row.image_lateral_m) if row.has_image_lateral_m else "",
                    "trajectory_lateral_m": float(row.trajectory_lateral_m),
                    "trajectory_x": float(row.trajectory_x),
                    "trajectory_y": float(row.trajectory_y),
                    "width_m": float(row.width_m),
                    "half_width_m": float(row.half_width_m),
                }
            )


def _read_text_if_exists(path: Path) -> str:
    return path.read_text() if path.exists() else ""


def _write_text_if_present(path: Path, text: str) -> None:
    if not text:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def _write_triangulated_outputs(
    response: idoslam_pb2.IdoSlamResponse,
    ground_points_csv: Path,
    correspondences_csv: Path,
    pair_logs_path: Path,
) -> None:
    ground_points_csv.parent.mkdir(parents=True, exist_ok=True)
    with ground_points_csv.open("w", newline="") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=["frame_index", "paired_frame_index", "world_x", "world_y", "world_z", "side"],
        )
        writer.writeheader()
        for point in response.ground_points:
            writer.writerow(
                {
                    "frame_index": int(point.frame_index),
                    "paired_frame_index": int(point.paired_frame_index),
                    "world_x": float(point.point.x),
                    "world_y": float(point.point.y),
                    "world_z": float(point.point.z),
                    "side": str(point.side),
                }
            )

    correspondences_csv.parent.mkdir(parents=True, exist_ok=True)
    with correspondences_csv.open("w", newline="") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=[
                "frame_index",
                "paired_frame_index",
                "source_x",
                "source_y",
                "target_x",
                "target_y",
                "world_x",
                "world_y",
                "world_z",
                "side",
                "on_road",
                "triangulated",
                "inlier",
            ],
        )
        writer.writeheader()
        for debug in response.pair_debug:
            for corr in debug.correspondences:
                writer.writerow(
                    {
                        "frame_index": int(debug.frame_index),
                        "paired_frame_index": int(debug.paired_frame_index),
                        "source_x": float(corr.source_x),
                        "source_y": float(corr.source_y),
                        "target_x": float(corr.target_x),
                        "target_y": float(corr.target_y),
                        "world_x": float(corr.world_point.x) if corr.triangulated else "",
                        "world_y": float(corr.world_point.y) if corr.triangulated else "",
                        "world_z": float(corr.world_point.z) if corr.triangulated else "",
                        "side": str(corr.side),
                        "on_road": bool(corr.on_road or bool(corr.side)),
                        "triangulated": bool(corr.triangulated),
                        "inlier": bool(corr.inlier),
                    }
                )

    pair_logs: list[dict[str, object]] = []
    for debug in response.pair_debug:
        pair_logs.append(
            {
                "frame_index": int(debug.frame_index),
                "paired_frame_index": int(debug.paired_frame_index),
                "status": str(debug.status),
                "good_match_count": int(debug.good_match_count),
                "inlier_count": int(debug.inlier_count),
                "triangulated_left": int(debug.triangulated_left),
                "triangulated_right": int(debug.triangulated_right),
                "on_road_count": int(debug.on_road_count),
            }
        )
    pair_logs_path.parent.mkdir(parents=True, exist_ok=True)
    pair_logs_path.write_text(json.dumps(pair_logs, indent=2))


def hydrate_workspace_from_idoslam_pb(recording: Path) -> dict[str, bool]:
    recording = recording.resolve()
    response = read_idoslam_pb(idoslam_proto_path(recording))
    recording = recording.resolve()
    if response.recording_path:
        proto_recording = Path(response.recording_path).resolve()
        if proto_recording != recording:
            raise RuntimeError(
                f"Resume proto recording mismatch: proto={proto_recording} requested={recording}"
            )

    has_correspondences = any(len(debug.correspondences) > 0 for debug in response.pair_debug)
    state = {
        "has_raw_poses": len(response.frame_poses) > 0,
        "has_refined_poses": len(response.refined_frame_poses) > 0,
        "has_pairwise_motion": len(response.pairwise_motion) > 0,
        "has_ground_points": len(response.ground_points) > 0,
        "has_pair_debug": len(response.pair_debug) > 0,
        "has_correspondences": has_correspondences,
        "has_plane_width": len(response.plane_width_estimates) > 0,
        "has_canonical_track": len(response.canonical_centerline) > 0 and len(response.canonical_frame_tracks) > 0,
        "has_triangulated_width": len(response.triangulated_width_estimates) > 0,
    }

    gps_rows = _load_gps_rows(recording)
    if state["has_raw_poses"]:
        raw_csv = pairwise_trajectory_csv_path(recording)
        _write_frame_pose_csv(raw_csv, list(response.frame_poses))
        if state["has_pairwise_motion"]:
            _write_pairwise_motion_csv(pairwise_motion_csv_path(recording), list(response.pairwise_motion))
        write_track_plot(pairwise_track_plot_path(recording), _frame_pose_rows_from_proto(list(response.frame_poses)), gps_rows)

    if state["has_refined_poses"]:
        refined_csv = refined_trajectory_csv_path(recording)
        _write_frame_pose_csv(refined_csv, list(response.refined_frame_poses))
        write_track_plot(refined_track_plot_path(recording), _frame_pose_rows_from_proto(list(response.refined_frame_poses)), gps_rows)

    if state["has_ground_points"] or state["has_pair_debug"]:
        _write_triangulated_outputs(
            response,
            triangulated_ground_points_csv_path(recording),
            triangulated_correspondences_csv_path(recording),
            triangulated_pair_logs_path(recording),
        )

    if state["has_plane_width"]:
        _write_track_width_estimates_csv(
            plane_width_csv_path(recording),
            response.plane_width_estimates,
            include_bike_fraction=True,
            include_method=False,
        )
    _write_text_if_present(plane_output_dir(recording) / "summary.json", response.plane_width_summary_json)

    if state["has_canonical_track"]:
        _write_canonical_centerline_csv(canonical_centerline_csv_path(recording), response.canonical_centerline)
        _write_canonical_frame_tracks_csv(canonical_lap_trajectories_csv_path(recording), response.canonical_frame_tracks)
    _write_text_if_present(canonical_output_dir(recording) / "summary.json", response.canonical_summary_json)

    if state["has_triangulated_width"]:
        _write_track_width_estimates_csv(
            triangulated_width_csv_path(recording),
            response.triangulated_width_estimates,
            include_bike_fraction=False,
            include_method=True,
        )
    _write_text_if_present(triangulated_output_dir(recording) / "summary.json", response.triangulated_summary_json)

    return state


def _add_frame_poses(
    dest: list[idoslam_pb2.IdoSlamFramePose],
    trajectory_csv: Path,
    device_id: str,
) -> None:
    with trajectory_csv.open() as f:
        for row in csv.DictReader(f):
            pose = dest.add()
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


def write_idoslam_pb(recording: Path) -> Path:
    recording = recording.resolve()
    segmentation = seg_path(recording).resolve()
    output_path = idoslam_proto_path(recording)

    raw_trajectory_csv = pairwise_trajectory_csv_path(recording)
    if not raw_trajectory_csv.exists():
        raise FileNotFoundError(f"Missing raw trajectory CSV: {raw_trajectory_csv}")

    refined_candidate = refined_trajectory_csv_path(recording)
    refined_trajectory_csv = refined_candidate if refined_candidate.exists() else None

    pairwise_motion_csv = pairwise_motion_csv_path(recording)

    first_frame = next(iter_messages(recording, perceiver_pb2.PerceiverDataFrame), None)
    if first_frame is None:
        raise RuntimeError("Recording is empty")

    resp = idoslam_pb2.IdoSlamResponse()
    resp.first_frame_id.CopyFrom(first_frame.frame_identifier)
    resp.recording_path = str(recording)
    resp.segmentation_path = str(segmentation)
    resp.workspace_path = str(workspace_path(recording))

    device_id = first_frame.frame_identifier.device_id
    _add_frame_poses(resp.frame_poses, raw_trajectory_csv, device_id)
    if refined_trajectory_csv is not None and refined_trajectory_csv.exists():
        _add_frame_poses(resp.refined_frame_poses, refined_trajectory_csv, device_id)

    if pairwise_motion_csv.exists():
        with pairwise_motion_csv.open() as f:
            for row in csv.DictReader(f):
                motion = resp.pairwise_motion.add()
                motion.prev_frame_index = int(row["prev_frame_index"])
                motion.frame_index = int(row["frame_index"])
                motion.prev_timestamp_ns = int(row["prev_timestamp_ns"])
                motion.timestamp_ns = int(row["timestamp_ns"])
                motion.status = str(row.get("status", ""))
                motion.keypoints_prev = int(row.get("keypoints_prev", 0))
                motion.keypoints = int(row.get("keypoints", 0))
                motion.good_match_count = int(row.get("good_match_count", 0))
                motion.essential_inlier_count = int(row.get("essential_inlier_count", 0))
                motion.essential_inlier_ratio = float(row.get("essential_inlier_ratio", 0.0))
                motion.translation_magnitude = float(row.get("translation_magnitude", 0.0))
                motion.rotation_deg = float(row.get("rotation_deg", 0.0))
                motion.dx = float(row.get("dx", 0.0))
                motion.dy = float(row.get("dy", 0.0))
                motion.dz = float(row.get("dz", 0.0))
                motion.qx = float(row.get("qx", 0.0))
                motion.qy = float(row.get("qy", 0.0))
                motion.qz = float(row.get("qz", 0.0))
                motion.qw = float(row.get("qw", 1.0))
                motion.mask_pixels_prev = int(row.get("mask_pixels_prev", 0))
                motion.mask_pixels = int(row.get("mask_pixels", 0))

    _add_track_width_estimates(resp.plane_width_estimates, plane_width_csv_path(recording))
    resp.plane_width_summary_json = _read_text_if_exists(plane_output_dir(recording) / "summary.json")

    _add_canonical_centerline(resp.canonical_centerline, canonical_centerline_csv_path(recording))
    _add_canonical_frame_tracks(resp.canonical_frame_tracks, canonical_lap_trajectories_csv_path(recording))
    resp.canonical_summary_json = _read_text_if_exists(canonical_output_dir(recording) / "summary.json")

    _add_track_width_estimates(resp.triangulated_width_estimates, triangulated_width_csv_path(recording))
    resp.triangulated_summary_json = _read_text_if_exists(triangulated_output_dir(recording) / "summary.json")

    ground_points_csv = triangulated_ground_points_csv_path(recording)
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
    correspondences_csv = triangulated_correspondences_csv_path(recording)
    if correspondences_csv.exists():
        with correspondences_csv.open() as f:
            for row in csv.DictReader(f):
                key = (int(row["frame_index"]), int(row["paired_frame_index"]))
                grouped_corr[key].append(row)

    pair_logs_path = triangulated_pair_logs_path(recording)
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
            debug.on_road_count = int(row.get("on_road_count", 0))
            for corr_row in grouped_corr.get((frame_index, paired_frame_index), []):
                corr = debug.correspondences.add()
                corr.source_x = float(corr_row["source_x"])
                corr.source_y = float(corr_row["source_y"])
                corr.target_x = float(corr_row["target_x"])
                corr.target_y = float(corr_row["target_y"])
                corr.on_road = str(corr_row.get("on_road", "")).strip().lower() in ("1", "true", "yes") or bool(
                    str(corr_row.get("side", "")).strip()
                )
                corr.triangulated = str(corr_row.get("triangulated", "")).strip().lower() in ("1", "true", "yes")
                corr.inlier = str(corr_row.get("inlier", "")).strip().lower() in ("1", "true", "yes")
                world_x = str(corr_row.get("world_x", "")).strip()
                world_y = str(corr_row.get("world_y", "")).strip()
                world_z = str(corr_row.get("world_z", "")).strip()
                if world_x and world_y and world_z:
                    corr.world_point.x = float(world_x)
                    corr.world_point.y = float(world_y)
                    corr.world_point.z = float(world_z)
                corr.side = corr_row.get("side", "")

    if output_path.exists():
        output_path.unlink()
    _idoslam_io.write_file(output_path, [resp])
    return output_path
