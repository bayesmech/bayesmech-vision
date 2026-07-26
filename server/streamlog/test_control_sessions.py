import struct
from pathlib import Path

import pytest

from proto import control_pb2, perceiver_pb2
from streamlog.control_sessions import ControlSessionError, ControlSessionManager


def _write_manifest(project_dir: Path) -> Path:
    project = control_pb2.ControlProject(
        project_id="20260726_120000_robot_car",
        display_name="Robot Car",
        project_type=control_pb2.ROBOT_CAR,
        created_timestamp_ms=1_753_516_800_000,
        devices=[
            control_pb2.ControlDevice(
                device_id="robocar-1",
                display_name="Robot Car",
                device_type=control_pb2.ROBOT_CAR_DEVICE,
                role=control_pb2.PRIMARY_DEVICE,
                control_host="192.168.4.1",
                control_port=80,
                stream_host="192.168.4.2",
                stream_port=81,
                recording_file="car.vis.pb",
                capabilities=["video", "drive", "ultrasonic"],
                enabled=True,
            ),
            control_pb2.ControlDevice(
                device_id="phone",
                display_name="Phone",
                device_type=control_pb2.PHONE_DEVICE,
                role=control_pb2.AUGMENTED_DEVICE,
                stream_port=8080,
                recording_file="phone.vis.pb",
                capabilities=["video", "imu"],
                enabled=True,
            ),
        ],
    )
    manifest_path = project_dir / "project.control.pb"
    manifest_path.write_bytes(project.SerializeToString())
    return manifest_path


def _read_frames(path: Path) -> list[perceiver_pb2.PerceiverDataFrame]:
    frames = []
    with path.open("rb") as source:
        while header := source.read(4):
            length = struct.unpack(">I", header)[0]
            frame = perceiver_pb2.PerceiverDataFrame()
            frame.ParseFromString(source.read(length))
            frames.append(frame)
    return frames


def test_control_session_keeps_robot_and_phone_in_separate_recordings(tmp_path):
    recordings = tmp_path / "recordings"
    project_dir = recordings / "20260726_120000_robot_car"
    project_dir.mkdir(parents=True)
    manifest_path = _write_manifest(project_dir)
    manager = ControlSessionManager(recordings)

    opened = manager.open(manifest_path)
    assert opened["project_id"] == "20260726_120000_robot_car"
    assert manager.robot_profiles() == [
        {
            "id": "robocar-1",
            "name": "Robot Car",
            "controller_url": "http://192.168.4.1",
            "camera_url": "http://192.168.4.2",
            "camera_stream_url": "http://192.168.4.2:81/stream",
        }
    ]

    manager.write_video(
        "robocar-1",
        b"\xff\xd8robot-jpeg\xff\xd9",
        160,
        120,
        {"valid": True, "filtered_mm": 400, "seq": 9, "age_ms": 6},
    )
    phone_frame = perceiver_pb2.PerceiverDataFrame()
    phone_frame.frame_identifier.device_id = "pixel-1"
    phone_frame.frame_identifier.frame_number = 42
    phone_frame.rgb_frame.format = perceiver_pb2.ImageFrame.JPEG
    phone_frame.rgb_frame.data = b"\xff\xd8phone-jpeg\xff\xd9"
    manager.write_perceiver(phone_frame, "pixel-1")
    manager.close()

    robot_frames = _read_frames(project_dir / "car.vis.pb")
    phone_frames = _read_frames(project_dir / "phone.vis.pb")
    assert len(robot_frames) == 1
    assert len(phone_frames) == 1
    ultrasonic = robot_frames[0].ultrasonic_sensor_data
    assert ultrasonic.valid is True
    assert ultrasonic.distance_meters == pytest.approx(0.4)
    assert ultrasonic.normalized_distance == pytest.approx(0.1)
    assert 0 <= ultrasonic.normalized_distance <= 1
    assert phone_frames[0].frame_identifier.device_id == "pixel-1"
    assert not phone_frames[0].HasField("ultrasonic_sensor_data")


def test_control_session_rejects_manifest_outside_recordings(tmp_path):
    recordings = tmp_path / "recordings"
    recordings.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    manifest_path = _write_manifest(outside)
    manager = ControlSessionManager(recordings)

    with pytest.raises(ControlSessionError, match="recordings directory"):
        manager.open(manifest_path)


def test_robot_profile_changes_are_persisted_for_multiple_devices(tmp_path):
    recordings = tmp_path / "recordings"
    project_dir = recordings / "20260726_120000_robot_car"
    project_dir.mkdir(parents=True)
    manifest_path = _write_manifest(project_dir)
    manager = ControlSessionManager(recordings)
    manager.open(manifest_path)

    manager.upsert_robot_profile(
        {
            "id": "robocar-2",
            "name": "Track Car",
            "controller_url": "http://192.168.1.41",
            "camera_url": "http://192.168.1.42",
            "camera_stream_url": "http://192.168.1.42:81/stream",
        }
    )
    manager.write_video(
        "robocar-2",
        b"\xff\xd8second-robot\xff\xd9",
        160,
        120,
        {"valid": True, "filtered_mm": 1000, "seq": 2, "age_ms": 4},
    )
    manager.disable_device("robocar-2")
    manager.close()

    saved = control_pb2.ControlProject()
    saved.ParseFromString(manifest_path.read_bytes())
    second = next(device for device in saved.devices if device.device_id == "robocar-2")
    assert second.role == control_pb2.AUGMENTED_DEVICE
    assert second.control_port == 80
    assert second.stream_port == 81
    assert second.recording_file.endswith(".robocar-2.vis.pb")
    assert second.enabled is False
    assert (project_dir / second.recording_file).is_file()
