"""Ball trajectory extraction helpers for Pongtown."""

from __future__ import annotations

import math
from dataclasses import dataclass

import cv2
import numpy as np


@dataclass
class BallTrackPoint:
    observation_idx: int
    frame_idx: int
    frame_number: int
    timestamp_ns: int
    u: float
    v: float
    area_px: int
    confidence: float
    has_table_position: bool
    cam_xyz_mm: np.ndarray | None
    table_xyz_mm: np.ndarray | None
    side: str
    inside_table: bool


@dataclass
class BallTrajectorySegment:
    start_observation_idx: int
    end_observation_idx: int
    start_frame_idx: int
    end_frame_idx: int
    start_timestamp_ns: int
    end_timestamp_ns: int
    dt_s: float
    du_img: float
    dv_img: float
    image_distance_px: float
    image_speed_px_s: float
    has_table_displacement: bool
    table_delta_mm: np.ndarray | None
    table_distance_mm: float
    table_speed_mm_s: float


@dataclass
class BallBouncePoint:
    bounce_idx: int
    observation_idx: int
    frame_idx: int
    frame_number: int
    timestamp_ns: int
    u: float
    v: float
    prominence_px: float
    confidence: float
    has_table_position: bool
    cam_xyz_mm: np.ndarray | None
    table_xyz_mm: np.ndarray | None
    side: str
    inside_table: bool


@dataclass
class SnookerBallObservation:
    object_id: int
    label: str
    frame_idx: int
    frame_number: int
    timestamp_ns: int
    u: float
    v: float
    area_px: int
    confidence: float
    has_table_position: bool
    cam_xyz_mm: np.ndarray | None
    table_xyz_mm: np.ndarray | None
    inside_table: bool


def ball_centroid(mask: np.ndarray) -> tuple[float, float, int] | None:
    if mask is None or not mask.any():
        return None
    n, _, stats, centroids = cv2.connectedComponentsWithStats(
        mask.astype(np.uint8), connectivity=8
    )
    if n <= 1:
        return None
    areas = stats[1:, cv2.CC_STAT_AREA]
    best = int(np.argmax(areas)) + 1
    area = int(stats[best, cv2.CC_STAT_AREA])
    if area < 2:
        return None
    cx, cy = centroids[best]
    return float(cx), float(cy), area


def gaussian_smooth(y: np.ndarray, sigma: float) -> np.ndarray:
    if sigma <= 0:
        return y.copy()
    radius = int(max(1, np.ceil(3 * sigma)))
    x = np.arange(-radius, radius + 1)
    k = np.exp(-0.5 * (x / sigma) ** 2)
    k /= k.sum()
    return np.convolve(y, k, mode="same")


def detect_bounces(
    obs: list[BallTrackPoint],
    *,
    min_prominence_px: float,
    min_spacing: int,
    smooth_sigma: float,
) -> list[tuple[int, float]]:
    """Return (observation index, prominence px) for image-y trajectory peaks."""
    if len(obs) < 5:
        return []
    v = np.array([o.v for o in obs], dtype=np.float64)
    frame_idx = np.array([o.frame_idx for o in obs], dtype=np.int64)
    v_smooth = gaussian_smooth(v, smooth_sigma)
    cands: list[tuple[int, float]] = []
    for i in range(2, len(obs) - 2):
        if not (v_smooth[i] > v_smooth[i - 1] and v_smooth[i] >= v_smooth[i + 1]):
            continue
        if (frame_idx[i] - frame_idx[i - 2] > 8) or (
            frame_idx[i + 2] - frame_idx[i] > 8
        ):
            continue
        lo_l = max(0, i - 6)
        lo_r = min(len(v_smooth), i + 7)
        prom = min(
            v_smooth[i] - v_smooth[lo_l:i].min(),
            v_smooth[i] - v_smooth[i + 1 : lo_r].min(),
        )
        if prom < min_prominence_px:
            continue
        if v[i] - v[max(0, i - 2)] <= 0:
            continue
        if v[min(len(v) - 1, i + 2)] - v[i] >= 0:
            continue
        cands.append((i, float(prom)))
    cands.sort(key=lambda x: x[0])
    pruned: list[tuple[int, float]] = []
    for idx, prom in cands:
        if pruned and idx - pruned[-1][0] < min_spacing:
            if prom > pruned[-1][1]:
                pruned[-1] = (idx, prom)
            continue
        pruned.append((idx, prom))
    return pruned


def ray_plane_intersect_in_camera(
    K: np.ndarray, uv: tuple[float, float], T_table_to_camera: np.ndarray
) -> np.ndarray | None:
    K_inv = np.linalg.inv(K)
    pix_h = np.array([uv[0], uv[1], 1.0], dtype=np.float64)
    d_cam = K_inv @ pix_h
    d_cam /= np.linalg.norm(d_cam)

    R_t2c = T_table_to_camera[:3, :3]
    t_t2c = T_table_to_camera[:3, 3]
    n_cam = R_t2c[:, 2]
    denom = float(n_cam @ d_cam)
    if abs(denom) < 1e-9:
        return None
    s = float(n_cam @ t_t2c) / denom
    if s <= 0:
        return None
    return s * d_cam


