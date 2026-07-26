# BayesMech Vision

## New machine setup

1. Clone the repo.

2. Copy `.env.example` to `.env` and fill in the required keys. If another
   worktree on the same machine already has a usable `.env`, symlink to that
   file instead.

3. Create or clone the `recordings/` directory. The full recordings bucket can
   be synced from S3:

   ```sh
   aws s3 sync s3://bayesmech-recordings/recordings/ ./recordings/
   ```

   If another local worktree already has `recordings/`, symlink to that
   directory instead.

4. Download model weights from the `bayesmech-models` S3 bucket into
   `server/segmentation/models`, or symlink that directory from another local
   worktree.

5. Install the Python environment:

   ```sh
   cd server
   uv sync
   ```

6. Install the dashboard dependencies:

   ```sh
   cd analysis/dashboard
   npm install
   ```

## Running on EC2

Run the dashboard server:

```sh
cd analysis/dashboard
npm run devserver
```

The Electron app starts the local Streamlog service automatically when it is
available in this checkout. It can also be run separately for server-only
development:

```sh
cd server
uv run streamlog/main.py
```

## Live device control

The desktop UX creates control workspaces from the robot button beside the
**Project** heading. Choose **Robot Car**, **Robot Hand**, or **Drone Control**.
The app creates `recordings/YYYYMMDD_HHMMSS_<preset>/`, opens it as the current
project with a fresh chat, and shows its project-scoped **Control** tab. Ordinary
recording projects do not get a Control tab.

Each directory contains a protobuf `.control.pb` manifest. It records the
project preset plus every primary or augmented device's role, host, control
port/path/transport, incoming stream port/path/transport, capabilities, and
relative `.vis.pb` recording filename. Streamlog is the device gateway: phones
continue to publish protobuf frames to
`ws://<streamlog-host>:8080/ar-stream`, while Streamlog talks to each robot's
controller and camera over the local network.

Start the desktop UX; it starts and health-checks local Streamlog automatically:

```sh
cd ux
npm start
```

Set `VITE_STREAMLOG_ENDPOINT` or `STREAMLOG_ENDPOINT` to use an already-running
remote gateway instead.

The sister `robot-control` firmware defaults are preconfigured as:

| Service | Default |
| --- | --- |
| Motor controller | `http://192.168.4.1` |
| Camera health/capture | `http://192.168.4.2` |
| Raw camera stream | `http://192.168.4.2:81/stream` |

When the robot is on a shared LAN, put persistent addresses in
`server/streamlog/config.yaml`, set the
`ROBOCAR_*` values from `.env.example`, or use **Add robot**. Multiple persistent
profiles can be declared under `devices.robots`; `ROBOCAR_DEVICES` can override
them with a JSON array. Opening a Robot Car control project registers the
network coordinates saved in its manifest.

The Control tab keeps every live device's video stream open concurrently.
Selecting a robot adds ultrasonic telemetry, hold-to-drive WASD controls,
independent wheel sliders, and an emergency stop. Nonzero commands are
refreshed every 350 ms to satisfy the firmware watchdog. Releasing controls,
hiding/changing the tab, or losing window focus sends a stop, while the
robot's one-second watchdog remains the final safety backstop.

Robot frames are saved to the primary device's `.vis.pb`; a simultaneous phone
stream is saved to a different `.phone.vis.pb` in the same project. The scanner
groups both files under one project entry and adds **Video Car** and
**Video Phone** tabs as those streams appear. Robot frames include
`ultrasonic_sensor_data`, with `normalized_distance` clamped to `0..1` against
the sensor's four-metre range, as well as the raw metre value and validity
metadata.

The normalized device API is:

| Method and path | Purpose |
| --- | --- |
| `GET /api/devices` | List configured robots and dynamically connected phones |
| `POST /api/devices/robots` | Add or update a robot profile |
| `GET /api/devices/{id}/video` | MJPEG stream for either a phone or robot |
| `POST /api/devices/{id}/motors` | Set `lf`, `rf`, `lb`, and `rb` together |
| `POST /api/devices/{id}/stop` | Immediately stop all four motors |
| `POST /api/control-projects/open` | Activate a `.control.pb` capture manifest |
| `GET /api/control-projects/active` | Inspect the active project and stream files |
| `POST /api/control-projects/close` | Flush and close the active project capture |

Robot video is converted from the sister firmware's 160×120 RGB565 stream to
MJPEG in Streamlog. Phone video stays isolated by `device_id`; connecting a
second phone no longer clears or replaces the first device session.

## Remote runner

Server-side analysis can run on the UX machine or on a network-accessible GPU
machine through the runner:

```sh
cd server
uv run python -m runner
```

This listens on `127.0.0.1:8787` by default. For a remote host, configure
`RUNNER_HOST=0.0.0.0`, `RUNNER_TOKEN`, and `RUNNER_ENDPOINT` as described in
[`server/runner/README.md`](server/runner/README.md). Public runners must use an
authentication token and should use TLS.

## Analyzer commands

When running any Python-based analyzer, always run it from `server/` because the
`uv` environment configuration is there.

## Recording data

Get the `.vis.pb` data files needed for analysis:

```sh
aws s3 sync s3://bayesmech-recordings/recordings/ ./recordings/ --exclude "*" --include "*.vis.pb"
```
