# Analyzer Dashboard Clean Rewrite Design

Status: draft for rewrite planning

This document describes the current analyzer dashboard, the Streamlog endpoints it
uses, the current playback and seeking model, the expensive upfront work performed
by each panel, and a fresh design for a rewrite. The rewrite plan is intentionally
independent of the current React state structure. It preserves the product shape:
recording library, RGB playback, segmentation, motion capture, sport
understanding, sensor/trajectory views, localization/mapping, Model Musings, live
streaming, upload, and endpoint health.

## Goals

- Make seeking feel immediate, especially while scrubbing.
- Make playback stable at recording frame rate without one network round trip per
  displayed frame.
- Move protobuf parsing, image decoding, mask inflation, overlay primitive
  generation, and analysis indexing off the main thread.
- Make each panel's data dependencies explicit and lazy enough that loading a
  recording does not do work for every possible panel immediately.
- Treat Streamlog as the source of immutable recording data and indexed analysis
  artifacts. The dashboard should own playback clock state for file recordings.
- Keep live streaming separate from file playback. Live mode is subscription
  based; file playback is seek/range based.
- Make endpoint health visible in the dashboard and distinguish HTTP data health
  from live WebSocket health.

## Non-Goals

- Do not redesign Streamlog storage in this document.
- Do not require auth, TLS, or multi-user collaboration semantics.
- Do not require server-rendered overlays as the primary path. Client-side
  rendering remains useful for inspection and rapid iteration.
- Do not preserve the current dashboard provider API. The rewrite should expose
  a cleaner state model even if migration uses adapters temporarily.

## Current System Summary

The current dashboard is a Vite React app under `analysis/dashboard`. The main
state owner is `DashboardProvider` in `src/context/DashboardContext.tsx`. It owns:

- WebSocket connection status.
- Current displayed RGB/depth/geometry frame.
- Current segmentation annotation.
- Current Pongtown frame.
- Playback state: `currentIndex`, `totalFrames`, `isPlaying`, `serverFps`.
- Live/file mode.
- Trajectory positions and precomputed sensor data.
- Loaded IDOSLAM artifact.
- Ring buffers for decoded frames, decoded annotations, and Pongtown records.

The `DashboardWebSocketService` is a singleton. It connects to the dashboard
WebSocket, demultiplexes binary prefixes, and fans messages out to listener sets:

- Prefix `0x01`: length-delimited `PerceiverDataFrame`.
- Prefix `0x02`: length-delimited `SegmentationResponse`.
- Prefix `0x03`: length-delimited `PongtownResponse`.
- Text JSON: stats, trajectory, sensor data.

The app still uses a mixture of new `/streamlog/{plane}/...` endpoints and legacy
`/api/...` compatibility endpoints. File playback still depends on legacy
playback state because the WebSocket seek endpoint resolves the current recording
from Streamlog's server-side playback state.

## Current Streamlog Endpoints Hit By The Dashboard

This table lists the endpoints the current dashboard can call. "Preferred" means
the current TypeScript service tries the new `/streamlog` route first. "Fallback"
means the legacy route is still used when the new route is absent or for state
that has not been redesigned yet.

| Method | Endpoint | Caller | Purpose | Response |
| --- | --- | --- | --- | --- |
| GET | `/streamlog/health` | `streamlog.ts` discovery and health dialog | Discover listeners, plane assignments, roots, version, config hash | JSON |
| GET | `/streamlog/outstream/health` | health dialog | Outstream status and limits | JSON |
| GET | `/streamlog/instream/health` | health dialog | Upload/transcription limits and open imports | JSON |
| GET | `/streamlog/insightgen/health` | health dialog | Insightgen provider/library status | JSON |
| GET | `/streamlog/analyzers/health` | health dialog | Analyzer queue/pipeline status | JSON |
| GET | `/streamlog/insightgen/recordings` | `fetchRecordings` preferred, upload/load modal | Recording library | JSON `{ recordings }` |
| GET | `/api/recordings` | `fetchRecordings` fallback | Legacy recording library | JSON `{ recordings }` |
| POST | `/api/playback/start` | `startPlayback`, `loadRecording` | Set server-side playback recording for WebSocket seek | JSON |
| POST | `/api/playback/stop` | `stopPlayback` | Clear server-side playback state | JSON |
| POST | `/api/playback/live` | `switchToLive` | Set server-side mode to live | JSON |
| GET | `/api/playback/status` | health dialog | Legacy playback mode status | JSON |
| GET | `/api/stream` | `fetchStreamStats`, health dialog | Legacy playback stats | JSON |
| WS | `/streamlog/outstream/dashboard/ws` | `DashboardWebSocketService` preferred | File seek, stats, trajectory, sensor data, live frame fanout | Text JSON and prefixed protobuf binary |
| WS | `/ws/dashboard` | WebSocket fallback and health dialog | Legacy dashboard bridge | Text JSON and prefixed protobuf binary |
| POST | `/streamlog/instream/imports` | `uploadRecording` preferred | Start upload/import session | JSON import session |
| PUT | `/streamlog/instream/imports/{import_id}/content` | `uploadRecording` preferred | Upload full `.vis.pb` bytes | JSON import session |
| POST | `/streamlog/instream/imports/{import_id}:complete` | `uploadRecording` preferred | Validate/publish upload | JSON import session |
| POST | `/api/upload_recording` | upload fallback | Legacy multipart upload and start playback | JSON |
| GET | `/streamlog/outstream/recordings/{id}/analyses/idoslam/artifacts/proto` | `fetchIdoSlam` preferred | Download IDOSLAM protobuf artifact | protobuf, usually length-delimited |
| GET | `/api/idoslam?file={id}` | `fetchIdoSlam` fallback | Legacy IDOSLAM artifact | protobuf |
| GET | `/streamlog/outstream/recordings/{id}/analyses/motioncap/records?include_summary=true` | `fetchRecordingMotioncapData` preferred | Download all motioncap records and summary | length-delimited protobuf |
| GET | `/api/analysis/recordings/{id}/analyses/motioncap/records?include_summary=true` | motioncap fallback | Legacy motioncap records | length-delimited protobuf |
| GET | `/streamlog/outstream/recordings/{id}/analyses/pongtown/records?include_summary=true` | `fetchRecordingPongtownData` preferred | Download all Pongtown records and summary | length-delimited protobuf |
| GET | `/api/analysis/recordings/{id}/analyses/pongtown/records?include_summary=true` | Pongtown fallback | Legacy Pongtown records | length-delimited protobuf |
| GET | `/streamlog/outstream/recordings/{id}/analyses/genspark/artifacts/proto` | `fetchGensparkResponse` preferred | Download Model Musings response | protobuf |
| GET | `/api/analysis/recordings/{id}/analyses/genspark/artifacts/proto` | Genspark artifact fallback | Legacy Model Musings artifact | protobuf |
| GET | `/streamlog/insightgen/recordings/{id}/chat?since_timestamp_ns={ns}` | `fetchGensparkChatHistory` preferred | Download chat history/delta | protobuf `ChatHistory` |
| GET | `/api/insightgen/chat?file={id}&since_timestamp_ns={ns}` | chat fallback | Legacy chat history/delta | protobuf `ChatHistory` |
| POST | `/streamlog/insightgen/recordings/{id}/chat` | `sendGensparkMessage` preferred | Send follow-up Model Musings message | JSON response |
| POST | `/api/insightgen/chat` | chat post fallback | Legacy follow-up chat | JSON response |
| GET | `/streamlog/analyzers/pipelines` | health dialog | List runnable analyzer pipelines | JSON |
| POST | `/streamlog/analyzers/recordings/{id}/runs` | `regenerateGensparkAnalysis` preferred | Start analyzer run for Genspark regeneration | JSON run |
| POST | `/api/insightgen/regenerate` | regenerate fallback | Legacy Genspark regenerate | JSON |

