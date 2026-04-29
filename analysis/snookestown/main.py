#!/usr/bin/env python3
"""Interactive snooker table pose visualizer.

Shows the segmentation map for a selected frame with a projected snooker table
rectangle overlaid.  Five camera-pose parameters (x, y, z, theta, phi —
measured from the table centre) are exposed as sliders.  The IoU between the
projected quad and the table mask is displayed in real time, and a gradient-
free optimiser can find the best-fitting pose for the current frame.

Usage
-----
    cd server
    uv run python ../analysis/snookestown/main.py ../recordings/<name>/<name>.vis.pb

    # Explicit seg file:
    uv run python ../analysis/snookestown/main.py ../recordings/<name>/<name>.vis.pb \\
        --seg ../recordings/<name>/<name>.segmentation.pb
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import cv2
import numpy as np
import matplotlib.pyplot as plt
from matplotlib.widgets import Slider, Button
from scipy.optimize import minimize

_project_root = Path(__file__).resolve().parent.parent.parent
_server_root  = _project_root / "server"
sys.path.insert(0, str(_project_root))
sys.path.insert(0, str(_project_root / "proto"))
sys.path.insert(0, str(_server_root))

from proto import perceiver_pb2, segmentation_pb2
from streamlog.protoio import ProtoIO
from snookestown.loader import FrameMasks, decode_mask, canonical_label

_frame_io = ProtoIO(perceiver_pb2.PerceiverDataFrame)
_seg_io   = ProtoIO(segmentation_pb2.SegmentationResponse)

# ── Table constants ───────────────────────────────────────────────────────────
TABLE_W = 3569.0   # mm, long side
TABLE_H = 1778.0   # mm, short side

# 4 corners in table-mm (origin = table centre, z = 0 plane)
CORNERS_MM = np.array([
    [-TABLE_W / 2, -TABLE_H / 2, 0.0],
    [ TABLE_W / 2, -TABLE_H / 2, 0.0],
    [ TABLE_W / 2,  TABLE_H / 2, 0.0],
    [-TABLE_W / 2,  TABLE_H / 2, 0.0],
], dtype=np.float64)

# Seg label → RGB colour for canvas
_SEG_RGB = {
    "table":  np.array([40,  140,  40], dtype=np.uint8),
    "ball":   np.array([255, 210,   0], dtype=np.uint8),
    "cue":    np.array([ 60, 190, 190], dtype=np.uint8),
    "person": np.array([220,  60,  60], dtype=np.uint8),
}


# ── Camera model ──────────────────────────────────────────────────────────────

def build_R(theta_deg: float, phi_deg: float) -> np.ndarray:
    """World-to-camera rotation matrix.

    Coordinate system
    -----------------
    World: origin = table centre, +x along long side, +y along short side, +z up.

    Camera parameters
    -----------------
    theta : azimuth (deg) — which horizontal direction the camera points.
            theta=0 → camera looks along world +y (table short-side direction).
    phi   : elevation FROM HORIZONTAL (deg).
            phi=0  → looking horizontally in the theta direction.
            phi=90 → looking straight down at the table.
            phi=-90→ looking straight up.

    With phi=0, z≈0: the table surface (z=0) is just below the camera, so the
    horizon (z=0 plane extended to infinity) sits at v=cy and the table fills
    the lower half of the image — matching the intuitive "floor-level view".

    Row convention
    --------------
    R rows = [right, down, look] so that P_cam = R @ (P_world − cam_pos).
    Camera +x = image right, camera +y = image down, camera +z = into scene.
    """
    th = np.deg2rad(theta_deg)
    ph = np.deg2rad(phi_deg)

    # Look direction in world space:
    #   ph=0  → (sin(th), cos(th), 0)       — horizontal
    #   ph=90 → (0, 0, -1)                  — straight down
    look = np.array([
        np.sin(th) * np.cos(ph),
        np.cos(th) * np.cos(ph),
        -np.sin(ph),
    ], dtype=np.float64)
    look /= np.linalg.norm(look)   # safety normalise

    # Camera right = cross(look, world_up), normalised.
    # Degenerate when look ≈ ±world_up (phi≈±90); fall back to theta-based vector.
    world_up = np.array([0.0, 0.0, 1.0])
    right = np.cross(look, world_up)
    if np.linalg.norm(right) < 1e-6:
        # Camera pointing straight up or down — use theta to pin the roll.
        right = np.array([np.cos(th), -np.sin(th), 0.0])
    right /= np.linalg.norm(right)

    # Camera down = cross(look, right).
    # Satisfies right × down = look (right-handed), so world-floor (z<0 from cam)
    # correctly maps to positive v (lower half of image).
    down = np.cross(look, right)
    down /= np.linalg.norm(down)

    return np.stack([right, down, look], axis=0)


def _clip_near_plane(pts_cam: np.ndarray, near: float = 1.0) -> np.ndarray | None:
    """Sutherland-Hodgman clip of a polygon in camera space to z_cam > near.

    Handles the case where some table corners are behind the camera (e.g. the
    near edge of the table when the camera is at table level, phi≈0).
    Returns clipped (M, 3) or None if the entire polygon is behind the camera.
    """
    result = []
    n = len(pts_cam)
    for i in range(n):
        a, b = pts_cam[i], pts_cam[(i + 1) % n]
        a_in = a[2] > near
        b_in = b[2] > near
        if a_in:
            result.append(a)
        if a_in != b_in:
            t = (near - a[2]) / (b[2] - a[2])
            result.append(a + t * (b - a))
    return np.array(result, dtype=np.float64) if len(result) >= 3 else None


def project(corners_w: np.ndarray, cx: float, cy: float, cz: float,
            theta: float, phi: float, K: np.ndarray
            ) -> tuple[np.ndarray | None, bool]:
    """Project table corners to pixel coords with near-plane clipping.

    Returns
    -------
    pts_2d : (M, 2) float pixel coords of the clipped polygon, or None
    ok     : True when any part of the polygon is visible
    """
    R       = build_R(theta, phi)
    cam_pos = np.array([cx, cy, cz], dtype=np.float64)
    P_cam   = (R @ (corners_w - cam_pos).T).T    # (4, 3)

    clipped = _clip_near_plane(P_cam)
    if clipped is None:
        return None, False

    u = K[0, 0] * clipped[:, 0] / clipped[:, 2] + K[0, 2]
    v = K[1, 1] * clipped[:, 1] / clipped[:, 2] + K[1, 2]
    return np.stack([u, v], axis=1), True


# ── Polygon helpers ───────────────────────────────────────────────────────────

def _clip_to_frame(poly: np.ndarray, w: int, h: int) -> np.ndarray | None:
    """Sutherland-Hodgman clip of a convex polygon to [0,w)×[0,h)."""
    def clip_edge(pts, x0, y0, x1, y1):
        out = []
        if not pts:
            return out
        inside = lambda p: (x1-x0)*(p[1]-y0) - (y1-y0)*(p[0]-x0) >= 0
        def intersect(a, b):
            dx, dy   = b[0]-a[0], b[1]-a[1]
            ex, ey   = x1-x0, y1-y0
            denom    = dx*ey - dy*ex
            if abs(denom) < 1e-10:
                return a
            t = ((x0-a[0])*ey - (y0-a[1])*ex) / denom
            return [a[0]+t*dx, a[1]+t*dy]
        n = len(pts)
        for i in range(n):
            a, b = pts[i], pts[(i+1) % n]
            ia, ib = inside(a), inside(b)
            if ia:
                out.append(a)
            if ia != ib:
                out.append(intersect(a, b))
        return out

    pts = [[float(p[0]), float(p[1])] for p in poly]
    for edge in [(0,0,w,0), (w,0,w,h), (w,h,0,h), (0,h,0,0)]:
        pts = clip_edge(pts, *edge)
    if len(pts) < 3:
        return None
    return np.array(pts, dtype=np.float32)


def compute_iou(pts_2d: np.ndarray | None, ok: bool,
                table_mask: np.ndarray) -> float:
    """IoU between projected quad (clipped to frame) and table_mask."""
    if not ok or pts_2d is None:
        return 0.0
    h, w = table_mask.shape
    clipped = _clip_to_frame(pts_2d, w, h)
    if clipped is None:
        return 0.0
    proj = np.zeros((h, w), dtype=np.uint8)
    cv2.fillPoly(proj, [clipped.astype(np.int32)], 1)
    inter = int((proj & table_mask.astype(np.uint8)).sum())
    union = int((proj | table_mask.astype(np.uint8)).sum())
    return inter / max(union, 1)


# ── Rendering ─────────────────────────────────────────────────────────────────

def _render_seg(bundle: FrameMasks | None, h: int, w: int) -> np.ndarray:
    canvas = np.full((h, w, 3), 20, dtype=np.uint8)
    if bundle is None:
        return canvas
    for label, masks in [("table",  bundle.table),
                         ("person", bundle.persons),
                         ("cue",    bundle.cues),
                         ("ball",   bundle.balls)]:
        col = _SEG_RGB[label]
        for _, _, m in masks:
            if m.shape == (h, w):
                canvas[m] = col
    return canvas


# ── Data loading ──────────────────────────────────────────────────────────────

def load_data(vis_path: Path, seg_path: Path):
    """Return (frames_list, K, img_h, img_w).

    frames_list : list of (FrameMasks | None, img_h, img_w)
    K           : 3×3 intrinsic matrix (scaled to mask resolution)
    """
    print("Loading .vis.pb …")
    vis_frames = _frame_io.read_file(vis_path)

    # Intrinsics from first frame that has them.
    K_orig, orig_h, orig_w = None, None, None
    for f in vis_frames:
        intr = f.camera_intrinsics
        if intr and intr.fx > 0:
            K_orig = np.array([[intr.fx, 0, intr.cx],
                                [0, intr.fy, intr.cy],
                                [0, 0, 1]], dtype=np.float64)
            # Try to get original image size
            try:
                from motioncap.geometry import decode_frame_rgb
                rgb = decode_frame_rgb(f)
                orig_h, orig_w = rgb.shape[:2]
            except Exception:
                pass
            break

    print("Loading .segmentation.pb …")
    seg_responses = _seg_io.read_file(seg_path)

    frames_list: list[tuple[FrameMasks | None, int, int]] = []
    img_h, img_w = 720, 1280  # fallback

    for resp in seg_responses:
        bundle = FrameMasks()
        fh, fw = None, None
        for m in resp.masks:
            label = canonical_label(m.label)
            if label is None:
                continue
            try:
                mask = decode_mask(m.mask_data)
            except Exception:
                continue
            if fh is None:
                fh, fw = mask.shape
            entry = (int(m.object_id), float(m.confidence), mask)
            if   label == "table":  bundle.table.append(entry)
            elif label == "ball":   bundle.balls.append(entry)
            elif label == "cue":    bundle.cues.append(entry)
            elif label == "person": bundle.persons.append(entry)
        if fh is not None:
            img_h, img_w = fh, fw
        frames_list.append((bundle, img_h, img_w))

    print(f"Loaded {len(frames_list)} frames  ({img_h}×{img_w})")

    # Scale K to mask resolution if needed.
    if K_orig is None:
        fl = max(img_w, img_h) * 1.2
        K = np.array([[fl, 0, img_w/2], [0, fl, img_h/2], [0, 0, 1]], dtype=np.float64)
        print("No intrinsics found — using estimated K")
    elif orig_h is not None and (orig_h != img_h or orig_w != img_w):
        sx, sy = img_w / orig_w, img_h / orig_h
        K = K_orig.copy()
        K[0, 0] *= sx;  K[0, 2] *= sx
        K[1, 1] *= sy;  K[1, 2] *= sy
        print(f"Scaled K from {orig_w}×{orig_h} → {img_w}×{img_h}")
    else:
        K = K_orig

    return frames_list, K, img_h, img_w


# ── App ───────────────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser(description="Snooker pose visualizer")
    ap.add_argument("recording", type=Path, help="Path to .vis.pb")
    ap.add_argument("--seg", type=Path, default=None, help="Path to .segmentation.pb (default: sibling)")
    args = ap.parse_args()

    vis_path = args.recording.resolve()
    if args.seg:
        seg_path = args.seg.resolve()
    else:
        stem = vis_path.name.removesuffix(".vis.pb")
        primary_seg_path = vis_path.parent / f"{stem}.segmentation.pb"
        legacy_seg_path = vis_path.parent / f"{stem}.seg.pb"
        seg_path = primary_seg_path if primary_seg_path.exists() or not legacy_seg_path.exists() else legacy_seg_path

    frames_list, K, img_h, img_w = load_data(vis_path, seg_path)
    n_frames = len(frames_list)
    if n_frames == 0:
        print("No segmentation frames found.")
        return

    # ── Figure layout ─────────────────────────────────────────────────────────
    fig = plt.figure(figsize=(15, 11))
    fig.patch.set_facecolor("#1a1a2e")

    # Image axes: top 62% of figure
    ax_img = fig.add_axes([0.03, 0.38, 0.94, 0.60])
    ax_img.set_facecolor("#000000")
    ax_img.axis("off")
    ax_img.set_title("Snooker pose visualizer", color="white", fontsize=11, pad=4)

    img_disp = ax_img.imshow(
        np.zeros((img_h, img_w, 3), dtype=np.uint8),
        aspect="equal", interpolation="nearest",
    )

    # IoU readout
    iou_txt = ax_img.text(
        0.01, 0.97, "IoU: —", transform=ax_img.transAxes,
        color="yellow", fontsize=13, va="top", fontweight="bold",
    )

    # Sliders — (name, label, lo, hi, init, fmt)
    _HW = TABLE_W * 2   # ≈ 7138 mm — symmetric range for x/y/z
    _sliders_def = [
        ("frame", "Frame",    0,      n_frames-1, 0,      "d"),
        ("cx",    "cx (mm)", -_HW,    _HW,        0,      ".0f"),
        ("cy",    "cy (mm)", -_HW,    _HW,        0,      ".0f"),
        ("cz",    "cz (mm)", -_HW,    _HW,        2000,   ".0f"),
        ("theta", "θ (°)",   -180,    180,        0,      ".1f"),
        ("phi",   "φ (°)",   -180,    180,        0,      ".1f"),
    ]
    sliders: dict[str, Slider] = {}
    n_sl = len(_sliders_def)
    for i, (name, label, lo, hi, init, fmt) in enumerate(_sliders_def):
        bot = 0.32 - i * 0.052
        ax_s = fig.add_axes([0.12, bot, 0.74, 0.032],
                             facecolor="#16213e")
        color = "#e94560" if name == "frame" else "#0f3460"
        sl = Slider(ax_s, label, lo, hi, valinit=init,
                    color=color, valfmt=f"%{fmt}")
        sl.label.set_color("white")
        sl.valtext.set_color("white")
        if name == "frame":
            sl.valstep = 1
        sliders[name] = sl

    # Optimise button
    ax_btn = fig.add_axes([0.88, 0.315, 0.09, 0.038])
    btn_opt = Button(ax_btn, "Optimise", color="#e94560", hovercolor="#c73652")
    btn_opt.label.set_color("white")

    # ── Helpers ───────────────────────────────────────────────────────────────

    def get_table_mask(fidx: int) -> np.ndarray:
        bundle, fh, fw = frames_list[fidx]
        mask = np.zeros((fh, fw), dtype=bool)
        if bundle is not None:
            for _, _, m in bundle.table:
                if m.shape == (fh, fw):
                    mask |= m
        return mask

    def redraw():
        fidx  = int(sliders["frame"].val)
        cx    = float(sliders["cx"].val)
        cy    = float(sliders["cy"].val)
        cz    = float(sliders["cz"].val)
        theta = float(sliders["theta"].val)
        phi   = float(sliders["phi"].val)

        bundle, fh, fw = frames_list[fidx]
        canvas = _render_seg(bundle, fh, fw)        # RGB uint8

        pts, ok = project(CORNERS_MM, cx, cy, cz, theta, phi, K)
        table_mask = get_table_mask(fidx)
        iou = compute_iou(pts, ok, table_mask)

        # Draw clipped projected quad
        if ok and pts is not None:
            clipped = _clip_to_frame(pts, fw, fh)
            if clipped is not None:
                # Semi-transparent fill via blending
                fill_mask = np.zeros((fh, fw), dtype=np.uint8)
                cv2.fillPoly(fill_mask, [clipped.astype(np.int32)], 1)
                canvas[fill_mask.astype(bool)] = (
                    canvas[fill_mask.astype(bool)] * 0.55
                    + np.array([0, 200, 255]) * 0.45
                ).astype(np.uint8)
                # Outline
                cv2.polylines(canvas, [clipped.astype(np.int32)],
                              True, (0, 230, 255), 2, cv2.LINE_AA)
            # Corner dots (original unclipped pts that are within frame)
            for pt in pts.astype(int):
                if 0 <= pt[0] < fw and 0 <= pt[1] < fh:
                    cv2.circle(canvas, tuple(pt), 7, (255, 80, 0), -1, cv2.LINE_AA)

        img_disp.set_data(canvas)
        iou_txt.set_text(f"IoU: {iou:.4f}")
        fig.canvas.draw_idle()

    def on_change(_):
        redraw()

    for sl in sliders.values():
        sl.on_changed(on_change)

    # ── Optimiser ─────────────────────────────────────────────────────────────

    def on_optimise(_):
        fidx       = int(sliders["frame"].val)
        table_mask = get_table_mask(fidx)

        def loss(p):
            cx, cy, cz, theta, phi = p
            pts, ok = project(CORNERS_MM, cx, cy, cz, theta, phi, K)
            return 1.0 - compute_iou(pts, ok, table_mask)

        x0 = np.array([sliders["cx"].val, sliders["cy"].val, sliders["cz"].val,
                        sliders["theta"].val, sliders["phi"].val])
        iou0 = 1.0 - loss(x0)
        print(f"Optimising frame {fidx}  (start IoU={iou0:.4f}) …")
        iou_txt.set_text("Optimising…")
        fig.canvas.draw_idle()
        plt.pause(0.01)

        result = minimize(
            loss, x0, method="Powell",
            options={"maxiter": 3000, "ftol": 1e-6, "xtol": 5.0},
        )
        cx, cy, cz, theta, phi = result.x
        iou1 = 1.0 - result.fun
        print(f"Done  IoU: {iou0:.4f} → {iou1:.4f}  "
              f"cx={cx:.0f}  cy={cy:.0f}  cz={cz:.0f}  "
              f"θ={theta:.1f}  φ={phi:.1f}")

        _HW = TABLE_W * 2
        sliders["cx"].set_val(np.clip(cx, -_HW, _HW))
        sliders["cy"].set_val(np.clip(cy, -_HW, _HW))
        sliders["cz"].set_val(np.clip(cz, -_HW, _HW))
        sliders["theta"].set_val((theta + 180) % 360 - 180)
        sliders["phi"].set_val((phi + 180) % 360 - 180)
        redraw()

    btn_opt.on_clicked(on_optimise)

    redraw()
    plt.show()


if __name__ == "__main__":
    main()
