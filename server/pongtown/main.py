"""Pongtown CLI: stages 1→2→3 with debug overlays.

Usage:
    uv run python pongtown/main.py ../recordings/<name>/<name>.vis.pb
    uv run python pongtown/main.py ../recordings/<name>/<name>.vis.pb --mode pingpong
    uv run python pongtown/main.py ../recordings/<name>/<name>.vis.pb --mode snooker
    uv run python pongtown/main.py ../recordings/<name>/<name>.vis.pb --stop-after 1
"""
from __future__ import annotations

import argparse
import logging
import math
import sys
from pathlib import Path

import cv2
import numpy as np
import yaml

_server_root = Path(__file__).resolve().parent.parent
_project_root = _server_root.parent
sys.path.insert(0, str(_project_root))
sys.path.insert(0, str(_project_root / "proto"))
sys.path.insert(0, str(_server_root))

from proto import pongtown_pb2
from pongtown.iou_scorer import IoUScorer, summarise
from pongtown.loader import iter_bundles
from pongtown.pose import (
    PoseResult,
    canonical_corners_mm,
    canonical_net_local_mm,
    canonical_net_strip_mm,
    solve_net_pose,
    solve_table_pose,
)
from pongtown.quad_fit import (
    METHOD_QUAD_FAILED,
    fit_net_quadrilateral,
    fit_table_quadrilateral,
)
from pongtown.render import (
    montage,
    render_pose_panel,
    render_stage1_panel,
)
from pongtown.trajectory import (
    BallTrackPoint,
    SnookerBallObservation,
    extract_ball_trajectory,
    extract_snooker_ball_positions,
)
from streamlog.protoio import ProtoIO


log = logging.getLogger("pongtown")
_pong_io = ProtoIO(pongtown_pb2.PongtownResponse)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Table pose pipeline for ping-pong/snooker recordings")
    p.add_argument("vis_path", type=Path)
    p.add_argument("--mode", choices=["pingpong", "snooker"], default="pingpong")
    p.add_argument("--seg-path", type=Path, default=None)
    p.add_argument("--max-frames", type=int, default=0)
    p.add_argument("--sample-every", type=int, default=1)
    p.add_argument("--stop-after", type=int, choices=[1, 2, 3], default=3)
    p.add_argument("--no-debug", action="store_true")
    return p.parse_args()


def _deep_merge(base: dict, override: dict) -> dict:
    out = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = value
    return out


def _config_for_mode(cfg: dict, mode: str) -> dict:
    modes = cfg.get("modes", {})
    if mode not in modes:
        raise ValueError(f"Unknown pongtown mode {mode!r}; configured modes: {sorted(modes)}")
    base = {k: v for k, v in cfg.items() if k != "modes"}
    merged = _deep_merge(base, modes.get(mode, {}))
    merged["mode"] = mode
    return merged


def _sport_mode_enum(mode: str) -> int:
    if mode == "pingpong":
        return pongtown_pb2.PongtownResponse.PINGPONG
    if mode == "snooker":
        return pongtown_pb2.PongtownResponse.SNOOKER
    return pongtown_pb2.PongtownResponse.SPORT_MODE_UNKNOWN


def _seg_path_for(vis: Path, explicit: Path | None = None) -> Path:
    if explicit is not None:
        return explicit
    stem = vis.name.removesuffix(".vis.pb")
    candidates = [
        vis.parent / f"{stem}.seg.pb",
        vis.parent / f"{stem}.segmentation.pb",
    ]
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return candidates[0]


def _debug_dir(vis: Path) -> Path:
    stem = vis.name.removesuffix(".vis.pb")
    return vis.parent / f"{stem}.pongtown.debug"


def _pongtown_pb_path(vis: Path) -> Path:
    stem = vis.name.removesuffix(".vis.pb")
    return vis.parent / f"{stem}.pongtown.pb"


def _flatten(arr: np.ndarray | None) -> list[float]:
    if arr is None:
        return []
    return [float(v) for v in np.asarray(arr).reshape(-1)]


def _extend_floats(field, arr: np.ndarray | None) -> None:
    vals = _flatten(arr)
    if vals:
        field.extend(vals)


def _copy_frame_identifier(resp: pongtown_pb2.PongtownResponse, bundle) -> None:
    if bundle.raw_frame is not None:
        resp.frame_identifier.CopyFrom(bundle.raw_frame.frame_identifier)
    resp.frame_identifier.frame_number = int(bundle.frame_number)
    if resp.frame_identifier.timestamp_ns == 0:
        resp.frame_identifier.timestamp_ns = int(bundle.timestamp_ns)


def _fill_intrinsics(msg, K: np.ndarray, image_shape: tuple[int, int]) -> None:
    h, w = image_shape
    msg.fx = float(K[0, 0])
    msg.fy = float(K[1, 1])
    msg.cx = float(K[0, 2])
    msg.cy = float(K[1, 2])
    msg.image_width = float(w)
    msg.image_height = float(h)


def _side_enum(side: str, inside_table: bool) -> int:
    if not inside_table and side:
        return pongtown_pb2.PongtownResponse.OFF_TABLE
    if side == "near":
        return pongtown_pb2.PongtownResponse.NEAR
    if side == "far":
        return pongtown_pb2.PongtownResponse.FAR
    if side == "net":
        return pongtown_pb2.PongtownResponse.NET
    return pongtown_pb2.PongtownResponse.SIDE_UNKNOWN


