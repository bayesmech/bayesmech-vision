"""Snooker ball colour classifier.

Hardcoded HSV palette — snooker uses a fixed 8-colour set
(white, red, yellow, green, brown, blue, pink, black), so these
centroids are constants of the sport, not tuning parameters.

OpenCV HSV convention: H in [0, 180), S in [0, 255], V in [0, 255].
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

_server_root = Path(__file__).resolve().parent.parent
_project_root = _server_root.parent
sys.path.insert(0, str(_project_root))
sys.path.insert(0, str(_project_root / "proto"))
sys.path.insert(0, str(_server_root))

from proto import snookestown_pb2

Color = snookestown_pb2.SnookerResponse.Color


# HSV centroids for each snooker ball colour.
# Cushion felt is green, so GREEN's chromatic signature overlaps cushion bleed;
# the classifier leans on S and V to separate ball-green from felt-green.
_PALETTE: list[tuple[int, np.ndarray]] = [
    (Color.RED,    np.array([  2, 210, 150], dtype=np.float32)),
    (Color.YELLOW, np.array([ 25, 230, 220], dtype=np.float32)),
    (Color.GREEN,  np.array([ 70, 200, 110], dtype=np.float32)),
    (Color.BROWN,  np.array([ 12, 180,  90], dtype=np.float32)),
    (Color.BLUE,   np.array([110, 220, 170], dtype=np.float32)),
    (Color.PINK,   np.array([172, 110, 230], dtype=np.float32)),
    (Color.BLACK,  np.array([  0,   0,  25], dtype=np.float32)),
    (Color.WHITE,  np.array([  0,   0, 235], dtype=np.float32)),
]

# Chromatic colours: weight S and H heavily, V less.
# Achromatic colours (BLACK, WHITE): dominated by V.
_WEIGHTS_CHROMATIC = np.array([2.0, 1.5, 0.5], dtype=np.float32)
_WEIGHTS_ACHROMATIC = np.array([0.0, 0.3, 2.0], dtype=np.float32)
_ACHROMATIC = {Color.BLACK, Color.WHITE}


def rgb_to_hsv(rgb: np.ndarray) -> np.ndarray:
    """Convert a (3,) RGB uint8 vector to (3,) HSV float32 in OpenCV ranges."""
    import cv2
    px = np.array(rgb, dtype=np.uint8).reshape(1, 1, 3)
    hsv = cv2.cvtColor(px, cv2.COLOR_RGB2HSV)[0, 0].astype(np.float32)
    return hsv


def _hue_dist(h_a: float, h_b: float) -> float:
    """Circular hue distance in OpenCV units (wraps at 180)."""
    d = abs(h_a - h_b)
    return float(min(d, 180.0 - d))


def classify(rgb: np.ndarray) -> tuple[int, float]:
    """Classify a mean RGB colour into the snooker palette.

    Returns (Color enum value, confidence in [0, 1]).
    Confidence = normalised margin between best and second-best candidate.
    """
    hsv = rgb_to_hsv(rgb)

    scores: list[tuple[int, float]] = []
    for color_id, centroid in _PALETTE:
        w = _WEIGHTS_ACHROMATIC if color_id in _ACHROMATIC else _WEIGHTS_CHROMATIC
        dh = _hue_dist(hsv[0], centroid[0])
        ds = hsv[1] - centroid[1]
        dv = hsv[2] - centroid[2]
        d = float(np.sqrt(w[0] * dh * dh + w[1] * ds * ds + w[2] * dv * dv))
        scores.append((color_id, d))

    scores.sort(key=lambda t: t[1])
    best_id, best_d = scores[0]
    second_d = scores[1][1] if len(scores) > 1 else best_d + 1.0
    # Confidence = (second - best) / (second + best), clamped to [0, 1].
    denom = second_d + best_d
    conf = (second_d - best_d) / denom if denom > 1e-6 else 0.0
    return best_id, float(np.clip(conf, 0.0, 1.0))


def color_name(color_id: int) -> str:
    return Color.Name(color_id)


# Display-friendly BGR draw colours for each ball.
_DRAW_BGR: dict[int, tuple[int, int, int]] = {
    Color.COLOR_UNKNOWN: (128, 128, 128),
    Color.RED:    (0, 0, 220),
    Color.YELLOW: (0, 220, 230),
    Color.GREEN:  (40, 180, 40),
    Color.BROWN:  (30, 60, 110),
    Color.BLUE:   (220, 100, 0),
    Color.PINK:   (200, 150, 230),
    Color.BLACK:  (20, 20, 20),
    Color.WHITE:  (240, 240, 240),
}


def draw_bgr(color_id: int) -> tuple[int, int, int]:
    return _DRAW_BGR.get(color_id, (128, 128, 128))
