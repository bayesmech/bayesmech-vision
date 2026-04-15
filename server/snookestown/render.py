"""Top-down table MP4 + per-frame RGB debug overlays."""
from __future__ import annotations

import sys
from pathlib import Path

import cv2
import numpy as np

_server_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_server_root))

from snookestown import colors
from snookestown.ball_detect import BallDetection
from snookestown.loader import FrameMasks
from snookestown.table_pose import TablePoseResult
from snookestown.tracker import Track


# ── Top-down MP4 ──────────────────────────────────────────────────────────────

def _mm_to_px(x_mm: float, y_mm: float, scale: float, margin: int) -> tuple[int, int]:
    return int(round(x_mm * scale)) + margin, int(round(y_mm * scale)) + margin


def render_topdown_mp4(
    tracks: list[Track],
    total_frames: int,
    timestamps_ns: list[int],
    out_path: Path,
    cfg: dict,
) -> None:
    W_mm = float(cfg["table"]["width_mm"])
    H_mm = float(cfg["table"]["height_mm"])
    draw_w = int(cfg["output"]["topdown_width_px"])
    margin = 40
    scale = (draw_w - 2 * margin) / W_mm
    draw_h = int(H_mm * scale) + 2 * margin
    canvas_size = (draw_w, draw_h)
    ball_r_px = max(4, int(round(float(cfg["output"]["ball_radius_draw_mm"]) * scale)))
    tail_frames = 60  # last ~2 s at 30 fps

    if len(timestamps_ns) >= 2 and timestamps_ns[-1] > timestamps_ns[0]:
        fps = (len(timestamps_ns) - 1) / ((timestamps_ns[-1] - timestamps_ns[0]) / 1e9)
    else:
        fps = float(cfg["output"].get("video_fps", 30))

    codec = cv2.VideoWriter_fourcc(*cfg["output"]["video_codec"])
    writer = cv2.VideoWriter(str(out_path), codec, fps, canvas_size)

    # Pre-index tracks by frame.
    frame_to_points: dict[int, list[tuple[Track, float, float, bool, bool]]] = {}
    for t in tracks:
        for f, p in t.positions.items():
            frame_to_points.setdefault(f, []).append((t, p.x_mm, p.y_mm, p.moving, p.interpolated))

    # Pocket positions.
    canon_pockets = [
        (0.0, 0.0), (W_mm, 0.0), (W_mm, H_mm), (0.0, H_mm),
        (W_mm / 2, 0.0), (W_mm / 2, H_mm),
    ]

    for frame_idx in range(total_frames):
        canvas = np.full((draw_h, draw_w, 3), 20, dtype=np.uint8)
        # Table felt.
        tl = _mm_to_px(0, 0, scale, margin)
        br = _mm_to_px(W_mm, H_mm, scale, margin)
        cv2.rectangle(canvas, tl, br, (40, 110, 40), thickness=-1)
        cv2.rectangle(canvas, tl, br, (80, 80, 60), thickness=2)

        # Pockets.
        for px_mm, py_mm in canon_pockets:
            cx, cy = _mm_to_px(px_mm, py_mm, scale, margin)
            cv2.circle(canvas, (cx, cy), max(ball_r_px + 4, 10), (20, 20, 20), thickness=-1)

        # Tails for moving balls.
        for t in tracks:
            for f in range(max(0, frame_idx - tail_frames), frame_idx):
                if f not in t.positions or (f + 1) not in t.positions:
                    continue
                p0 = t.positions[f]
                p1 = t.positions[f + 1]
                if not (p0.moving or p1.moving):
                    continue
                a = _mm_to_px(p0.x_mm, p0.y_mm, scale, margin)
                b = _mm_to_px(p1.x_mm, p1.y_mm, scale, margin)
                alpha = (f - (frame_idx - tail_frames)) / max(tail_frames, 1)
                col = colors.draw_bgr(t.color)
                faded = tuple(int(c * (0.2 + 0.8 * alpha)) for c in col)
                cv2.line(canvas, a, b, faded, 2, cv2.LINE_AA)

        # Balls at this frame.
        for t, x_mm, y_mm, moving, interp in frame_to_points.get(frame_idx, []):
            cx, cy = _mm_to_px(x_mm, y_mm, scale, margin)
            col = colors.draw_bgr(t.color)
            if interp:
                cv2.circle(canvas, (cx, cy), ball_r_px, col, thickness=1, lineType=cv2.LINE_AA)
            else:
                cv2.circle(canvas, (cx, cy), ball_r_px, col, thickness=-1, lineType=cv2.LINE_AA)
            cv2.circle(canvas, (cx, cy), ball_r_px, (0, 0, 0), thickness=1, lineType=cv2.LINE_AA)
            cv2.putText(canvas, f"{t.track_id}", (cx + ball_r_px + 2, cy - ball_r_px),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.4, (230, 230, 230), 1, cv2.LINE_AA)

        cv2.putText(canvas, f"frame {frame_idx}", (8, 22),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, (200, 200, 200), 1, cv2.LINE_AA)
        writer.write(canvas)

    writer.release()


