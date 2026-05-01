import asyncio
import json
import time
from pathlib import Path

import pytest

import genspark.server as gs
from genspark.main import McpToolRunner, TOOLS


ROOT = Path(__file__).resolve().parents[3]
RECORDINGS = ROOT / "recordings"


DIRECTLY_COVERED_TOOLS = {
    "scene_context",
    "scene_emphasis",
    "list_available_analyses",
    "get_recording_metadata",
    "segmentation_list_identified_objects",
    "segmentation_get_objects_at_time",
    "segmentation_get_object_track",
    "segmentation_get_relative_position",
    "motioncap_list_tracks",
    "motioncap_get_track_summary",
    "motioncap_get_moving_objects",
    "motioncap_find_extrema",
    "motioncap_estimate_period",
    "get_motioncap_tracks",
    "pongtown_get_table_pose_at_time",
    "pongtown_get_ball_trajectory",
    "pongtown_get_table_bounces",
    "pongtown_get_ball_speed",
    "slam_get_map",
    "slam_get_pose_at_time",
    "slam_get_velocity_at_time",
    "slam_get_position_on_road",
    "slam_get_road_width_at_time",
    "slam_get_lap_progress",
    "slam_get_motion_between",
}


def _recording(relative: str) -> Path:
    path = RECORDINGS / relative
    if not path.exists():
        pytest.skip(f"fixture recording not found: {path}")
    return path


def _timed(max_seconds: float, fn, *args, **kwargs):
    start = time.perf_counter()
    value = fn(*args, **kwargs)
    elapsed = time.perf_counter() - start
    assert elapsed < max_seconds
    return value, elapsed


def _timed_json(max_seconds: float, fn, *args, **kwargs) -> tuple[dict, float]:
    value, elapsed = _timed(max_seconds, fn, *args, **kwargs)
    return json.loads(value), elapsed


def test_direct_fixture_coverage_matches_advertised_tools() -> None:
    assert DIRECTLY_COVERED_TOOLS == {tool["name"] for tool in TOOLS}


def test_core_segmentation_and_motioncap_tools_on_pendulum_fixture() -> None:
    gs.set_recording(_recording("20260429_200000_pendulum_demo/20260429_200000.vis.pb"))

    scene_context, _ = _timed(10.0, gs.scene_context, gs.SceneType.EXPERIMENT_PENDULUM)
    assert "experiment-pendulum" in scene_context

    pingpong_context, _ = _timed(10.0, gs.scene_context, gs.SceneType.SPORT_PINGPONG)
    assert "Pongtown" in pingpong_context

    snooker_context, _ = _timed(10.0, gs.scene_context, gs.SceneType.SPORT_SNOOKER)
    assert "sport-snooker" in snooker_context

    bike_context, _ = _timed(10.0, gs.scene_context, gs.SceneType.SPORT_BIKE)
    assert "SLAM" in bike_context

    emphasis, _ = _timed(10.0, gs.scene_emphasis, 0.0, 7.0, "benchmark")
    assert "Marked emphasis" in emphasis

    analyses, _ = _timed_json(10.0, gs.list_available_analyses)
    assert analyses["analyses"]["segmentation"]["available"] is True
    assert analyses["analyses"]["motioncap"]["available"] is True

    metadata, _ = _timed_json(10.0, gs.get_recording_metadata)
    assert metadata["available"] is True
    duration = min(10.0, float(metadata["duration_s"]))

    listed, _ = _timed_json(10.0, gs.segmentation_list_identified_objects)
    assert listed["available"] is True
    assert listed["objects"]
    object_id = int(listed["objects"][0]["object_id"])

    snapshot, _ = _timed_json(10.0, gs.segmentation_get_objects_at_time, 0.0)
    assert snapshot["available"] is True
    assert snapshot["geometry_included"] is False
    assert snapshot["objects"]

    snapshot_with_geometry, _ = _timed_json(
        10.0, gs.segmentation_get_objects_at_time, 0.0, include_geometry=True
    )
    assert snapshot_with_geometry["available"] is True
    assert snapshot_with_geometry["geometry_included"] is True

    track, _ = _timed_json(10.0, gs.segmentation_get_object_track, object_id, limit=10)
    assert track["available"] is True
    assert track["positions"]

    if len(listed["objects"]) >= 2:
        second_object_id = int(listed["objects"][1]["object_id"])
        relative, _ = _timed_json(
            10.0,
            gs.segmentation_get_relative_position,
            str(object_id),
            str(second_object_id),
        )
        assert relative["available"] in (True, False)

    raft_tracks, _ = _timed_json(10.0, gs.motioncap_list_tracks, False)
    assert raft_tracks["available"] is True
    raft_id = int(raft_tracks["tracks"][0]["track_id"])

    segmentation_tracks, _ = _timed_json(10.0, gs.motioncap_list_tracks, True)
    assert segmentation_tracks["available"] is True
    seg_id = int(segmentation_tracks["tracks"][0]["track_id"])

    for segmentation, track_id in ((False, raft_id), (True, seg_id)):
        summary, _ = _timed_json(10.0, gs.motioncap_get_track_summary, track_id, segmentation=segmentation)
        assert summary["available"] is True

        moving, _ = _timed_json(
            10.0, gs.motioncap_get_moving_objects, 0.0, duration, segmentation=segmentation
        )
        assert moving["available"] is True

        extrema, _ = _timed_json(10.0, gs.motioncap_find_extrema, track_id, axis="x", segmentation=segmentation)
        assert extrema["available"] is True

        period, _ = _timed_json(10.0, gs.motioncap_estimate_period, track_id, axis="x", segmentation=segmentation)
        assert period["available"] is True

        tracks, _ = _timed_json(
            10.0,
            gs.get_motioncap_tracks,
            0.0,
            duration,
            segmentation=segmentation,
            limit_per_track=50,
        )
        assert tracks["available"] is True


