# Streamlog device gateway

`streamlog.devices.DeviceRegistry` is the shared abstraction for live producers.
Android Perceiver clients register dynamically from the `device_id` in their
frames. Robocars are configured under `devices.robots` in
`streamlog/config.yaml` and adapt the `robot-control` HTTP contract:

- controller `/api/status`, `/api/ultrasonic`, `/api/motors`, and `/api/stop`
- ESP32-CAM `/health` and the port-81 raw RGB565 `/stream`

All device video is exposed as MJPEG at `/api/devices/{id}/video`. The registry
keeps independent latest-frame channels, connection state, capabilities, and
telemetry for every device, so concurrent phones and robots do not overwrite
one another.

`streamlog.control_sessions.ControlSessionManager` activates a project
`.control.pb` through `POST /api/control-projects/open`. It lazily writes one
length-delimited `.vis.pb` per enabled device. Converted robot camera frames
carry a normalized `ultrasonic_sensor_data` sample; augmented phone frames are
preserved as received in their separate file. See the root README for project
creation, configuration, and Control-tab usage.
