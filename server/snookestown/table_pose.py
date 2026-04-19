"""Estimate per-frame table pose: homography from image pixels → table millimetres.

Input: detected cushions and pockets from pockets.detect_boundary(), plus known
canonical table dimensions (W, H) in mm.

The six canonical pocket positions are:
    CORNER: (0, 0), (W, 0), (W, H), (0, H)
    MIDDLE: (W/2, 0), (W/2, H)

A detected pocket's kind (CORNER vs MIDDLE) constrains which canonical slots
are eligible, narrowing the assignment search massively. We enumerate all
kind-consistent assignments, solve H for each via DLT, and pick the one whose
reprojected table rectangle best fits the actual table-mask contour.

When fewer than 4 pockets are detected, we augment the constraint set with
cushion-line intersections (virtual corners at the junction of two cushions).
"""
from __future__ import annotations

import itertools
import sys
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

_server_root = Path(__file__).resolve().parent.parent
_project_root = _server_root.parent
sys.path.insert(0, str(_project_root))
sys.path.insert(0, str(_project_root / "proto"))
sys.path.insert(0, str(_server_root))

from proto import snookestown_pb2

from snookestown.pockets import BoundaryResult, Cushion, Pocket, compute_hull_quad, detect_boundary

Method = snookestown_pb2.SnookerResponse.TablePose.Method
PocketKind = snookestown_pb2.SnookerResponse.TablePose.DetectedPocket.Kind


@dataclass
class TablePoseResult:
    H_img_to_table: np.ndarray | None      # 3×3, None if FAILED
    method: int                            # Method enum
    quality: float                         # 0..1
    pockets: list[Pocket]                  # input pockets, with assigned_* fields filled when known
    assigned_mm: list[tuple[float, float]] # per-pocket assigned (x_mm, y_mm) or (0, 0) if unassigned
    cushions: list[Cushion]
    hull_quad: np.ndarray | None = None    # (M, 2) float32: convex hull of visible table mask


def canonical_pockets_mm(W: float, H: float) -> list[tuple[float, float, int]]:
    """All 6 canonical snooker pocket positions in table-mm with kinds."""
    return [
        (0.0, 0.0, PocketKind.CORNER),
        (W,   0.0, PocketKind.CORNER),
        (W,   H,   PocketKind.CORNER),
        (0.0, H,   PocketKind.CORNER),
        (W / 2, 0.0, PocketKind.MIDDLE),
        (W / 2, H,   PocketKind.MIDDLE),
    ]


# ── Scoring helpers ──────────────────────────────────────────────────────────

def _fill_poly_mask(shape: tuple[int, int], poly: np.ndarray) -> np.ndarray:
    """Rasterise a polygon into a uint8 binary mask of given (H, W) shape."""
    m = np.zeros(shape, dtype=np.uint8)
    pts = poly.reshape(-1, 1, 2).astype(np.int32)
    cv2.fillPoly(m, [pts], 1)
    return m


def _clip_polygon_to_rect(poly: np.ndarray, w: int, h: int) -> np.ndarray | None:
    """Sutherland-Hodgman clip of a convex polygon to the image rectangle [0,w)×[0,h).

    `poly` is (N, 2) float. Returns clipped (M, 2) float or None if empty.
    """
    def _clip_edge(pts, x0, y0, x1, y1):
        """Clip a list of 2D points against one half-plane defined by (p0→p1, left side)."""
        out = []
        if not pts:
            return out
        def inside(p):
            return (x1 - x0) * (p[1] - y0) - (y1 - y0) * (p[0] - x0) >= 0
        def intersect(a, b):
            dx, dy = b[0] - a[0], b[1] - a[1]
            ex, ey = x1 - x0, y1 - y0
            denom = dx * ey - dy * ex
            if abs(denom) < 1e-10:
                return a
            t = ((x0 - a[0]) * ey - (y0 - a[1]) * ex) / denom
            return [a[0] + t * dx, a[1] + t * dy]
        n = len(pts)
        for i in range(n):
            a, b = pts[i], pts[(i + 1) % n]
            ia, ib = inside(a), inside(b)
            if ia:
                out.append(a)
            if ia != ib:
                out.append(intersect(a, b))
        return out

    pts = [[float(p[0]), float(p[1])] for p in poly]
    pts = _clip_edge(pts, 0,   0,   w,   0  )   # bottom edge  y>=0
    pts = _clip_edge(pts, w,   0,   w,   h  )   # right  edge  x<=w
    pts = _clip_edge(pts, w,   h,   0,   h  )   # top    edge  y<=h
    pts = _clip_edge(pts, 0,   h,   0,   0  )   # left   edge  x>=0
    if len(pts) < 3:
        return None
    return np.array(pts, dtype=np.float32)


