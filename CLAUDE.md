# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

BayesMech Vision is an AR data capture and analysis system. An Android app streams camera/sensor data over WebSockets to a Python FastAPI server, which serves a React dashboard for live monitoring and playback of recordings. A separate offline pipeline runs SAM2 video segmentation on recordings.

## Commands

### Server (Python / uv)

```bash
# Run the vision server (from project root)
uv run python server/streamlog/main.py

# Install dependencies
cd server && uv sync
```

### Tests & Linting (Python)

```bash
# Run all tests (from server/ directory)
cd server && uv run pytest

# Run a single test file or test function
cd server && uv run pytest analysis/snooker/tests/test_editor.py
cd server && uv run pytest analysis/snooker/tests/test_editor.py::test_function_name

# Format code
cd server && uv run black .
```

### Dashboard (Node / npm)

```bash
cd analysis/dashboard
npm install              # install deps
npm run dev              # dev server with HMR (proxies /api and /ws to localhost:8080)
npm run devserver        # same but accessible on 0.0.0.0:80
npm run build            # production build → analysis/dashboard/dist/ (served by FastAPI)
npm run lint             # ESLint
```

### Protobuf Regeneration

```bash
# Python: regenerate *_pb2.py files after editing .proto files
cd proto && bash generate_proto.sh

# TypeScript: regenerate analysis/dashboard/src/proto/bundle.js (or use npm run proto)
cd analysis/dashboard && npm run proto
```

### Analysis Tools (Python)

```bash
# Segmentation (offline batch, from server/ directory)
cd server
uv run python segmentation/main.py ../recordings/<name>.vis.pb
uv run python segmentation/main.py ../recordings/<name>.vis.pb --max-frames 200  # first 200 frames

# Ball tracking / pongtown (from server/ directory)
cd server
uv run python pongtown/main.py ../recordings/<name>.vis.pb --mode snooker
uv run python pongtown/main.py ../recordings/<name>.vis.pb --mode pingpong --stop-after 2

# Snooker editor — interactive top-view editor for .pongtown.pb files
cd server
uv run python ../analysis/snooker/main.py ../recordings/<name>/<name>.pongtown.pb

# Motion heatmap (offline batch)
cd server
uv run python motioncap/main.py ../recordings/<name>.vis.pb --debug-render-video

# Interactive homography analysis — two frames side-by-side, hover for point correspondence
cd server
uv run python ../analysis/homography/main.py ../recordings/<name>.vis.pb
```

## Architecture

### Data Flow

```
Android App → WS /ar-stream → FrameStore → DashboardBridge → WS /ws/dashboard → React
                                                ↑
                                          Annotator (loads .segmentation.pb, .motioncap.pb)
```

- **Android** (`android/`): captures ARCore frames (RGB + depth + IMU + GPS + geometry) as `PerceiverDataFrame` protos, streams live via WebSocket.
- **Server** (`server/streamlog/`): FastAPI app serving port 8080. `FrameStore` holds all frames in memory with pub/sub for live broadcast. `DashboardBridge` manages binary WebSocket protocol to browsers. `Annotator` loads pre-computed `.segmentation.pb` files and serves annotation lookups by `(timestamp_ns, frame_number)`. Analysis pipelines are triggered via REST endpoints (`/api/analysis/recordings/{name}/analyses/{pipeline}`).
- **Dashboard** (`analysis/dashboard/`): React + Vite SPA. `DashboardContext` owns all frame/annotation ring-buffers and playback state. After `npm run build`, the `dist/` is served as static files by FastAPI at `/`. Proto bundle is auto-regenerated on `npm run dev` and `npm run build` via pre-hooks.
- **Offline pipelines** (`server/segmentation/`, `server/pongtown/`, `server/motioncap/`, `server/idoslam/`): each reads a `.vis.pb` recording, writes its own `.{pipeline}.pb` artifact to the same `recordings/` directory, and can be triggered either from CLI or via the server's REST API.
- **Snooker editor** (`analysis/snooker/`): interactive matplotlib-based top-view editor for `.pongtown.pb` files in snooker mode. Loads sibling `.vis.pb` and `.segmentation.pb` automatically. Writes snooker ball color via `track_id` field (1=white … 8=black). On load, auto-backfills ball positions from segmentation masks and auto-fixes occlusion frames.

### Recording File Formats

| Extension | Contents |
|-----------|----------|
| `.vis.pb` | Length-delimited `PerceiverDataFrame` protos |
| `.segmentation.pb` | Length-delimited `SegmentationResponse` protos |
| `.pongtown.pb` | Length-delimited `PongtownResponse` protos (ball tracking + table pose) |
| `.motioncap.pb` | Length-delimited `MotionCaptureResponse` protos |

Wire format for all: `[uint32 BE = N][N bytes proto]` repeated. Implemented in `server/streamlog/protoio.py` and mirrored in `analysis/dashboard/src/services/proto.ts`.

### WebSocket Binary Protocol (server → dashboard)

```
0x01 + length-delimited PerceiverDataFrame(s)   → frame data
0x02 + length-delimited SegmentationResponse(s) → segmentation masks
```

Dashboard → server messages are JSON: `{"action": "seek", "start": N, "end": M}`, `{"action": "get_stats"}`, etc.

### Mask Encoding

Segmentation masks use a compressed binary format (not PNG):
```
[height: uint32 LE][width: uint32 LE][zlib(np.packbits(mask.flatten()))]
```

Python decode: `h,w = struct.unpack('<II', data[:8]); mask = np.unpackbits(zlib.decompress(data[8:]))[:h*w].reshape(h,w)`
TypeScript: `decodeMask()` in `analysis/dashboard/src/services/proto.ts` using pako for inflate.

### Protobuf Definitions

All `.proto` files live in `proto/`. Python generated files (`*_pb2.py`) are in `proto/`. TypeScript uses a pre-compiled static module at `analysis/dashboard/src/proto/bundle.js` (namespace: `bayesmech.vision`). The TypeScript bundle includes: primitives, perceiver, spatial, segmentation, motioncap, idoslam, pongtown, insightgen.

### Key Configuration Files

- `server/streamlog/config.yaml` — server host/port (default 8080), buffer settings, insightgen video params
- `server/segmentation/segmentation_config.yaml` — SAM2 model variant, grid spacing, max objects

### Android App Structure

- `MainActivity` + `AppViewModel` (shared state via StateFlow / SharedPreferences)
- 3 fragments: `CameraFragment`, `LibraryFragment`, `SettingsFragment`
- `ARDataCapture` + `CameraDataExtractor` capture ARCore frames
- `ARStreamClient` streams frames over WebSocket as raw proto bytes
- Full-screen `GLSurfaceView` rendered by `DatagrabRenderer` sits behind the fragment overlay

### SAM2 Setup (one-time)

Download the checkpoint before running segmentation:
```bash
wget https://dl.fbaipublicfiles.com/segment_anything_2/092824/sam2.1_hiera_small.pt \
  -P server/segmentation/models/sam2/
```

Variants: `tiny` (~50 MB), `small` (~175 MB), `base_plus`, `large`.

### Path Conventions

All Python server files resolve the project root as `Path(__file__).parent.parent.parent` and add it to `sys.path` so `from proto import perceiver_pb2` works. Run the server from the project root, not from within `server/`. Run analysis tools from `server/` so relative recording paths work.