def _populate_ball_position_proto(
    msg: pongtown_pb2.PongtownResponse.BallPosition,
    obs: BallTrackPoint,
) -> None:
    msg.track_id = 1
    msg.observation_idx = int(obs.observation_idx)
    msg.frame_idx = int(obs.frame_idx)
    msg.frame_number = int(obs.frame_number)
    msg.timestamp_ns = int(obs.timestamp_ns)
    msg.u_img = float(obs.u)
    msg.v_img = float(obs.v)
    msg.area_px = int(obs.area_px)
    msg.confidence = float(obs.confidence)
    msg.interpolated = False
    msg.has_table_position = bool(obs.has_table_position)
    _extend_floats(msg.cam_xyz_mm, obs.cam_xyz_mm)
    _extend_floats(msg.table_xyz_mm, obs.table_xyz_mm)
    msg.side = _side_enum(obs.side, obs.inside_table)
    msg.inside_table = bool(obs.inside_table)


def _populate_snooker_ball_position_proto(
    msg: pongtown_pb2.PongtownResponse.SnookerBallPosition,
    obs: SnookerBallObservation,
) -> None:
    msg.object_id = int(obs.object_id)
    msg.label = str(obs.label)
    msg.frame_idx = int(obs.frame_idx)
    msg.frame_number = int(obs.frame_number)
    msg.timestamp_ns = int(obs.timestamp_ns)
    msg.u_img = float(obs.u)
    msg.v_img = float(obs.v)
    msg.area_px = int(obs.area_px)
    msg.confidence = float(obs.confidence)
    msg.has_table_position = bool(obs.has_table_position)
    _extend_floats(msg.cam_xyz_mm, obs.cam_xyz_mm)
    _extend_floats(msg.table_xyz_mm, obs.table_xyz_mm)
    msg.inside_table = bool(obs.inside_table)


def _populate_ball_trajectory_proto(
    msg: pongtown_pb2.PongtownResponse.BallTrajectory,
    trajectory: dict,
) -> None:
    positions: list[BallTrackPoint] = trajectory["positions"]
    msg.track_id = 1
    msg.observed_frames = len(positions)
    msg.min_bounce_prominence_px = float(trajectory["min_prominence_px"])
    msg.min_bounce_spacing_frames = int(trajectory["min_spacing_frames"])
    msg.smooth_sigma = float(trajectory["smooth_sigma"])
    if positions:
        msg.first_frame_idx = int(positions[0].frame_idx)
        msg.last_frame_idx = int(positions[-1].frame_idx)
        msg.first_timestamp_ns = int(positions[0].timestamp_ns)
        msg.last_timestamp_ns = int(positions[-1].timestamp_ns)
    for obs in positions:
        _populate_ball_position_proto(msg.positions.add(), obs)
    for seg in trajectory["segments"]:
        out = msg.segments.add()
        out.start_observation_idx = int(seg.start_observation_idx)
        out.end_observation_idx = int(seg.end_observation_idx)
        out.start_frame_idx = int(seg.start_frame_idx)
        out.end_frame_idx = int(seg.end_frame_idx)
        out.start_timestamp_ns = int(seg.start_timestamp_ns)
        out.end_timestamp_ns = int(seg.end_timestamp_ns)
        out.dt_s = float(seg.dt_s)
        out.du_img = float(seg.du_img)
        out.dv_img = float(seg.dv_img)
        out.image_distance_px = float(seg.image_distance_px)
        out.image_speed_px_s = float(seg.image_speed_px_s)
        out.has_table_displacement = bool(seg.has_table_displacement)
        _extend_floats(out.table_delta_mm, seg.table_delta_mm)
        out.table_distance_mm = float(seg.table_distance_mm)
        out.table_speed_mm_s = float(seg.table_speed_mm_s)
    for bnc in trajectory["bounces"]:
        out = msg.bounces.add()
        out.bounce_idx = int(bnc.bounce_idx)
        out.observation_idx = int(bnc.observation_idx)
        out.frame_idx = int(bnc.frame_idx)
        out.frame_number = int(bnc.frame_number)
        out.timestamp_ns = int(bnc.timestamp_ns)
        out.u_img = float(bnc.u)
        out.v_img = float(bnc.v)
        out.prominence_px = float(bnc.prominence_px)
        out.confidence = float(bnc.confidence)
        out.has_table_position = bool(bnc.has_table_position)
        _extend_floats(out.cam_xyz_mm, bnc.cam_xyz_mm)
        _extend_floats(out.table_xyz_mm, bnc.table_xyz_mm)
        out.side = _side_enum(bnc.side, bnc.inside_table)
        out.inside_table = bool(bnc.inside_table)


def _project_points(
    points_mm: np.ndarray,
    rvec: np.ndarray | None,
    tvec: np.ndarray | None,
    K: np.ndarray,
) -> np.ndarray | None:
    if rvec is None or tvec is None:
        return None
    proj, _ = cv2.projectPoints(points_mm, rvec, tvec, K, None)
    return proj.reshape(-1, 2)


