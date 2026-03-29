"""
SIFT-based motion detection: match features across frames,
filter by residual displacement (background prediction minus actual).
"""

import sys
from pathlib import Path

import cv2
import numpy as np

_server_root = Path(__file__).resolve().parent.parent
_project_root = _server_root.parent
sys.path.insert(0, str(_project_root))
sys.path.insert(0, str(_project_root / "proto"))


def spatial_filter_features(kp: list, desc: np.ndarray, img_h: int, img_w: int,
                            min_density: float = 10.0) -> tuple[list, np.ndarray]:
    """
    Filter keypoints to ensure spatial distribution.
    Keeps at most one feature per cell to avoid clustering.
    min_density: target minimum features per cm² (used to compute cell size).

    Args:
        kp: list of cv2.KeyPoint
        desc: descriptor array or None
        img_h, img_w: image dimensions
        min_density: minimum features per cm² (default 10)

    Returns:
        (filtered_kp, filtered_desc)
    """
    if not kp or desc is None:
        return [], desc

    # Estimate cell size: assume 1 cm² = roughly 30 pixels² at typical distance
    # (rough heuristic; adjust if needed)
    cell_size = max(1, int(np.sqrt(900.0 / min_density)))  # pixels

    # Grid-based filtering: keep at most one point per cell
    grid = {}
    filtered_idx = []

    for i, k in enumerate(kp):
        x, y = k.pt
        cell_x = int(x) // cell_size
        cell_y = int(y) // cell_size
        cell_key = (cell_x, cell_y)

        if cell_key not in grid:
            grid[cell_key] = i
            filtered_idx.append(i)

    if not filtered_idx:
        return [], desc

    filtered_kp = [kp[i] for i in filtered_idx]
    filtered_desc = desc[filtered_idx] if desc is not None else None

    return filtered_kp, filtered_desc


def compute_pose_H(R_ref: np.ndarray, t_ref: np.ndarray,
                   R_curr: np.ndarray, t_curr: np.ndarray,
                   K: np.ndarray, depth_est: float) -> np.ndarray:
    """
    Compute homography from relative pose (POSE_APPROX method).
    Maps current-frame pixels → reference-frame pixels (background prediction).

    Args:
        R_ref, t_ref: reference camera pose (world→camera)
        R_curr, t_curr: current camera pose (world→camera)
        K: camera intrinsic matrix (3x3)
        depth_est: estimated scene depth (metres)

    Returns:
        H (3x3): homography matrix for warpPerspective
    """
    K_inv = np.linalg.inv(K)

    # Convert to world→camera convention (ARCore poses are world→camera)
    R_ref_w2c = R_ref.T
    R_curr_w2c = R_curr.T

    # Relative transform: current camera → reference camera
    R_rel = R_ref_w2c @ np.linalg.inv(R_curr_w2c)
    t_rel = R_ref_w2c @ (t_curr - t_ref)

    # Fronto-parallel plane approximation at depth_est
    n = np.array([0.0, 0.0, 1.0])
    H = K @ (R_rel - np.outer(t_rel, n) / depth_est) @ K_inv

    return H


def detect_sift(img: np.ndarray, max_features: int = 1000) \
        -> tuple[list, np.ndarray | None]:
    """
    Detect SIFT keypoints in grayscale image with spatial filtering.

    Args:
        img: RGB image (will be converted to grayscale)
        max_features: max keypoints to detect before spatial filtering

    Returns:
        (kp, desc): spatially filtered keypoints and descriptors, or ([], None)
    """
    if len(img.shape) == 3 and img.shape[2] == 3:
        img_gray = cv2.cvtColor(img, cv2.COLOR_RGB2GRAY)
    else:
        img_gray = img

    h, w = img_gray.shape
    sift = cv2.SIFT_create(nfeatures=max_features)
    kp, desc = sift.detectAndCompute(img_gray, None)

    if not kp or desc is None:
        return [], None

    # Apply spatial filtering to avoid clustering
    kp, desc = spatial_filter_features(kp, desc, h, w, min_density=10.0)

    return kp, desc


def match_features(desc_ref: np.ndarray, desc_cur: np.ndarray,
                   ratio_thresh: float = 0.75) -> list:
    """
    Match SIFT descriptors using Lowe's ratio test.

    Args:
        desc_ref, desc_cur: descriptor arrays (Nx128) or None
        ratio_thresh: Lowe's ratio for good matches

    Returns:
        List of cv2.DMatch objects
    """
    if desc_ref is None or desc_cur is None or len(desc_ref) == 0 or len(desc_cur) == 0:
        return []

    # Use FLANN for faster matching on larger feature sets
    FLANN_INDEX_KDTREE = 1
    index_params = dict(algorithm=FLANN_INDEX_KDTREE, trees=5)
    search_params = dict(checks=50)
    matcher = cv2.FlannBasedMatcher(index_params, search_params)

    # KNN matching with k=2 for Lowe's ratio test
    matches = matcher.knnMatch(desc_ref, desc_cur, k=2)

    # Apply Lowe's ratio test
    good = []
    for m_list in matches:
        if len(m_list) == 2:
            m, n = m_list
            if m.distance < ratio_thresh * n.distance:
                good.append(m)

    return good