The current Streamlog server also exposes better file-playback endpoints that are
not yet the dashboard's primary path:

- `GET /streamlog/outstream/recordings/{id}/frames/{frame_index}`
- `GET /streamlog/outstream/recordings/{id}/frames`
- `GET /streamlog/outstream/recordings/{id}/frames:resolve`
- `GET /streamlog/outstream/recordings/{id}/annotations/{kind}:resolve`
- `GET /streamlog/outstream/recordings/{id}/annotations/{kind}`
- `GET /streamlog/outstream/recordings/{id}/trajectory`
- `GET /streamlog/outstream/recordings/{id}/sensors`
- `GET /streamlog/outstream/recordings/{id}/analyses`
- `GET /streamlog/outstream/recordings/{id}/analyses/{analysis}`
- `GET /streamlog/outstream/recordings/{id}/analyses/{analysis}/views/{view}`

These should become the primary rewrite path.

## Current Dashboard WebSocket Protocol

The dashboard sends JSON commands over the dashboard WebSocket:

| Client action | Payload | Server behavior | Response |
| --- | --- | --- | --- |
| `get_stats` | `{ action: "get_stats" }` | Reads Streamlog playback state | JSON `{ type: "stats", source, frame_count, buffered_frames, recording_fps, first_timestamp_ns, last_timestamp_ns }` |
| `seek` | `{ action: "seek", start, end }` | Reads frame range from current server-side recording, then matching segmentation and Pongtown records | Binary prefix `0x01` frames, optional prefix `0x02` annotations, optional prefix `0x03` Pongtown |
| `get_trajectory` | `{ action: "get_trajectory" }` | Scans all frames, decodes poses, emits x/z path | JSON `{ type: "trajectory", positions }` |
| `get_sensor_data` | `{ action: "get_sensor_data" }` | Scans all frames, decodes IMU/GPS | JSON `{ type: "sensor_data", frames }` |
| `get_annotations` | `{ action: "get_annotations", frame_number? }` | Reads all segmentation records or one frame | Binary prefix `0x02` |
| `get_pongtown` | `{ action: "get_pongtown", frame_number? }` or `{ start, end }` | Reads matching Pongtown records | Binary prefix `0x03` |

Issues:

- Messages have no request IDs.
- `seek` responses cannot be cancelled.
- The client has only one boolean `seekPendingRef`, so concurrent seek requests
  are ambiguous.
- File playback sends a WebSocket `seek` for every frame.
- WebSocket `seek` depends on server-side playback state set by
  `/api/playback/start`, so the file path is not stateless.

## Current Playback And Seeking Logic

### Recording Load

On `loadRecording(name)`:

1. Clear frame, annotation, Pongtown, trajectory, sensor, coverage, and SLAM
   state.
2. Call `POST /api/playback/start` to set server playback state.
3. Fetch IDOSLAM artifact.
4. Ask WebSocket for stats.
5. Ask WebSocket for full trajectory.
6. Ask WebSocket for full sensor data.
7. Send `seek(0, 1)`.
8. Set `isPlaying = true`.

This means a recording load starts playing immediately and kicks off multiple
large requests at once. Trajectory and sensor data require a full server-side
scan of all source frames. IDOSLAM, Genspark, and the active panel may also start
large downloads in parallel.

### File Playback

File playback is client-driven:

1. `setInterval(1000 / serverFps)` advances `currentIndexRef`.
2. Each tick sends `dashboardWs.seek(next, next + 1)`.
3. The server reads one frame and returns matching annotations and Pongtown.
4. The client decodes the returned frame on the main thread.
5. If `isPlaying` is still true, the latest decoded frame becomes
   `displayedFrame`.

