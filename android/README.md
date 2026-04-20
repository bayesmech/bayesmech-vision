# Android App Structure

This directory contains the Android client for BayesMech Vision.

The app is organized around a small number of responsibilities:

- `MainActivity` owns app startup, ARCore session setup, renderer startup, and top-level tab navigation.
- `DatagrabRenderer` owns the live ARCore render loop and coordinates capture, visualization, and streaming.
- `AppViewModel` holds shared UI state such as server settings, recording state, coverage stats, and library data.

## Package Layout

### `app/src/main/java/com/bayesmech/vision/`

- `MainActivity.kt`
  Top-level activity shell. Sets up ARCore, starts streaming, and swaps between the camera, library, settings, and analysis screens.

- `DatagrabRenderer.kt`
  ARCore render-loop entry point. Renders the camera background, point clouds, and planes. When tracking is active, it samples ARCore frame data and hands it to the capture pipeline.

- `AppViewModel.kt`
  Shared state for fragments and activity-level settings.

- `LoginActivity.kt`
  Google sign-in entry screen.

### `app/src/main/java/com/bayesmech/vision/ui/`

- `CameraFragment.kt`
  Camera controls, recording UI, microphone/transcription flow, and send action for text notes.

- `LibraryFragment.kt`
  Lists recordings available from the backend and navigates into analysis.

- `SettingsFragment.kt`
  Server connection status, capture toggles, saved local files, and user profile/sign-out.

- `AnalysisFragment.kt`
  Recording playback and insight/chat UI for processed recordings.

### `app/src/main/java/com/bayesmech/vision/capture/`

This package now holds the ARCore-to-protobuf capture pipeline instead of mixing that logic into the renderer.

- `ArCoreFrameSampler.kt`
  Acquires the current RGB image, depth image, and point cloud from an ARCore frame based on the current capture settings.

- `CapturedFrameData.kt`
  Small resource wrapper for the sampled frame payload. Owns cleanup of the bitmap, depth image, and point cloud.

- `ArCoreImageConverter.kt`
  Converts ARCore `YUV_420_888` camera images into JPEG-backed `Bitmap` objects. This is the only place that owns the low-level YUV/NV21 conversion path.

- `CameraDataExtractor.kt`
  Pure extraction/encoding helpers that convert sampled data into protobuf-friendly structures:
  pose, intrinsics, JPEG RGB frames, depth frames, and inferred geometry.

- `ARDataCapture.kt`
  Capture orchestration layer. Applies throttling/adaptive quality, builds `PerceiverDataFrame`, records frames locally, sends them to the server, and updates coverage stats.

### `app/src/main/java/com/bayesmech/vision/sensors/`

- `SensorDataCollector.kt`
  Registers Android motion sensors and GPS updates and exposes the latest values.

- `SensorSnapshot.kt`
  Immutable snapshot of the current sensor state. Converts raw sensor values into protobuf IMU data and provides a single place for sensor summaries.

### `app/src/main/java/com/bayesmech/vision/network/`

- `ARStreamClient.kt`
  WebSocket client for `/ar-stream`, including reconnect behavior and connection status reporting.

- `BandwidthMonitor.kt`
  Rolling bandwidth estimator used to choose a `QualityLevel`.

- `StreamConfig.kt`
  Runtime streaming configuration plus the adaptive `QualityLevel` presets.

### `app/src/main/java/com/bayesmech/vision/recording/`

- `RecordingManager.kt`
  Writes length-delimited `PerceiverDataFrame` protobufs to local storage and truncates incomplete trailing frames on stop.

### `app/src/main/java/com/bayesmech/vision/coverage/`

- `CoverageTracker.kt`
  Rolling capture coverage accounting over the last 10 seconds.

- `CoverageStats.kt`
  Value object consumed by the settings UI.

### `app/src/main/java/com/bayesmech/vision/audio/`

- `StreamlogTranscriptionClient.kt`
  Uploads recorded microphone audio to the streamlog server transcription endpoint.

### `app/src/main/java/com/bayesmech/vision/common/`

- `helpers/`
  ARCore/session/display helpers and timestamp utilities.

- `samplerender/`
  Low-level OpenGL rendering support borrowed from the ARCore sample renderer stack.

## Resource Layout

### `app/src/main/res/layout/`

Fragment and activity XML layouts for:

- camera
- library
- settings
- analysis
- reusable list/chat items

### `app/src/main/res/drawable/`

Icons and shape drawables for the app chrome, camera controls, status badges, and buttons.

### `app/src/main/res/values/`

Shared colors, strings, and styles.

## Shared Data Model

### `../proto/`

This repository shares protobuf schemas between Android, the streamlog server, and the dashboard.

- `perceiver.proto`
  Main live-capture wire format.

- `insightgen.proto`
  Analysis and chat payloads.

- other `*.proto`
  Additional shared schemas used by downstream analysis tools.

## Runtime Data Flow

The main Android capture path is:

1. `MainActivity` starts `DatagrabRenderer` and the WebSocket stream client.
2. `DatagrabRenderer` receives ARCore frames in `onDrawFrame`.
3. `ArCoreFrameSampler` acquires RGB/depth/point-cloud data from the frame.
4. `SensorDataCollector` provides a `SensorSnapshot`.
5. `ARDataCapture` assembles a `PerceiverDataFrame` using `CameraDataExtractor`.
6. The frame is optionally written locally by `RecordingManager`.
7. The frame is sent to streamlog by `ARStreamClient`.

## Design Notes

- ARCore acquisition is intentionally separate from protobuf assembly.
- Sensor sampling is intentionally separate from frame serialization.
- The renderer should own rendering and high-level coordination, not image conversion details.
- The capture package should own the messy ARCore extraction details so the rest of the app stays readable.
