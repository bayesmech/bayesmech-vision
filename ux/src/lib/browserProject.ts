import protobuf from 'protobufjs'
import perceiverProto from '../../../proto/perceiver.proto?raw'
import primitivesProto from '../../../proto/primitives.proto?raw'
import spatialProto from '../../../proto/spatial.proto?raw'
import segmentationProto from '../../../proto/segmentation.proto?raw'
import motionCaptureProto from '../../../proto/motioncap.proto?raw'
import insightgenProto from '../../../proto/insightgen.proto?raw'
import idoSlamProto from '../../../proto/idoslam.proto?raw'
import type {
  ChatThread,
  IdoSlamSummary,
  MotionCaptureOverlay,
  MotionCaptureTrack,
  ProjectAnalysis,
  ProjectScanResult,
  RecordingEntry,
  SegMask,
  SensorDataSummary,
  VisFrame,
  VisSummary,
} from '../types'

const FRAME_SIZE_LIMIT = 10 * 1024 * 1024
const MAX_SAMPLE_FRAMES = 96
const MAX_POINTS_PER_FRAME = 220

const BUILT_IN_ANALYSES = [
  { key: 'video', title: 'Video', kind: 'video', source: 'vis' as const },
  { key: 'surface-planes', title: 'Surface Estimates', kind: 'geometry', source: 'vis' as const },
  { key: 'sensors', title: 'Sensor Data', kind: 'sensors', source: 'vis' as const },
]

const ANALYSIS_DEFS = [
  { key: 'segmentation', title: 'Segmentation', kind: 'protobuf', suffixes: ['segmentation.pb', 'seg.pb'] },
  { key: 'motioncap', title: 'Motion Capture', kind: 'protobuf', suffixes: ['motioncap.pb', 'motion.pb'] },
  { key: 'idoslam', title: 'Map Generation', kind: 'protobuf', suffixes: ['idoslam.pb'] },
  { key: 'genspark', title: 'AI Analysis', kind: 'protobuf', suffixes: ['genspark.pb'] },
  { key: 'chat', title: 'Follow-up Chat', kind: 'protobuf', suffixes: ['chat.pb'] },
  { key: 'reconstruction', title: '3D Reconstruction', kind: 'protobuf', suffixes: ['reconstruct.pb', 'recon.pb'] },
  { key: 'snookestown', title: 'Snookestown', kind: 'protobuf', suffixes: ['snook.pb'] },
  { key: 'pongtown', title: 'Pongtown', kind: 'protobuf', suffixes: ['pongtown.pb'] },
]

type BrowserFileScan = {
  project: ProjectScanResult
  filesByPath: Map<string, File>
}

type FrameIndexEntry = {
  offset: number
  length: number
}

let perceiverType: protobuf.Type | null = null
let gensparkResponseType: protobuf.Type | null = null
let chatHistoryType: protobuf.Type | null = null
let idoSlamType: protobuf.Type | null = null
let motionCaptureType: protobuf.Type | null = null

function stripProtoHeader(source: string): string {
  return source
    .split('\n')
    .filter((line) => {
      const trimmed = line.trim()
      return !(
        trimmed.startsWith('syntax ') ||
        trimmed.startsWith('package ') ||
        trimmed.startsWith('option ') ||
        trimmed.startsWith('import ')
      )
    })
    .join('\n')
}

function getPerceiverType(): protobuf.Type {
  if (perceiverType) return perceiverType

  const schema = [
    'syntax = "proto3";',
    'package bayesmech.vision;',
    stripProtoHeader(primitivesProto),
    stripProtoHeader(spatialProto),
    stripProtoHeader(perceiverProto),
  ].join('\n\n')

  const root = new protobuf.Root()
  protobuf.parse(schema, root, { keepCase: false })
  root.resolveAll()
  perceiverType = root.lookupType('bayesmech.vision.PerceiverDataFrame')
  return perceiverType
}

function getInsightgenTypes(): { genspark: protobuf.Type; chat: protobuf.Type } {
  if (gensparkResponseType && chatHistoryType) {
    return { genspark: gensparkResponseType, chat: chatHistoryType }
  }

  const root = new protobuf.Root()
  protobuf.parse(insightgenProto, root, { keepCase: false })
  root.resolveAll()
  gensparkResponseType = root.lookupType('bayesmech.vision.GensparkResponse')
  chatHistoryType = root.lookupType('bayesmech.vision.ChatHistory')
  return { genspark: gensparkResponseType, chat: chatHistoryType }
}

function getIdoSlamType(): protobuf.Type {
  if (idoSlamType) return idoSlamType

  const schema = [
    'syntax = "proto3";',
    'package bayesmech.vision;',
    stripProtoHeader(primitivesProto),
    stripProtoHeader(spatialProto),
    stripProtoHeader(perceiverProto),
    stripProtoHeader(idoSlamProto),
  ].join('\n\n')

  const root = new protobuf.Root()
  protobuf.parse(schema, root, { keepCase: false })
  root.resolveAll()
  idoSlamType = root.lookupType('bayesmech.vision.IdoSlamResponse')
  return idoSlamType
}

function getMotionCaptureType(): protobuf.Type {
  if (motionCaptureType) return motionCaptureType
  const schema = [
    'syntax = "proto3";',
    'package bayesmech.vision;',
    stripProtoHeader(primitivesProto),
    stripProtoHeader(spatialProto),
    stripProtoHeader(perceiverProto),
    stripProtoHeader(motionCaptureProto),
  ].join('\n\n')
  const root = new protobuf.Root()
  protobuf.parse(schema, root, { keepCase: false })
  root.resolveAll()
  motionCaptureType = root.lookupType('bayesmech.vision.MotionCaptureResponse')
  return motionCaptureType
}

