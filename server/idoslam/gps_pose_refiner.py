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
    fit_similarity,
    gps_to_local_xy,
    iter_messages,
    pairwise_motion_csv_path,
    pairwise_trajectory_csv_path,
    pose_refine_output_dir,
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
    p.add_argument("--trajectory-csv", type=Path, default=None, help="Path to trajectory_pairwise_sift.csv")
    p.add_argument("--pair-motion-csv", type=Path, default=None, help="Path to pairwise_sift_motion.csv")
    p.add_argument("--output-dir", type=Path, default=None, help="Optional output directory")
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


def rmse_m(pred_xy: np.ndarray, target_xy: np.ndarray) -> float:
    return float(np.sqrt(np.mean(np.sum((pred_xy - target_xy) ** 2, axis=1))))


def weighted_pair_scale(motion: PairMotionRow | None, cfg: dict[str, object]) -> float:
    weights_cfg = cfg["weights"]
    base = float(weights_cfg["visual"])
    if motion is None:
        return 0.0
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


def build_linear_residual_model(
    frame_count: int,
    raw_deltas2: np.ndarray,
    pair_weights: np.ndarray,
    observed_indices: np.ndarray,
    gps_xy_m: np.ndarray,
    gps_accuracy_m: np.ndarray,
    similarity_scale: float,
    similarity_rot: np.ndarray,
    similarity_trans: np.ndarray,
    gps_weight: float,
    smoothness_weight: float,
) -> tuple[sparse.csr_matrix, np.ndarray]:
    row_indices: list[int] = []
    col_indices: list[int] = []
    values: list[float] = []
    targets: list[float] = []
    row = 0
    variable_count = 2 * frame_count

    for idx, delta2 in enumerate(raw_deltas2):
        pair_weight = float(pair_weights[idx])
        if pair_weight <= 0.0:
            continue
        step_scale = math.sqrt(pair_weight) * similarity_scale
        for dim in range(2):
            row_indices.extend([row, row])
            col_indices.extend([2 * idx + dim, 2 * (idx + 1) + dim])
            values.extend([-step_scale, step_scale])
            targets.append(step_scale * float(delta2[dim]))
            row += 1

    if gps_weight > 0.0 and len(observed_indices) > 0:
        gps_scale = math.sqrt(gps_weight) * similarity_scale
        for obs_pos, frame_idx in enumerate(observed_indices):
            accuracy_scale = gps_scale / float(gps_accuracy_m[obs_pos])
            coeff = accuracy_scale * similarity_rot
            offset = accuracy_scale * similarity_trans
            target_xy = accuracy_scale * gps_xy_m[obs_pos]
            base_col = 2 * int(frame_idx)
            row_indices.extend([row, row, row + 1, row + 1])
            col_indices.extend([base_col, base_col + 1, base_col, base_col + 1])
            values.extend(
                [
                    float(coeff[0, 0]),
                    float(coeff[0, 1]),
                    float(coeff[1, 0]),
                    float(coeff[1, 1]),
                ]
            )
            targets.extend(
                [
                    float(target_xy[0] - offset[0]),
                    float(target_xy[1] - offset[1]),
                ]
            )
            row += 2

    if smoothness_weight > 0.0 and frame_count >= 3:
        smooth_scale = math.sqrt(smoothness_weight) * similarity_scale
        for idx in range(frame_count - 2):
            for dim in range(2):
                row_indices.extend([row, row, row])
                col_indices.extend([2 * idx + dim, 2 * (idx + 1) + dim, 2 * (idx + 2) + dim])
                values.extend([smooth_scale, -2.0 * smooth_scale, smooth_scale])
                targets.append(0.0)
                row += 1

    if row == 0:
        return sparse.csr_matrix((0, variable_count), dtype=np.float64), np.zeros(0, dtype=np.float64)
    jacobian = sparse.csr_matrix((values, (row_indices, col_indices)), shape=(row, variable_count), dtype=np.float64)
    return jacobian, np.asarray(targets, dtype=np.float64)


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
    raw_track_plot = trajectory_csv.parent / "track_plot.png"
    if not (out_dir / "track_plot.png").exists() and raw_track_plot.exists():
        shutil.copy2(raw_track_plot, out_dir / "track_plot.png")
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=2))


