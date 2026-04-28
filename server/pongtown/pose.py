"""Stage 2: PnP from 4 corners (+ optional 2 midline-edge intersections)."""
from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np

from pongtown.geometry import line_from_two_points, line_intersection


@dataclass
class PoseResult:
    rvec: np.ndarray | None
    tvec: np.ndarray | None
    T_table_to_camera: np.ndarray | None
    pnp_iou: float
    success: bool


def canonical_corners_mm(W: float = 2740.0, H: float = 1525.0) -> np.ndarray:
    return np.array(
        [
            [-W / 2, -H / 2, 0.0],
            [+W / 2, -H / 2, 0.0],
            [+W / 2, +H / 2, 0.0],
            [-W / 2, +H / 2, 0.0],
        ],
        dtype=np.float64,
    )


def canonical_midline_mm(H: float = 1525.0) -> np.ndarray:
    """Two endpoints of the table midline in table-local frame."""
    return np.array([[0.0, -H / 2, 0.0], [0.0, +H / 2, 0.0]], dtype=np.float64)


def canonical_net_strip_mm(W_net: float = 1830.0, h_net: float = 152.5) -> np.ndarray:
    """Net rectangle in table-local frame (x = 0 plane)."""
    return np.array(
        [
            [0.0, -W_net / 2, 0.0],
            [0.0, +W_net / 2, 0.0],
            [0.0, +W_net / 2, h_net],
            [0.0, -W_net / 2, h_net],
        ],
        dtype=np.float64,
    )


def intersect_midline_with_table_long_edges(
    quad_img: np.ndarray, midline_img: np.ndarray
) -> np.ndarray | None:
    """Return (2, 2): intersections of the midline with the two long edges of
    quad_img. The two longest opposite-pair edges are treated as long."""
    if quad_img is None or midline_img is None or len(quad_img) != 4:
        return None
    edge_lengths = [np.linalg.norm(quad_img[(i + 1) % 4] - quad_img[i]) for i in range(4)]
    mean_02 = (edge_lengths[0] + edge_lengths[2]) / 2.0
    mean_13 = (edge_lengths[1] + edge_lengths[3]) / 2.0
    long_idx = (0, 2) if mean_02 >= mean_13 else (1, 3)
    try:
        L_mid = line_from_two_points(midline_img[0], midline_img[1])
    except ValueError:
        return None
    pts = []
    for i in long_idx:
        try:
            L_edge = line_from_two_points(quad_img[i], quad_img[(i + 1) % 4])
        except ValueError:
            return None
        p = line_intersection(L_mid, L_edge)
        if p is None:
            return None
        pts.append(p)
    return np.array(pts, dtype=np.float64)


def _make_T(rvec: np.ndarray, tvec: np.ndarray) -> np.ndarray:
    R, _ = cv2.Rodrigues(rvec)
    T = np.eye(4)
    T[:3, :3] = R
    T[:3, 3] = tvec.reshape(3)
    return T


def _project_world_to_image(
    P: np.ndarray, rvec: np.ndarray, tvec: np.ndarray, K: np.ndarray
) -> np.ndarray:
    pts, _ = cv2.projectPoints(P, rvec, tvec, K, None)
    return pts.reshape(-1, 2)