def sift_heatmap(kp_ref: list, kp_cur: list, matches: list,
                 H: np.ndarray, img_h: int, img_w: int,
                 motion_threshold: float = 15.0, sigma: float = 20.0,
                 rgb_cur: np.ndarray | None = None) \
        -> tuple[np.ndarray, int, np.ndarray | None]:
    """
    Build heatmap by filtering matched features on residual displacement.

    Strategy: Compute residuals for all matches. If >80% are large, homography is wrong,
    so invert detection (small residuals = motion). Otherwise, large residuals = motion.

    Args:
        kp_ref, kp_cur: SIFT keypoint lists
        matches: DMatch objects (match.queryIdx in ref, match.trainIdx in cur)
        H: homography mapping current pixels → reference pixels
        img_h, img_w: output heatmap dimensions
        motion_threshold: residual threshold (pixels)
        sigma: Gaussian blob radius (pixels)
        rgb_cur: current frame RGB (optional, for visualization)

    Returns:
        (heatmap_u8, n_moving, vis_img): uint8 heatmap, count of moving features, visualization image
    """
    heatmap_float = np.zeros((img_h, img_w), dtype=np.float32)

    # Start visualization with current frame
    vis_img = None
    if rgb_cur is not None:
        vis_img = rgb_cur.copy()

    if not matches or H is None or len(kp_ref) == 0 or len(kp_cur) == 0:
        return (heatmap_float * 0).astype(np.uint8), 0, vis_img

    H_inv = np.linalg.inv(H)
    residuals = []
    positions = []  # (x, y) for each residual

    for m in matches:
        idx_ref = m.queryIdx
        idx_cur = m.trainIdx

        if idx_ref >= len(kp_ref) or idx_cur >= len(kp_cur):
            continue

        kp_r = kp_ref[idx_ref]
        kp_c = kp_cur[idx_cur]

        # Current feature position
        cur_x, cur_y = kp_c.pt

        # Reference feature position
        ref_x, ref_y = kp_r.pt

        # Transform ref feature through H_inv to get expected position in current frame
        p_ref_homo = np.array([ref_x, ref_y, 1.0])
        p_expected_homo = H_inv @ p_ref_homo

        if abs(p_expected_homo[2]) < 1e-9:
            continue

        expected_x = p_expected_homo[0] / p_expected_homo[2]
        expected_y = p_expected_homo[1] / p_expected_homo[2]

        # Residual: distance between expected and actual position
        residual = np.sqrt((cur_x - expected_x)**2 + (cur_y - expected_y)**2)
        residuals.append(residual)
        positions.append((cur_x, cur_y))

    if not residuals:
        return (heatmap_float * 0).astype(np.uint8), 0, vis_img

    # Compute statistics
    med_res = np.median(residuals)
    residuals_arr = np.array(residuals)

    # Use median + IQR to detect outliers (moving features)
    # Features with large residuals relative to the median are probably moving
    q75 = np.percentile(residuals_arr, 75)
    q25 = np.percentile(residuals_arr, 25)
    iqr = q75 - q25

    # Adaptive threshold: use percentile-based outlier detection
    # Features in the top 25% of residuals are flagged as moving
    dynamic_threshold = np.percentile(residuals_arr, 75)

    n_moving = 0
    n_static = 0

    for i, (residual, (cur_x, cur_y)) in enumerate(zip(residuals, positions)):
        is_moving = residual > dynamic_threshold

        # Visualize: X for all matched features, color by residual
        if vis_img is not None:
            pt = (int(cur_x), int(cur_y))
            if is_moving:
                cv2.drawMarker(vis_img, pt, (0, 0, 255), cv2.MARKER_CROSS, 10, 2)  # Red = moving
                n_moving += 1
            else:
                cv2.drawMarker(vis_img, pt, (255, 0, 0), cv2.MARKER_CROSS, 8, 1)   # Blue = static
                n_static += 1

        # Accumulate heatmap at moving features
        if is_moving:
            cv2.circle(heatmap_float, (int(cur_x), int(cur_y)),
                      int(sigma), 1.0, thickness=-1)

    # Apply Gaussian blur to smooth
    if n_moving > 0:
        heatmap_float = cv2.GaussianBlur(heatmap_float, (int(2*sigma+1), int(2*sigma+1)), sigma/2)

    # Normalize to 0–255
    max_val = heatmap_float.max()
    if max_val > 0:
        heatmap_u8 = (heatmap_float / max_val * 255).clip(0, 255).astype(np.uint8)
    else:
        heatmap_u8 = np.zeros((img_h, img_w), dtype=np.uint8)

    # Debug output
    moving_ratio = n_moving / (n_moving + n_static) if (n_moving + n_static) > 0 else 0
    print(f"    Residuals: {len(residuals)} total, "
          f"med={med_res:.1f}px, q75={dynamic_threshold:.1f}px | "
          f"Moving: {n_moving} ({moving_ratio*100:.0f}%), Static: {n_static}")

    return heatmap_u8, n_moving, vis_img
