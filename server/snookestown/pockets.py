"""Extract cushion lines and pocket candidates from the table mask boundary.

SAM3 gives us a single `table` mask. Cushions are implicit — they're the parts
of the mask boundary that run in straight lines through the image interior
(i.e. not along the image border, which is camera clipping, not a real cushion).
Pockets appear as concave bites in that boundary.

Algorithm:
  1. Largest contour of the mask.
  2. Mark each contour vertex BORDER / INTERIOR by proximity to image edges.
  3. Compute convex hull and convexity defects. Each defect's "far point" is a
     pocket candidate — unless the far point is on the image border (mask cut
     off, not a real pocket) or the defect depth is below threshold.
  4. Cluster nearby pocket candidates.
  5. Between consecutive pockets (walking the contour), the remaining interior
     points lie on a single cushion — RANSAC-fit that line to get cushion
     endpoints.
  6. Classify each pocket as CORNER (flanking cushions at an angle) vs
     MIDDLE (flanking cushions are colinear — a middle pocket sits in the
     middle of one long cushion).
"""
from __future__ import annotations

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

PocketKind = snookestown_pb2.SnookerResponse.TablePose.DetectedPocket.Kind


@dataclass
class Cushion:
    """A fitted straight cushion segment in image coords."""
    p0: tuple[float, float]
    p1: tuple[float, float]
    direction: np.ndarray   # unit vector (dx, dy)
    support: int            # number of inlier contour points


@dataclass
class Pocket:
    x_img: float
    y_img: float
    kind: int               # PocketKind enum
    depth_px: float         # how far the concavity bites into the table
    left_cushion: int = -1  # index into cushions list
    right_cushion: int = -1


@dataclass
class BoundaryResult:
    cushions: list[Cushion]
    pockets: list[Pocket]
    contour: np.ndarray     # (N, 2) int32, the contour we analysed
    hull_quad: np.ndarray | None = None  # (M, 2) float32 convex hull of the mask, ≈4 sides


# ── Internals ─────────────────────────────────────────────────────────────────

def _largest_contour(mask: np.ndarray, min_area: int) -> np.ndarray | None:
    contours, _ = cv2.findContours(
        mask.astype(np.uint8),
        cv2.RETR_EXTERNAL,
        cv2.CHAIN_APPROX_NONE,
    )
    if not contours:
        return None
    c = max(contours, key=cv2.contourArea)
    if cv2.contourArea(c) < min_area:
        return None
    return c.reshape(-1, 2)


def _is_border(points: np.ndarray, shape: tuple[int, int], tol: int) -> np.ndarray:
    """Return bool array — True where point is within `tol` of any image edge."""
    h, w = shape
    x, y = points[:, 0], points[:, 1]
    return (x < tol) | (x > w - 1 - tol) | (y < tol) | (y > h - 1 - tol)


def _fit_line_ransac(points: np.ndarray, inlier_px: float) -> tuple[np.ndarray, int] | None:
    """RANSAC-fit a line to (N, 2) points.

    Returns ((vx, vy, x0, y0) à la cv2.fitLine, inlier_count) or None if too few.
    """
    if len(points) < 4:
        return None
    line = cv2.fitLine(points.astype(np.float32), cv2.DIST_L2, 0, 0.01, 0.01)
    vx, vy, x0, y0 = float(line[0]), float(line[1]), float(line[2]), float(line[3])
    # Perpendicular distance of each point to the line.
    dx = points[:, 0] - x0
    dy = points[:, 1] - y0
    # perpendicular = cross(direction, diff)
    perp = np.abs(dx * vy - dy * vx)
    inliers = int(np.sum(perp <= inlier_px))
    return (np.array([vx, vy, x0, y0], dtype=np.float32), inliers)


