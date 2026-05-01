from __future__ import annotations

import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(PROJECT_ROOT))

from analysis.snooker.editor import BallObservationSeed, SnookerPongtownEditor, pongtown_pb2


def _record(frame_idx: int) -> pongtown_pb2.PongtownResponse:
    rec = pongtown_pb2.PongtownResponse()
    rec.frame_identifier.timestamp_ns = 1_000_000_000 + frame_idx
    rec.frame_identifier.frame_number = 100 + frame_idx
    rec.frame_output.frame_idx = frame_idx
    rec.frame_output.has_pose = True
    rec.frame_output.T_table_to_camera.extend(
        [
            1.0,
            0.0,
            0.0,
            0.0,
            0.0,
            1.0,
            0.0,
            0.0,
            0.0,
            0.0,
            1.0,
            1000.0,
            0.0,
            0.0,
            0.0,
            1.0,
        ]
    )
    dbg = rec.pnp_frame_debug.add()
    dbg.frame_idx = frame_idx
    dbg.camera_matrix.extend([100.0, 0.0, 50.0, 0.0, 100.0, 50.0, 0.0, 0.0, 1.0])
    return rec


def _model(frame_count: int = 5) -> SnookerPongtownEditor:
    summary = pongtown_pb2.PongtownResponse()
    summary.table_width_mm = 3569.0
    summary.table_height_mm = 1778.0
    return SnookerPongtownEditor([*[_record(i) for i in range(frame_count)], summary])


def _summary_position(
    summary: pongtown_pb2.PongtownResponse,
    *,
    observation_idx: int,
    frame_idx: int,
    track_id: int,
    x_mm: float,
    y_mm: float,
) -> None:
    pos = summary.ball_trajectory.positions.add()
    pos.observation_idx = observation_idx
    pos.frame_idx = frame_idx
    pos.frame_number = 100 + frame_idx
    pos.timestamp_ns = 1_000_000_000 + frame_idx
    pos.track_id = track_id
    pos.confidence = 0.8
    pos.has_table_position = True
    pos.table_xyz_mm.extend([x_mm, y_mm, 0.0])


def test_change_color_future_matches_by_near_position() -> None:
    model = _model()
    for i in range(model.frame_count):
        model.add_ball(i, 2, i * 20.0, 10.0)
    model.add_ball(3, 2, 900.0, 700.0)

    changed = model.change_color_future(0, 0, 6)

    assert changed == 5
    assert [model.balls(i)[0].track_id for i in range(model.frame_count)] == [6, 6, 6, 6, 6]
    assert model.balls(3)[1].track_id == 2


def test_delete_future_removes_only_continuous_near_matches() -> None:
    model = _model()
    for i in range(model.frame_count):
        model.add_ball(i, 1, i * 15.0, 0.0)
    model.add_ball(4, 1, 1200.0, 0.0)

    removed = model.delete_future(1, 0)

    assert removed == 4
    assert len(model.balls(0)) == 1
    assert len(model.balls(1)) == 0
    assert len(model.balls(4)) == 1
    assert pytest.approx(model.ball_xy(4, 0)[0]) == 1200.0


def test_move_future_updates_table_and_projected_image_coordinates() -> None:
    model = _model(3)
    for i in range(model.frame_count):
        model.add_ball(i, 1, i * 10.0, i * 5.0)

    moved = model.move_future(0, 0, 100.0, 200.0)

    assert moved == 3
    for i in range(model.frame_count):
        ball = model.balls(i)[0]
        assert tuple(ball.table_xyz_mm[:2]) == (100.0, 200.0)
        assert pytest.approx(ball.u_img) == 60.0
        assert pytest.approx(ball.v_img) == 70.0


def test_interpolate_creates_missing_frame_positions_and_rebuilds_summary() -> None:
    model = _model(5)
    model.add_ball(0, 4, -100.0, -100.0)

    changed = model.interpolate(0, 0, 0, 4, (-100.0, -100.0), (100.0, 100.0))
    model.rebuild_summary()

    assert changed == 5
    assert [len(model.balls(i)) for i in range(5)] == [1, 1, 1, 1, 1]
    assert tuple(model.ball_xy(2, 0)) == (0.0, 0.0)
    assert len(model.summary.ball_trajectory.positions) == 5
    assert model.summary.ball_trajectory.positions[2].interpolated


def test_load_hydrates_per_frame_positions_from_pongtown_summary() -> None:
    summary = pongtown_pb2.PongtownResponse()
    summary.table_width_mm = 3569.0
    summary.table_height_mm = 1778.0
    _summary_position(
        summary,
        observation_idx=0,
        frame_idx=2,
        track_id=3,
        x_mm=250.0,
        y_mm=-125.0,
    )

    model = SnookerPongtownEditor([*[_record(i) for i in range(4)], summary])

    assert model.first_frame_with_balls() == 2
    assert len(model.balls(2)) == 1
    assert model.balls(2)[0].track_id == 3
    assert model.ball_xy(2, 0) == (250.0, -125.0)