# ── Seg-overlay MP4 (masks + projected table) ───────────────────────────────

_SEG_COLORS_BGR = {
    "table":  (40, 140, 40),
    "ball":   (0, 200, 255),
    "cue":    (180, 180, 60),
    "person": (60, 60, 220),
}


def _blend_mask(canvas: np.ndarray, mask: np.ndarray, bgr: tuple[int, int, int],
                alpha: float = 0.45) -> None:
    if mask.shape[:2] != canvas.shape[:2]:
        mask = cv2.resize(mask.astype(np.uint8), (canvas.shape[1], canvas.shape[0]),
                          interpolation=cv2.INTER_NEAREST).astype(bool)
    overlay = np.zeros_like(canvas)
    overlay[mask] = bgr
    canvas[mask] = (canvas[mask] * (1 - alpha) + overlay[mask] * alpha).astype(np.uint8)


def _first_mask_shape(fm: FrameMasks) -> tuple[int, int] | None:
    for group in (fm.table, fm.balls, fm.cues, fm.persons):
        if group:
            return group[0][2].shape
    return None


def render_segoverlay_mp4(
    masks_by_index: dict,
    n_frames: int,
    poses: list[TablePoseResult],
    out_path: Path,
    timestamps_ns: list[int],
    cfg: dict,
) -> None:
    """Build frames from the seg file only: coloured masks on a neutral
    background, with the reprojected table rectangle + 6 canonical pockets
    drawn on top. No pixels from the .vis file are used.
    """
    W = float(cfg["table"]["width_mm"])
    H = float(cfg["table"]["height_mm"])

    if len(timestamps_ns) >= 2 and timestamps_ns[-1] > timestamps_ns[0]:
        fps = (len(timestamps_ns) - 1) / ((timestamps_ns[-1] - timestamps_ns[0]) / 1e9)
    else:
        fps = float(cfg["output"].get("video_fps", 30))

    # Image dimensions: take from the first frame that has any mask.
    dims: tuple[int, int] | None = None
    for idx in range(n_frames):
        fm = masks_by_index.get(idx)
        if fm is None:
            continue
        s = _first_mask_shape(fm)
        if s is not None:
            dims = s
            break
    if dims is None:
        return
    h, w = dims

    codec = cv2.VideoWriter_fourcc(*cfg["output"]["video_codec"])
    writer = cv2.VideoWriter(str(out_path), codec, fps, (w, h))

    canon_mm = np.array([
        [0.0, 0.0, 1.0], [W, 0.0, 1.0],
        [W, H, 1.0], [0.0, H, 1.0],
        [W / 2, 0.0, 1.0], [W / 2, H, 1.0],
    ])
    rect_mm = np.array([
        [0.0, 0.0, 1.0], [W, 0.0, 1.0], [W, H, 1.0], [0.0, H, 1.0],
    ])

    for idx in range(n_frames):
        canvas = np.full((h, w, 3), 25, dtype=np.uint8)
        fm = masks_by_index.get(idx)
        if fm is not None:
            # Paint masks as solid colours (no blending — no RGB underneath).
            for _, _, m in fm.table:
                if m.shape == (h, w):
                    canvas[m] = _SEG_COLORS_BGR["table"]
            for _, _, m in fm.persons:
                if m.shape == (h, w):
                    canvas[m] = _SEG_COLORS_BGR["person"]
            for _, _, m in fm.cues:
                if m.shape == (h, w):
                    canvas[m] = _SEG_COLORS_BGR["cue"]
            for _, _, m in fm.balls:
                if m.shape == (h, w):
                    canvas[m] = _SEG_COLORS_BGR["ball"]

        pose = poses[idx]
        # Hull quad — the convex hull of the visible table mask pixels.
        # This is the ground-truth bounding shape we're trying to match.
        if pose.hull_quad is not None:
            cv2.polylines(canvas, [pose.hull_quad.astype(np.int32)], True,
                          (255, 128, 0), 2, cv2.LINE_AA)  # orange

        if pose.H_img_to_table is not None:
            try:
                H_inv = np.linalg.inv(pose.H_img_to_table)
                # Clip the back-projected table rect to the frame before drawing.
                rect_img = (H_inv @ rect_mm.T).T
                rect_img = rect_img[:, :2] / rect_img[:, 2:3]
                from snookestown.table_pose import _clip_polygon_to_rect
                clipped = _clip_polygon_to_rect(rect_img, w, h)
                if clipped is not None:
                    cv2.polylines(canvas, [clipped.astype(np.int32)], True,
                                  (0, 255, 255), 2, cv2.LINE_AA)  # yellow
                cp_img = (H_inv @ canon_mm.T).T
                cp_img = cp_img[:, :2] / cp_img[:, 2:3]
                for i, (x, y) in enumerate(cp_img):
                    if 0 <= x < w and 0 <= y < h:
                        col = (0, 255, 0) if i >= 4 else (0, 180, 255)
                        cv2.circle(canvas, (int(round(x)), int(round(y))), 10, col, 2, cv2.LINE_AA)
            except np.linalg.LinAlgError:
                pass

        method_name = {0: "UNKNOWN", 1: "POCKET_FULL", 2: "POCKET_PARTIAL",
                       3: "EDGE_ONLY", 4: "POSE_PROPAGATED", 5: "FAILED"}
        cv2.putText(canvas, f"frame {idx}  {method_name.get(pose.method, '?')} "
                    f"q={pose.quality:.2f}",
                    (8, 22), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (255, 255, 255), 1, cv2.LINE_AA)

        legend = [("table", _SEG_COLORS_BGR["table"]),
                  ("ball", _SEG_COLORS_BGR["ball"]),
                  ("cue", _SEG_COLORS_BGR["cue"]),
                  ("person", _SEG_COLORS_BGR["person"]),
                  ("hull quad", (255, 128, 0)),
                  ("projected rect", (0, 255, 255))]
        for i, (name, col) in enumerate(legend):
            y = 42 + i * 18
            cv2.rectangle(canvas, (8, y - 10), (22, y + 2), col, -1)
            cv2.putText(canvas, name, (28, y), cv2.FONT_HERSHEY_SIMPLEX,
                        0.45, (230, 230, 230), 1, cv2.LINE_AA)

        writer.write(canvas)

    writer.release()