def _score_assignment(
    H: np.ndarray,
    table_W: float, table_H: float,
    detected: list[Pocket],
    assigned_mm: list[tuple[float, float]],
    table_mask: np.ndarray,
    valid_region: np.ndarray | None = None,
    hull_quad: np.ndarray | None = None,
    any_mask: np.ndarray | None = None,
) -> float:
    """Score a candidate H.  Higher is better; range roughly [0, 3].

    Primary signal: IoU between
      • the back-projected table rectangle clipped to the frame boundary
      • the convex hull of the observed table mask pixels (hull_quad)
    This is correct because:
      - The table may extend beyond the frame, so we drop the part of the
        projection that falls outside and do not penalise it.
      - Any in-frame projection pixel that is NOT in the hull_quad IS penalised
        (those are pixels we can see and they don't look like table).
      - Balls and persons sitting on the table are inside hull_quad, so they
        are not penalised either.
    Fallback when hull_quad is unavailable: compare against table_mask directly.
    """
    try:
        H_inv = np.linalg.inv(H)
    except np.linalg.LinAlgError:
        return -1.0

    score = 0.0
    h_img, w_img = table_mask.shape

    # (a) Pocket reprojection residual.
    if detected and assigned_mm:
        img_pts = np.array([[p.x_img, p.y_img, 1.0] for p in detected])
        proj = img_pts @ H.T
        proj = proj[:, :2] / proj[:, 2:3]
        target = np.array(assigned_mm)
        diag = float(np.hypot(table_W, table_H))
        resid = np.linalg.norm(proj - target, axis=1).mean()
        score += max(0.0, 1.0 - resid / (0.05 * diag))

    # (b) Back-project the table rectangle corners into image space.
    corners_mm = np.array([
        [0.0, 0.0, 1.0],
        [table_W, 0.0, 1.0],
        [table_W, table_H, 1.0],
        [0.0, table_H, 1.0],
    ])
    corners_img = (H_inv @ corners_mm.T).T
    if np.any(np.abs(corners_img[:, 2]) < 1e-9):
        return -1.0
    corners_img = corners_img[:, :2] / corners_img[:, 2:3]

    if not _is_valid_quad(corners_img, (h_img, w_img)):
        return -1.0

    # Clip the back-projected rectangle to the frame.
    proj_clipped = _clip_polygon_to_rect(corners_img, w_img, h_img)
    if proj_clipped is None:
        return -1.0

    # Rasterise both the clipped projection and the reference polygon.
    proj_mask = _fill_poly_mask((h_img, w_img), proj_clipped)

    if hull_quad is not None:
        ref_mask = _fill_poly_mask((h_img, w_img), hull_quad)
    else:
        # Fallback: use table_mask itself, masked to valid_region.
        ref_mask = table_mask.astype(np.uint8)
        if valid_region is not None:
            ref_mask = ref_mask & valid_region.astype(np.uint8)

    proj_area = int(proj_mask.sum())
    ref_area  = int(ref_mask.sum())
    if proj_area == 0 or ref_area == 0:
        return -1.0

    intersection = int((proj_mask & ref_mask).sum())
    union        = proj_area + ref_area - intersection
    iou = intersection / max(union, 1)
    score += 2.0 * iou   # primary signal, weight 2

    # Penalty: fraction of the in-frame projection that has NO label at all.
    # Pixels with any mask (table/ball/cue/person) are fine; truly empty
    # background pixels inside the projection are penalised.
    if any_mask is not None and any_mask.shape == table_mask.shape:
        empty_in_proj = int((proj_mask & ~any_mask.astype(np.uint8)).sum())
        empty_frac = empty_in_proj / max(proj_area, 1)
        score -= 0.5 * empty_frac

    # (c) Small bonus for canonical pockets reprojecting inside the frame.
    can_pockets_mm = np.array([
        [0.0, 0.0, 1.0], [table_W, 0.0, 1.0],
        [table_W, table_H, 1.0], [0.0, table_H, 1.0],
        [table_W / 2, 0.0, 1.0], [table_W / 2, table_H, 1.0],
    ])
    cp_img = (H_inv @ can_pockets_mm.T).T
    cp_img = cp_img[:, :2] / cp_img[:, 2:3]
    in_bounds = (
        (cp_img[:, 0] >= 0) & (cp_img[:, 0] < w_img)
        & (cp_img[:, 1] >= 0) & (cp_img[:, 1] < h_img)
    )
    score += float(in_bounds.sum()) / 12.0

    return score