def to_table_local(P_cam_mm: np.ndarray, T_table_to_camera: np.ndarray) -> np.ndarray:
    T_c2t = np.linalg.inv(T_table_to_camera)
    return (T_c2t[:3, :3] @ P_cam_mm) + T_c2t[:3, 3]


def classify_side(
    P_table_mm: np.ndarray, table_w_mm: float, table_h_mm: float
) -> tuple[str, bool]:
    x, y = float(P_table_mm[0]), float(P_table_mm[1])
    inside = abs(x) <= table_w_mm / 2 + 50.0 and abs(y) <= table_h_mm / 2 + 50.0
    if x > 0:
        side = "near"
    elif x < 0:
        side = "far"
    else:
        side = "net"
    return side, bool(inside)


def ball_position_confidence(
    area_px: int,
    table_xyz_mm: np.ndarray | None,
    table_w_mm: float,
    table_h_mm: float,
) -> float:
    area_score = min(1.0, float(area_px) / 30.0)
    if table_xyz_mm is None:
        return float(area_score)
    x, y = abs(float(table_xyz_mm[0])), abs(float(table_xyz_mm[1]))
    margin_x = max(0.0, table_w_mm / 2 + 200.0 - x) / (table_w_mm / 2 + 200.0)
    margin_y = max(0.0, table_h_mm / 2 + 200.0 - y) / (table_h_mm / 2 + 200.0)
    geom_score = float(np.clip(min(margin_x, margin_y), 0.0, 1.0))
    return float(0.65 * area_score + 0.35 * geom_score)


def bounce_confidence(
    b_obs: BallTrackPoint,
    prominence: float,
    P_table_mm: np.ndarray,
    table_w_mm: float,
    table_h_mm: float,
) -> float:
    area_score = min(1.0, b_obs.area_px / 30.0)
    prom_score = min(1.0, prominence / 12.0)
    x, y = abs(P_table_mm[0]), abs(P_table_mm[1])
    margin_x = max(0.0, table_w_mm / 2 + 200.0 - x) / (table_w_mm / 2 + 200.0)
    margin_y = max(0.0, table_h_mm / 2 + 200.0 - y) / (table_h_mm / 2 + 200.0)
    geom_score = float(np.clip(min(margin_x, margin_y), 0.0, 1.0))
    return float(0.4 * area_score + 0.3 * prom_score + 0.3 * geom_score)


def trajectory_config(cfg: dict) -> tuple[float, int, float]:
    traj_cfg = cfg.get("trajectory", {})
    return (
        float(traj_cfg.get("min_bounce_prominence_px", 4.0)),
        int(traj_cfg.get("min_bounce_spacing_frames", 4)),
        float(traj_cfg.get("smooth_sigma", 1.0)),
    )


