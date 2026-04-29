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


def canonical_half_rectangle_mm(W: float = 2740.0, H: float = 1525.0) -> np.ndarray:
    """4 corners of the camera-side half of the table, ordered to match the
    half-quad emitted by quad_fit: [outer1, outer2, mid1, mid2] CCW.

    The visible half occupies x ∈ [0, W/2] in canonical table-local coords
    (origin at full-table centre, +x = camera side, +z = table normal).
    """
    return np.array(
        [
            [+W / 2, -H / 2, 0.0],   # outer1
            [+W / 2, +H / 2, 0.0],   # outer2
            [0.0,    +H / 2, 0.0],   # mid1
            [0.0,    -H / 2, 0.0],   # mid2
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
    half_rectangle: bool = False,
) -> PoseResult:
    """Solve table pose via IPPE_SQUARE on 4 corners, disambiguated by midline,
    refined with LM if midline is available.
    """
    if quad_img is None or len(quad_img) != 4:
        return PoseResult(None, None, None, 0.0, False)

    if half_rectangle:
        P_corners = canonical_half_rectangle_mm(
            cfg["table"]["width_mm"], cfg["table"]["height_mm"]
        )
    else:
        P_corners = canonical_corners_mm(
            cfg["table"]["width_mm"], cfg["table"]["height_mm"]
        )
    P_mid = canonical_midline_mm(cfg["table"]["height_mm"])

    quad = np.ascontiguousarray(quad_img.astype(np.float64))
    # Try all 4 cyclic rotations of the canonical corners (and both orderings
    # for half_rectangle). The _ccw_from_top_left ordering of the quad depends
    # on camera position, so the correct canonical↔image correspondence is not
    # known a priori. We pick whichever rotation minimises the best-solution
    # IPPE reprojection error.
    rotations = [np.roll(P_corners, -r, axis=0) for r in range(4)]
    if half_rectangle:
        rotations += [np.roll(P_corners, -r, axis=0) for r in range(4)]
        quads = [quad] * 4 + [quad[::-1].copy()] * 4
    else:
        quads = [quad] * 4
    best_ok = False
    best_rvecs = None
    best_tvecs = None
    best_err = float("inf")
    best_quad = quad
    best_P_corners = P_corners
    for q, P in zip(quads, rotations):
        try:
            ok, rvecs, tvecs, _ = cv2.solvePnPGeneric(
                P, q, K, None, flags=cv2.SOLVEPNP_IPPE
            )
        except cv2.error:
            continue
        if not ok or len(rvecs) == 0:
            continue
        # Best reprojection error across the two IPPE solutions.
        errs = []
        for rv, tv in zip(rvecs, tvecs):
            re = _project_world_to_image(P, rv, tv, K)
            errs.append(float(np.linalg.norm(re - q, axis=1).mean()))
        err = min(errs)
        if err < best_err:
            best_err = err
            best_ok = True
            best_rvecs = rvecs
            best_tvecs = tvecs
            best_quad = q
            best_P_corners = P
    if not best_ok:
        return PoseResult(None, None, None, 0.0, False)
    rvecs, tvecs = best_rvecs, best_tvecs
    quad = best_quad
    P_corners = best_P_corners

    # Net/midline-based LM refinement is skipped: the net mask detection is
    # too noisy (~20-30 px endpoint error) and pulls the pose away from the
    # 4-5 px IPPE solution. The 4-corner cyclic-rotation IPPE is sufficient.
    m_endpoints_img: np.ndarray | None = None
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
        # Score against the FULL canonical rectangle regardless of which corners
        # were used for PnP — gives a stable comparable IoU across modes.
        P_full = canonical_corners_mm(cfg["table"]["width_mm"], cfg["table"]["height_mm"])
        reproj_corners = _project_world_to_image(P_full, rvec, tvec, K)
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


def canonical_net_local_mm(W_net: float = 1830.0, h_net: float = 152.5) -> np.ndarray:
    """4 net corners in net-local frame where z=0 (required by IPPE).

    Net-local axes: x = net width direction, y = vertical (up along net),
    z = normal to net plane (toward camera when table top faces up).
    Origin at centre of the net's bottom edge.

    This frame relates to table-local frame by:
      net_x = table_y,  net_y = table_z,  net_z = table_x
    """
    return np.array(
        [
            [-W_net / 2, 0.0,   0.0],
            [+W_net / 2, 0.0,   0.0],
            [+W_net / 2, h_net, 0.0],
            [-W_net / 2, h_net, 0.0],
        ],
        dtype=np.float64,
    )


def solve_net_pose(
    net_quad_img: np.ndarray,
    K: np.ndarray,
    *,
    cfg: dict,
    T_camera_to_world: np.ndarray | None = None,
    world_up_axis: int = 1,
) -> PoseResult:
    """Independently solve the net pose via IPPE on the 4 detected net corners.

    Uses the net-local z=0 frame so IPPE is valid (the net is a vertical plane,
    not the table plane).  Disambiguation: the net-local y-axis (vertical up
    the net) must point up in world frame.

    The returned rvec/tvec express T_net_to_camera in the net-local frame.
    """
    if net_quad_img is None or len(net_quad_img) != 4:
        return PoseResult(None, None, None, 0.0, False)

    t_cfg = cfg["table"]
    W_net = t_cfg["height_mm"] + 2.0 * t_cfg["net_overhang_mm"]
    h_net = t_cfg["net_height_mm"]
    P_net = canonical_net_local_mm(W_net, h_net)

    quad = np.ascontiguousarray(net_quad_img.astype(np.float64))

    best_ok = False
    best_rvecs: list | None = None
    best_tvecs: list | None = None
    best_err = float("inf")
    best_P = P_net

    for rot in range(4):
        P = np.roll(P_net, -rot, axis=0)
        try:
            ok, rvecs, tvecs, _ = cv2.solvePnPGeneric(
                P, quad, K, None, flags=cv2.SOLVEPNP_IPPE
            )
        except cv2.error:
            continue
        if not ok or len(rvecs) == 0:
            continue
        for rv, tv in zip(rvecs, tvecs):
            re = _project_world_to_image(P, rv, tv, K)
            err = float(np.linalg.norm(re - quad, axis=1).mean())
            if err < best_err:
                best_err = err
                best_ok = True
                best_rvecs = rvecs
                best_tvecs = tvecs
                best_P = P

    if not best_ok:
        return PoseResult(None, None, None, 0.0, False)

    # Disambiguate: net-local y (vertical) must point up in world.
    best_idx = 0
    if T_camera_to_world is not None:
        best_dot = -float("inf")
        for i, (rv, tv) in enumerate(zip(best_rvecs, best_tvecs)):
            R, _ = cv2.Rodrigues(rv)
            T_nc = np.eye(4)
            T_nc[:3, :3] = R
            T_nc[:3, 3] = tv.reshape(3)
            T_nw = T_camera_to_world @ T_nc
            net_up_world = T_nw[:3, :3] @ np.array([0.0, 1.0, 0.0])
            dot = float(net_up_world[world_up_axis])
            if dot > best_dot:
                best_dot = dot
                best_idx = i

    rvec = best_rvecs[best_idx].reshape(3).astype(np.float64)
    tvec = best_tvecs[best_idx].reshape(3).astype(np.float64)
    T = _make_T(rvec, tvec)
    return PoseResult(rvec, tvec, T, 0.0, True)