def _project_endpoints_on_line(
    points: np.ndarray, line: np.ndarray
) -> tuple[tuple[float, float], tuple[float, float]]:
    """Project the extreme inlier points onto the fitted line to get a segment."""
    vx, vy, x0, y0 = line
    t = (points[:, 0] - x0) * vx + (points[:, 1] - y0) * vy
    tmin, tmax = float(t.min()), float(t.max())
    p0 = (x0 + tmin * vx, y0 + tmin * vy)
    p1 = (x0 + tmax * vx, y0 + tmax * vy)
    return p0, p1


def _cluster_pocket_candidates(
    cands: list[tuple[int, float, float, float]],  # (far_idx, x, y, depth)
    radius: float,
) -> list[tuple[int, float, float, float]]:
    """Cluster close candidates; within each cluster, keep the deepest."""
    if not cands:
        return []
    cands = sorted(cands, key=lambda c: -c[3])  # deepest first
    chosen: list[tuple[int, float, float, float]] = []
    for cand in cands:
        if any(
            (cand[1] - c[1]) ** 2 + (cand[2] - c[2]) ** 2 < radius * radius
            for c in chosen
        ):
            continue
        chosen.append(cand)
    # Re-sort by far_idx so that we can walk the contour in order.
    chosen.sort(key=lambda c: c[0])
    return chosen


# ── Main entry ────────────────────────────────────────────────────────────────

def _merge_to_n_corners(pts: np.ndarray, n: int) -> np.ndarray:
    """Reduce a convex polygon to exactly `n` corners by repeatedly removing
    the vertex whose removal changes the enclosed area least.

    This always gives exactly n corners regardless of starting size, and never
    distorts the hull more than necessary.
    """
    verts = list(pts.astype(np.float32))
    while len(verts) > n:
        best_i, best_loss = 0, float("inf")
        N = len(verts)
        for i in range(N):
            a = np.array(verts[(i - 1) % N])
            b = np.array(verts[i])
            c = np.array(verts[(i + 1) % N])
            # Area of the triangle that would be lost by removing verts[i].
            loss = 0.5 * abs(float((b[0] - a[0]) * (c[1] - a[1])
                                    - (c[0] - a[0]) * (b[1] - a[1])))
            if loss < best_loss:
                best_loss = loss
                best_i = i
        verts.pop(best_i)
    return np.array(verts, dtype=np.float32)