Problems:

- Playback requires one WebSocket request per frame.
- Network, server read, protobuf decode, JPEG Blob creation, depth rendering, and
  mask/annotation decoding can all sit on the critical path.
- `setInterval` is not tied to actual decode/display readiness. It can advance
  the logical index ahead of the displayed frame.
- Late responses can display stale frames because there is no request generation
  or request ID.
- If decode/render is slow, the interval still queues more work.

### Scrubbing And Seeking

The range input calls:

1. Pause if currently playing.
2. `seekTo(index)`.
3. `seekTo` clamps the index, updates `currentIndex`, updates `frameCount`,
   sets `seekPendingRef = true`, and sends `seek(index, index + 1)`.
4. `handleFrames` accepts file-mode frames only when `seekPendingRef` or
   `isPlayingRef` is true.
5. On a seek response, it clears `seekPendingRef` and displays the latest frame.

Problems:

- Slider `onChange` can fire many seeks, but there is only one
  `seekPendingRef`.
- Older responses can arrive after newer responses and still display.
- `requestFrame(frameNumber)` sends a WebSocket seek without setting
  `seekPendingRef`. If playback is paused, the response can be dropped.
- `seekTo` always sends a network request, even if the decoded frame is already
  in the `FrameBuffer`.
- There is no drag state. The app does not distinguish pointer-down scrubbing,
  preview seeking, and committed seeking.
- Annotation and Pongtown updates are coupled to frame seek responses, but some
  panels also fetch their own full artifacts, creating redundant paths.

### Live Mode

Live mode calls `POST /api/playback/live`, sets local `isLive`, starts displaying
incoming WebSocket live frames when `isPlaying` is true, and updates coverage
statistics. It has no seek capability. Pausing only freezes display; ingest can
continue.

## Current Upfront And Per-Panel Computation

This section names the work currently done so the rewrite can decide what to
move, defer, or eliminate.

### Common Frame Path

Every frame decoded by `FrameDecoder.decodeFrame` may do:

- Extract `FrameIdentifier`, pose, IMU, GPS, inferred geometry.
- Cache/scales camera intrinsics.
- Scan JPEG headers to infer RGB dimensions if width/height is absent.
- Create an object URL for RGB bytes.
- Decode raw depth bytes into a grayscale canvas and convert it to a data URL.
- Convert point cloud and plane geometry into plain JS arrays.

This all runs on the main thread. Depth conversion is especially expensive and
is performed even if the sensors tab is not visible.

### Segmentation Panel

Current work:

- Receives matching segmentation records from WebSocket seek responses.
- Inflates each mask with `pako`.
- Builds a composite mask canvas.
- Converts the canvas to a data URL for display.
- Builds a per-frame legend and decoded mask arrays.
- Stores a floor-search annotation buffer so frame N can reuse the latest
  annotation with `frameNumber <= N`.

Costs:

- `pako.inflate`, bit unpacking, and canvas compositing are main-thread tasks.
- Data URLs allocate large strings and add GC pressure.
- Floor-search reuse can show stale annotations when exact records are sparse,
  which is sometimes intended but should be explicit in the new design.

### Motion Capture Panel

On first activation for a recording:

- Fetches all motioncap records with `include_summary=true`.
- Decodes all records in `fetchRecordingMotioncapData`.
- Builds `frames`, `byFrameNumber`, `byHeatmapIndex`.
- Extracts summary tracks and segmentation trajectories.

Per displayed frame:

- Finds current motioncap frame by displayed frame number or heatmap index.
- Loads the displayed RGB Blob URL into an `Image`.
- Draws the RGB image into a base canvas.
- Inflates the current heatmap with `pako`.
- Converts heatmap bytes to `ImageData` using a jet color map.
- Draws active trajectory tails and labels as SVG.
- Keeps an LRU cache of 12 rendered heatmaps.

Costs:

- Full artifact decode happens before the panel can be fully usable.
- Heatmap inflation/colorization is main-thread work.
- RGB is decoded again by the browser from a Blob URL for the panel overlay,
  even though the RGB frame was already decoded for the main stream viewer.

Rewrite decision:

- The motioncap panel renders the current RGB frame underneath RAFT or
  segmentation tracks, but the panel may downsample its RGB canvas internally to
  keep playback cheap.
- Motion heatmaps are rendered because they are an important diagnostic layer.
  They must be lazy, worker-side, and throttled so playback does not build a
  queue of obsolete heatmap frames.
- Track tails are selected from the current displayed frame index so they remain
  inline with video playback without main-thread heatmap inflation.

### Sport Understanding Panel

On first activation for a recording:

- Fetches all Pongtown records with `include_summary=true`.
- Decodes every Pongtown record.
- Builds maps by frame number and frame index.
- Keeps summary record for 3D table state.

Per displayed frame:

- Also requests current Pongtown frame via WebSocket if not already in the
  buffer.
- Selects current Pongtown record by displayed frame number or index.
- Computes overlay geometry for hull, PnP, or global pose.
- Renders the current RGB Blob URL under SVG overlays.
- Passes full summary and all frames to `PongtownTable3D`.

Pongtown 3D table:

- Builds a Three.js scene per table/sport change.
- Computes bounce markers from summary.
- Applies pose corrections.
- Builds timed snooker frames and smooths snooker marker positions.
- On every marker update, disposes and recreates marker meshes/sprites.

Costs:

- Full Pongtown decode happens on panel open.
- 3D marker updates allocate and dispose Three.js objects frequently.
- Snooker smoothing and marker creation depend on all frames and can become
  expensive.
