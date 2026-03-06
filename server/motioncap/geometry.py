"""
Shared geometry, frame decoding, and stabilization functions.
Imported by both motioncap/main.py and analysis/homography/main.py.
"""
import struct
import sys
import zlib
from pathlib import Path

import cv2
import numpy as np

_server_root = Path(__file__).resolve().parent.parent
_project_root = _server_root.parent
sys.path.insert(0, str(_project_root))
sys.path.insert(0, str(_project_root / "proto"))
sys.path.insert(0, str(_server_root))

from proto import perceiver_pb2, motioncap_pb2

Method = motioncap_pb2.MotionCaptureResponse.StabilizationMethod


# ── Frame decoding ───────────────────────────────────────────────────────────

def decode_frame_rgb(frame: perceiver_pb2.PerceiverDataFrame) -> np.ndarray:
    img = frame.rgb_frame
    ImageFormat = perceiver_pb2.ImageFrame.ImageFormat
    if img.format == ImageFormat.JPEG:
        buf = np.frombuffer(img.data, dtype=np.uint8)
        bgr = cv2.imdecode(buf, cv2.IMREAD_COLOR)
        return cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
    elif img.format == ImageFormat.BITMAP_RGB:
        raw = np.frombuffer(img.data, dtype=np.uint8)
        total = len(raw) // 3
        side = int(total ** 0.5)
        return raw.reshape((side, side, 3))
    raise ValueError(f"Unsupported RGB format: {img.format}")


def decode_depth(frame: perceiver_pb2.PerceiverDataFrame,
                 rgb_h: int, rgb_w: int) -> np.ndarray | None:
    """Return depth in metres as float32 (H, W), resized to match RGB, or None."""
    df = frame.depth_frame
    if not df or not df.data:
        return None
    DepthFormat = perceiver_pb2.DepthFrame.DepthFormat
    raw = np.frombuffer(df.data, dtype=np.uint8)
    if df.format == DepthFormat.UINT16_MILLIMETERS:
        depth = raw.view(np.uint16).astype(np.float32) / 1000.0
    elif df.format == DepthFormat.FLOAT32_METERS:
        depth = raw.view(np.float32)
    else:
        return None
    # Infer dimensions
    n = depth.size
    intr = frame.camera_intrinsics
    if intr and intr.depth_width > 0 and intr.depth_height > 0:
        dh, dw = int(intr.depth_height), int(intr.depth_width)
    else:
        # Square-root fallback
        side = int(n ** 0.5)
        dh = dw = side
    if dh * dw != n:
        return None
    depth = depth.reshape(dh, dw)
    if (dh, dw) != (rgb_h, rgb_w):
        depth = cv2.resize(depth, (rgb_w, rgb_h), interpolation=cv2.INTER_NEAREST)
    return depth


# ── Heatmap encoding ─────────────────────────────────────────────────────────

def encode_heatmap(heatmap_u8: np.ndarray) -> bytes:
    """Encode uint8 heatmap as [h:u32le][w:u32le][zlib(uint8 array)]."""
    h, w = heatmap_u8.shape
    header = struct.pack("<II", h, w)
    compressed = zlib.compress(heatmap_u8.tobytes(), level=1)
    return header + compressed


# ── Quaternion / rotation helpers ────────────────────────────────────────────

def quat_to_rot(qx: float, qy: float, qz: float, qw: float) -> np.ndarray:
    """Unit quaternion → 3×3 rotation matrix (row-major)."""
    x, y, z, w = qx, qy, qz, qw
    return np.array([
        [1 - 2*(y*y + z*z),     2*(x*y - z*w),     2*(x*z + y*w)],
        [    2*(x*y + z*w), 1 - 2*(x*x + z*z),     2*(y*z - x*w)],
        [    2*(x*z - y*w),     2*(y*z + x*w), 1 - 2*(x*x + y*y)],
    ], dtype=np.float64)


