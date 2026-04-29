// Re-export proto types for convenience
import { bayesmech } from '../proto/bundle'

export type PerceiverDataFrame = bayesmech.vision.PerceiverDataFrame
export type IPerceiverDataFrame = bayesmech.vision.IPerceiverDataFrame
export type SegmentationResponse = bayesmech.vision.SegmentationResponse
export type ISegmentationResponse = bayesmech.vision.ISegmentationResponse
export type IdoSlamResponse = bayesmech.vision.IdoSlamResponse
export type IIdoSlamResponse = bayesmech.vision.IIdoSlamResponse
export type PongtownResponse = bayesmech.vision.PongtownResponse
export type IPongtownResponse = bayesmech.vision.IPongtownResponse

// === Legacy interfaces used by existing components ===

export interface Vec3 {
  x: number
  y: number
  z: number
}

export interface Quaternion {
  x: number
  y: number
  z: number
  w: number
}

export interface CameraPose {
  position: Vec3
  rotation: Quaternion
}

export interface CameraIntrinsics {
  fx: number
  fy: number
  cx: number
  cy: number
  image_width: number
  image_height: number
  depth_width: number
  depth_height: number
}

export interface ImuData {
  angular_velocity?: Vec3
  linear_acceleration?: Vec3
  gravity?: Vec3
  magnetic_field?: Vec3
}

export interface GpsLocation {
  latitude: number
  longitude: number
  altitude: number
  accuracy: number
  bearing: number
  speed: number
  timestamp_ms: number
}

export interface TrackedPoint {
  x: number
  y: number
  z: number
  confidence: number
}

export interface InferredPlane {
  /** 0=unknown, 1=horiz_upward, 2=horiz_downward, 3=vertical */
  type: number
  /** Polygon boundary vertices in plane-local space (x, 0, z). Apply center_pose to get world coords. */
  polygon: Vec3[]
  center_pose?: CameraPose
  extent_x: number
  extent_z: number
}

export interface InferredGeometry {
  plane_count: number
  point_cloud_count: number
  point_cloud: TrackedPoint[]
  planes: InferredPlane[]
}

// === Chart data ===

export interface ChartPoint {
  x: number
  y: number
}

// === Trajectory ===

export interface TrajectoryPoint {
  x: number
  y: number
}

// === Connection ===

export type ConnectionStatus = 'Connected' | 'Disconnected' | 'Connecting'

// === API responses ===

export interface StreamStats {
  source: string
  device_id: string | null
  frame_count: number
  buffered_frames: number
  fps: number
  is_replaying: boolean
  first_timestamp_ns: number
  last_timestamp_ns: number
  intrinsics: CameraIntrinsics | null
}

export interface RecordingInfo {
  name: string
  title: string
  size_mb: number
  recorded_at: number
  has_segmentation: boolean
  has_idoslam?: boolean
  has_motioncap: boolean
  has_pongtown?: boolean
}

// === Decoded frame for UI consumption ===

export interface DecodedFrame {
  source: string
  device_id: string
  timestamp_ns: number
  device_timestamp_ns?: number
  frame_number: number
  rgb_width?: number
  rgb_height?: number
  depth_width?: number
  depth_height?: number
  rgbBlobUrl?: string
  depthBlobUrl?: string
  hasDepthData?: boolean   // true when depth_frame bytes are present in the proto
  camera_pose?: CameraPose
  camera_intrinsics?: CameraIntrinsics
  imu?: ImuData
  inferred_geometry?: InferredGeometry
  gps?: GpsLocation
}

// === Signal coverage over a rolling window of frames ===

export interface CoverageStats {
  windowSize: number        // number of frames in the rolling window
  depth: number             // 0-100%
  pose: number
  linearAccel: number
  angularVelocity: number
  gravity: number
  magneticField: number
  intrinsicsCount: number   // cumulative frames that carried intrinsics
  geometry: number
  gps: number
}

export interface SegmentationLegendEntry {
  objectId: number
  label: string
  color: [number, number, number]
}

export interface DecodedMask {
  objectId: number
  label: string
  width: number
  height: number
  mask: Uint8Array
}

export interface DecodedAnnotation {
  frameNumber: number
  blobUrl: string
  legend: SegmentationLegendEntry[]
  masks: DecodedMask[]
}

// === Precomputed sensor data for file-mode playback ===

export interface SensorFrameData {
  fn: number          // frame_number
  ts: number          // timestamp_ns (as number — large ints may lose precision but acceptable for display)
  imu?: {
    linear_acceleration?: Vec3
    angular_velocity?: Vec3
    gravity?: Vec3
    magnetic_field?: Vec3
  }
  gps?: GpsLocation
}

export interface MotioncapTrackPosition {
  frame_idx: number
  cx: number
  cy: number
  area?: number
  interpolated?: boolean
}

export interface MotioncapTrackLegendItem {
  track_id: number
  color: [number, number, number]
  detected_frames: number
  total_positions: number
  presence_fraction: number
  positions: MotioncapTrackPosition[]
}

export interface MotioncapFrameRecord {
  heatmapIndex: number
  frameNumber: number
  timestampNs: number
  heatmapData: Uint8Array
}

export interface MotioncapData {
  frames: MotioncapFrameRecord[]
  byFrameNumber: Map<number, MotioncapFrameRecord>
  byHeatmapIndex: Map<number, MotioncapFrameRecord>
  tracks: MotioncapTrackLegendItem[]
}

export interface PongtownFrameRecord {
  frameIndex: number
  frameNumber: number
  timestampNs: number
  record: bayesmech.vision.PongtownResponse
}

export interface PongtownData {
  frames: PongtownFrameRecord[]
  byFrameNumber: Map<number, PongtownFrameRecord>
  byFrameIndex: Map<number, PongtownFrameRecord>
  summary?: bayesmech.vision.PongtownResponse
}
