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


def quad_passes_sanity(
    quad: np.ndarray, mask: np.ndarray, cfg: dict, *, check_area_ratio: bool = True
) -> bool:
    qcfg = cfg["quad"]
    pts = quad.astype(np.float32).reshape(-1, 1, 2)
    if not cv2.isContourConvex(pts):
        return False
    area_quad = cv2.contourArea(pts)
    area_mask = float(mask.sum())
    if area_mask < qcfg["min_mask_area_px"]:
        return False
    if check_area_ratio:
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
    if not short_idx:
        return None
    if len(short_idx) > 1:
        # Multiple parallel-to-midline candidates: keep the one farthest from
        # the midline (the genuine outer edge; the others are likely artefacts).
        a_m, b_m, c_m = midline
        dists = []
        for i in short_idx:
            L_i = three_lines[i][0]
            p = np.array([-L_i[2] * L_i[0], -L_i[2] * L_i[1]])
            dists.append(abs(a_m * p[0] + b_m * p[1] + c_m))
        si = short_idx[int(np.argmax(dists))]
    else:
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
    """The midline must cross through the table quad — i.e. some quad vertices
    lie on each side of the (infinite) midline. This is the perspective-safe
    invariant: 'midline parallel to short edges' fails because short edges
    converge at a vanishing point.
    """
    d = signed_distance_to_line(quad, midline_L)
    return float(d.min()) < 0 < float(d.max())


def _reflect_points(pts: np.ndarray, L: tuple[float, float, float]) -> np.ndarray:
    """Reflect (N, 2) points across line ax+by+c=0 (a^2+b^2=1)."""
    a, b, c = L
    d = pts[:, 0] * a + pts[:, 1] * b + c
    return pts - 2.0 * d[:, None] * np.array([a, b])[None, :]


def _identify_midline_edge(quad: np.ndarray, net_mask: np.ndarray) -> int:
    """Return the index i of the quad edge (i, i+1) closest to the net mask.
    That edge sits on the table midline (= the boundary between the visible
    half and the missing far half)."""
    if not net_mask.any():
        return -1
    ys, xs = np.where(net_mask)
    net_centroid = np.array([float(xs.mean()), float(ys.mean())])
    best_i, best_d = -1, float("inf")
    for i in range(4):
        mid = (quad[i] + quad[(i + 1) % 4]) / 2.0
        d = float(np.linalg.norm(mid - net_centroid))
        if d < best_d:
            best_d, best_i = d, i
    return best_i


def expand_half_to_full_table(
    quad: np.ndarray, net_mask: np.ndarray | None
) -> tuple[np.ndarray, bool]:
    """Expand a one-half-of-the-table quad to a full-table quad by reflecting
    the outer (non-midline) edge through the midline (= the quad edge nearest
    the net). Returns (full_quad, was_expanded).

    The visible mask is one half of the table top (~1.37m × 1.525m). The quad
    fitted to that mask has 4 edges: one runs along the midline (where the
    net sits) and the opposite edge runs along the outer short edge of the
    half. Reflecting that outer edge through the midline edge yields the
    outer short edge of the missing far half, and we combine them into the
    full-table quad.
    """
    if net_mask is None or not net_mask.any():
        return quad, False
    mid_i = _identify_midline_edge(quad, net_mask)
    if mid_i < 0:
        return quad, False
    # Quad ordering: index mid_i and mid_i+1 = the midline edge endpoints.
    # mid_i+2 and mid_i+3 = outer-end corners. Long edges connect (mid_i ↔
    # mid_i+3) and (mid_i+1 ↔ mid_i+2).
    A = quad[mid_i]                       # midline corner, side L
    B = quad[(mid_i + 1) % 4]              # midline corner, side R
    C = quad[(mid_i + 2) % 4]              # outer corner, side R (opposite of B)
    D = quad[(mid_i + 3) % 4]              # outer corner, side L (opposite of A)
    try:
        midline_L = line_from_two_points(A, B)
    except ValueError:
        return quad, False
    # Gate: only expand if the net centroid is close to the midline edge
    # (relative to the quad's short dimension). If the net is near the
    # middle — i.e. input is already a full table — leave it.
    ys, xs = np.where(net_mask)
    net_centroid = np.array([float(xs.mean()), float(ys.mean())])
    perp_dist = abs(
        midline_L[0] * net_centroid[0]
        + midline_L[1] * net_centroid[1]
        + midline_L[2]
    )
    edges = [np.linalg.norm(quad[(i + 1) % 4] - quad[i]) for i in range(4)]
    short_dim = min(edges)
    if perp_dist > short_dim * 0.25:
        return quad, False
    # Compute the vanishing point V = intersection of the two long edges
    # (D-A and C-B). Both long edges of the near half lie along table-3D
    # directions perpendicular to midline; in image they converge at V.
    try:
        long_L = line_from_two_points(D, A)
        long_R = line_from_two_points(C, B)
    except ValueError:
        return quad, False
    V = line_intersection(long_L, long_R)
    # Cross-ratio extension: for points colinear with V, equal 3D spacing
    # corresponds to equal spacing of 1/|P-V| in image. The far outer corners
    # D' and C' satisfy 1/|D'-V| = 2/|A-V| - 1/|D-V| (and analogously for C').
    if V is not None:
        Vp = np.array(V)
        far = []
        for outer, mid in [(D, A), (C, B)]:
            r0 = float(np.linalg.norm(outer - Vp))
            r1 = float(np.linalg.norm(mid - Vp))
            denom = 2.0 / r1 - 1.0 / r0
            if denom <= 1e-6:
                # Vanishing point at infinity (parallel long edges); fall back
                # to 2D reflection.
                far.append(_reflect_points(outer.reshape(1, 2), midline_L)[0])
                continue
            r2 = 1.0 / denom
            direction = (mid - Vp) / r1
            far.append(Vp + r2 * direction)
        D_far, C_far = far[0], far[1]
    else:
        # Long edges parallel — vanishing point at infinity, 2D reflection
        # is exact.
        outer = np.array([D, C])
        ref = _reflect_points(outer, midline_L)
        D_far, C_far = ref[0], ref[1]
    full = np.array([D, C, C_far, D_far])
    return _ccw_from_top_left(full), True


