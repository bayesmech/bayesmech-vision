import protobuf from 'protobufjs'
import perceiverProto from '../../../proto/perceiver.proto?raw'
import primitivesProto from '../../../proto/primitives.proto?raw'
import spatialProto from '../../../proto/spatial.proto?raw'
import segmentationProto from '../../../proto/segmentation.proto?raw'
import type { ProjectAnalysis, ProjectScanResult, RecordingEntry, SegMask, VisFrame, VisSummary } from '../types'

const FRAME_SIZE_LIMIT = 10 * 1024 * 1024
const MAX_SAMPLE_FRAMES = 96
const MAX_POINTS_PER_FRAME = 220

const BUILT_IN_ANALYSES = [
  { key: 'video', title: 'Video', kind: 'video', source: 'vis' as const },
  { key: 'point-cloud', title: 'Point Clouds', kind: 'geometry', source: 'vis' as const },
  { key: 'surface-planes', title: 'Surface Estimates', kind: 'geometry', source: 'vis' as const },
]

const ANALYSIS_DEFS = [
  { key: 'segmentation', title: 'Segmentation', kind: 'protobuf', suffixes: ['segmentation.pb', 'seg.pb'] },
  { key: 'motioncap', title: 'Motion Capture', kind: 'protobuf', suffixes: ['motioncap.pb', 'motion.pb'] },
  { key: 'idoslam', title: 'Localization and Mapping', kind: 'protobuf', suffixes: ['idoslam.pb'] },
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

async function readFrameIndex(file: File): Promise<{ offsets: FrameIndexEntry[]; errors: number }> {
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

      if (length === 0 || length > FRAME_SIZE_LIMIT || frameStart + 4 + length > file.size) {
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
