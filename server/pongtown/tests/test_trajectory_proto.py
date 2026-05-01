from types import SimpleNamespace

import numpy as np

from pongtown.main import (
    _populate_ball_trajectory_proto,
    pongtown_pb2,
)
from pongtown.trajectory import extract_ball_trajectory


def _synthetic_result(frame_idx: int, v_img: int) -> dict:
    mask = np.zeros((12, 12), dtype=bool)
    mask[v_img, 5] = True
    mask[v_img, 6] = True

    intrinsics = np.array(
        [[100.0, 0.0, 5.0], [0.0, 100.0, 5.0], [0.0, 0.0, 1.0]],
        dtype=np.float64,
    )
    T_table_to_camera = np.eye(4, dtype=np.float64)
    T_table_to_camera[2, 3] = 1000.0

    return {
        "bundle": SimpleNamespace(
            frame_idx=frame_idx,
            frame_number=100 + frame_idx,
            timestamp_ns=frame_idx * 1_000_000_000,
            ball_mask=mask,
            intrinsics=intrinsics,
        ),
        "T_final_table_to_camera": T_table_to_camera,
    }


def test_ball_trajectory_populates_summary_proto() -> None:
    cfg = {
        "table": {"width_mm": 2740, "height_mm": 1525},
        "trajectory": {
            "min_bounce_prominence_px": 0.5,
            "min_bounce_spacing_frames": 1,
            "smooth_sigma": 0.0,
        },
    }
    results = [_synthetic_result(i, v) for i, v in enumerate([5, 6, 7, 6, 5])]

    trajectory = extract_ball_trajectory(results, cfg)

    summary = pongtown_pb2.PongtownResponse()
    _populate_ball_trajectory_proto(summary.pingpong_tracking.ball_trajectory, trajectory)

    assert len(summary.pingpong_tracking.ball_trajectory.positions) == 5
    assert len(summary.pingpong_tracking.ball_trajectory.segments) == 4
    assert len(summary.pingpong_tracking.ball_trajectory.bounces) == 1
    assert len(results[0]["ball_positions"]) == 1

    bounce = summary.pingpong_tracking.ball_trajectory.bounces[0]
    assert bounce.frame_idx == 2
    assert bounce.has_table_position
    assert bounce.prominence_px > 0

    segment = summary.pingpong_tracking.ball_trajectory.segments[0]
    assert segment.has_table_displacement
    assert segment.dt_s == 1.0
