import React, { createContext, useContext, useState, useEffect, useRef, useCallback } from 'react'
import type {
  ConnectionStatus, DecodedFrame, DecodedAnnotation, CoverageStats,
  TrajectoryPoint, CameraPose, CameraIntrinsics, ImuData, Vec3, SensorFrameData,
} from '../types'
import { dashboardWs } from '../services/websocket'
import { startPlayback, switchToLive as apiSwitchToLive } from '../services/api'
import { bytesToBlobUrl, compositeMasksToDataUrl, MASK_COLORS } from '../services/proto'
import { bayesmech } from '../proto/bundle'

// =====================================================================
// Proto decoding
// =====================================================================

const ImageFormat = bayesmech.vision.ImageFrame.ImageFormat

const DepthFormat = bayesmech.vision.DepthFrame.DepthFormat

class FrameDecoder {
  private cachedIntrinsics: CameraIntrinsics | null = null
  private depthLogCount = 0

  /** Try to recover width×height from pixel count by testing common aspect ratios. */
  private static inferDepthDimensions(numPixels: number): [number, number] | null {
    for (const [wr, hr] of [[16, 9], [4, 3], [1, 1]] as [number, number][]) {
      const h = Math.round(Math.sqrt(numPixels * hr / wr))
      if (h > 0 && numPixels % h === 0) {
        const w = numPixels / h
        if (w > 0) return [w, h]
      }
    }
    return null
  }

  private static vec3(v: bayesmech.vision.IVector3 | null | undefined): Vec3 | undefined {
    if (!v) return undefined
    return { x: v.x ?? 0, y: v.y ?? 0, z: v.z ?? 0 }
  }

  /**
   * Convert raw depth bytes to a grayscale data URL (closer = brighter).
   * Uses a regular HTMLCanvasElement so toDataURL() can be called synchronously.
   */
  private decodeDepthToDataUrl(
    data: Uint8Array,
    format: number,
    width: number,
    height: number,
  ): string | null {
    const bytesPerPixel = format === DepthFormat.FLOAT32_METERS ? 4 : 2
    if (data.length < width * height * bytesPerPixel) return null

    const canvas = document.createElement('canvas')
    canvas.width = width
    canvas.height = height
    const ctx = canvas.getContext('2d')
    if (!ctx) return null

    const imageData = ctx.createImageData(width, height)
    const px = imageData.data
    const view = new DataView(data.buffer, data.byteOffset, data.byteLength)
    const n = width * height
    let lo = Infinity, hi = 0

    if (format === DepthFormat.FLOAT32_METERS) {
      for (let i = 0; i < n; i++) {
        const d = view.getFloat32(i * 4, true)
        if (d > 0 && isFinite(d)) { if (d < lo) lo = d; if (d > hi) hi = d }
      }
      const range = hi - lo || 1
      for (let i = 0; i < n; i++) {
        const d = view.getFloat32(i * 4, true)
        const v = (d > 0 && isFinite(d)) ? Math.round((1 - (d - lo) / range) * 255) : 0
        px[i * 4] = v; px[i * 4 + 1] = v; px[i * 4 + 2] = v; px[i * 4 + 3] = 255
      }
    } else {
      // UINT16_MILLIMETERS, little-endian
      for (let i = 0; i < n; i++) {
        const d = view.getUint16(i * 2, true)
        if (d > 0) { if (d < lo) lo = d; if (d > hi) hi = d }
      }
      const range = hi - lo || 1
      for (let i = 0; i < n; i++) {
        const d = view.getUint16(i * 2, true)
        const v = d === 0 ? 0 : Math.round((1 - (d - lo) / range) * 255)
        px[i * 4] = v; px[i * 4 + 1] = v; px[i * 4 + 2] = v; px[i * 4 + 3] = 255
      }
    }

    ctx.putImageData(imageData, 0, 0)
    return canvas.toDataURL('image/png')
  }