def compute_hull_quad(table_mask: np.ndarray) -> np.ndarray | None:
    """Convex hull of the largest connected component of the table mask,
    forced to exactly 4 corners.

    Small disconnected components (e.g. spurious SAM3 fragments) are ignored
    because we only take the largest connected component by area.

    Returns a (4, 2) float32 array (x, y pixel coords) ordered CCW, or None.
    """
    mask_u8 = table_mask.astype(np.uint8)
    contours, _ = cv2.findContours(mask_u8, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
    if not contours:
        return None
    # Use only the largest connected component — ignores distant fragments.
    contour = max(contours, key=cv2.contourArea)
    if cv2.contourArea(contour) < 100:
        return None

    hull = cv2.convexHull(contour)   # (N, 1, 2) int32, already CCW
    pts = hull.reshape(-1, 2).astype(np.float32)
    if len(pts) < 3:
        return None
    if len(pts) == 4:
        return pts
    if len(pts) < 4:
        # Degenerate (triangle or line) — pad to 4 by duplicating a midpoint.
        mid = ((pts[0] + pts[1]) / 2).reshape(1, 2)
        pts = np.vstack([pts[:1], mid, pts[1:]])
        pts = pts[:4]
        return pts
    # More than 4 corners: iteratively merge the least-significant corner.
    return _merge_to_n_corners(pts, 4)


def detect_boundary(
    table_mask: np.ndarray,
    cfg: dict,
) -> BoundaryResult | None:
    """Detect cushions and pockets from a binary table mask.

    `table_mask` is a boolean or 0/1 array of shape (H, W).
    `cfg` expects keys `table_pose.*` and `pockets.*`.
    """
    shape = table_mask.shape  # (H, W)
    mask_u8 = table_mask.astype(np.uint8)

    tp_cfg = cfg["table_pose"]
    pk_cfg = cfg["pockets"]

    contour = _largest_contour(mask_u8, tp_cfg["min_table_mask_area_px"])
    if contour is None or len(contour) < 8:
        return None

    hull_quad = compute_hull_quad(mask_u8)

    border_mask = _is_border(contour, shape, tp_cfg["border_tolerance_px"])

    # Convex hull + convexity defects.
    hull_idx = cv2.convexHull(contour.reshape(-1, 1, 2), returnPoints=False)
    if hull_idx is None or len(hull_idx) < 3:
        return None
    try:
        defects = cv2.convexityDefects(contour.reshape(-1, 1, 2), hull_idx)
    except cv2.error:
        return None
    if defects is None:
        return None

    # Collect pocket candidates from defects.
    candidates: list[tuple[int, float, float, float]] = []  # (far_idx, x, y, depth)
    for d in defects.reshape(-1, 4):
        s_idx, e_idx, f_idx, depth_fx = int(d[0]), int(d[1]), int(d[2]), int(d[3])
        depth_px = depth_fx / 256.0
        if depth_px < pk_cfg["pocket_depth_min_px"]:
            continue
        if border_mask[f_idx]:
            continue  # defect far point is on the image border → not a real pocket
        # Also skip if BOTH flanking hull points are on the border (meaningless).
        if border_mask[s_idx] and border_mask[e_idx]:
            continue
        x, y = float(contour[f_idx, 0]), float(contour[f_idx, 1])
        candidates.append((f_idx, x, y, depth_px))

    pockets_raw = _cluster_pocket_candidates(
        candidates, pk_cfg["pocket_cluster_radius_px"]
    )

    # Extract cushion segments between consecutive pockets along the contour.
    # The contour is closed, so we walk from pocket to pocket.  Segments whose
    # interior points are mostly on the image border are not real cushions.
    cushions: list[Cushion] = []
    pocket_cushion_assignments: list[tuple[int, int]] = []  # per pocket: (left_cushion, right_cushion)

    N = len(contour)
    pocket_idxs = [p[0] for p in pockets_raw]

    if not pocket_idxs:
        # No pockets detected — whole interior portion is one cushion run.
        # Split by border stretches.
        segments = _split_non_border_runs(N, border_mask)
        for seg in segments:
            cushion = _fit_cushion_from_segment(contour, seg, tp_cfg)
            if cushion is not None:
                cushions.append(cushion)
        return BoundaryResult(
            cushions=cushions,
            pockets=[],
            contour=contour,
            hull_quad=hull_quad,
        )

    # Walk: between pocket i and pocket i+1 (circularly) is one cushion run.
    for i, p_idx in enumerate(pocket_idxs):
        next_idx = pocket_idxs[(i + 1) % len(pocket_idxs)]
        run = _contour_slice(N, p_idx + 1, next_idx)  # exclusive of endpoints
        if not run:
            continue
        # Filter out border vertices.
        interior_pts = [
            contour[j] for j in run if not border_mask[j]
        ]
        if len(interior_pts) < tp_cfg["edge_min_segment_length_px"] // 2:
            # Run too short or mostly on the border; no cushion here.
            continue
        pts = np.asarray(interior_pts, dtype=np.float32)
        fit = _fit_line_ransac(pts, tp_cfg["edge_ransac_inlier_px"])
        if fit is None:
            continue
        line, inliers = fit
        if inliers < tp_cfg["edge_min_segment_length_px"] // 2:
            continue
        p0, p1 = _project_endpoints_on_line(pts, line)
        direction = np.array([line[0], line[1]], dtype=np.float32)
        cushions.append(Cushion(p0=p0, p1=p1, direction=direction, support=inliers))

    # Build Pocket objects with cushion flanking indices.
    pockets: list[Pocket] = []
    for i, (f_idx, x, y, depth_px) in enumerate(pockets_raw):
        # The cushion BEFORE this pocket (between the previous pocket and this
        # one) is at index (i - 1) % len(cushions); the cushion AFTER is at
        # index i.  (When some runs are rejected as too short, these indices
        # won't align — handle by carrying a running cushion-index mapping.)
        # Simpler: assign nearest cushions post-hoc by endpoint proximity.
        left_c, right_c = _nearest_flanking_cushions(cushions, (x, y))
        kind = _classify_pocket_kind(cushions, left_c, right_c, pk_cfg)
        pockets.append(Pocket(
            x_img=x, y_img=y, kind=kind, depth_px=depth_px,
            left_cushion=left_c, right_cushion=right_c,
        ))

    return BoundaryResult(cushions=cushions, pockets=pockets, contour=contour, hull_quad=hull_quad)


def _contour_slice(n: int, start: int, end: int) -> list[int]:
    """Return contour indices from `start` up to (but excluding) `end`, wrapping."""
    if n == 0:
        return []
    start %= n
    end %= n
    if start == end:
        return []
    if start < end:
        return list(range(start, end))
    return list(range(start, n)) + list(range(0, end))


def _split_non_border_runs(n: int, border: np.ndarray) -> list[list[int]]:
    """Return lists of contiguous (circular) indices where border==False."""
    runs: list[list[int]] = []
    if n == 0:
        return runs
    # Find any index that IS on the border to start, so runs don't wrap.
    start = 0
    if border.all():
        return runs
    if not border[0]:
        # Rotate to a border starting point so we don't split a real run.
        for k in range(n):
            if border[k]:
                start = k
                break
    current: list[int] = []
    for off in range(n):
        idx = (start + off) % n
        if border[idx]:
            if current:
                runs.append(current)
                current = []
        else:
            current.append(idx)
    if current:
        runs.append(current)
    return runs


def _fit_cushion_from_segment(
    contour: np.ndarray, run: list[int], tp_cfg: dict
) -> Cushion | None:
    if len(run) < tp_cfg["edge_min_segment_length_px"] // 2:
        return None
    pts = contour[run].astype(np.float32)
    fit = _fit_line_ransac(pts, tp_cfg["edge_ransac_inlier_px"])
    if fit is None:
        return None
    line, inliers = fit
    if inliers < tp_cfg["edge_min_segment_length_px"] // 2:
        return None
    p0, p1 = _project_endpoints_on_line(pts, line)
    return Cushion(p0=p0, p1=p1,
                   direction=np.array([line[0], line[1]], dtype=np.float32),
                   support=inliers)


def _nearest_flanking_cushions(
    cushions: list[Cushion], pt: tuple[float, float]
) -> tuple[int, int]:
    """Find the two cushions whose endpoints are closest to the given pocket.

    Returns (left, right) indices (or -1 if fewer than 2 cushions).
    """
    if len(cushions) < 1:
        return -1, -1
    px, py = pt
    # For each cushion, distance = min(dist to p0, dist to p1).
    dists: list[tuple[int, float]] = []
    for i, c in enumerate(cushions):
        d0 = (c.p0[0] - px) ** 2 + (c.p0[1] - py) ** 2
        d1 = (c.p1[0] - px) ** 2 + (c.p1[1] - py) ** 2
        dists.append((i, min(d0, d1)))
    dists.sort(key=lambda t: t[1])
    if len(dists) == 1:
        return dists[0][0], -1
    return dists[0][0], dists[1][0]


def _classify_pocket_kind(
    cushions: list[Cushion],
    left: int, right: int,
    pk_cfg: dict,
) -> int:
    """CORNER if flanking cushions are perpendicular-ish; MIDDLE if colinear."""
    if left < 0 or right < 0:
        return PocketKind.UNKNOWN
    if left == right:
        # Same cushion on both sides → pocket is in the middle of one cushion.
        return PocketKind.MIDDLE
    d1 = cushions[left].direction
    d2 = cushions[right].direction
    # Normalise to account for sign flips.
    cos = abs(float(np.dot(d1, d2)))
    cos = min(1.0, max(0.0, cos))
    # ~colinear → MIDDLE; else CORNER.
    if cos > np.cos(np.deg2rad(15.0)):
        return PocketKind.MIDDLE
    return PocketKind.CORNER