function byteSizeLabel(bytes: number): string {
  if (!Number.isFinite(bytes)) return '0 B'
  const units = ['B', 'KB', 'MB', 'GB']
  let value = bytes
  let idx = 0
  while (value >= 1024 && idx < units.length - 1) {
    value /= 1024
    idx += 1
  }
  return `${value >= 10 || idx === 0 ? value.toFixed(0) : value.toFixed(1)} ${units[idx]}`
}

function fileRelativePath(file: File): string {
  const withRelative = file as File & { webkitRelativePath?: string }
  return withRelative.webkitRelativePath || file.name
}

function dirname(relativePath: string): string {
  const slash = relativePath.lastIndexOf('/')
  return slash >= 0 ? relativePath.slice(0, slash) : ''
}

function basename(relativePath: string): string {
  const slash = relativePath.lastIndexOf('/')
  return slash >= 0 ? relativePath.slice(slash + 1) : relativePath
}

function browserPath(relativePath: string): string {
  return `browser://${relativePath}`
}

function commonRootName(files: File[]): string {
  const first = files[0]
  if (!first) return 'Selected files'
  const relativePath = fileRelativePath(first)
  const slash = relativePath.indexOf('/')
  return slash > 0 ? relativePath.slice(0, slash) : 'Selected files'
}

function artifactFor(
  relativeDir: string,
  baseName: string,
  fileByRelativePath: Map<string, File>,
  def: (typeof ANALYSIS_DEFS)[number],
  suffix: string,
): ProjectAnalysis | null {
  const candidate = `${relativeDir ? `${relativeDir}/` : ''}${baseName}.${suffix}`
  const file = fileByRelativePath.get(candidate)
  if (!file) return null

  return {
    key: def.key,
    title: def.title,
    kind: def.kind,
    source: 'artifact',
    suffix,
    path: browserPath(candidate),
    relativePath: candidate,
    sizeBytes: file.size,
    sizeLabel: byteSizeLabel(file.size),
    modifiedMs: file.lastModified,
  }
}

function analysesForVis(relativePath: string, fileByRelativePath: Map<string, File>): ProjectAnalysis[] {
  const dir = dirname(relativePath)
  const fileName = basename(relativePath)
  const baseName = fileName.slice(0, -'.vis.pb'.length)
  const analyses: ProjectAnalysis[] = BUILT_IN_ANALYSES.map((analysis) => ({
    ...analysis,
    path: browserPath(relativePath),
    relativePath,
  }))

  for (const def of ANALYSIS_DEFS) {
    for (const suffix of def.suffixes) {
      const artifact = artifactFor(dir, baseName, fileByRelativePath, def, suffix)
      if (artifact) {
        analyses.push(artifact)
        break
      }
    }
  }

  return analyses
}

export function scanBrowserFiles(fileList: FileList | File[]): BrowserFileScan {
  const files = Array.from(fileList)
  const fileByRelativePath = new Map<string, File>()
  for (const file of files) {
    fileByRelativePath.set(fileRelativePath(file), file)
  }

  const visFiles = files
    .filter((file) => fileRelativePath(file).endsWith('.vis.pb'))
    .sort((a, b) => fileRelativePath(a).localeCompare(fileRelativePath(b)))

  const filesByPath = new Map<string, File>()
  const recordings: RecordingEntry[] = visFiles.map((file, index) => {
    const relativePath = fileRelativePath(file)
    const fileName = basename(relativePath)
    const dir = dirname(relativePath)
    const baseName = fileName.slice(0, -'.vis.pb'.length)
    const folderName = dir ? basename(dir) : ''
    const path = browserPath(relativePath)
    filesByPath.set(path, file)

    // Register artifact files too (e.g. .segmentation.pb) so overlays can read
    // them by their browser:// path, mirroring the recording registration.
    const analyses = analysesForVis(relativePath, fileByRelativePath)
    for (const analysis of analyses) {
      if (analysis.source !== 'artifact') continue
      const artifactFile = fileByRelativePath.get(analysis.relativePath)
      if (artifactFile) filesByPath.set(analysis.path, artifactFile)
    }

    return {
      id: `${baseName}:${index}`,
      name: folderName && folderName !== baseName ? `${folderName}/${baseName}` : baseName,
      fileStem: baseName,
      path,
      directoryPath: dir || commonRootName(files),
      relativePath,
      sizeBytes: file.size,
      sizeLabel: byteSizeLabel(file.size),
      modifiedMs: file.lastModified,
      analyses,
    }
  })

  return {
    filesByPath,
    project: {
      rootPath: `browser://${commonRootName(files)}`,
      name: commonRootName(files),
      recordings,
      error: recordings.length ? undefined : 'Select a folder or files containing at least one .vis.pb file.',
    },
  }
}

function concatBytes(left: Uint8Array, right: Uint8Array): Uint8Array {
  if (left.length === 0) return right
  const combined = new Uint8Array(left.length + right.length)
  combined.set(left)
  combined.set(right, left.length)
  return combined
}

function readUint32BE(bytes: Uint8Array, offset: number): number {
  return (
    bytes[offset] * 0x1000000 +
    ((bytes[offset + 1] << 16) | (bytes[offset + 2] << 8) | bytes[offset + 3])
  )
}