def _is_valid_quad(corners: np.ndarray, shape: tuple[int, int]) -> bool:
    h, w = shape
    # All corners within a generous margin of the image bounds.
    margin = max(h, w)  # allow offscreen corners (partial-view case)
    if (corners[:, 0].min() < -margin or corners[:, 0].max() > w + margin
        or corners[:, 1].min() < -margin or corners[:, 1].max() > h + margin):
        return False
    # Must be convex and non-self-intersecting: use sign of cross products.
    cross_signs = []
    for i in range(4):
        a = corners[i]
        b = corners[(i + 1) % 4]
        c = corners[(i + 2) % 4]
        cross = (b[0] - a[0]) * (c[1] - b[1]) - (b[1] - a[1]) * (c[0] - b[0])
        if abs(cross) < 1.0:
            return False
        cross_signs.append(np.sign(cross))
    return len(set(cross_signs)) == 1


# ── Assignment enumeration ───────────────────────────────────────────────────

def _enumerate_assignments(
    detected: list[Pocket],
    canon: list[tuple[float, float, int]],
    max_to_consider: int = 6,
) -> list[list[int]]:
    """Enumerate kind-consistent slot assignments.

    Returns a list of index-lists: result[i][j] = canonical index assigned to
    detected pocket j (or -1 for "unassigned").
    """
    # Cap the detected list to at most 6 (more than the table has).
    detected = detected[:max_to_consider]
    n = len(detected)
    if n == 0:
        return []

    # Pre-compute eligible canonical slots per detection.
    eligible: list[list[int]] = []
    for p in detected:
        slots = [i for i, c in enumerate(canon) if c[2] == p.kind]
        if not slots:
            # Unknown kind — allow any slot.
            slots = list(range(len(canon)))
        eligible.append(slots)

    results: list[list[int]] = []

    def recurse(i: int, used: set[int], acc: list[int]) -> None:
        if i == n:
            results.append(acc.copy())
            return
        for slot in eligible[i]:
            if slot in used:
                continue
            acc.append(slot)
            used.add(slot)
            recurse(i + 1, used, acc)
            used.remove(slot)
            acc.pop()

    recurse(0, set(), [])
    return results


# ── Main entry ───────────────────────────────────────────────────────────────

def estimate_table_pose(
    table_mask: np.ndarray,
    cfg: dict,
    boundary: BoundaryResult | None = None,
    valid_region: np.ndarray | None = None,
    raw_table_mask: np.ndarray | None = None,
    hull_quad_override: np.ndarray | None = None,
    any_mask: np.ndarray | None = None,
) -> TablePoseResult:
    """Estimate H from image pixels to table-mm.

    `hull_quad_override`: pre-computed, temporally-smoothed hull quad from the
    caller. Overrides per-frame hull computation; used as the scoring reference.
    `any_mask`: union of all label masks (table|ball|cue|person). Pixels inside
    the projected rectangle that have no label are penalised in scoring.
    """
    W = float(cfg["table"]["width_mm"])
    H = float(cfg["table"]["height_mm"])

    # Hull quad: caller supplies a temporally-smoothed one; fall back to
    # per-frame computation only when not provided.
    if hull_quad_override is not None:
        hull_quad = hull_quad_override
    else:
        hull_mask = raw_table_mask if raw_table_mask is not None else table_mask
        hull_quad = compute_hull_quad(hull_mask)

    if boundary is None:
        boundary = detect_boundary(table_mask, cfg)
    if boundary is None:
        return TablePoseResult(
            H_img_to_table=None, method=Method.FAILED, quality=0.0,
            pockets=[], assigned_mm=[], cushions=[], hull_quad=hull_quad,
        )
    boundary.hull_quad = hull_quad

    canon = canonical_pockets_mm(W, H)

    quad_best = _solve_from_quad(boundary, table_mask, W, H, valid_region, any_mask)

    pocket_best: TablePoseResult | None = None
    if len(boundary.pockets) >= 4:
        pocket_best = _solve_from_pockets(
            boundary, canon, table_mask, W, H,
            method_if_good=Method.POCKET_FULL,
            valid_region=valid_region, any_mask=any_mask,
        )
    elif len(boundary.pockets) >= 2:
        pocket_best = _solve_from_pockets(
            boundary, canon, table_mask, W, H,
            method_if_good=Method.POCKET_PARTIAL,
            valid_region=valid_region, any_mask=any_mask,
        )

    # Pick the candidate with the highest quality score.
    candidates = [c for c in (pocket_best, quad_best) if c is not None
                  and c.H_img_to_table is not None]
    if candidates:
        return max(candidates, key=lambda r: r.quality)

    return _solve_from_min_rect(boundary, table_mask, W, H, valid_region=valid_region)


