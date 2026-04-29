import cv2
import numpy as np
import yaml
from pathlib import Path

from pongtown.quad_fit import fit_table_quadrilateral


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


def test_handles_partial_occlusion_without_crashing():
    # Right portion of the table is occluded. The algorithm should either
    # produce a valid quad for the visible portion (primary path) or fall
    # back to QUAD_FROM_MIDLINE recovery — but should not crash and should
    # not produce a wildly inconsistent result.
    H, W = 480, 640
    corners = np.array([[100, 80], [540, 80], [540, 400], [100, 400]], dtype=np.float32)
    table_mask = _draw_filled_quad((H, W), corners)
    table_mask[:, 200:] = False
    net_corners = np.array([[318, 50], [322, 50], [322, 430], [318, 430]], dtype=np.float32)
    net_mask = _draw_filled_quad((H, W), net_corners)

    res = fit_table_quadrilateral(table_mask, net_mask, cfg=_load_cfg())
    assert res.quad_img is not None
    assert res.midline_img is not None
    # Either a primary fit on the visible portion (right≈200) or a recovery
    # that reflects through midline (right≈540) is acceptable.
    right_x = float(np.max(res.quad_img[:, 0]))
    assert (abs(right_x - 200) < 10) or (abs(right_x - 540) < 12)
