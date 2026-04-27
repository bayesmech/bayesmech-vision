#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import math
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import yaml
from scipy import sparse
from scipy.optimize import least_squares

_server_root = Path(__file__).resolve().parent.parent
_project_root = _server_root.parent
for _p in (str(_project_root), str(_project_root / "proto"), str(_server_root)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from proto import perceiver_pb2
from idoslam.common import (
    estimate_pairwise_global_alignment,
    gps_to_local_xy,
    iter_messages,
    pairwise_motion_csv_path,
    pairwise_trajectory_csv_path,
    pose_refine_output_dir,
    project_track_to_2d,
    refined_trajectory_csv_path,
    write_track_plot,
)


_CONFIG_PATH = Path(__file__).parent / "config.yaml"


@dataclass
class TrajectoryRow:
    frame_index: int
    frame_number: int
    timestamp_ns: int
    x: float
    y: float
    z: float
    qx: float
    qy: float
    qz: float
    qw: float


@dataclass
class PairMotionRow:
    prev_frame_index: int
    frame_index: int
    status: str
    good_match_count: int
    essential_inlier_count: int
    essential_inlier_ratio: float


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Refine the pairwise SIFT trajectory with GPS XY priors")
    p.add_argument("recording", type=Path, help="Path to .vis.pb recording")
    return p.parse_args()


def output_path(recording: Path) -> Path:
    return pose_refine_output_dir(recording)


def default_trajectory_path(recording: Path) -> Path:
    return pairwise_trajectory_csv_path(recording)


def default_pair_motion_path(recording: Path) -> Path:
    return pairwise_motion_csv_path(recording)


def _config() -> dict[str, object]:
    with _CONFIG_PATH.open() as f:
        raw = yaml.safe_load(f) or {}
    pose_cfg = raw.get("pose_refinement") or {}
    solver_cfg = pose_cfg.get("solver") or {}
    weights_cfg = pose_cfg.get("weights") or {}
    gps_cfg = pose_cfg.get("gps") or {}
    acceptance_cfg = pose_cfg.get("acceptance") or {}
    visual_weight = float(weights_cfg.get("visual", 1.0))
    gps_ratio = weights_cfg.get("gps_visual_ratio")
    if gps_ratio is None:
        legacy_gps_weight = float(weights_cfg.get("gps_xy", 0.15))
        gps_ratio = legacy_gps_weight / max(visual_weight, 1e-9)
    return {
        "enabled": bool(pose_cfg.get("enabled", True)),
        "solver": {
            "loss": str(solver_cfg.get("loss", "soft_l1")),
            "f_scale": float(solver_cfg.get("f_scale", 1.0)),
            "max_nfev": int(solver_cfg.get("max_nfev", 250)),
        },
        "weights": {
            "visual": visual_weight,
            "gps_visual_ratio": float(gps_ratio),
            "smoothness": float(weights_cfg.get("smoothness", 0.05)),
            "non_ok_pair_scale": float(weights_cfg.get("non_ok_pair_scale", 0.1)),
        },
        "gps": {
            "min_common_points": int(gps_cfg.get("min_common_points", 20)),
            "min_accuracy_m": float(gps_cfg.get("min_accuracy_m", 3.0)),
            "max_accuracy_m": float(gps_cfg.get("max_accuracy_m", 30.0)),
        },
        "acceptance": {
            "require_gps_improvement": bool(acceptance_cfg.get("require_gps_improvement", True)),
            "min_gps_rmse_improvement_m": float(acceptance_cfg.get("min_gps_rmse_improvement_m", 0.05)),
            "max_visual_residual_rmse_m": float(acceptance_cfg.get("max_visual_residual_rmse_m", 2.5)),
            "max_step_delta_m": float(acceptance_cfg.get("max_step_delta_m", 4.0)),
        },
    }


def load_trajectory_rows(csv_path: Path) -> list[TrajectoryRow]:
    rows: list[TrajectoryRow] = []
    with csv_path.open() as f:
        for row in csv.DictReader(f):
            rows.append(
                TrajectoryRow(
                    frame_index=int(row["frame_index"]),
                    frame_number=int(row["frame_number"]),
                    timestamp_ns=int(row["timestamp_ns"]),
                    x=float(row["x"]),
                    y=float(row["y"]),
                    z=float(row["z"]),
                    qx=float(row["qx"]),
                    qy=float(row["qy"]),
                    qz=float(row["qz"]),
                    qw=float(row["qw"]),
                )
            )
    return rows


def load_pair_motion_rows(csv_path: Path) -> dict[tuple[int, int], PairMotionRow]:
    out: dict[tuple[int, int], PairMotionRow] = {}
    with csv_path.open() as f:
        for row in csv.DictReader(f):
            motion = PairMotionRow(
                prev_frame_index=int(row["prev_frame_index"]),
                frame_index=int(row["frame_index"]),
                status=str(row.get("status", "")),
                good_match_count=int(row.get("good_match_count", 0)),
                essential_inlier_count=int(row.get("essential_inlier_count", 0)),
                essential_inlier_ratio=float(row.get("essential_inlier_ratio", 0.0)),
            )
            out[(motion.prev_frame_index, motion.frame_index)] = motion
    return out


def load_gps_rows(recording: Path, needed_timestamps: set[int]) -> list[dict[str, float]]:
    gps_rows: list[dict[str, float]] = []
    for frame_index, frame in enumerate(iter_messages(recording, perceiver_pb2.PerceiverDataFrame)):
        if not frame.HasField("gps_location"):
            continue
        timestamp_ns = int(frame.frame_identifier.timestamp_ns)
        if timestamp_ns not in needed_timestamps:
            continue
        gps_rows.append(
            {
                "frame_index": frame_index,
                "frame_number": int(frame.frame_identifier.frame_number),
                "timestamp_ns": timestamp_ns,
                "latitude": float(frame.gps_location.latitude),
                "longitude": float(frame.gps_location.longitude),
                "altitude": float(frame.gps_location.altitude),
                "accuracy": float(frame.gps_location.accuracy),
            }
        )
    gps_rows.sort(key=lambda row: int(row["timestamp_ns"]))
    return gps_rows


def track_plane(points_xyz: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    mean = points_xyz.mean(axis=0, keepdims=True)
    centered = points_xyz - mean
    _, _, vh = np.linalg.svd(centered, full_matrices=False)
    basis2 = vh[:2].T
    normal = vh[2]
    coords2 = centered @ basis2
    heights = centered @ normal
    return mean.reshape(3), basis2, normal, coords2, heights


def metric_track_xy(coords2: np.ndarray, scale: float, rot: np.ndarray, trans: np.ndarray) -> np.ndarray:
    return (scale * (rot @ coords2.T)).T + trans


def inverse_metric_track_xy(metric_xy: np.ndarray, scale: float, rot: np.ndarray, trans: np.ndarray) -> np.ndarray:
    return (rot.T @ ((metric_xy - trans) / scale).T).T


def fit_two_point_similarity(src: np.ndarray, dst: np.ndarray) -> tuple[float, np.ndarray, np.ndarray]:
    src_vec = src[1] - src[0]
    dst_vec = dst[1] - dst[0]
    src_len = float(np.linalg.norm(src_vec))
    dst_len = float(np.linalg.norm(dst_vec))
    if src_len <= 1e-9 or dst_len <= 1e-9:
        return float("nan"), np.eye(2, dtype=np.float64), np.zeros(2, dtype=np.float64)
    angle = math.atan2(float(dst_vec[1]), float(dst_vec[0])) - math.atan2(float(src_vec[1]), float(src_vec[0]))
    c = math.cos(angle)
    s = math.sin(angle)
    rot = np.array([[c, -s], [s, c]], dtype=np.float64)
    scale = dst_len / src_len
    trans = dst[0] - scale * (rot @ src[0])
    return scale, rot, trans


def rmse_m(pred_xy: np.ndarray, target_xy: np.ndarray) -> float:
    if len(pred_xy) == 0:
        return 0.0
    return float(np.sqrt(np.mean(np.sum((pred_xy - target_xy) ** 2, axis=1))))


def weighted_pair_scale(motion: PairMotionRow | None, cfg: dict[str, object]) -> float:
    weights_cfg = cfg["weights"]
    base = float(weights_cfg["visual"])
    if motion is None:
        return base
    inlier_quality = float(np.clip(motion.essential_inlier_count / 30.0, 0.25, 2.0))
    ratio_quality = float(np.clip(motion.essential_inlier_ratio / 0.5, 0.25, 2.0))
    weight = base * inlier_quality * ratio_quality
    if motion.status != "ok":
        weight *= float(weights_cfg["non_ok_pair_scale"])
    return weight


def optimizer_residuals(
    flat_coords2: np.ndarray,
    jacobian: sparse.csr_matrix,
    target: np.ndarray,
) -> np.ndarray:
    return np.asarray(jacobian @ flat_coords2 - target).reshape(-1)


def build_anchored_linear_residual_model(
    frame_count: int,
    raw_metric_xy: np.ndarray,
    pair_weights: np.ndarray,
    observed_indices: np.ndarray,
    gps_xy_m: np.ndarray,
    gps_accuracy_m: np.ndarray,
    gps_weight: float,
    smoothness_weight: float,
    fixed_metric_xy: dict[int, np.ndarray],
) -> tuple[sparse.csr_matrix, np.ndarray, np.ndarray, np.ndarray]:
    variable_frame_indices = np.asarray(
        [idx for idx in range(frame_count) if idx not in fixed_metric_xy],
        dtype=np.int32,
    )
    variable_by_frame = {int(frame_idx): pos for pos, frame_idx in enumerate(variable_frame_indices)}

    row_indices: list[int] = []
    col_indices: list[int] = []
    values: list[float] = []
    targets: list[float] = []
    row = 0
    variable_count = 2 * len(variable_frame_indices)

    def add_residual(terms: list[tuple[int, int, float]], target_value: float, scale: float) -> None:
        nonlocal row
        adjusted_target = scale * float(target_value)
        has_variable = False
        for frame_idx, dim, coeff in terms:
            scaled_coeff = scale * float(coeff)
            fixed_xy = fixed_metric_xy.get(int(frame_idx))
            if fixed_xy is not None:
                adjusted_target -= scaled_coeff * float(fixed_xy[dim])
                continue
            variable_pos = variable_by_frame[int(frame_idx)]
            row_indices.append(row)
            col_indices.append(2 * variable_pos + dim)
            values.append(scaled_coeff)
            has_variable = True
        if has_variable or abs(adjusted_target) > 1e-12:
            targets.append(adjusted_target)
            row += 1

    raw_metric_deltas = raw_metric_xy[1:] - raw_metric_xy[:-1]
    for idx, delta2 in enumerate(raw_metric_deltas):
        pair_weight = float(pair_weights[idx])
        if pair_weight <= 0.0:
            continue
        step_scale = math.sqrt(pair_weight)
        for dim in range(2):
            add_residual(
                [(idx, dim, -1.0), (idx + 1, dim, 1.0)],
                float(delta2[dim]),
                step_scale,
            )

    if gps_weight > 0.0 and len(observed_indices) > 0:
        gps_scale = math.sqrt(gps_weight)
        for obs_pos, frame_idx in enumerate(observed_indices):
            accuracy_scale = gps_scale / float(gps_accuracy_m[obs_pos])
            for dim in range(2):
                add_residual(
                    [(int(frame_idx), dim, 1.0)],
                    float(gps_xy_m[obs_pos, dim]),
                    accuracy_scale,
                )

    if smoothness_weight > 0.0 and frame_count >= 3:
        smooth_scale = math.sqrt(smoothness_weight)
        for idx in range(frame_count - 2):
            for dim in range(2):
                add_residual(
                    [(idx, dim, 1.0), (idx + 1, dim, -2.0), (idx + 2, dim, 1.0)],
                    0.0,
                    smooth_scale,
                )

    if row == 0:
        jacobian = sparse.csr_matrix((0, variable_count), dtype=np.float64)
    else:
        jacobian = sparse.csr_matrix((values, (row_indices, col_indices)), shape=(row, variable_count), dtype=np.float64)
    initial = raw_metric_xy[variable_frame_indices].reshape(-1)
    return jacobian, np.asarray(targets, dtype=np.float64), initial, variable_frame_indices


def write_gps_score_csv(
    out_path: Path,
    traj_rows: list[TrajectoryRow],
    observed_indices: np.ndarray,
    gps_xy_m: np.ndarray,
    gps_accuracy_m: np.ndarray,
    raw_metric_xy: np.ndarray,
    refined_metric_xy: np.ndarray,
    chosen_metric_xy: np.ndarray,
    anchor_frame_indices: set[int],
) -> None:
    fieldnames = [
        "frame_index",
        "frame_number",
        "timestamp_ns",
        "gps_x_m",
        "gps_y_m",
        "gps_accuracy_m",
        "is_anchor",
        "raw_pose_x_m",
        "raw_pose_y_m",
        "raw_gps_error_m",
        "refined_pose_x_m",
        "refined_pose_y_m",
        "refined_gps_error_m",
        "chosen_pose_x_m",
        "chosen_pose_y_m",
        "chosen_gps_error_m",
    ]
    with out_path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for obs_pos, frame_idx in enumerate(observed_indices):
            row = traj_rows[int(frame_idx)]
            gps_xy = gps_xy_m[obs_pos]
            raw_xy = raw_metric_xy[int(frame_idx)]
            refined_xy = refined_metric_xy[int(frame_idx)]
            chosen_xy = chosen_metric_xy[int(frame_idx)]
            writer.writerow(
                {
                    "frame_index": row.frame_index,
                    "frame_number": row.frame_number,
                    "timestamp_ns": row.timestamp_ns,
                    "gps_x_m": float(gps_xy[0]),
                    "gps_y_m": float(gps_xy[1]),
                    "gps_accuracy_m": float(gps_accuracy_m[obs_pos]),
                    "is_anchor": int(int(frame_idx) in anchor_frame_indices),
                    "raw_pose_x_m": float(raw_xy[0]),
                    "raw_pose_y_m": float(raw_xy[1]),
                    "raw_gps_error_m": float(np.linalg.norm(raw_xy - gps_xy)),
                    "refined_pose_x_m": float(refined_xy[0]),
                    "refined_pose_y_m": float(refined_xy[1]),
                    "refined_gps_error_m": float(np.linalg.norm(refined_xy - gps_xy)),
                    "chosen_pose_x_m": float(chosen_xy[0]),
                    "chosen_pose_y_m": float(chosen_xy[1]),
                    "chosen_gps_error_m": float(np.linalg.norm(chosen_xy - gps_xy)),
                }
            )


def write_pairwise_change_csv(
    out_path: Path,
    traj_rows: list[TrajectoryRow],
    pair_motion: dict[tuple[int, int], PairMotionRow],
    pair_weights: np.ndarray,
    raw_metric_xy: np.ndarray,
    refined_metric_xy: np.ndarray,
    chosen_metric_xy: np.ndarray,
) -> None:
    fieldnames = [
        "prev_frame_index",
        "frame_index",
        "status",
        "pair_weight",
        "raw_dx_m",
        "raw_dy_m",
        "raw_step_m",
        "refined_dx_m",
        "refined_dy_m",
        "refined_step_m",
        "refined_pairwise_change_m",
        "chosen_dx_m",
        "chosen_dy_m",
        "chosen_step_m",
        "chosen_pairwise_change_m",
    ]
    raw_deltas = raw_metric_xy[1:] - raw_metric_xy[:-1]
    refined_deltas = refined_metric_xy[1:] - refined_metric_xy[:-1]
    chosen_deltas = chosen_metric_xy[1:] - chosen_metric_xy[:-1]
    with out_path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for idx in range(len(traj_rows) - 1):
            prev_row = traj_rows[idx]
            row = traj_rows[idx + 1]
            motion = pair_motion.get((prev_row.frame_index, row.frame_index))
            raw_delta = raw_deltas[idx]
            refined_delta = refined_deltas[idx]
            chosen_delta = chosen_deltas[idx]
            writer.writerow(
                {
                    "prev_frame_index": prev_row.frame_index,
                    "frame_index": row.frame_index,
                    "status": motion.status if motion is not None else "missing",
                    "pair_weight": float(pair_weights[idx]),
                    "raw_dx_m": float(raw_delta[0]),
                    "raw_dy_m": float(raw_delta[1]),
                    "raw_step_m": float(np.linalg.norm(raw_delta)),
                    "refined_dx_m": float(refined_delta[0]),
                    "refined_dy_m": float(refined_delta[1]),
                    "refined_step_m": float(np.linalg.norm(refined_delta)),
                    "refined_pairwise_change_m": float(np.linalg.norm(refined_delta - raw_delta)),
                    "chosen_dx_m": float(chosen_delta[0]),
                    "chosen_dy_m": float(chosen_delta[1]),
                    "chosen_step_m": float(np.linalg.norm(chosen_delta)),
                    "chosen_pairwise_change_m": float(np.linalg.norm(chosen_delta - raw_delta)),
                }
            )


def copy_raw_outputs(
    trajectory_csv: Path,
    out_csv: Path,
    out_dir: Path,
    traj_rows_for_plot: list[dict[str, float]],
    gps_rows: list[dict[str, float]],
    summary: dict[str, object],
) -> None:
    shutil.copy2(trajectory_csv, out_csv)
    write_track_plot(out_dir / "track_plot.png", traj_rows_for_plot, gps_rows)
    write_track_plot(out_dir / "pre_refinement_poses.png", traj_rows_for_plot, gps_rows)
    write_track_plot(out_dir / "post_refinement_poses.png", traj_rows_for_plot, gps_rows)
    raw_track_plot = trajectory_csv.parent / "track_plot.png"
    if not (out_dir / "track_plot.png").exists() and raw_track_plot.exists():
        shutil.copy2(raw_track_plot, out_dir / "track_plot.png")
    if not (out_dir / "pre_refinement_poses.png").exists() and raw_track_plot.exists():
        shutil.copy2(raw_track_plot, out_dir / "pre_refinement_poses.png")
    if not (out_dir / "post_refinement_poses.png").exists() and raw_track_plot.exists():
        shutil.copy2(raw_track_plot, out_dir / "post_refinement_poses.png")
    summary.update(
        {
            "pre_refinement_pose_plot": str(out_dir / "pre_refinement_poses.png"),
            "post_refinement_pose_plot": str(out_dir / "post_refinement_poses.png"),
            "selected_pose_plot": str(out_dir / "track_plot.png"),
        }
    )
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=2))


