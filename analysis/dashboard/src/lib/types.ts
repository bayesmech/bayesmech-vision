export type LoadState = 'idle' | 'loading' | 'ready' | 'empty' | 'error';
export type PlaybackMode = 'file' | 'live';
export type PlaybackStatus = 'idle' | 'ready' | 'playing' | 'paused' | 'buffering' | 'seeking' | 'stalled' | 'error';
export type PanelId = 'segmentation' | 'motioncap' | 'sport' | 'sensors' | 'localization';
export type Priority = 'interactive' | 'playback' | 'prefetch' | 'background';

export type ProgressState = {
  visible: boolean;
  label: string;
  loaded: number;
  total: number;
  detail?: string;
};

export type RecordingRow = {
  name: string;
  title?: string;
  size_mb?: number;
  recorded_at?: number;
  has_segmentation?: boolean;
  has_idoslam?: boolean;
  has_motioncap?: boolean;
  has_pongtown?: boolean;
  available_analyses?: string[];
  analysis_url?: string;
};

export type AnalysisArtifact = {
  name: string;
  title: string;
  available: boolean;
  kind: string;
  encoding: string;
  media_type?: string;
  is_directory?: boolean;
  downloadable?: boolean;
  sliceable?: boolean;
  proto_message_type?: string;
  relative_path?: string;
  size_bytes?: number;
  download_url?: string;
  records_url?: string;
};

export type AnalysisAvailability = {
  name: string;
  title: string;
  available: boolean;
  artifacts: AnalysisArtifact[];
  views?: Array<{ name: string; title: string; url: string; media_type: string }>;
};

export type RecordingManifest = {
  recordingId: string;
  frameCount: number;
  fps: number;
  firstTimestampNs: number;
  lastTimestampNs: number;
  durationNs: number;
  deviceIds: string[];
  sourceSizeBytes: number;
  analyses: AnalysisAvailability[];
  status?: string;
};

export type RawRecordingManifest = {
  recording_id?: string;
  source_size_bytes?: number;
  status?: string;
  frame_count?: number;
  first_timestamp_ns?: number;
  last_timestamp_ns?: number;
  duration_ns?: number;
  estimated_fps?: number;
  device_ids?: string[];
};

export type FrameResponseMeta = {
  recording_id?: string;
  frame_index: number;
  frame_number: number;
  timestamp_ns: number;
  relative_timestamp_ns: number;
  selector_match_delta_ns: number;
  payload_media_type?: string;
  byte_length?: number;
};

export type FrameMetadata = {
  frameIndex: number;
  frameNumber: number;
  timestampNs: number;
  relativeTimestampNs: number;
  deviceId: string;
  hasRgb: boolean;
  hasDepth: boolean;
  hasPose: boolean;
  hasImu: boolean;
  hasGps: boolean;
  hasGeometry: boolean;
  rgbWidth?: number;
  rgbHeight?: number;
  depthWidth?: number;
  depthHeight?: number;
};

export type Vector3Like = { x: number; y: number; z: number };
export type CameraIntrinsics = {
  fx: number;
  fy: number;
  cx: number;
  cy: number;
  imageWidth: number;
  imageHeight: number;
  depthWidth?: number;
  depthHeight?: number;
};

export type DecodedFrameAsset = {
  frameIndex: number;
  frameNumber: number;
  timestampNs: number;
  relativeTimestampNs: number;
  metadata: FrameMetadata;
  rgbBitmap?: ImageBitmap;
  depthBitmap?: ImageBitmap;
  rgbWidth?: number;
  rgbHeight?: number;
  cameraIntrinsics?: CameraIntrinsics;
  pose?: { position?: Vector3Like; rotation?: { x: number; y: number; z: number; w: number } };
  imu?: {
    angularVelocity?: Vector3Like;
    linearAcceleration?: Vector3Like;
    gravity?: Vector3Like;
    magneticField?: Vector3Like;
  };
  gps?: {
    latitude: number;
    longitude: number;
    altitude: number;
    accuracy: number;
    bearing: number;
    speed: number;
    timestampMs: number;
  };
  geometry?: {
    pointCloud: Array<{ x: number; y: number; z: number; confidence: number }>;
    planes: Array<{
      type: number;
      extentX: number;
      extentZ: number;
      polygon: Vector3Like[];
      center?: { position?: Vector3Like };
    }>;
  };
};

export type SegmentationLegendItem = {
  objectId: number;
  label: string;
  confidence: number;
  pixelCount: number;
  color: string;
};

