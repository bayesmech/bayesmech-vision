# Streamlog Clean Implementation Specification

This document defines Streamlog as a fresh, implementation-agnostic system. It
specifies product responsibilities, public endpoint planes, data contracts,
operational behavior, logging, and test expectations. It intentionally avoids
prescribing a programming language, framework, package layout, or module shape.

## Scope

Streamlog is a recording-oriented service for `.vis.pb` data, analysis
artifacts, uploads, transcription, recording library metadata, Insightgen chat,
and analyzer execution.

Primary responsibilities:

- Serve `.vis.pb` recordings and analysis artifacts by stable recording ID.
- Support random access by frame index, source frame number, absolute
  timestamp, and recording-relative timestamp.
- Support multi-video dashboards by allowing independent timestamp-based
  requests against separate recordings.
- Accept recording uploads/syncs and validate them before publication.
- Accept live ingest connections that append to a recording in real time.
- Accept voice/audio transcription requests through a separate input endpoint.
- Serve Insightgen library data, summaries, thumbnails, media, and chat.
- Start and monitor long-running analyzer runs.
- Provide plane-specific logging, health, metrics, and operational isolation.

Out of scope:

- Streamlog does not composite or synchronize multiple videos internally.
- Streamlog does not own the dashboard playback clock.
- Streamlog does not provide a multi-source batch API for video or
  segmentation lookup.
- Streamlog does not run live (streaming) analyzers. Analyzers operate on
  recordings, including recordings that are still being written.
- Auth, multi-tenant isolation, and other production-grade concerns are out of
  scope. This is a demo system on a trusted network.

## Public Planes

Streamlog has four public planes. A plane is an endpoint group, logging
dimension, and operational workload class. Planes do not have to be separate
deployable services.

- `Outstream`: outbound recording data. Serves source `.vis.pb` files,
  individual frames, frame ranges, rendered media layers, annotations, sidecar
  records, full analysis files, sliced analysis records, trajectories, and
  sensors. This is the latency-sensitive playback path.
- `Instream`: inbound data. Handles recording upload/sync, import
  finalization, validation, index scheduling, live ingest, and voice/audio
  transcription. Upload, live ingest, and transcription have separate limits
  and concurrency budgets.
- `Insightgen`: recording library, metadata, thumbnails, summaries,
  generated-insight media, and follow-up chat. Insightgen stays inside the
  Streamlog runtime and may bind a separate port.
- `Analyzers`: long-running pipeline execution. Starts, monitors, cancels,
  retries, and logs analyzer runs such as segmentation, motion capture, SLAM,
  reconstruction, sport-understanding pipelines, and Genspark regeneration.

Plane isolation:

- All planes run inside one Streamlog system. A plane may be assigned its own
  listener (port) but is not a separate process.
- `Outstream` reads must not wait behind `Instream` uploads, live ingest,
  transcription, `Insightgen` provider work, or `Analyzers` runs.

Endpoint namespace:

- All public routes use the `/streamlog` path root.
- Plane names are the first path segment under `/streamlog`.
- Ports are deployment details and are not encoded into endpoint paths.
- A listener may expose one plane, multiple planes, or all planes, but each
  listener keeps the same `/streamlog` path namespace.

Named listeners:

- Every open port is represented as a named listener.
- Listener names use lowercase kebab-case and start with `streamlog`.
- The default listener is named `streamlog`.
- Optional plane-specific listeners are named with the plane suffix:
  `streamlog-outstream`, `streamlog-instream`, `streamlog-insightgen`, and
  `streamlog-analyzers`.
- Each listener declares:
  - listener name
  - bind host and bind port
  - public base URL
  - served planes
  - `/streamlog` base path
  - health status
- Global health returns the listener registry so clients and operators can
  discover the correct named base URL without hard-coding ports.

Example listener registry shape:

```json
{
  "listeners": [
    {
      "name": "streamlog",
      "base_url": "https://vision.example.com/streamlog",
      "planes": ["Outstream", "Instream", "Analyzers"]
    },
    {
      "name": "streamlog-insightgen",
      "base_url": "https://insightgen.vision.example.com/streamlog",
      "planes": ["Insightgen"]
    }
  ]
}
```

## Domain Model

Recording ID:

- Stable logical ID for one recording.
- Must never be interpreted as a filesystem path.
- Must be normalized and validated before storage or lookup.

Recording:

- Contains one source `.vis.pb` stream.
- May contain analysis artifacts, rendered media, chat history, generated
  summaries, analyzer outputs, and indexes.
- Has a manifest describing source metadata, available artifacts, generated
  metadata, and operational status.

Frame identity:

- `frame_index`: zero-based position in the `.vis.pb` file.
- `frame_number`: source `PerceiverFrameIdentifier.frame_number`.
- `timestamp_ns`: source `PerceiverFrameIdentifier.timestamp_ns`.
- `relative_timestamp_ns`: `timestamp_ns - first_timestamp_ns` for that
  recording unless an explicit sync offset is applied.

Timestamp model:

- `timestamp_ns` is the canonical capture timestamp inside each
  `PerceiverDataFrame`.
- Timestamps across `.vis.pb` files are assumed to share a wall clock.
  Streamlog does not correct, align, or estimate clock skew.
- Multi-video dashboards request each recording by the same absolute
  `timestamp_ns`. Lineup, drift detection, and any leading-frame drop until
  recordings agree are the dashboard's responsibility, using the
  `selector_match_delta_ns` returned on every frame response.
- Recording-relative time remains useful for timelines, trimming, diagnostics,
  and local playback.

Frame selector:

- A request must use exactly one primary selector:
  - `frame_index`
  - `frame_number`
  - `timestamp_ns`
  - `relative_timestamp_ns`
- Timestamp selectors support `match`:
  - `exact`: equal timestamp only.
  - `floor`: greatest timestamp less than or equal to target.
  - `ceil`: smallest timestamp greater than or equal to target.
  - `nearest`: smallest absolute timestamp delta.
- Timestamp selectors support optional `tolerance_ns`.
- If no frame satisfies tolerance, single-frame requests return `404`; range
  requests report item-level errors where an envelope is used.

Range selector:

- Supports:
  - `start_frame_index`, `end_frame_index`
  - `start_frame_number`, `end_frame_number`
  - `start_timestamp_ns`, `end_timestamp_ns`
  - `start_relative_timestamp_ns`, `end_relative_timestamp_ns`
  - `limit`
  - optional `stride`
- Index ranges are half-open: `[start, end)`.
- Frame-number and timestamp ranges are inclusive unless the request explicitly
  states otherwise.

Resolved frame metadata:

- `recording_id`
- `frame_index`
- `frame_number`
- `timestamp_ns`
- `relative_timestamp_ns`
- `selector_match_delta_ns` for timestamp requests
- payload media type and byte length

## Storage And File Formats

Canonical recording layout:

- One folder per recording ID under the configured recordings root.
- One source `.vis.pb` file per recording.
- Analysis artifacts, generated summaries, chat history, rendered media,
  analyzer outputs, and index sidecars are stored under the same recording
  boundary or a configured artifact root addressable by recording ID.

Length-delimited protobuf streams:

- The file format is:
  `[uint32 big-endian length][serialized protobuf message bytes]...`
- Readers must support streaming iteration without loading the full file.
- Readers should expose byte offset, byte length, and parsed message metadata.
- Corrupt or suspicious record prefixes should produce structured errors with
  enough context to diagnose the damaged record.

Recording index:

- One index entry per source frame.
- Required fields:
  - `frame_index`
  - `frame_number`
  - `timestamp_ns`
  - byte offset and byte length
  - device ID when present
  - signal-presence flags for RGB, depth, pose, IMU, GPS, geometry, and
    intrinsics
- Lookup requirements:
  - by `frame_index`
  - by `frame_number`
  - by `timestamp_ns` using exact, floor, ceil, and nearest semantics
  - by `relative_timestamp_ns` using the same timestamp match semantics
- Summary metadata:
  - first and last timestamp
  - duration
  - estimated fps
  - frame count
  - device IDs
  - first known dimensions and intrinsics

Index invalidation and growth:

- For sealed recordings, an index is valid only for the source path, size, and
  modification timestamp it was built from. If any of those change, the index
  must rebuild before reads resume.
- For recordings still being written by live ingest, the index is append-only:
  each new frame's offset/length/identity is appended as the frame is
  persisted, and Outstream reads see the current tail. No special "live mode"
  read path exists; the recording is read like any other file, with its frame
  count growing over time.
- Corrupt recordings must be represented in manifests with clear status and
  error detail.

## Outstream Requirements

Outstream serves all outbound recording and analysis data.

Source file serving:

- Download the full source `.vis.pb` for a recording.
- Return media type and content length.
- Support range requests where the serving environment supports them.
- Do not expose absolute filesystem paths.

Frame serving:

- Get one frame by `frame_index`.
- Resolve one frame by `frame_number`, `timestamp_ns`, or
  `relative_timestamp_ns`.
- Return raw `PerceiverDataFrame` bytes for single-frame reads.
- Include resolved frame metadata in headers or an equivalent metadata channel.
- Support single-recording frame ranges for prefetch and scans.
- Serving a frame should require selector lookup plus direct byte read, not a
  full recording parse.

Frame response contract:

- Body: serialized `PerceiverDataFrame`.
- Media type: `application/x-protobuf`.
- Metadata fields:
  - `recording_id`
  - `frame_index`
  - `frame_number`
  - `timestamp_ns`
  - `relative_timestamp_ns`
  - `selector_match_delta_ns`

Range response contract:

- Body: length-delimited `PerceiverDataFrame` records or an explicit envelope
  containing records plus metadata.
- Must include per-frame resolved metadata.
- Must enforce max range size and response byte limits.

Annotations and sidecars:

- Serve annotation/sidecar records by selector and by range.
- Supported kinds include segmentation and sport-understanding records.
- Annotation lookup must use the same selector semantics as frame lookup.
- Segmentation lookup must support exact and floor matching by source frame
  number.
- Timestamp lookup must support exact, floor, ceil, and nearest matching.
- Sidecar failures must be reported separately from frame failures unless the
  request marks the sidecar as required.
- Segmentation mask payloads preserve:
  `[height uint32 LE][width uint32 LE][zlib(np.packbits(mask))]`.

Trajectory and sensors:

- Serve compact trajectory data for a recording.
- Serve compact IMU/GPS sensor streams for a recording.
- Large recordings may use selector/range pagination instead of one large
  response.

Analysis artifacts:

- Maintain a typed artifact registry with:
  - analysis name
  - artifact name
  - path suffix
  - media type
  - encoding
  - protobuf message type when applicable
  - sliceability
  - summary-record detection when applicable
- Serve an analysis/artifact index per recording.
- Serve analysis detail.
- Download full artifact files.
- Slice length-delimited analysis records by timestamp, frame number, and
  limit.
- Support summary inclusion for artifacts with summary records.
- Serve derived views such as motion-capture track legends and heatmaps.

Media layers:

- `raw`: source RGB frame.
- `understanding`: derived overlay using analysis artifacts.
- Render one frame by selector.
- Render a media-layer frame range.
- Rendering must align analysis records by timestamp or frame number.
- Render requests should accept width/quality controls subject to configured
  bounds.

Multi-video support:

- Outstream serves one recording per request.
- A dashboard requests two or more recordings independently using the same
  absolute `timestamp_ns`.
- Every response includes `selector_match_delta_ns` so the dashboard can
  detect drift, missing frames, and tolerance misses, and drop leading frames
  on individual streams until they line up.
- No multi-source video or segmentation batch API is required.

## Instream Requirements

Instream handles inbound recording files, live ingest from devices, and
transcription inputs.

Recording import:

- Start an import session.
- Accept complete-file uploads.
- Accept resumable chunk uploads.
- Validate uploaded `.vis.pb` streams before publication.
- Reject unsupported file types and invalid content.
- Finalize imports atomically so partial uploads are never visible as complete
  recordings.
- Schedule or build a recording index after validation.
- Return import status and the resulting recording manifest.
- Support import cancellation and cleanup of partial data.
- Enforce upload size, chunk size, and timeout limits.

Upload/sync:

- File sync to the server is in scope.
- Upload/sync must be idempotent where possible.
- The manifest should expose checksum/hash data when available.
- Re-uploading an identical file should not create duplicate recordings unless
  the caller explicitly requests a new recording ID.

Live ingest:

- Accept a streaming connection from any device that wants to push a recording
  in real time. Concurrency is bounded only by host capacity; there is no
  per-device pairing, claim, or auth.
- The connection carries length-delimited `PerceiverDataFrame` messages in the
  same wire format as a `.vis.pb` file.
- The server appends frames to the recording's `.vis.pb` file in arrival order
  and updates the index incrementally. If the server falls behind, it stays
  behind and continues in order; frames are not reordered or dropped on the
  server side.