async function readFrameIndex(
  file: File,
  sizeLimit = FRAME_SIZE_LIMIT,
): Promise<{ offsets: FrameIndexEntry[]; errors: number }> {
  const reader = file.stream().getReader()
  const offsets: FrameIndexEntry[] = []
  let pending = new Uint8Array()
  let pendingStartOffset = 0
  let errors = 0

  while (true) {
    const { done, value } = await reader.read()
    const combined = done ? pending : concatBytes(pending, value)
    let cursor = 0

    while (cursor + 4 <= combined.length) {
      const frameStart = pendingStartOffset + cursor
      const length = readUint32BE(combined, cursor)

      if (length === 0 || length > sizeLimit || frameStart + 4 + length > file.size) {
        errors += 1
        cursor += 1
        continue
      }

      if (cursor + 4 + length > combined.length) break

      offsets.push({ offset: frameStart + 4, length })
      cursor += 4 + length
    }

    pending = combined.slice(cursor)
    pendingStartOffset += cursor

    if (done) break
  }

  return { offsets, errors }
}

function buildSampleIndexes(count: number): number[] {
  if (count <= 0) return []
  if (count <= MAX_SAMPLE_FRAMES) {
    return Array.from({ length: count }, (_value, index) => index)
  }

  const indexes = new Set([0, count - 1])
  for (let i = 0; i < MAX_SAMPLE_FRAMES; i += 1) {
    indexes.add(Math.round((i / (MAX_SAMPLE_FRAMES - 1)) * (count - 1)))
  }
  return [...indexes].sort((a, b) => a - b)
}

function finiteNumber(value: unknown, fallback = 0): number {
  const number = Number(value)
  return Number.isFinite(number) ? number : fallback
}

function vector3(value: unknown) {
  const item = value as { x?: unknown; y?: unknown; z?: unknown } | null | undefined
  return {
    x: finiteNumber(item?.x),
    y: finiteNumber(item?.y),
    z: finiteNumber(item?.z),
  }
}

function quaternion(value: unknown) {
  const item = value as { x?: unknown; y?: unknown; z?: unknown; w?: unknown } | null | undefined
  return {
    x: finiteNumber(item?.x),
    y: finiteNumber(item?.y),
    z: finiteNumber(item?.z),
    w: finiteNumber(item?.w, 1),
  }
}

function pose(value: unknown) {
  const item = value as { position?: unknown; rotation?: unknown } | null | undefined
  if (!item) return null
  return {
    position: vector3(item.position),
    rotation: quaternion(item.rotation),
  }
}

function timestampString(value: unknown): string {
  if (value == null) return '0'
  if (typeof value === 'number') return String(Math.trunc(value))
  if (typeof value === 'bigint') return value.toString()
  if (typeof value === 'object' && 'toString' in value && typeof value.toString === 'function') {
    return value.toString()
  }
  return String(value)
}

function timestampDurationSeconds(first: string, last: string): number {
  try {
    const delta = BigInt(last) - BigInt(first)
    if (delta <= 0n) return 0
    return Number(delta / 1000000n) / 1000
  } catch {
    return 0
  }
}

function samplePointCloud(pointCloud: unknown) {
  if (!Array.isArray(pointCloud) || pointCloud.length === 0) return []
  const stride = Math.max(1, Math.ceil(pointCloud.length / MAX_POINTS_PER_FRAME))
  const points = []

  for (let i = 0; i < pointCloud.length && points.length < MAX_POINTS_PER_FRAME; i += stride) {
    const tracked = pointCloud[i] as { point?: unknown; confidence?: unknown }
    const point = vector3(tracked?.point)
    if ([point.x, point.y, point.z].every(Number.isFinite)) {
      points.push({
        ...point,
        confidence: finiteNumber(tracked?.confidence, 0.4),
      })
    }
  }

  return points
}

function samplePlanes(planes: unknown) {
  if (!Array.isArray(planes) || planes.length === 0) return []
  return planes.slice(0, 24).map((planeValue) => {
    const plane = planeValue as {
      type?: unknown
      extentX?: unknown
      extentZ?: unknown
      centerPose?: unknown
      polygon?: unknown
    }
    return {
      type: finiteNumber(plane.type),
      extentX: finiteNumber(plane.extentX),
      extentZ: finiteNumber(plane.extentZ),
      centerPose: pose(plane.centerPose),
      polygon: Array.isArray(plane.polygon) ? plane.polygon.slice(0, 16).map(vector3) : [],
    }
  })
}

function rgbPreviewFromFrame(frame: Record<string, unknown>) {
  const rgb = frame.rgbFrame as { data?: Uint8Array; format?: unknown; width?: unknown; height?: unknown } | undefined
  if (!rgb?.data?.length) return null
  const format = Number(rgb.format ?? 0)
  if (format !== 4) return null

  return {
    dataUrl: URL.createObjectURL(new Blob([new Uint8Array(rgb.data)], { type: 'image/jpeg' })),
    width: finiteNumber(rgb.width),
    height: finiteNumber(rgb.height),
    frameNumber: finiteNumber((frame.frameIdentifier as { frameNumber?: unknown } | undefined)?.frameNumber),
  }
}

function depthStatsFromFrame(frame: Record<string, unknown>) {
  const depth = frame.depthFrame as { data?: Uint8Array; format?: unknown; width?: unknown; height?: unknown } | undefined
  if (!depth?.data?.length) return null

  const bytes = depth.data
  const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength)
  const format = Number(depth.format ?? 0)
  const stride = Math.max(1, Math.floor(bytes.length / 8000))
  let min = Number.POSITIVE_INFINITY
  let max = 0
  let count = 0

  if (format === 1) {
    const step = Math.max(2, stride + (stride % 2))
    for (let offset = 0; offset + 1 < bytes.length; offset += step) {
      const value = view.getUint16(offset, true) / 1000
      if (value > 0 && Number.isFinite(value)) {
        min = Math.min(min, value)
        max = Math.max(max, value)
        count += 1
      }
    }
  } else if (format === 2) {
    const step = Math.max(4, stride + ((4 - (stride % 4)) % 4))
    for (let offset = 0; offset + 3 < bytes.length; offset += step) {
      const value = view.getFloat32(offset, true)
      if (value > 0 && Number.isFinite(value)) {
        min = Math.min(min, value)
        max = Math.max(max, value)
        count += 1
      }
    }
  }

  if (!count) return null
  return {
    width: finiteNumber(depth.width),
    height: finiteNumber(depth.height),
    format,
    minMeters: min,
    maxMeters: max,
    sampledPixels: count,
  }
}