def raw_pose_gps_global_alignment_summary(
    traj_rows: list[TrajectoryRow],
    observed_indices: list[int],
    observed_xy_m: list[np.ndarray],
) -> dict[str, object]:
    if len(observed_indices) < 2:
        return {
            "raw_pose_gps_global_alignment_pair_count": 0,
            "raw_pose_gps_global_alignment_reason": "too_few_gps_pairs",
        }
    points_xyz = np.array([[row.x, row.y, row.z] for row in traj_rows], dtype=np.float64)
    obs_idx_arr = np.asarray(observed_indices, dtype=np.int32)
    slam_2d = project_track_to_2d(points_xyz[obs_idx_arr])
    gps_xy = np.asarray(observed_xy_m, dtype=np.float64)
    alignment = estimate_pairwise_global_alignment(slam_2d, gps_xy)
    translation = np.asarray(alignment["translation"], dtype=np.float64)
    return {
        "raw_pose_gps_global_alignment_pair_count": int(alignment["pair_count"]),
        "raw_pose_gps_global_rotation_rad": float(alignment["theta_rad"]),
        "raw_pose_gps_global_rotation_deg": float(alignment["theta_deg"]),
        "raw_pose_gps_global_scale_divisor": float(alignment["scale_divisor"]),
        "raw_pose_gps_global_visual_to_gps_scale": float(alignment["visual_to_gps_scale"]),
        "raw_pose_gps_global_delta_x_m": float(translation[0]),
        "raw_pose_gps_global_delta_y_m": float(translation[1]),
    }