- Two devices must not push to the same recording ID; behavior in that case is
  undefined.
- The recording is a normal file throughout. There is no separate "live"
  read API: Outstream serves it like any other recording, and the visible
  frame count grows as frames are persisted.
- When the connection ends (clean or otherwise), the recording is left
  in place as-is. No explicit finalize step is required for the file to be
  readable.
- Live ingest is a legacy convenience and is not required for any other
  Streamlog feature; uploads via `/streamlog/instream/imports` remain the
  primary path.

Transcription:

- Voice/audio transcription is in scope.
- Transcription is a separate Instream endpoint group.
- Accept audio uploads with explicit content-type and size validation.
- Use isolated provider credentials.
- Do not log transcript text by default.
- Support synchronous completion for small requests and asynchronous status for
  longer requests.
- Return transcript text, language/provider metadata when available, and
  structured provider errors.

## Insightgen Requirements

Insightgen provides recording library data, summaries, thumbnails, generated
media, and follow-up chat.

Recording library:

- List recordings with title, tags, thumbnail availability, analysis
  availability, frame count, duration, timestamp range, and preview metadata.
- Return a detail manifest for one recording.
- Cache manifests and invalidate them when source or artifact metadata changes.
- Listing recordings must avoid full file scans on warm cache.

Thumbnails:

- Serve a thumbnail for a recording.
- Thumbnail selection should be deterministic.
- Thumbnail generation should use configured dimensions and quality bounds.
- Thumbnail generation should be cached by source/artifact metadata.

Summaries:

- Serve generated Insight/Genspark summary data for a recording.
- Expose missing, generating, failed, and ready states distinctly.
- Regeneration is requested through `Analyzers`, not through a blocking
  Insightgen request.

Insight video:

- Serve generated-insight media for a recording and layer.
- Large media responses should be paginated or selector-based.
- Highlight windows should be represented as normalized artifacts so clients do
  not depend on provider-specific tool-call text.

Chat:

- Provide chat history or delta for a recording.
- Accept a follow-up chat message for a recording.
- Persist chat turns as recording artifacts.
- Reconstruct chat context from generated summary artifacts.
- Invalidate chat sessions when the underlying generated summary changes.
- Keep model/provider credentials isolated from Outstream and Instream paths.
- Do not log raw prompts, raw chat messages, API keys, or provider payloads by
  default.

## Analyzers Requirements

Analyzers manages long-running pipeline execution.

Pipeline registry:

- List runnable pipelines.
- For each pipeline, expose:
  - pipeline name
  - accepted inputs
  - produced artifacts
  - resource labels such as CPU, GPU, memory, disk, external provider
  - configurable parameters
  - whether debug video output is supported

Analyzer runs:

- Start a run for a recording.
- Assign a stable run ID.
- Track status: queued, running, succeeded, failed, cancelled.
- Track start/end timestamps.
- Track recording ID, pipeline name, parameters, and resource labels.
- Expose progress and produced artifacts.
- Expose run logs.
- Support cancellation.
- Support retry.
- Avoid blocking Outstream, Instream, and Insightgen request handling.

Genspark regeneration:

- Model-backed summary regeneration is represented as an analyzer run.
- Successful regeneration publishes a new summary artifact and invalidates
  dependent Insightgen chat context.

## Public Endpoint List

Paths below are absolute paths on any listener that serves the relevant plane.
A listener exposes only the planes assigned to it. The default listener named
`streamlog` should expose all enabled planes unless deployment policy assigns a
plane to a separate named listener. Listener `base_url` values include the
scheme, host, optional port, and `/streamlog` base path; clients can combine a
listener `base_url` with plane-local suffixes such as
`/outstream/recordings/{id}/frames:resolve`.