function expandBounds(bounds: NonNullable<VisSummary['bounds']>, point: { x: number; y: number; z: number } | null) {
  if (!point) return bounds
  const values = [point.x, point.y, point.z]
  if (!values.every(Number.isFinite)) return bounds

  return {
    min: {
      x: Math.min(bounds.min.x, point.x),
      y: Math.min(bounds.min.y, point.y),
      z: Math.min(bounds.min.z, point.z),
    },
    max: {
      x: Math.max(bounds.max.x, point.x),
      y: Math.max(bounds.max.y, point.y),
      z: Math.max(bounds.max.z, point.z),
    },
  }
}

export async function readBrowserVisSummary(recording: RecordingEntry, file: File): Promise<VisSummary> {
  const { offsets, errors } = await readFrameIndex(file)
  const sampleIndexes = buildSampleIndexes(offsets.length)
  const frameType = getPerceiverType()
  const samples: VisSummary['samples'] = []
  const devices = new Set<string>()
  let firstTimestampNs = '0'
  let lastTimestampNs = '0'
  let firstFrameNumber = 0
  let lastFrameNumber = 0
  let rgbPreview: VisSummary['rgbPreview'] = null
  let depthStats: VisSummary['depthStats'] = null
  let sampledPointCount = 0
  let sampledPlaneCount = 0
  let framesWithPointCloud = 0
  let framesWithPlanes = 0
  let framesWithRgb = 0
  let framesWithDepth = 0
  let bounds: NonNullable<VisSummary['bounds']> = {
    min: { x: Number.POSITIVE_INFINITY, y: Number.POSITIVE_INFINITY, z: Number.POSITIVE_INFINITY },
    max: { x: Number.NEGATIVE_INFINITY, y: Number.NEGATIVE_INFINITY, z: Number.NEGATIVE_INFINITY },
  }

  for (const index of sampleIndexes) {
    const item = offsets[index]
    if (!item) continue
    const payload = new Uint8Array(await file.slice(item.offset, item.offset + item.length).arrayBuffer())

    let frame: Record<string, unknown>
    try {
      frame = frameType.decode(payload) as unknown as Record<string, unknown>
    } catch {
      continue
    }

    const identifier = frame.frameIdentifier as { timestampNs?: unknown; frameNumber?: unknown; deviceId?: unknown } | undefined
    const timestampNs = timestampString(identifier?.timestampNs)
    const frameNumber = finiteNumber(identifier?.frameNumber)
    const deviceId = String(identifier?.deviceId ?? '').trim()
    if (deviceId) devices.add(deviceId)
    if (samples.length === 0) {
      firstTimestampNs = timestampNs
      firstFrameNumber = frameNumber
    }
    lastTimestampNs = timestampNs
    lastFrameNumber = frameNumber

    const cameraPose = pose(frame.cameraPose)
    if (cameraPose) bounds = expandBounds(bounds, cameraPose.position)

    const geometry = (frame.inferredGeometry ?? {}) as { pointCloud?: unknown; planes?: unknown }
    const points = samplePointCloud(geometry.pointCloud)
    const planes = samplePlanes(geometry.planes)
    sampledPointCount += Array.isArray(geometry.pointCloud) ? geometry.pointCloud.length : 0
    sampledPlaneCount += Array.isArray(geometry.planes) ? geometry.planes.length : 0
    if (points.length) framesWithPointCloud += 1
    if (planes.length) framesWithPlanes += 1
    if ((frame.rgbFrame as { data?: Uint8Array } | undefined)?.data?.length) framesWithRgb += 1
    if ((frame.depthFrame as { data?: Uint8Array } | undefined)?.data?.length) framesWithDepth += 1

    for (const point of points) bounds = expandBounds(bounds, point)
    for (const plane of planes) {
      if (plane.centerPose) bounds = expandBounds(bounds, plane.centerPose.position)
    }

    if (!rgbPreview) rgbPreview = rgbPreviewFromFrame(frame)
    if (!depthStats) depthStats = depthStatsFromFrame(frame)

    const rgb = frame.rgbFrame as { width?: unknown; height?: unknown; format?: unknown; data?: Uint8Array } | undefined
    const depth = frame.depthFrame as { width?: unknown; height?: unknown; format?: unknown; data?: Uint8Array } | undefined

    samples.push({
      sampleIndex: index,
      frameNumber,
      timestampNs,
      cameraPose,
      points,
      planes,
      rgb: rgb
        ? {
            width: finiteNumber(rgb.width),
            height: finiteNumber(rgb.height),
            format: finiteNumber(rgb.format),
            bytes: finiteNumber(rgb.data?.length),
          }
        : null,
      depth: depth
        ? {
            width: finiteNumber(depth.width),
            height: finiteNumber(depth.height),
            format: finiteNumber(depth.format),
            bytes: finiteNumber(depth.data?.length),
          }
        : null,
      userTextInput: String(frame.userTextInput ?? ''),
    })
  }

  const hasFiniteBounds = Number.isFinite(bounds.min.x) && Number.isFinite(bounds.max.x)
  return {
    path: recording.path,
    fileName: file.name,
    sizeBytes: file.size,
    sizeLabel: byteSizeLabel(file.size),
    frameCount: offsets.length,
    decodedFrames: samples.length,
    parseErrors: errors,
    firstTimestampNs,
    lastTimestampNs,
    durationSeconds: timestampDurationSeconds(firstTimestampNs, lastTimestampNs),
    firstFrameNumber,
    lastFrameNumber,
    devices: [...devices],
    framesWithRgb,
    framesWithDepth,
    framesWithPointCloud,
    framesWithPlanes,
    sampledPointCount,
    sampledPlaneCount,
    rgbPreview,
    depthStats,
    bounds: hasFiniteBounds ? bounds : null,
    samples,
  }
}

