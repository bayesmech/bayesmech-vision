import math

import numpy as np
import pytest

from pongtown.geometry import (
    circular_median_mod_pi,
    geometric_median,
    line_from_two_points,
    line_intersection,
    point_line_distance,
    polygon_iou,
    signed_distance_to_line,
)


def test_line_from_two_points_horizontal():
    p1 = np.array([0.0, 5.0])
    p2 = np.array([3.0, 5.0])
    a, b, c = line_from_two_points(p1, p2)
    # Line is y = 5 (horizontal). Direction vector should have no y component.
    assert abs(a) < 1e-9
    # Normalisation: a^2 + b^2 = 1
    assert abs(a * a + b * b - 1.0) < 1e-9
    # Both points lie on the line.
    assert abs(a * p1[0] + b * p1[1] + c) < 1e-9
    assert abs(a * p2[0] + b * p2[1] + c) < 1e-9


def test_line_intersection_perpendicular():
    L1 = line_from_two_points(np.array([0.0, 0.0]), np.array([1.0, 0.0]))  # y=0
    L2 = line_from_two_points(np.array([3.0, -2.0]), np.array([3.0, 4.0]))  # x=3
    p = line_intersection(L1, L2)
    assert p is not None
    assert abs(p[0] - 3.0) < 1e-9 and abs(p[1] - 0.0) < 1e-9


def test_line_intersection_parallel_returns_none():
    L1 = line_from_two_points(np.array([0.0, 0.0]), np.array([1.0, 0.0]))
    L2 = line_from_two_points(np.array([0.0, 1.0]), np.array([1.0, 1.0]))
    assert line_intersection(L1, L2) is None


def test_point_line_distance():
    L = line_from_two_points(np.array([0.0, 0.0]), np.array([1.0, 0.0]))  # y=0
    pts = np.array([[0.0, 3.0], [5.0, -4.0], [10.0, 0.0]])
    d = point_line_distance(pts, L)
    np.testing.assert_allclose(d, [3.0, 4.0, 0.0], atol=1e-9)


def test_signed_distance_keeps_sign():
    L = line_from_two_points(np.array([0.0, 0.0]), np.array([1.0, 0.0]))
    pts = np.array([[0.0, 3.0], [0.0, -3.0]])
    d = signed_distance_to_line(pts, L)
    assert d[0] * d[1] < 0  # opposite sides


def test_polygon_iou_identical():
    poly = np.array([[10, 10], [90, 10], [90, 90], [10, 90]])
    iou = polygon_iou(poly, poly, image_shape=(100, 100))
    assert iou == pytest.approx(1.0)


def test_polygon_iou_disjoint():
    a = np.array([[0, 0], [10, 0], [10, 10], [0, 10]])
    b = np.array([[50, 50], [60, 50], [60, 60], [50, 60]])
    iou = polygon_iou(a, b, image_shape=(100, 100))
    assert iou == pytest.approx(0.0)


def test_polygon_iou_with_denominator_mask_excludes_pixels():
    # Both polys share half overlap; without exclusion IoU = 1/3.
    # Mask out the non-overlap region from denominator → IoU rises.
    a = np.array([[0, 0], [50, 0], [50, 100], [0, 100]])
    b = np.array([[25, 0], [75, 0], [75, 100], [25, 100]])
    base = polygon_iou(a, b, image_shape=(100, 100))
    assert 0.30 < base < 0.36

    # Mask removes the parts of each polygon that are outside the overlap.
    excl = np.zeros((100, 100), dtype=bool)
    excl[:, :25] = True
    excl[:, 50:] = True
    boosted = polygon_iou(a, b, image_shape=(100, 100), denom_exclude_mask=excl)
    assert boosted == pytest.approx(1.0)


def test_geometric_median_robust_to_outlier():
    pts = np.array([[0.0, 0.0], [0.1, 0.0], [0.0, 0.1], [0.05, 0.05], [100.0, 100.0]])
    m = geometric_median(pts)
    assert np.linalg.norm(m - np.array([0.05, 0.05])) < 0.5  # outlier ignored


def test_circular_median_mod_pi():
    # 0, π are equivalent mod π; throw in some noise.
    angles = np.array([0.01, -0.02, math.pi - 0.01, math.pi + 0.03, 0.0])
    m = circular_median_mod_pi(angles)
    # Expect close to 0 mod π.
    assert min(abs(m), abs(m - math.pi)) < 0.05