Global:

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/streamlog/health` | Overall system health, named listeners, bound ports, enabled planes |

`Outstream`:

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/streamlog/outstream/health` | Outstream health and cache/index status |
| `GET` | `/streamlog/outstream/recordings/{id}/source` | Download the full source `.vis.pb` file |
| `GET` | `/streamlog/outstream/recordings/{id}/frames/{frame_index}` | Get one frame by zero-based index |
| `GET` | `/streamlog/outstream/recordings/{id}/frames:resolve` | Get one frame by frame number, timestamp, or relative timestamp |
| `GET` | `/streamlog/outstream/recordings/{id}/frames` | Get a frame range |
| `GET` | `/streamlog/outstream/recordings/{id}/layers/{layer}/frames:resolve` | Render one frame for a media layer |
| `GET` | `/streamlog/outstream/recordings/{id}/layers/{layer}/frames` | Render a media-layer frame range |
| `GET` | `/streamlog/outstream/recordings/{id}/annotations/{kind}:resolve` | Get one annotation or sidecar record by frame selector |
| `GET` | `/streamlog/outstream/recordings/{id}/annotations/{kind}` | Get annotation or sidecar records by range |
| `GET` | `/streamlog/outstream/recordings/{id}/trajectory` | Compact trajectory stream |
| `GET` | `/streamlog/outstream/recordings/{id}/sensors` | Compact IMU/GPS stream |
| `GET` | `/streamlog/outstream/recordings/{id}/analyses` | Analysis/artifact index for a recording |
| `GET` | `/streamlog/outstream/recordings/{id}/analyses/{analysis}` | Analysis detail |
| `GET` | `/streamlog/outstream/recordings/{id}/analyses/{analysis}/artifacts/{artifact}` | Download a full analysis artifact |
| `GET` | `/streamlog/outstream/recordings/{id}/analyses/{analysis}/records` | Slice length-delimited analysis records |
| `GET` | `/streamlog/outstream/recordings/{id}/analyses/{analysis}/views/{view}` | Derived analysis view |

`Instream`:

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/streamlog/instream/health` | Instream health, upload limits, transcription status |
| `POST` | `/streamlog/instream/imports` | Start an upload/sync import session |
| `PUT` | `/streamlog/instream/imports/{import_id}/content` | Upload or replace import bytes |
| `PUT` | `/streamlog/instream/imports/{import_id}/chunks/{chunk_index}` | Upload one resumable chunk |
| `POST` | `/streamlog/instream/imports/{import_id}:complete` | Finalize upload, validate, and schedule indexing |
| `GET` | `/streamlog/instream/imports/{import_id}` | Inspect import status |
| `POST` | `/streamlog/instream/imports/{import_id}:cancel` | Cancel an import and clean partial data |
| `WS`   | `/streamlog/instream/live/{id}` | Live ingest: stream length-delimited `PerceiverDataFrame`s into recording `{id}` |
| `POST` | `/streamlog/instream/transcriptions` | Upload voice/audio and request transcription |
| `GET` | `/streamlog/instream/transcriptions/{transcription_id}` | Inspect transcription status/result |

`Insightgen`:

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/streamlog/insightgen/health` | Insightgen health, provider status, library/cache status |
| `GET` | `/streamlog/insightgen/recordings` | List recordings with metadata, thumbnails, and analysis availability |
| `GET` | `/streamlog/insightgen/recordings/{id}` | Recording manifest and Insightgen metadata |
| `GET` | `/streamlog/insightgen/recordings/{id}/thumbnail` | Recording thumbnail |
| `GET` | `/streamlog/insightgen/recordings/{id}/summary` | Generated Insight/Genspark summary |
| `GET` | `/streamlog/insightgen/recordings/{id}/video` | Generated-insight media for a layer |
| `GET` | `/streamlog/insightgen/recordings/{id}/chat` | Chat history or delta |
| `POST` | `/streamlog/insightgen/recordings/{id}/chat` | Send follow-up chat message |