const sensorDataCacheByFile = new WeakMap<File, Promise<SensorDataSummary>>()

export function readBrowserVisSensors(recording: RecordingEntry, file: File): Promise<SensorDataSummary> {
  let cached = sensorDataCacheByFile.get(file)
  if (cached) return cached

  cached = (async () => {
    const { offsets } = await cachedFrameIndex(file)
    const frameType = getPerceiverType()
    const samples: SensorDataSummary['samples'] = []

    for (let index = 0; index < offsets.length; index += 1) {
      const item = offsets[index]
      const payload = new Uint8Array(await file.slice(item.offset, item.offset + item.length).arrayBuffer())
      let frame: Record<string, unknown>
      try {
        frame = frameType.decode(payload) as unknown as Record<string, unknown>
      } catch {
        continue
      }

      const identifier = frame.frameIdentifier as {
        timestampNs?: unknown
        frameNumber?: unknown
        deviceId?: unknown
      } | undefined
      const imu = frame.imuData as {
        linearAcceleration?: unknown
        angularVelocity?: unknown
        gravity?: unknown
        magneticField?: unknown
      } | undefined
      const gps = frame.gpsLocation as {
        latitude?: unknown
        longitude?: unknown
        altitude?: unknown
        accuracy?: unknown
        bearing?: unknown
        speed?: unknown
        timestampMs?: unknown
      } | undefined
      const ultrasonic = frame.ultrasonicSensorData as {
        normalizedDistance?: unknown
        distanceMeters?: unknown
        maxRangeMeters?: unknown
        valid?: unknown
        sequence?: unknown
        ageMs?: unknown
      } | undefined

      samples.push({
        index,
        frameNumber: finiteNumber(identifier?.frameNumber),
        timestampNs: timestampString(identifier?.timestampNs),
        deviceId: String(identifier?.deviceId ?? ''),
        linearAcceleration: imu?.linearAcceleration ? vector3(imu.linearAcceleration) : null,
        angularVelocity: imu?.angularVelocity ? vector3(imu.angularVelocity) : null,
        gravity: imu?.gravity ? vector3(imu.gravity) : null,
        magneticField: imu?.magneticField ? vector3(imu.magneticField) : null,
        cameraPose: pose(frame.cameraPose),
        gps: gps
          ? {
              latitude: finiteNumber(gps.latitude),
              longitude: finiteNumber(gps.longitude),
              altitude: finiteNumber(gps.altitude),
              accuracy: finiteNumber(gps.accuracy),
              bearing: finiteNumber(gps.bearing),
              speed: finiteNumber(gps.speed),
              timestampMs: timestampString(gps.timestampMs),
            }
          : null,
        ultrasonic: ultrasonic
          ? {
              normalizedDistance: Math.max(0, Math.min(1, finiteNumber(ultrasonic.normalizedDistance))),
              distanceMeters: finiteNumber(ultrasonic.distanceMeters),
              maxRangeMeters: finiteNumber(ultrasonic.maxRangeMeters),
              valid: Boolean(ultrasonic.valid),
              sequence: finiteNumber(ultrasonic.sequence),
              ageMs: finiteNumber(ultrasonic.ageMs),
            }
          : null,
      })
    }

    return { path: recording.path, frameCount: offsets.length, samples }
  })()

  sensorDataCacheByFile.set(file, cached)
  return cached
}

function browserIdoSlamSummary(response: Record<string, unknown>, path: string): IdoSlamSummary {
  const asArray = (value: unknown): Array<Record<string, unknown>> => Array.isArray(value) ? value : []
  const poseValue = (value: Record<string, unknown>) => {
    const frameId = value.frameId as Record<string, unknown> | undefined
    const worldPose = value.worldPose as Record<string, unknown> | undefined
    return {
      frameIndex: finiteNumber(value.frameIndex),
      frameNumber: finiteNumber(frameId?.frameNumber),
      timestampNs: timestampString(frameId?.timestampNs),
      position: vector3(worldPose?.position),
      eulerDegrees: vector3(value.eulerDegrees),
    }
  }
  const widthValue = (value: Record<string, unknown>) => ({
    frameIndex: finiteNumber(value.frameIndex),
    latitude: finiteNumber(value.latitude),
    longitude: finiteNumber(value.longitude),
    widthM: finiteNumber(value.widthM),
    leftOffsetM: finiteNumber(value.leftOffsetM),
    rightOffsetM: finiteNumber(value.rightOffsetM),
    bikeFraction: finiteNumber(value.bikeFraction),
    method: String(value.method ?? ''),
  })
  const pairDebug = asArray(response.pairDebug)
  const correspondences = pairDebug.flatMap((pair) => asArray(pair.correspondences))

  return {
    path,
    framePoses: asArray(response.framePoses).map(poseValue),
    refinedFramePoses: asArray(response.refinedFramePoses).map(poseValue),
    pairwiseMotion: asArray(response.pairwiseMotion).map((item) => ({
      frameIndex: finiteNumber(item.frameIndex),
      status: String(item.status ?? ''),
      goodMatchCount: finiteNumber(item.goodMatchCount),
      essentialInlierCount: finiteNumber(item.essentialInlierCount),
      essentialInlierRatio: finiteNumber(item.essentialInlierRatio),
      translationMagnitude: finiteNumber(item.translationMagnitude),
      rotationDeg: finiteNumber(item.rotationDeg),
    })),
    planeWidthEstimates: asArray(response.planeWidthEstimates).map(widthValue),
    triangulatedWidthEstimates: asArray(response.triangulatedWidthEstimates).map(widthValue),
    canonicalCenterline: asArray(response.canonicalCenterline).map((item) => ({
      progressM: finiteNumber(item.progressM),
      centerX: finiteNumber(item.centerX),
      centerY: finiteNumber(item.centerY),
      widthM: finiteNumber(item.widthM),
      leftX: finiteNumber(item.leftX),
      leftY: finiteNumber(item.leftY),
      rightX: finiteNumber(item.rightX),
      rightY: finiteNumber(item.rightY),
    })),
    groundPointCount: asArray(response.groundPoints).length,
    pairDebugCount: pairDebug.length,
    correspondenceCount: correspondences.length,
    inlierCount: correspondences.filter((item) => Boolean(item.inlier)).length,
  }
}