def first_distinct_gps_anchor_indices(
    observed_indices: np.ndarray,
    raw_coords2: np.ndarray,
    gps_xy_obs: np.ndarray,
) -> np.ndarray:
    first_pos = 0
    first_frame_idx = int(observed_indices[first_pos])
    first_gps = gps_xy_obs[first_pos]
    first_visual = raw_coords2[first_frame_idx]
    for obs_pos in range(1, len(observed_indices)):
        frame_idx = int(observed_indices[obs_pos])
        if np.linalg.norm(gps_xy_obs[obs_pos] - first_gps) <= 1e-6:
            continue
        if np.linalg.norm(raw_coords2[frame_idx] - first_visual) <= 1e-9:
            continue
        return np.asarray([first_frame_idx, frame_idx], dtype=np.int32)
    return observed_indices[:2].astype(np.int32)


def main() -> None:
    args = parse_args()
    config = _config()
    recording = args.recording.resolve()
    trajectory_csv = default_trajectory_path(recording)
    pair_motion_csv = default_pair_motion_path(recording)
    out_dir = output_path(recording)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_csv = out_dir / refined_trajectory_csv_path(recording).name

    traj_rows = load_trajectory_rows(trajectory_csv)
    pair_motion = load_pair_motion_rows(pair_motion_csv) if pair_motion_csv.exists() else {}
    needed_timestamps = {row.timestamp_ns for row in traj_rows}
    gps_rows = load_gps_rows(recording, needed_timestamps)

    plot_traj_rows = [
        {
            "frame_index": row.frame_index,
            "frame_number": row.frame_number,
            "timestamp_ns": row.timestamp_ns,
            "x": row.x,
            "y": row.y,
            "z": row.z,
        }
        for row in traj_rows
    ]
    base_summary: dict[str, object] = {
        "recording": str(recording),
        "trajectory_csv": str(trajectory_csv),
        "pair_motion_csv": str(pair_motion_csv),
        "output_dir": str(out_dir),
        "enabled": bool(config["enabled"]),
        "trajectory_row_count": len(traj_rows),
        "gps_row_count": len(gps_rows),
    }
    if not config["enabled"]:
        summary = {
            **base_summary,
            "applied": False,
            "accepted": False,
            "reason": "disabled_in_config",
        }
        copy_raw_outputs(trajectory_csv, out_csv, out_dir, plot_traj_rows, gps_rows, summary)
        print(json.dumps(summary, indent=2))
        return

    gps_cfg = config["gps"]
    gps_rows_by_ts = {int(row["timestamp_ns"]): row for row in gps_rows}
    observed_indices: list[int] = []
    observed_xy_m: list[np.ndarray] = []
    observed_accuracy_m: list[float] = []
    matched_rows = [gps_rows_by_ts[row.timestamp_ns] for row in traj_rows if row.timestamp_ns in gps_rows_by_ts]
    matched_rows = [
        row
        for row in matched_rows
        if float(row["accuracy"]) <= float(gps_cfg["max_accuracy_m"]) or float(row["accuracy"]) <= 0.0
    ]
    if matched_rows:
        gps_xy_all = gps_to_local_xy(
            np.array([float(row["latitude"]) for row in matched_rows], dtype=np.float64),
            np.array([float(row["longitude"]) for row in matched_rows], dtype=np.float64),
        )
        gps_xy_by_ts = {int(row["timestamp_ns"]): gps_xy_all[idx] for idx, row in enumerate(matched_rows)}
    else:
        gps_xy_by_ts = {}

    for idx, row in enumerate(traj_rows):
        gps_row = gps_rows_by_ts.get(row.timestamp_ns)
        gps_xy = gps_xy_by_ts.get(row.timestamp_ns)
        if gps_row is None or gps_xy is None:
            continue
        observed_indices.append(idx)
        observed_xy_m.append(gps_xy)
        accuracy_m = float(gps_row["accuracy"])
        if accuracy_m <= 0.0:
            accuracy_m = float(gps_cfg["min_accuracy_m"])
        accuracy_m = max(float(gps_cfg["min_accuracy_m"]), accuracy_m)
        observed_accuracy_m.append(accuracy_m)

    base_summary.update(raw_pose_gps_global_alignment_summary(traj_rows, observed_indices, observed_xy_m))

    if len(observed_indices) < int(gps_cfg["min_common_points"]):
        summary = {
            **base_summary,
            "applied": False,
            "accepted": False,
            "reason": "too_few_gps_matches",
            "gps_match_count": len(observed_indices),
        }
        copy_raw_outputs(trajectory_csv, out_csv, out_dir, plot_traj_rows, gps_rows, summary)
        print(json.dumps(summary, indent=2))
        return

    points_xyz = np.array([[row.x, row.y, row.z] for row in traj_rows], dtype=np.float64)
    plane_mean, plane_basis2, plane_normal, raw_coords2, heights = track_plane(points_xyz)
    obs_idx_arr = np.asarray(observed_indices, dtype=np.int32)
    gps_xy_obs = np.asarray(observed_xy_m, dtype=np.float64)
    gps_accuracy_obs = np.asarray(observed_accuracy_m, dtype=np.float64)

    anchor_frame_indices_arr = first_distinct_gps_anchor_indices(obs_idx_arr, raw_coords2, gps_xy_obs)
    anchor_obs_positions = [int(np.flatnonzero(obs_idx_arr == frame_idx)[0]) for frame_idx in anchor_frame_indices_arr]
    anchor_src = raw_coords2[anchor_frame_indices_arr]
    anchor_dst = gps_xy_obs[anchor_obs_positions]
    similarity_scale, similarity_rot, similarity_trans = fit_two_point_similarity(anchor_src, anchor_dst)
    if not np.isfinite(similarity_scale) or similarity_scale <= 1e-9:
        summary = {
            **base_summary,
            "applied": False,
            "accepted": False,
            "reason": "invalid_two_point_anchor_similarity",
        }
        copy_raw_outputs(trajectory_csv, out_csv, out_dir, plot_traj_rows, gps_rows, summary)
        print(json.dumps(summary, indent=2))
        return

    raw_metric_xy = metric_track_xy(raw_coords2, similarity_scale, similarity_rot, similarity_trans)
    fixed_metric_xy = {
        int(anchor_frame_indices_arr[0]): gps_xy_obs[anchor_obs_positions[0]].copy(),
        int(anchor_frame_indices_arr[1]): gps_xy_obs[anchor_obs_positions[1]].copy(),
    }
    for frame_idx, fixed_xy in fixed_metric_xy.items():
        raw_metric_xy[frame_idx] = fixed_xy

    pair_weights = np.asarray(
        [
            weighted_pair_scale(
                pair_motion.get((traj_rows[idx].frame_index, traj_rows[idx + 1].frame_index)),
                config,
            )
            for idx in range(len(traj_rows) - 1)
        ],
        dtype=np.float64,
    )
    visual_weight = float(config["weights"]["visual"])
    gps_visual_ratio = float(config["weights"]["gps_visual_ratio"])
    gps_weight = visual_weight * gps_visual_ratio

    jacobian, target, initial_metric_xy, variable_frame_indices = build_anchored_linear_residual_model(
        len(traj_rows),
        raw_metric_xy,
        pair_weights,
        obs_idx_arr,
        gps_xy_obs,
        gps_accuracy_obs,
        gps_weight,
        float(config["weights"]["smoothness"]),
        fixed_metric_xy,
    )

    solver_cfg = config["solver"]
    result = least_squares(
        optimizer_residuals,
        initial_metric_xy,
        args=(jacobian, target),
        jac=lambda _x, _jacobian, _target: _jacobian,
        loss=str(solver_cfg["loss"]),
        f_scale=float(solver_cfg["f_scale"]),
        max_nfev=int(solver_cfg["max_nfev"]),
        tr_solver="lsmr",
    )
    refined_metric_xy = raw_metric_xy.copy()
    refined_variables = result.x.reshape(-1, 2)
    for var_pos, frame_idx in enumerate(variable_frame_indices):
        refined_metric_xy[int(frame_idx)] = refined_variables[var_pos]
    for frame_idx, fixed_xy in fixed_metric_xy.items():
        refined_metric_xy[frame_idx] = fixed_xy

    raw_aligned_xy = raw_metric_xy[obs_idx_arr]
    refined_aligned_xy = refined_metric_xy[obs_idx_arr]
    raw_gps_rmse_m = rmse_m(raw_aligned_xy, gps_xy_obs)
    refined_gps_rmse_m = rmse_m(refined_aligned_xy, gps_xy_obs)
    remaining_obs_mask = np.ones(len(obs_idx_arr), dtype=bool)
    remaining_obs_mask[anchor_obs_positions] = False
    raw_gps_rmse_remaining_m = rmse_m(raw_aligned_xy[remaining_obs_mask], gps_xy_obs[remaining_obs_mask])
    refined_gps_rmse_remaining_m = rmse_m(refined_aligned_xy[remaining_obs_mask], gps_xy_obs[remaining_obs_mask])

    raw_metric_deltas = raw_metric_xy[1:] - raw_metric_xy[:-1]
    refined_metric_deltas = refined_metric_xy[1:] - refined_metric_xy[:-1]
    visual_residuals_m = refined_metric_deltas - raw_metric_deltas
    valid_visual = pair_weights > 0.0
    if np.any(valid_visual):
        visual_rmse_m = float(np.sqrt(np.mean(np.sum(visual_residuals_m[valid_visual] ** 2, axis=1))))
        mean_step_delta_m = float(np.mean(np.linalg.norm(visual_residuals_m[valid_visual], axis=1)))
        max_step_delta_m = float(np.max(np.linalg.norm(visual_residuals_m[valid_visual], axis=1)))
    else:
        visual_rmse_m = 0.0
        mean_step_delta_m = 0.0
        max_step_delta_m = 0.0

    acceptance_cfg = config["acceptance"]
    accepted = True
    reason = "accepted"
    if bool(acceptance_cfg["require_gps_improvement"]):
        improvement_m = raw_gps_rmse_remaining_m - refined_gps_rmse_remaining_m
        if improvement_m < float(acceptance_cfg["min_gps_rmse_improvement_m"]):
            accepted = False
            reason = "gps_improvement_below_threshold"
    if visual_rmse_m > float(acceptance_cfg["max_visual_residual_rmse_m"]):
        accepted = False
        reason = "visual_residual_rmse_too_high"
    if max_step_delta_m > float(acceptance_cfg["max_step_delta_m"]):
        accepted = False
        reason = "step_delta_too_high"
    if not result.success:
        accepted = False
        reason = "solver_failed"

    refined_coords2 = inverse_metric_track_xy(refined_metric_xy, similarity_scale, similarity_rot, similarity_trans)
    refined_xyz = plane_mean[None, :] + refined_coords2 @ plane_basis2.T + heights[:, None] * plane_normal[None, :]
    chosen_metric_xy = refined_metric_xy if accepted else raw_metric_xy
    chosen_coords2 = inverse_metric_track_xy(chosen_metric_xy, similarity_scale, similarity_rot, similarity_trans)
    chosen_xyz = plane_mean[None, :] + chosen_coords2 @ plane_basis2.T + heights[:, None] * plane_normal[None, :]

    refined_candidate_rows: list[dict[str, float]] = []
    selected_rows: list[dict[str, float]] = []
    with out_csv.open("w", newline="") as f:
        fieldnames = ["frame_index", "frame_number", "timestamp_ns", "x", "y", "z", "qx", "qy", "qz", "qw"]
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for idx, row in enumerate(traj_rows):
            refined_row = {
                "frame_index": row.frame_index,
                "frame_number": row.frame_number,
                "timestamp_ns": row.timestamp_ns,
                "x": float(refined_xyz[idx, 0]),
                "y": float(refined_xyz[idx, 1]),
                "z": float(refined_xyz[idx, 2]),
                "qx": row.qx,
                "qy": row.qy,
                "qz": row.qz,
                "qw": row.qw,
            }
            selected_row = {
                "frame_index": row.frame_index,
                "frame_number": row.frame_number,
                "timestamp_ns": row.timestamp_ns,
                "x": float(chosen_xyz[idx, 0]),
                "y": float(chosen_xyz[idx, 1]),
                "z": float(chosen_xyz[idx, 2]),
                "qx": row.qx,
                "qy": row.qy,
                "qz": row.qz,
                "qw": row.qw,
            }
            writer.writerow(refined_row)
            refined_candidate_rows.append(refined_row)
            selected_rows.append(selected_row)

    write_track_plot(out_dir / "pre_refinement_poses.png", plot_traj_rows, gps_rows)
    write_track_plot(out_dir / "post_refinement_poses.png", refined_candidate_rows, gps_rows)
    write_track_plot(out_dir / "track_plot.png", refined_candidate_rows, gps_rows)
    write_track_plot(out_dir / "selected_poses.png", selected_rows, gps_rows)
    anchor_frame_indices = {int(idx) for idx in anchor_frame_indices_arr}
    write_gps_score_csv(
        out_dir / "gps_pose_refinement_scores.csv",
        traj_rows,
        obs_idx_arr,
        gps_xy_obs,
        gps_accuracy_obs,
        raw_metric_xy,
        refined_metric_xy,
        chosen_metric_xy,
        anchor_frame_indices,
    )
    write_pairwise_change_csv(
        out_dir / "pairwise_pose_change_scores.csv",
        traj_rows,
        pair_motion,
        pair_weights,
        raw_metric_xy,
        refined_metric_xy,
        chosen_metric_xy,
    )
    raw_track_plot = trajectory_csv.parent / "track_plot.png"
    if not (out_dir / "track_plot.png").exists() and raw_track_plot.exists():
        shutil.copy2(raw_track_plot, out_dir / "track_plot.png")

    summary = {
        **base_summary,
        "applied": True,
        "accepted": bool(accepted),
        "reason": reason,
        "gps_match_count": len(observed_indices),
        "gps_rmse_alignment": "first_two_gps_matches_fixed",
        "anchor_frame_indices": [int(idx) for idx in anchor_frame_indices_arr],
        "anchor_frame_numbers": [int(traj_rows[int(idx)].frame_number) for idx in anchor_frame_indices_arr],
        "optimized_frame_count": int(len(variable_frame_indices)),
        "fixed_anchor_count": len(anchor_frame_indices),
        "visual_weight": visual_weight,
        "gps_visual_ratio": gps_visual_ratio,
        "effective_gps_weight": gps_weight,
        "solver_variable_count": int(jacobian.shape[1]),
        "solver_residual_count": int(jacobian.shape[0]),
        "solver_jacobian_nnz": int(jacobian.nnz),
        "similarity_scale_m_per_vo_unit": float(similarity_scale),
        "raw_gps_rmse_m": raw_gps_rmse_m,
        "refined_gps_rmse_m": refined_gps_rmse_m,
        "raw_gps_rmse_remaining_m": raw_gps_rmse_remaining_m,
        "refined_gps_rmse_remaining_m": refined_gps_rmse_remaining_m,
        "visual_residual_rmse_m": visual_rmse_m,
        "pairwise_delta_change_rmse_m": visual_rmse_m,
        "pairwise_delta_change_mean_m": mean_step_delta_m,
        "pairwise_delta_change_max_m": max_step_delta_m,
        "max_step_delta_m": max_step_delta_m,
        "gps_score_csv": str(out_dir / "gps_pose_refinement_scores.csv"),
        "pairwise_change_csv": str(out_dir / "pairwise_pose_change_scores.csv"),
        "refined_trajectory_csv": str(out_csv),
        "stored_refined_pose_source": "optimized_candidate",
        "pre_refinement_pose_plot": str(out_dir / "pre_refinement_poses.png"),
        "post_refinement_pose_plot": str(out_dir / "post_refinement_poses.png"),
        "stored_refined_pose_plot": str(out_dir / "track_plot.png"),
        "selected_pose_plot": str(out_dir / "selected_poses.png"),
        "solver_success": bool(result.success),
        "solver_status": int(result.status),
        "solver_message": str(result.message),
        "solver_cost": float(result.cost),
        "solver_nfev": int(result.nfev),
    }
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=2))
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