  decodeFrame(proto: bayesmech.vision.PerceiverDataFrame, skipDepth = false): DecodedFrame {
    const id = proto.frameIdentifier
    const frame: DecodedFrame = {
      source: 'file',
      device_id: id?.deviceId ?? '',
      timestamp_ns: Number(id?.timestampNs ?? 0),
      frame_number: id?.frameNumber ?? 0,
    }

    // Intrinsics first — depth decoding needs depth_width / depth_height
    const intr = proto.cameraIntrinsics
    if (intr) {
      this.cachedIntrinsics = {
        fx: intr.fx ?? 0, fy: intr.fy ?? 0,
        cx: intr.cx ?? 0, cy: intr.cy ?? 0,
        image_width: intr.imageWidth ?? 0, image_height: intr.imageHeight ?? 0,
        depth_width: intr.depthWidth ?? 0, depth_height: intr.depthHeight ?? 0,
      }
    }
    if (this.cachedIntrinsics) {
      frame.camera_intrinsics = this.cachedIntrinsics
    }

    const rgb = proto.rgbFrame
    if (rgb?.data && rgb.data.length > 0) {
      const mime = rgb.format === ImageFormat.JPEG ? 'image/jpeg' : 'image/png'
      frame.rgbBlobUrl = bytesToBlobUrl(rgb.data as Uint8Array, mime)
    }

    const depth = proto.depthFrame
    frame.hasDepthData = (depth?.data?.length ?? 0) > 0
    if (!skipDepth && frame.hasDepthData && depth?.data) {
      this.depthLogCount++

      const bytesPerPixel = depth.format === DepthFormat.FLOAT32_METERS ? 4 : 2
      let dw = Math.round(this.cachedIntrinsics?.depth_width ?? 0)
      let dh = Math.round(this.cachedIntrinsics?.depth_height ?? 0)
      const dimSource = (dw > 0 && dh > 0) ? 'intrinsics' : (() => {
        const dims = FrameDecoder.inferDepthDimensions((depth.data as Uint8Array).length / bytesPerPixel)
        if (dims) { [dw, dh] = dims }
        return dims ? 'inferred' : 'unknown'
      })()

      if (this.depthLogCount === 1 || this.depthLogCount % 100 === 0) {
        console.log(
          `[FrameDecoder] depth #${this.depthLogCount}:`,
          `${(depth.data as Uint8Array).length} bytes,`,
          `format=${depth.format === DepthFormat.FLOAT32_METERS ? 'FLOAT32_METERS' : 'UINT16_MM'},`,
          `dims ${dw}×${dh} (${dimSource})`,
        )
      }

      if (dw > 0 && dh > 0) {
        const dataUrl = this.decodeDepthToDataUrl(depth.data as Uint8Array, depth.format ?? 0, dw, dh)
        if (dataUrl) frame.depthBlobUrl = dataUrl
      }
    }

    const pose = proto.cameraPose
    if (pose) {
      frame.camera_pose = {
        position: { x: pose.position?.x ?? 0, y: pose.position?.y ?? 0, z: pose.position?.z ?? 0 },
        rotation: { x: pose.rotation?.x ?? 0, y: pose.rotation?.y ?? 0, z: pose.rotation?.z ?? 0, w: pose.rotation?.w ?? 0 },
      } as CameraPose
    }

    const imu = proto.imuData
    if (imu) {
      frame.imu = {
        angular_velocity: FrameDecoder.vec3(imu.angularVelocity),
        linear_acceleration: FrameDecoder.vec3(imu.linearAcceleration),
        gravity: FrameDecoder.vec3(imu.gravity),
        magnetic_field: FrameDecoder.vec3(imu.magneticField),
      } as ImuData
    }

    const geom = proto.inferredGeometry
    if (geom) {
      frame.inferred_geometry = {
        plane_count: geom.planes?.length ?? 0,
        point_cloud_count: geom.pointCloud?.length ?? 0,
        point_cloud: (geom.pointCloud ?? []).map(tp => ({
          x: tp.point?.x ?? 0,
          y: tp.point?.y ?? 0,
          z: tp.point?.z ?? 0,
          confidence: tp.confidence ?? 0,
        })),
        planes: (geom.planes ?? []).map(p => ({
          type: p.type ?? 0,
          polygon: (p.polygon ?? []).map(v => ({ x: v.x ?? 0, y: v.y ?? 0, z: v.z ?? 0 })),
          center_pose: p.centerPose ? {
            position: {
              x: p.centerPose.position?.x ?? 0,
              y: p.centerPose.position?.y ?? 0,
              z: p.centerPose.position?.z ?? 0,
            },
            rotation: {
              x: p.centerPose.rotation?.x ?? 0,
              y: p.centerPose.rotation?.y ?? 0,
              z: p.centerPose.rotation?.z ?? 0,
              w: p.centerPose.rotation?.w ?? 1,
            },
          } : undefined,
          extent_x: p.extentX ?? 0,
          extent_z: p.extentZ ?? 0,
        })),
      }
    }

    const gps = proto.gpsLocation
    if (gps && (gps.latitude !== 0 || gps.longitude !== 0)) {
      frame.gps = {
        latitude: gps.latitude ?? 0,
        longitude: gps.longitude ?? 0,
        altitude: gps.altitude ?? 0,
        accuracy: gps.accuracy ?? 0,
        bearing: gps.bearing ?? 0,
        speed: gps.speed ?? 0,
        timestamp_ms: Number(gps.timestampMs ?? 0),
      }
    }

    return frame
  }