const idoSlamCacheByFile = new WeakMap<File, Promise<IdoSlamSummary>>()

export function readBrowserIdoSlam(path: string, file: File): Promise<IdoSlamSummary> {
  let cached = idoSlamCacheByFile.get(file)
  if (cached) return cached

  cached = (async () => {
    const bytes = new Uint8Array(await file.arrayBuffer())
    let offset = 0
    let latest: Record<string, unknown> | null = null
    const responseType = getIdoSlamType()
    while (offset + 4 <= bytes.length) {
      const length = readUint32BE(bytes, offset)
      offset += 4
      if (length <= 0 || offset + length > bytes.length) break
      try {
        latest = responseType.decode(bytes.subarray(offset, offset + length)) as unknown as Record<string, unknown>
      } catch {
        // Retain the latest valid record.
      }
      offset += length
    }
    if (!latest) throw new Error('IDOSLAM file has no protobuf records.')
    return browserIdoSlamSummary(latest, path)
  })()

  idoSlamCacheByFile.set(file, cached)
  return cached
}

// Cache each File's decoded frame-offset index so random-access seeking does not
// rescan the whole file on every request.
const frameIndexCacheByFile = new WeakMap<File, Promise<{ offsets: FrameIndexEntry[]; errors: number }>>()

function cachedFrameIndex(file: File): Promise<{ offsets: FrameIndexEntry[]; errors: number }> {
  let cached = frameIndexCacheByFile.get(file)
  if (!cached) {
    cached = readFrameIndex(file)
    frameIndexCacheByFile.set(file, cached)
  }
  return cached
}

// Random-access decode of a single frame's RGB image, by frame index into the
// recording (0 .. frameCount-1). Mirrors the Electron main-process readVisFrame
// for browser (no-bridge) mode. Returns null dataUrl when the frame has no
// decodable RGB (non-JPEG format or an RGB-less frame).
export async function readBrowserVisFrame(file: File, frameIndex: number): Promise<VisFrame | null> {
  const { offsets } = await cachedFrameIndex(file)
  const total = offsets.length
  if (total === 0) return null

  const index = Math.max(0, Math.min(total - 1, Math.trunc(Number(frameIndex) || 0)))
  const item = offsets[index]
  if (!item) return null

  const frameType = getPerceiverType()
  const payload = new Uint8Array(await file.slice(item.offset, item.offset + item.length).arrayBuffer())

  let frame: Record<string, unknown>
  try {
    frame = frameType.decode(payload) as unknown as Record<string, unknown>
  } catch {
    return null
  }

  const rgb = frame.rgbFrame as { data?: Uint8Array; format?: unknown; width?: unknown; height?: unknown } | undefined
  const identifier = frame.frameIdentifier as { frameNumber?: unknown; timestampNs?: unknown } | undefined
  const isJpeg = Number(rgb?.format ?? 0) === 4 && Boolean(rgb?.data?.length)
  const dataUrl = isJpeg
    ? URL.createObjectURL(new Blob([new Uint8Array(rgb!.data as Uint8Array)], { type: 'image/jpeg' }))
    : null

  return {
    index,
    frameCount: total,
    frameNumber: finiteNumber(identifier?.frameNumber),
    timestampNs: timestampString(identifier?.timestampNs),
    width: finiteNumber(rgb?.width),
    height: finiteNumber(rgb?.height),
    dataUrl,
  }
}

// --- Segmentation overlay (browser mode) --------------------------------

let segmentationType: protobuf.Type | null = null

function getSegmentationType(): protobuf.Type {
  if (segmentationType) return segmentationType

  const schema = [
    'syntax = "proto3";',
    'package bayesmech.vision;',
    stripProtoHeader(primitivesProto),
    stripProtoHeader(spatialProto),
    stripProtoHeader(perceiverProto),
    stripProtoHeader(segmentationProto),
  ].join('\n\n')

  const root = new protobuf.Root()
  protobuf.parse(schema, root, { keepCase: false })
  root.resolveAll()
  segmentationType = root.lookupType('bayesmech.vision.SegmentationResponse')
  return segmentationType
}

type SegmentationIndex = {
  byFrame: Map<number, FrameIndexEntry>
  sortedFrames: number[]
  labels: string[]
}

const segIndexCacheByFile = new WeakMap<File, Promise<SegmentationIndex>>()