- WebSocket per-frame Pongtown fetch duplicates the full artifact path.

### Sensors Panel

On recording load, before the sensors tab is opened:

- WebSocket `get_sensor_data` scans all source frames server-side and sends all
  IMU/GPS records as JSON.
- WebSocket `get_trajectory` scans all source frames server-side and sends pose
  x/z positions as JSON.

On first render:

- `DashboardPage` derives `gpsTrack` by scanning all sensor data.
- Four `MotionChart` components each build complete Chart.js datasets from all
  sensor frames.
- `TrajectoryCanvas` computes a trajectory scale once, then redraws on seek.
- `GpsMapViewer` creates a Leaflet polyline for the full GPS route.
- `GeometryStreamViewer` renders point cloud and plane overlays for the current
  frame.

Costs:

- Full sensor and trajectory loading happens for every recording even if the user
  never opens sensors/localization.
- JSON is larger and slower than typed binary or protobuf for long recordings.
- Chart datasets are rebuilt on the main thread.
- Geometry overlay projection happens on the main thread.

### Localization And Mapping Panel

Load behavior:

- IDOSLAM is fetched lazily when the localization tab opens.
- The current segmentation window is loaded with the same floor-match policy as
  the segmentation tab because road mask and edge rendering depends on decoded
  mask rasters.
- Frame decode must retain camera intrinsics across frames so road projection
  can work even when intrinsics are only populated in early frames.

When the localization tab is active:

- Render six compact canvases, two per row:
  - Pre-optimization SLAM from `frame_poses`.
  - Post-optimization SLAM from `refined_frame_poses`.
  - SIFT correspondences over the current RGB frame.
  - Camera ground pose.
  - Road mask and edge estimates over the current RGB frame.
  - Ground plane projection.
- SLAM maps use 3D PCA on pose `world_pose.position` values rather than a fixed
  `x/z` projection. Mean-center the pose cloud, build the covariance matrix, use
  power iteration for the dominant axis, deflate it, then use power iteration for
  the second axis. Fit the resulting 2D trajectory into the map canvas with a
  fixed margin.
- The post-optimization map must not silently fall back to raw poses. If refined
  poses are unavailable, show an empty state so missing optimization is visible.
- SIFT overlay selects the nearest `pair_debug` record by current frame index,
  scales correspondence coordinates into the contained RGB rect, draws
  source-to-target lines on the same image, and caps the drawn correspondences at
  roughly 900 for readability.
- Road rendering combines decoded segmentation masks with labels `road`,
  `pavement`, and `bike`. The `bike` mask is also kept separately as an anchor
  and occluder for edge selection.
- Road edge extraction scans mask rows from the bottom upward, keeps sufficiently
  wide contiguous road segments, uses the bike centroid or image center as the
  anchor, rejects large inter-row edge jumps, skips bike-occluded edge pixels,
  and derives left, right, and midline samples.
- Ground projection scales retained camera intrinsics to mask resolution, uses
  IDOSLAM `plane_width_summary_json` pitch/height when present, casts mask
  pixels through an image-to-ground projector, keeps plausible near-field points,
  and projects edge samples through the same projector.
- Camera ground pose computes the camera basis from the current frame quaternion
  and compares it to a ground/world basis derived from GPS bearing when present.

Costs:

- Some map projection work is repeated on current-pose changes even though most
  of it is recording-wide.
- Road projection scans masks and samples pixels on the main thread.
- SIFT drawing uses the already decoded `ImageBitmap`, avoiding a second Blob URL
  image decode.
- This panel depends on both current frame and current segmentation annotation,
  so slow frame or mask decode directly affects it.

### Model Musings Panel

On recording selection:

- Fetches Genspark response and chat history in parallel.
- Builds markdown text for summary and tool calls.
- Can submit follow-up chat.
- Can start Genspark regeneration through the analyzers plane.

Costs:

- Mostly not a playback bottleneck.
- It is in the first viewport and currently loads on every recording select.
  That is acceptable if deprioritized behind first-frame display.

## Bottleneck Summary

The current slow paths are:

- One server/WebSocket request per playback frame.
- Main-thread protobuf decode for frame, segmentation, motioncap, Pongtown, and
  IDOSLAM artifacts.
- Main-thread JPEG Blob URL creation and repeated browser image decode.
- Main-thread depth conversion to canvas data URL.
- Main-thread `pako.inflate` for segmentation masks and motion heatmaps.
- Full sensor and trajectory JSON generation on every recording load.
- Full artifact fetch/decode for panel activation, with no progressive index.
- No cancellation or stale response protection for seeks.
- React state updates for high-frequency playback data.
- Canvas and Three.js render work triggered by React effects instead of a
  dedicated playback/render scheduler.

## Clean Rewrite Architecture

The rewrite should split the dashboard into four layers:

1. Streamlog client: typed endpoint functions and WebSocket/live adapters.
2. Recording runtime: playback clock, request scheduler, cache manager, and
   worker coordinator.
3. Panel data stores: lazily loaded, panel-specific derived data.
4. React views: subscribe to low-frequency snapshots and render from refs or
   external stores.

### Core Concepts

`RecordingRuntime`

- One runtime per loaded recording.
- Owns recording ID, manifest, frame index metadata, analysis availability,
  caches, workers, and playback clock.
- Has a generation ID. Every load, unload, seek, and worker request carries a
  generation. Stale responses are ignored.

`PlaybackClock`

- Owns desired time/index, playback rate, playing/paused/scrubbing state.
- Advances through `requestAnimationFrame`, not `setInterval`.
- Maps wall-clock time to target frame using manifest timestamps or nominal fps.
- Allows frame skipping when decode falls behind.
- Separates `targetIndex`, `displayIndex`, `requestedIndex`, and
  `committedSeekIndex`.