def _project_pose_quad(
    result: PoseResult,
    points_mm: np.ndarray,
    K: np.ndarray,
) -> np.ndarray | None:
    if not result.success:
        return None
    return _project_points(points_mm, result.rvec, result.tvec, K)


def _net_points_local(cfg: dict) -> np.ndarray:
    t_cfg = cfg["table"]
    width = float(t_cfg["height_mm"]) + 2.0 * float(t_cfg["net_overhang_mm"])
    return canonical_net_local_mm(width, float(t_cfg["net_height_mm"]))


def _net_points_table(cfg: dict) -> np.ndarray:
    t_cfg = cfg["table"]
    width = float(t_cfg["height_mm"]) + 2.0 * float(t_cfg["net_overhang_mm"])
    return canonical_net_strip_mm(width, float(t_cfg["net_height_mm"]))


def _net_enabled(cfg: dict) -> bool:
    return bool(cfg.get("table", {}).get("net_enabled", True))


def _render_geom_kwargs(cfg: dict) -> dict:
    t_cfg = cfg["table"]
    return {
        "table_width_mm": float(t_cfg["width_mm"]),
        "table_height_mm": float(t_cfg["height_mm"]),
        "draw_net": _net_enabled(cfg),
        "net_width_mm": float(t_cfg["height_mm"]) + 2.0 * float(t_cfg["net_overhang_mm"]),
        "net_height_mm": float(t_cfg["net_height_mm"]),
    }


def _stage2_table_quad(result: dict, cfg: dict) -> np.ndarray | None:
    cached = result.get("quad_pnp")
    if cached is not None:
        return cached
    b = result["bundle"]
    P = canonical_corners_mm(cfg["table"]["width_mm"], cfg["table"]["height_mm"])
    return _project_pose_quad(result["pres"], P, b.intrinsics)


def _stage2_net_quad(result: dict, cfg: dict) -> np.ndarray | None:
    if not _net_enabled(cfg):
        return None
    cached = result.get("quad_net_pnp")
    if cached is not None:
        return cached
    b = result["bundle"]
    return _project_pose_quad(result["nres"], _net_points_local(cfg), b.intrinsics)


def _stage2_overlay_net_quad(result: dict, cfg: dict) -> np.ndarray | None:
    if not _net_enabled(cfg):
        return None
    cached = result.get("quad_net_overlay_pnp")
    if cached is not None:
        return cached
    b = result["bundle"]
    net_quad = _stage2_net_quad(result, cfg)
    if net_quad is not None:
        return net_quad
    return _project_pose_quad(result["pres"], _net_points_table(cfg), b.intrinsics)


def _is_off_screen(quad: np.ndarray | None, image_shape: tuple[int, int]) -> bool:
    if quad is None:
        return False
    h, w = image_shape
    return bool(
        (quad[:, 0] < 0).all()
        or (quad[:, 0] >= w).all()
        or (quad[:, 1] < 0).all()
        or (quad[:, 1] >= h).all()
    )


def _populate_pnp_debug(resp: pongtown_pb2.PongtownResponse, result: dict, cfg: dict) -> None:
    b = result["bundle"]
    qres = result["qres"]
    pres = result["pres"]
    nres = result["nres"]

    dbg = resp.pnp_frame_debug.add()
    dbg.frame_idx = int(b.frame_idx)
    _fill_intrinsics(dbg.camera_intrinsics, b.intrinsics, b.rgb.shape[:2])
    _extend_floats(dbg.camera_matrix, b.intrinsics)

    dbg.image_plane_method = int(qres.method)
    dbg.image_plane_quad_quality = float(qres.quality)
    _extend_floats(dbg.image_plane_table_quad_img, qres.quad_img)
    _extend_floats(dbg.image_plane_half_table_quad_img, qres.half_quad_img)
    _extend_floats(dbg.image_plane_midline_img, qres.midline_img)
    _extend_floats(dbg.image_plane_net_quad_img, result["net_quad_img"])

    dbg.pnp_table_success = bool(pres.success)
    dbg.pnp_table_iou = float(pres.pnp_iou)
    _extend_floats(dbg.pnp_table_quad_img, _stage2_table_quad(result, cfg))
    _extend_floats(dbg.pnp_T_table_to_camera, pres.T_table_to_camera)

    dbg.pnp_net_success = bool(nres.success)
    dbg.pnp_net_iou = float(nres.pnp_iou)
    _extend_floats(dbg.pnp_net_quad_img, _stage2_net_quad(result, cfg))
    _extend_floats(dbg.pnp_T_net_to_camera, nres.T_table_to_camera)
    _extend_floats(dbg.pnp_overlay_net_quad_img, _stage2_overlay_net_quad(result, cfg))