async function buildSegmentationIndex(file: File): Promise<SegmentationIndex> {
  const { offsets } = await readFrameIndex(file)
  const segType = getSegmentationType()
  const byFrame = new Map<number, FrameIndexEntry>()
  const labels = new Map<string, string>()

  for (const item of offsets) {
    const payload = new Uint8Array(await file.slice(item.offset, item.offset + item.length).arrayBuffer())
    let response: Record<string, unknown>
    try {
      response = segType.decode(payload) as unknown as Record<string, unknown>
    } catch {
      continue
    }
    const identifier = response.frameIdentifier as { frameNumber?: unknown } | undefined
    byFrame.set(finiteNumber(identifier?.frameNumber), item)
    const masks = (response.masks as Array<Record<string, unknown>> | undefined) ?? []
    for (const mask of masks) {
      const label = String(mask.label ?? '').trim().replace(/\s+/g, ' ')
      if (label) labels.set(label.toLowerCase(), label)
    }
  }

  const sortedFrames = [...byFrame.keys()].sort((a, b) => a - b)
  return { byFrame, sortedFrames, labels: [...labels.values()].sort((a, b) => a.localeCompare(b)) }
}

function cachedSegmentationIndex(file: File): Promise<SegmentationIndex> {
  let cached = segIndexCacheByFile.get(file)
  if (!cached) {
    cached = buildSegmentationIndex(file)
    segIndexCacheByFile.set(file, cached)
  }
  return cached
}

// Largest indexed frame <= target (clamped into range), or null when empty.
function nearestSegFrame(sortedFrames: number[], target: number): number | null {
  if (sortedFrames.length === 0) return null
  if (target <= sortedFrames[0]) return sortedFrames[0]
  const last = sortedFrames.length - 1
  if (target >= sortedFrames[last]) return sortedFrames[last]
  let lo = 0
  let hi = last
  while (lo < hi) {
    const mid = (lo + hi + 1) >> 1
    if (sortedFrames[mid] <= target) lo = mid
    else hi = mid - 1
  }
  return sortedFrames[lo]
}

function bytesToBase64(bytes: Uint8Array): string {
  let binary = ''
  const chunk = 0x8000
  for (let i = 0; i < bytes.length; i += chunk) {
    binary += String.fromCharCode(...bytes.subarray(i, i + chunk))
  }
  return btoa(binary)
}

// Decoded masks for the segmentation response nearest (<=) a video frame number.
// Mirrors the Electron main-process readSegmentationMasks for browser mode.
export async function readBrowserSegmentationMasks(file: File, frameNumber: number): Promise<SegMask[] | null> {
  const { byFrame, sortedFrames } = await cachedSegmentationIndex(file)
  const targetFrame = nearestSegFrame(sortedFrames, Math.trunc(Number(frameNumber) || 0))
  if (targetFrame == null) return []
  const item = byFrame.get(targetFrame)
  if (!item) return []

  const segType = getSegmentationType()
  const payload = new Uint8Array(await file.slice(item.offset, item.offset + item.length).arrayBuffer())
  let response: Record<string, unknown>
  try {
    response = segType.decode(payload) as unknown as Record<string, unknown>
  } catch {
    return []
  }

  const masks = (response.masks as Array<Record<string, unknown>> | undefined) ?? []
  return masks
    .filter((mask) => (mask.maskData as Uint8Array | undefined)?.length)
    .map((mask) => ({
      objectId: finiteNumber(mask.objectId),
      label: String(mask.label ?? ''),
      maskData: bytesToBase64(mask.maskData as Uint8Array),
    }))
}

export async function readBrowserSegmentationLabels(file: File): Promise<string[]> {
  const { labels } = await cachedSegmentationIndex(file)
  return labels
}

type BrowserMotionCaptureIndex = {
  byFrame: Map<number, { item: FrameIndexEntry; heatmapIndex: number }>
  sortedFrames: number[]
  tracks: Array<Record<string, unknown>>
  segmentationTracks: Array<Record<string, unknown>>
}

const motionCaptureIndexCacheByFile = new WeakMap<File, Promise<BrowserMotionCaptureIndex>>()

function browserMotionCaptureTrails(
  sourceTracks: Array<Record<string, unknown>>,
  heatmapIndex: number,
  kind: MotionCaptureTrack['kind'],
): MotionCaptureTrack[] {
  const tailStart = Math.max(0, heatmapIndex - 30)
  return sourceTracks.flatMap((track) => {
    const positions = (track.positions as Array<Record<string, unknown>> | undefined) ?? []
    const points = positions
      .map((point) => ({
        frameIndex: finiteNumber(point.frameIdx),
        cx: finiteNumber(point.cx),
        cy: finiteNumber(point.cy),
        interpolated: Boolean(point.interpolated),
      }))
      .filter((point) => point.frameIndex >= tailStart && point.frameIndex <= heatmapIndex)
      .sort((left, right) => left.frameIndex - right.frameIndex)
    if (!points.length) return []
    return [{
      trackId: finiteNumber(track.trackId),
      label: String(track.label ?? '').trim(),
      kind,
      detectedFrames: finiteNumber(track.detectedFrames),
      totalPositions: finiteNumber(track.totalPositions),
      presenceFraction: finiteNumber(track.presenceFraction),
      points,
    }]
  })
}

async function buildBrowserMotionCaptureIndex(file: File): Promise<BrowserMotionCaptureIndex> {
  const { offsets } = await readFrameIndex(file, 512 * 1024 * 1024)
  const type = getMotionCaptureType()
  const byFrame = new Map<number, { item: FrameIndexEntry; heatmapIndex: number }>()
  let tracks: Array<Record<string, unknown>> = []
  let segmentationTracks: Array<Record<string, unknown>> = []
  let heatmapIndex = 0
  for (const item of offsets) {
    const payload = new Uint8Array(await file.slice(item.offset, item.offset + item.length).arrayBuffer())
    let response: Record<string, unknown>
    try {
      response = type.decode(payload) as unknown as Record<string, unknown>
    } catch {
      continue
    }
    const responseTracks = (response.tracks as Array<Record<string, unknown>> | undefined) ?? []
    const responseSegmentationTracks = (
      response.segmentationTrajectories as Array<Record<string, unknown>> | undefined
    ) ?? []
    const isSummary = responseTracks.length > 0
      || responseSegmentationTracks.length > 0
      || finiteNumber(response.totalFrames) > 0
    if (isSummary) {
      if (responseTracks.length) tracks = responseTracks
      if (responseSegmentationTracks.length) segmentationTracks = responseSegmentationTracks
      continue
    }
    const identifier = response.frameIdentifier as Record<string, unknown> | undefined
    byFrame.set(finiteNumber(identifier?.frameNumber), { item, heatmapIndex })
    heatmapIndex += 1
  }
  return {
    byFrame,
    sortedFrames: [...byFrame.keys()].sort((left, right) => left - right),
    tracks,
    segmentationTracks,
  }
}