`Analyzers`:

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/streamlog/analyzers/health` | Analyzer queue and worker health |
| `GET` | `/streamlog/analyzers/pipelines` | List runnable analyzer pipelines and resource requirements |
| `POST` | `/streamlog/analyzers/recordings/{id}/runs` | Start an analyzer run for a recording |
| `GET` | `/streamlog/analyzers/runs` | List analyzer runs, filterable by recording and pipeline |
| `GET` | `/streamlog/analyzers/runs/{run_id}` | Analyzer run status and produced artifacts |
| `GET` | `/streamlog/analyzers/runs/{run_id}/logs` | Analyzer run logs |
| `POST` | `/streamlog/analyzers/runs/{run_id}:cancel` | Cancel an analyzer run |
| `POST` | `/streamlog/analyzers/runs/{run_id}:retry` | Retry an analyzer run |

Outstream frame resolve examples:

```text
/streamlog/outstream/recordings/cam_a/frames:resolve?frame_number=120
/streamlog/outstream/recordings/cam_a/frames:resolve?timestamp_ns=1710000000000000000&match=nearest&tolerance_ns=20000000
/streamlog/outstream/recordings/cam_a/frames:resolve?relative_timestamp_ns=1500000000&match=floor
```

## Error Handling

All HTTP errors use a consistent structured response:

```json
{
  "error": {
    "code": "frame_not_found",
    "message": "No frame within tolerance",
    "details": {
      "recording_id": "cam_a",
      "timestamp_ns": 123,
      "tolerance_ns": 16666667
    }
  }
}
```

Required error categories:

- invalid request
- invalid selector
- recording not found
- frame not found
- artifact not found
- unsupported media type
- corrupt recording
- upload validation failed
- transcription failed
- provider unavailable
- analyzer run failed
- rate limited
- unauthorized

## Configuration

Required configuration:

- named listeners with bind host, bind port, public base URL, served planes,
  and base path
- default `streamlog` listener
- optional plane-specific listeners such as `streamlog-insightgen`
- recordings root
- artifact root when separate from recordings
- logs root, defaulting to `/logs`
- dashboard static root when static serving is enabled
- cache sizes
- index sidecar enablement
- max Outstream range size
- max source download size before streaming/range behavior is required
- max upload size
- max upload chunk size
- max transcription upload size
- media rendering defaults:
  - quality
  - max width
  - overlay alpha
  - motion tail length
- plane enablement
- provider credentials by plane
- concurrency and queue limits by plane

Configuration requirements:

- Runtime behavior is config-controlled.
- The sanitized effective configuration is logged at startup.
- Secrets, tokens, and provider credentials are never written to logs.
- Each launch records a config hash for correlation with logs and analyzer
  outputs.
- Listener names, bound ports, public base URLs, and served planes are logged at
  startup and returned by global health.

## Logging And Observability

All logs go under `/logs`.

Every log record includes:

- timestamp
- level
- run ID
- plane: `Outstream`, `Instream`, `Insightgen`, or `Analyzers`
- component or boundary
- operation
- request ID or analyzer run ID when applicable
- recording ID when applicable
- artifact name when applicable
- frame index, frame number, and timestamp when applicable
- duration
- byte counts for reads, writes, uploads, and responses
- status
- sanitized error type and message on failure

Plane logs:

- Operators must be able to inspect one plane without unrelated plane noise.
- Long-running analyzer runs have run-specific logs linked from the
  `Analyzers` plane.
- High-volume per-frame Outstream success logs are sampled or aggregated by
  default.

Plane-specific logging:

- `Outstream`: frame resolves, selector type, match delta, cache hits/misses,
  response bytes, slow reads, missing frames, full artifact downloads, analysis
  slices, rendered-layer requests, and sidecar misses.
- `Instream`: upload/sync lifecycle, bytes received, validation result, import
  finalization, index scheduling, rejected file types, checksums, partial
  cleanup, live ingest connect/disconnect, frames appended per stream,
  transcription lifecycle, provider latency, and failure category.
- `Insightgen`: library requests, manifest and thumbnail cache decisions,
  summary/video/chat lifecycle, provider latency, cache use, and artifact
  reads/writes.
- `Analyzers`: queue wait, start/finish/failure, worker identity, progress,
  produced artifacts, log locations, cancellation, retry, debug artifact paths,
  and resource usage summaries.

Debug controls:

- Debug-only command switches use a `--debug-` prefix.
- Debug video generation uses `--debug-render-video` where applicable.
- Debug outputs are logged under the relevant plane and stored under `/logs` or
  linked from `/logs`.
- Debug mode must not be required for normal operations.

Metrics:

- request counts by plane, route, status, and latency bucket
- Outstream frame request latency
- frame cache hit/miss counts
- index cache hit/miss counts
- artifact request counts and bytes served
- upload bytes received and validation duration
- transcription request duration and provider latency
- Insightgen provider latency and error rate
- analyzer queue wait, run duration, success/failure/cancel counts
- resource usage summaries for analyzer runs

Health:

- Global health reports enabled planes, bound ports, logs root, recordings
  root, named listener registry, public base URLs, and degraded dependencies.
- Plane health reports plane-specific queues, caches, provider status, limits,
  and recent failure state.

## Compute And Parallelism

`Outstream`:

- Raw frame reads should be low CPU and low latency once an index exists.
- Multi-video dashboards can make the plane I/O-heavy because each visible
  source issues independent frame, sidecar, and media-layer requests.
- Full artifact downloads can be bandwidth-heavy.
- Analysis record slicing can be parse-heavy without artifact indexes.
- Media-layer rendering can be CPU/GPU-heavy because it may decode, resize,
  overlay, and encode image data.
- Raw frame reads should have priority over full downloads, slicing, and
  rendering.

`Instream`:

- Upload/sync is network- and disk-I/O heavy.
- Live ingest is steady-state network- and disk-I/O heavy, with one append
  stream per connected device. If writes lag, ingest stays behind in arrival
  order rather than reordering or dropping.
- Validation and index building scan complete `.vis.pb` files; live recordings
  build their index incrementally as frames arrive.
- Transcription is long-running, externally dependent, and potentially
  memory/network-heavy for large audio.
- Upload, live ingest, validation/indexing, and transcription require separate
  internal budgets.

`Insightgen`:

- Cached library and summary reads are low compute.
- Cold manifest scans and thumbnail generation are I/O- and image-decode-heavy.
- Chat and summary operations may depend on external model providers.
- Generated media can be CPU-heavy if it packages or renders many frames.
- Long generation/regeneration work belongs in `Analyzers`; lightweight reads
  can stay request/response.

`Analyzers`:

- Highest and most variable compute plane.
- Runs may use CPU, GPU, memory, disk, external tools, and external providers.
- Resource labels and per-resource concurrency limits are required.
- Analyzer runs expose progress and resource usage without blocking other
  planes.

## Security

Auth, TLS, and tenant isolation are out of scope. Streamlog assumes a trusted
network. The following hygiene rules still apply:

- Treat recording IDs, artifact names, pipeline names, and import IDs as
  logical IDs, never paths. Reject path traversal.
- Enforce upload, transcription, and response size limits.
- Validate content types on uploads and transcription.
- Do not expose absolute local filesystem paths in public responses.
- Do not log raw audio, transcript text, prompts, chat messages, API keys,
  or provider payloads by default.

## Test Requirements

Unit tests:

- Length-delimited protobuf read/write.
- Corrupt record detection and structured error reporting.
- Recording ID normalization and path safety.
- Recording index build from fixture `.vis.pb`.
- Selector resolution:
  - exact frame index
  - exact frame number
  - exact timestamp
  - floor timestamp
  - ceil timestamp
  - nearest timestamp
  - tolerance miss
  - duplicate timestamps
  - non-monotonic timestamps
- Artifact registry metadata and lookup.
- Sliceable artifact filtering and summary inclusion.
- Annotation lookup by frame number and timestamp.
- Media-layer alignment by frame selector.

API tests:

- health endpoints expose enabled planes and dependency status.
- recording list uses cached manifests after warmup.
- source download returns the correct media type and size metadata.
- frame resolve returns the expected frame and metadata.
- range requests respect bounds, stride, limit, and response size limits.
- missing recording returns `404`.
- invalid selector returns `400`.
- timestamp tolerance miss returns `404`.
- analysis index reports expected artifacts.
- artifact download returns the correct media type.
- analysis record slicing respects frame/timestamp filters.
- upload finalization rejects corrupt `.vis.pb` files.
- transcription rejects invalid content types and oversized uploads.
- analyzer run lifecycle covers queued, running, succeeded, failed, cancelled,
  and retried states.

Integration tests:

- Import a small recording, build an index, list it in Insightgen, and fetch a
  frame through Outstream.
- Resolve the same absolute timestamp for two recordings and verify both
  responses include match deltas.
- Fetch a frame and segmentation sidecar for the same selector.
- Render raw and understanding layers for the same selector and verify metadata
  alignment.
- Start an analyzer run and verify produced artifacts become visible through
  Outstream and Insightgen manifests.

Performance tests:

- Cold index time for representative recordings.
- Warm frame lookup latency.
- Outstream behavior under two-video dashboard request rates.
- Full artifact download impact on frame seek latency.
- Media render latency and cache hit latency.
- Upload validation throughput.
- Analyzer queue behavior under resource contention.

## Open Questions

1. What are the target maximum `.vis.pb` size, frame size, and warm seek
   latency?
2. Which upload/sync protocols are required beyond multipart and resumable
   chunk upload?
3. Which analysis artifact names and media types are mandatory for the first
   implementation milestone?
4. What is the expected ceiling on concurrent live ingest connections, and
   what should happen when it is exceeded — refuse, queue, or degrade?
