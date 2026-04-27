import cv2
import numpy as np
import yaml
from pathlib import Path

from pongtown.quad_fit import fit_table_quadrilateral, METHOD_QUAD_FROM_MIDLINE


def _load_cfg() -> dict:
    p = Path(__file__).resolve().parent.parent / "config.yaml"
    return yaml.safe_load(open(p))


def _draw_filled_quad(shape, corners) -> np.ndarray:
    m = np.zeros(shape, dtype=np.uint8)
    cv2.fillPoly(m, [np.asarray(corners, np.int32).reshape(-1, 1, 2)], 1)
    return m.astype(bool)


def test_fits_axis_aligned_rectangle_with_net():
    # Table: 440 wide x 320 tall, so long axis is horizontal (x).
    # Net midline must be parallel to the short edges (vertical), at x≈320.
    H, W = 480, 640
    corners = np.array([[100, 80], [540, 80], [540, 400], [100, 400]], dtype=np.float32)
    table_mask = _draw_filled_quad((H, W), corners)
    # Net is a vertical strip at x≈320, extending past the table top/bottom
    # (analog of the 152.5 mm overhang).
    net_corners = np.array([[318, 50], [322, 50], [322, 430], [318, 430]], dtype=np.float32)
    net_mask = _draw_filled_quad((H, W), net_corners)

    res = fit_table_quadrilateral(table_mask, net_mask, cfg=_load_cfg())
    assert res.quad_img is not None
    sorted_actual = res.quad_img[np.lexsort((res.quad_img[:, 1], res.quad_img[:, 0]))]
    sorted_expected = corners[np.lexsort((corners[:, 1], corners[:, 0]))]
    assert np.allclose(sorted_actual, sorted_expected, atol=4.0)
    assert res.midline_img is not None
    # Midline endpoints should sit near x≈320 (one of the net's vertical edges).
    assert abs(res.midline_img[0, 0] - 320) < 5
    assert abs(res.midline_img[1, 0] - 320) < 5


def test_recovers_when_short_edge_occluded():
    # Same layout, but make the right SHORT edge unfittable (jagged) so RANSAC
    # finds only 3 strong lines and must fall back to the midline reflection.
    H, W = 480, 640
    corners = np.array([[100, 80], [540, 80], [540, 400], [100, 400]], dtype=np.float32)
    table_mask = _draw_filled_quad((H, W), corners)
    rng = np.random.default_rng(0)
    for y in range(80, 401):
        cutoff = 540 - int(rng.integers(0, 80))
        table_mask[y, cutoff:] = False
    net_corners = np.array([[318, 50], [322, 50], [322, 430], [318, 430]], dtype=np.float32)
    net_mask = _draw_filled_quad((H, W), net_corners)

    res = fit_table_quadrilateral(table_mask, net_mask, cfg=_load_cfg())
    assert res.method == METHOD_QUAD_FROM_MIDLINE
    assert res.quad_img is not None
    right_x = max(res.quad_img[:, 0])
    assert abs(right_x - 540) < 8
