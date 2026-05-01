import cv2
import numpy as np
import yaml
from pathlib import Path

from pongtown.pose import (
    canonical_corners_mm,
    canonical_midline_mm,
    solve_table_pose,
)
from pongtown.main import _config_for_mode, _net_enabled


def _cfg() -> dict:
    return yaml.safe_load(open(Path(__file__).resolve().parent.parent / "config.yaml"))


def _project(P: np.ndarray, K: np.ndarray, R: np.ndarray, t: np.ndarray) -> np.ndarray:
    cam = (R @ P.T).T + t
    cam = cam / cam[:, 2:3]
    return (K @ cam.T).T[:, :2]


def test_pnp_recovers_known_pose_round_trip():
    K = np.array([[800.0, 0, 640], [0, 800.0, 360], [0, 0, 1.0]])
    cfg = _cfg()
    P_corners = canonical_corners_mm()
    P_mid = canonical_midline_mm()

    rvec_gt = np.array([np.deg2rad(150), 0.0, 0.0])
    R_gt, _ = cv2.Rodrigues(rvec_gt)
    t_gt = np.array([0.0, 500.0, 3000.0])

    quad_img = _project(P_corners, K, R_gt, t_gt)
    midline_img = _project(P_mid, K, R_gt, t_gt)

    res = solve_table_pose(quad_img, midline_img, K, cfg=cfg)
    assert res.success
    assert np.linalg.norm(res.tvec - t_gt) < 50.0
    R_est, _ = cv2.Rodrigues(res.rvec)
    reproj = _project(P_corners, K, R_est, res.tvec.reshape(3))
    assert np.max(np.linalg.norm(reproj - quad_img, axis=1)) < 1.0


def test_pnp_works_without_midline():
    K = np.array([[800.0, 0, 640], [0, 800.0, 360], [0, 0, 1.0]])
    cfg = _cfg()
    P_corners = canonical_corners_mm()
    rvec_gt = np.array([np.deg2rad(140), 0.0, 0.0])
    R_gt, _ = cv2.Rodrigues(rvec_gt)
    t_gt = np.array([0.0, 300.0, 2500.0])
    quad_img = _project(P_corners, K, R_gt, t_gt)

    res = solve_table_pose(quad_img, None, K, cfg=cfg)
    assert res.success
    assert res.rvec is not None
    R_est, _ = cv2.Rodrigues(res.rvec)
    reproj = _project(P_corners, K, R_est, res.tvec.reshape(3))
    assert np.max(np.linalg.norm(reproj - quad_img, axis=1)) < 1.0


def test_snooker_mode_config_uses_full_table_no_net():
    cfg = _config_for_mode(_cfg(), "snooker")
    assert cfg["table"]["width_mm"] == 3569
    assert cfg["table"]["height_mm"] == 1778
    assert not _net_enabled(cfg)
    assert "green snooker table top" in cfg["mask_labels"]["table_top"]
    assert cfg["quad"]["score_full_table_mask"]