`FrameScheduler`

- Converts target indexes into prioritized frame range requests.
- Maintains a decode/prefetch window.
- Cancels obsolete HTTP requests with `AbortController`.
- Batches adjacent frame needs into range requests.
- Never blocks UI state updates on network response.

`CacheManager`

- Keeps three cache tiers:
  - Raw protobuf frame bytes by index/range.
  - Decoded frame metadata and `ImageBitmap` by frame index.
  - Optional derived assets: depth bitmap, segmentation bitmap,
    overlay primitives.
- Uses memory budgets and LRU eviction.
- Revokes/disposes image resources deterministically.

`WorkerCoordinator`

- Owns workers and request IDs.
- Provides typed async APIs for frame decode, annotation decode, motioncap parse,
  Pongtown parse, sensor indexing, and SLAM derivations.
- Transfers `ArrayBuffer`, `ImageBitmap`, `ImageData`, and typed arrays instead
  of copying.

`PanelStore`

- Each panel gets its own data store with explicit states:
  - `idle`
  - `loading`
  - `ready`
  - `empty`
  - `error`
- Panel stores can be prewarmed at low priority but should not block first frame.

## Target Streamlog Endpoint Strategy

### File Playback Should Be Stateless

The rewrite should not call `/api/playback/start` for file playback. A selected
recording ID should be enough for every file request.

Preferred file playback endpoints:

- `GET /streamlog/outstream/recordings/{id}` or
  `GET /streamlog/insightgen/recordings/{id}` for manifest/detail.
- `GET /streamlog/outstream/recordings/{id}/analyses` for artifact availability.
- `GET /streamlog/outstream/recordings/{id}/frames?start_frame_index={a}&end_frame_index={b}&limit={n}` for range prefetch.
- `GET /streamlog/outstream/recordings/{id}/frames/{frame_index}` for cold single-frame seek.
- `GET /streamlog/outstream/recordings/{id}/frames:resolve?timestamp_ns={ts}&match=nearest&tolerance_ns={tol}` for timestamp-aligned comparison.
- `GET /streamlog/outstream/recordings/{id}/annotations/segmentation?start_frame_number={a}&end_frame_number={b}` for annotation prefetch.
- `GET /streamlog/outstream/recordings/{id}/annotations/segmentation:resolve?frame_number={n}` for one annotation.
- `GET /streamlog/outstream/recordings/{id}/trajectory` only when a trajectory view is needed.
- `GET /streamlog/outstream/recordings/{id}/sensors` only when sensor/localization features need it.
- `GET /streamlog/outstream/recordings/{id}/analyses/{analysis}/records` for sliceable analysis artifacts.
- `GET /streamlog/outstream/recordings/{id}/analyses/{analysis}/artifacts/{artifact}` for full non-sliceable artifacts.

### WebSocket Use

Use WebSockets for live ingest/playback and optional push notifications, not for
file playback's primary frame path.

Target WebSockets:

- `WS /streamlog/instream/live/{id}`: Android/live ingestion.
- `WS /streamlog/outstream/live/{id}` or existing dashboard WS: live dashboard
  subscription.
- Optional future `WS /streamlog/events`: analyzer run status, recording import
  status, new artifact notifications.

File playback should work if every WebSocket is disconnected as long as HTTP
outstream endpoints are healthy.

### Recommended Streamlog API Improvements

These are not required to begin the rewrite but would simplify correctness:

- Add a first-class recording manifest endpoint under outstream:
  `GET /streamlog/outstream/recordings/{id}`.
- Add a protobuf or JSON frame-range envelope that includes records and metadata
  in one body, instead of using response headers for frame metadata.
- Add a batch "display bundle" endpoint:
  `GET /streamlog/outstream/recordings/{id}/display-bundle?start_frame_index=&end_frame_index=&layers=rgb,segmentation,pongtown`
  for latency-sensitive seeks. This endpoint should still return raw protobuf
  records, not pre-rendered UI state.
- Add slice indexes for analysis artifacts so motioncap and Pongtown can be
  fetched by frame range without scanning whole artifact files.
- Add binary sensor/trajectory endpoints or protobuf records instead of large
  JSON arrays for long recordings.
- Add optional request IDs to dashboard WebSocket responses if it remains in use
  for file seek compatibility.

## Fresh Playback And Seeking Design

### Recording Load

On recording selection:

1. Abort and dispose the previous runtime.
2. Create a new `RecordingRuntime` with a new generation ID.
3. Fetch manifest and analysis availability.
4. Fetch and decode frame 0 or nearest poster frame.
5. Display first frame as soon as it is decoded.
6. Start prefetch around frame 0.
7. Start low-priority panel prewarm:
   - Genspark response/chat because it is in the top viewport.
   - Analysis availability metadata.
   - Optional sensor/trajectory metadata, but not full data unless the UI needs
     it.
8. Leave playback paused unless product policy explicitly chooses autoplay.

Recording load should never block first frame on sensors, trajectory, IDOSLAM,
motioncap, or Pongtown.

### Playback Clock

Playback should be driven by `requestAnimationFrame`:

1. On play, store `playStartWallTime` and `playStartMediaTime`.
2. On each animation frame, compute target media time.
3. Convert media time to target frame:
   - Prefer timestamp index from manifest.
   - Fall back to `round(mediaTime * fps)`.
4. If decoded frame for target index exists, display it.
5. If target is not decoded, display nearest decoded frame not newer than target
   within a tolerance. If the gap is too large, show a loading state or skip
   ahead when decoded.
6. Keep the scheduler prefetching ahead of the target.

This model tolerates variable decode latency. It also lets playback skip frames
instead of slowing the UI event loop.

### Prefetch Windows