# ── Per-frame RGB debug overlay ──────────────────────────────────────────────

def render_debug_overlay(
    rgb: np.ndarray,
    pose: TablePoseResult,
    detections: list[BallDetection],
    tracks_by_obj_id: dict[int, Track],
    cfg: dict,
) -> np.ndarray:
    canvas = cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR).copy()
    W = float(cfg["table"]["width_mm"])
    H = float(cfg["table"]["height_mm"])

    # Reproject table rectangle + canonical pockets.
    if pose.H_img_to_table is not None:
        try:
            H_inv = np.linalg.inv(pose.H_img_to_table)
        except np.linalg.LinAlgError:
            H_inv = None
        if H_inv is not None:
            corners_mm = np.array([
                [0.0, 0.0, 1.0],
                [W, 0.0, 1.0],
                [W, H, 1.0],
                [0.0, H, 1.0],
            ])
            corners_img = (H_inv @ corners_mm.T).T
            corners_img = corners_img[:, :2] / corners_img[:, 2:3]
            cv2.polylines(
                canvas, [corners_img.astype(np.int32)], isClosed=True,
                color=(0, 255, 255), thickness=2, lineType=cv2.LINE_AA,
            )

            canon_pockets_mm = np.array([
                [0.0, 0.0, 1.0], [W, 0.0, 1.0],
                [W, H, 1.0], [0.0, H, 1.0],
                [W / 2, 0.0, 1.0], [W / 2, H, 1.0],
            ])
            cp_img = (H_inv @ canon_pockets_mm.T).T
            cp_img = cp_img[:, :2] / cp_img[:, 2:3]
            for i, (x, y) in enumerate(cp_img):
                col = (0, 255, 0) if i >= 4 else (0, 200, 255)
                cv2.circle(canvas, (int(round(x)), int(round(y))), 8, col, 2, cv2.LINE_AA)

    # Detected pockets (input evidence).
    for p in pose.pockets:
        col = (0, 80, 255) if p.kind == 1 else (255, 80, 0)  # CORNER orange, MIDDLE blue
        cv2.drawMarker(canvas, (int(round(p.x_img)), int(round(p.y_img))),
                       col, cv2.MARKER_TRIANGLE_UP, 14, 2, cv2.LINE_AA)

    # Fitted cushions.
    for c in pose.cushions:
        a = (int(round(c.p0[0])), int(round(c.p0[1])))
        b = (int(round(c.p1[0])), int(round(c.p1[1])))
        cv2.line(canvas, a, b, (200, 200, 0), 1, cv2.LINE_AA)

    # Ball detections with track IDs.
    for d in detections:
        t = tracks_by_obj_id.get(d.mask_object_id)
        tid = t.track_id if t is not None else -1
        col = colors.draw_bgr(t.color) if t is not None else (200, 200, 200)
        cv2.circle(canvas, (int(round(d.x_img)), int(round(d.y_img))), 5, col, 2, cv2.LINE_AA)
        cv2.putText(canvas, f"T{tid}", (int(round(d.x_img)) + 6, int(round(d.y_img)) - 6),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.4, col, 1, cv2.LINE_AA)

    # Method / quality label.
    method_name = {0: "UNKNOWN", 1: "POCKET_FULL", 2: "POCKET_PARTIAL",
                   3: "EDGE_ONLY", 4: "POSE_PROPAGATED", 5: "FAILED"}
    cv2.putText(canvas, f"{method_name.get(pose.method, '?')} q={pose.quality:.2f}",
                (8, 22), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (255, 255, 255), 1, cv2.LINE_AA)
    return canvas