def extract_ball_trajectory(results: list[dict], cfg: dict) -> dict:
    """Extract image-space ball observations plus table-local trajectory data."""
    table_w_mm = float(cfg["table"]["width_mm"])
    table_h_mm = float(cfg["table"]["height_mm"])
    min_prominence_px, min_spacing_frames, smooth_sigma = trajectory_config(cfg)

    positions: list[BallTrackPoint] = []
    for r in results:
        r["ball_positions"] = []
        b = r["bundle"]
        cent = ball_centroid(b.ball_mask)
        if cent is None:
            continue

        u, v, area = cent
        T_t2c = r.get("T_final_table_to_camera")
        P_cam = P_table = None
        side = ""
        inside = False
        if T_t2c is not None:
            P_cam = ray_plane_intersect_in_camera(b.intrinsics, (u, v), T_t2c)
            if P_cam is not None:
                P_table = to_table_local(P_cam, T_t2c)
                side, inside = classify_side(P_table, table_w_mm, table_h_mm)

        obs = BallTrackPoint(
            observation_idx=len(positions),
            frame_idx=int(b.frame_idx),
            frame_number=int(b.frame_number),
            timestamp_ns=int(b.timestamp_ns),
            u=float(u),
            v=float(v),
            area_px=int(area),
            confidence=ball_position_confidence(area, P_table, table_w_mm, table_h_mm),
            has_table_position=P_table is not None,
            cam_xyz_mm=P_cam,
            table_xyz_mm=P_table,
            side=side,
            inside_table=bool(inside),
        )
        positions.append(obs)
        r["ball_positions"].append(obs)

    segments: list[BallTrajectorySegment] = []
    for prev, curr in zip(positions, positions[1:]):
        dt_s = max(0.0, (curr.timestamp_ns - prev.timestamp_ns) / 1_000_000_000.0)
        du = float(curr.u - prev.u)
        dv = float(curr.v - prev.v)
        image_dist = float(math.hypot(du, dv))
        image_speed = image_dist / dt_s if dt_s > 0 else 0.0
        table_delta = None
        table_dist = 0.0
        table_speed = 0.0
        if prev.table_xyz_mm is not None and curr.table_xyz_mm is not None:
            table_delta = curr.table_xyz_mm - prev.table_xyz_mm
            table_dist = float(np.linalg.norm(table_delta))
            table_speed = table_dist / dt_s if dt_s > 0 else 0.0
        segments.append(
            BallTrajectorySegment(
                start_observation_idx=prev.observation_idx,
                end_observation_idx=curr.observation_idx,
                start_frame_idx=prev.frame_idx,
                end_frame_idx=curr.frame_idx,
                start_timestamp_ns=prev.timestamp_ns,
                end_timestamp_ns=curr.timestamp_ns,
                dt_s=dt_s,
                du_img=du,
                dv_img=dv,
                image_distance_px=image_dist,
                image_speed_px_s=image_speed,
                has_table_displacement=table_delta is not None,
                table_delta_mm=table_delta,
                table_distance_mm=table_dist,
                table_speed_mm_s=table_speed,
            )
        )

    bounces: list[BallBouncePoint] = []
    for bounce_idx, (obs_idx, prom) in enumerate(
        detect_bounces(
            positions,
            min_prominence_px=min_prominence_px,
            min_spacing=min_spacing_frames,
            smooth_sigma=smooth_sigma,
        )
    ):
        obs = positions[obs_idx]
        if obs.table_xyz_mm is not None:
            conf = bounce_confidence(
                obs, prom, obs.table_xyz_mm, table_w_mm, table_h_mm
            )
        else:
            area_score = min(1.0, obs.area_px / 30.0)
            prom_score = min(1.0, prom / 12.0)
            conf = float(0.5 * area_score + 0.5 * prom_score)
        bounces.append(
            BallBouncePoint(
                bounce_idx=bounce_idx,
                observation_idx=obs.observation_idx,
                frame_idx=obs.frame_idx,
                frame_number=obs.frame_number,
                timestamp_ns=obs.timestamp_ns,
                u=obs.u,
                v=obs.v,
                prominence_px=float(prom),
                confidence=float(conf),
                has_table_position=obs.table_xyz_mm is not None,
                cam_xyz_mm=obs.cam_xyz_mm,
                table_xyz_mm=obs.table_xyz_mm,
                side=obs.side,
                inside_table=obs.inside_table,
            )
        )

    return {
        "positions": positions,
        "segments": segments,
        "bounces": bounces,
        "min_prominence_px": min_prominence_px,
        "min_spacing_frames": min_spacing_frames,
        "smooth_sigma": smooth_sigma,
    }


def extract_snooker_ball_positions(results: list[dict], cfg: dict) -> dict:
    """Extract all visible snooker ball positions for each frame."""
    table_w_mm = float(cfg["table"]["width_mm"])
    table_h_mm = float(cfg["table"]["height_mm"])

    positions: list[SnookerBallObservation] = []
    observed_frames = 0
    for r in results:
        r["snooker_ball_positions"] = []
        b = r["bundle"]
        frame_positions: list[SnookerBallObservation] = []
        for obj in getattr(b, "ball_objects", []):
            cent = ball_centroid(obj.mask)
            if cent is None:
                continue

            u, v, area = cent
            T_t2c = r.get("T_final_table_to_camera")
            P_cam = P_table = None
            inside = False
            if T_t2c is not None:
                P_cam = ray_plane_intersect_in_camera(b.intrinsics, (u, v), T_t2c)
                if P_cam is not None:
                    P_table = to_table_local(P_cam, T_t2c)
                    x, y = abs(float(P_table[0])), abs(float(P_table[1]))
                    inside = (
                        x <= table_w_mm / 2 + 50.0
                        and y <= table_h_mm / 2 + 50.0
                    )

            confidence = float(obj.confidence)
            if confidence <= 0:
                confidence = ball_position_confidence(area, P_table, table_w_mm, table_h_mm)
            obs = SnookerBallObservation(
                object_id=int(obj.object_id),
                label=str(obj.label),
                frame_idx=int(b.frame_idx),
                frame_number=int(b.frame_number),
                timestamp_ns=int(b.timestamp_ns),
                u=float(u),
                v=float(v),
                area_px=int(area),
                confidence=confidence,
                has_table_position=P_table is not None,
                cam_xyz_mm=P_cam,
                table_xyz_mm=P_table,
                inside_table=bool(inside),
            )
            frame_positions.append(obs)
            positions.append(obs)
        if frame_positions:
            observed_frames += 1
        r["snooker_ball_positions"] = frame_positions

    return {
        "positions": positions,
        "observed_frames": observed_frames,
        "total_observations": len(positions),
    }