Use configurable windows by mode:

- Paused: current frame plus `[-2, +8]`.
- Playing forward: current frame plus `[0, +90]` at 30 fps, about 3 seconds.
- Scrubbing: latest pointer index plus small preview window `[-1, +1]`.
- After committed seek: current frame plus `[-15, +45]`.

Request ranges in chunks, for example 12 to 32 frames per HTTP request. Tune
chunk size based on average frame byte size and Streamlog's
`max_outstream_response_bytes`.

### Seeking And Scrubbing

Use explicit seek phases:

- `beginScrub`: pause playback, remember whether it was playing, increment
  scrub generation.
- `previewScrub(index)`: update UI thumb immediately; request preview frame
  throttled to roughly 15 to 30 Hz; ignore stale preview responses.
- `commitScrub(index)`: cancel older requests, set target index, request exact
  frame at high priority, update prefetch window.
- `cancelScrub`: restore previous display and play state if needed.

Every request must carry:

- `recordingId`
- `runtimeGeneration`
- `requestId`
- `priority`
- `purpose`: `preview`, `commit`, `prefetch`, `playback`

Display rules:

- A decoded frame can display only if its recording and generation match.
- A preview response can display only while the same scrub generation is active.
- A committed seek response wins over prefetch/playback responses.
- A playback response can display only if it is close to the current clock
  target.

### Frame Identity

The rewrite should treat frame index, frame number, timestamp, and relative
timestamp as distinct:

- `frameIndex`: zero-based storage index and playback timeline index.
- `frameNumber`: device/source frame number from protobuf.
- `timestampNs`: absolute or source timestamp.
- `relativeTimestampNs`: timeline timestamp relative to recording start.

All caches should key primary frame data by `frameIndex`. Analysis records can
maintain secondary indexes by frame number and timestamp.

### Annotation Matching

Segmentation matching is fixed to floor matching in the dashboard:

- Display the latest segmentation annotation at or before the current frame.
- Do not expose Exact/Floor/Nearest UI options in the segmentation panel.
- Keep the floor-match policy visible in implementation naming and tests, not as
  a user-facing mode selector.
- Exact or nearest matching can remain worker/index capabilities for debugging
  or future tools, but the production dashboard panel should not switch modes.

## Web Worker Plan

Workers are the main performance mechanism for the rewrite. They should use a
small typed protocol rather than passing arbitrary React-shaped objects.

### Worker Message Envelope

All worker requests:

```ts
type WorkerRequest<T> = {
  requestId: number
  recordingId: string
  generation: number
  priority: 'interactive' | 'playback' | 'prefetch' | 'background'
  type: string
  payload: T
}
```

All worker responses:

```ts
type WorkerResponse<T> = {
  requestId: number
  recordingId: string
  generation: number
  type: string
  ok: boolean
  payload?: T
  error?: string
}
```

The main thread owns stale-response filtering. Workers should also drop queued
work for cancelled generations when asked.

### Frame Worker

Responsibilities:

- Split length-delimited frame payloads.
- Decode `PerceiverDataFrame`.
- Extract frame identifiers, pose, IMU, GPS, intrinsics, and geometry.
- Decode RGB bytes into `ImageBitmap` using `createImageBitmap(new Blob(...))`.
- Optionally parse JPEG dimensions if missing.
- Lazily decode depth only when requested by a visible panel.
- Render depth to `ImageBitmap` using `OffscreenCanvas`.
- Return transferable `ImageBitmap` and typed arrays.

Main-thread output shape:

```ts
type DecodedFrameAsset = {
  frameIndex: number
  frameNumber: number
  timestampNs: number
  rgbBitmap?: ImageBitmap
  depthBitmap?: ImageBitmap
  metadata: FrameMetadata
}
```

Do not create Blob URLs for playback frames in the new design. Canvas components
should draw `ImageBitmap` directly.

### Annotation Worker

Responsibilities:

- Decode length-delimited `SegmentationResponse` records.
- Build exact, floor, and nearest indexes.
- Inflate `maskData` with `pako`.
- Unpack mask bits.
- Build legends.
- Composite masks to `ImageBitmap` using `OffscreenCanvas`.
- Optionally produce individual masks for localization road projection.

The worker should maintain a small cache of decoded masks around the current
timeline window. It should avoid returning full mask arrays unless a panel asks
for them.

### Motioncap Worker

Responsibilities:

- Decode all or ranged `MotionCaptureResponse` records.
- Build summary tracks, segmentation trajectories, frame number index, and
  timestamp index.
- Precompute trajectory tails or compact per-frame visible segments.
- Keep per-frame motioncap records available for lazy heatmap lookup.
- Inflate and colorize only the requested current heatmap frame, in the worker.
- Downsample heatmap colorization to a practical draw width such as `640px`
  before transferring an `ImageBitmap`.

The main thread should receive:

- Track summaries for legends.
- Current frame trajectory primitives.
- Current frame heatmap bitmap when ready.

The main thread should not inflate heatmaps or loop over heatmap pixels. Slow or
stale heatmap responses must not overwrite newer displayed frames.

### Pongtown Worker

Responsibilities:

- Decode all or ranged `PongtownResponse` records.
- Build summary, frame indexes, and table specification.
- Compute 2D overlay geometry for hull/PnP/global modes.
- Compute pingpong bounce markers and pose-corrected markers.
- Compute snooker timed frames, stable marker smoothing, demo overrides if still
  needed, and constrained table positions.

The Three.js panel should receive stable marker data and update object positions
instead of disposing/recreating meshes every frame.

### Sensor And Trajectory Worker

Responsibilities:

- Parse sensor/trajectory data from Streamlog.
- Convert GPS to local coordinates.
- Build typed arrays for IMU channels.
- Build frame-index and timestamp lookup tables.
- Generate chart downsampled views for the current viewport using min/max or
  LTTB-style decimation.
- Return current GPS by frame index in O(1) or O(log n), not by reverse scanning
  arrays on each render.

Chart.js should consume already-decimated data for the visible x-axis window.

### Localization Worker

Responsibilities:

- Decode IDOSLAM artifact.
- Build raw/refined pose indexes by frame index, frame number, and timestamp.
- Compute PCA projections for raw and refined SLAM pose clouds once per
  recording.
- Select nearest pair debug record by current frame index.
- Subsample SIFT correspondences to the current visual density limit.
- Build road projection data from decoded segmentation masks and frame
  intrinsics.
- Combine road/pavement/bike masks, extract road edge rows, reject unstable
  edge jumps, and use the bike mask as both anchor and occlusion test.
- Build camera attitude primitives.

The main thread should only draw returned primitives, or the worker should render
to `OffscreenCanvas` where browser support is adequate.

### Render Workers And OffscreenCanvas

Initial rewrite should use `ImageBitmap` transfer and main-thread canvas draws.
Then move these to OffscreenCanvas where bottlenecks remain:

- Depth rendering.
- Segmentation compositing.
- Road mask projection.
- Trajectory and map canvases.

Three.js can remain on the main thread initially because user interaction with
OrbitControls is direct. If it remains a bottleneck, move Three.js to
OffscreenCanvas in a second pass.

## Target Panel Loading Strategy

### Always Loaded After Recording Select

Only the minimum needed to make the first screen useful:

- Recording manifest.
- Analysis availability.
- First frame.
- Small frame prefetch window.
- Genspark response/chat at low priority because it is visible in the top grid.

### Segmentation

Load on demand for the current window:

- Request segmentation for current frame and prefetch window.
- Decode masks in annotation worker.
- Cache exact/floor/nearest records.
- Composite only frames likely to display.

### Motion Capture

Load when tab is opened or prewarm after idle:

- Fetch motioncap summary and a compact index first.
- Decode full tracks in worker for legends and tail overlays.
- Prefer the trailing motioncap summary record and avoid decoding every
  per-frame heatmap record when the panel only needs tracks.
- Playback requests only the visible track primitives for the current displayed
  frame index.
- Render the current `ImageBitmap` under the tracks through a low-resolution
  canvas layer, capped to a practical draw width such as `640px` when needed.
- Request heatmaps separately from track primitives. Track updates are fast and
  tied to the displayed frame; heatmap requests are single-flight with one
  queued latest request so playback cannot accumulate stale worker jobs.
- Merge a heatmap response only if its frame index and frame number still match
  the current displayed frame; otherwise close and discard its bitmap.

If Streamlog cannot yet return summary/index separately, fetch the full artifact
in the worker, but do not block first frame.

### Sport Understanding

Load when tab is opened or prewarm after idle:

- Fetch Pongtown summary and frame records.
- Build overlay indexes in worker.
- Request per-frame overlay primitives by frame index.
- Keep the 3D table scene stable and update marker positions/materials.

### Sensors

Load when tab is opened or when localization requires sensor data:

- Fetch sensors in binary/protobuf or JSON.
- Worker builds typed arrays and chart-ready decimated windows.
- GPS route is built once and marker updates by current index.

### Localization And Mapping

Load when tab is opened or after low-priority prewarm:

- Fetch IDOSLAM artifact.
- Worker builds pose indexes and map alignment once.
- Current-frame updates ask worker for cheap current pose and pair debug
  primitives.
- Road projection runs only when segmentation for current frame is available.

### Model Musings

Load after first frame display:

- Fetch Genspark response and chat history in parallel.
- Render markdown on main thread unless it proves expensive.
- Regeneration starts analyzer run and should subscribe to run status if event
  stream exists.

## Proposed State Model

Do not keep high-frequency playback state solely in React context. Use an
external store with selector subscriptions.

Top-level stores:

- `ConnectionStore`: Streamlog discovery, HTTP health, WebSocket health.
- `RecordingStore`: selected recording, manifest, analysis availability.
- `PlaybackStore`: mode, target index, display index, playing, rate, buffering.
- `FrameStore`: decoded frame cache and loading states.
- `AnnotationStore`: segmentation cache and matching policy.
- `PanelStore`: per-panel data states.
- `WorkerStore`: worker queue metrics, in-flight requests, errors.

React components subscribe to small slices:

- Header subscribes to connection summary.
- Playback controls subscribe to playback summary and actions.
- RGB viewer subscribes to current frame bitmap.
- Panels subscribe to their own store plus current frame identity.

This avoids re-rendering the whole dashboard on every decoded frame or worker
message.

## UI Health Model

The status badge should not use a single `Connected`/`Disconnected` concept for
all modes.

Recommended status dimensions:

- `Streamlog HTTP`: discovery and data endpoints healthy/unhealthy.
- `File playback`: ready, buffering, seeking, stalled, error.
- `Live stream`: connected, connecting, disconnected, idle.
- `Workers`: ready, degraded, crashed.
- `Analysis data`: available, loading, missing, error.

For file playback, a disconnected live WebSocket should not imply the dashboard
is unusable. The badge can show:

- `READY`: HTTP playback path healthy.
- `BUFFERING`: waiting for frame decode/network.
- `LIVE`: live WebSocket connected.
- `DEGRADED`: some optional endpoints failed.
- `OFFLINE`: no Streamlog HTTP health.

The health popup should list:

- Global health.
- Outstream health.
- Instream health.
- Insightgen health.
- Analyzers health.
- Recording library.
- Current recording manifest.
- Current frame range endpoint.
- Current segmentation endpoint.
- Current active panel artifact endpoint.
- Live/dashboard WebSocket, if live mode is active.
- Worker status and queue depths.