def test_load_hydrates_per_frame_positions_from_snooker_tracking() -> None:
    records = [_record(i) for i in range(3)]
    records[1].sport_mode = pongtown_pb2.PongtownResponse.SNOOKER
    snooker_ball = records[1].snooker_tracking.ball_positions.add()
    snooker_ball.object_id = 42
    snooker_ball.label = "blue ball"
    snooker_ball.frame_idx = 1
    snooker_ball.frame_number = 101
    snooker_ball.timestamp_ns = 1_000_000_001
    snooker_ball.u_img = 80.0
    snooker_ball.v_img = 90.0
    snooker_ball.area_px = 33
    snooker_ball.confidence = 0.9
    snooker_ball.has_table_position = True
    snooker_ball.table_xyz_mm.extend([300.0, -200.0, 0.0])
    snooker_ball.cam_xyz_mm.extend([300.0, -200.0, 1000.0])
    snooker_ball.inside_table = True

    summary = pongtown_pb2.PongtownResponse()
    summary.table_width_mm = 3569.0
    summary.table_height_mm = 1778.0
    summary.sport_mode = pongtown_pb2.PongtownResponse.SNOOKER
    summary.snooker_tracking.observed_frames = 1
    summary.snooker_tracking.total_observations = 1

    model = SnookerPongtownEditor([*records, summary])

    assert len(model.balls(1)) == 1
    ball = model.balls(1)[0]
    assert ball.track_id == 6
    assert ball.area_px == 33
    assert ball.confidence == pytest.approx(0.9)
    assert tuple(ball.table_xyz_mm[:2]) == pytest.approx((300.0, -200.0))


def test_load_does_not_duplicate_existing_pongtown_positions() -> None:
    records = [_record(i) for i in range(3)]
    ball = records[1].ball_positions.add()
    ball.observation_idx = 7
    ball.frame_idx = 1
    ball.track_id = 2
    ball.has_table_position = True
    ball.table_xyz_mm.extend([50.0, 60.0, 0.0])

    summary = pongtown_pb2.PongtownResponse()
    summary.table_width_mm = 3569.0
    summary.table_height_mm = 1778.0
    _summary_position(
        summary,
        observation_idx=7,
        frame_idx=1,
        track_id=2,
        x_mm=50.0,
        y_mm=60.0,
    )

    model = SnookerPongtownEditor([*records, summary])

    assert model.first_frame_with_balls() == 1
    assert len(model.balls(1)) == 1


def test_image_observation_seed_adds_projected_ball_position() -> None:
    model = _model(2)

    added = model.ensure_ball_positions_from_image_observations(
        {
            100: [
                BallObservationSeed(
                    u_img=60.0,
                    v_img=70.0,
                    area_px=12,
                    confidence=0.75,
                    track_id=2,
                )
            ]
        }
    )

    assert added == 1
    assert model.total_ball_positions() == 1
    ball = model.balls(0)[0]
    assert ball.track_id == 2
    assert ball.area_px == 12
    assert ball.confidence == pytest.approx(0.75)
    assert ball.has_table_position
    assert tuple(ball.table_xyz_mm[:2]) == pytest.approx((100.0, 200.0))
    assert tuple(ball.cam_xyz_mm) == pytest.approx((100.0, 200.0, 1000.0))
    assert len(model.summary.ball_trajectory.positions) == 1


def test_rebuild_summary_mirrors_positions_into_snooker_tracking() -> None:
    model = _model(2)
    model.add_ball(0, 2, 10.0, 20.0)
    model.add_ball(1, 8, -30.0, 40.0)

    model.rebuild_summary()

    assert model.frames[0].sport_mode == pongtown_pb2.PongtownResponse.SNOOKER
    assert model.frames[0].WhichOneof("tracking") == "snooker_tracking"
    assert model.frames[0].snooker_tracking.ball_positions[0].label == "red ball"
    assert model.frames[0].snooker_tracking.ball_positions[0].object_id == 1
    assert tuple(model.frames[0].snooker_tracking.ball_positions[0].table_xyz_mm[:2]) == pytest.approx(
        (10.0, 20.0)
    )
    assert model.frames[1].snooker_tracking.ball_positions[0].label == "black ball"
    assert model.frames[1].snooker_tracking.ball_positions[0].object_id == 2
    assert model.summary.sport_mode == pongtown_pb2.PongtownResponse.SNOOKER
    assert model.summary.WhichOneof("tracking") == "snooker_tracking"
    assert model.summary.snooker_tracking.observed_frames == 2
    assert model.summary.snooker_tracking.total_observations == 2
    assert len(model.summary.snooker_tracking.ball_positions) == 0


def test_copy_ball_positions_from_previous_frame_replaces_current_metadata() -> None:
    model = _model(3)
    model.add_ball(0, 2, 10.0, 20.0)
    model.add_ball(1, 6, 300.0, 400.0)

    changed = model.copy_ball_positions_from_previous_frames([1])

    assert changed == 1
    assert len(model.balls(1)) == 1
    copied = model.balls(1)[0]
    assert copied.track_id == 2
    assert copied.interpolated
    assert copied.frame_idx == 1
    assert copied.frame_number == 101
    assert tuple(copied.table_xyz_mm[:2]) == pytest.approx((10.0, 20.0))
    assert len(model.summary.ball_trajectory.positions) == 2