  decodeAnnotation(proto: bayesmech.vision.SegmentationResponse): DecodedAnnotation | null {
    const frameNumber = proto.frameIdentifier?.frameNumber ?? 0
    const masks = proto.masks
    if (!masks || masks.length === 0) return null
    const dataUrl = compositeMasksToDataUrl(masks)
    if (!dataUrl) return null

    // Deduplicate by objectId — one legend entry per tracked object
    const seen = new Set<number>()
    const legend: import('../types').SegmentationLegendEntry[] = []
    for (const m of masks) {
      const objId = m.objectId ?? 0
      if (seen.has(objId)) continue
      seen.add(objId)
      const c = MASK_COLORS[((objId % MASK_COLORS.length) + MASK_COLORS.length) % MASK_COLORS.length]
      legend.push({ objectId: objId, label: m.label || 'UNDEFINED', color: [c[0], c[1], c[2]] })
    }

    return { frameNumber, blobUrl: dataUrl, legend }
  }
}

// =====================================================================
// Frame buffer — ring buffer keyed by frame_number
// =====================================================================

const FRAME_BUFFER_CAPACITY = 500

class FrameBuffer {
  private frames = new Map<number, DecodedFrame>()
  private insertionOrder: number[] = []
  readonly capacity: number

  constructor(capacity = FRAME_BUFFER_CAPACITY) {
    this.capacity = capacity
  }

  push(frame: DecodedFrame): void {
    const key = frame.frame_number
    if (this.frames.has(key)) {
      const old = this.frames.get(key)!
      if (old.rgbBlobUrl) URL.revokeObjectURL(old.rgbBlobUrl)
      if (old.depthBlobUrl) URL.revokeObjectURL(old.depthBlobUrl)
      this.frames.set(key, frame)
      return
    }
    if (this.insertionOrder.length >= this.capacity) {
      const oldest = this.insertionOrder.shift()!
      const evicted = this.frames.get(oldest)
      if (evicted) {
        if (evicted.rgbBlobUrl) URL.revokeObjectURL(evicted.rgbBlobUrl)
        if (evicted.depthBlobUrl) URL.revokeObjectURL(evicted.depthBlobUrl)
        this.frames.delete(oldest)
      }
    }
    this.insertionOrder.push(key)
    this.frames.set(key, frame)
  }

  get(frameNumber: number): DecodedFrame | null {
    return this.frames.get(frameNumber) ?? null
  }

  has(frameNumber: number): boolean {
    return this.frames.has(frameNumber)
  }

  latest(): DecodedFrame | null {
    if (this.insertionOrder.length === 0) return null
    return this.frames.get(this.insertionOrder[this.insertionOrder.length - 1]) ?? null
  }

  destroy(): void {
    for (const frame of this.frames.values()) {
      if (frame.rgbBlobUrl) URL.revokeObjectURL(frame.rgbBlobUrl)
      if (frame.depthBlobUrl) URL.revokeObjectURL(frame.depthBlobUrl)
    }
    this.frames.clear()
    this.insertionOrder = []
  }
}

// =====================================================================
// Annotation buffer — ring buffer keyed by frame_number
// =====================================================================