export type SegmentationAsset = {
  frameNumber: number;
  timestampNs: number;
  matchMode: 'exact' | 'floor' | 'nearest';
  bitmap?: ImageBitmap;
  width?: number;
  height?: number;
  legend: SegmentationLegendItem[];
  masks?: Array<{
    objectId: number;
    label: string;
    confidence: number;
    pixelCount: number;
    width: number;
    height: number;
    values: Uint8Array;
  }>;
};

export type MotionTrackPoint = {
  frameIdx: number;
  cx: number;
  cy: number;
  area: number;
  interpolated: boolean;
};

export type MotionTrack = {
  trackId: number;
  label: string;
  detectedFrames: number;
  totalPositions: number;
  presenceFraction: number;
  color: string;
  positions: MotionTrackPoint[];
};

export type MotionOverlayAsset = {
  frameNumber: number;
  frameIndex: number;
  heatmapBitmap?: ImageBitmap;
  heatmapWidth?: number;
  heatmapHeight?: number;
  raftSegments: MotionTrack[];
  segmentationSegments: MotionTrack[];
};

export type PongOverlayMode = 'hull' | 'pnp' | 'global';

export type PongOverlayAsset = {
  frameNumber: number;
  frameIndex: number;
  sportMode: 'pingpong' | 'snooker' | 'unknown';
  tableQuad?: number[];
  midline?: number[];
  netQuad?: number[];
  ballPositions: Array<{
    u: number;
    v: number;
    radius: number;
    label: string;
    confidence: number;
    insideTable?: boolean;
  }>;
  score?: number;
  message?: string;
};

export type Pong3DState = {
  sportMode: 'pingpong' | 'snooker' | 'unknown';
  tableWidthMm: number;
  tableHeightMm: number;
  netHeightMm: number;
  bounces: Array<{
    frameIdx: number;
    xMm: number;
    yMm: number;
    zMm: number;
    insideTable: boolean;
    corrected?: boolean;
  }>;
  balls: Array<{
    frameIdx: number;
    label: string;
    color: string;
    xMm: number;
    yMm: number;
    zMm: number;
    insideTable: boolean;
  }>;
};

export type SensorFrame = {
  fn: number;
  ts: number;
  imu?: {
    linear_acceleration?: Vector3Like;
    angular_velocity?: Vector3Like;
    gravity?: Vector3Like;
    magnetic_field?: Vector3Like;
  };
  gps?: {
    latitude: number;
    longitude: number;
    altitude: number;
    accuracy: number;
    bearing: number;
    speed: number;
    timestamp_ms: number;
  };
};

export type TrajectoryPoint = { x: number; y: number };

export type SensorDataset = {
  frames: SensorFrame[];
  trajectory: TrajectoryPoint[];
};

export type SlamPose = {
  frameIndex: number;
  frameNumber: number;
  timestampNs: number;
  position?: Vector3Like;
  euler?: Vector3Like;
};

export type LocalizationAsset = {
  rawPoses: SlamPose[];
  refinedPoses: SlamPose[];
  groundPoints: Array<{ frameIndex: number; x: number; y: number; z: number; side: string }>;
  planeWidthSummary?: { pitchDeg: number; cameraHeightM: number };
  pairDebug: Array<{
    frameIndex: number;
    pairedFrameIndex: number;
    status: string;
    correspondences: Array<{
      sourceX: number;
      sourceY: number;
      targetX: number;
      targetY: number;
      onRoad: boolean;
      side: string;
    }>;
  }>;
  canonicalTrack: Array<{ frameIndex: number; x: number; y: number; progress: number; width: number }>;
};

export type ChatTurn = {
  role: string;
  text: string;
  timestampNs?: number;
  toolCalls?: Array<{ toolName: string; argumentsJson: string; result: string }>;
};

export type ModelMusingsData = {
  title?: string;
  summaryText?: string;
  parameters: Array<{ name: string; value: string; unit: string }>;
  turns: ChatTurn[];
  status: LoadState;
  error?: string;
};

export type EndpointCheck = {
  name: string;
  kind: string;
  url: string;
  status: 'ok' | 'failed' | 'idle';
  latencyMs?: number;
  detail?: string;
};

export type WorkerRequest<T = unknown> = {
  requestId: number;
  recordingId: string;
  generation: number;
  priority: Priority;
  type: string;
  payload: T;
};

export type WorkerResponse<T = unknown> = {
  requestId: number;
  recordingId: string;
  generation: number;
  type: string;
  ok: boolean;
  payload?: T;
  error?: string;
};

export type DisplayBundle = {
  frame?: DecodedFrameAsset;
  annotation?: SegmentationAsset;
  motioncap?: MotionOverlayAsset;
  pongtown?: PongOverlayAsset;
  localization?: LocalizationAsset;
};