def test_pongtown_tools_on_tabletennis_and_snooker_fixtures() -> None:
    gs.set_recording(_recording("20260425_140029_tabletennis_home/20260425_140029.vis.pb"))

    table_pose, _ = _timed_json(10.0, gs.pongtown_get_table_pose_at_time, 0.0)
    assert table_pose["available"] is True

    trajectory, _ = _timed_json(10.0, gs.pongtown_get_ball_trajectory, limit=10)
    assert trajectory["available"] is True
    assert trajectory["positions"]

    bounces, _ = _timed_json(10.0, gs.pongtown_get_table_bounces, from_time=0.0, to_time=10.0)
    assert bounces["available"] is True

    speed, _ = _timed_json(10.0, gs.pongtown_get_ball_speed, 0.0, 10.0, "image")
    assert speed["available"] is True

    gs.set_recording(_recording("20260426_221334_snooker_topview/20260426_221334.vis.pb"))
    snooker_table, _ = _timed_json(10.0, gs.pongtown_get_table_pose_at_time, 0.0)
    assert snooker_table["available"] is True

    snooker_trajectory, _ = _timed_json(10.0, gs.pongtown_get_ball_trajectory, limit=10)
    assert snooker_trajectory["available"] in (True, False)


def test_slam_tools_on_bike_fixture() -> None:
    gs.set_recording(_recording("20260326_124404_bike_home/20260326_124404.vis.pb"))

    slam_map, _ = _timed_json(10.0, gs.slam_get_map, limit=10)
    assert slam_map["available"] is True
    assert slam_map["map"]

    pose, _ = _timed_json(10.0, gs.slam_get_pose_at_time, 0.0)
    assert pose["available"] is True

    velocity, _ = _timed_json(10.0, gs.slam_get_velocity_at_time, 1.0)
    assert velocity["available"] is True

    position, _ = _timed_json(10.0, gs.slam_get_position_on_road, 1.0)
    assert position["available"] in (True, False)

    width, _ = _timed_json(10.0, gs.slam_get_road_width_at_time, 1.0)
    assert width["available"] in (True, False)

    lap, _ = _timed_json(10.0, gs.slam_get_lap_progress, 1.0)
    assert lap["available"] in (True, False)

    motion, _ = _timed_json(10.0, gs.slam_get_motion_between, 0.0, 10.0)
    assert motion["available"] is True


def test_missing_analysis_tools_fail_gracefully() -> None:
    gs.set_recording(_recording("20260425_140029_tabletennis_home/20260425_140029.vis.pb"))
    missing_motion, _ = _timed_json(10.0, gs.motioncap_list_tracks, False)
    assert missing_motion["available"] is False
    assert missing_motion["error_type"] == "analysis_missing"
    assert missing_motion["retry_recommended"] is False

    gs.set_recording(_recording("20260429_200000_pendulum_demo/20260429_200000.vis.pb"))
    missing_pong, _ = _timed_json(10.0, gs.pongtown_get_table_pose_at_time, 0.0)
    assert missing_pong["available"] is False
    assert missing_pong["error_type"] == "analysis_missing"
    assert missing_pong["retry_recommended"] is False

    missing_slam, _ = _timed_json(10.0, gs.slam_get_map, limit=10)
    assert missing_slam["available"] is False
    assert missing_slam["error_type"] == "analysis_missing"
    assert missing_slam["retry_recommended"] is False


class _FakeTimeoutSession:
    async def call_tool(self, name: str, args: dict) -> None:
        await asyncio.sleep(1)


class _FakeErrorSession:
    async def call_tool(self, name: str, args: dict) -> None:
        raise RuntimeError("boom")


def test_mcp_runner_returns_nonretryable_timeout_and_server_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GENSPARK_TOOL_TIMEOUT_SECONDS", "0.01")
    timeout_runner = McpToolRunner(Path("unused.vis.pb"))
    timeout_runner._session = _FakeTimeoutSession()
    timeout = json.loads(timeout_runner.call_tool("slow_tool", {}))
    timeout_runner.stop()
    assert timeout["available"] is False
    assert timeout["error_type"] == "tool_timeout"
    assert timeout["retry_recommended"] is False

    error_runner = McpToolRunner(Path("unused.vis.pb"))
    error_runner._session = _FakeErrorSession()
    error = json.loads(error_runner.call_tool("broken_tool", {}))
    error_runner.stop()
    assert error["available"] is False
    assert error["error_type"] == "tool_server_error"
    assert error["retry_recommended"] is False