def _solve_from_pockets(
    boundary: BoundaryResult,
    canon: list[tuple[float, float, int]],
    table_mask: np.ndarray,
    W: float, H: float,
    method_if_good: int,
    valid_region: np.ndarray | None = None,
    any_mask: np.ndarray | None = None,
) -> TablePoseResult:
    detected = boundary.pockets
    assignments = _enumerate_assignments(detected, canon)

    best: tuple[float, np.ndarray, list[tuple[float, float]]] | None = None

    for assignment in assignments:
        # Need at least 4 point correspondences for a homography.
        # If we have fewer pockets than 4, skip here — _solve_from_pockets
        # handles that via the augment-with-rect path below.
        if len(detected) < 4:
            break
        img_pts = np.array(
            [[detected[j].x_img, detected[j].y_img] for j in range(len(detected))],
            dtype=np.float32,
        )
        mm_pts = np.array(
            [canon[assignment[j]][:2] for j in range(len(detected))],
            dtype=np.float32,
        )
        H_mat, _ = cv2.findHomography(img_pts, mm_pts, method=0)
        if H_mat is None:
            continue
        assigned_mm = [canon[a][:2] for a in assignment]
        score = _score_assignment(
            H_mat, W, H, detected, assigned_mm, table_mask, valid_region,
            hull_quad=boundary.hull_quad, any_mask=any_mask,
        )
        if best is None or score > best[0]:
            best = (score, H_mat, assigned_mm)

    if best is None and 2 <= len(detected) < 4:
        best = _solve_with_rect_augment(
            boundary, canon, table_mask, W, H, valid_region, any_mask,
        )

    if best is None or best[0] < 0.3:
        return _solve_from_min_rect(boundary, table_mask, W, H, valid_region=valid_region,
                                    any_mask=any_mask)

    score, H_mat, assigned_mm = best
    return TablePoseResult(
        H_img_to_table=H_mat.astype(np.float64),
        method=method_if_good,
        quality=float(np.clip(score / 3.0, 0.0, 1.0)),
        pockets=boundary.pockets,
        assigned_mm=assigned_mm,
        cushions=boundary.cushions,
        hull_quad=boundary.hull_quad,
    )