def solve_table_pose(
    quad_img: np.ndarray,
    midline_img: np.ndarray | None,
    K: np.ndarray,
    *,
    cfg: dict,
    table_mask: np.ndarray | None = None,
    net_mask: np.ndarray | None = None,
    person_mask: np.ndarray | None = None,
    image_shape: tuple[int, int] | None = None,
    T_camera_to_world: np.ndarray | None = None,
    world_up_axis: int = 1,
) -> PoseResult:
    """Solve table pose via IPPE_SQUARE on 4 corners, disambiguated by midline,
    refined with LM if midline is available.
    """
    if quad_img is None or len(quad_img) != 4:
        return PoseResult(None, None, None, 0.0, False)

    P_corners = canonical_corners_mm(
        cfg["table"]["width_mm"], cfg["table"]["height_mm"]
    )
    P_mid = canonical_midline_mm(cfg["table"]["height_mm"])

    quad = np.ascontiguousarray(quad_img.astype(np.float64))
    try:
        ok, rvecs, tvecs, _ = cv2.solvePnPGeneric(
            P_corners, quad, K, None, flags=cv2.SOLVEPNP_IPPE
        )
    except cv2.error:
        return PoseResult(None, None, None, 0.0, False)
    if not ok or len(rvecs) == 0:
        return PoseResult(None, None, None, 0.0, False)

    m_endpoints_img: np.ndarray | None = None
    if midline_img is not None:
        m_endpoints_img = intersect_midline_with_table_long_edges(quad, midline_img)
    best_idx = 0
    # Disambiguate IPPE's planar ambiguity. Priority: ARCore world-up (the
    # table normal must point up in world; ARCore poses are reliable enough
    # that this kicks the wrong-side solution out cleanly). Fall back to
    # midline-endpoint match if no world-up info.
    if T_camera_to_world is not None:
        best_dot = -float("inf")
        for i, (rv, tv) in enumerate(zip(rvecs, tvecs)):
            R, _ = cv2.Rodrigues(rv)
            T_tc = np.eye(4)
            T_tc[:3, :3] = R
            T_tc[:3, 3] = tv.reshape(3)
            T_tw = T_camera_to_world @ T_tc
            normal_world = T_tw[:3, :3] @ np.array([0.0, 0.0, 1.0])
            dot = float(normal_world[world_up_axis])
            if dot > best_dot:
                best_dot = dot
                best_idx = i
    elif m_endpoints_img is not None:
        best_err = float("inf")
        for i, (rv, tv) in enumerate(zip(rvecs, tvecs)):
            reproj = _project_world_to_image(P_mid, rv, tv, K)
            d_a = np.linalg.norm(reproj[0] - m_endpoints_img[0]) + np.linalg.norm(reproj[1] - m_endpoints_img[1])
            d_b = np.linalg.norm(reproj[0] - m_endpoints_img[1]) + np.linalg.norm(reproj[1] - m_endpoints_img[0])
            err = min(d_a, d_b)
            if err < best_err:
                best_err = err
                best_idx = i
    rvec = rvecs[best_idx].reshape(3).astype(np.float64)
    tvec = tvecs[best_idx].reshape(3).astype(np.float64)

    if m_endpoints_img is not None:
        reproj = _project_world_to_image(P_mid, rvec, tvec, K)
        if (
            np.linalg.norm(reproj[0] - m_endpoints_img[0])
            + np.linalg.norm(reproj[1] - m_endpoints_img[1])
            <= np.linalg.norm(reproj[0] - m_endpoints_img[1])
            + np.linalg.norm(reproj[1] - m_endpoints_img[0])
        ):
            ordered = m_endpoints_img
        else:
            ordered = m_endpoints_img[::-1]
        obj = np.vstack([P_corners, P_mid])
        img = np.vstack([quad, ordered])
        try:
            rvec_r, tvec_r = cv2.solvePnPRefineLM(
                obj, img, K, None, rvec.copy(), tvec.copy().reshape(3, 1)
            )
            rvec = np.asarray(rvec_r).reshape(3)
            tvec = np.asarray(tvec_r).reshape(3)
        except cv2.error:
            pass

    T = _make_T(rvec, tvec)

    iou = 0.0
    if image_shape is not None and table_mask is not None:
        reproj_corners = _project_world_to_image(P_corners, rvec, tvec, K)
        h, w = image_shape
        canvas = np.zeros((h, w), dtype=np.uint8)
        cv2.fillPoly(
            canvas, [np.round(reproj_corners).astype(np.int32).reshape(-1, 1, 2)], 1
        )
        reproj_mask = canvas.astype(bool)
        union = table_mask.copy()
        if net_mask is not None:
            union = union | net_mask
        denom_excl = person_mask
        if denom_excl is not None:
            inter = (reproj_mask & union) & ~denom_excl
            uni = (reproj_mask | union) & ~denom_excl
        else:
            inter = reproj_mask & union
            uni = reproj_mask | union
        u = int(uni.sum())
        iou = float(inter.sum()) / float(u) if u > 0 else 0.0

    return PoseResult(rvec, tvec, T, iou, True)