def _write_pongtown_pb(
    out_path: Path,
    results: list[dict],
    cfg: dict,
    *,
    T_global_world: np.ndarray | None,
    T_global_net_world: np.ndarray | None,
    frames_used: int,
    pingpong_trajectory: dict | None,
    snooker_tracking: dict | None,
    refined_cost: float = 0.0,
) -> None:
    if out_path.exists():
        out_path.unlink()

    sport_mode = _sport_mode_enum(cfg.get("mode", ""))

    batch: list[pongtown_pb2.PongtownResponse] = []
    for result in results:
        b = result["bundle"]
        qres = result["qres"]
        pres = result["pres"]
        rec = pongtown_pb2.PongtownResponse()
        rec.sport_mode = sport_mode
        _copy_frame_identifier(rec, b)

        tp = rec.table_pose
        final_off_screen = bool(result.get("final_off_screen", False))
        tp.method = (
            pongtown_pb2.PongtownResponse.TablePose.OFF_SCREEN
            if final_off_screen
            else int(qres.method)
        )
        tp.quad_quality = float(qres.quality)
        tp.pnp_iou = float(pres.pnp_iou)
        _extend_floats(tp.quad_img, qres.quad_img)
        _extend_floats(tp.midline_img, qres.midline_img)
        _extend_floats(tp.T_table_to_camera, pres.T_table_to_camera)
        _extend_floats(tp.quad_img_global, result.get("quad_global"))
        if "final_score" in result:
            tp.global_iou = float(result["final_score"].iou)

        _populate_pnp_debug(rec, result, cfg)

        out = rec.frame_output
        out.frame_idx = int(b.frame_idx)
        out.has_pose = result.get("T_final_table_to_camera") is not None
        out.off_screen = final_off_screen
        if "final_score" in result:
            out.global_iou = float(result["final_score"].iou)
        _extend_floats(out.T_table_to_camera, result.get("T_final_table_to_camera"))
        _extend_floats(out.table_quad_img, result.get("quad_global"))
        _extend_floats(out.net_quad_img, result.get("quad_net_global"))
        out.has_net_pose = result.get("T_final_net_to_camera") is not None
        _extend_floats(out.T_net_to_camera, result.get("T_final_net_to_camera"))

        if cfg.get("mode") == "pingpong":
            tracking = rec.pingpong_tracking
            tracking.SetInParent()
            for obs in result.get("ball_positions", []):
                _populate_ball_position_proto(tracking.ball_positions.add(), obs)
                # Deprecated compatibility field for older dashboard builds.
                _populate_ball_position_proto(rec.ball_positions.add(), obs)
        elif cfg.get("mode") == "snooker":
            tracking = rec.snooker_tracking
            tracking.SetInParent()
            for obs in result.get("snooker_ball_positions", []):
                _populate_snooker_ball_position_proto(tracking.ball_positions.add(), obs)

        batch.append(rec)
        if len(batch) >= 50:
            _pong_io.write_file(out_path, batch)
            batch.clear()

    if batch:
        _pong_io.write_file(out_path, batch)

    summary = pongtown_pb2.PongtownResponse()
    summary.sport_mode = sport_mode
    summary.global_table_pose.has_pose = T_global_world is not None
    _extend_floats(summary.global_table_pose.T_table_to_world, T_global_world)
    summary.global_table_pose.refined_cost = float(refined_cost)
    summary.global_table_pose.frames_used = int(frames_used)

    ious = np.array(
        [
            float(r["final_score"].iou)
            for r in results
            if "final_score" in r and r["final_score"].target_pixels > 0
        ],
        dtype=np.float64,
    )
    if ious.size:
        summary.global_table_pose.mean_iou = float(ious.mean())
        summary.global_table_pose.p10_iou = float(np.percentile(ious, 10))
        summary.global_table_pose.p90_iou = float(np.percentile(ious, 90))
    summary.global_table_pose.has_net_pose = T_global_net_world is not None
    _extend_floats(summary.global_table_pose.T_net_to_world, T_global_net_world)

    summary.table_width_mm = float(cfg["table"]["width_mm"])
    summary.table_height_mm = float(cfg["table"]["height_mm"])
    summary.net_overhang_mm = float(cfg["table"]["net_overhang_mm"])
    summary.net_height_mm = float(cfg["table"]["net_height_mm"])
    if cfg.get("mode") == "pingpong" and pingpong_trajectory is not None:
        summary.pingpong_tracking.SetInParent()
        _populate_ball_trajectory_proto(
            summary.pingpong_tracking.ball_trajectory,
            pingpong_trajectory,
        )
        # Deprecated compatibility field for older dashboard builds.
        _populate_ball_trajectory_proto(summary.ball_trajectory, pingpong_trajectory)
    elif cfg.get("mode") == "snooker" and snooker_tracking is not None:
        summary.snooker_tracking.SetInParent()
        summary.snooker_tracking.observed_frames = int(snooker_tracking["observed_frames"])
        summary.snooker_tracking.total_observations = int(snooker_tracking["total_observations"])
    _pong_io.write_file(out_path, [summary])


def _lift_to_world(T_table_to_camera: np.ndarray, T_camera_to_world: np.ndarray) -> np.ndarray:
    return T_camera_to_world @ T_table_to_camera


def _decompose_xy_yaw_height(T: np.ndarray, world_up_axis: int) -> np.ndarray:
    """Project SE(3) onto (x, y, z, yaw) where yaw is rotation about world_up.
    Returns 4-vector (x, y, z, yaw)."""
    t = T[:3, 3]
    R = T[:3, :3]
    if world_up_axis == 1:  # Y up (ARCore default)
        x_axis = R[:, 0]  # table-local +X in world
        yaw = math.atan2(x_axis[2], x_axis[0])
    else:
        x_axis = R[:, 0]
        yaw = math.atan2(x_axis[1], x_axis[0])
    return np.array([float(t[0]), float(t[1]), float(t[2]), float(yaw)])