def _solve_with_rect_augment(
    boundary: BoundaryResult,
    canon: list[tuple[float, float, int]],
    table_mask: np.ndarray,
    W: float, H: float,
    valid_region: np.ndarray | None = None,
    any_mask: np.ndarray | None = None,
) -> tuple[float, np.ndarray, list[tuple[float, float]]] | None:
    """When <4 pockets, use minAreaRect corners to fill in virtual constraints.

    Strategy: compute minAreaRect on the table mask contour, enumerate all
    orientations of that rect matched to (0,0,W,0,W,H,0,H) AND pocket
    assignments, pick best.
    """
    contour = boundary.contour
    if len(contour) < 4:
        return None
    rect = cv2.minAreaRect(contour.astype(np.float32))
    box_img = cv2.boxPoints(rect)  # 4x2

    # Reorder box_img to start at top-left of the mask centroid (deterministic).
    cx = box_img[:, 0].mean()
    cy = box_img[:, 1].mean()
    # Classify each corner by quadrant relative to centroid.
    def angle(p):
        return np.arctan2(p[1] - cy, p[0] - cx)
    order = sorted(range(4), key=lambda i: angle(box_img[i]))
    box_img = box_img[order]

    rect_canon_variants = [
        [(0.0, 0.0), (W, 0.0), (W, H), (0.0, H)],
        [(W, 0.0), (W, H), (0.0, H), (0.0, 0.0)],
        [(W, H), (0.0, H), (0.0, 0.0), (W, 0.0)],
        [(0.0, H), (0.0, 0.0), (W, 0.0), (W, H)],
        # Also flipped orientations (swap diagonals).
        [(0.0, 0.0), (0.0, H), (W, H), (W, 0.0)],
        [(W, 0.0), (0.0, 0.0), (0.0, H), (W, H)],
        [(W, H), (W, 0.0), (0.0, 0.0), (0.0, H)],
        [(0.0, H), (W, H), (W, 0.0), (0.0, 0.0)],
    ]

    detected = boundary.pockets
    best: tuple[float, np.ndarray, list[tuple[float, float]]] | None = None
    pocket_assignments = (
        _enumerate_assignments(detected, canon) if detected else [[]]
    )

    for variant in rect_canon_variants:
        for pa in pocket_assignments:
            mm_pts = list(variant)
            img_pts = [tuple(p) for p in box_img]
            for j, slot in enumerate(pa):
                img_pts.append((detected[j].x_img, detected[j].y_img))
                mm_pts.append((canon[slot][0], canon[slot][1]))
            img_arr = np.array(img_pts, dtype=np.float32)
            mm_arr = np.array(mm_pts, dtype=np.float32)
            if len(img_arr) < 4:
                continue
            H_mat, _ = cv2.findHomography(img_arr, mm_arr, method=0)
            if H_mat is None:
                continue
            assigned_mm = [canon[a][:2] for a in pa]
            score = _score_assignment(
                H_mat, W, H, detected, assigned_mm, table_mask, valid_region,
                hull_quad=boundary.hull_quad, any_mask=any_mask,
            )
            if best is None or score > best[0]:
                best = (score, H_mat, assigned_mm)
    return best


def _fit_quad_to_contour(contour: np.ndarray) -> np.ndarray | None:
    """Fit a convex 4-sided polygon to `contour` via adaptive approxPolyDP.

    Returns a (4, 2) float array of corner pixels, ordered counter-clockwise
    from the top-left-ish corner, or None if no 4-gon fits.
    """
    if len(contour) < 4:
        return None
    c = contour.astype(np.float32).reshape(-1, 1, 2)
    perim = float(cv2.arcLength(c, True))
    # Binary-search epsilon so approxPolyDP returns exactly 4 vertices.
    lo, hi = 0.001 * perim, 0.15 * perim
    best: np.ndarray | None = None
    for _ in range(25):
        mid = 0.5 * (lo + hi)
        approx = cv2.approxPolyDP(c, mid, True).reshape(-1, 2)
        n = len(approx)
        if n == 4:
            best = approx
            break
        if n > 4:
            lo = mid
        else:
            hi = mid
    if best is None:
        # Fallback: take the convex hull of the contour then approxPolyDP it.
        hull = cv2.convexHull(c).reshape(-1, 1, 2)
        hperim = float(cv2.arcLength(hull, True))
        lo, hi = 0.001 * hperim, 0.2 * hperim
        for _ in range(25):
            mid = 0.5 * (lo + hi)
            approx = cv2.approxPolyDP(hull, mid, True).reshape(-1, 2)
            n = len(approx)
            if n == 4:
                best = approx
                break
            if n > 4:
                lo = mid
            else:
                hi = mid
    if best is None:
        return None

    # Order counter-clockwise around centroid, starting near the top-left.
    cx = float(best[:, 0].mean())
    cy = float(best[:, 1].mean())
    angles = np.arctan2(best[:, 1] - cy, best[:, 0] - cx)
    order = np.argsort(angles)
    best = best[order]
    return best.astype(np.float32)