def pose_components(frame: perceiver_pb2.PerceiverDataFrame):
    """Return (R 3×3, t 3-vec) from camera_pose, or (None, None) if absent."""
    p = frame.camera_pose
    if p is None:
        return None, None
    rot = p.rotation
    pos = p.position
    R = quat_to_rot(rot.x, rot.y, rot.z, rot.w)
    t = np.array([pos.x, pos.y, pos.z], dtype=np.float64)
    return R, t


def build_K(frame: perceiver_pb2.PerceiverDataFrame,
            cached: list) -> np.ndarray | None:
    """Return 3×3 intrinsic matrix, caching on first frame."""
    if cached:
        return cached[0]
    intr = frame.camera_intrinsics
    if not intr or intr.fx == 0:
        return None
    K = np.array([
        [intr.fx, 0,       intr.cx],
        [0,       intr.fy, intr.cy],
        [0,       0,       1      ],
    ], dtype=np.float64)
    cached.append(K)
    return K


# ── World→pixel projection (mirrors GeometryStreamViewer.tsx) ────────────────

def world_to_pixel(wx: float, wy: float, wz: float,
                   R_cam: np.ndarray, t_cam: np.ndarray,
                   K: np.ndarray) -> tuple[float, float] | None:
    """Project a world point into pixel coords for a given camera pose."""
    # Translate to camera-relative
    d = np.array([wx - t_cam[0], wy - t_cam[1], wz - t_cam[2]])
    # Rotate into camera frame (R_cam maps world→camera, i.e., R_cam = R_world2cam)
    cam = R_cam @ d
    if cam[2] >= 0:
        return None  # behind camera
    depth = -cam[2]
    u = K[0, 0] * cam[0] / depth + K[0, 2]
    v = K[1, 1] * cam[1] / depth + K[1, 2]
    return u, v


# ── Stabilization methods ────────────────────────────────────────────────────

def _warp_depth(rgb_curr: np.ndarray, depth_curr: np.ndarray,
                R_rel: np.ndarray, t_rel: np.ndarray,
                K: np.ndarray) -> np.ndarray:
    """Method 1: Per-pixel dense warp using depth + 6DOF pose."""
    h, w = rgb_curr.shape[:2]
    u_grid, v_grid = np.meshgrid(np.arange(w, dtype=np.float64),
                                  np.arange(h, dtype=np.float64))
    z = depth_curr.astype(np.float64)
    valid = z > 0.01
    x = (u_grid - K[0, 2]) * z / K[0, 0]
    y = (v_grid - K[1, 2]) * z / K[1, 1]
    pts = np.stack([x, y, z], axis=-1)           # (H, W, 3)
    pts_ref = (R_rel @ pts.reshape(-1, 3).T).T + t_rel   # (H*W, 3)
    pz = pts_ref[:, 2]
    with np.errstate(divide='ignore', invalid='ignore'):
        u_new = np.where(pz > 0.01, K[0, 0] * pts_ref[:, 0] / pz + K[0, 2], -1)
        v_new = np.where(pz > 0.01, K[1, 1] * pts_ref[:, 1] / pz + K[1, 2], -1)
    map_x = u_new.reshape(h, w).astype(np.float32)
    map_y = v_new.reshape(h, w).astype(np.float32)
    # Pixels with invalid depth get mapped to (-1,-1) → borderValue
    map_x[~valid] = -1
    map_y[~valid] = -1
    return cv2.remap(rgb_curr, map_x, map_y, cv2.INTER_LINEAR,
                     borderMode=cv2.BORDER_REPLICATE)


def _warp_homography(rgb_curr: np.ndarray, H: np.ndarray,
                     h: int, w: int) -> np.ndarray:
    return cv2.warpPerspective(rgb_curr, H, (w, h),
                               flags=cv2.INTER_LINEAR,
                               borderMode=cv2.BORDER_REPLICATE)