const ANNOTATION_BUFFER_CAPACITY = 200

/** Index of first element strictly greater than value (standard upper_bound). */
function upperBound(arr: number[], value: number): number {
  let lo = 0, hi = arr.length
  while (lo < hi) {
    const mid = (lo + hi) >> 1
    if (arr[mid] <= value) lo = mid + 1
    else hi = mid
  }
  return lo
}

/** Index of value in sorted arr, or -1 if absent. */
function sortedIndexOf(arr: number[], value: number): number {
  const pos = upperBound(arr, value) - 1
  return (pos >= 0 && arr[pos] === value) ? pos : -1
}

class AnnotationBuffer {
  private annotations = new Map<number, DecodedAnnotation>()
  private insertionOrder: number[] = []  // eviction ring, insertion order
  private sortedKeys: number[] = []      // ascending-sorted frame numbers, for floor search
  readonly capacity: number

  constructor(capacity = ANNOTATION_BUFFER_CAPACITY) {
    this.capacity = capacity
  }

  set(annotation: DecodedAnnotation): void {
    const key = annotation.frameNumber
    if (this.annotations.has(key)) {
      URL.revokeObjectURL(this.annotations.get(key)!.blobUrl)
      this.annotations.set(key, annotation)
      return
    }
    if (this.insertionOrder.length >= this.capacity) {
      const oldest = this.insertionOrder.shift()!
      const evicted = this.annotations.get(oldest)
      if (evicted) {
        URL.revokeObjectURL(evicted.blobUrl)
        this.annotations.delete(oldest)
      }
      const si = sortedIndexOf(this.sortedKeys, oldest)
      if (si >= 0) this.sortedKeys.splice(si, 1)
    }
    this.insertionOrder.push(key)
    this.sortedKeys.splice(upperBound(this.sortedKeys, key), 0, key)
    this.annotations.set(key, annotation)
  }

  /** Return the annotation for the largest stored frameNumber <= frameNumber. */
  get(frameNumber: number): DecodedAnnotation | null {
    const pos = upperBound(this.sortedKeys, frameNumber) - 1
    if (pos < 0) return null
    return this.annotations.get(this.sortedKeys[pos]) ?? null
  }

  hasExact(frameNumber: number): boolean {
    return this.annotations.has(frameNumber)
  }

  /** True if any annotation exists with frameNumber <= frameNumber. */
  has(frameNumber: number): boolean {
    return upperBound(this.sortedKeys, frameNumber) > 0
  }

  latest(): DecodedAnnotation | null {
    if (this.insertionOrder.length === 0) return null
    return this.annotations.get(this.insertionOrder[this.insertionOrder.length - 1]) ?? null
  }

  destroy(): void {
    for (const annotation of this.annotations.values()) {
      URL.revokeObjectURL(annotation.blobUrl)
    }
    this.annotations.clear()
    this.insertionOrder = []
    this.sortedKeys = []
  }
}

// =====================================================================
// Coverage tracker — rolling window of frame signal presence
// =====================================================================

const COVERAGE_WINDOW = 150   // ~5 s at 30 fps

interface FrameRecord {
  hasDepth: boolean
  hasPose: boolean
  hasAccel: boolean
  hasGyro: boolean
  hasGravity: boolean
  hasMag: boolean
  hasIntrinsics: boolean
  hasGeometry: boolean
  hasGps: boolean
}

const ZERO_COVERAGE: CoverageStats = {
  windowSize: 0, depth: 0, pose: 0, linearAccel: 0,
  angularVelocity: 0, gravity: 0, magneticField: 0,
  intrinsicsCount: 0, geometry: 0, gps: 0,
}

class CoverageTracker {
  private records: FrameRecord[] = []
  private totalIntrinsics = 0

  record(frame: DecodedFrame): void {
    const hasIntrinsics = !!frame.camera_intrinsics
    if (hasIntrinsics) this.totalIntrinsics++
    this.records.push({
      hasDepth: frame.hasDepthData ?? false,
      hasPose: !!frame.camera_pose,
      hasAccel: !!frame.imu?.linear_acceleration,
      hasGyro: !!frame.imu?.angular_velocity,
      hasGravity: !!frame.imu?.gravity,
      hasMag: !!frame.imu?.magnetic_field,
      hasIntrinsics,
      hasGeometry:
        (frame.inferred_geometry?.plane_count ?? 0) > 0 ||
        (frame.inferred_geometry?.point_cloud_count ?? 0) > 0,
      hasGps: !!frame.gps,
    })
    if (this.records.length > COVERAGE_WINDOW) this.records.shift()
  }