def _solve_from_quad(
    boundary: BoundaryResult,
    table_mask: np.ndarray,
    W: float, H: float,
    valid_region: np.ndarray | None,
    any_mask: np.ndarray | None = None,
) -> TablePoseResult | None:
    """Fit a 4-corner quadrilateral to the table contour and pick the ordering
    that best matches the canonical rectangle via the scoring function."""
    quad = _fit_quad_to_contour(boundary.contour)
    if quad is None:
        return None

    # Enumerate all 4 cyclic starts × 2 orientations (CCW / mirrored) = 8 maps
    # of the 4 image corners to canonical (0,0), (W,0), (W,H), (0,H).
    canon4 = np.array([
        [0.0, 0.0], [W, 0.0], [W, H], [0.0, H],
    ], dtype=np.float32)

    best: tuple[float, np.ndarray] | None = None
    for flip in (False, True):
        pts = quad[::-1] if flip else quad
        for roll in range(4):
            img_pts = np.roll(pts, -roll, axis=0)
            H_mat, _ = cv2.findHomography(img_pts, canon4, method=0)
            if H_mat is None:
                continue
            score = _score_assignment(H_mat, W, H, [], [], table_mask, valid_region,
                                      hull_quad=boundary.hull_quad, any_mask=any_mask)
            if best is None or score > best[0]:
                best = (score, H_mat)

    if best is None or best[0] < 0.3:
        return None
    score, H_mat = best
    return TablePoseResult(
        H_img_to_table=H_mat.astype(np.float64),
        method=Method.EDGE_ONLY,
        quality=float(np.clip(score / 3.0, 0.0, 1.0)),
        pockets=boundary.pockets,
        assigned_mm=[],
        cushions=boundary.cushions,
        hull_quad=boundary.hull_quad,
    )


def _solve_from_min_rect(
    boundary: BoundaryResult,
    table_mask: np.ndarray,
    W: float, H: float,
    valid_region: np.ndarray | None = None,
    any_mask: np.ndarray | None = None,
) -> TablePoseResult:
    contour = boundary.contour
    if len(contour) < 4:
        return TablePoseResult(
            H_img_to_table=None, method=Method.FAILED, quality=0.0,
            pockets=boundary.pockets, assigned_mm=[], cushions=boundary.cushions,
        )
    rect = cv2.minAreaRect(contour.astype(np.float32))
    box_img = cv2.boxPoints(rect)
    # Order corners around centroid for a deterministic mapping (no orientation lock).
    cx = box_img[:, 0].mean()
    cy = box_img[:, 1].mean()
    order = sorted(range(4), key=lambda i: np.arctan2(box_img[i, 1] - cy, box_img[i, 0] - cx))
    box_img = box_img[order]

    # Decide which rectangle dimension is the LONG one based on edge lengths.
    e0 = np.linalg.norm(box_img[1] - box_img[0])
    e1 = np.linalg.norm(box_img[2] - box_img[1])
    # Always map the long edge to the table width (W). The long edges of a
    # standard rectangle alternate, so check edge lengths.
    if e0 >= e1:
        # box_img[0]→[1] is long → map to (0,0)→(W,0)
        mm_pts = np.array([
            [0.0, 0.0], [W, 0.0], [W, H], [0.0, H],
        ], dtype=np.float32)
    else:
        # box_img[0]→[1] is short → rotate by one
        mm_pts = np.array([
            [0.0, H], [0.0, 0.0], [W, 0.0], [W, H],
        ], dtype=np.float32)

    H_mat, _ = cv2.findHomography(box_img, mm_pts, method=0)
    if H_mat is None:
        return TablePoseResult(
            H_img_to_table=None, method=Method.FAILED, quality=0.0,
            pockets=boundary.pockets, assigned_mm=[], cushions=boundary.cushions,
        )
    score = _score_assignment(H_mat, W, H, [], [], table_mask, valid_region,
                              hull_quad=boundary.hull_quad, any_mask=any_mask)
    return TablePoseResult(
        H_img_to_table=H_mat.astype(np.float64),
        method=Method.EDGE_ONLY,
        quality=float(np.clip(score / 3.0, 0.0, 1.0)),
        pockets=boundary.pockets,
        assigned_mm=[],
        cushions=boundary.cushions,
        hull_quad=boundary.hull_quad,
    )
