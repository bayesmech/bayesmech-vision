"""Manifest-backed capture for multi-device control projects."""

from __future__ import annotations

import os
import struct
import time
from pathlib import Path
from typing import Any, BinaryIO
from urllib.parse import urlsplit, urlunsplit

from proto import control_pb2, perceiver_pb2


class ControlSessionError(ValueError):
    """Raised when a control manifest or capture target is invalid."""


class ControlSessionManager:
    """Routes device frames into the .vis.pb files declared by one project."""

    def __init__(self, recordings_dir: Path) -> None:
        self._recordings_dir = recordings_dir.resolve()
        self._manifest_path: Path | None = None
        self._project: control_pb2.ControlProject | None = None
        self._writers: dict[str, BinaryIO] = {}
        self._frame_counts: dict[str, int] = {}

    @property
    def active(self) -> bool:
        return self._project is not None

    def _path_within_recordings(self, value: str | Path) -> Path:
        resolved = Path(value).expanduser().resolve()
        try:
            resolved.relative_to(self._recordings_dir)
        except ValueError as exc:
            raise ControlSessionError(
                "Control projects must be stored under the recordings directory"
            ) from exc
        return resolved

    def open(self, manifest_path: str | Path) -> dict[str, Any]:
        resolved = self._path_within_recordings(manifest_path)
        if not resolved.name.endswith(".control.pb") or not resolved.is_file():
            raise ControlSessionError("A readable .control.pb manifest is required")
        project = control_pb2.ControlProject()
        try:
            project.ParseFromString(resolved.read_bytes())
        except Exception as exc:
            raise ControlSessionError(
                f"Could not decode control manifest: {exc}"
            ) from exc
        if not project.project_id or not project.devices:
            raise ControlSessionError("Control manifest has no project id or devices")

        self.close()
        self._manifest_path = resolved
        self._project = project
        return self.snapshot()

    def close(self) -> None:
        for writer in self._writers.values():
            writer.flush()
            os.fsync(writer.fileno())
            writer.close()
        self._writers.clear()
        self._frame_counts.clear()
        self._manifest_path = None
        self._project = None

    def snapshot(self) -> dict[str, Any]:
        project = self._project
        return {
            "active": project is not None,
            "project_id": project.project_id if project else None,
            "manifest_path": str(self._manifest_path) if self._manifest_path else None,
            "streams": [
                {
                    "device_id": device.device_id,
                    "recording_file": device.recording_file,
                    "frames_written": self._frame_counts.get(device.device_id, 0),
                    "exists": self._recording_path(device).is_file(),
                }
                for device in (project.devices if project else [])
                if device.enabled
            ],
        }

    def robot_profiles(self) -> list[dict[str, str]]:
        project = self._project
        if project is None:
            return []
        profiles = []
        for device in project.devices:
            if (
                not device.enabled
                or device.device_type != control_pb2.ROBOT_CAR_DEVICE
                or not device.control_host
                or not device.stream_host
            ):
                continue

            def http_url(host: str, port: int, path: str = "") -> str:
                netloc = host if port in {0, 80} else f"{host}:{port}"
                return urlunsplit(("http", netloc, path, "", ""))

            profiles.append(
                {
                    "id": device.device_id,
                    "name": device.display_name or device.device_id,
                    "controller_url": http_url(
                        device.control_host, int(device.control_port)
                    ),
                    "camera_url": http_url(device.stream_host, 80),
                    "camera_stream_url": http_url(
                        device.stream_host,
                        int(device.stream_port),
                        device.stream_path or "/stream",
                    ),
                }
            )
        return profiles

    def _persist_project(self) -> None:
        if self._manifest_path is None or self._project is None:
            return
        temporary_path = self._manifest_path.with_suffix(
            f"{self._manifest_path.suffix}.tmp"
        )
        temporary_path.write_bytes(self._project.SerializeToString())
        os.replace(temporary_path, self._manifest_path)

    def upsert_robot_profile(self, profile: dict[str, str]) -> None:
        project = self._project
        if project is None:
            return
        device_id = str(profile.get("id") or "").strip()
        controller = urlsplit(str(profile.get("controller_url") or ""))
        camera = urlsplit(str(profile.get("camera_url") or ""))
        stream = urlsplit(str(profile.get("camera_stream_url") or ""))
        if not device_id or not controller.hostname or not camera.hostname:
            raise ControlSessionError(
                "Robot profile is missing its id or network hosts"
            )

        device = next(
            (item for item in project.devices if item.device_id == device_id),
            None,
        )
        if device is None:
            device = project.devices.add()
            device.device_id = device_id
            has_primary = any(
                item.enabled and item.role == control_pb2.PRIMARY_DEVICE
                for item in project.devices
                if item is not device
            )
            device.role = (
                control_pb2.AUGMENTED_DEVICE
                if has_primary
                else control_pb2.PRIMARY_DEVICE
            )
            device.recording_file = f"{project.project_id}.{device_id}.vis.pb"
        device.display_name = str(profile.get("name") or device_id)
        device.device_type = control_pb2.ROBOT_CAR_DEVICE
        device.control_host = controller.hostname
        device.control_port = controller.port or 80
        device.control_path = "/api"
        device.control_transport = control_pb2.HTTP_CONTROL
        device.stream_host = stream.hostname or camera.hostname
        device.stream_port = stream.port or 81
        device.stream_path = stream.path or "/stream"
        device.stream_transport = control_pb2.RGB565_HTTP_STREAM
        device.capabilities[:] = ["video", "drive", "ultrasonic"]
        device.enabled = True
        self._persist_project()

    def disable_device(self, device_id: str) -> None:
        project = self._project
        if project is None:
            return
        device = next(
            (item for item in project.devices if item.device_id == device_id),
            None,
        )
        if device is None:
            return
        device.enabled = False
        writer = self._writers.pop(device_id, None)
        if writer is not None:
            writer.flush()
            os.fsync(writer.fileno())
            writer.close()
        self._persist_project()

    def _recording_path(self, device: control_pb2.ControlDevice) -> Path:
        if self._manifest_path is None:
            raise ControlSessionError("No control project is active")
        raw_name = str(device.recording_file or "").strip()
        if (
            not raw_name.endswith(".vis.pb")
            or Path(raw_name).is_absolute()
            or Path(raw_name).name != raw_name
        ):
            raise ControlSessionError(
                f"Invalid recording file for device {device.device_id}"
            )
        return self._manifest_path.parent / raw_name

    def _device_for(
        self,
        device_id: str,
        *,
        mobile: bool,
    ) -> control_pb2.ControlDevice | None:
        project = self._project
        if project is None:
            return None
        for device in project.devices:
            if device.enabled and device.device_id == device_id:
                return device
        if not mobile:
            return None
        return next(
            (
                device
                for device in project.devices
                if device.enabled and device.device_type == control_pb2.PHONE_DEVICE
            ),
            None,
        )

    def _write(
        self,
        device: control_pb2.ControlDevice,
        frame: perceiver_pb2.PerceiverDataFrame,
    ) -> None:
        writer = self._writers.get(device.device_id)
        if writer is None:
            recording_path = self._recording_path(device)
            recording_path.parent.mkdir(parents=True, exist_ok=True)
            writer = open(recording_path, "ab")
            self._writers[device.device_id] = writer
        raw = frame.SerializeToString()
        writer.write(struct.pack(">I", len(raw)))
        writer.write(raw)
        frame_count = self._frame_counts.get(device.device_id, 0) + 1
        self._frame_counts[device.device_id] = frame_count
        if frame_count % 30 == 0:
            writer.flush()

    def write_perceiver(
        self,
        frame: perceiver_pb2.PerceiverDataFrame,
        device_id: str,
    ) -> None:
        device = self._device_for(device_id, mobile=True)
        if device is not None:
            self._write(device, frame)

    def write_video(
        self,
        device_id: str,
        jpeg: bytes,
        width: int,
        height: int,
        ultrasonic: dict[str, Any] | None,
    ) -> None:
        device = self._device_for(device_id, mobile=False)
        if device is None:
            return
        frame_number = self._frame_counts.get(device.device_id, 0) + 1
        frame = perceiver_pb2.PerceiverDataFrame()
        frame.frame_identifier.timestamp_ns = time.time_ns()
        frame.frame_identifier.frame_number = frame_number
        frame.frame_identifier.device_id = device_id
        frame.rgb_frame.data = jpeg
        frame.rgb_frame.format = perceiver_pb2.ImageFrame.JPEG
        frame.rgb_frame.width = width
        frame.rgb_frame.height = height
        frame.rgb_frame.quality = 80

        sample = ultrasonic or {}
        valid = bool(sample.get("valid")) and int(sample.get("age_ms") or 0) < 500
        distance_mm = int(sample.get("filtered_mm") or 0) if valid else 0
        max_range_meters = 4.0
        sensor = frame.ultrasonic_sensor_data
        sensor.valid = valid
        sensor.distance_meters = max(0.0, distance_mm / 1000.0)
        sensor.max_range_meters = max_range_meters
        sensor.normalized_distance = max(
            0.0, min(1.0, sensor.distance_meters / max_range_meters)
        )
        sensor.sequence = max(0, int(sample.get("seq") or 0))
        sensor.age_ms = max(0, int(sample.get("age_ms") or 0))
        self._write(device, frame)
