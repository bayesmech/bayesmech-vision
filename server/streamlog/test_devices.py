import asyncio

import pytest
from aiohttp import web
from PIL import Image

from proto import perceiver_pb2
from streamlog.devices import (
    DeviceRegistry,
    DeviceValidationError,
    default_camera_stream_url,
    normalize_device_id,
    rgb565_be_to_jpeg,
)


def test_default_camera_stream_url_uses_sister_firmware_port():
    assert (
        default_camera_stream_url("http://192.168.4.2")
        == "http://192.168.4.2:81/stream"
    )
    assert (
        default_camera_stream_url("http://robot-camera.local")
        == "http://robot-camera.local:81/stream"
    )


def test_rgb565_big_endian_conversion_preserves_primary_colours():
    # Three broad red, green, and blue bands in RGB565 big-endian.
    row = (
        bytes.fromhex("f800") * 8
        + bytes.fromhex("07e0") * 8
        + bytes.fromhex("001f") * 8
    )
    jpeg = rgb565_be_to_jpeg(row * 8, 24, 8, quality=95)
    decoded = Image.open(__import__("io").BytesIO(jpeg)).convert("RGB")
    red = decoded.getpixel((3, 3))
    green = decoded.getpixel((11, 3))
    blue = decoded.getpixel((20, 3))
    assert red[0] > red[1] and red[0] > red[2]
    assert green[1] > green[0] and green[1] > green[2]
    assert blue[2] > blue[0] and blue[2] > blue[1]


def test_robot_profiles_are_normalized_and_multiple_devices_are_supported():
    registry = DeviceRegistry(
        {
            "robots": [
                {
                    "id": "Robot One",
                    "name": "Workshop",
                    "controller_url": "http://192.168.1.31/",
                    "camera_url": "http://192.168.1.32/",
                },
                {
                    "id": "robot-two",
                    "name": "Track",
                    "controller_url": "http://192.168.1.41",
                    "camera_url": "http://192.168.1.42",
                },
            ]
        }
    )
    snapshots = registry.snapshots()
    assert [device["id"] for device in snapshots] == ["robot-two", "robot-one"]
    assert all(device["capabilities"]["drive"] for device in snapshots)


def test_motor_commands_require_all_four_bounded_integer_values():
    registry = DeviceRegistry(
        {
            "robots": [
                {
                    "id": "robot",
                    "controller_url": "http://192.168.1.31",
                    "camera_url": "http://192.168.1.32",
                }
            ]
        }
    )

    with pytest.raises(DeviceValidationError):
        asyncio.run(registry.set_motors("robot", {"lf": 10, "rf": 10}))
    with pytest.raises(DeviceValidationError):
        asyncio.run(
            registry.set_motors(
                "robot",
                {"lf": 256, "rf": 0, "lb": 0, "rb": 0},
            )
        )


def test_device_id_rejects_empty_values():
    assert normalize_device_id("Robot #1") == "robot-1"
    with pytest.raises(DeviceValidationError):
        normalize_device_id("---")