def stabilize(
    rgb_curr: np.ndarray,
    frame_curr: perceiver_pb2.PerceiverDataFrame,
    frame_ref: perceiver_pb2.PerceiverDataFrame,
    R_ref: np.ndarray, t_ref: np.ndarray,
    R_curr: np.ndarray, t_curr: np.ndarray,
    K: np.ndarray,
    cfg: dict,
    forced_method: str = "auto",
) -> tuple[np.ndarray, Method, float]:
    """
    Warp rgb_curr to align with the reference frame.
    Returns (warped, method_used, confidence).
    """
    h, w = rgb_curr.shape[:2]
    K_inv = np.linalg.inv(K)

    # Relative transform: current camera → reference camera
    # R_world2ref = R_ref.T  (ARCore: R rotates world→camera, so R_world2cam = R.T)
    R_ref_w2c = R_ref.T        # world → reference camera
    R_curr_w2c = R_curr.T      # world → current camera
    # R_rel maps current camera frame → reference camera frame
    R_rel = R_ref_w2c @ np.linalg.inv(R_curr_w2c)   # ≡ R_ref_w2c @ R_curr (since R_curr_w2c = R_curr.T and R_curr is orthogonal)
    t_rel = R_ref_w2c @ (t_curr - t_ref)            # translation in reference camera frame

    method_str = forced_method if forced_method != "auto" else "auto"
    min_pts = cfg["stabilization"]["min_points_for_ransac"]
    pt_conf_thresh = cfg["stabilization"]["point_confidence_threshold"]
    assumed_depth = cfg["stabilization"]["assumed_depth_m"]
    ransac_thresh = cfg["stabilization"]["ransac_threshold_px"]

    # ── Method 1: Depth warp ──────────────────────────────────────────────
    if method_str in ("auto", "depth"):
        depth = decode_depth(frame_curr, h, w)
        if depth is not None:
            warped = _warp_depth(rgb_curr, depth, R_rel, t_rel, K)
            return warped, Method.DEPTH_WARP, 1.0

    # ── Method 2: Plane homography ────────────────────────────────────────
    if method_str in ("auto", "plane"):
        planes = list(frame_ref.inferred_geometry.planes)
        if planes:
            plane = planes[0]
            c = plane.center_pose
            # Plane normal = z-axis of plane's local frame in world coords
            Rp = quat_to_rot(c.rotation.x, c.rotation.y, c.rotation.z, c.rotation.w)
            n_world = Rp[:, 2]
            p_world = np.array([c.position.x, c.position.y, c.position.z])
            # Plane normal and distance in reference camera frame
            n_ref = R_ref_w2c @ n_world
            d_ref = float(n_ref @ (R_ref_w2c @ p_world - t_rel))
            if abs(d_ref) > 0.01:
                H = K @ (R_rel - np.outer(t_rel, n_ref) / d_ref) @ K_inv
                warped = _warp_homography(rgb_curr, H, h, w)
                return warped, Method.PLANE_HOMO, 0.9

    # ── Method 3: RANSAC from projected point cloud ───────────────────────
    if method_str in ("auto", "points"):
        cloud = list(frame_ref.inferred_geometry.point_cloud)
        cloud = [p for p in cloud if p.confidence >= pt_conf_thresh]
        if len(cloud) >= min_pts:
            pts_ref_2d, pts_curr_2d = [], []
            for tp in cloud:
                pxy_ref = world_to_pixel(tp.point.x, tp.point.y, tp.point.z,
                                          R_ref_w2c, t_ref, K)
                pxy_curr = world_to_pixel(tp.point.x, tp.point.y, tp.point.z,
                                           R_curr_w2c, t_curr, K)
                if pxy_ref and pxy_curr:
                    pts_ref_2d.append(pxy_ref)
                    pts_curr_2d.append(pxy_curr)
            if len(pts_ref_2d) >= min_pts:
                src = np.array(pts_curr_2d, dtype=np.float32)
                dst = np.array(pts_ref_2d,  dtype=np.float32)
                H, mask = cv2.findHomography(src, dst, cv2.RANSAC, ransac_thresh)
                if H is not None:
                    inlier_ratio = float(mask.sum()) / len(mask)
                    warped = _warp_homography(rgb_curr, H, h, w)
                    return warped, Method.POINT_RANSAC, inlier_ratio

    # ── Method 4: Pose-only approximate homography ────────────────────────
    if method_str in ("auto", "pose"):
        # Try to estimate depth from depth frame, else use config default
        depth_est = assumed_depth
        depth = decode_depth(frame_curr, h, w)
        if depth is not None:
            valid = depth[depth > 0.01]
            if valid.size > 0:
                depth_est = float(np.median(valid))
        n_ref = np.array([0.0, 0.0, 1.0])  # fronto-parallel approximation
        H = K @ (R_rel - np.outer(t_rel, n_ref) / depth_est) @ K_inv
        warped = _warp_homography(rgb_curr, H, h, w)
        return warped, Method.POSE_APPROX, 0.6

    # ── Method 5: Optical flow fallback ──────────────────────────────────
    rgb_ref = decode_frame_rgb(frame_ref)
    ref_gray  = cv2.cvtColor(rgb_ref,  cv2.COLOR_RGB2GRAY)
    curr_gray = cv2.cvtColor(rgb_curr, cv2.COLOR_RGB2GRAY)
    flow = cv2.calcOpticalFlowFarneback(
        ref_gray, curr_gray, None, 0.5, 3, 15, 3, 5, 1.2, 0)
    step = 16
    ys, xs = np.mgrid[step//2:h:step, step//2:w:step]
    pts_grid = np.stack([xs.ravel(), ys.ravel()], axis=1).astype(np.float32)
    pts_moved = pts_grid + flow[ys.ravel(), xs.ravel()]
    H, mask = cv2.findHomography(pts_moved, pts_grid, cv2.RANSAC, 3.0)
    if H is None:
        return rgb_curr.copy(), Method.OPTICAL_FLOW, 0.0
    warped = _warp_homography(rgb_curr, H, h, w)
    inlier_ratio = float(mask.sum()) / max(len(mask), 1)
    return warped, Method.OPTICAL_FLOW, inlier_ratio


# ── Heatmap computation ──────────────────────────────────────────────────────

def compute_heatmap(warped: np.ndarray, reference: np.ndarray,
                    noise_thresh: int, blur_kernel: int
                    ) -> tuple[np.ndarray, float]:
    """
    Compute per-pixel motion heatmap from warped vs. reference frame.
    Returns (heatmap_uint8 H×W, max_raw_value).
    """
    diff = np.abs(warped.astype(np.float32) - reference.astype(np.float32))
    diff_gray = diff.max(axis=2)   # max over colour channels

    # Suppress noise floor
    diff_gray[diff_gray < noise_thresh] = 0

    max_raw = float(diff_gray.max())

    # Gaussian smooth
    if blur_kernel > 1:
        diff_gray = cv2.GaussianBlur(diff_gray, (blur_kernel, blur_kernel), 0)

    # Normalise to uint8
    if max_raw > 0:
        heatmap_u8 = (diff_gray / max_raw * 255).clip(0, 255).astype(np.uint8)
    else:
        heatmap_u8 = np.zeros(diff_gray.shape, dtype=np.uint8)

    return heatmap_u8, max_raw


def pixel_map(
    u: float, v: float,
    frame_a: perceiver_pb2.PerceiverDataFrame,   # source frame (pixel is here)
    frame_b: perceiver_pb2.PerceiverDataFrame,   # target frame
    R_a: np.ndarray, t_a: np.ndarray,
    R_b: np.ndarray, t_b: np.ndarray,
    K: np.ndarray,
    cfg: dict,
    forced_method: str = "auto",
) -> tuple[float, float] | None:
    """
    Map pixel (u, v) in frame_a to the corresponding pixel in frame_b.
    Returns (u', v') or None if the point is behind the camera / no valid data.
    """
    h_img = int(K[1, 2] * 2)
    w_img = int(K[0, 2] * 2)
    K_inv = np.linalg.inv(K)

    R_a_w2c = R_a.T
    R_b_w2c = R_b.T
    # R_rel: a-camera-frame → b-camera-frame
    R_rel = R_b_w2c @ np.linalg.inv(R_a_w2c)
    t_rel = R_b_w2c @ (t_a - t_b)

    method_str = forced_method

    # Method 1: depth warp — unproject pixel through depth, reproject
    if method_str in ("auto", "depth"):
        depth = decode_depth(frame_a, h_img, w_img)
        if depth is not None:
            ui, vi = int(round(u)), int(round(v))
            if 0 <= vi < depth.shape[0] and 0 <= ui < depth.shape[1]:
                z = float(depth[vi, ui])
                if z > 0.01:
                    x = (u - K[0, 2]) * z / K[0, 0]
                    y = (v - K[1, 2]) * z / K[1, 1]
                    p_b = R_rel @ np.array([x, y, z]) + t_rel
                    if p_b[2] > 0.01:
                        return (K[0, 0] * p_b[0] / p_b[2] + K[0, 2],
                                K[1, 1] * p_b[1] / p_b[2] + K[1, 2])

    # Methods 2-5: compute H, map point
    H = _compute_H(R_rel, t_rel, K, K_inv, frame_a, frame_b,
                    R_a_w2c, t_a, R_b_w2c, t_b, cfg, method_str, h_img, w_img)
    if H is None:
        return None
    p = H @ np.array([u, v, 1.0])
    if abs(p[2]) < 1e-9:
        return None
    return p[0] / p[2], p[1] / p[2]


def _compute_H(
    R_rel, t_rel, K, K_inv,
    frame_a, frame_b,
    R_a_w2c, t_a, R_b_w2c, t_b,
    cfg, method_str, h_img, w_img,
) -> np.ndarray | None:
    """Compute 3x3 homography H mapping frame_a pixels → frame_b pixels."""
    min_pts = cfg["stabilization"]["min_points_for_ransac"]
    pt_conf = cfg["stabilization"]["point_confidence_threshold"]
    assumed_depth = cfg["stabilization"]["assumed_depth_m"]
    ransac_thresh = cfg["stabilization"]["ransac_threshold_px"]

    # Plane homography
    if method_str in ("auto", "plane"):
        planes = list(frame_a.inferred_geometry.planes)
        if planes:
            plane = planes[0]
            c = plane.center_pose
            Rp = quat_to_rot(c.rotation.x, c.rotation.y, c.rotation.z, c.rotation.w)
            n_world = Rp[:, 2]
            p_world = np.array([c.position.x, c.position.y, c.position.z])
            n_a = R_a_w2c @ n_world
            d_a = float(n_a @ R_a_w2c @ (p_world - t_a))
            if abs(d_a) > 0.01:
                return K @ (R_rel - np.outer(t_rel, n_a) / d_a) @ K_inv

    # RANSAC from point cloud
    if method_str in ("auto", "points"):
        cloud = [p for p in frame_a.inferred_geometry.point_cloud
                 if p.confidence >= pt_conf]
        if len(cloud) >= min_pts:
            src, dst = [], []
            for tp in cloud:
                pa = world_to_pixel(tp.point.x, tp.point.y, tp.point.z, R_a_w2c, t_a, K)
                pb = world_to_pixel(tp.point.x, tp.point.y, tp.point.z, R_b_w2c, t_b, K)
                if pa and pb:
                    src.append(pa)
                    dst.append(pb)
            if len(src) >= min_pts:
                H, mask = cv2.findHomography(
                    np.array(src, dtype=np.float32),
                    np.array(dst, dtype=np.float32),
                    cv2.RANSAC, ransac_thresh)
                if H is not None:
                    return H

    # Pose approximate
    if method_str in ("auto", "pose", "depth"):
        depth_est = assumed_depth
        depth = decode_depth(frame_a, h_img, w_img)
        if depth is not None:
            valid = depth[depth > 0.01]
            if valid.size > 0:
                depth_est = float(np.median(valid))
        n = np.array([0.0, 0.0, 1.0])
        return K @ (R_rel - np.outer(t_rel, n) / depth_est) @ K_inv

    return None