  getStats(): CoverageStats {
    const n = this.records.length
    if (n === 0) return { ...ZERO_COVERAGE, intrinsicsCount: this.totalIntrinsics }
    const pct = (fn: (r: FrameRecord) => boolean) =>
      Math.round(this.records.filter(fn).length * 100 / n)
    return {
      windowSize: n,
      depth: pct(r => r.hasDepth),
      pose: pct(r => r.hasPose),
      linearAccel: pct(r => r.hasAccel),
      angularVelocity: pct(r => r.hasGyro),
      gravity: pct(r => r.hasGravity),
      magneticField: pct(r => r.hasMag),
      intrinsicsCount: this.totalIntrinsics,
      geometry: pct(r => r.hasGeometry),
      gps: pct(r => r.hasGps),
    }
  }

  destroy(): void {
    this.records = []
    this.totalIntrinsics = 0
  }
}

// =====================================================================
// Context
// =====================================================================

interface DashboardState {
  connectionStatus: ConnectionStatus
  displayedFrame: DecodedFrame | null
  displayedAnnotation: DecodedAnnotation | null
  frameCount: number
  fps: number
  coverageStats: CoverageStats
  isLive: boolean
  // Trajectory
  trajectoryPositions: TrajectoryPoint[]
  firstTimestampNs: number
  lastTimestampNs: number
  // Precomputed sensor data (available after recording load)
  sensorData: SensorFrameData[]
  // Buffer access
  getFrame: (frameNumber: number) => DecodedFrame | null
  getAnnotation: (frameNumber: number) => DecodedAnnotation | null
  requestFrame: (frameNumber: number) => void
  requestAnnotation: (frameNumber: number) => void
  // Playback
  currentIndex: number
  totalFrames: number
  isPlaying: boolean
  serverFps: number
  play: () => void
  pause: () => void
  seekTo: (index: number) => void
  skipForward: () => void
  skipBackward: () => void
  loadRecording: (name: string) => Promise<void>
  switchToLive: () => Promise<void>
}

const DashboardContext = createContext<DashboardState | undefined>(undefined)

const FPS_WINDOW_SIZE = 10