def _approx_quad_from_mask(mask: np.ndarray) -> np.ndarray | None:
    """Try to extract a 4-corner polygon from the largest contour's convex hull
    via approxPolyDP with an epsilon sweep. Returns (4, 2) float64 CCW from
    top-left of bbox, or None if no 4-vertex approximation is found."""
    contours, _ = cv2.findContours(
        mask.astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE
    )
    if not contours:
        return None
    biggest = max(contours, key=cv2.contourArea)
    hull = cv2.convexHull(biggest)
    perim = cv2.arcLength(hull, True)
    if perim < 1.0:
        return None
    for eps_frac in (0.005, 0.01, 0.02, 0.03, 0.05, 0.08, 0.12, 0.18, 0.25):
        approx = cv2.approxPolyDP(hull, eps_frac * perim, True)
        if len(approx) == 4:
            pts = approx.reshape(-1, 2).astype(np.float64)
            return _ccw_from_top_left(pts)
        if len(approx) < 4:
            return None
    return None


def fit_table_quadrilateral(
    table_mask: np.ndarray | None,
    net_mask: np.ndarray | None,
    *,
    cfg: dict,
) -> QuadResult:
    qcfg = cfg["quad"]
    if table_mask is None or table_mask.sum() < qcfg["min_mask_area_px"]:
        return QuadResult(METHOD_QUAD_FAILED, None, None, 0.0)

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

    # Primary: approxPolyDP on convex hull of largest contour. The midline
    # invariant is informational (not gating) at Stage 1 — perspective-safe
    # midline detection is too noisy on real frames; Stage 2 IPPE
    # disambiguation uses the midline against canonical geometry directly.
    cand = _approx_quad_from_mask(table_mask)
    if cand is not None and quad_passes_sanity(cand, table_mask, cfg):
        # If the segmentation only covers one half of the table, reflect
        # the outer corners through the midline to produce the full-table
        # quad. (SAM3 typically emits a single 'ping-pong table top' mask
        # for the half nearest the camera.) The midline is identified as
        # the quad edge closest to the net mask centroid.
        cand, _ = expand_half_to_full_table(cand, net_mask)
        quad = cand
        method = METHOD_QUAD_FULL

    # Recovery: 3-line RANSAC + midline reflection.
    if quad is None and midline_L is not None:
        pts, ang = extract_edge_points(table_mask)
        if len(pts) >= qcfg["ransac_min_inliers"]:
            rng = np.random.default_rng(13)
            lines = ransac_lines(
                pts, ang,
                k=4,
                distance_threshold=qcfg["ransac_distance_threshold_px"],
                angle_threshold_rad=math.radians(qcfg["ransac_angle_threshold_deg"]),
                min_inliers=qcfg["ransac_min_inliers"],
                max_iterations=qcfg["ransac_max_iterations"],
                rng=rng,
            )
            if len(lines) >= 3:
                # Filter lines geometrically against midline: keep the
                # parallel-to-midline candidate farthest from midline (the
                # genuine outer short edge) plus the 2 strongest
                # perpendicular-to-midline lines (the long edges).
                mid_ang = _line_angle_mod_pi(midline_L)
                par_thresh = math.radians(qcfg["parallel_threshold_deg"])
                a_m, b_m, c_m = midline_L
                parallel: list[tuple[int, float]] = []
                perpendicular: list[tuple[int, int]] = []
                for i, (L, ix) in enumerate(lines):
                    a_i = _line_angle_mod_pi(L)
                    diff = _angle_diff_mod_pi(a_i, mid_ang)
                    if diff < par_thresh:
                        p = np.array([-L[2] * L[0], -L[2] * L[1]])
                        d = abs(a_m * p[0] + b_m * p[1] + c_m)
                        parallel.append((i, d))
                    elif abs(diff - math.pi / 2) < par_thresh:
                        perpendicular.append((i, len(ix)))
                if parallel and len(perpendicular) >= 2:
                    short_i = max(parallel, key=lambda x: x[1])[0]
                    long_i = sorted(perpendicular, key=lambda x: -x[1])[:2]
                    chosen = [lines[short_i]] + [lines[j] for j, _ in long_i]
                    synth = synthesise_missing_short_edge(chosen, midline_L, cfg=cfg)
                    if synth is not None:
                        pairs = cluster_into_two_pairs(synth)
                        if pairs is not None:
                            rcand = quad_from_two_pairs(synth, pairs[0], pairs[1])
                            if rcand is not None and quad_passes_sanity(
                                rcand, table_mask, cfg, check_area_ratio=False
                            ):
                                quad = rcand
                                method = METHOD_QUAD_FROM_MIDLINE

    if quad is None:
        return QuadResult(METHOD_QUAD_FAILED, None, midline_pts, 0.0)

    quality = 0.7 + 0.3 * midline_q
    return QuadResult(method, quad, midline_pts, float(quality))