def main() -> None:
    args = parse_args()
    config = _config()
    recording = args.recording.resolve()
    trajectory_csv = args.trajectory_csv.resolve() if args.trajectory_csv else default_trajectory_path(recording)
    pair_motion_csv = args.pair_motion_csv.resolve() if args.pair_motion_csv else default_pair_motion_path(recording)
    out_dir = args.output_dir.resolve() if args.output_dir else output_path(recording)
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

    raw_obs2 = raw_coords2[obs_idx_arr]
    similarity_scale, similarity_rot, similarity_trans = fit_similarity(raw_obs2, gps_xy_obs)
    if not np.isfinite(similarity_scale) or similarity_scale <= 1e-9:
        summary = {
            **base_summary,
            "applied": False,
            "accepted": False,
            "reason": "invalid_similarity_initialization",
        }
        copy_raw_outputs(trajectory_csv, out_csv, out_dir, plot_traj_rows, gps_rows, summary)
        print(json.dumps(summary, indent=2))
        return

    raw_deltas2 = raw_coords2[1:] - raw_coords2[:-1]
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

    jacobian, target = build_linear_residual_model(
        len(traj_rows),
        raw_deltas2,
        pair_weights,
        obs_idx_arr,
        gps_xy_obs,
        gps_accuracy_obs,
        float(similarity_scale),
        similarity_rot,
        similarity_trans,
        gps_weight,
        float(config["weights"]["smoothness"]),
    )

    solver_cfg = config["solver"]
    result = least_squares(
        optimizer_residuals,
        raw_coords2.reshape(-1),
        args=(jacobian, target),
        jac=lambda _x, _jacobian, _target: _jacobian,
        loss=str(solver_cfg["loss"]),
        f_scale=float(solver_cfg["f_scale"]),
        max_nfev=int(solver_cfg["max_nfev"]),
        tr_solver="lsmr",
    )
    refined_coords2 = result.x.reshape(-1, 2)

    raw_eval_scale, raw_eval_rot, raw_eval_trans = fit_similarity(raw_obs2, gps_xy_obs)
    refined_eval_scale, refined_eval_rot, refined_eval_trans = fit_similarity(refined_coords2[obs_idx_arr], gps_xy_obs)
    raw_aligned_xy = metric_track_xy(raw_obs2, raw_eval_scale, raw_eval_rot, raw_eval_trans)
    refined_aligned_xy = metric_track_xy(refined_coords2[obs_idx_arr], refined_eval_scale, refined_eval_rot, refined_eval_trans)
    raw_gps_rmse_m = rmse_m(raw_aligned_xy, gps_xy_obs)
    refined_gps_rmse_m = rmse_m(refined_aligned_xy, gps_xy_obs)

    visual_residuals_m = similarity_scale * (refined_coords2[1:] - refined_coords2[:-1] - raw_deltas2)
    valid_visual = pair_weights > 0.0
    if np.any(valid_visual):
        visual_rmse_m = float(np.sqrt(np.mean(np.sum(visual_residuals_m[valid_visual] ** 2, axis=1))))
        max_step_delta_m = float(np.max(np.linalg.norm(visual_residuals_m[valid_visual], axis=1)))
    else:
        visual_rmse_m = 0.0
        max_step_delta_m = 0.0

    acceptance_cfg = config["acceptance"]
    accepted = True
    reason = "accepted"
    if bool(acceptance_cfg["require_gps_improvement"]):
        improvement_m = raw_gps_rmse_m - refined_gps_rmse_m
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

    chosen_coords2 = refined_coords2 if accepted else raw_coords2
    chosen_xyz = plane_mean[None, :] + chosen_coords2 @ plane_basis2.T + heights[:, None] * plane_normal[None, :]

    written_rows: list[dict[str, float]] = []
    with out_csv.open("w", newline="") as f:
        fieldnames = ["frame_index", "frame_number", "timestamp_ns", "x", "y", "z", "qx", "qy", "qz", "qw"]
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for idx, row in enumerate(traj_rows):
            out_row = {
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
            writer.writerow(out_row)
            written_rows.append(out_row)

    write_track_plot(out_dir / "track_plot.png", written_rows, gps_rows)
    raw_track_plot = trajectory_csv.parent / "track_plot.png"
    if not (out_dir / "track_plot.png").exists() and raw_track_plot.exists():
        shutil.copy2(raw_track_plot, out_dir / "track_plot.png")

    summary = {
        **base_summary,
        "applied": True,
        "accepted": bool(accepted),
        "reason": reason,
        "gps_match_count": len(observed_indices),
        "visual_weight": visual_weight,
        "gps_visual_ratio": gps_visual_ratio,
        "effective_gps_weight": gps_weight,
        "solver_variable_count": int(jacobian.shape[1]),
        "solver_residual_count": int(jacobian.shape[0]),
        "solver_jacobian_nnz": int(jacobian.nnz),
        "similarity_scale_m_per_vo_unit": float(similarity_scale),
        "raw_gps_rmse_m": raw_gps_rmse_m,
        "refined_gps_rmse_m": refined_gps_rmse_m,
        "visual_residual_rmse_m": visual_rmse_m,
        "max_step_delta_m": max_step_delta_m,
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