export const DashboardProvider: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  const [connectionStatus, setConnectionStatus] = useState<ConnectionStatus>('Disconnected')
  const [displayedFrame, setDisplayedFrame] = useState<DecodedFrame | null>(null)
  const [displayedAnnotation, setDisplayedAnnotation] = useState<DecodedAnnotation | null>(null)
  const [frameCount, setFrameCount] = useState(0)
  const [fps, setFps] = useState(0)
  const [isLive, setIsLive] = useState(false)

  // Playback state
  const [currentIndex, setCurrentIndex] = useState(0)
  const [totalFrames, setTotalFrames] = useState(0)
  const [isPlaying, setIsPlaying] = useState(false)
  const [serverFps, setServerFps] = useState(30)
  const playIntervalRef = useRef<ReturnType<typeof setInterval> | null>(null)

  // Trajectory & timestamps
  const [trajectoryPositions, setTrajectoryPositions] = useState<TrajectoryPoint[]>([])
  const [firstTimestampNs, setFirstTimestampNs] = useState(0)
  const [lastTimestampNs, setLastTimestampNs] = useState(0)

  // Precomputed sensor data (loaded once per recording)
  const [sensorData, setSensorData] = useState<SensorFrameData[]>([])

  const [coverageStats, setCoverageStats] = useState<CoverageStats>(ZERO_COVERAGE)

  const frameTimestamps = useRef<number[]>([])
  const frameBuffer = useRef(new FrameBuffer())
  const annotationBuffer = useRef(new AnnotationBuffer())
  const decoder = useRef(new FrameDecoder())
  const coverageTracker = useRef(new CoverageTracker())

  // Refs for access inside callbacks without stale closures
  const isPlayingRef = useRef(false)
  const isLiveRef = useRef(false)
  const frameCountRef = useRef(0)
  const totalFramesRef = useRef(0)
  const serverFpsRef = useRef(30)
  const currentIndexRef = useRef(0)
  const displayedFrameRef = useRef<DecodedFrame | null>(null)
  // Set when seekTo/skip sends a seek request, cleared when the response arrives.
  // This lets handleFrames distinguish "I asked for this" from "server pushed this".
  const seekPendingRef = useRef(false)

  useEffect(() => { isPlayingRef.current = isPlaying }, [isPlaying])
  useEffect(() => { isLiveRef.current = isLive }, [isLive])

  const updateDisplayedFrame = useCallback((frame: DecodedFrame | null) => {
    displayedFrameRef.current = frame
    setDisplayedFrame(frame)
    // Update annotation to match
    if (frame) {
      const ann = annotationBuffer.current.get(frame.frame_number)
      setDisplayedAnnotation(ann)
    } else {
      setDisplayedAnnotation(null)
    }
  }, [])

  const handleFrames = useCallback((frames: bayesmech.vision.PerceiverDataFrame[]) => {
    // In file mode, only process frames when we actually asked for them.
    // Drop any unsolicited server pushes (e.g. live frames from a connected device).
    const shouldProcess = isLiveRef.current || seekPendingRef.current || isPlayingRef.current
    if (!shouldProcess) return

    const skipDepth = !isLiveRef.current

    for (const proto of frames) {
      const frame = decoder.current.decodeFrame(proto, skipDepth)
      frameBuffer.current.push(frame)
      if (isLiveRef.current) {
        // Coverage tracking only meaningful in live mode
        coverageTracker.current.record(frame)
      }
    }

    const latest = frameBuffer.current.latest()

    if (isLiveRef.current) {
      // Live mode: frameCount = total received, display follows when playing
      frameCountRef.current += frames.length
      setFrameCount(frameCountRef.current)
      if (isPlayingRef.current && latest) {
        updateDisplayedFrame(latest)
        currentIndexRef.current = latest.frame_number
        setCurrentIndex(latest.frame_number)
      }
    } else {
      // File mode: only update display if we're playing OR this is a seek response.
      if (isPlayingRef.current && latest) {
        updateDisplayedFrame(latest)
      } else if (seekPendingRef.current && latest) {
        seekPendingRef.current = false
        updateDisplayedFrame(latest)
      }
      // frameCount in file mode is driven by currentIndex, not WS traffic
    }

    const now = performance.now()
    frameTimestamps.current.push(now)
    if (frameTimestamps.current.length > FPS_WINDOW_SIZE) {
      frameTimestamps.current.shift()
    }
    const ts = frameTimestamps.current
    if (ts.length >= 2) {
      const elapsed = (ts[ts.length - 1] - ts[0]) / 1000
      setFps(Math.round(((ts.length - 1) / elapsed) * 10) / 10)
    }
  }, [updateDisplayedFrame])

  const handleAnnotations = useCallback((annotations: bayesmech.vision.SegmentationResponse[]) => {
    for (const proto of annotations) {
      const frameNumber = proto.frameIdentifier?.frameNumber ?? 0
      if (annotationBuffer.current.hasExact(frameNumber)) continue
      const annotation = decoder.current.decodeAnnotation(proto)
      if (annotation) annotationBuffer.current.set(annotation)
    }
    // If new annotation matches the currently displayed frame, update
    const current = displayedFrameRef.current
    if (current) {
      const ann = annotationBuffer.current.get(current.frame_number)
      if (ann) setDisplayedAnnotation(ann)
    }
  }, [])

  const handleStats = useCallback((stats: Record<string, unknown>) => {
    const bf = stats.buffered_frames as number | undefined
    const rfps = stats.recording_fps as number | undefined
    const source = stats.source as string | undefined
    const fts = stats.first_timestamp_ns as number | undefined
    const lts = stats.last_timestamp_ns as number | undefined

    if (bf !== undefined) {
      totalFramesRef.current = bf
      setTotalFrames(bf)
    }
    if (rfps !== undefined && rfps > 0) {
      serverFpsRef.current = rfps
      setServerFps(rfps)
    }
    if (source !== undefined) {
      const live = source === 'live'
      isLiveRef.current = live
      setIsLive(live)
    }
    if (fts !== undefined) setFirstTimestampNs(fts)
    if (lts !== undefined) setLastTimestampNs(lts)
  }, [])

  const handleTrajectory = useCallback((positions: TrajectoryPoint[]) => {
    setTrajectoryPositions(positions)
  }, [])

  const handleSensorData = useCallback((frames: SensorFrameData[]) => {
    setSensorData(frames)
  }, [])

  const getFrame = useCallback((frameNumber: number): DecodedFrame | null => {
    return frameBuffer.current.get(frameNumber)
  }, [])

  const getAnnotation = useCallback((frameNumber: number): DecodedAnnotation | null => {
    return annotationBuffer.current.get(frameNumber)
  }, [])

  const requestFrame = useCallback((frameNumber: number): void => {
    if (!frameBuffer.current.has(frameNumber)) {
      dashboardWs.seek(frameNumber, frameNumber + 1)
    }
  }, [])

  const requestAnnotation = useCallback((frameNumber: number): void => {
    if (!annotationBuffer.current.has(frameNumber)) {
      dashboardWs.getAnnotationForFrame(frameNumber)
    }
  }, [])

  const seekTo = useCallback((index: number) => {
    if (isLiveRef.current) return
    const max = totalFramesRef.current
    const clamped = Math.max(0, Math.min(index, Math.max(max - 1, 0)))
    currentIndexRef.current = clamped
    setCurrentIndex(clamped)
    setFrameCount(clamped + 1)
    seekPendingRef.current = true
    dashboardWs.seek(clamped, clamped + 1)
  }, [])

  const play = useCallback(() => {
    if (isLiveRef.current) {
      // Live: just start displaying incoming frames
      setIsPlaying(true)
      return
    }
    if (currentIndexRef.current >= totalFramesRef.current - 1 && totalFramesRef.current > 0) {
      // At end of file: restart from beginning
      currentIndexRef.current = 0
      setCurrentIndex(0)
      setFrameCount(1)
      seekPendingRef.current = true
      dashboardWs.seek(0, 1)
    }
    setIsPlaying(true)
  }, [])

  const pause = useCallback(() => {
    setIsPlaying(false)
    if (playIntervalRef.current) {
      clearInterval(playIntervalRef.current)
      playIntervalRef.current = null
    }
  }, [])

  const skipForward = useCallback(() => {
    if (isLiveRef.current) return
    const jump = Math.round(10 * serverFpsRef.current)
    seekTo(currentIndexRef.current + jump)
  }, [seekTo])

  const skipBackward = useCallback(() => {
    if (isLiveRef.current) return
    const jump = Math.round(10 * serverFpsRef.current)
    seekTo(currentIndexRef.current - jump)
  }, [seekTo])

  const loadRecording = useCallback(async (name: string) => {
    // Reset all state
    frameBuffer.current.destroy()
    frameBuffer.current = new FrameBuffer()
    annotationBuffer.current.destroy()
    annotationBuffer.current = new AnnotationBuffer()
    coverageTracker.current.destroy()
    coverageTracker.current = new CoverageTracker()
    frameCountRef.current = 0
    totalFramesRef.current = 0
    currentIndexRef.current = 0
    frameTimestamps.current = []

    setFrameCount(0)
    setTotalFrames(0)
    setCurrentIndex(0)
    setFps(0)
    setDisplayedFrame(null)
    setDisplayedAnnotation(null)
    setTrajectoryPositions([])
    setFirstTimestampNs(0)
    setLastTimestampNs(0)
    setCoverageStats(ZERO_COVERAGE)
    setSensorData([])

    // Load on server
    await startPlayback(name)

    // Request stats, full trajectory, and full sensor data (precomputed once)
    dashboardWs.getStats()
    dashboardWs.getTrajectory()
    dashboardWs.getSensorData()

    // Seek to frame 0 and start playing
    currentIndexRef.current = 0
    setCurrentIndex(0)
    setFrameCount(1)
    seekPendingRef.current = true
    dashboardWs.seek(0, 1)
    setIsPlaying(true)
  }, [])

  const switchToLive = useCallback(async () => {
    // Reset all state
    frameBuffer.current.destroy()
    frameBuffer.current = new FrameBuffer()
    annotationBuffer.current.destroy()
    annotationBuffer.current = new AnnotationBuffer()
    coverageTracker.current.destroy()
    coverageTracker.current = new CoverageTracker()
    frameCountRef.current = 0
    totalFramesRef.current = 0
    currentIndexRef.current = 0
    frameTimestamps.current = []

    setFrameCount(0)
    setTotalFrames(0)
    setCurrentIndex(0)
    setFps(0)
    setDisplayedFrame(null)
    setDisplayedAnnotation(null)
    setTrajectoryPositions([])
    setFirstTimestampNs(0)
    setLastTimestampNs(0)
    setCoverageStats(ZERO_COVERAGE)

    // Tell server to switch to live
    await apiSwitchToLive()

    // Set live mode
    isLiveRef.current = true
    setIsLive(true)
    setIsPlaying(true)

    dashboardWs.getStats()
  }, [])

  // Client-driven playback for file recordings.
  // Always client-driven — server never pushes replay frames for files.
  useEffect(() => {
    if (isPlaying && !isLive && totalFrames > 0) {
      playIntervalRef.current = setInterval(() => {
        const next = currentIndexRef.current + 1
        if (next >= totalFramesRef.current) {
          setIsPlaying(false)
          return
        }
        currentIndexRef.current = next
        setCurrentIndex(next)
        setFrameCount(next + 1)
        dashboardWs.seek(next, next + 1)
      }, 1000 / serverFpsRef.current)
    } else if (playIntervalRef.current) {
      clearInterval(playIntervalRef.current)
      playIntervalRef.current = null
    }
    return () => {
      if (playIntervalRef.current) {
        clearInterval(playIntervalRef.current)
        playIntervalRef.current = null
      }
    }
  }, [isPlaying, isLive, serverFps, totalFrames])

  useEffect(() => {
    const timer = setInterval(() => {
      setCoverageStats(coverageTracker.current.getStats())
    }, 1000)
    return () => clearInterval(timer)
  }, [])

  // Poll stats every 3s so isLive and totalFrames stay current
  useEffect(() => {
    const timer = setInterval(() => {
      dashboardWs.getStats()
    }, 3000)
    return () => clearInterval(timer)
  }, [])

  useEffect(() => {
    dashboardWs.connect()
    const unsubFrames = dashboardWs.addFrameListener(handleFrames)
    const unsubAnnotations = dashboardWs.addAnnotationListener(handleAnnotations)
    const unsubStatus = dashboardWs.addStatusListener(setConnectionStatus)
    const unsubStats = dashboardWs.addStatsListener(handleStats)
    const unsubTrajectory = dashboardWs.addTrajectoryListener(handleTrajectory)
    const unsubSensorData = dashboardWs.addSensorDataListener(handleSensorData)

    dashboardWs.getStats()

    return () => {
      unsubFrames()
      unsubAnnotations()
      unsubStatus()
      unsubStats()
      unsubTrajectory()
      unsubSensorData()
      dashboardWs.disconnect()
      frameBuffer.current.destroy()
      annotationBuffer.current.destroy()
      coverageTracker.current.destroy()
      if (playIntervalRef.current) clearInterval(playIntervalRef.current)
    }
  }, [handleFrames, handleAnnotations, handleStats, handleTrajectory, handleSensorData])

  return (
    <DashboardContext.Provider value={{
      connectionStatus, displayedFrame, displayedAnnotation, frameCount, fps, coverageStats,
      isLive, trajectoryPositions, firstTimestampNs, lastTimestampNs, sensorData,
      getFrame, getAnnotation, requestFrame, requestAnnotation,
      currentIndex, totalFrames, isPlaying, serverFps,
      play, pause, seekTo, skipForward, skipBackward, loadRecording, switchToLive,
    }}>
      {children}
    </DashboardContext.Provider>
  )
}

export function useDashboard(): DashboardState {
  const ctx = useContext(DashboardContext)
  if (!ctx) throw new Error('useDashboard must be used within a DashboardProvider')
  return ctx
}
