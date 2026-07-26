"""Multi-device registry and LAN adapters for live control surfaces.

Phones publish JPEG frames through the existing ``/ar-stream`` websocket.
Robocars expose the HTTP API and raw RGB565 stream implemented by the sister
``robot-control`` repository.  This module normalizes both into one device
catalog and one MJPEG video endpoint for browser and Electron clients.
"""

from __future__ import annotations

import asyncio
import io
import logging
import re
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Callable
from urllib.parse import urlparse, urlunparse

import aiohttp
import numpy as np
from PIL import Image

logger = logging.getLogger(__name__)

MJPEG_BOUNDARY = "frame"
_DEVICE_ID_RE = re.compile(r"[^a-zA-Z0-9._-]+")
_MOBILE_STALE_SECONDS = 5.0
_ROBOT_STALE_SECONDS = 4.0


class DeviceError(RuntimeError):
    """Base error for device registry operations."""


class DeviceNotFoundError(DeviceError):
    """Raised when a requested device is not registered."""


class DeviceValidationError(DeviceError):
    """Raised for an invalid device profile or command."""


class DeviceConnectionError(DeviceError):
    """Raised when a registered LAN device cannot be reached."""


@dataclass(slots=True)
class VideoFrame:
    jpeg: bytes
    width: int
    height: int
    sequence: int
    captured_at: float


@dataclass(slots=True)
class Device:
    id: str
    name: str
    kind: str
    capabilities: set[str]
    controller_url: str | None = None
    camera_url: str | None = None
    camera_stream_url: str | None = None
    connections: set[str] = field(default_factory=set)
    last_seen: float = 0.0
    frame_count: int = 0
    frame_times: deque[float] = field(default_factory=lambda: deque(maxlen=90))
    status: dict[str, Any] | None = None
    ultrasonic: dict[str, Any] | None = None
    camera_health: dict[str, Any] | None = None
    error: str | None = None


def normalize_device_id(value: str) -> str:
    normalized = _DEVICE_ID_RE.sub("-", str(value).strip()).strip("-._").lower()
    if not normalized:
        raise DeviceValidationError("Device id must contain a letter or number")
    if len(normalized) > 80:
        raise DeviceValidationError("Device id must be 80 characters or fewer")
    return normalized


def normalize_http_url(value: str, field_name: str) -> str:
    raw = str(value or "").strip().rstrip("/")
    try:
        parsed = urlparse(raw)
    except ValueError as exc:
        raise DeviceValidationError(f"{field_name} is not a valid URL") from exc
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise DeviceValidationError(f"{field_name} must be an http(s) URL")
    if parsed.username or parsed.password:
        raise DeviceValidationError(f"{field_name} must not contain credentials")
    if parsed.query or parsed.fragment:
        raise DeviceValidationError(
            f"{field_name} must not contain a query or fragment"
        )
    return raw


def default_camera_stream_url(camera_url: str) -> str:
    """Map the sister firmware's port-80 camera URL to its port-81 stream."""
    parsed = urlparse(camera_url)
    host = parsed.hostname or ""
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    netloc = f"{host}:81"
    return urlunparse((parsed.scheme, netloc, "/stream", "", "", ""))


def rgb565_be_to_jpeg(
    payload: bytes,
    width: int,
    height: int,
    *,
    quality: int = 80,
) -> bytes:
    """Convert one big-endian RGB565 frame from the ESP32-CAM to JPEG."""
    expected = width * height * 2
    if width <= 0 or height <= 0 or expected > 640 * 480 * 2:
        raise DeviceValidationError("Invalid RGB565 frame dimensions")
    if len(payload) != expected:
        raise DeviceValidationError(
            f"RGB565 frame has {len(payload)} bytes; expected {expected}"
        )

    pixels = np.frombuffer(payload, dtype=">u2").reshape((height, width))
    rgb = np.empty((height, width, 3), dtype=np.uint8)
    rgb[..., 0] = ((pixels >> 11) & 0x1F) * 255 // 31
    rgb[..., 1] = ((pixels >> 5) & 0x3F) * 255 // 63
    rgb[..., 2] = (pixels & 0x1F) * 255 // 31

    output = io.BytesIO()
    Image.fromarray(rgb, mode="RGB").save(
        output,
        format="JPEG",
        quality=max(30, min(95, int(quality))),
        subsampling=0,
        optimize=False,
    )
    return output.getvalue()


