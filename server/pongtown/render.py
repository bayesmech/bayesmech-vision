"""Overlay rendering for pongtown stages."""
from __future__ import annotations

import cv2
import numpy as np


COLOR_TABLE = (255, 255, 0)    # cyan in BGR — table top
COLOR_LEGS = (50, 50, 200)     # dark red — table legs/apron (should be OUTSIDE quad)
COLOR_NET = (255, 0, 255)      # magenta
COLOR_PERSON = (0, 255, 255)   # yellow
COLOR_QUAD = (255, 255, 255)  # white
COLOR_MIDLINE = (0, 0, 255)   # red
COLOR_LINE = (0, 255, 0)      # green
COLOR_OFF = (0, 0, 255)       # red border


def _alpha_blend(rgb_bgr: np.ndarray, mask: np.ndarray, color, alpha=0.35) -> np.ndarray:
    overlay = rgb_bgr.copy()
    overlay[mask] = color
    return cv2.addWeighted(overlay, alpha, rgb_bgr, 1 - alpha, 0)


def render_stage1_panel(
    rgb_bgr: np.ndarray,
    table_mask: np.ndarray | None,
    net_mask: np.ndarray | None,
    person_mask: np.ndarray,
    quad_img: np.ndarray | None,
    midline_img: np.ndarray | None,
    title: str,
    legs_mask: np.ndarray | None = None,
) -> np.ndarray:
    img = rgb_bgr.copy()
    if legs_mask is not None and legs_mask.any():
        img = _alpha_blend(img, legs_mask, COLOR_LEGS, alpha=0.4)
    if table_mask is not None:
        img = _alpha_blend(img, table_mask, COLOR_TABLE)
    if net_mask is not None:
        img = _alpha_blend(img, net_mask, COLOR_NET)
    if person_mask.any():
        img = _alpha_blend(img, person_mask, COLOR_PERSON, alpha=0.25)
    if midline_img is not None:
        p1 = tuple(np.round(midline_img[0]).astype(int))
        p2 = tuple(np.round(midline_img[1]).astype(int))
        cv2.line(img, p1, p2, COLOR_MIDLINE, 2)
    if quad_img is not None:
        pts = np.round(quad_img).astype(np.int32).reshape(-1, 1, 2)
        cv2.polylines(img, [pts], True, COLOR_QUAD, 2)
    cv2.putText(img, title, (10, 25), cv2.FONT_HERSHEY_SIMPLEX, 0.6, COLOR_QUAD, 2)
    return img


def render_pose_panel(
    rgb_bgr: np.ndarray,
    rvec: np.ndarray | None,
    tvec: np.ndarray | None,
    K: np.ndarray,
    iou: float,
    title: str,
    mask_overlays: dict[str, np.ndarray] | None = None,
    off_screen: bool = False,
    quad_thickness: int = 2,
) -> np.ndarray:
    """Render the canonical table rectangle + net rectangle + midline reprojected
    into the image under the given pose. Used for both Stage 2 (per-frame PnP)
    and Stage 3 (global pose) panels."""
    img = rgb_bgr.copy()
    if mask_overlays:
        for kind, m in mask_overlays.items():
            if m is None or not m.any():
                continue
            color = {
                "legs": COLOR_LEGS,
                "table": COLOR_TABLE,
                "net": COLOR_NET,
                "person": COLOR_PERSON,
            }.get(kind, (200, 200, 200))
            alpha = 0.25 if kind == "person" else 0.35
            img = _alpha_blend(img, m, color, alpha)
    if rvec is None or tvec is None:
        cv2.putText(img, "PnP FAILED", (10, 25), cv2.FONT_HERSHEY_SIMPLEX, 0.6, COLOR_OFF, 2)
        return img
    from pongtown.pose import (
        canonical_corners_mm,
        canonical_midline_mm,
        canonical_net_strip_mm,
    )
    P_corners = canonical_corners_mm()
    P_mid = canonical_midline_mm()
    P_net = canonical_net_strip_mm()
    proj_c, _ = cv2.projectPoints(P_corners, rvec, tvec, K, None)
    proj_n, _ = cv2.projectPoints(P_net, rvec, tvec, K, None)
    proj_m, _ = cv2.projectPoints(P_mid, rvec, tvec, K, None)
    cv2.polylines(
        img, [np.round(proj_c).astype(np.int32).reshape(-1, 1, 2)], True,
        COLOR_QUAD, quad_thickness,
    )
    cv2.polylines(
        img, [np.round(proj_n).astype(np.int32).reshape(-1, 1, 2)], True,
        COLOR_NET, 2,
    )
    p1 = tuple(np.round(proj_m[0, 0]).astype(int))
    p2 = tuple(np.round(proj_m[1, 0]).astype(int))
    cv2.line(img, p1, p2, COLOR_MIDLINE, 2)
    cv2.putText(
        img, f"{title} iou={iou:.2f}", (10, 25),
        cv2.FONT_HERSHEY_SIMPLEX, 0.6, COLOR_QUAD, 2,
    )
    if off_screen:
        cv2.rectangle(img, (0, 0), (img.shape[1] - 1, img.shape[0] - 1), COLOR_OFF, 6)
    return img


def montage(panels: list[np.ndarray]) -> np.ndarray:
    """Concatenate panels horizontally, padding to a common height."""
    h = max(p.shape[0] for p in panels)
    out = []
    for p in panels:
        if p.shape[0] != h:
            pad = np.zeros((h - p.shape[0], p.shape[1], 3), dtype=p.dtype)
            p = np.vstack([p, pad])
        out.append(p)
    return np.hstack(out)