function cachedBrowserMotionCaptureIndex(file: File): Promise<BrowserMotionCaptureIndex> {
  let cached = motionCaptureIndexCacheByFile.get(file)
  if (!cached) {
    cached = buildBrowserMotionCaptureIndex(file)
    motionCaptureIndexCacheByFile.set(file, cached)
  }
  return cached
}

export async function readBrowserMotionCapture(
  file: File,
  frameNumber: number,
): Promise<MotionCaptureOverlay | null> {
  const index = await cachedBrowserMotionCaptureIndex(file)
  const targetFrame = nearestSegFrame(index.sortedFrames, Math.trunc(Number(frameNumber) || 0))
  if (targetFrame == null) return null
  const indexedFrame = index.byFrame.get(targetFrame)
  if (!indexedFrame) return null
  const payload = new Uint8Array(
    await file.slice(indexedFrame.item.offset, indexedFrame.item.offset + indexedFrame.item.length).arrayBuffer(),
  )
  const response = getMotionCaptureType().decode(payload) as unknown as Record<string, unknown>
  const heatmap = response.heatmap as Record<string, unknown> | undefined
  const heatmapBytes = heatmap?.heatmapData as Uint8Array | undefined
  return {
    frameNumber: targetFrame,
    heatmapIndex: indexedFrame.heatmapIndex,
    heatmapData: heatmapBytes?.length ? bytesToBase64(heatmapBytes) : null,
    maxMotionRaw: finiteNumber(heatmap?.maxMotionRaw),
    stabilizationMethod: finiteNumber(response.methodUsed),
    stabilizationConfidence: finiteNumber(response.stabilizationConfidence),
    tracks: [
      ...browserMotionCaptureTrails(index.tracks, indexedFrame.heatmapIndex, 'motion'),
      ...browserMotionCaptureTrails(index.segmentationTracks, indexedFrame.heatmapIndex, 'segmentation'),
    ],
  }
}

function browserToolArguments(value: unknown): Record<string, unknown> {
  const raw = String(value || '').trim()
  if (!raw) return {}
  try {
    const parsed: unknown = JSON.parse(raw)
    return parsed && typeof parsed === 'object' && !Array.isArray(parsed)
      ? parsed as Record<string, unknown>
      : { value: parsed }
  } catch {
    return { raw }
  }
}

export async function readBrowserChatThread(
  gensparkFile?: File,
  chatFile?: File,
): Promise<ChatThread> {
  const types = getInsightgenTypes()
  let analysis: ChatThread['analysis'] = null
  let chatHistory: Record<string, unknown> | null = null

  if (gensparkFile) {
    const payload = new Uint8Array(await gensparkFile.arrayBuffer())
    const response = types.genspark.toObject(types.genspark.decode(payload), {
      defaults: true,
      longs: String,
    }) as Record<string, unknown>
    const summary = response.summary as Record<string, unknown> | null
    const parameters = (summary?.parameters as Array<Record<string, unknown>> | undefined) ?? []
    const responseTurns = (response.turns as Array<Record<string, unknown>> | undefined) ?? []
    const turns = responseTurns.map((turn) => ({
      text: String(turn.text || ''),
      toolCalls: (
        (turn.toolCalls as Array<Record<string, unknown>> | undefined) ?? []
      ).map((call) => ({
        name: String(call.toolName || ''),
        arguments: browserToolArguments(call.argumentsJson),
        result: String(call.result || ''),
      })),
    }))
    if (
      (summary && (summary.title || summary.text || parameters.length))
      || turns.length
    ) {
      analysis = {
        title: String(summary?.title || 'Genspark analysis'),
        text: String(summary?.text || ''),
        parameters: parameters.map((parameter) => ({
          name: String(parameter.name || ''),
          value: String(parameter.value || ''),
          unit: String(parameter.unit || ''),
        })),
        turns,
      }
    }
  }

  if (chatFile) {
    const payload = new Uint8Array(await chatFile.arrayBuffer())
    chatHistory = types.chat.toObject(types.chat.decode(payload), {
      defaults: true,
      longs: String,
    }) as Record<string, unknown>
    if (!analysis) {
      const initialTurn = chatHistory.initialTurn as Record<string, unknown> | null
      const raw = String(initialTurn?.text || '').trim()
      if (raw) {
        const titleMatch = /^##\s+([^\n]+)\n*/.exec(raw)
        analysis = {
          title: titleMatch?.[1]?.trim() || 'AI Analysis',
          text: titleMatch ? raw.slice(titleMatch[0].length).trim() : raw,
          parameters: [],
          turns: [],
        }
      }
    }
  }

  const turns = (chatHistory?.turns as Array<Record<string, unknown>> | undefined) ?? []
  return {
    analysis,
    turns: turns
      .filter((turn) => String(turn.text || '').trim())
      .map((turn) => ({
        role: String(turn.role || '').toLowerCase() === 'user' ? 'user' as const : 'assistant' as const,
        text: String(turn.text || ''),
        timestampNs: timestampString(turn.timestampNs),
      })),
  }
}
