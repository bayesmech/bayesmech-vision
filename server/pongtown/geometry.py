"""Pure-numpy geometric primitives used by all pongtown stages."""
from __future__ import annotations

import math

import cv2
import numpy as np


def line_from_two_points(p1: np.ndarray, p2: np.ndarray) -> tuple[float, float, float]:
    """Return (a, b, c) with a^2 + b^2 = 1 representing line ax + by + c = 0."""
    x1, y1 = float(p1[0]), float(p1[1])
    x2, y2 = float(p2[0]), float(p2[1])
    a = y2 - y1
    b = x1 - x2
    c = x2 * y1 - x1 * y2
    n = math.hypot(a, b)
    if n < 1e-12:
        raise ValueError("Degenerate line: p1 == p2")
    return a / n, b / n, c / n


def line_intersection(
    L1: tuple[float, float, float], L2: tuple[float, float, float]
) -> tuple[float, float] | None:
    a1, b1, c1 = L1
    a2, b2, c2 = L2
    det = a1 * b2 - a2 * b1
    if abs(det) < 1e-9:
        return None
    x = (b1 * c2 - b2 * c1) / det
    y = (a2 * c1 - a1 * c2) / det
    return float(x), float(y)


def point_line_distance(pts: np.ndarray, L: tuple[float, float, float]) -> np.ndarray:
    a, b, c = L
    return np.abs(pts[:, 0] * a + pts[:, 1] * b + c)


def signed_distance_to_line(pts: np.ndarray, L: tuple[float, float, float]) -> np.ndarray:
    a, b, c = L
    return pts[:, 0] * a + pts[:, 1] * b + c


def _rasterise_polygon(poly: np.ndarray, shape: tuple[int, int]) -> np.ndarray:
    canvas = np.zeros(shape, dtype=np.uint8)
    pts = np.asarray(poly, dtype=np.int32).reshape(-1, 1, 2)
    cv2.fillPoly(canvas, [pts], 1)
    return canvas.astype(bool)


def polygon_iou(
    poly_a: np.ndarray,
    poly_b: np.ndarray,
    image_shape: tuple[int, int],
    denom_exclude_mask: np.ndarray | None = None,
) -> float:
    """IoU of two polygons rasterised at `image_shape = (H, W)`.

    `denom_exclude_mask` (bool, same shape) removes pixels from the union
    denominator — used to exclude person pixels from the score.
    """
    a = _rasterise_polygon(poly_a, image_shape)
    b = _rasterise_polygon(poly_b, image_shape)
    inter = a & b
    union = a | b
    if denom_exclude_mask is not None:
        union = union & ~denom_exclude_mask
        inter = inter & ~denom_exclude_mask
    u = int(union.sum())
    if u == 0:
        return 0.0
    return float(inter.sum()) / float(u)


def geometric_median(points: np.ndarray, n_iters: int = 64, eps: float = 1e-6) -> np.ndarray:
    """Weiszfeld iterations. `points: (N, D)`. Returns `(D,)`."""
    pts = np.asarray(points, dtype=np.float64)
    x = pts.mean(axis=0)
    for _ in range(n_iters):
        d = np.linalg.norm(pts - x, axis=1)
        d = np.where(d < eps, eps, d)
        w = 1.0 / d
        x_new = (pts * w[:, None]).sum(axis=0) / w.sum()
        if np.linalg.norm(x_new - x) < eps:
            return x_new
        x = x_new
    return x


def circular_median_mod_pi(angles_rad: np.ndarray) -> float:
    """Median direction for axis-symmetric data (mod π).

    Returns a value in [0, π). Uses the doubled-angle trick so that 0 and π
    are treated as equivalent.
    """
    a = np.asarray(angles_rad, dtype=np.float64)
    z = np.exp(2j * a)
    mean_angle = math.atan2(z.imag.mean(), z.real.mean()) / 2.0
    if mean_angle < 0:
        mean_angle += math.pi
    return mean_angle