def _multipart_frame(jpeg: bytes) -> bytes:
    return (
        (
            f"--{MJPEG_BOUNDARY}\r\n"
            "Content-Type: image/jpeg\r\n"
            f"Content-Length: {len(jpeg)}\r\n\r\n"
        ).encode("ascii")
        + jpeg
        + b"\r\n"
    )


def _perceiver_jpeg(frame: Any) -> tuple[bytes, int, int] | None:
    image = frame.rgb_frame
    data = bytes(image.data)
    width = int(image.width)
    height = int(image.height)
    if not data or width <= 0 or height <= 0:
        return None

    # ImageFrame.ImageFormat values from perceiver.proto.
    if int(image.format) == 4:  # JPEG
        return data, width, height

    modes = {
        1: ("RGB", width * height * 3),
        2: ("RGBA", width * height * 4),
        5: ("L", width * height),
    }
    mode_and_size = modes.get(int(image.format))
    if mode_and_size is None or len(data) != mode_and_size[1]:
        return None
    image_object = Image.frombytes(mode_and_size[0], (width, height), data)
    if image_object.mode != "RGB":
        image_object = image_object.convert("RGB")
    output = io.BytesIO()
    image_object.save(output, format="JPEG", quality=82)
    return output.getvalue(), width, height


class DeviceRegistry:
    """Tracks live producers and adapts robot HTTP endpoints into one API."""

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        self._config = config or {}
        self._devices: dict[str, Device] = {}
        self._latest_video: dict[str, VideoFrame] = {}
        self._video_subscribers: dict[str, set[asyncio.Queue[VideoFrame]]] = {}
        self._probe_tasks: dict[str, asyncio.Task] = {}
        self._session: aiohttp.ClientSession | None = None
        self._running = False
        self._frame_callback: (
            Callable[[str, bytes, int, int, dict[str, Any] | None], None] | None
        ) = None

        for profile in self._config.get("robots", []) or []:
            try:
                self.register_robot(profile)
            except DeviceValidationError as exc:
                logger.error("Ignoring invalid robot profile %r: %s", profile, exc)

    @property
    def device_count(self) -> int:
        return len(self._devices)

    def set_frame_callback(
        self,
        callback: Callable[[str, bytes, int, int, dict[str, Any] | None], None] | None,
    ) -> None:
        self._frame_callback = callback

    async def start(self) -> None:
        if self._running:
            return
        timeout = aiohttp.ClientTimeout(
            total=float(self._config.get("request_timeout_seconds", 2.0)),
            connect=float(self._config.get("connect_timeout_seconds", 1.0)),
        )
        self._session = aiohttp.ClientSession(timeout=timeout)
        self._running = True
        for device in self._devices.values():
            if device.kind == "robot":
                self._start_probe(device.id)

    async def close(self) -> None:
        self._running = False
        tasks = list(self._probe_tasks.values())
        self._probe_tasks.clear()
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        if self._session is not None:
            await self._session.close()
            self._session = None

    def register_robot(self, profile: dict[str, Any]) -> dict[str, Any]:
        device_id = normalize_device_id(profile.get("id") or profile.get("name") or "")
        controller_url = normalize_http_url(
            profile.get("controller_url") or profile.get("controllerUrl") or "",
            "controller_url",
        )
        camera_url = normalize_http_url(
            profile.get("camera_url") or profile.get("cameraUrl") or "",
            "camera_url",
        )
        raw_stream_url = (
            profile.get("camera_stream_url")
            or profile.get("cameraStreamUrl")
            or default_camera_stream_url(camera_url)
        )
        camera_stream_url = normalize_http_url(raw_stream_url, "camera_stream_url")
        name = str(profile.get("name") or device_id).strip()[:100] or device_id

        current = self._devices.get(device_id)
        if current is not None and current.kind != "robot":
            raise DeviceValidationError(
                f"Device id '{device_id}' is already used by a {current.kind}"
            )
        device = current or Device(
            id=device_id,
            name=name,
            kind="robot",
            capabilities={"video", "drive", "ultrasonic"},
        )
        device.name = name
        device.controller_url = controller_url
        device.camera_url = camera_url
        device.camera_stream_url = camera_stream_url
        device.error = None
        self._devices[device_id] = device
        if self._running:
            self._start_probe(device_id)
        return self.snapshot(device_id)

    async def remove_robot(self, device_id: str) -> None:
        device = self.get(device_id)
        if device.kind != "robot":
            raise DeviceValidationError("Only configured robot devices can be removed")
        try:
            await self.stop(device.id)
        except DeviceError:
            pass
        task = self._probe_tasks.pop(device.id, None)
        if task is not None:
            task.cancel()
        self._devices.pop(device.id, None)
        self._latest_video.pop(device.id, None)
        self._video_subscribers.pop(device.id, None)

    def get(self, device_id: str) -> Device:
        normalized = normalize_device_id(device_id)
        device = self._devices.get(normalized)
        if device is None:
            raise DeviceNotFoundError(f"Unknown device '{normalized}'")
        return device

    def snapshots(self) -> list[dict[str, Any]]:
        return [
            self.snapshot(device.id)
            for device in sorted(
                self._devices.values(),
                key=lambda item: (item.kind != "robot", item.name.casefold(), item.id),
            )
        ]

    def snapshot(self, device_id: str) -> dict[str, Any]:
        device = self.get(device_id)
        now = time.monotonic()
        stale_after = (
            _MOBILE_STALE_SECONDS if device.kind == "mobile" else _ROBOT_STALE_SECONDS
        )
        connected = (
            bool(device.connections)
            if device.kind == "mobile"
            else (device.last_seen > 0 and now - device.last_seen <= stale_after)
        )
        frame_fps = 0.0
        if len(device.frame_times) >= 2:
            elapsed = device.frame_times[-1] - device.frame_times[0]
            if elapsed > 0:
                frame_fps = (len(device.frame_times) - 1) / elapsed
        latest = self._latest_video.get(device.id)
        return {
            "id": device.id,
            "name": device.name,
            "kind": device.kind,
            "connected": connected,
            "last_seen_ms": (
                max(0, round((now - device.last_seen) * 1000))
                if device.last_seen
                else None
            ),
            "capabilities": {
                "video": "video" in device.capabilities,
                "drive": "drive" in device.capabilities,
                "ultrasonic": "ultrasonic" in device.capabilities,
            },
            "video": {
                "path": f"/api/devices/{device.id}/video",
                "width": (
                    latest.width
                    if latest
                    else (int((device.camera_health or {}).get("frame_width") or 0))
                ),
                "height": (
                    latest.height
                    if latest
                    else (int((device.camera_health or {}).get("frame_height") or 0))
                ),
                "frame_count": device.frame_count,
                "fps": round(frame_fps, 1),
            },
            "status": device.status,
            "ultrasonic": device.ultrasonic,
            "camera": device.camera_health,
            "error": device.error,
        }

    def publish_perceiver(self, frame: Any, connection_id: str) -> str:
        raw_id = str(frame.frame_identifier.device_id or "").strip()
        device_id = normalize_device_id(raw_id or f"mobile-{connection_id[:8]}")
        device = self._devices.get(device_id)
        if device is None:
            device = Device(
                id=device_id,
                name=raw_id or f"Mobile {connection_id[:8]}",
                kind="mobile",
                capabilities={"video", "imu", "depth", "gps"},
            )
            self._devices[device_id] = device
        elif device.kind != "mobile":
            device_id = normalize_device_id(f"mobile-{device_id}")
            device = self._devices.setdefault(
                device_id,
                Device(
                    id=device_id,
                    name=f"Mobile {raw_id or connection_id[:8]}",
                    kind="mobile",
                    capabilities={"video", "imu", "depth", "gps"},
                ),
            )

        device.connections.add(connection_id)
        device.last_seen = time.monotonic()
        device.error = None
        converted = _perceiver_jpeg(frame)
        if converted is not None:
            jpeg, width, height = converted
            self._publish_video(device, jpeg, width, height)
        return device.id

    def disconnect_mobile(self, device_id: str | None, connection_id: str) -> None:
        if not device_id:
            return
        device = self._devices.get(device_id)
        if device is None or device.kind != "mobile":
            return
        device.connections.discard(connection_id)
        if not device.connections:
            device.error = "Stream disconnected"

    def _publish_video(
        self,
        device: Device,
        jpeg: bytes,
        width: int,
        height: int,
    ) -> None:
        now = time.monotonic()
        device.last_seen = now
        device.frame_count += 1
        device.frame_times.append(now)
        frame = VideoFrame(
            jpeg=jpeg,
            width=width,
            height=height,
            sequence=device.frame_count,
            captured_at=now,
        )
        self._latest_video[device.id] = frame
        if device.kind == "robot" and self._frame_callback is not None:
            try:
                self._frame_callback(
                    device.id,
                    jpeg,
                    width,
                    height,
                    device.ultrasonic,
                )
            except Exception:
                logger.exception("Could not persist frame from %s", device.id)
        for queue in list(self._video_subscribers.get(device.id, set())):
            if queue.full():
                try:
                    queue.get_nowait()
                except asyncio.QueueEmpty:
                    pass
            try:
                queue.put_nowait(frame)
            except asyncio.QueueFull:
                pass

    def _start_probe(self, device_id: str) -> None:
        previous = self._probe_tasks.pop(device_id, None)
        if previous is not None:
            previous.cancel()
        self._probe_tasks[device_id] = asyncio.create_task(
            self._probe_robot(device_id),
            name=f"robot-probe:{device_id}",
        )

    async def _get_json(self, url: str) -> dict[str, Any]:
        if self._session is None:
            raise DeviceConnectionError("Device registry is not running")
        try:
            async with self._session.get(
                url,
                headers={"Accept": "application/json", "Cache-Control": "no-store"},
            ) as response:
                if response.status < 200 or response.status >= 300:
                    detail = (await response.text())[:240]
                    raise DeviceConnectionError(
                        f"{url} returned HTTP {response.status}: {detail}"
                    )
                payload = await response.json(content_type=None)
        except (aiohttp.ClientError, asyncio.TimeoutError, OSError) as exc:
            raise DeviceConnectionError(f"Cannot reach {url}: {exc}") from exc
        if not isinstance(payload, dict):
            raise DeviceConnectionError(f"{url} did not return a JSON object")
        return payload

    async def _probe_robot(self, device_id: str) -> None:
        ultrasonic_interval = max(
            0.08, float(self._config.get("ultrasonic_poll_seconds", 0.12))
        )
        status_interval = max(0.5, float(self._config.get("status_poll_seconds", 1.0)))
        camera_interval = max(1.0, float(self._config.get("camera_poll_seconds", 3.0)))
        next_status = 0.0
        next_camera = 0.0

        while self._running and device_id in self._devices:
            device = self._devices[device_id]
            now = time.monotonic()
            requests: list[tuple[str, asyncio.Task]] = []
            if device.controller_url:
                requests.append(
                    (
                        "ultrasonic",
                        asyncio.create_task(
                            self._get_json(f"{device.controller_url}/api/ultrasonic")
                        ),
                    )
                )
                if now >= next_status:
                    next_status = now + status_interval
                    requests.append(
                        (
                            "status",
                            asyncio.create_task(
                                self._get_json(f"{device.controller_url}/api/status")
                            ),
                        )
                    )
            if device.camera_url and now >= next_camera:
                next_camera = now + camera_interval
                requests.append(
                    (
                        "camera_health",
                        asyncio.create_task(
                            self._get_json(f"{device.camera_url}/health")
                        ),
                    )
                )

            success = False
            failures: list[str] = []
            tasks = [task for _, task in requests]
            try:
                results = await asyncio.gather(*tasks, return_exceptions=True)
            except asyncio.CancelledError:
                for task in tasks:
                    task.cancel()
                if tasks:
                    await asyncio.gather(*tasks, return_exceptions=True)
                raise
            for (key, _task), result in zip(requests, results, strict=True):
                if isinstance(result, BaseException):
                    if isinstance(result, asyncio.CancelledError):
                        raise result
                    failures.append(str(result))
                else:
                    setattr(device, key, result)
                    success = True
            if success:
                device.last_seen = time.monotonic()
                device.error = None
            elif failures:
                device.error = failures[0]
            await asyncio.sleep(ultrasonic_interval)

    async def _post_robot(
        self,
        device: Device,
        path: str,
        data: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        if device.kind != "robot" or not device.controller_url:
            raise DeviceValidationError("Device does not support robot commands")
        if self._session is None:
            raise DeviceConnectionError("Device registry is not running")
        url = f"{device.controller_url}{path}"
        try:
            async with self._session.post(
                url,
                data=data,
                headers={"Accept": "application/json", "Cache-Control": "no-store"},
            ) as response:
                payload_text = await response.text()
                if response.status < 200 or response.status >= 300:
                    raise DeviceConnectionError(
                        f"{url} returned HTTP {response.status}: {payload_text[:240]}"
                    )
                payload = await response.json(content_type=None)
        except DeviceConnectionError:
            raise
        except (aiohttp.ClientError, asyncio.TimeoutError, OSError) as exc:
            raise DeviceConnectionError(f"Cannot reach {url}: {exc}") from exc
        if not isinstance(payload, dict):
            raise DeviceConnectionError(f"{url} did not return a JSON object")
        device.status = payload
        device.last_seen = time.monotonic()
        device.error = None
        return payload

    async def set_motors(
        self,
        device_id: str,
        speeds: dict[str, Any],
    ) -> dict[str, Any]:
        device = self.get(device_id)
        normalized: dict[str, str] = {}
        for wheel in ("lf", "rf", "lb", "rb"):
            value = speeds.get(wheel)
            if isinstance(value, bool):
                raise DeviceValidationError(
                    "All four motor speeds must be integers from -255 to 255"
                )
            try:
                number = int(value)
            except (TypeError, ValueError) as exc:
                raise DeviceValidationError(
                    "All four motor speeds must be integers from -255 to 255"
                ) from exc
            if (
                number < -255
                or number > 255
                or str(value).strip()
                not in {
                    str(number),
                    f"+{number}" if number >= 0 else str(number),
                }
            ):
                raise DeviceValidationError(
                    "All four motor speeds must be integers from -255 to 255"
                )
            normalized[wheel] = str(number)
        return await self._post_robot(device, "/api/motors", normalized)

    async def stop(self, device_id: str) -> dict[str, Any]:
        device = self.get(device_id)
        return await self._post_robot(device, "/api/stop")

    async def stream_mjpeg(self, device_id: str) -> AsyncIterator[bytes]:
        device = self.get(device_id)
        if "video" not in device.capabilities:
            raise DeviceValidationError("Device does not expose video")
        if device.kind == "robot":
            async for chunk in self._stream_robot_mjpeg(device):
                yield chunk
            return

        queue: asyncio.Queue[VideoFrame] = asyncio.Queue(maxsize=1)
        subscribers = self._video_subscribers.setdefault(device.id, set())
        subscribers.add(queue)
        latest = self._latest_video.get(device.id)
        if latest is not None:
            queue.put_nowait(latest)
        try:
            while device.id in self._devices:
                frame = await asyncio.wait_for(queue.get(), timeout=20.0)
                yield _multipart_frame(frame.jpeg)
        except asyncio.TimeoutError:
            return
        finally:
            subscribers.discard(queue)
            if not subscribers:
                self._video_subscribers.pop(device.id, None)

    async def _stream_robot_mjpeg(self, device: Device) -> AsyncIterator[bytes]:
        if self._session is None or not device.camera_stream_url:
            raise DeviceConnectionError("Robot camera is not configured")

        while self._running and device.id in self._devices:
            try:
                async with self._session.get(
                    device.camera_stream_url,
                    headers={"Accept": "application/octet-stream"},
                    timeout=aiohttp.ClientTimeout(total=None, connect=2, sock_read=10),
                ) as response:
                    if response.status < 200 or response.status >= 300:
                        raise DeviceConnectionError(
                            f"Camera stream returned HTTP {response.status}"
                        )
                    width = int(
                        response.headers.get("X-Frame-Width")
                        or (device.camera_health or {}).get("frame_width")
                        or 160
                    )
                    height = int(
                        response.headers.get("X-Frame-Height")
                        or (device.camera_health or {}).get("frame_height")
                        or 120
                    )
                    frame_size = width * height * 2
                    if frame_size <= 0 or frame_size > 640 * 480 * 2:
                        raise DeviceConnectionError(
                            "Camera reported invalid frame dimensions"
                        )
                    buffer = bytearray()
                    async for packet in response.content.iter_chunked(16 * 1024):
                        buffer.extend(packet)
                        while len(buffer) >= frame_size:
                            raw = bytes(buffer[:frame_size])
                            del buffer[:frame_size]
                            jpeg = await asyncio.to_thread(
                                rgb565_be_to_jpeg,
                                raw,
                                width,
                                height,
                            )
                            self._publish_video(device, jpeg, width, height)
                            yield _multipart_frame(jpeg)
            except asyncio.CancelledError:
                raise
            except (
                DeviceError,
                aiohttp.ClientError,
                asyncio.TimeoutError,
                OSError,
            ) as exc:
                device.error = str(exc)
                logger.debug("Robot camera %s disconnected: %s", device.id, exc)
                await asyncio.sleep(1.0)
