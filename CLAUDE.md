# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

BayesMech Vision is an AR data capture and analysis system. An Android app streams camera/sensor data over WebSockets to a Python FastAPI server, which serves a React dashboard for live monitoring and playback of recordings. A separate offline pipeline runs SAM2 video segmentation on recordings.

## Commands

### Server (Python / uv)

```bash
# Run the vision server (from project root)
uv run python server/streamlog/main.py

# Run segmentation on a recording (from server/ directory)
cd server
uv run python segmentation/main.py ../recordings/<name>.vis.pb

# Quick test — first 200 frames only
uv run python segmentation/main.py ../recordings/<name>.vis.pb --max-frames 200

# Install dependencies
cd server && uv sync
```

### Dashboard (Node / npm)

```bash
cd dashboard
npm install          # install deps
npm run dev          # dev server with HMR (proxies API to localhost:8080)
npm run build        # production build → dashboard/dist/ (served by FastAPI)
npm run lint         # ESLint
```

### Protobuf Regeneration

```bash
# Python: regenerate *_pb2.py files after editing .proto files
cd proto && bash generate_proto.sh

# TypeScript: regenerate dashboard/src/proto/bundle.js after editing .proto files
cd dashboard
npx pbjs -t static-module -w commonjs -o src/proto/bundle.js \
  ../proto/primitives.proto ../proto/perceiver.proto \
  ../proto/spatial.proto ../proto/segmentation.proto
npx pbts -o src/proto/bundle.d.ts src/proto/bundle.js
```

### Analysis Tools (Python)

```bash
# Interactive homography analysis — two frames side-by-side, hover for point correspondence
cd server
uv run python ../analysis/homography/main.py ../recordings/<name>.vis.pb

# Motion heatmap (offline batch)
cd server
uv run python motioncap/main.py ../recordings/<name>.vis.pb --debug-render-video
```

## Architecture

### Data Flow

```
Android App → WS /ar-stream → FrameStore → DashboardBridge → WS /ws/dashboard → React
                                                ↑
                                          Annotator (loads .segmentation.pb)
```

- **Android** (`android/`): captures ARCore frames (RGB + depth + IMU + GPS + geometry) as `PerceiverDataFrame` protos, streams live via WebSocket.
- **Server** (`server/streamlog/`): FastAPI app serving port 8080. `FrameStore` holds all frames in memory with pub/sub for live broadcast. `DashboardBridge` manages binary WebSocket protocol to browsers. `Annotator` loads pre-computed `.segmentation.pb` files and serves annotation lookups by `(timestamp_ns, frame_number)`.
- **Dashboard** (`dashboard/`): React + Vite SPA. `DashboardContext` owns all frame/annotation ring-buffers and playback state. After `npm run build`, the `dist/` is served as static files by FastAPI at `/`.
- **Segmentation** (`server/segmentation/`): Offline-only. Reads `.vis.pb`, runs SAM2 in 100-frame chunks, writes `.segmentation.pb` to the same `recordings/` directory.

### Recording File Formats

| Extension | Contents |
|-----------|----------|
| `.vis.pb` | Length-delimited `PerceiverDataFrame` protos |
| `.segmentation.pb` | Length-delimited `SegmentationResponse` protos |

Wire format for both: `[uint32 BE = N][N bytes proto]` repeated. Implemented in `server/streamlog/protoio.py` and mirrored in `dashboard/src/services/proto.ts`.

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
TypeScript: `decodeMask()` in `dashboard/src/services/proto.ts` using pako for inflate.

### Protobuf Definitions

All `.proto` files live in `proto/`. Python generated files (`*_pb2.py`) are in `proto/`. TypeScript uses a pre-compiled static module at `dashboard/src/proto/bundle.js` (namespace: `bayesmech.vision`).

### Key Configuration Files

- `server/streamlog/config.yaml` — server host/port (default 8080), buffer settings
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

All Python server files resolve the project root as `Path(__file__).parent.parent.parent` and add it to `sys.path` so `from proto import perceiver_pb2` works. Run the server from the project root, not from within `server/`.
