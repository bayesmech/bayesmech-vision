"""Stage 1: per-frame ping-pong table quadrilateral via 4-line RANSAC."""
from __future__ import annotations

import math
from dataclasses import dataclass

import cv2
import numpy as np

from pongtown.geometry import (
    line_from_two_points,
    line_intersection,
    point_line_distance,
    signed_distance_to_line,
)


# Method enum values mirror PongtownResponse.TablePose.Method.
METHOD_UNKNOWN = 0
METHOD_QUAD_FULL = 1
METHOD_QUAD_FROM_MIDLINE = 2
METHOD_QUAD_FAILED = 3


@dataclass
class QuadResult:
    method: int
    quad_img: np.ndarray | None
    midline_img: np.ndarray | None
    quality: float


def extract_edge_points(mask: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Return (pts, grad_angles_rad). pts: (N, 2) [x, y]."""
    m = mask.astype(np.uint8) * 255
    gx = cv2.Sobel(m, cv2.CV_32F, 1, 0, ksize=3)
    gy = cv2.Sobel(m, cv2.CV_32F, 0, 1, ksize=3)
    mag = np.hypot(gx, gy)
    if mag.size == 0:
        return np.zeros((0, 2)), np.zeros((0,))
    thresh = mag.max() * 0.25
    if thresh < 1e-3:
        return np.zeros((0, 2)), np.zeros((0,))
    ys, xs = np.where(mag > thresh)
    pts = np.column_stack([xs, ys]).astype(np.float64)
    angles = np.arctan2(gy[ys, xs], gx[ys, xs])
    return pts, angles


def _angle_diff_mod_pi(a: float, b: float) -> float:
    d = (a - b) % math.pi
    return min(d, math.pi - d)


def _fit_line_tls(pts: np.ndarray) -> tuple[float, float, float]:
    """Total-least-squares line fit via SVD."""
    centroid = pts.mean(axis=0)
    centered = pts - centroid
    _, _, vh = np.linalg.svd(centered, full_matrices=False)
    direction = vh[0]
    normal = np.array([-direction[1], direction[0]])
    a, b = normal
    c = -(a * centroid[0] + b * centroid[1])
    n = math.hypot(a, b)
    return float(a / n), float(b / n), float(c / n)


def ransac_lines(
    pts: np.ndarray,
    grad_angles: np.ndarray,
    *,
    k: int,
    distance_threshold: float,
    angle_threshold_rad: float,
    min_inliers: int,
    max_iterations: int,
    rng: np.random.Generator,
) -> list[tuple[tuple[float, float, float], np.ndarray]]:
    """Sequentially extract up to k lines."""
    available = np.ones(len(pts), dtype=bool)
    out: list[tuple[tuple[float, float, float], np.ndarray]] = []
    for _ in range(k):
        avail_idx = np.where(available)[0]
        if len(avail_idx) < min_inliers:
            break
        best_inliers: np.ndarray | None = None
        best_line: tuple[float, float, float] | None = None
        for _ in range(max_iterations):
            i, j = rng.choice(avail_idx, size=2, replace=False)
            if _angle_diff_mod_pi(grad_angles[i], grad_angles[j]) > angle_threshold_rad:
                continue
            try:
                L = line_from_two_points(pts[i], pts[j])
            except ValueError:
                continue
            d = point_line_distance(pts[avail_idx], L)
            ok_dist = d < distance_threshold
            normal_angle = math.atan2(L[1], L[0])
            ang_diffs = np.array(
                [_angle_diff_mod_pi(a, normal_angle) for a in grad_angles[avail_idx]]
            )
            ok_ang = ang_diffs < angle_threshold_rad
            inlier_mask = ok_dist & ok_ang
            if best_inliers is None or inlier_mask.sum() > best_inliers.sum():
                best_inliers = inlier_mask
                best_line = L
        if best_line is None or int(best_inliers.sum()) < min_inliers:
            break
        ix = avail_idx[best_inliers]
        L_refit = _fit_line_tls(pts[ix])
        out.append((L_refit, ix))
        available[ix] = False
    return out


def _line_angle_mod_pi(L: tuple[float, float, float]) -> float:
    """Angle of the line direction in [0, π)."""
    a, b, _ = L
    return math.atan2(a, -b) % math.pi


def cluster_into_two_pairs(
    lines: list[tuple[tuple[float, float, float], np.ndarray]],
) -> tuple[list[int], list[int]] | None:
    """Group 4 line indices into two opposite-side pairs by angle (mod π)."""
    if len(lines) != 4:
        return None
    angs = np.array([_line_angle_mod_pi(L) for L, _ in lines])
    best = None
    for combo in [(0, 1), (0, 2), (0, 3)]:
        a_idx = list(combo)
        b_idx = [i for i in range(4) if i not in a_idx]
        spread = (
            _angle_diff_mod_pi(angs[a_idx[0]], angs[a_idx[1]])
            + _angle_diff_mod_pi(angs[b_idx[0]], angs[b_idx[1]])
        )
        if best is None or spread < best[0]:
            best = (spread, a_idx, b_idx)
    if best is None or best[0] > math.radians(20):
        return None
    return best[1], best[2]


def quad_from_two_pairs(
    lines: list[tuple[tuple[float, float, float], np.ndarray]],
    pair_a: list[int],
    pair_b: list[int],
) -> np.ndarray | None:
    """Compute 4 corners (CCW from top-left of bbox) by pairwise intersection."""
    corners: list[tuple[float, float]] = []
    for i in pair_a:
        for j in pair_b:
            p = line_intersection(lines[i][0], lines[j][0])
            if p is None:
                return None
            corners.append(p)
    pts = np.array(corners, dtype=np.float64)
    return _ccw_from_top_left(pts)


def _ccw_from_top_left(pts: np.ndarray) -> np.ndarray:
    """Order 4 points CCW starting with the one closest to top-left of bbox."""
    centroid = pts.mean(axis=0)
    angles = np.arctan2(pts[:, 1] - centroid[1], pts[:, 0] - centroid[0])
    order = np.argsort(angles)
    pts_sorted = pts[order]
    start = int(np.argmin(pts_sorted[:, 0] + pts_sorted[:, 1]))
    return np.roll(pts_sorted, -start, axis=0)


def fit_net_midline(
    net_mask: np.ndarray, table_mask: np.ndarray, cfg: dict
) -> tuple[np.ndarray, float] | None:
    """Detect the bottom long edge of the net mask."""
    qcfg = cfg["quad"]
    pts, ang = extract_edge_points(net_mask)
    if len(pts) < qcfg["ransac_min_inliers"] // 2:
        return None
    rng = np.random.default_rng(7)
    lines = ransac_lines(
        pts, ang,
        k=4,
        distance_threshold=qcfg["ransac_distance_threshold_px"],
        angle_threshold_rad=math.radians(qcfg["ransac_angle_threshold_deg"]),
        min_inliers=qcfg["ransac_min_inliers"] // 2,
        max_iterations=qcfg["ransac_max_iterations"],
        rng=rng,
    )
    if len(lines) < 2:
        return None
    angs = np.array([_line_angle_mod_pi(L) for L, _ in lines])
    inlier_counts = np.array([len(ix) for _, ix in lines])
    ref = int(np.argmax(inlier_counts))
    ref_ang = angs[ref]
    long_idx = [
        i for i, a in enumerate(angs)
        if _angle_diff_mod_pi(a, ref_ang) < math.radians(qcfg["ransac_angle_threshold_deg"])
    ]
    if len(long_idx) < 2:
        return None
    table_h, table_w = table_mask.shape
    best_score = -1.0
    best: tuple[np.ndarray, float] | None = None
    for li in long_idx:
        L, ix = lines[li]
        pts_li = pts[ix]
        coords = pts_li.astype(int)
        inside = 0
        for x, y in coords:
            if 0 <= y < table_h and 0 <= x < table_w and table_mask[y, x]:
                inside += 1
        frac = inside / max(len(coords), 1)
        if frac > best_score:
            best_score = frac
            direction = np.array([-L[1], L[0]])
            t = pts_li @ direction
            i_min = int(np.argmin(t))
            i_max = int(np.argmax(t))
            best = (
                np.array([pts_li[i_min], pts_li[i_max]]),
                float(min(1.0, frac + 0.2)),
            )
    return best


def quad_passes_sanity(quad: np.ndarray, mask: np.ndarray, cfg: dict) -> bool:
    qcfg = cfg["quad"]
    pts = quad.astype(np.float32).reshape(-1, 1, 2)
    if not cv2.isContourConvex(pts):
        return False
    area_quad = cv2.contourArea(pts)
    area_mask = float(mask.sum())
    if area_mask < qcfg["min_mask_area_px"]:
        return False
    ratio = area_quad / area_mask
    if not (qcfg["area_ratio_min"] <= ratio <= qcfg["area_ratio_max"]):
        return False
    edges = [np.linalg.norm(quad[(i + 1) % 4] - quad[i]) for i in range(4)]
    long_edge = max(edges)
    short_edge = min(edges)
    if short_edge < 1.0:
        return False
    ar = long_edge / short_edge
    if not (qcfg["aspect_ratio_min"] <= ar <= qcfg["aspect_ratio_max"]):
        return False
    return True


def synthesise_missing_short_edge(
    three_lines: list[tuple[tuple[float, float, float], np.ndarray]],
    midline: tuple[float, float, float],
    *,
    cfg: dict,
) -> list[tuple[tuple[float, float, float], np.ndarray]] | None:
    """Reflect the visible short edge through the midline to recover the 4th line."""
    if len(three_lines) != 3:
        return None
    angs = np.array([_line_angle_mod_pi(L) for L, _ in three_lines])
    mid_ang = _line_angle_mod_pi(midline)
    short_idx = [
        i for i, a in enumerate(angs)
        if _angle_diff_mod_pi(a, mid_ang) < math.radians(cfg["quad"]["parallel_threshold_deg"])
    ]
    if len(short_idx) != 1:
        return None
    si = short_idx[0]
    Ls, _ = three_lines[si]
    a_m, b_m, c_m = midline
    a_s, b_s, c_s = Ls
    p = np.array([-c_s * a_s, -c_s * b_s])
    d = a_m * p[0] + b_m * p[1] + c_m
    p_ref = p - 2.0 * d * np.array([a_m, b_m])
    direction = np.array([-b_s, a_s])
    p2 = p_ref + direction
    L_ref = line_from_two_points(p_ref, p2)
    return list(three_lines) + [(L_ref, np.zeros(0, dtype=int))]


def _midline_to_line_params(midline_pts: np.ndarray) -> tuple[float, float, float]:
    return line_from_two_points(midline_pts[0], midline_pts[1])


def _midline_constraint_ok(quad: np.ndarray, midline_L, cfg: dict) -> bool:
    """Midline must be roughly parallel to the two short edges and bisect the long edges."""
    qcfg = cfg["quad"]
    edges = [(quad[i], quad[(i + 1) % 4]) for i in range(4)]
    edge_lengths = [np.linalg.norm(p2 - p1) for p1, p2 in edges]
    order = np.argsort(edge_lengths)
    short_a, short_b = order[0], order[1]
    short_angs = []
    for i in (short_a, short_b):
        L = line_from_two_points(edges[i][0], edges[i][1])
        short_angs.append(_line_angle_mod_pi(L))
    mid_ang = _line_angle_mod_pi(midline_L)
    parallel_thresh = math.radians(qcfg["parallel_threshold_deg"])
    if not all(_angle_diff_mod_pi(a, mid_ang) < parallel_thresh for a in short_angs):
        return False
    centres = []
    for i in (short_a, short_b):
        c = (edges[i][0] + edges[i][1]) / 2.0
        centres.append(c)
    d0 = signed_distance_to_line(np.array([centres[0]]), midline_L)[0]
    d1 = signed_distance_to_line(np.array([centres[1]]), midline_L)[0]
    if d0 * d1 >= 0:
        return False
    asymmetry = abs(abs(d0) - abs(d1)) / max(abs(d0) + abs(d1), 1.0)
    return asymmetry < qcfg["midline_equidistance_tolerance"]


def fit_table_quadrilateral(
    table_mask: np.ndarray | None,
    net_mask: np.ndarray | None,
    *,
    cfg: dict,
) -> QuadResult:
    qcfg = cfg["quad"]
    if table_mask is None or table_mask.sum() < qcfg["min_mask_area_px"]:
        return QuadResult(METHOD_QUAD_FAILED, None, None, 0.0)

    rng = np.random.default_rng(13)
    pts, ang = extract_edge_points(table_mask)
    if len(pts) < qcfg["ransac_min_inliers"]:
        return QuadResult(METHOD_QUAD_FAILED, None, None, 0.0)
    lines = ransac_lines(
        pts, ang,
        k=4,
        distance_threshold=qcfg["ransac_distance_threshold_px"],
        angle_threshold_rad=math.radians(qcfg["ransac_angle_threshold_deg"]),
        min_inliers=qcfg["ransac_min_inliers"],
        max_iterations=qcfg["ransac_max_iterations"],
        rng=rng,
    )

    midline_pts: np.ndarray | None = None
    midline_L: tuple[float, float, float] | None = None
    midline_q = 0.0
    if net_mask is not None and net_mask.any():
        ml = fit_net_midline(net_mask, table_mask, cfg)
        if ml is not None:
            midline_pts, midline_q = ml
            midline_L = _midline_to_line_params(midline_pts)

    method = METHOD_QUAD_FAILED
    quad: np.ndarray | None = None

    if len(lines) == 4:
        pairs = cluster_into_two_pairs(lines)
        if pairs is not None:
            cand = quad_from_two_pairs(lines, pairs[0], pairs[1])
            if cand is not None and quad_passes_sanity(cand, table_mask, cfg):
                if midline_L is None or _midline_constraint_ok(cand, midline_L, cfg):
                    quad = cand
                    method = METHOD_QUAD_FULL

    if quad is None and midline_L is not None and len(lines) >= 3:
        # Try with the strongest 3 lines (drop weakest if we have 4) +
        # midline reflection. Sort by inlier count descending.
        top3 = sorted(lines, key=lambda x: -len(x[1]))[:3]
        synth = synthesise_missing_short_edge(top3, midline_L, cfg=cfg)
        if synth is not None:
            pairs = cluster_into_two_pairs(synth)
            if pairs is not None:
                cand = quad_from_two_pairs(synth, pairs[0], pairs[1])
                if cand is not None and quad_passes_sanity(cand, table_mask, cfg):
                    quad = cand
                    method = METHOD_QUAD_FROM_MIDLINE

    if quad is None:
        return QuadResult(METHOD_QUAD_FAILED, None, midline_pts, 0.0)

    total_inliers = sum(len(ix) for _, ix in lines if len(ix) > 0)
    inlier_ratio = min(1.0, total_inliers / max(len(pts) * 0.5, 1.0))
    quality = 0.7 * inlier_ratio + 0.3 * midline_q
    return QuadResult(method, quad, midline_pts, float(quality))
