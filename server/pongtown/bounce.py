"""Pongtown bounce localization.

Given a recording with .vis.pb + .seg.pb + .pongtown.pb, detect frames where
the ping-pong ball trajectory reverses direction (a bounce), then ray-cast
the bounce-frame ball pixel through the table plane (in camera frame, using
the per-frame global Stage 3 pose written to .pongtown.pb) to localise the
bounce point on the table.

Usage:
    uv run python pongtown/bounce.py ../recordings/<name>/<name>.vis.pb

Output: recordings/<name>/<name>.pongtown.bounces/
    bounce_NN_frame_FFFFFF.png   # side-by-side (RGB + top-down)
    trajectory.png               # full image-space ball trajectory
    topdown_all.png              # top-down render with all bounces
    bounces.json                 # machine-readable bounce dump
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np
import yaml

_server_root = Path(__file__).resolve().parent.parent
_project_root = _server_root.parent
sys.path.insert(0, str(_project_root))
sys.path.insert(0, str(_project_root / "proto"))
sys.path.insert(0, str(_server_root))

from proto import pongtown_pb2  # noqa: E402
from pongtown.loader import iter_bundles  # noqa: E402
from pongtown.trajectory import (  # noqa: E402
    ball_centroid as _ball_centroid,
    bounce_confidence as _confidence,
    classify_side as _classify_side,
    detect_bounces as _detect_bounces,
    ray_plane_intersect_in_camera as _ray_plane_intersect_in_camera,
    to_table_local as _to_table_local,
)
from streamlog.protoio import ProtoIO  # noqa: E402


log = logging.getLogger("pongtown.bounce")
_pong_io = ProtoIO(pongtown_pb2.PongtownResponse)


@dataclass
class BallObs:
    frame_idx: int
    frame_number: int
    timestamp_ns: int
    u: float
    v: float
    area_px: int
    K: np.ndarray
    T_table_to_camera: np.ndarray  # per-frame global Stage 3 pose
    rgb: np.ndarray
    ball_mask: np.ndarray


@dataclass
class Bounce:
    obs_idx: int
    frame_idx: int
    frame_number: int
    timestamp_ns: int
    pixel_uv: tuple[float, float]
    cam_xyz_mm: np.ndarray
    table_xyz_mm: np.ndarray
    side: str           # "near", "far", "off"
    inside_table: bool
    confidence: float
    prominence_px: float


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Ping-pong ball bounce localization")
    p.add_argument("vis_path", type=Path)
    p.add_argument("--max-frames", type=int, default=0)
    p.add_argument("--sample-every", type=int, default=1)
    p.add_argument("--min-prominence-px", type=float, default=4.0)
    p.add_argument("--min-spacing-frames", type=int, default=4)
    p.add_argument("--smooth-sigma", type=float, default=1.0)
    return p.parse_args()


def _seg_path_for(vis: Path) -> Path:
    stem = vis.name.removesuffix(".vis.pb")
    return vis.parent / f"{stem}.seg.pb"


def _pongtown_pb_path(vis: Path) -> Path:
    stem = vis.name.removesuffix(".vis.pb")
    return vis.parent / f"{stem}.pongtown.pb"


def _output_dir(vis: Path) -> Path:
    stem = vis.name.removesuffix(".vis.pb")
    return vis.parent / f"{stem}.pongtown.bounces"


def _load_per_frame_pose(pongtown_pb: Path) -> dict[int, np.ndarray]:
    """Map frame_idx → 4x4 T_table_to_camera (global Stage 3, per-frame)."""
    out: dict[int, np.ndarray] = {}
    for rec in _pong_io.read_file(pongtown_pb):
        fo = rec.frame_output
        if not fo.has_pose or len(fo.T_table_to_camera) != 16:
            continue
        out[int(fo.frame_idx)] = np.array(fo.T_table_to_camera, dtype=np.float64).reshape(4, 4)
    return out


# ── Rendering ────────────────────────────────────────────────────────────────

COLOR_BALL = (0, 255, 255)        # yellow
COLOR_BOUNCE = (0, 0, 255)        # red
COLOR_TRAJ = (255, 200, 0)        # cyan-blue
COLOR_TABLE_LINE = (255, 255, 255)
COLOR_NET_LINE = (255, 0, 255)
COLOR_FAR_HALF = (40, 80, 40)
COLOR_NEAR_HALF = (60, 100, 60)
COLOR_BG = (30, 30, 30)


def _render_topdown(
    bounces: list[Bounce],
    table_w_mm: float, table_h_mm: float,
    *,
    highlight_idx: int | None = None,
    canvas_h: int = 480,
) -> np.ndarray:
    """Top-down view: table_w (long, x) horizontal; table_h (short, y) vertical.

    Camera-side half (+x) drawn on the right. Net (x=0) is the central vertical line.
    """
    margin_mm = 400.0
    total_w_mm = table_w_mm + 2 * margin_mm
    total_h_mm = table_h_mm + 2 * margin_mm
    scale = canvas_h / total_h_mm
    canvas_w = int(round(total_w_mm * scale))
    canvas = np.full((canvas_h, canvas_w, 3), COLOR_BG, dtype=np.uint8)

    def to_px(x_mm: float, y_mm: float) -> tuple[int, int]:
        # x_mm: table long axis → image x. Camera side (+x) on the right.
        # y_mm: table short axis → image y. +y_table is "up" in our render
        # (so we flip).
        cx = canvas_w / 2 + x_mm * scale
        cy = canvas_h / 2 - y_mm * scale
        return int(round(cx)), int(round(cy))

    # Halves.
    near_tl = to_px(0.0, table_h_mm / 2)
    near_br = to_px(table_w_mm / 2, -table_h_mm / 2)
    far_tl = to_px(-table_w_mm / 2, table_h_mm / 2)
    far_br = to_px(0.0, -table_h_mm / 2)
    cv2.rectangle(canvas, far_tl, far_br, COLOR_FAR_HALF, -1)
    cv2.rectangle(canvas, near_tl, near_br, COLOR_NEAR_HALF, -1)
    # Outline.
    p1 = to_px(-table_w_mm / 2, +table_h_mm / 2)
    p2 = to_px(+table_w_mm / 2, -table_h_mm / 2)
    cv2.rectangle(canvas, p1, p2, COLOR_TABLE_LINE, 2)
    # Midline (centre line, y=0).
    a = to_px(-table_w_mm / 2, 0.0)
    b = to_px(+table_w_mm / 2, 0.0)
    cv2.line(canvas, a, b, COLOR_TABLE_LINE, 1)
    # Net (x=0).
    a = to_px(0.0, +table_h_mm / 2 + 152.5)
    b = to_px(0.0, -table_h_mm / 2 - 152.5)
    cv2.line(canvas, a, b, COLOR_NET_LINE, 2)

    # "near"/"far" labels.
    cv2.putText(canvas, "near (camera side)", to_px(table_w_mm / 4, -table_h_mm / 2 - 100),
                cv2.FONT_HERSHEY_SIMPLEX, 0.4, COLOR_TABLE_LINE, 1)
    cv2.putText(canvas, "far", to_px(-table_w_mm / 2 + 80, -table_h_mm / 2 - 100),
                cv2.FONT_HERSHEY_SIMPLEX, 0.4, COLOR_TABLE_LINE, 1)

    # Bounces.
    for i, bnc in enumerate(bounces):
        x_mm, y_mm = float(bnc.table_xyz_mm[0]), float(bnc.table_xyz_mm[1])
        px = to_px(x_mm, y_mm)
        radius = max(4, int(round(6 * (0.5 + bnc.confidence))))
        is_hi = (highlight_idx is not None and i == highlight_idx)
        color = (0, 255, 255) if is_hi else COLOR_BOUNCE
        thickness = -1
        cv2.circle(canvas, px, radius + (3 if is_hi else 0), color, thickness)
        cv2.circle(canvas, px, radius + (3 if is_hi else 0), (0, 0, 0), 1)
        label = f"{i + 1}"
        cv2.putText(canvas, label, (px[0] + radius + 3, px[1] + 4),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.4, COLOR_TABLE_LINE, 1)

    return canvas


def _render_image_panel(
    obs: BallObs, bnc: Bounce, table_quad_img: np.ndarray | None,
    midline_img: np.ndarray | None,
) -> np.ndarray:
    img = obs.rgb.copy()
    # Table outline (from per-frame global pose).
    if table_quad_img is not None:
        pts = np.round(table_quad_img).astype(np.int32).reshape(-1, 1, 2)
        cv2.polylines(img, [pts], True, COLOR_TABLE_LINE, 2)
    if midline_img is not None and len(midline_img) == 2:
        p1 = tuple(np.round(midline_img[0]).astype(int))
        p2 = tuple(np.round(midline_img[1]).astype(int))
        cv2.line(img, p1, p2, COLOR_NET_LINE, 1)

    # Ball mask outline.
    if obs.ball_mask is not None and obs.ball_mask.any():
        contours, _ = cv2.findContours(
            obs.ball_mask.astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE,
        )
        cv2.drawContours(img, contours, -1, COLOR_BALL, 1)

    u, v = bnc.pixel_uv
    cv2.circle(img, (int(round(u)), int(round(v))), 6, COLOR_BOUNCE, 2)
    cv2.line(img, (int(round(u)) - 10, int(round(v))),
             (int(round(u)) + 10, int(round(v))), COLOR_BOUNCE, 1)
    cv2.line(img, (int(round(u)), int(round(v)) - 10),
             (int(round(u)), int(round(v)) + 10), COLOR_BOUNCE, 1)

    title = (
        f"frame {obs.frame_idx} | bounce {bnc.obs_idx} | "
        f"side={bnc.side} inside={bnc.inside_table} | "
        f"prom={bnc.prominence_px:.1f}px | conf={bnc.confidence:.2f}"
    )
    cv2.putText(img, title, (10, 25), cv2.FONT_HERSHEY_SIMPLEX, 0.55,
                COLOR_TABLE_LINE, 2)
    coord_lbl = (
        f"table-local (mm): x={bnc.table_xyz_mm[0]:+.0f}  "
        f"y={bnc.table_xyz_mm[1]:+.0f}  z={bnc.table_xyz_mm[2]:+.0f}"
    )
    cv2.putText(img, coord_lbl, (10, 45), cv2.FONT_HERSHEY_SIMPLEX, 0.5,
                COLOR_TABLE_LINE, 1)
    return img


def _project_table_quad(K: np.ndarray, T_table_to_camera: np.ndarray,
                        table_w_mm: float, table_h_mm: float) -> np.ndarray:
    P = np.array(
        [
            [-table_w_mm / 2, -table_h_mm / 2, 0.0],
            [+table_w_mm / 2, -table_h_mm / 2, 0.0],
            [+table_w_mm / 2, +table_h_mm / 2, 0.0],
            [-table_w_mm / 2, +table_h_mm / 2, 0.0],
        ],
        dtype=np.float64,
    )
    R = T_table_to_camera[:3, :3]
    t = T_table_to_camera[:3, 3]
    rvec, _ = cv2.Rodrigues(R)
    proj, _ = cv2.projectPoints(P, rvec, t, K, None)
    return proj.reshape(-1, 2)


def _project_midline(K: np.ndarray, T_table_to_camera: np.ndarray,
                     table_h_mm: float) -> np.ndarray:
    P = np.array([[0.0, -table_h_mm / 2, 0.0], [0.0, +table_h_mm / 2, 0.0]])
    R = T_table_to_camera[:3, :3]
    t = T_table_to_camera[:3, 3]
    rvec, _ = cv2.Rodrigues(R)
    proj, _ = cv2.projectPoints(P, rvec, t, K, None)
    return proj.reshape(-1, 2)


def _render_trajectory_plot(
    obs: list[BallObs], bounces: list[Bounce], image_shape: tuple[int, int],
) -> np.ndarray:
    """Sparkline of v(t) plus image-space ball trail overlaid on the first
    available RGB frame for context."""
    H, W = image_shape
    bg = obs[0].rgb.copy() if obs else np.full((H, W, 3), 30, dtype=np.uint8)
    # Image-space ball trail.
    pts = np.array([[o.u, o.v] for o in obs], dtype=np.float32).reshape(-1, 1, 2)
    if len(pts) >= 2:
        cv2.polylines(bg, [np.round(pts).astype(np.int32)], False, COLOR_TRAJ, 2)
    for bnc in bounces:
        u, v = bnc.pixel_uv
        cv2.circle(bg, (int(round(u)), int(round(v))), 8, COLOR_BOUNCE, 2)
    cv2.putText(bg, f"ball trajectory ({len(obs)} obs, {len(bounces)} bounces)",
                (10, 25), cv2.FONT_HERSHEY_SIMPLEX, 0.6, COLOR_TABLE_LINE, 2)
    return bg


def _hstack_pad(panels: list[np.ndarray]) -> np.ndarray:
    h = max(p.shape[0] for p in panels)
    out = []
    for p in panels:
        if p.shape[0] != h:
            pad = np.zeros((h - p.shape[0], p.shape[1], 3), dtype=p.dtype)
            p = np.vstack([p, pad])
        out.append(p)
    return np.hstack(out)


# ── Main ─────────────────────────────────────────────────────────────────────


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    args = parse_args()

    cfg_path = Path(__file__).resolve().parent / "config.yaml"
    cfg = yaml.safe_load(open(cfg_path))
    table_w_mm = float(cfg["table"]["width_mm"])
    table_h_mm = float(cfg["table"]["height_mm"])

    vis_path = args.vis_path.resolve()
    seg_path = _seg_path_for(vis_path)
    pongtown_pb = _pongtown_pb_path(vis_path)
    if not vis_path.is_file() or not seg_path.is_file():
        raise FileNotFoundError(f"Missing vis/seg: {vis_path} or {seg_path}")
    if not pongtown_pb.is_file():
        raise FileNotFoundError(f"Missing pongtown.pb (run pongtown/main.py first): {pongtown_pb}")

    pose_by_frame = _load_per_frame_pose(pongtown_pb)
    log.info("Loaded %d per-frame global poses", len(pose_by_frame))

    # ── Pass 1: extract ball observations (only frames with a global pose) ──
    obs: list[BallObs] = []
    for b in iter_bundles(
        vis_path, seg_path, cfg["mask_labels"],
        max_frames=args.max_frames, sample_every=args.sample_every,
    ):
        T_t2c = pose_by_frame.get(int(b.frame_idx))
        if T_t2c is None:
            continue
        cent = _ball_centroid(b.ball_mask)
        if cent is None:
            continue
        u, v, area = cent
        obs.append(BallObs(
            frame_idx=int(b.frame_idx),
            frame_number=int(b.frame_number),
            timestamp_ns=int(b.timestamp_ns),
            u=u, v=v, area_px=area,
            K=b.intrinsics.copy(),
            T_table_to_camera=T_t2c.copy(),
            rgb=b.rgb.copy(),
            ball_mask=b.ball_mask.copy(),
        ))
    log.info("Ball observations: %d", len(obs))
    if not obs:
        log.warning("No ball observations; nothing to do")
        return

    # ── Detect bounces in image v(t) ────────────────────────────────────────
    bounces_idx = _detect_bounces(
        obs,
        min_prominence_px=args.min_prominence_px,
        min_spacing=args.min_spacing_frames,
        smooth_sigma=args.smooth_sigma,
    )
    log.info("Bounce candidates: %d", len(bounces_idx))

    # ── Localise each bounce on the table plane (camera-frame ray cast) ─────
    bounces: list[Bounce] = []
    for obs_idx, prom in bounces_idx:
        o = obs[obs_idx]
        P_cam = _ray_plane_intersect_in_camera(
            o.K, (o.u, o.v), o.T_table_to_camera,
        )
        if P_cam is None:
            log.warning("Bounce at frame %d: ray parallel/behind plane", o.frame_idx)
            continue
        P_table = _to_table_local(P_cam, o.T_table_to_camera)
        side, inside = _classify_side(P_table, table_w_mm, table_h_mm)
        conf = _confidence(o, prom, P_table, table_w_mm, table_h_mm)
        bounces.append(Bounce(
            obs_idx=obs_idx,
            frame_idx=o.frame_idx,
            frame_number=o.frame_number,
            timestamp_ns=o.timestamp_ns,
            pixel_uv=(o.u, o.v),
            cam_xyz_mm=P_cam,
            table_xyz_mm=P_table,
            side=side,
            inside_table=inside,
            confidence=conf,
            prominence_px=prom,
        ))
        log.info(
            "  bounce frame=%d uv=(%.1f, %.1f)  table_xy_mm=(%+.0f, %+.0f) z=%+.0f"
            "  side=%s inside=%s prom=%.1f conf=%.2f",
            o.frame_idx, o.u, o.v, P_table[0], P_table[1], P_table[2],
            side, inside, prom, conf,
        )

    # ── Output ──────────────────────────────────────────────────────────────
    odir = _output_dir(vis_path)
    odir.mkdir(parents=True, exist_ok=True)
    log.info("Writing → %s", odir)

    # Per-bounce side-by-side panels.
    for i, bnc in enumerate(bounces):
        o = obs[bnc.obs_idx]
        quad = _project_table_quad(o.K, o.T_table_to_camera, table_w_mm, table_h_mm)
        midline = _project_midline(o.K, o.T_table_to_camera, table_h_mm)
        left = _render_image_panel(o, bnc, quad, midline)
        right = _render_topdown(
            bounces, table_w_mm, table_h_mm,
            highlight_idx=i, canvas_h=left.shape[0],
        )
        montage = _hstack_pad([left, right])
        out = odir / f"bounce_{i + 1:02d}_frame_{bnc.frame_idx:06d}.png"
        cv2.imwrite(str(out), montage)

    # Aggregate top-down with all bounces.
    cv2.imwrite(
        str(odir / "topdown_all.png"),
        _render_topdown(bounces, table_w_mm, table_h_mm, canvas_h=720),
    )

    # Image-space trajectory.
    if obs:
        cv2.imwrite(
            str(odir / "trajectory.png"),
            _render_trajectory_plot(obs, bounces, obs[0].rgb.shape[:2]),
        )

    # JSON dump.
    bounces_json = [
        {
            "index": i + 1,
            "frame_idx": bnc.frame_idx,
            "frame_number": bnc.frame_number,
            "timestamp_ns": bnc.timestamp_ns,
            "pixel_uv": [float(bnc.pixel_uv[0]), float(bnc.pixel_uv[1])],
            "cam_xyz_mm": [float(v) for v in bnc.cam_xyz_mm],
            "table_xyz_mm": [float(v) for v in bnc.table_xyz_mm],
            "side": bnc.side,
            "inside_table": bnc.inside_table,
            "confidence": bnc.confidence,
            "prominence_px": bnc.prominence_px,
        }
        for i, bnc in enumerate(bounces)
    ]
    (odir / "bounces.json").write_text(json.dumps({
        "vis_path": str(vis_path),
        "table_width_mm": table_w_mm,
        "table_height_mm": table_h_mm,
        "n_observations": len(obs),
        "n_bounces": len(bounces),
        "bounces": bounces_json,
    }, indent=2))
    log.info("Done: %d bounces written", len(bounces))


if __name__ == "__main__":
    main()