def test_robot_adapter_probes_controls_and_converts_the_raw_stream():
    async def scenario():
        received_motors = {}

        async def status(_request):
            return web.json_response(
                {
                    "lf": 0,
                    "rf": 0,
                    "lb": 0,
                    "rb": 0,
                    "moving": False,
                    "watchdog": False,
                    "clients": 1,
                    "uptime_ms": 1000,
                }
            )

        async def ultrasonic(_request):
            return web.json_response(
                {
                    "seq": 7,
                    "valid": True,
                    "distance_mm": 420,
                    "filtered_mm": 400,
                    "duration_us": 2400,
                    "age_ms": 5,
                    "interval_ms": 60,
                    "valid_samples": 7,
                    "invalid_samples": 0,
                    "timeouts": 0,
                    "trig_pin": 33,
                    "echo_pin": 32,
                }
            )

        async def motors(request):
            received_motors.update(await request.post())
            payload = {
                key: int(received_motors[key]) for key in ("lf", "rf", "lb", "rb")
            }
            return web.json_response(
                {
                    **payload,
                    "moving": any(payload.values()),
                    "watchdog": True,
                    "clients": 1,
                    "uptime_ms": 1200,
                }
            )

        async def stop(_request):
            return await status(_request)

        async def health(_request):
            return web.json_response(
                {
                    "ok": True,
                    "camera": True,
                    "stream_format": "RGB565",
                    "frame_width": 2,
                    "frame_height": 1,
                    "rssi": -48,
                }
            )

        async def stream(_request):
            response = web.StreamResponse(
                headers={
                    "Content-Type": "application/octet-stream",
                    "X-Frame-Width": "2",
                    "X-Frame-Height": "1",
                    "X-Frame-Format": "RGB565-BE",
                }
            )
            await response.prepare(_request)
            await response.write(bytes.fromhex("f800001f"))
            await asyncio.sleep(0.05)
            return response

        app = web.Application()
        app.router.add_get("/api/status", status)
        app.router.add_get("/api/ultrasonic", ultrasonic)
        app.router.add_post("/api/motors", motors)
        app.router.add_post("/api/stop", stop)
        app.router.add_get("/health", health)
        app.router.add_get("/stream", stream)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        port = site._server.sockets[0].getsockname()[1]
        base_url = f"http://127.0.0.1:{port}"

        registry = DeviceRegistry(
            {
                "robots": [
                    {
                        "id": "mock-robot",
                        "controller_url": base_url,
                        "camera_url": base_url,
                        "camera_stream_url": f"{base_url}/stream",
                    }
                ],
                "ultrasonic_poll_seconds": 0.08,
                "status_poll_seconds": 0.5,
                "camera_poll_seconds": 1,
            }
        )
        try:
            await registry.start()
            await asyncio.sleep(0.12)
            snapshot = registry.snapshot("mock-robot")
            assert snapshot["connected"] is True
            assert snapshot["ultrasonic"]["filtered_mm"] == 400
            assert snapshot["camera"]["rssi"] == -48

            command = await registry.set_motors(
                "mock-robot",
                {"lf": 90, "rf": 80, "lb": 70, "rb": 60},
            )
            assert command["moving"] is True
            assert dict(received_motors) == {
                "lf": "90",
                "rf": "80",
                "lb": "70",
                "rb": "60",
            }

            stream_iterator = registry.stream_mjpeg("mock-robot")
            first_chunk = await asyncio.wait_for(anext(stream_iterator), timeout=2)
            assert first_chunk.startswith(b"--frame\r\nContent-Type: image/jpeg")
            assert b"\xff\xd8" in first_chunk
            await stream_iterator.aclose()
        finally:
            await registry.close()
            await runner.cleanup()

    asyncio.run(scenario())


def test_multiple_mobile_streams_keep_independent_video_channels():
    registry = DeviceRegistry()
    image_buffer = __import__("io").BytesIO()
    Image.new("RGB", (2, 2), (20, 80, 160)).save(image_buffer, format="JPEG")

    def frame(device_id: str, frame_number: int):
        message = perceiver_pb2.PerceiverDataFrame()
        message.frame_identifier.device_id = device_id
        message.frame_identifier.frame_number = frame_number
        message.rgb_frame.data = image_buffer.getvalue()
        message.rgb_frame.format = perceiver_pb2.ImageFrame.JPEG
        message.rgb_frame.width = 2
        message.rgb_frame.height = 2
        return message

    first_id = registry.publish_perceiver(frame("phone-a", 1), "connection-a")
    second_id = registry.publish_perceiver(frame("phone-b", 1), "connection-b")
    assert first_id == "phone-a"
    assert second_id == "phone-b"
    assert {item["id"] for item in registry.snapshots()} == {"phone-a", "phone-b"}

    async def read_both():
        first_stream = registry.stream_mjpeg(first_id)
        second_stream = registry.stream_mjpeg(second_id)
        try:
            first, second = await asyncio.gather(
                anext(first_stream), anext(second_stream)
            )
            assert first.startswith(b"--frame")
            assert second.startswith(b"--frame")
        finally:
            await first_stream.aclose()
            await second_stream.aclose()

    asyncio.run(read_both())
