"""Per-frame ball detection in table-mm.

Projects each `ball` mask centroid through H_img_to_table to get a 2D position
on the table surface, samples the mean RGB from the mask interior (eroded to
avoid felt bleed), and suppresses detections overlapping a cue or person mask.
"""
from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

_server_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_server_root))

from snookestown.loader import FrameMasks


@dataclass
class BallDetection:
    x_mm: float
    y_mm: float
    x_img: float
    y_img: float
    area_px: int
    mean_rgb: tuple[float, float, float]
    mask_object_id: int


def _project(H: np.ndarray, u: float, v: float) -> tuple[float, float]:
    p = H @ np.array([u, v, 1.0])
    if abs(p[2]) < 1e-9:
        return float("nan"), float("nan")
    return float(p[0] / p[2]), float(p[1] / p[2])


def _mask_centroid(mask: np.ndarray) -> tuple[float, float, int] | None:
    m = cv2.moments(mask.astype(np.uint8))
    area = int(m["m00"])
    if area <= 0:
        return None
    cx = m["m10"] / m["m00"]
    cy = m["m01"] / m["m00"]
    return float(cx), float(cy), area


def _mean_rgb_inside(
    rgb: np.ndarray, mask: np.ndarray, erode_kernel_px: int
) -> tuple[float, float, float]:
    m = mask.astype(np.uint8)
    if erode_kernel_px > 1:
        k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (erode_kernel_px, erode_kernel_px))
        eroded = cv2.erode(m, k)
        if eroded.any():
            m = eroded
    mean = cv2.mean(rgb, mask=m)
    return float(mean[0]), float(mean[1]), float(mean[2])


def _overlaps_any(
    cx: float, cy: float, masks: list[tuple[int, float, np.ndarray]]
) -> bool:
    ix, iy = int(round(cx)), int(round(cy))
    for _, _, m in masks:
        if 0 <= iy < m.shape[0] and 0 <= ix < m.shape[1] and m[iy, ix]:
            return True
    return False


def detect_balls(
    rgb: np.ndarray,
    frame_masks: FrameMasks,
    H_img_to_table: np.ndarray | None,
    cfg: dict,
) -> list[BallDetection]:
    """Detect ball positions for one frame.  Empty list if H is None."""
    if H_img_to_table is None:
        return []

    bd_cfg = cfg["ball_detect"]
    min_area = int(bd_cfg["min_mask_area_px"])
    max_area = int(bd_cfg["max_mask_area_px"])
    erode_k = int(bd_cfg["erode_kernel_px"])

    # Resize rgb to match mask shape if needed.
    if frame_masks.balls:
        sample_mask = frame_masks.balls[0][2]
        if sample_mask.shape != rgb.shape[:2]:
            rgb_m = cv2.resize(
                rgb, (sample_mask.shape[1], sample_mask.shape[0]),
                interpolation=cv2.INTER_AREA,
            )
        else:
            rgb_m = rgb
    else:
        return []

    detections: list[BallDetection] = []
    for obj_id, _conf, mask in frame_masks.balls:
        cent = _mask_centroid(mask)
        if cent is None:
            continue
        cx, cy, area = cent
        if area < min_area or area > max_area:
            continue
        # Suppress balls whose centroid falls inside a cue or person mask.
        if _overlaps_any(cx, cy, frame_masks.cues):
            continue
        if _overlaps_any(cx, cy, frame_masks.persons):
            continue

        x_mm, y_mm = _project(H_img_to_table, cx, cy)
        if not (np.isfinite(x_mm) and np.isfinite(y_mm)):
            continue

        mean_rgb = _mean_rgb_inside(rgb_m, mask, erode_k)
        detections.append(BallDetection(
            x_mm=x_mm, y_mm=y_mm, x_img=cx, y_img=cy,
            area_px=area, mean_rgb=mean_rgb, mask_object_id=obj_id,
        ))

    return detections