## Data Contracts For The Rewrite

### Recording Manifest

The dashboard needs this shape, regardless of whether it comes from current JSON
or a future proto:

```ts
type RecordingManifest = {
  recordingId: string
  frameCount: number
  fps: number
  firstTimestampNs: number
  lastTimestampNs: number
  durationNs: number
  deviceIds: string[]
  sourceSizeBytes: number
  analyses: AnalysisAvailability[]
}
```

### Frame Metadata

Every decoded frame should carry:

```ts
type FrameMetadata = {
  frameIndex: number
  frameNumber: number
  timestampNs: number
  relativeTimestampNs: number
  deviceId: string
  hasRgb: boolean
  hasDepth: boolean
  hasPose: boolean
  hasImu: boolean
  hasGps: boolean
  hasGeometry: boolean
  rgbWidth?: number
  rgbHeight?: number
}
```

### Display Bundle

The runtime should treat each display update as a bundle:

```ts
type DisplayBundle = {
  frame: DecodedFrameAsset
  annotation?: DecodedAnnotationAsset
  motioncap?: MotioncapOverlayAsset
  pongtown?: PongtownOverlayAsset
  localization?: LocalizationOverlayAsset
}
```

Panels can render partial bundles. Missing optional analysis should not block RGB
display.

## Implementation Plan

### Phase 1: Measurement And API Client

- Add typed Streamlog client functions for every endpoint used by the rewrite.
- Add endpoint health checks matching the target status model.
- Add simple metrics:
  - endpoint latency
  - bytes fetched
  - frames decoded per second
  - main-thread long tasks
  - worker queue depth
  - cache hit/miss rates
- Add deterministic integration fixtures for representative recordings.

### Phase 2: Stateless File Playback

- Introduce `RecordingRuntime`, `PlaybackClock`, `FrameScheduler`, and
  `CacheManager`.
- Use HTTP outstream frame range endpoints instead of WebSocket seek for file
  playback.
- Support range prefetch and single-frame committed seek.
- Add request IDs, generation IDs, and cancellation.
- Keep current UI panels mostly unchanged through an adapter if necessary.

### Phase 3: Frame Worker

- Move frame protobuf decode to worker.
- Return `ImageBitmap` for RGB.
- Defer depth decode until sensors tab needs it.
- Replace Blob URLs in playback path.
- Add frame cache memory budget.

### Phase 4: Annotation And Analysis Workers

- Move segmentation mask decode/composite to annotation worker.
- Move motioncap parse and track primitive generation to motioncap worker.
- Move Pongtown parse and overlay primitive generation to Pongtown worker.
- Remove duplicate WebSocket and full artifact paths where a panel already has
  indexed data.

### Phase 5: Sensor And Localization Workers

- Move sensor indexing, chart decimation, trajectory scaling, SLAM map alignment,
  road projection, and SIFT subsampling into workers.
- Make sensors/localization lazy by tab.
- Add worker crash recovery and visible degraded state.

### Phase 6: Remove Legacy Coupling

- Stop using `/api/playback/start` for file recordings.
- Stop using dashboard WebSocket for file seek.
- Keep dashboard WebSocket only for live mode and optional events.
- Remove or isolate legacy fallbacks behind a compatibility layer.

## Acceptance Criteria

Playback:

- Warm-cache seek displays a frame in under 16 ms.
- Cold seek to an uncached nearby frame displays RGB in under 250 ms on a normal
  local dev machine.
- Continuous 30 fps playback does not issue one network request per frame.
- Scrubbing can generate many pointer updates without stale frame flashes.
- Main thread has no repeated long tasks over 50 ms during steady playback.

Workers:

- Frame decode, mask decode, overlay primitive generation, and heavy analysis
  indexing happen off the main thread.
- Every worker response is generation-checked.
- Worker crashes surface as panel-level degraded states, not full dashboard
  failure.

Endpoints:

- File playback works with only outstream HTTP endpoints healthy.
- Live mode clearly reports WebSocket status.
- Health popup identifies which endpoint class is broken.

Panels:

- First RGB frame is not blocked by motioncap, Pongtown, sensors, trajectory, or
  IDOSLAM loading.
- Opening a heavy panel shows panel-specific loading and does not stall playback.
- Missing artifacts show explicit empty states.

Memory:

- Decoded frame and bitmap caches have bounded memory.
- Image resources are disposed on eviction and recording switch.
- Large parsed artifacts are owned by workers or compact panel stores.

## Open Questions

- Should file playback autoplay after selecting a recording, or should it load
  paused on the first frame?
- Should segmentation default to exact, floor, or nearest matching?
- Should Streamlog add a manifest endpoint under outstream, or should the
  dashboard continue using the insightgen recording detail endpoint for
  manifests?
- Should Streamlog expose compact analysis indexes for motioncap and Pongtown so
  the dashboard can avoid downloading full artifacts on tab open?
- What memory budget should be enforced for decoded RGB/depth/analysis bitmaps?
- Should we keep the top Model Musings panel in the first viewport, or lazy-load
  it behind the first RGB frame?

## Recommended Final Shape

The clean dashboard should feel like a local video editor backed by Streamlog:

- Selecting a recording creates a stateless runtime for that recording.
- The first frame appears quickly.
- Playback uses a clock, prefetch ranges, and worker decode.
- Seeking is cancellable and generation-safe.
- Panels subscribe to current frame identity and request their own derived data
  through worker-backed stores.
- Optional analysis data never blocks RGB playback.
- Live mode is a separate WebSocket subscription mode.
- Endpoint health reports exactly which plane or worker is degraded.
