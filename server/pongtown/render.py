"""Overlay rendering for pongtown stages."""
from __future__ import annotations

import cv2
import numpy as np


COLOR_TABLE = (255, 255, 0)   # cyan in BGR
COLOR_NET = (255, 0, 255)     # magenta
COLOR_PERSON = (0, 255, 255)  # yellow
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
) -> np.ndarray:
    img = rgb_bgr.copy()
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
