"""Pongtown CLI: stages 1→2→3 with debug overlays.

Usage:
    uv run python pongtown/main.py ../recordings/<name>/<name>.vis.pb
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

from pongtown.iou_scorer import IoUScorer, summarise
from pongtown.loader import iter_bundles
from pongtown.pose import PoseResult, canonical_corners_mm, solve_net_pose, solve_table_pose
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


log = logging.getLogger("pongtown")


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Ping-pong table pose pipeline")
    p.add_argument("vis_path", type=Path)
    p.add_argument("--max-frames", type=int, default=0)
    p.add_argument("--sample-every", type=int, default=1)
    p.add_argument("--stop-after", type=int, choices=[1, 2, 3], default=3)
    p.add_argument("--no-debug", action="store_true")
    return p.parse_args()


def _seg_path_for(vis: Path) -> Path:
    stem = vis.name.removesuffix(".vis.pb")
    return vis.parent / f"{stem}.seg.pb"


def _debug_dir(vis: Path) -> Path:
    stem = vis.name.removesuffix(".vis.pb")
    return vis.parent / f"{stem}.pongtown.debug"


def _pongtown_pb_path(vis: Path) -> Path:
    stem = vis.name.removesuffix(".vis.pb")
    return vis.parent / f"{stem}.pongtown.pb"


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
    cfg = yaml.safe_load(open(cfg_path))

    vis_path = args.vis_path.resolve()
    seg_path = _seg_path_for(vis_path)
    if not vis_path.is_file() or not seg_path.is_file():
        raise FileNotFoundError(f"Missing inputs: {vis_path} or {seg_path}")

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
        qres = fit_table_quadrilateral(b.table_mask, b.net_mask, cfg=cfg)
        pres = PoseResult(None, None, None, 0.0, False)
        net_quad_img = fit_net_quadrilateral(b.net_mask)
        nres = PoseResult(None, None, None, 0.0, False)
        if args.stop_after >= 2:
            if qres.method != METHOD_QUAD_FAILED:
                pres = solve_table_pose(
                    qres.quad_img, qres.midline_img, b.intrinsics,
                    cfg=cfg,
                    table_mask=b.table_mask, net_mask=b.net_mask,
                    person_mask=b.person_mask,
                    image_shape=b.rgb.shape[:2],
                    T_camera_to_world=b.T_camera_to_world,
                    world_up_axis=1,
                    half_rectangle=False,
                )
            if net_quad_img is not None:
                nres = solve_net_pose(
                    net_quad_img, b.intrinsics,
                    cfg=cfg,
                    T_camera_to_world=b.T_camera_to_world,
                    world_up_axis=1,
                    net_mask=b.net_mask,
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
                "net": b.net_mask,
                "person": b.person_mask,
            }
            panels = [render_stage1_panel(
                b.rgb, b.table_mask, b.net_mask, b.person_mask,
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

    # ── Stage 3: weighted RANSAC over per-frame poses lifted to world ────────
    # Select top 10% of valid frames by Stage 2 PnP IoU.  Using pnp_iou as
    # the filter (not Stage 1 quality) ensures RANSAC sees only frames where
    # the per-frame pose is actually good, not just frames where the quad fit
    # was good.
    top_pct = float(cfg.get("world", {}).get("ransac_top_pct", 0.10))
    iou_thresh = float(np.percentile([r["pres"].pnp_iou for r in valid], 100 * (1 - top_pct)))
    eligible = [r for r in valid if r["pres"].pnp_iou >= iou_thresh]
    if not eligible:
        log.warning("No frames in top %.0f%% IoU; falling back to all valid", top_pct * 100)
        eligible = valid
    log.info(
        "Stage 3 candidates: %d / %d frames in top %.0f%% by PnP IoU (thresh=%.3f)",
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
    if poses_T.shape[0] == 0:
        log.warning("No valid poses for Stage 3")
        return

    T_global_world, inlier_mask = _weighted_ransac_pose(
        poses_T, weights,
        pos_inlier_thresh_m=cfg.get("world", {}).get("ransac_pos_inlier_m", 0.20),
        rot_inlier_thresh_rad=math.radians(
            cfg.get("world", {}).get("ransac_yaw_inlier_deg", 8.0)
        ),
    )

    # ── Stage 3 net: independent RANSAC over per-frame net poses ─────────────
    net_valid = [r for r in results if r["nres"].success]
    T_global_net_world: np.ndarray | None = None
    if net_valid:
        net_iou_thresh = float(
            np.percentile([r["nres"].pnp_iou for r in net_valid], 100 * (1 - top_pct))
        )
        net_eligible = [r for r in net_valid if r["nres"].pnp_iou >= net_iou_thresh]
        if not net_eligible:
            net_eligible = net_valid
        log.info(
            "Stage 3 net candidates: %d / %d frames in top %.0f%% by net IoU (thresh=%.3f)",
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
            "Stage 3 net RANSAC: inliers %d/%d, t=(%.3f, %.3f, %.3f) m",
            int(net_inlier_mask.sum()), len(net_inlier_mask),
            t_net[0] / 1000, t_net[1] / 1000, t_net[2] / 1000,
        )
    else:
        log.warning("Stage 3 net: no valid net poses, net rectangle will use table pose fallback")
    t = T_global_world[:3, 3]
    log.info(
        "Stage 3 RANSAC: inliers %d/%d, t=(%.3f, %.3f, %.3f) m",
        int(inlier_mask.sum()), len(inlier_mask),
        t[0] / 1000, t[1] / 1000, t[2] / 1000,
    )

    # Reproject the global pose into every frame, score IoU, optionally render.
    scorer = IoUScorer()
    P_corners_mm = canonical_corners_mm(
        cfg["table"]["width_mm"], cfg["table"]["height_mm"]
    )
    stage1_scores = []
    stage2_scores = []
    stage3_scores = []
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
        if r["pres"].success:
            proj_c2, _ = cv2.projectPoints(
                P_corners_mm, r["pres"].rvec, r["pres"].tvec, b.intrinsics, None
            )
            quad_pnp = proj_c2.reshape(-1, 2)
        else:
            quad_pnp = None
        s2 = scorer.score(
            quad_img=quad_pnp,
            image_shape=b.rgb.shape[:2],
            table_top_mask=b.table_top_mask, net_mask=b.net_mask,
            person_mask=b.person_mask, bat_mask=b.bat_mask,
        )
        stage2_scores.append(s2)
        # Stage 3: reproject canonical rect from the global pose.
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
            net_mask=b.net_mask,
            person_mask=b.person_mask,
            bat_mask=b.bat_mask,
        )
        stage3_scores.append(score)

        if not (debug and odir is not None and (b.frame_idx % every) == 0):
            continue
        mask_overlay = {
            "legs": b.table_legs_mask,
            "table": b.table_mask,
            "net": b.net_mask,
            "person": b.person_mask,
        }
        # Global net pose projected back to this frame's camera.
        net_rv_s3 = net_tv_s3 = None
        if T_global_net_world is not None:
            T_net_cam = T_world_to_camera @ T_global_net_world
            net_rv_s3, _ = cv2.Rodrigues(T_net_cam[:3, :3])
            net_tv_s3 = T_net_cam[:3, 3]
        stage3 = render_pose_panel(
            b.rgb, rv, tv, b.intrinsics, score.iou,
            f"f{b.frame_idx} stage3 global",
            mask_overlays=mask_overlay,
            quad_thickness=3,
            net_rvec=net_rv_s3, net_tvec=net_tv_s3,
        )
        stage1 = render_stage1_panel(
            b.rgb, b.table_mask, b.net_mask, b.person_mask,
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
        )
        cv2.imwrite(
            str(odir / f"frame_{b.frame_idx:06d}.png"),
            montage([stage1, stage2, stage3]),
        )

    # ── Final stats ──────────────────────────────────────────────────────────
    for label, scores in (
        ("Stage 1", stage1_scores),
        ("Stage 2", stage2_scores),
        ("Stage 3", stage3_scores),
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


if __name__ == "__main__":
    main()