def _compose_xy_yaw_height(p: np.ndarray, world_up_axis: int) -> np.ndarray:
    """Inverse of _decompose. Table-local Z (table normal) maps to world up."""
    x, y_up, z, yaw = float(p[0]), float(p[1]), float(p[2]), float(p[3])
    c, s = math.cos(yaw), math.sin(yaw)
    if world_up_axis == 1:
        # table x → (cos yaw, 0, sin yaw)
        # table y → (-sin yaw, 0, cos yaw)
        # table z (up) → (0, 1, 0)
        R = np.array(
            [
                [c, -s, 0.0],
                [0.0, 0.0, 1.0],
                [s,  c,  0.0],
            ]
        )
    else:
        R = np.array(
            [
                [c, -s, 0.0],
                [s,  c, 0.0],
                [0.0, 0.0, 1.0],
            ]
        )
    T = np.eye(4)
    T[:3, :3] = R
    T[:3, 3] = [x, y_up, z]
    return T


def _weighted_geometric_median(points: np.ndarray, weights: np.ndarray, n_iters: int = 64) -> np.ndarray:
    pts = np.asarray(points, dtype=np.float64)
    w = np.asarray(weights, dtype=np.float64)
    x = (pts * w[:, None]).sum(axis=0) / w.sum()
    eps = 1e-6
    for _ in range(n_iters):
        d = np.linalg.norm(pts - x, axis=1)
        d = np.where(d < eps, eps, d)
        w_eff = w / d
        x_new = (pts * w_eff[:, None]).sum(axis=0) / w_eff.sum()
        if np.linalg.norm(x_new - x) < eps:
            return x_new
        x = x_new
    return x


def _weighted_circular_median_mod_pi(angles: np.ndarray, weights: np.ndarray) -> float:
    z = np.exp(2j * angles)
    avg = (z * weights).sum() / weights.sum()
    a = math.atan2(avg.imag, avg.real) / 2.0
    if a < 0:
        a += math.pi
    return a


def _so3_average(rotations: np.ndarray, weights: np.ndarray) -> np.ndarray:
    """Weighted Frobenius-norm-minimising mean rotation (Procrustes).

    rotations: (N, 3, 3); weights: (N,). Returns (3, 3).
    """
    M = (weights[:, None, None] * rotations).sum(axis=0)
    U, _, Vt = np.linalg.svd(M)
    R = U @ Vt
    if np.linalg.det(R) < 0:
        # Reflection — flip last col of U.
        U[:, -1] *= -1
        R = U @ Vt
    return R


