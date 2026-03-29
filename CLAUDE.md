# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

BayesMech Vision is an AR data capture and analysis system. An Android app streams camera/sensor data over WebSockets to a Python FastAPI server, which serves a React dashboard for live monitoring and playback of recordings. Offline pipelines run SAM2/SAM3 video segmentation, RAFT optical-flow motion capture, and AI video analysis on recordings.

> **See also**: `server/CLAUDE.md` for server-specific tooling, module details, and one-time setup steps.

## Commands

### Server (Python / uv)

```bash
# Run the vision server (from project root)
uv run python server/streamlog/main.py

# Install dependencies
cd server && uv sync

# Segmentation (from server/)
cd server
uv run python segmentation/main.py ../recordings/<name>.vis.pb --model sam3 --text "object"
uv run python segmentation/main.py ../recordings/<name>.vis.pb --model sam2 --variant small

# Motion capture: RAFT heatmaps + object tracking (from server/)
cd server
uv run python motioncap/main.py ../recordings/<name>.vis.pb
uv run python motioncap/main.py ../recordings/<name>.vis.pb --output-video
uv run python motioncap/main.py ../recordings/<name>.vis.pb --max-frames 200

# AI video analysis (from server/)
cd server
uv run python genspark/main.py ../recordings/<name>.vis.pb
uv run python genspark/main.py ../recordings/<name>.vis.pb --provider claude
```

### Dashboard (Node / npm)

```bash
cd analysis/dashboard
npm install          # install deps
npm run dev          # dev server with HMR (proxies API to localhost:8080)
npm run build        # production build → analysis/dashboard/dist/ (served by FastAPI)
npm run lint         # ESLint
```

### Protobuf Regeneration

```bash
# Python: regenerate *_pb2.py files after editing .proto files
cd proto && bash generate_proto.sh

# TypeScript: regenerate analysis/dashboard/src/proto/bundle.js after editing .proto files
cd analysis/dashboard
npx pbjs -t static-module -w commonjs -o src/proto/bundle.js \
  ../../proto/primitives.proto ../../proto/perceiver.proto \
  ../../proto/spatial.proto ../../proto/segmentation.proto ../../proto/motioncap.proto
npx pbts -o src/proto/bundle.d.ts src/proto/bundle.js
```

### Analysis Tools (Python)

```bash
# Interactive homography analysis — two frames side-by-side, hover for point correspondence
cd server
uv run python ../analysis/homography/main.py ../recordings/<name>.vis.pb

# RAFT flow debugger with feature correlation visualisation
cd server
uv run python ../analysis/flow/main.py ../recordings/<name>.vis.pb
```

## Architecture

### Data Flow

```
Android App → WS /ar-stream → FrameStore → DashboardBridge → WS /ws/dashboard → React
                                                ↑
                                          Annotator (loads .seg.pb)
```

- **Android** (`android/`): captures ARCore frames (RGB + depth + IMU + GPS + geometry) as `PerceiverDataFrame` protos, streams live via WebSocket.
- **Server** (`server/streamlog/`): FastAPI app serving port 8080. `FrameStore` holds all frames in memory with pub/sub for live broadcast. `DashboardBridge` manages binary WebSocket protocol to browsers. `Annotator` loads pre-computed `.seg.pb` files and serves annotation lookups by `(timestamp_ns, frame_number)`.
- **Dashboard** (`analysis/dashboard/`): React + Vite SPA. `DashboardContext` owns all frame/annotation ring-buffers and playback state. After `npm run build`, the `dist/` is served as static files by FastAPI at `/`.
- **Segmentation** (`server/segmentation/`): Offline-only. Reads `.vis.pb`, runs SAM2/SAM3, writes `.seg.pb` to `recordings/`.
- **Motion Capture** (`server/motioncap/`): Offline-only. RAFT optical flow with pose-based background subtraction → heatmaps + persistent object tracker → writes `.motion.pb`.
- **AI Analysis** (`server/genspark/`): Offline-only. Sends recording to Gemini/Claude/OpenAI for analysis against a prompt in `genspark/prompt.md`.

### Recording File Formats

| Extension | Contents |
|-----------|----------|
| `.vis.pb` | Length-delimited `PerceiverDataFrame` protos |
| `.seg.pb` | Length-delimited `SegmentationResponse` protos |
| `.motion.pb` | Length-delimited `MotionCaptureResponse` protos — per-frame heatmap records followed by a single tracks-summary record (`tracks` field populated) |

Wire format: `[uint32 BE = N][N bytes proto]` repeated. Implemented in `server/streamlog/protoio.py` and mirrored in `analysis/dashboard/src/services/proto.ts`.

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

All `.proto` files live in `proto/`. Python generated files (`*_pb2.py`) are in `proto/`. TypeScript uses a pre-compiled static module at `analysis/dashboard/src/proto/bundle.js` (namespace: `bayesmech.vision`).

### Motion Capture Pipeline

Two sequential steps controlled by `server/motioncap/motioncap_config.yaml`:

- **RAFT** (`raft_motion.py`): dense optical flow residuals. `subtraction_mode`: `"pose"` (uses ARCore depth+pose, best quality), `"consensus"` (median flow, no pose), `"absolute"` (raw flow). Two passes: pass 1 computes float16 residuals, pass 2 applies global normalisation. Output: per-frame `MotionHeatmap` in `.motion.pb`.
- **Tracker** (`tracker.py`): temporal consistency filter → blob detection → nearest-neighbour matching with velocity prediction + appearance features. Merges fragmented tracks, interpolates gaps. Output: single trailing `MotionCaptureResponse` record with `tracks` field containing all `MotionTrack` objects.

Pipeline flags: `pipeline.regenerate_raft: false` skips RAFT and loads heatmaps from existing `.motion.pb` — useful for re-tuning tracker parameters without re-running slow RAFT.

### Key Configuration Files

- `server/streamlog/config.yaml` — server host/port (default 8080), buffer settings
- `server/segmentation/segmentation_config.yaml` — SAM2/SAM3 model variant, grid spacing, sampling
- `server/motioncap/motioncap_config.yaml` — RAFT subtraction mode, reference strategy, tracker thresholds
- `server/genspark/genspark_config.yaml` — provider, video resolution/fps/duration, model names

### Android App Structure

- `MainActivity` + `AppViewModel` (shared state via StateFlow / SharedPreferences)
- 3 fragments: `CameraFragment`, `LibraryFragment`, `SettingsFragment`
- `ARDataCapture` + `CameraDataExtractor` capture ARCore frames
- `ARStreamClient` streams frames over WebSocket as raw proto bytes
- Full-screen `GLSurfaceView` rendered by `DatagrabRenderer` sits behind the fragment overlay

### Path Conventions

All Python server files set `_server_root = Path(__file__).resolve().parent.parent` and `_project_root = _server_root.parent`, then insert both into `sys.path`. This allows `from proto import perceiver_pb2` and `from streamlog.protoio import ProtoIO` to work regardless of invocation directory. Run the server from the project root.