def _weighted_ransac_pose(
    poses_T: np.ndarray,                 # (N, 4, 4) per-frame T_table_to_world
    weights: np.ndarray,
    *,
    pos_inlier_thresh_m: float = 0.20,
    rot_inlier_thresh_rad: float = math.radians(8.0),
    n_iter: int = 200,
    rng: np.random.Generator | None = None,
) -> tuple[np.ndarray, np.ndarray]:
    """RANSAC over per-frame T_table_to_world poses, weighted by quad-fit
    quality + PnP IoU. Returns (best_T, inlier_mask).

    Each iteration picks one pose (sampled with probability ∝ weight) as the
    proposal, counts weighted inliers within position and rotation thresholds,
    and keeps the proposal with the highest total inlier weight. Final pose
    is the weighted geometric median translation + Procrustes mean rotation
    of the inlier set.
    """
    if rng is None:
        rng = np.random.default_rng(0)
    n = poses_T.shape[0]
    if n == 0:
        return np.eye(4), np.zeros(0, dtype=bool)
    probs = weights / weights.sum() if weights.sum() > 0 else np.full(n, 1.0 / n)
    pos_thresh_mm = pos_inlier_thresh_m * 1000.0
    cos_rot_thresh = math.cos(rot_inlier_thresh_rad)
    Rs = poses_T[:, :3, :3]
    ts = poses_T[:, :3, 3]

    best_score = -1.0
    best_inliers = np.zeros(n, dtype=bool)
    for _ in range(min(n_iter, 5 * n)):
        i = int(rng.choice(n, p=probs))
        cand_R = Rs[i]
        cand_t = ts[i]
        # Position distance.
        dpos = np.linalg.norm(ts - cand_t, axis=1)
        # Rotation distance: trace((R_cand^T R_i) - 1) / 2 = cos(angle).
        traces = np.einsum("ij,kij->k", cand_R, Rs)
        cos_ang = (traces - 1.0) / 2.0
        ok = (dpos < pos_thresh_mm) & (cos_ang > cos_rot_thresh)
        score = float(weights[ok].sum())
        if score > best_score:
            best_score = score
            best_inliers = ok

    if best_inliers.sum() == 0:
        return poses_T[int(np.argmax(weights))], np.ones(n, dtype=bool)

    in_T = poses_T[best_inliers]
    in_w = weights[best_inliers]
    in_w_norm = in_w / in_w.sum()
    t_med = _weighted_geometric_median(in_T[:, :3, 3], in_w)
    R_avg = _so3_average(in_T[:, :3, :3], in_w_norm)

    out = np.eye(4)
    out[:3, :3] = R_avg
    out[:3, 3] = t_med
    return out, best_inliers


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    args = parse_args()

    cfg_path = Path(__file__).resolve().parent / "config.yaml"
    cfg = _config_for_mode(yaml.safe_load(open(cfg_path)), args.mode)
    net_enabled = _net_enabled(cfg)

    vis_path = args.vis_path.resolve()
    seg_path = _seg_path_for(vis_path, args.seg_path.resolve() if args.seg_path else None)
    if not vis_path.is_file() or not seg_path.is_file():
        raise FileNotFoundError(f"Missing inputs: {vis_path} or {seg_path}")
    log.info("Mode: %s; segmentation: %s", args.mode, seg_path.name)

    debug = (not args.no_debug) and cfg["pipeline"]["debug_dumps"]
    odir: Path | None = None
    if debug:
        odir = _debug_dir(vis_path) / "overlay"
        odir.mkdir(parents=True, exist_ok=True)
        log.info("Debug overlays → %s", odir)

    every = max(1, int(cfg["overlay"]["every_n_frames"]))

    # ── Stage 1 + per-frame PnP (Stage 2) ────────────────────────────────────
    results: list[dict] = []  # one entry per loaded frame

    for b in iter_bundles(
        vis_path, seg_path, cfg["mask_labels"],
        max_frames=args.max_frames, sample_every=args.sample_every,
    ):
        net_mask = b.net_mask if net_enabled else None
        qres = fit_table_quadrilateral(b.table_mask, net_mask, cfg=cfg)
        pres = PoseResult(None, None, None, 0.0, False)
        net_quad_img = fit_net_quadrilateral(net_mask) if net_enabled else None
        nres = PoseResult(None, None, None, 0.0, False)
        if args.stop_after >= 2:
            if qres.method != METHOD_QUAD_FAILED:
                pres = solve_table_pose(
                    qres.quad_img, qres.midline_img, b.intrinsics,
                    cfg=cfg,
                    table_mask=b.table_mask, net_mask=net_mask,
                    person_mask=b.person_mask,
                    image_shape=b.rgb.shape[:2],
                    T_camera_to_world=b.T_camera_to_world,
                    world_up_axis=1,
                    half_rectangle=False,
                )
            if net_enabled and net_quad_img is not None:
                nres = solve_net_pose(
                    net_quad_img, b.intrinsics,
                    cfg=cfg,
                    T_camera_to_world=b.T_camera_to_world,
                    world_up_axis=1,
                    net_mask=net_mask,
                    image_shape=b.rgb.shape[:2],
                )
        results.append({
            "bundle": b,
            "qres": qres,
            "pres": pres,
            "net_quad_img": net_quad_img,
            "nres": nres,
        })
        if debug and odir is not None and (b.frame_idx % every) == 0:
            mask_overlay = {
                "legs": b.table_legs_mask,
                "table": b.table_mask,
                "net": net_mask,
                "person": b.person_mask,
            }
            panels = [render_stage1_panel(
                b.rgb, b.table_mask, net_mask, b.person_mask,
                qres.quad_img, qres.midline_img,
                f"f{b.frame_idx} stage1 q={qres.quality:.2f}",
                legs_mask=b.table_legs_mask,
            )]
            if args.stop_after >= 2:
                panels.append(render_pose_panel(
                    b.rgb, pres.rvec, pres.tvec, b.intrinsics,
                    pres.pnp_iou,
                    f"f{b.frame_idx} stage2",
                    mask_overlays=mask_overlay,
                    net_rvec=nres.rvec, net_tvec=nres.tvec,
                    **_render_geom_kwargs(cfg),
                ))
            cv2.imwrite(str(odir / f"frame_{b.frame_idx:06d}.png"), montage(panels))

    if args.stop_after == 1:
        ok = sum(1 for r in results if r["qres"].method != METHOD_QUAD_FAILED)
        log.info("Stage 1 done: %d / %d frames with a quad", ok, len(results))
        return

    valid = [r for r in results if r["pres"].success]
    if args.stop_after == 2 or not valid:
        ious = [r["pres"].pnp_iou for r in valid]
        if ious:
            log.info(
                "Stage 2 done: %d/%d ok, mean PnP IoU %.3f, p10=%.3f, p90=%.3f",
                len(valid), len(results),
                float(np.mean(ious)),
                float(np.percentile(ious, 10)),
                float(np.percentile(ious, 90)),
            )
        else:
            log.info("Stage 2 done: 0 valid poses")
        if args.stop_after == 2:
            return

    T_global_net_world: np.ndarray | None = None
    T_global_world: np.ndarray | None = None
    inlier_mask = np.zeros(0, dtype=bool)

    if not valid:
        log.warning("No valid Stage 2 poses; writing Pongtown proto with Stage 1/2 debug only")
    else:
        # ── Final global pose: weighted RANSAC over per-frame poses ─────────
        # Select top frames by Stage 2 PnP IoU.  Using pnp_iou as the filter
        # ensures RANSAC sees only frames where the per-frame pose is good, not
        # just frames where the Stage 1 quad fit looked plausible.
        top_pct = float(cfg.get("world", {}).get("ransac_top_pct", 0.10))
        iou_thresh = float(
            np.percentile([r["pres"].pnp_iou for r in valid], 100 * (1 - top_pct))
        )
        eligible = [r for r in valid if r["pres"].pnp_iou >= iou_thresh]
        if not eligible:
            log.warning("No frames in top %.0f%% IoU; falling back to all valid", top_pct * 100)
            eligible = valid
        log.info(
            "Final pose candidates: %d / %d frames in top %.0f%% by PnP IoU (thresh=%.3f)",
            len(eligible), len(valid), top_pct * 100, iou_thresh,
        )
        poses_T = []
        weights = []
        for r in eligible:
            T_world = _lift_to_world(r["pres"].T_table_to_camera, r["bundle"].T_camera_to_world)
            poses_T.append(T_world)
            weights.append(0.5 * r["qres"].quality + 0.5 * r["pres"].pnp_iou)
        poses_T = np.stack(poses_T, axis=0)
        weights = np.array(weights)

        T_global_world, inlier_mask = _weighted_ransac_pose(
            poses_T, weights,
            pos_inlier_thresh_m=cfg.get("world", {}).get("ransac_pos_inlier_m", 0.20),
            rot_inlier_thresh_rad=math.radians(
                cfg.get("world", {}).get("ransac_yaw_inlier_deg", 8.0)
            ),
        )
        t = T_global_world[:3, 3]
        log.info(
            "Final pose RANSAC: inliers %d/%d, t=(%.3f, %.3f, %.3f) m",
            int(inlier_mask.sum()), len(inlier_mask),
            t[0] / 1000, t[1] / 1000, t[2] / 1000,
        )

        # ── Final net pose: independent RANSAC over per-frame net poses ─────
        net_valid = [r for r in results if r["nres"].success] if net_enabled else []
        if net_valid:
            net_iou_thresh = float(
                np.percentile([r["nres"].pnp_iou for r in net_valid], 100 * (1 - top_pct))
            )
            net_eligible = [r for r in net_valid if r["nres"].pnp_iou >= net_iou_thresh]
            if not net_eligible:
                net_eligible = net_valid
            log.info(
                "Final net candidates: %d / %d frames in top %.0f%% by net IoU (thresh=%.3f)",
                len(net_eligible), len(net_valid), top_pct * 100, net_iou_thresh,
            )
            net_poses_T = np.stack(
                [_lift_to_world(r["nres"].T_table_to_camera, r["bundle"].T_camera_to_world)
                 for r in net_eligible], axis=0,
            )
            net_weights = np.array([r["nres"].pnp_iou for r in net_eligible])
            net_weights = np.where(net_weights > 0, net_weights, 1.0)
            T_global_net_world, net_inlier_mask = _weighted_ransac_pose(
                net_poses_T, net_weights,
                pos_inlier_thresh_m=cfg.get("world", {}).get("ransac_pos_inlier_m", 0.20),
                rot_inlier_thresh_rad=math.radians(
                    cfg.get("world", {}).get("ransac_yaw_inlier_deg", 8.0)
                ),
            )
            t_net = T_global_net_world[:3, 3]
            log.info(
                "Final net RANSAC: inliers %d/%d, t=(%.3f, %.3f, %.3f) m",
                int(net_inlier_mask.sum()), len(net_inlier_mask),
                t_net[0] / 1000, t_net[1] / 1000, t_net[2] / 1000,
            )
        else:
            if net_enabled:
                log.warning("Final net pose: no valid net poses, net rectangle will use table pose fallback")

    # Reproject the global pose into every frame, score IoU, optionally render.
    scorer = IoUScorer()
    P_corners_mm = canonical_corners_mm(
        cfg["table"]["width_mm"], cfg["table"]["height_mm"]
    )
    stage1_scores = []
    stage2_scores = []
    final_scores = []
    for r in results:
        b = r["bundle"]
        # Stage 1: the detected/extended quad in image space.
        s1 = scorer.score(
            quad_img=r["qres"].quad_img,
            image_shape=b.rgb.shape[:2],
            table_top_mask=b.table_top_mask, net_mask=b.net_mask,
            person_mask=b.person_mask, bat_mask=b.bat_mask,
        )
        stage1_scores.append(s1)
        # Stage 2: reproject canonical rect from this frame's PnP pose.
        quad_pnp = _stage2_table_quad(r, cfg)
        r["quad_pnp"] = quad_pnp
        r["quad_net_pnp"] = _stage2_net_quad(r, cfg)
        r["quad_net_overlay_pnp"] = _stage2_overlay_net_quad(r, cfg)
        s2 = scorer.score(
            quad_img=quad_pnp,
            image_shape=b.rgb.shape[:2],
            table_top_mask=b.table_top_mask, net_mask=b.net_mask,
            person_mask=b.person_mask, bat_mask=b.bat_mask,
        )
        stage2_scores.append(s2)
        r["stage1_score"] = s1
        r["stage2_score"] = s2

        if T_global_world is None:
            score = scorer.score(
                quad_img=None,
                image_shape=b.rgb.shape[:2],
                table_top_mask=b.table_top_mask,
                net_mask=b.net_mask if net_enabled else None,
                person_mask=b.person_mask,
                bat_mask=b.bat_mask,
            )
            final_scores.append(score)
            r["final_score"] = score
            continue

        # Final output: reproject canonical rect from the global pose.
        T_camera_to_world = b.T_camera_to_world
        T_world_to_camera = np.linalg.inv(T_camera_to_world)
        T_table_to_camera = T_world_to_camera @ T_global_world
        R = T_table_to_camera[:3, :3]
        tv = T_table_to_camera[:3, 3]
        rv, _ = cv2.Rodrigues(R)
        proj_c, _ = cv2.projectPoints(P_corners_mm, rv, tv, b.intrinsics, None)
        quad_global = proj_c.reshape(-1, 2)
        score = scorer.score(
            quad_img=quad_global,
            image_shape=b.rgb.shape[:2],
            table_top_mask=b.table_top_mask,
            net_mask=b.net_mask if net_enabled else None,
            person_mask=b.person_mask,
            bat_mask=b.bat_mask,
        )
        final_scores.append(score)
        r["T_final_table_to_camera"] = T_table_to_camera
        r["quad_global"] = quad_global
        r["final_score"] = score
        r["final_off_screen"] = _is_off_screen(quad_global, b.rgb.shape[:2])

        # Global net pose projected back to this frame's camera. If the
        # independent net pose is unavailable, match the renderer fallback by
        # projecting the canonical net strip from the global table pose.
        net_rv_s3 = net_tv_s3 = None
        if net_enabled and T_global_net_world is not None:
            T_net_cam = T_world_to_camera @ T_global_net_world
            r["T_final_net_to_camera"] = T_net_cam
            net_rv_s3, _ = cv2.Rodrigues(T_net_cam[:3, :3])
            net_tv_s3 = T_net_cam[:3, 3]
            r["quad_net_global"] = _project_points(
                _net_points_local(cfg), net_rv_s3, net_tv_s3, b.intrinsics
            )
        elif net_enabled:
            r["quad_net_global"] = _project_points(
                _net_points_table(cfg), rv, tv, b.intrinsics
            )

        if not (debug and odir is not None and (b.frame_idx % every) == 0):
            continue
        mask_overlay = {
            "legs": b.table_legs_mask,
            "table": b.table_mask,
            "net": b.net_mask if net_enabled else None,
            "person": b.person_mask,
        }
        final_panel = render_pose_panel(
            b.rgb, rv, tv, b.intrinsics, score.iou,
            f"f{b.frame_idx} final global",
            mask_overlays=mask_overlay,
            quad_thickness=3,
            net_rvec=net_rv_s3, net_tvec=net_tv_s3,
            **_render_geom_kwargs(cfg),
        )
        stage1 = render_stage1_panel(
            b.rgb, b.table_mask, b.net_mask if net_enabled else None, b.person_mask,
            r["qres"].quad_img, r["qres"].midline_img,
            f"f{b.frame_idx} stage1 q={r['qres'].quality:.2f}",
            legs_mask=b.table_legs_mask,
        )
        stage2 = render_pose_panel(
            b.rgb, r["pres"].rvec, r["pres"].tvec, b.intrinsics,
            r["pres"].pnp_iou,
            f"f{b.frame_idx} stage2",
            mask_overlays=mask_overlay,
            net_rvec=r["nres"].rvec, net_tvec=r["nres"].tvec,
            **_render_geom_kwargs(cfg),
        )
        cv2.imwrite(
            str(odir / f"frame_{b.frame_idx:06d}.png"),
            montage([stage1, stage2, final_panel]),
        )

    # ── Final stats ──────────────────────────────────────────────────────────
    for label, scores in (
        ("Stage 1", stage1_scores),
        ("Stage 2", stage2_scores),
        ("Final", final_scores),
    ):
        s = summarise(scores)
        if s.get("n", 0) == 0:
            log.warning("%s: no frames with target pixels", label)
            continue
        log.info(
            "%s IoU: n=%d mean=%.3f min=%.3f max=%.3f",
            label, s["n"], s["mean"], s["min"], s["max"],
        )
        deciles = " ".join(f"p{q}={s[f'p{q}']:.3f}" for q in range(10, 100, 10))
        log.info("%s IoU deciles: %s", label, deciles)

    pingpong_trajectory = None
    snooker_tracking = None
    if cfg.get("mode") == "pingpong":
        pingpong_trajectory = extract_ball_trajectory(results, cfg)
        log.info(
            "Ping-pong trajectory: %d positions, %d segments, %d bounce candidates",
            len(pingpong_trajectory["positions"]),
            len(pingpong_trajectory["segments"]),
            len(pingpong_trajectory["bounces"]),
        )
    elif cfg.get("mode") == "snooker":
        snooker_tracking = extract_snooker_ball_positions(results, cfg)
        log.info(
            "Snooker tracking: %d observations across %d frames",
            snooker_tracking["total_observations"],
            snooker_tracking["observed_frames"],
        )

    out_pb = _pongtown_pb_path(vis_path)
    _write_pongtown_pb(
        out_pb,
        results,
        cfg,
        T_global_world=T_global_world,
        T_global_net_world=T_global_net_world,
        frames_used=int(inlier_mask.sum()),
        pingpong_trajectory=pingpong_trajectory,
        snooker_tracking=snooker_tracking,
    )
    log.info("Wrote %s (%d frames + 1 summary)", out_pb.name, len(results))


if __name__ == "__main__":
    main()
