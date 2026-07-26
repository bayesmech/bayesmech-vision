const electron = require('electron')
const { spawn } = require('node:child_process')
const fs = require('node:fs')
const http = require('node:http')
const https = require('node:https')
const os = require('node:os')
const path = require('node:path')
const protobuf = require('protobufjs')

const isElectronRuntime = typeof electron !== 'string'
const { app, BrowserWindow, Menu, dialog, ipcMain, shell, nativeImage } = isElectronRuntime ? electron : {}

const APP_NAME = 'BayesMech Vision'
const FRAME_SIZE_LIMIT = 10 * 1024 * 1024
const IDOSLAM_FRAME_SIZE_LIMIT = 512 * 1024 * 1024
const MAX_SAMPLE_FRAMES = 96
const MAX_POINTS_PER_FRAME = 220
// Keep dense VGGT reconstructions detailed in the desktop viewer. The previous
// 120k aggregate cap reduced the 10-frame table-tennis capture from 600k saved
// points to only 12k per frame.
const MAX_WORLDGEN_PREVIEW_POINTS = 1000000
const MAX_WORLDGEN_SPLAT_PREVIEW_POINTS = 1000000
const DEFAULT_WORLDGEN_POINTS_PER_FRAME = 60000
const DEFAULT_RUNNER_ENDPOINT = 'http://127.0.0.1:8787'
const DEFAULT_VGGT_HEALTH_TIMEOUT_MS = 15000
const DEFAULT_VGGT_REQUEST_TIMEOUT_MS = 15 * 60 * 1000

function parseEnvValue(value) {
  const trimmed = value.trim()
  if (
    (trimmed.startsWith('"') && trimmed.endsWith('"')) ||
    (trimmed.startsWith("'") && trimmed.endsWith("'"))
  ) {
    return trimmed.slice(1, -1)
  }
  return trimmed
}

function loadEnvFile(filePath) {
  if (!fs.existsSync(filePath)) return
  const lines = fs.readFileSync(filePath, 'utf8').split(/\r?\n/)
  for (const line of lines) {
    const cleaned = line.trim()
    if (!cleaned || cleaned.startsWith('#')) continue
    const match = /^(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$/.exec(cleaned)
    if (!match) continue
    const [, key, rawValue] = match
    if (process.env[key] === undefined) process.env[key] = parseEnvValue(rawValue)
  }
}

function loadLocalEnv() {
  const candidates = [
    path.resolve(__dirname, '..', '..', '.env'),
    path.resolve(__dirname, '..', '.env'),
    path.resolve(process.cwd(), '.env'),
  ]
  for (const candidate of candidates) loadEnvFile(candidate)
}

loadLocalEnv()

const SKIPPED_DIRS = new Set([
  '.git',
  '.gradle',
  '.idea',
  '.pytest_cache',
  '.venv',
  '__pycache__',
  'build',
  'dist',
  'node_modules',
])

const BUILT_IN_ANALYSES = [
  { key: 'video', title: 'Video', kind: 'video', source: 'vis' },
  { key: 'surface-planes', title: 'Surface Estimates', kind: 'geometry', source: 'vis' },
  { key: 'sensors', title: 'Sensor Data', kind: 'sensors', source: 'vis' },
]

const ANALYSIS_DEFS = [
  {
    key: 'segmentation',
    title: 'Segmentation',
    kind: 'protobuf',
    suffixes: ['segmentation.pb', 'seg.pb'],
  },
  {
    key: 'motioncap',
    title: 'Motion Capture',
    kind: 'protobuf',
    suffixes: ['motioncap.pb', 'motion.pb'],
  },
  {
    key: 'idoslam',
    title: 'Map Generation',
    kind: 'protobuf',
    suffixes: ['idoslam.pb'],
  },
  {
    key: 'genspark',
    title: 'AI Analysis',
    kind: 'protobuf',
    suffixes: ['genspark.pb'],
  },
  {
    key: 'chat',
    title: 'Follow-up Chat',
    kind: 'protobuf',
    suffixes: ['chat.pb'],
  },
  {
    key: 'reconstruction',
    title: '3D Reconstruction',
    kind: 'protobuf',
    suffixes: ['reconstruct.pb', 'recon.pb'],
  },
  {
    key: 'snookestown',
    title: 'Snookestown',
    kind: 'protobuf',
    suffixes: ['snook.pb'],
  },
  {
    key: 'pongtown',
    title: 'Pongtown',
    kind: 'protobuf',
    suffixes: ['pongtown.pb'],
  },
]

let mainWindow = null
let perceiverType = null
let segmentationType = null
let motionCaptureType = null
let vggtResponseType = null
let gensparkResponseType = null
let chatHistoryType = null
let idoSlamType = null
const worldgenSplatDestinations = new Map()

function appIconPath() {
  const candidates = [
    path.join(__dirname, 'assets', 'bayesmech-icon-512.png'),
    path.join(__dirname, 'assets', 'bayesmech-logo.png'),
    path.join(__dirname, '..', 'public', 'logo.png'),
    path.join(__dirname, '..', 'dist', 'logo.png'),
  ]
  return candidates.find((candidate) => fs.existsSync(candidate))
}

function appIcon() {
  const iconPath = appIconPath()
  if (!iconPath) return undefined
  const icon = nativeImage?.createFromPath(iconPath)
  return icon && !icon.isEmpty() ? icon : iconPath
}

function createWindow() {
  const icon = appIcon()
  mainWindow = new BrowserWindow({
    width: 1500,
    height: 980,
    minWidth: 1040,
    minHeight: 680,
    backgroundColor: '#101114',
    title: APP_NAME,
    icon,
    autoHideMenuBar: true,
    webPreferences: {
      preload: path.join(__dirname, 'preload.cjs'),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: false,
    },
  })
  if (icon) mainWindow.setIcon(icon)
  mainWindow.setMenuBarVisibility(false)

  if (process.env.VITE_DEV_SERVER_URL) {
    mainWindow.loadURL(process.env.VITE_DEV_SERVER_URL)
  } else {
    mainWindow.loadFile(path.join(__dirname, '..', 'dist', 'index.html'))
  }
}

function installMenu() {
  Menu.setApplicationMenu(null)
}

function findRepoRoot() {
  const candidates = [
    path.resolve(__dirname, '..', '..'),
    path.resolve(process.cwd(), '..'),
    process.cwd(),
    app?.getAppPath?.(),
  ].filter(Boolean)
  for (const candidate of candidates) {
    if (fs.existsSync(path.join(candidate, 'proto', 'perceiver.proto'))) {
      return candidate
    }
  }
  return path.resolve(__dirname, '..', '..')
}

function getPerceiverType() {
  if (perceiverType) return perceiverType

  const protoDir = path.join(findRepoRoot(), 'proto')
  const root = new protobuf.Root()
  root.resolvePath = (_origin, target) => path.join(protoDir, target)
  root.loadSync(['perceiver.proto'], { keepCase: false })
  root.resolveAll()
  perceiverType = root.lookupType('bayesmech.vision.PerceiverDataFrame')
  return perceiverType
}

function getSegmentationType() {
  if (segmentationType) return segmentationType

  const protoDir = path.join(findRepoRoot(), 'proto')
  const root = new protobuf.Root()
  root.resolvePath = (_origin, target) => path.join(protoDir, target)
  root.loadSync(['segmentation.proto'], { keepCase: false })
  root.resolveAll()
  segmentationType = root.lookupType('bayesmech.vision.SegmentationResponse')
  return segmentationType
}

function getMotionCaptureType() {
  if (motionCaptureType) return motionCaptureType

  const protoDir = path.join(findRepoRoot(), 'proto')
  const root = new protobuf.Root()
  root.resolvePath = (_origin, target) => path.join(protoDir, target)
  root.loadSync(['motioncap.proto'], { keepCase: false })
  root.resolveAll()
  motionCaptureType = root.lookupType('bayesmech.vision.MotionCaptureResponse')
  return motionCaptureType
}

function getVggtResponseType() {
  if (vggtResponseType) return vggtResponseType

  const protoDir = path.join(findRepoRoot(), 'proto')
  const root = new protobuf.Root()
  root.resolvePath = (_origin, target) => path.join(protoDir, target)
  root.loadSync(['vggt.proto'], { keepCase: false })
  root.resolveAll()
  vggtResponseType = root.lookupType('bayesmech.vision.VggtInferenceResponse')
  return vggtResponseType
}

function getInsightgenTypes() {
  if (gensparkResponseType && chatHistoryType) {
    return { gensparkResponseType, chatHistoryType }
  }

  const protoDir = path.join(findRepoRoot(), 'proto')
  const root = new protobuf.Root()
  root.resolvePath = (_origin, target) => path.join(protoDir, target)
  root.loadSync(['insightgen.proto'], { keepCase: false })
  root.resolveAll()
  gensparkResponseType = root.lookupType('bayesmech.vision.GensparkResponse')
  chatHistoryType = root.lookupType('bayesmech.vision.ChatHistory')
  return { gensparkResponseType, chatHistoryType }
}

function getIdoSlamType() {
  if (idoSlamType) return idoSlamType

  const protoDir = path.join(findRepoRoot(), 'proto')
  const root = new protobuf.Root()
  root.resolvePath = (_origin, target) => path.join(protoDir, target)
  root.loadSync(['idoslam.proto'], { keepCase: false })
  root.resolveAll()
  idoSlamType = root.lookupType('bayesmech.vision.IdoSlamResponse')
  return idoSlamType
}

function byteSizeLabel(bytes) {
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

function safeStat(filePath) {
  try {
    return fs.statSync(filePath)
  } catch {
    return null
  }
}

function walkForVisFiles(rootPath) {
  const results = []
  const stack = [rootPath]

  while (stack.length) {
    const dir = stack.pop()
    let entries = []
    try {
      entries = fs.readdirSync(dir, { withFileTypes: true })
    } catch {
      continue
    }

    for (const entry of entries) {
      const entryPath = path.join(dir, entry.name)
      if (entry.isDirectory()) {
        if (!SKIPPED_DIRS.has(entry.name)) stack.push(entryPath)
        continue
      }
      if (entry.isFile() && entry.name.endsWith('.vis.pb')) {
        results.push(entryPath)
      }
    }
  }

  return results.sort((a, b) => a.localeCompare(b))
}

function artifactFor(dir, baseName, projectPath, def, suffix) {
  const artifactPath = path.join(dir, `${baseName}.${suffix}`)
  const stat = safeStat(artifactPath)
  if (!stat?.isFile()) return null

  return {
    key: def.key,
    title: def.title,
    kind: def.kind,
    source: 'artifact',
    suffix,
    path: artifactPath,
    relativePath: path.relative(projectPath, artifactPath),
    sizeBytes: stat.size,
    sizeLabel: byteSizeLabel(stat.size),
    modifiedMs: stat.mtimeMs,
  }
}

function worldgenArtifactsFor(dir, baseName, projectPath) {
  let entries = []
  try {
    entries = fs.readdirSync(dir, { withFileTypes: true })
  } catch {
    return []
  }

  const canonicalName = `${baseName}.vggt.pb`
  const candidates = entries
    .filter((entry) => entry.isFile() && (
      entry.name === canonicalName ||
      (entry.name.startsWith(`${baseName}.`) && entry.name.endsWith('.vggt.pb'))
    ))
    .map((entry) => {
      const artifactPath = path.join(dir, entry.name)
      const stat = safeStat(artifactPath)
      return stat?.isFile() ? { entry, artifactPath, stat } : null
    })
    .filter(Boolean)
    .sort((a, b) => b.stat.mtimeMs - a.stat.mtimeMs)

  // New recordings have exactly one recording-level World Modeling stream. Until a
  // legacy marker-pair project is migrated by its next generation, expose only
  // its most recent old file as a compatibility fallback instead of showing a
  // separate analysis entry for every marker pair.
  const selected = candidates.find((candidate) => candidate.entry.name === canonicalName) ?? candidates[0]
  if (!selected) return []
  return [{
    key: 'worldgen',
    title: 'World Modeling',
    kind: 'worldgen',
    source: 'artifact',
    suffix: selected.entry.name.slice(baseName.length + 1),
    path: selected.artifactPath,
    relativePath: path.relative(projectPath, selected.artifactPath),
    sizeBytes: selected.stat.size,
    sizeLabel: byteSizeLabel(selected.stat.size),
    modifiedMs: selected.stat.mtimeMs,
  }]
}

function analysesForVis(projectPath, visPath) {
  const dir = path.dirname(visPath)
  const baseName = path.basename(visPath).slice(0, -'.vis.pb'.length)
  const analyses = BUILT_IN_ANALYSES.map((analysis) => ({
    ...analysis,
    path: visPath,
    relativePath: path.relative(projectPath, visPath),
  }))

  for (const def of ANALYSIS_DEFS) {
    for (const suffix of def.suffixes) {
      const artifact = artifactFor(dir, baseName, projectPath, def, suffix)
      if (artifact) {
        analyses.push(artifact)
        break
      }
    }
  }

  analyses.push(...worldgenArtifactsFor(dir, baseName, projectPath))

  return analyses
}

function scanProject(projectPath) {
  if (!projectPath || typeof projectPath !== 'string') {
    return { error: 'Project path is required.', rootPath: '', name: '', recordings: [] }
  }

  const rootPath = path.resolve(projectPath)
  const stat = safeStat(rootPath)
  if (!stat?.isDirectory()) {
    return {
      error: `Not a directory: ${rootPath}`,
      rootPath,
      name: path.basename(rootPath),
      recordings: [],
    }
  }

  const visFiles = walkForVisFiles(rootPath)
  const recordings = visFiles.map((visPath, index) => {
    const visStat = safeStat(visPath)
    const dir = path.dirname(visPath)
    const baseName = path.basename(visPath).slice(0, -'.vis.pb'.length)
    const folderName = path.basename(dir)

    return {
      id: `${baseName}:${index}`,
      name: folderName === baseName ? baseName : `${folderName}/${baseName}`,
      fileStem: baseName,
      path: visPath,
      directoryPath: dir,
      relativePath: path.relative(rootPath, visPath),
      sizeBytes: visStat?.size ?? 0,
      sizeLabel: byteSizeLabel(visStat?.size ?? 0),
      modifiedMs: visStat?.mtimeMs ?? 0,
      analyses: analysesForVis(rootPath, visPath),
    }
  })

  return {
    rootPath,
    name: path.basename(rootPath),
    recordings,
    error: recordings.length ? undefined : 'This project does not contain any .vis.pb files.',
  }
}

function commonAncestor(paths) {
  if (!paths.length) return process.cwd()
  const splitPaths = paths.map((item) => path.resolve(item).split(path.sep))
  const first = splitPaths[0]
  let cursor = 0
  while (cursor < first.length && splitPaths.every((parts) => parts[cursor] === first[cursor])) {
    cursor += 1
  }
  const common = first.slice(0, cursor).join(path.sep)
  return common || path.parse(path.resolve(paths[0])).root
}

function scanVisFiles(filePaths) {
  const visFiles = [...new Set(filePaths)]
    .filter((filePath) => filePath.endsWith('.vis.pb') && safeStat(filePath)?.isFile())
    .sort((a, b) => a.localeCompare(b))
  const rootPath = commonAncestor(visFiles.map((filePath) => path.dirname(filePath)))

  const recordings = visFiles.map((visPath, index) => {
    const visStat = safeStat(visPath)
    const dir = path.dirname(visPath)
    const baseName = path.basename(visPath).slice(0, -'.vis.pb'.length)
    const folderName = path.basename(dir)

    return {
      id: `${baseName}:${index}`,
      name: folderName === baseName ? baseName : `${folderName}/${baseName}`,
      fileStem: baseName,
      path: visPath,
      directoryPath: dir,
      relativePath: path.relative(rootPath, visPath),
      sizeBytes: visStat?.size ?? 0,
      sizeLabel: byteSizeLabel(visStat?.size ?? 0),
      modifiedMs: visStat?.mtimeMs ?? 0,
      analyses: analysesForVis(rootPath, visPath),
    }
  })

  return {
    rootPath,
    name: path.basename(rootPath) || 'Selected files',
    recordings,
    error: recordings.length ? undefined : 'Select one or more .vis.pb files.',
  }
}

function readFrameIndex(filePath, sizeLimit = FRAME_SIZE_LIMIT) {
  const stat = fs.statSync(filePath)
  const offsets = []
  const header = Buffer.allocUnsafe(4)
  const fd = fs.openSync(filePath, 'r')
  let position = 0
  let errors = 0

  try {
    while (position + 4 <= stat.size) {
      const bytesRead = fs.readSync(fd, header, 0, 4, position)
      if (bytesRead < 4) break

      const length = header.readUInt32BE(0)
      if (length === 0 || length > sizeLimit || position + 4 + length > stat.size) {
        errors += 1
        position += 1
        continue
      }

      offsets.push({ offset: position + 4, length })
      position += 4 + length
    }
  } finally {
    fs.closeSync(fd)
  }

  return { offsets, errors }
}

function buildSampleIndexes(count) {
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

function finiteNumber(value, fallback = 0) {
  const number = Number(value)
  return Number.isFinite(number) ? number : fallback
}

function vector3(value) {
  if (!value) return { x: 0, y: 0, z: 0 }
  return {
    x: finiteNumber(value.x),
    y: finiteNumber(value.y),
    z: finiteNumber(value.z),
  }
}

function quaternion(value) {
  if (!value) return { x: 0, y: 0, z: 0, w: 1 }
  return {
    x: finiteNumber(value.x),
    y: finiteNumber(value.y),
    z: finiteNumber(value.z),
    w: finiteNumber(value.w, 1),
  }
}

function pose(value) {
  if (!value) return null
  return {
    position: vector3(value.position),
    rotation: quaternion(value.rotation),
  }
}

function timestampString(value) {
  if (value == null) return '0'
  if (typeof value === 'number') return String(Math.trunc(value))
  if (typeof value === 'bigint') return value.toString()
  if (typeof value.toString === 'function') return value.toString()
  return String(value)
}

function timestampDurationSeconds(first, last) {
  try {
    const delta = BigInt(last) - BigInt(first)
    if (delta <= 0n) return 0
    return Number(delta / 1000000n) / 1000
  } catch {
    return 0
  }
}

function timestampDeltaSeconds(first, last) {
  try {
    const delta = BigInt(last) - BigInt(first)
    return Number(delta) / 1000000000
  } catch {
    return 0
  }
}

function samplePointCloud(pointCloud) {
  if (!Array.isArray(pointCloud) || pointCloud.length === 0) return []
  const stride = Math.max(1, Math.ceil(pointCloud.length / MAX_POINTS_PER_FRAME))
  const points = []

  for (let i = 0; i < pointCloud.length && points.length < MAX_POINTS_PER_FRAME; i += stride) {
    const tracked = pointCloud[i]
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

function samplePlanes(planes) {
  if (!Array.isArray(planes) || planes.length === 0) return []

  return planes.slice(0, 24).map((plane) => ({
    type: finiteNumber(plane.type),
    extentX: finiteNumber(plane.extentX),
    extentZ: finiteNumber(plane.extentZ),
    centerPose: pose(plane.centerPose),
    polygon: Array.isArray(plane.polygon) ? plane.polygon.slice(0, 16).map(vector3) : [],
  }))
}

// Decode a frame's RGB payload to a browser-displayable image. Only JPEG
// (RgbFormat 4) is directly usable as a data URL; other formats return null.
function rgbFrameImage(frame) {
  const rgb = frame?.rgbFrame
  if (!rgb?.data?.length) return null
  const format = Number(rgb.format ?? 0)
  if (format !== 4) return null

  const data = Buffer.from(rgb.data)
  return {
    dataUrl: `data:image/jpeg;base64,${data.toString('base64')}`,
    width: finiteNumber(rgb.width),
    height: finiteNumber(rgb.height),
  }
}

function rgbFrameJpeg(frame) {
  const rgb = frame?.rgbFrame
  if (!rgb?.data?.length) return null
  const format = Number(rgb.format ?? 0)
  if (format !== 4) return null
  return Buffer.from(rgb.data)
}

function rgbPreviewFromFrame(frame) {
  const image = rgbFrameImage(frame)
  if (!image) return null
  return { ...image, frameNumber: finiteNumber(frame?.frameIdentifier?.frameNumber) }
}

function depthStatsFromFrame(frame) {
  const depth = frame?.depthFrame
  if (!depth?.data?.length) return null

  const data = Buffer.from(depth.data)
  const format = Number(depth.format ?? 0)
  const stride = Math.max(1, Math.floor(data.length / 8000))
  let min = Number.POSITIVE_INFINITY
  let max = 0
  let count = 0

  if (format === 1) {
    const step = Math.max(2, stride + (stride % 2))
    for (let offset = 0; offset + 1 < data.length; offset += step) {
      const value = data.readUInt16LE(offset) / 1000
      if (value > 0 && Number.isFinite(value)) {
        min = Math.min(min, value)
        max = Math.max(max, value)
        count += 1
      }
    }
  } else if (format === 2) {
    const step = Math.max(4, stride + ((4 - (stride % 4)) % 4))
    for (let offset = 0; offset + 3 < data.length; offset += step) {
      const value = data.readFloatLE(offset)
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

function expandBounds(bounds, point) {
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

function readVisSummary(filePath) {
  if (!filePath || !filePath.endsWith('.vis.pb')) {
    throw new Error('Expected a .vis.pb file.')
  }

  const resolved = path.resolve(filePath)
  const stat = fs.statSync(resolved)
  const { offsets, errors } = readFrameIndex(resolved)
  const sampleIndexes = buildSampleIndexes(offsets.length)
  const frameType = getPerceiverType()
  const fd = fs.openSync(resolved, 'r')
  const samples = []
  const devices = new Set()
  let firstTimestampNs = '0'
  let lastTimestampNs = '0'
  let firstFrameNumber = 0
  let lastFrameNumber = 0
  let rgbPreview = null
  let depthStats = null
  let sampledPointCount = 0
  let sampledPlaneCount = 0
  let framesWithPointCloud = 0
  let framesWithPlanes = 0
  let framesWithRgb = 0
  let framesWithDepth = 0
  let bounds = {
    min: { x: Number.POSITIVE_INFINITY, y: Number.POSITIVE_INFINITY, z: Number.POSITIVE_INFINITY },
    max: { x: Number.NEGATIVE_INFINITY, y: Number.NEGATIVE_INFINITY, z: Number.NEGATIVE_INFINITY },
  }

  try {
    for (const index of sampleIndexes) {
      const item = offsets[index]
      if (!item) continue
      const payload = Buffer.allocUnsafe(item.length)
      fs.readSync(fd, payload, 0, item.length, item.offset)

      let frame
      try {
        frame = frameType.decode(payload)
      } catch {
        continue
      }

      const identifier = frame.frameIdentifier ?? {}
      const timestampNs = timestampString(identifier.timestampNs)
      const frameNumber = finiteNumber(identifier.frameNumber)
      const deviceId = String(identifier.deviceId ?? '').trim()
      if (deviceId) devices.add(deviceId)
      if (samples.length === 0) {
        firstTimestampNs = timestampNs
        firstFrameNumber = frameNumber
      }
      lastTimestampNs = timestampNs
      lastFrameNumber = frameNumber

      const cameraPose = pose(frame.cameraPose)
      if (cameraPose) bounds = expandBounds(bounds, cameraPose.position)

      const geometry = frame.inferredGeometry ?? {}
      const points = samplePointCloud(geometry.pointCloud)
      const planes = samplePlanes(geometry.planes)
      sampledPointCount += Array.isArray(geometry.pointCloud) ? geometry.pointCloud.length : 0
      sampledPlaneCount += Array.isArray(geometry.planes) ? geometry.planes.length : 0
      if (points.length) framesWithPointCloud += 1
      if (planes.length) framesWithPlanes += 1
      if (frame.rgbFrame?.data?.length) framesWithRgb += 1
      if (frame.depthFrame?.data?.length) framesWithDepth += 1

      for (const point of points) bounds = expandBounds(bounds, point)
      for (const plane of planes) {
        if (plane.centerPose) bounds = expandBounds(bounds, plane.centerPose.position)
      }

      if (!rgbPreview) rgbPreview = rgbPreviewFromFrame(frame)
      if (!depthStats) depthStats = depthStatsFromFrame(frame)

      samples.push({
        sampleIndex: index,
        frameNumber,
        timestampNs,
        cameraPose,
        points,
        planes,
        rgb: frame.rgbFrame
          ? {
              width: finiteNumber(frame.rgbFrame.width),
              height: finiteNumber(frame.rgbFrame.height),
              format: finiteNumber(frame.rgbFrame.format),
              bytes: finiteNumber(frame.rgbFrame.data?.length),
            }
          : null,
        depth: frame.depthFrame
          ? {
              width: finiteNumber(frame.depthFrame.width),
              height: finiteNumber(frame.depthFrame.height),
              format: finiteNumber(frame.depthFrame.format),
              bytes: finiteNumber(frame.depthFrame.data?.length),
            }
          : null,
        userTextInput: String(frame.userTextInput ?? ''),
      })
    }
  } finally {
    fs.closeSync(fd)
  }

  const hasFiniteBounds = Number.isFinite(bounds.min.x) && Number.isFinite(bounds.max.x)
  return {
    path: resolved,
    fileName: path.basename(resolved),
    sizeBytes: stat.size,
    sizeLabel: byteSizeLabel(stat.size),
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

// Frame-offset index cache so random-access seeking does not rescan the whole
// file on every request. Keyed by resolved path; invalidated on size/mtime change.
const frameIndexCache = new Map()

function getFrameOffsets(resolved) {
  const stat = fs.statSync(resolved)
  const key = `${stat.size}:${stat.mtimeMs}`
  const cached = frameIndexCache.get(resolved)
  if (cached && cached.key === key) return cached.offsets
  const { offsets } = readFrameIndex(resolved)
  frameIndexCache.set(resolved, { key, offsets })
  return offsets
}

function decodedFrameAt(resolved, offsets, frameIndex, fd) {
  const item = offsets[frameIndex]
  if (!item) return null
  const payload = Buffer.allocUnsafe(item.length)
  fs.readSync(fd, payload, 0, item.length, item.offset)
  try {
    return getPerceiverType().decode(payload)
  } catch {
    return null
  }
}

// Random-access decode of a single frame's RGB image, by frame index into the
// recording (0 .. frameCount-1). Returns null dataUrl when the frame carries no
// decodable RGB (e.g. non-JPEG format or an RGB-less frame).
function readVisFrame(filePath, frameIndex) {
  if (!filePath || !filePath.endsWith('.vis.pb')) {
    throw new Error('Expected a .vis.pb file.')
  }

  const resolved = path.resolve(filePath)
  const offsets = getFrameOffsets(resolved)
  const total = offsets.length
  if (total === 0) return null

  const index = Math.max(0, Math.min(total - 1, Math.trunc(Number(frameIndex) || 0)))
  const item = offsets[index]
  if (!item) return null

  const frameType = getPerceiverType()
  const fd = fs.openSync(resolved, 'r')
  let frame
  try {
    const payload = Buffer.allocUnsafe(item.length)
    fs.readSync(fd, payload, 0, item.length, item.offset)
    frame = frameType.decode(payload)
  } catch {
    return null
  } finally {
    fs.closeSync(fd)
  }

  const image = rgbFrameImage(frame)
  const identifier = frame.frameIdentifier ?? {}
  return {
    index,
    frameCount: total,
    frameNumber: finiteNumber(identifier.frameNumber),
    timestampNs: timestampString(identifier.timestampNs),
    width: image?.width ?? finiteNumber(frame.rgbFrame?.width),
    height: image?.height ?? finiteNumber(frame.rgbFrame?.height),
    dataUrl: image?.dataUrl ?? null,
  }
}

const sensorDataCache = new Map()

function sensorGps(value) {
  if (!value) return null
  return {
    latitude: finiteNumber(value.latitude),
    longitude: finiteNumber(value.longitude),
    altitude: finiteNumber(value.altitude),
    accuracy: finiteNumber(value.accuracy),
    bearing: finiteNumber(value.bearing),
    speed: finiteNumber(value.speed),
    timestampMs: timestampString(value.timestampMs),
  }
}

function sensorSample(frame, index) {
  const identifier = frame.frameIdentifier ?? {}
  const imu = frame.imuData
  return {
    index,
    frameNumber: finiteNumber(identifier.frameNumber),
    timestampNs: timestampString(identifier.timestampNs),
    deviceId: String(identifier.deviceId ?? ''),
    linearAcceleration: imu?.linearAcceleration ? vector3(imu.linearAcceleration) : null,
    angularVelocity: imu?.angularVelocity ? vector3(imu.angularVelocity) : null,
    gravity: imu?.gravity ? vector3(imu.gravity) : null,
    magneticField: imu?.magneticField ? vector3(imu.magneticField) : null,
    cameraPose: pose(frame.cameraPose),
    gps: sensorGps(frame.gpsLocation),
  }
}

function readVisSensors(filePath) {
  if (!filePath || !filePath.endsWith('.vis.pb')) {
    throw new Error('Expected a .vis.pb file.')
  }

  const resolved = path.resolve(filePath)
  const stat = fs.statSync(resolved)
  const cacheKey = `${stat.size}:${stat.mtimeMs}`
  const cached = sensorDataCache.get(resolved)
  if (cached?.key === cacheKey) return cached.value

  const { offsets } = readFrameIndex(resolved)
  const frameType = getPerceiverType()
  const samples = []
  const fd = fs.openSync(resolved, 'r')
  try {
    for (let index = 0; index < offsets.length; index += 1) {
      const frame = decodedFrameAt(resolved, offsets, index, fd)
      if (frame) samples.push(sensorSample(frame, index))
    }
  } finally {
    fs.closeSync(fd)
  }

  const value = { path: resolved, frameCount: offsets.length, samples }
  sensorDataCache.set(resolved, { key: cacheKey, value })
  return value
}

function idoSlamPose(value) {
  return {
    frameIndex: finiteNumber(value?.frameIndex),
    frameNumber: finiteNumber(value?.frameId?.frameNumber),
    timestampNs: timestampString(value?.frameId?.timestampNs),
    position: vector3(value?.worldPose?.position),
    eulerDegrees: vector3(value?.eulerDegrees),
  }
}

function idoSlamWidthEstimate(value) {
  return {
    frameIndex: finiteNumber(value?.frameIndex),
    latitude: finiteNumber(value?.latitude),
    longitude: finiteNumber(value?.longitude),
    widthM: finiteNumber(value?.widthM),
    leftOffsetM: finiteNumber(value?.leftOffsetM),
    rightOffsetM: finiteNumber(value?.rightOffsetM),
    bikeFraction: finiteNumber(value?.bikeFraction),
    method: String(value?.method ?? ''),
  }
}

function idoSlamSummary(response, resolved) {
  const pairDebug = Array.isArray(response.pairDebug) ? response.pairDebug : []
  let correspondenceCount = 0
  let inlierCount = 0
  for (const pair of pairDebug) {
    const correspondences = Array.isArray(pair.correspondences) ? pair.correspondences : []
    correspondenceCount += correspondences.length
    inlierCount += correspondences.filter((item) => item?.inlier).length
  }

  return {
    path: resolved,
    framePoses: (response.framePoses ?? []).map(idoSlamPose),
    refinedFramePoses: (response.refinedFramePoses ?? []).map(idoSlamPose),
    pairwiseMotion: (response.pairwiseMotion ?? []).map((item) => ({
      frameIndex: finiteNumber(item.frameIndex),
      status: String(item.status ?? ''),
      goodMatchCount: finiteNumber(item.goodMatchCount),
      essentialInlierCount: finiteNumber(item.essentialInlierCount),
      essentialInlierRatio: finiteNumber(item.essentialInlierRatio),
      translationMagnitude: finiteNumber(item.translationMagnitude),
      rotationDeg: finiteNumber(item.rotationDeg),
    })),
    planeWidthEstimates: (response.planeWidthEstimates ?? []).map(idoSlamWidthEstimate),
    triangulatedWidthEstimates: (response.triangulatedWidthEstimates ?? []).map(idoSlamWidthEstimate),
    canonicalCenterline: (response.canonicalCenterline ?? []).map((item) => ({
      progressM: finiteNumber(item.progressM),
      centerX: finiteNumber(item.centerX),
      centerY: finiteNumber(item.centerY),
      widthM: finiteNumber(item.widthM),
      leftX: finiteNumber(item.leftX),
      leftY: finiteNumber(item.leftY),
      rightX: finiteNumber(item.rightX),
      rightY: finiteNumber(item.rightY),
    })),
    groundPointCount: Array.isArray(response.groundPoints) ? response.groundPoints.length : 0,
    pairDebugCount: pairDebug.length,
    correspondenceCount,
    inlierCount,
  }
}

function readIdoSlam(filePath) {
  if (!filePath || !filePath.endsWith('.pb')) {
    throw new Error('Expected an IDOSLAM .pb file.')
  }

  const resolved = path.resolve(filePath)
  const { offsets } = readFrameIndex(resolved, IDOSLAM_FRAME_SIZE_LIMIT)
  const item = offsets[offsets.length - 1]
  if (!item) throw new Error(`IDOSLAM file has no protobuf records: ${resolved}`)

  const payload = Buffer.allocUnsafe(item.length)
  const fd = fs.openSync(resolved, 'r')
  try {
    fs.readSync(fd, payload, 0, item.length, item.offset)
  } finally {
    fs.closeSync(fd)
  }
  return idoSlamSummary(getIdoSlamType().decode(payload), resolved)
}

// Segmentation index: frame_number -> file offset of its SegmentationResponse,
// plus a sorted frame list for nearest-frame lookup on random seeks. Cached per
// resolved path; invalidated on size/mtime change.
const segmentationIndexCache = new Map()

function normalizedLabel(label) {
  return String(label ?? '').trim().replace(/\s+/g, ' ')
}

function getSegmentationIndex(resolved) {
  const stat = fs.statSync(resolved)
  const key = `${stat.size}:${stat.mtimeMs}`
  const cached = segmentationIndexCache.get(resolved)
  if (cached && cached.key === key) return cached.index

  const { offsets } = readFrameIndex(resolved)
  const segType = getSegmentationType()
  const byFrame = new Map()
  const labels = new Map()
  const fd = fs.openSync(resolved, 'r')
  try {
    for (const item of offsets) {
      const payload = Buffer.allocUnsafe(item.length)
      fs.readSync(fd, payload, 0, item.length, item.offset)
      let response
      try {
        response = segType.decode(payload)
      } catch {
        continue
      }
      const frameNumber = finiteNumber(response.frameIdentifier?.frameNumber)
      // Later responses for a frame supersede earlier ones.
      byFrame.set(frameNumber, item)
      const masks = Array.isArray(response.masks) ? response.masks : []
      for (const mask of masks) {
        const label = normalizedLabel(mask?.label)
        if (label) labels.set(label.toLowerCase(), label)
      }
    }
  } finally {
    fs.closeSync(fd)
  }

  const sortedFrames = [...byFrame.keys()].sort((a, b) => a - b)
  const index = { byFrame, sortedFrames, labels: [...labels.values()].sort((a, b) => a.localeCompare(b)) }
  segmentationIndexCache.set(resolved, { key, index })
  return index
}

// Largest indexed frame number <= target (binary search), or the smallest frame
// if target precedes all of them. Gives a continuous overlay across seeks even
// when segmentation was sampled sparsely relative to the video frames.
function nearestSegFrame(sortedFrames, target) {
  if (sortedFrames.length === 0) return null
  let lo = 0
  let hi = sortedFrames.length - 1
  if (target <= sortedFrames[0]) return sortedFrames[0]
  if (target >= sortedFrames[hi]) return sortedFrames[hi]
  while (lo < hi) {
    const mid = (lo + hi + 1) >> 1
    if (sortedFrames[mid] <= target) lo = mid
    else hi = mid - 1
  }
  return sortedFrames[lo]
}

// Decoded masks for the segmentation response matching (nearest <=) a video
// frame number. Returns [] when the file has no responses, null when the path is
// invalid. mask_data is returned base64-encoded for the renderer to inflate.
function readSegmentationMasks(filePath, frameNumber) {
  if (!filePath || !filePath.endsWith('.pb')) {
    throw new Error('Expected a segmentation .pb file.')
  }

  const resolved = path.resolve(filePath)
  const { byFrame, sortedFrames } = getSegmentationIndex(resolved)
  const targetFrame = nearestSegFrame(sortedFrames, Math.trunc(Number(frameNumber) || 0))
  if (targetFrame == null) return []
  const item = byFrame.get(targetFrame)
  if (!item) return []

  const segType = getSegmentationType()
  const fd = fs.openSync(resolved, 'r')
  let response
  try {
    const payload = Buffer.allocUnsafe(item.length)
    fs.readSync(fd, payload, 0, item.length, item.offset)
    response = segType.decode(payload)
  } catch {
    return []
  } finally {
    fs.closeSync(fd)
  }

  const masks = Array.isArray(response.masks) ? response.masks : []
  return masks
    .filter((mask) => mask?.maskData?.length)
    .map((mask) => ({
      objectId: finiteNumber(mask.objectId),
      label: String(mask.label ?? ''),
      maskData: Buffer.from(mask.maskData).toString('base64'),
    }))
}

function readSegmentationLabels(filePath) {
  if (!filePath || !filePath.endsWith('.pb')) {
    throw new Error('Expected a segmentation .pb file.')
  }
  const resolved = path.resolve(filePath)
  return getSegmentationIndex(resolved).labels
}

const motionCaptureIndexCache = new Map()

function motionCaptureTrails(tracks, heatmapIndex, kind) {
  const tailStart = Math.max(0, heatmapIndex - 30)
  return (tracks || [])
    .map((track) => {
      const points = (track.positions || [])
        .map((point) => ({
          frameIndex: finiteNumber(point.frameIdx),
          cx: finiteNumber(point.cx),
          cy: finiteNumber(point.cy),
          interpolated: Boolean(point.interpolated),
        }))
        .filter((point) => point.frameIndex >= tailStart && point.frameIndex <= heatmapIndex)
        .sort((left, right) => left.frameIndex - right.frameIndex)
      if (!points.length) return null
      return {
        trackId: finiteNumber(track.trackId),
        label: normalizedLabel(track.label),
        kind,
        detectedFrames: finiteNumber(track.detectedFrames),
        totalPositions: finiteNumber(track.totalPositions),
        presenceFraction: finiteNumber(track.presenceFraction),
        points,
      }
    })
    .filter(Boolean)
}

function getMotionCaptureIndex(resolved) {
  const stat = fs.statSync(resolved)
  const key = `${stat.size}:${stat.mtimeMs}`
  const cached = motionCaptureIndexCache.get(resolved)
  if (cached && cached.key === key) return cached.index

  const { offsets } = readFrameIndex(resolved, IDOSLAM_FRAME_SIZE_LIMIT)
  const type = getMotionCaptureType()
  const byFrame = new Map()
  let tracks = []
  let segmentationTracks = []
  let heatmapIndex = 0
  const fd = fs.openSync(resolved, 'r')
  try {
    for (const item of offsets) {
      const payload = Buffer.allocUnsafe(item.length)
      fs.readSync(fd, payload, 0, item.length, item.offset)
      let response
      try {
        response = type.decode(payload)
      } catch {
        continue
      }
      const responseTracks = Array.isArray(response.tracks) ? response.tracks : []
      const responseSegmentationTracks = Array.isArray(response.segmentationTrajectories)
        ? response.segmentationTrajectories
        : []
      const isSummary = responseTracks.length > 0
        || responseSegmentationTracks.length > 0
        || finiteNumber(response.totalFrames) > 0
      if (isSummary) {
        if (responseTracks.length) tracks = responseTracks
        if (responseSegmentationTracks.length) segmentationTracks = responseSegmentationTracks
        continue
      }
      const frameNumber = finiteNumber(response.frameIdentifier?.frameNumber)
      byFrame.set(frameNumber, { item, heatmapIndex })
      heatmapIndex += 1
    }
  } finally {
    fs.closeSync(fd)
  }
  const index = {
    byFrame,
    sortedFrames: [...byFrame.keys()].sort((a, b) => a - b),
    tracks,
    segmentationTracks,
  }
  motionCaptureIndexCache.set(resolved, { key, index })
  return index
}

function readMotionCapture(filePath, frameNumber) {
  if (!filePath || !filePath.endsWith('.pb')) {
    throw new Error('Expected a motion capture .pb file.')
  }
  const resolved = path.resolve(filePath)
  const index = getMotionCaptureIndex(resolved)
  const targetFrame = nearestSegFrame(index.sortedFrames, Math.trunc(Number(frameNumber) || 0))
  if (targetFrame == null) return null
  const indexedFrame = index.byFrame.get(targetFrame)
  if (!indexedFrame) return null

  const fd = fs.openSync(resolved, 'r')
  let response
  try {
    const payload = Buffer.allocUnsafe(indexedFrame.item.length)
    fs.readSync(fd, payload, 0, indexedFrame.item.length, indexedFrame.item.offset)
    response = getMotionCaptureType().decode(payload)
  } finally {
    fs.closeSync(fd)
  }
  const heatmapBytes = response.heatmap?.heatmapData
  return {
    frameNumber: targetFrame,
    heatmapIndex: indexedFrame.heatmapIndex,
    heatmapData: heatmapBytes?.length ? Buffer.from(heatmapBytes).toString('base64') : null,
    maxMotionRaw: finiteNumber(response.heatmap?.maxMotionRaw),
    stabilizationMethod: finiteNumber(response.methodUsed),
    stabilizationConfidence: finiteNumber(response.stabilizationConfidence),
    tracks: [
      ...motionCaptureTrails(index.tracks, indexedFrame.heatmapIndex, 'motion'),
      ...motionCaptureTrails(index.segmentationTracks, indexedFrame.heatmapIndex, 'segmentation'),
    ],
  }
}

function analysisFromInitialTurn(turn) {
  const raw = String(turn?.text || '').trim()
  if (!raw) return null
  const titleMatch = /^##\s+([^\n]+)\n*/.exec(raw)
  return {
    title: titleMatch?.[1]?.trim() || 'AI Analysis',
    text: titleMatch ? raw.slice(titleMatch[0].length).trim() : raw,
    parameters: [],
  }
}

function readChatThread(recordingPath) {
  if (!recordingPath || typeof recordingPath !== 'string' || !recordingPath.endsWith('.vis.pb')) {
    throw new Error('Expected a .vis.pb recording.')
  }

  const resolved = path.resolve(recordingPath)
  const basePath = resolved.slice(0, -'.vis.pb'.length)
  const gensparkPath = `${basePath}.genspark.pb`
  const chatPath = `${basePath}.chat.pb`
  const types = getInsightgenTypes()
  let analysis = null
  let chatHistory = null

  if (fs.existsSync(gensparkPath)) {
    const decoded = types.gensparkResponseType.decode(fs.readFileSync(gensparkPath))
    const response = types.gensparkResponseType.toObject(decoded, { defaults: true, longs: String })
    const summary = response.summary
    if (summary && (summary.title || summary.text || summary.parameters?.length)) {
      analysis = {
        title: String(summary.title || 'AI Analysis'),
        text: String(summary.text || ''),
        parameters: (summary.parameters || []).map((parameter) => ({
          name: String(parameter.name || ''),
          value: String(parameter.value || ''),
          unit: String(parameter.unit || ''),
        })),
      }
    }
  }

  if (fs.existsSync(chatPath)) {
    const decoded = types.chatHistoryType.decode(fs.readFileSync(chatPath))
    chatHistory = types.chatHistoryType.toObject(decoded, { defaults: true, longs: String })
    if (!analysis) analysis = analysisFromInitialTurn(chatHistory.initialTurn)
  }

  return {
    analysis,
    turns: (chatHistory?.turns || [])
      .filter((turn) => String(turn.text || '').trim())
      .map((turn) => ({
        role: String(turn.role || '').toLowerCase() === 'user' ? 'user' : 'assistant',
        text: String(turn.text || ''),
        timestampNs: timestampString(turn.timestampNs),
      })),
  }
}

const CHAT_WORKSPACE_VERSION = 1

function safeWorkspaceId(value, fallback) {
  const cleaned = String(value || '')
    .trim()
    .replace(/[^A-Za-z0-9._-]+/g, '-')
    .replace(/^[.-]+|[.-]+$/g, '')
    .slice(0, 160)
  return cleaned || fallback
}

function workspaceVideoDirectory(videoId) {
  return path.join(os.homedir(), '.bayesmech', safeWorkspaceId(videoId, 'video'))
}

function readJsonFile(filePath, fallback) {
  try {
    return JSON.parse(fs.readFileSync(filePath, 'utf8'))
  } catch {
    return fallback
  }
}

function writeJsonAtomic(filePath, value) {
  fs.mkdirSync(path.dirname(filePath), { recursive: true })
  const temporaryPath = path.join(
    path.dirname(filePath),
    `.${path.basename(filePath)}.${process.pid}.${Date.now()}.tmp`,
  )
  fs.writeFileSync(temporaryPath, `${JSON.stringify(value, null, 2)}\n`, 'utf8')
  fs.renameSync(temporaryPath, filePath)
}

function defaultChatTitle(createdAt) {
  return new Date(createdAt).toLocaleString(undefined, {
    dateStyle: 'medium',
    timeStyle: 'short',
  })
}

function normalizeWorkspaceMessage(message, index = 0) {
  const role = message?.role === 'user' || message?.role === 'command' ? message.role : 'assistant'
  const status = ['pending', 'ok', 'error'].includes(message?.status) ? message.status : undefined
  const createdAt = Number.isFinite(Date.parse(message?.createdAt))
    ? new Date(message.createdAt).toISOString()
    : new Date().toISOString()
  return {
    id: safeWorkspaceId(message?.id, `message-${Date.now()}-${index}`),
    role,
    text: String(message?.text || ''),
    createdAt,
    ...(status ? { status } : {}),
  }
}

function normalizeWorkspaceMarker(marker, index = 0) {
  return {
    id: safeWorkspaceId(marker?.id, `marker-${index + 1}`),
    name: String(marker?.name || `Marker ${index + 1}`),
    reference: String(marker?.reference || `Marker${index + 1}`),
    frameIndex: Math.max(0, Math.trunc(Number(marker?.frameIndex) || 0)),
    frameNumber: Math.max(0, Math.trunc(Number(marker?.frameNumber) || 0)),
    seconds: Math.max(0, Number(marker?.seconds) || 0),
    color: String(marker?.color || '#5aa9e6'),
  }
}

function normalizeChatSession(session) {
  const now = new Date().toISOString()
  const createdAt = Number.isFinite(Date.parse(session?.createdAt))
    ? new Date(session.createdAt).toISOString()
    : now
  const updatedAt = Number.isFinite(Date.parse(session?.updatedAt))
    ? new Date(session.updatedAt).toISOString()
    : now
  return {
    id: safeWorkspaceId(session?.id, `chat-${Date.now()}`),
    title: String(session?.title || '').trim() || defaultChatTitle(createdAt),
    createdAt,
    updatedAt,
    messages: Array.isArray(session?.messages)
      ? session.messages.map(normalizeWorkspaceMessage)
      : [],
    markers: Array.isArray(session?.markers)
      ? session.markers.map(normalizeWorkspaceMarker)
      : [],
  }
}

function writeChatSession(videoDirectory, session) {
  const normalized = normalizeChatSession(session)
  const chatDirectory = path.join(videoDirectory, normalized.id)
  fs.mkdirSync(chatDirectory, { recursive: true })
  writeJsonAtomic(path.join(chatDirectory, 'meta.json'), {
    version: CHAT_WORKSPACE_VERSION,
    id: normalized.id,
    title: normalized.title,
    createdAt: normalized.createdAt,
    updatedAt: normalized.updatedAt,
  })
  writeJsonAtomic(path.join(chatDirectory, 'chat.json'), {
    version: CHAT_WORKSPACE_VERSION,
    messages: normalized.messages,
  })
  writeJsonAtomic(path.join(chatDirectory, 'markers.json'), {
    version: CHAT_WORKSPACE_VERSION,
    markers: normalized.markers,
  })
  return normalized
}

function readChatSession(videoDirectory, entryName) {
  const chatId = safeWorkspaceId(entryName, '')
  if (!chatId || chatId !== entryName) return null
  const chatDirectory = path.join(videoDirectory, chatId)
  const meta = readJsonFile(path.join(chatDirectory, 'meta.json'), null)
  if (!meta || meta.id !== chatId) return null
  const chat = readJsonFile(path.join(chatDirectory, 'chat.json'), {})
  const markers = readJsonFile(path.join(chatDirectory, 'markers.json'), {})
  return normalizeChatSession({
    ...meta,
    messages: chat.messages,
    markers: markers.markers,
  })
}

function chatIdForDate(createdAt = new Date()) {
  const compact = createdAt.toISOString().replace(/\D/g, '').slice(0, 17)
  return `chat-${compact}-${Math.random().toString(36).slice(2, 7)}`
}

function legacyChatMessages(recordingPath) {
  try {
    return readChatThread(recordingPath).turns.map((turn, index) => {
      const timestampMs = Math.trunc(Number(turn.timestampNs) / 1e6)
      const createdAt = Number.isFinite(timestampMs) && timestampMs > 0
        ? new Date(timestampMs).toISOString()
        : new Date().toISOString()
      return normalizeWorkspaceMessage({
        id: `legacy-${index + 1}`,
        role: turn.role,
        text: turn.text,
        createdAt,
      }, index)
    })
  } catch {
    return []
  }
}

function workspaceManifest(videoId, recordingPath, activeChatId, chats) {
  return {
    version: CHAT_WORKSPACE_VERSION,
    videoId: String(videoId),
    recordingPath: path.resolve(recordingPath),
    activeChatId,
    chatOrder: chats.map((chat) => chat.id),
  }
}

function writeWorkspaceManifest(videoDirectory, videoId, recordingPath, activeChatId, chats) {
  writeJsonAtomic(
    path.join(videoDirectory, 'video.json'),
    workspaceManifest(videoId, recordingPath, activeChatId, chats),
  )
}

function loadChatWorkspace(videoId, recordingPath) {
  if (!recordingPath || typeof recordingPath !== 'string' || !recordingPath.endsWith('.vis.pb')) {
    throw new Error('Expected a .vis.pb recording.')
  }

  const normalizedVideoId = String(videoId || path.basename(recordingPath, '.vis.pb'))
  const videoDirectory = workspaceVideoDirectory(normalizedVideoId)
  fs.mkdirSync(videoDirectory, { recursive: true })
  const manifest = readJsonFile(path.join(videoDirectory, 'video.json'), {})
  const entries = fs.readdirSync(videoDirectory, { withFileTypes: true })
  const discovered = new Map()
  for (const entry of entries) {
    if (!entry.isDirectory()) continue
    const session = readChatSession(videoDirectory, entry.name)
    if (session) discovered.set(session.id, session)
  }
  const orderedIds = Array.isArray(manifest.chatOrder) ? manifest.chatOrder.map(String) : []
  const chats = [
    ...orderedIds.map((id) => discovered.get(id)).filter(Boolean),
    ...[...discovered.values()].filter((chat) => !orderedIds.includes(chat.id)),
  ]
  chats.sort((left, right) => {
    const leftIndex = orderedIds.indexOf(left.id)
    const rightIndex = orderedIds.indexOf(right.id)
    if (leftIndex >= 0 && rightIndex >= 0) return leftIndex - rightIndex
    if (leftIndex >= 0) return -1
    if (rightIndex >= 0) return 1
    return left.createdAt.localeCompare(right.createdAt)
  })

  if (chats.length === 0) {
    const createdAt = new Date()
    chats.push(writeChatSession(videoDirectory, {
      id: chatIdForDate(createdAt),
      title: defaultChatTitle(createdAt.toISOString()),
      createdAt: createdAt.toISOString(),
      updatedAt: createdAt.toISOString(),
      messages: legacyChatMessages(recordingPath),
      markers: [],
    }))
  }

  const activeChatId = chats.some((chat) => chat.id === manifest.activeChatId)
    ? manifest.activeChatId
    : chats[0].id
  writeWorkspaceManifest(videoDirectory, normalizedVideoId, recordingPath, activeChatId, chats)
  return {
    version: CHAT_WORKSPACE_VERSION,
    videoId: normalizedVideoId,
    recordingPath: path.resolve(recordingPath),
    activeChatId,
    chats,
  }
}

function createChatSession(videoId, recordingPath) {
  const workspace = loadChatWorkspace(videoId, recordingPath)
  const videoDirectory = workspaceVideoDirectory(videoId)
  const createdAt = new Date()
  const session = writeChatSession(videoDirectory, {
    id: chatIdForDate(createdAt),
    title: defaultChatTitle(createdAt.toISOString()),
    createdAt: createdAt.toISOString(),
    updatedAt: createdAt.toISOString(),
    messages: [],
    markers: [],
  })
  const chats = [...workspace.chats, session]
  writeWorkspaceManifest(videoDirectory, videoId, recordingPath, session.id, chats)
  return { ...workspace, activeChatId: session.id, chats }
}

function saveChatSession(videoId, recordingPath, session) {
  const workspace = loadChatWorkspace(videoId, recordingPath)
  const videoDirectory = workspaceVideoDirectory(videoId)
  const saved = writeChatSession(videoDirectory, session)
  const chats = workspace.chats.some((chat) => chat.id === saved.id)
    ? workspace.chats.map((chat) => (chat.id === saved.id ? saved : chat))
    : [...workspace.chats, saved]
  const activeChatId = chats.some((chat) => chat.id === workspace.activeChatId)
    ? workspace.activeChatId
    : saved.id
  writeWorkspaceManifest(videoDirectory, videoId, recordingPath, activeChatId, chats)
  return true
}

function setActiveChatSession(videoId, recordingPath, chatId) {
  const workspace = loadChatWorkspace(videoId, recordingPath)
  if (!workspace.chats.some((chat) => chat.id === chatId)) return false
  writeWorkspaceManifest(
    workspaceVideoDirectory(videoId),
    videoId,
    recordingPath,
    chatId,
    workspace.chats,
  )
  return true
}

function flattenNumbers(value, output = []) {
  if (Array.isArray(value)) {
    for (const item of value) flattenNumbers(item, output)
    return output
  }
  const number = Number(value)
  output.push(Number.isFinite(number) ? number : 0)
  return output
}

function float32Bytes(values) {
  const flat = flattenNumbers(values)
  const buffer = Buffer.allocUnsafe(flat.length * 4)
  flat.forEach((value, index) => buffer.writeFloatLE(value, index * 4))
  return buffer
}

function lengthDelimitedProtoRecord(payload) {
  const header = Buffer.allocUnsafe(4)
  header.writeUInt32BE(payload.length, 0)
  return Buffer.concat([header, Buffer.from(payload)])
}

function writeLengthDelimitedProtos(filePath, type, messages) {
  if (!messages.length) throw new Error(`Cannot write an empty protobuf stream: ${filePath}`)
  const records = messages.map((message) => lengthDelimitedProtoRecord(type.encode(type.create(message)).finish()))
  const temporaryPath = `${filePath}.tmp-${process.pid}-${Date.now()}`
  try {
    fs.writeFileSync(temporaryPath, Buffer.concat(records))
    fs.renameSync(temporaryPath, filePath)
  } finally {
    try {
      if (fs.existsSync(temporaryPath)) fs.unlinkSync(temporaryPath)
    } catch {
      // Best-effort cleanup; the canonical stream was either replaced or the
      // original file remains untouched.
    }
  }
}

function readLengthDelimitedProtos(filePath, type) {
  const stat = fs.statSync(filePath)
  if (stat.size < 4) throw new Error(`Invalid protobuf file: ${filePath}`)
  const fd = fs.openSync(filePath, 'r')
  const messages = []
  try {
    let position = 0
    while (position < stat.size) {
      if (stat.size - position < 4) throw new Error(`Truncated protobuf header in ${filePath}`)
      const header = Buffer.allocUnsafe(4)
      fs.readSync(fd, header, 0, 4, position)
      const length = header.readUInt32BE(0)
      position += 4
      if (length <= 0 || length > stat.size - position) {
        throw new Error(`Invalid protobuf length in ${filePath}`)
      }
      const payload = Buffer.allocUnsafe(length)
      fs.readSync(fd, payload, 0, length, position)
      messages.push(type.decode(payload))
      position += length
    }
  } finally {
    fs.closeSync(fd)
  }
  return messages
}

function bytesBuffer(value) {
  if (!value) return Buffer.alloc(0)
  return Buffer.from(value)
}

function readFloat32(buffer, index, fallback = 0) {
  const offset = index * 4
  return offset + 4 <= buffer.length ? buffer.readFloatLE(offset) : fallback
}

function readLocalSplatPreview(filePath) {
  if (!filePath || typeof filePath !== 'string') return null
  try {
    const resolved = path.resolve(filePath)
    if (!fs.existsSync(resolved)) return null
    return JSON.parse(fs.readFileSync(resolved, 'utf8'))
  } catch {
    return null
  }
}

function savedSplatInfo(splat, preview = null) {
  if (!splat) return null
  const hasData = Boolean(
    splat.status ||
    splat.jobId ||
    splat.plyPath ||
    splat.previewJsonPath ||
    splat.error ||
    splat.gaussianCount ||
    splat.previewPointCount,
  )
  if (!hasData) return null
  const embeddedPreviewPoints = normalizeSplatPreview({ points: splat.previewPoints || [] }, {}).points
  const localPreviewPoints = normalizeSplatPreview(preview, {}).points
  const previewPoints = embeddedPreviewPoints.length ? embeddedPreviewPoints : localPreviewPoints
  return {
    status: String(splat.status || 'queued'),
    jobId: String(splat.jobId || ''),
    stage: String(splat.stage || ''),
    message: String(splat.message || ''),
    progress: Math.min(1, Math.max(0, finiteNumber(splat.progress))),
    currentStep: finiteNumber(splat.currentStep),
    plyPath: String(splat.plyPath || ''),
    previewJsonPath: String(splat.previewJsonPath || ''),
    error: String(splat.error || ''),
    gaussianCount: finiteNumber(splat.gaussianCount),
    previewPointCount: finiteNumber(splat.previewPointCount, previewPoints.length),
    initPointCount: finiteNumber(splat.initPointCount),
    trainingFrameCount: finiteNumber(splat.trainingFrameCount),
    maxSteps: finiteNumber(splat.maxSteps),
    maxGaussians: finiteNumber(splat.maxGaussians),
    elapsedSec: finiteNumber(splat.elapsedSec),
    trainer: String(splat.trainer || ''),
    points: previewPoints,
  }
}

function encodeSplatPreviewPoints(points) {
  return (Array.isArray(points) ? points : []).map((point) => {
    const encoded = {
      x: finiteNumber(point.x),
      y: finiteNumber(point.y),
      z: finiteNumber(point.z),
      r: Math.min(1, Math.max(0, finiteNumber(point.r, 0.7))),
      g: Math.min(1, Math.max(0, finiteNumber(point.g, 0.7))),
      b: Math.min(1, Math.max(0, finiteNumber(point.b, 0.7))),
      opacity: Math.min(1, Math.max(0, finiteNumber(point.opacity, 0.8))),
      scale: Math.max(0, finiteNumber(point.scale, 0.02)),
    }
    const sx = firstFinite(point.sx, point.scaleX)
    const sy = firstFinite(point.sy, point.scaleY)
    const sz = firstFinite(point.sz, point.scaleZ)
    if (sx > 0 && sy > 0 && sz > 0) {
      encoded.scaleX = sx
      encoded.scaleY = sy
      encoded.scaleZ = sz
      encoded.rotX = finiteNumber(firstFinite(point.qx, point.rotX), 0)
      encoded.rotY = finiteNumber(firstFinite(point.qy, point.rotY), 0)
      encoded.rotZ = finiteNumber(firstFinite(point.qz, point.rotZ), 0)
      encoded.rotW = finiteNumber(firstFinite(point.qw, point.rotW), 1)
    }
    return encoded
  })
}

function worldgenFrameKey(frame) {
  const identifier = frame?.frameIdentifier
  const frameNumber = Number(identifier?.frameNumber)
  if (identifier && Number.isFinite(frameNumber)) return `frame:${frameNumber}`
  const timestampNs = timestampString(identifier?.timestampNs)
  if (timestampNs && timestampNs !== '0') return `timestamp:${timestampNs}`
  return `index:${finiteNumber(frame?.sourceFrameIndex)}`
}

function worldgenComputationKey(message) {
  const frames = (Array.isArray(message?.pointClouds) && message.pointClouds.length
    ? message.pointClouds
    : message?.cameras || [])
    .map(worldgenFrameKey)
    .sort()
  return frames.length ? frames.join('|') : `request:${String(message?.requestId || '')}`
}

function uniqueWorldgenMessages(messages) {
  const byComputation = new Map()
  for (const message of messages) {
    const key = worldgenComputationKey(message)
    // Recomputing the exact same frame set replaces its older record while a
    // different marker range remains another record in the same file.
    if (byComputation.has(key)) byComputation.delete(key)
    byComputation.set(key, message)
  }
  return [...byComputation.values()]
}

function worldgenFrameSort(left, right) {
  const byIndex = finiteNumber(left?.sourceFrameIndex) - finiteNumber(right?.sourceFrameIndex)
  if (byIndex) return byIndex
  return finiteNumber(left?.frameIdentifier?.frameNumber) - finiteNumber(right?.frameIdentifier?.frameNumber)
}

function aggregateWorldgenSplatInfo(messages) {
  const activeMessages = uniqueWorldgenMessages(messages)
  const lastPreviewPathIndex = new Map()
  activeMessages.forEach((message, index) => {
    const previewPath = String(message?.gaussianSplat?.previewJsonPath || '')
    if (previewPath) lastPreviewPathIndex.set(path.resolve(previewPath), index)
  })

  const infos = activeMessages
    .map((message, index) => {
      const splat = message?.gaussianSplat
      const hasEmbeddedPoints = Array.isArray(splat?.previewPoints) && splat.previewPoints.length > 0
      const previewPath = String(splat?.previewJsonPath || '')
      const resolvedPreviewPath = previewPath ? path.resolve(previewPath) : ''
      const canUseLocalPreview = !hasEmbeddedPoints && resolvedPreviewPath && lastPreviewPathIndex.get(resolvedPreviewPath) === index
      const info = savedSplatInfo(splat, canUseLocalPreview ? readLocalSplatPreview(resolvedPreviewPath) : null)
      return info ? { info, message } : null
    })
    .filter(Boolean)

  if (!infos.length) return null
  const latest = infos[infos.length - 1].info
  const pointSources = infos.map((entry) => entry.info.points).filter((points) => points.length)
  const points = []
  const perSourceCap = Math.max(1, Math.floor(MAX_WORLDGEN_SPLAT_PREVIEW_POINTS / Math.max(1, pointSources.length)))
  for (const source of pointSources) {
    const stride = Math.max(1, Math.ceil(source.length / perSourceCap))
    let added = 0
    for (let index = 0; index < source.length && added < perSourceCap; index += stride) {
      points.push(source[index])
      added += 1
    }
  }

  const computedFrames = new Set()
  for (const message of activeMessages) {
    for (const frame of [...(message.pointClouds || []), ...(message.cameras || [])]) {
      computedFrames.add(worldgenFrameKey(frame))
    }
  }
  const sum = (field) => infos.reduce((total, entry) => total + finiteNumber(entry.info[field]), 0)
  return {
    ...latest,
    status: points.length && latest.status === 'skipped' ? 'complete' : latest.status,
    gaussianCount: sum('gaussianCount'),
    previewPointCount: points.length,
    initPointCount: sum('initPointCount'),
    trainingFrameCount: computedFrames.size,
    maxGaussians: sum('maxGaussians'),
    elapsedSec: sum('elapsedSec'),
    points,
  }
}

function worldgenPreviewFromMessages(messages, outputPath) {
  const activeMessages = uniqueWorldgenMessages(messages)
  if (!activeMessages.length) throw new Error(`World Modeling file has no protobuf records: ${outputPath}`)
  const latest = activeMessages[activeMessages.length - 1]
  const camerasByFrame = new Map()
  const cloudsByFrame = new Map()
  for (const message of activeMessages) {
    for (const camera of message.cameras || []) camerasByFrame.set(worldgenFrameKey(camera), camera)
    for (const cloud of message.pointClouds || []) cloudsByFrame.set(worldgenFrameKey(cloud), cloud)
  }

  const cameras = [...camerasByFrame.values()].sort(worldgenFrameSort)
  const pointClouds = [...cloudsByFrame.values()].sort(worldgenFrameSort)
  const sourceIndices = [...cameras, ...pointClouds]
    .map((frame) => finiteNumber(frame.sourceFrameIndex))
    .filter(Number.isFinite)
  const frameKeys = new Set([...camerasByFrame.keys(), ...cloudsByFrame.keys()])
  const combined = {
    ...latest,
    requestId: path.basename(outputPath, '.vggt.pb'),
    markerStart: '',
    markerEnd: '',
    startFrameIndex: sourceIndices.length ? Math.min(...sourceIndices) : 0,
    endFrameIndex: sourceIndices.length ? Math.max(...sourceIndices) : 0,
    frameCount: frameKeys.size,
    cameras,
    pointClouds,
    elapsedSec: activeMessages.reduce((total, message) => total + finiteNumber(message.elapsedSec), 0),
  }
  return worldgenPreviewFromMessage(combined, outputPath, aggregateWorldgenSplatInfo(activeMessages))
}

function worldgenPreviewFromMessage(message, outputPath, splatInfo = null) {
  const pointClouds = Array.isArray(message.pointClouds) ? message.pointClouds : []
  const perFrameCap = Math.max(1, Math.floor(MAX_WORLDGEN_PREVIEW_POINTS / Math.max(1, pointClouds.length)))
  const points = []
  const frames = []
  let totalPointCount = 0

  for (const [cloudIndex, cloud] of pointClouds.entries()) {
    const identifier = cloud.frameIdentifier || {}
    const xyz = bytesBuffer(cloud.xyzF32Le)
    const rgb = bytesBuffer(cloud.rgbF32Le)
    const uv = bytesBuffer(cloud.uvF32Le)
    const conf = bytesBuffer(cloud.confidenceF32Le)
    const xyzCount = Math.floor(xyz.length / 12)
    const rgbCount = Math.floor(rgb.length / 12)
    const uvCount = Math.floor(uv.length / 8)
    const confCount = Math.floor(conf.length / 4)
    const pointTotal = Math.min(
      xyzCount,
      rgbCount || xyzCount,
      uvCount || xyzCount,
      confCount || xyzCount,
    )
    const pointCount = finiteNumber(cloud.pointCount, pointTotal)
    totalPointCount += pointCount
    const stride = Math.max(1, Math.ceil(pointTotal / perFrameCap))
    let returnedPointCount = 0

    frames.push({
      sampledFrameIndex: finiteNumber(cloud.sampledFrameIndex, cloudIndex),
      frameIndex: finiteNumber(cloud.sourceFrameIndex, cloudIndex),
      frameNumber: finiteNumber(identifier.frameNumber, cloudIndex),
      imageWidth: finiteNumber(cloud.imageWidth),
      imageHeight: finiteNumber(cloud.imageHeight),
      pointCount,
      returnedPointCount: finiteNumber(cloud.returnedPointCount, pointTotal),
    })

    for (let index = 0; index < pointTotal; index += stride) {
      points.push({
        x: readFloat32(xyz, index * 3),
        y: readFloat32(xyz, index * 3 + 1),
        z: readFloat32(xyz, index * 3 + 2),
        r: Math.min(1, Math.max(0, readFloat32(rgb, index * 3, 0.35))),
        g: Math.min(1, Math.max(0, readFloat32(rgb, index * 3 + 1, 0.66))),
        b: Math.min(1, Math.max(0, readFloat32(rgb, index * 3 + 2, 0.9))),
        confidence: readFloat32(conf, index, 0),
        frameIndex: finiteNumber(cloud.sourceFrameIndex, cloudIndex),
        frameNumber: finiteNumber(identifier.frameNumber, cloudIndex),
        framePointIndex: index,
        u: readFloat32(uv, index * 2, 0),
        v: readFloat32(uv, index * 2 + 1, 0),
      })
      returnedPointCount += 1
    }

    frames[frames.length - 1].returnedPointCount = returnedPointCount
  }

  const cameras = (Array.isArray(message.cameras) ? message.cameras : [])
    .map((camera, index) => {
      const identifier = camera.frameIdentifier || {}
      const center = Array.isArray(camera.cameraCenter) ? camera.cameraCenter : []
      if (center.length < 3) return null
      return {
        frameIndex: finiteNumber(camera.sourceFrameIndex, index),
        frameNumber: finiteNumber(identifier.frameNumber, index),
        x: finiteNumber(center[0]),
        y: finiteNumber(center[1]),
        z: finiteNumber(center[2]),
        matrix: flattenNumbers(camera.cameraToWorld || []),
        intrinsics: flattenNumbers(camera.intrinsics || []),
      }
    })
    .filter(Boolean)

  const id = message.requestId || path.basename(outputPath, '.vggt.pb')
  return {
    id,
    outputPath,
    markerStart: String(message.markerStart || ''),
    markerEnd: String(message.markerEnd || ''),
    startFrameIndex: finiteNumber(message.startFrameIndex),
    endFrameIndex: finiteNumber(message.endFrameIndex),
    frameCount: finiteNumber(message.frameCount, pointClouds.length),
    pointCount: totalPointCount,
    returnedPointCount: points.length,
    model: String(message.model || 'VGGT-Omega'),
    elapsedSec: finiteNumber(message.elapsedSec),
    frames,
    points,
    cameras,
    splat: splatInfo
      ? {
          status: splatInfo.status,
          jobId: splatInfo.jobId,
          stage: splatInfo.stage,
          message: splatInfo.message,
          progress: splatInfo.progress,
          currentStep: splatInfo.currentStep,
          plyPath: splatInfo.plyPath,
          previewJsonPath: splatInfo.previewJsonPath,
          error: splatInfo.error,
          gaussianCount: splatInfo.gaussianCount,
          previewPointCount: splatInfo.previewPointCount,
          initPointCount: splatInfo.initPointCount,
          trainingFrameCount: splatInfo.trainingFrameCount,
          maxSteps: splatInfo.maxSteps,
          maxGaussians: splatInfo.maxGaussians,
          elapsedSec: splatInfo.elapsedSec,
          trainer: splatInfo.trainer,
        }
      : null,
    splatPoints: splatInfo?.points ?? [],
  }
}

async function readWorldgen(filePath) {
  if (!filePath || typeof filePath !== 'string' || !filePath.endsWith('.vggt.pb')) {
    throw new Error('Expected a .vggt.pb file.')
  }
  const resolved = path.resolve(filePath)
  const messages = readLengthDelimitedProtos(resolved, getVggtResponseType())
  let result = worldgenPreviewFromMessages(messages, resolved)
  const splatInfo = result.splat
  const missingCompletedPly = splatInfo?.status === 'complete' && (
    !splatInfo.plyPath || !fs.existsSync(splatInfo.plyPath)
  )

  if (splatInfo?.jobId && (
    !['complete', 'failed', 'skipped'].includes(splatInfo.status) || missingCompletedPly
  )) {
    try {
      worldgenSplatDestinations.set(splatInfo.jobId, worldgenSplatPaths(resolved))
      const remote = await pollWorldgenSplat(splatInfo.jobId)
      let jobMessageIndex = messages.length - 1
      for (let index = messages.length - 1; index >= 0; index -= 1) {
        if (String(messages[index]?.gaussianSplat?.jobId || '') === splatInfo.jobId) {
          jobMessageIndex = index
          break
        }
      }
      const previous = messages[jobMessageIndex].gaussianSplat || {}
      messages[jobMessageIndex].gaussianSplat = {
        ...previous,
        status: remote.status,
        stage: remote.stage || previous.stage,
        message: remote.message || previous.message,
        progress: remote.progress,
        currentStep: remote.currentStep,
        plyPath: remote.plyPath || previous.plyPath,
        previewJsonPath: remote.previewJsonPath || previous.previewJsonPath,
        error: remote.error || previous.error,
        gaussianCount: remote.gaussianCount || previous.gaussianCount,
        previewPointCount: remote.previewPointCount || previous.previewPointCount,
        elapsedSec: remote.elapsedSec || previous.elapsedSec,
        previewPoints: remote.points?.length ? remote.points : previous.previewPoints,
      }
      result = worldgenPreviewFromMessages(messages, resolved)
      if (remote.status === 'complete' || remote.status === 'failed') {
        result = await saveWorldgenSplat(resolved, remote)
      }
    } catch {
      // Keep the saved VGGT point cloud usable even when the remote splat
      // status endpoint is unavailable.
    }
  }

  return result
}

async function saveWorldgenSplat(filePath, splat) {
  if (!filePath || typeof filePath !== 'string' || !filePath.endsWith('.vggt.pb')) {
    throw new Error('Expected a .vggt.pb file.')
  }
  const resolved = path.resolve(filePath)
  const type = getVggtResponseType()
  const messages = readLengthDelimitedProtos(resolved, type)
  let messageIndex = messages.length - 1
  const jobId = String(splat?.jobId || '')
  if (jobId) {
    for (let index = messages.length - 1; index >= 0; index -= 1) {
      if (String(messages[index]?.gaussianSplat?.jobId || '') === jobId) {
        messageIndex = index
        break
      }
    }
  }
  const message = messages[messageIndex]
  const previous = message.gaussianSplat || {}
  const paths = worldgenSplatPaths(resolved)
  let previewJsonPath = String(splat?.previewJsonPath || previous.previewJsonPath || '')
  const points = Array.isArray(splat?.points) ? splat.points : []
  const embeddedPoints = points.length ? points : (Array.isArray(previous.previewPoints) ? previous.previewPoints : [])

  if (points.length) {
    const preview = {
      status: String(splat.status || previous.status || 'complete'),
      ply_path: String(splat.plyPath || previous.plyPath || ''),
      preview_json_path: paths.previewPath,
      error: String(splat.error || previous.error || ''),
      gaussian_count: finiteNumber(splat.gaussianCount, previous.gaussianCount || points.length),
      preview_point_count: points.length,
      init_point_count: finiteNumber(splat.initPointCount, previous.initPointCount),
      training_frame_count: finiteNumber(splat.trainingFrameCount, previous.trainingFrameCount),
      max_steps: finiteNumber(splat.maxSteps, previous.maxSteps),
      max_gaussians: finiteNumber(splat.maxGaussians, previous.maxGaussians),
      elapsed_sec: finiteNumber(splat.elapsedSec, previous.elapsedSec),
      trainer: String(splat.trainer || previous.trainer || 'remote-vggt-service'),
      points,
    }
    fs.writeFileSync(paths.previewPath, JSON.stringify(preview), 'utf8')
    previewJsonPath = paths.previewPath
  }

  message.gaussianSplat = {
    status: String(splat?.status || previous.status || 'queued'),
    plyPath: String(splat?.plyPath || previous.plyPath || ''),
    previewJsonPath,
    error: String(splat?.error || previous.error || ''),
    gaussianCount: finiteNumber(splat?.gaussianCount, previous.gaussianCount),
    previewPointCount: finiteNumber(splat?.previewPointCount, previous.previewPointCount || points.length),
    initPointCount: finiteNumber(splat?.initPointCount, previous.initPointCount),
    trainingFrameCount: finiteNumber(splat?.trainingFrameCount, previous.trainingFrameCount),
    maxSteps: finiteNumber(splat?.maxSteps, previous.maxSteps),
    maxGaussians: finiteNumber(splat?.maxGaussians, previous.maxGaussians),
    elapsedSec: finiteNumber(splat?.elapsedSec, previous.elapsedSec),
    trainer: String(splat?.trainer || previous.trainer || 'remote-vggt-service'),
    jobId: String(splat?.jobId || previous.jobId || ''),
    stage: String(splat?.stage || previous.stage || ''),
    message: String(splat?.message || previous.message || ''),
    progress: Math.min(1, Math.max(0, finiteNumber(splat?.progress, previous.progress))),
    currentStep: finiteNumber(splat?.currentStep, previous.currentStep),
    previewPoints: encodeSplatPreviewPoints(embeddedPoints),
  }

  messages[messageIndex] = message
  writeLengthDelimitedProtos(resolved, type, messages)
  return readWorldgen(resolved)
}

function runnerEndpoint() {
  return String(process.env.RUNNER_ENDPOINT || DEFAULT_RUNNER_ENDPOINT).replace(/\/+$/g, '')
}

function worldgenEndpoint() {
  const runner = String(process.env.RUNNER_ENDPOINT || '').trim()
  const legacy = String(process.env.VGGT_ENDPOINT || '').trim()
  if (!runner && legacy && /^(1|true|yes|on)$/i.test(String(process.env.RUNNER_USE_LEGACY_VGGT || '').trim())) {
    return legacy.replace(/\/+$/g, '')
  }
  return `${runnerEndpoint()}/api/v1/worldgen`
}

function worldgenUsesRunner() {
  const legacyEnabled = /^(1|true|yes|on)$/i.test(String(process.env.RUNNER_USE_LEGACY_VGGT || '').trim())
  return !legacyEnabled || !String(process.env.VGGT_ENDPOINT || '').trim()
}

function worldgenToken() {
  return String(process.env.RUNNER_TOKEN || process.env.VGGT_API_TOKEN || '').trim()
}

function worldgenSplatDisabled() {
  return /^(0|false|no|off)$/i.test(String(process.env.WORLDGEN_SPLAT ?? '').trim())
}

function worldgenSplatPaths(vggtPath) {
  const dir = path.dirname(vggtPath)
  const name = path.basename(vggtPath)
  const stem = name.endsWith('.vggt.pb') ? name.slice(0, -'.vggt.pb'.length) : path.parse(name).name
  return {
    workspacePath: path.join(dir, `${stem}.splat-workspace`),
    plyPath: path.join(dir, `${stem}.splat.ply`),
    previewPath: path.join(dir, `${stem}.splat.preview.json`),
  }
}

function runProcess(command, args, options = {}) {
  return new Promise((resolve, reject) => {
    const child = spawn(command, args, {
      cwd: options.cwd,
      env: options.env,
      stdio: ['ignore', 'pipe', 'pipe'],
    })
    const stdout = []
    const stderr = []
    child.stdout.on('data', (chunk) => stdout.push(Buffer.from(chunk)))
    child.stderr.on('data', (chunk) => stderr.push(Buffer.from(chunk)))
    child.on('error', reject)
    child.on('close', (code) => {
      const out = Buffer.concat(stdout).toString('utf8')
      const err = Buffer.concat(stderr).toString('utf8')
      if (code === 0) {
        resolve({ stdout: out, stderr: err })
      } else {
        reject(new Error(`${command} exited with ${code}${err ? `: ${err.slice(-2000)}` : ''}`))
      }
    })
  })
}

function runShellCommand(command, options = {}) {
  return new Promise((resolve, reject) => {
    const child = spawn(command, [], {
      cwd: options.cwd,
      env: options.env,
      shell: true,
      stdio: ['ignore', 'pipe', 'pipe'],
    })
    const stdout = []
    const stderr = []
    child.stdout.on('data', (chunk) => stdout.push(Buffer.from(chunk)))
    child.stderr.on('data', (chunk) => stderr.push(Buffer.from(chunk)))
    child.on('error', reject)
    child.on('close', (code) => {
      const out = Buffer.concat(stdout).toString('utf8')
      const err = Buffer.concat(stderr).toString('utf8')
      if (code === 0) {
        resolve({ stdout: out, stderr: err })
      } else {
        reject(new Error(`restart command exited with ${code}${err ? `: ${err.slice(-2000)}` : ''}`))
      }
    })
  })
}

async function fetchWithTimeout(url, options = {}, timeoutMs = DEFAULT_VGGT_REQUEST_TIMEOUT_MS) {
  const controller = new AbortController()
  const timeout = setTimeout(() => controller.abort(), timeoutMs)
  try {
    return await fetch(url, { ...options, signal: controller.signal })
  } catch (err) {
    if (err?.name === 'AbortError') {
      throw new Error(`request timed out after ${Math.round(timeoutMs / 1000)}s`)
    }
    throw err
  } finally {
    clearTimeout(timeout)
  }
}

function nodeHttpRequest(urlString, options = {}) {
  const method = options.method || 'GET'
  const body = options.body ? Buffer.from(options.body) : null
  const timeoutMs = Math.max(1000, Math.trunc(finiteNumber(options.timeoutMs, DEFAULT_VGGT_REQUEST_TIMEOUT_MS)))
  const parsed = new URL(urlString)
  const transport = parsed.protocol === 'https:' ? https : http
  const headers = { ...(options.headers || {}) }
  if (body && headers['Content-Length'] == null && headers['content-length'] == null) {
    headers['Content-Length'] = String(body.length)
  }

  return new Promise((resolve, reject) => {
    const req = transport.request(parsed, { method, headers }, (res) => {
      const chunks = []
      res.on('data', (chunk) => chunks.push(Buffer.from(chunk)))
      res.on('end', () => {
        const payload = Buffer.concat(chunks)
        const status = Number(res.statusCode) || 0
        resolve({
          ok: status >= 200 && status < 300,
          status,
          headers: res.headers,
          body: payload,
          text: async () => payload.toString('utf8'),
          json: async () => JSON.parse(payload.toString('utf8')),
        })
      })
    })
    req.setTimeout(timeoutMs, () => {
      req.destroy(new Error(`request timed out after ${Math.round(timeoutMs / 1000)}s`))
    })
    req.on('error', reject)
    if (body) req.write(body)
    req.end()
  })
}

function nodeMultipartFilesRequest(urlString, fields, filePaths, options = {}) {
  const boundary = `----BayesMechRunner${Date.now().toString(36)}${Math.random().toString(36).slice(2)}`
  const parts = []
  for (const [name, value] of Object.entries(fields)) {
    parts.push({
      header: Buffer.from(
        `--${boundary}\r\nContent-Disposition: form-data; name="${name}"\r\n\r\n${String(value)}\r\n`,
        'utf8',
      ),
    })
  }
  for (const filePath of filePaths) {
    const resolved = path.resolve(filePath)
    const stat = fs.statSync(resolved)
    if (!stat.isFile()) throw new Error(`Runner input is not a file: ${resolved}`)
    const filename = path.basename(resolved).replace(/["\r\n]/g, '_')
    parts.push({
      header: Buffer.from(
        `--${boundary}\r\n` +
        `Content-Disposition: form-data; name="files"; filename="${filename}"\r\n` +
        'Content-Type: application/octet-stream\r\n\r\n',
        'utf8',
      ),
      filePath: resolved,
      size: stat.size,
      trailer: Buffer.from('\r\n', 'utf8'),
    })
  }
  const closing = Buffer.from(`--${boundary}--\r\n`, 'utf8')
  const contentLength = parts.reduce(
    (total, part) => total + part.header.length + (part.size || 0) + (part.trailer?.length || 0),
    closing.length,
  )
  const parsed = new URL(urlString)
  const transport = parsed.protocol === 'https:' ? https : http
  const timeoutMs = Math.max(1000, Math.trunc(finiteNumber(options.timeoutMs, DEFAULT_VGGT_REQUEST_TIMEOUT_MS)))
  const headers = {
    ...(options.headers || {}),
    'Content-Type': `multipart/form-data; boundary=${boundary}`,
    'Content-Length': String(contentLength),
  }

  return new Promise((resolve, reject) => {
    const req = transport.request(parsed, { method: options.method || 'POST', headers }, (res) => {
      const chunks = []
      res.on('data', (chunk) => chunks.push(Buffer.from(chunk)))
      res.on('end', () => {
        const payload = Buffer.concat(chunks)
        const status = Number(res.statusCode) || 0
        resolve({
          ok: status >= 200 && status < 300,
          status,
          headers: res.headers,
          body: payload,
          text: async () => payload.toString('utf8'),
          json: async () => JSON.parse(payload.toString('utf8')),
        })
      })
    })
    req.setTimeout(timeoutMs, () => {
      req.destroy(new Error(`request timed out after ${Math.round(timeoutMs / 1000)}s`))
    })
    req.on('error', reject)

    const write = (chunk) => new Promise((resolveWrite, rejectWrite) => {
      if (req.destroyed) {
        rejectWrite(new Error('Runner upload connection closed.'))
        return
      }
      if (req.write(chunk)) {
        resolveWrite()
        return
      }
      const cleanup = () => {
        req.off('drain', onDrain)
        req.off('error', onError)
      }
      const onDrain = () => {
        cleanup()
        resolveWrite()
      }
      const onError = (error) => {
        cleanup()
        rejectWrite(error)
      }
      req.once('drain', onDrain)
      req.once('error', onError)
    })

    ;(async () => {
      for (const part of parts) {
        await write(part.header)
        if (part.filePath) {
          for await (const chunk of fs.createReadStream(part.filePath)) await write(chunk)
          await write(part.trailer)
        }
      }
      req.end(closing)
    })().catch((error) => req.destroy(error))
  })
}

function nodeDownloadToFile(urlString, destinationPath, options = {}) {
  const timeoutMs = Math.max(1000, Math.trunc(finiteNumber(options.timeoutMs, DEFAULT_VGGT_REQUEST_TIMEOUT_MS)))
  const parsed = new URL(urlString)
  const transport = parsed.protocol === 'https:' ? https : http
  const resolved = path.resolve(destinationPath)
  const temporary = `${resolved}.partial-${process.pid}-${Date.now()}`
  fs.mkdirSync(path.dirname(resolved), { recursive: true })

  return new Promise((resolve, reject) => {
    const cleanup = (error) => {
      fs.rmSync(temporary, { force: true })
      reject(error)
    }
    const req = transport.request(parsed, { method: 'GET', headers: options.headers || {} }, (res) => {
      const status = Number(res.statusCode) || 0
      if (status < 200 || status >= 300) {
        const chunks = []
        res.on('data', (chunk) => chunks.push(Buffer.from(chunk)))
        res.on('end', () => {
          const detail = Buffer.concat(chunks).toString('utf8').slice(0, 300)
          cleanup(new Error(`Runner artifact download failed with HTTP ${status}${detail ? `: ${detail}` : ''}`))
        })
        return
      }
      const output = fs.createWriteStream(temporary, { flags: 'wx' })
      output.on('error', cleanup)
      res.on('error', cleanup)
      output.on('finish', () => {
        output.close(() => {
          try {
            fs.renameSync(temporary, resolved)
            resolve(resolved)
          } catch (error) {
            cleanup(error)
          }
        })
      })
      res.pipe(output)
    })
    req.setTimeout(timeoutMs, () => {
      req.destroy(new Error(`request timed out after ${Math.round(timeoutMs / 1000)}s`))
    })
    req.on('error', cleanup)
    req.end()
  })
}

function runnerHeaders(token = worldgenToken()) {
  return token ? { Authorization: `Bearer ${token}` } : {}
}

async function readRunnerHealth() {
  const endpoint = runnerEndpoint()
  const response = await nodeHttpRequest(`${endpoint}/api/v1/health`, {
    headers: { ...runnerHeaders(), Accept: 'application/json' },
    timeoutMs: Math.max(1000, Math.trunc(finiteNumber(process.env.RUNNER_HEALTH_TIMEOUT_MS, DEFAULT_VGGT_HEALTH_TIMEOUT_MS))),
  })
  if (!response.ok) throw new Error(`Runner health check failed with HTTP ${response.status}${await readResponseDetail(response)}`)
  return response.json()
}

async function readRunnerCapabilities() {
  const endpoint = runnerEndpoint()
  const response = await nodeHttpRequest(`${endpoint}/api/v1/capabilities`, {
    headers: { ...runnerHeaders(), Accept: 'application/json' },
    timeoutMs: Math.max(1000, Math.trunc(finiteNumber(process.env.RUNNER_HEALTH_TIMEOUT_MS, DEFAULT_VGGT_HEALTH_TIMEOUT_MS))),
  })
  if (!response.ok) throw new Error(`Runner capabilities failed with HTTP ${response.status}${await readResponseDetail(response)}`)
  return response.json()
}

async function submitRunnerJob(request) {
  if (!request?.jobType || typeof request.jobType !== 'string') throw new Error('Runner job type is required.')
  if (!request?.recordingPath || typeof request.recordingPath !== 'string') throw new Error('Runner recording path is required.')
  const recordingPath = path.resolve(request.recordingPath)
  const argumentsList = Array.isArray(request.arguments) ? request.arguments.map((value) => String(value)) : []
  const explicitInputs = Array.isArray(request.inputPaths) ? request.inputPaths : []
  const inputPaths = explicitInputs.length
    ? explicitInputs.map((value) => path.resolve(String(value)))
    : relatedRunnerInputPaths(recordingPath)
  if (!inputPaths.includes(recordingPath)) inputPaths.unshift(recordingPath)
  const uniqueInputs = [...new Set(inputPaths)]
  const endpoint = runnerEndpoint()
  const response = await nodeMultipartFilesRequest(`${endpoint}/api/v1/jobs`, {
    job_type: request.jobType,
    parameters: JSON.stringify({
      arguments: argumentsList,
      recording: path.basename(recordingPath),
    }),
  }, uniqueInputs, {
    method: 'POST',
    headers: {
      ...runnerHeaders(),
      Accept: 'application/json',
    },
    timeoutMs: Math.max(1000, Math.trunc(finiteNumber(process.env.RUNNER_UPLOAD_TIMEOUT_MS, DEFAULT_VGGT_REQUEST_TIMEOUT_MS))),
  })
  if (!response.ok) throw new Error(`Runner job submission failed with HTTP ${response.status}${await readResponseDetail(response)}`)
  return response.json()
}

function relatedRunnerInputPaths(recordingPath) {
  const resolved = path.resolve(recordingPath)
  const directory = path.dirname(resolved)
  const filename = path.basename(resolved)
  const stem = filename.endsWith('.vis.pb') ? filename.slice(0, -'.vis.pb'.length) : path.parse(filename).name
  const related = fs.readdirSync(directory, { withFileTypes: true })
    .filter((entry) => entry.isFile() && (entry.name === filename || (entry.name.startsWith(`${stem}.`) && entry.name.endsWith('.pb'))))
    .map((entry) => path.join(directory, entry.name))
  related.sort((left, right) => (left === resolved ? -1 : right === resolved ? 1 : left.localeCompare(right)))
  return related
}

async function readRunnerJob(jobId) {
  if (!jobId || typeof jobId !== 'string') throw new Error('Runner job id is required.')
  const response = await nodeHttpRequest(`${runnerEndpoint()}/api/v1/jobs/${encodeURIComponent(jobId)}`, {
    headers: { ...runnerHeaders(), Accept: 'application/json' },
    timeoutMs: Math.max(1000, Math.trunc(finiteNumber(process.env.RUNNER_HEALTH_TIMEOUT_MS, DEFAULT_VGGT_HEALTH_TIMEOUT_MS))),
  })
  if (!response.ok) throw new Error(`Runner job status failed with HTTP ${response.status}${await readResponseDetail(response)}`)
  return response.json()
}

async function cancelRunnerJob(jobId) {
  if (!jobId || typeof jobId !== 'string') throw new Error('Runner job id is required.')
  const response = await nodeHttpRequest(`${runnerEndpoint()}/api/v1/jobs/${encodeURIComponent(jobId)}/cancel`, {
    method: 'POST',
    headers: { ...runnerHeaders(), Accept: 'application/json' },
    timeoutMs: Math.max(1000, Math.trunc(finiteNumber(process.env.RUNNER_HEALTH_TIMEOUT_MS, DEFAULT_VGGT_HEALTH_TIMEOUT_MS))),
  })
  if (!response.ok) throw new Error(`Runner job cancellation failed with HTTP ${response.status}${await readResponseDetail(response)}`)
  return response.json()
}

async function downloadRunnerArtifact(jobId, artifactId, destinationPath) {
  if (!jobId || typeof jobId !== 'string') throw new Error('Runner job id is required.')
  if (!artifactId || typeof artifactId !== 'string') throw new Error('Runner artifact id is required.')
  if (!destinationPath || typeof destinationPath !== 'string') throw new Error('Runner artifact destination is required.')
  return nodeDownloadToFile(
    `${runnerEndpoint()}/api/v1/jobs/${encodeURIComponent(jobId)}/artifacts/${encodeURIComponent(artifactId)}`,
    destinationPath,
    { headers: runnerHeaders() },
  )
}

async function runRunnerJob(request) {
  const submitted = await submitRunnerJob(request)
  let job = submitted
  while (!['succeeded', 'failed', 'cancelled'].includes(job.status)) {
    await new Promise((resolve) => setTimeout(resolve, 2000))
    job = await readRunnerJob(job.id)
  }
  if (job.status !== 'succeeded') {
    const detail = job.stderr_tail || job.error || `Runner job ${job.status}.`
    throw new Error(detail.slice(-4000))
  }

  const recordingPath = path.resolve(request.recordingPath)
  const recordingDir = path.dirname(recordingPath)
  const stem = path.basename(recordingPath).replace(/\.vis\.pb$/i, '')
  const localArtifacts = []
  for (const artifact of job.artifacts || []) {
    const relative = String(artifact.relative_path || artifact.name || '')
    const outputRelative = relative.startsWith('inputs/')
      ? relative.slice('inputs/'.length)
      : path.join(`${stem}.runner-results`, relative)
    const destination = path.resolve(recordingDir, outputRelative)
    if (destination !== recordingDir && !destination.startsWith(`${recordingDir}${path.sep}`)) {
      throw new Error(`Runner returned an unsafe artifact path: ${relative}`)
    }
    await downloadRunnerArtifact(job.id, artifact.id, destination)
    localArtifacts.push({ ...artifact, local_path: destination })
  }
  return { ...job, local_artifacts: localArtifacts }
}

function buildMultipartBody(parts) {
  const boundary = `----BayesMechVision${Date.now().toString(36)}${Math.random().toString(36).slice(2)}`
  const chunks = []
  for (const part of parts) {
    chunks.push(Buffer.from(`--${boundary}\r\n`, 'utf8'))
    if (part.filename) {
      chunks.push(Buffer.from(
        `Content-Disposition: form-data; name="${part.name}"; filename="${part.filename}"\r\n` +
        `Content-Type: ${part.contentType || 'application/octet-stream'}\r\n\r\n`,
        'utf8',
      ))
      chunks.push(Buffer.from(part.value))
      chunks.push(Buffer.from('\r\n', 'utf8'))
    } else {
      chunks.push(Buffer.from(
        `Content-Disposition: form-data; name="${part.name}"\r\n\r\n${String(part.value)}\r\n`,
        'utf8',
      ))
    }
  }
  chunks.push(Buffer.from(`--${boundary}--\r\n`, 'utf8'))
  return {
    body: Buffer.concat(chunks),
    contentType: `multipart/form-data; boundary=${boundary}`,
  }
}

async function readResponseDetail(response) {
  const detail = await response.text().catch(() => '')
  return detail ? `: ${detail.slice(0, 300)}` : ''
}

async function checkWorldgenHealth(endpoint, token) {
  const timeoutMs = Math.max(1000, Math.trunc(finiteNumber(process.env.VGGT_HEALTH_TIMEOUT_MS, DEFAULT_VGGT_HEALTH_TIMEOUT_MS)))
  let response
  try {
    response = await nodeHttpRequest(`${endpoint}/health`, {
      method: 'GET',
      headers: {
        ...runnerHeaders(token),
        Accept: 'application/json',
      },
      timeoutMs,
    })
  } catch (err) {
    const message = err instanceof Error ? err.message : 'connection failed'
    throw new Error(`Cannot reach VGGT model at ${endpoint}: ${message}`)
  }

  if (!response.ok) {
    throw new Error(`VGGT model health check failed with HTTP ${response.status}${await readResponseDetail(response)}`)
  }

  const health = await response.json().catch(() => null)
  if (health && health.ok === false) {
    throw new Error(`VGGT model health check reported not ok: ${JSON.stringify(health).slice(0, 300)}`)
  }
  if (health && health.cuda_available === false) {
    throw new Error('VGGT model is reachable but CUDA is not available on the model host.')
  }
  return health
}

async function restartWorldgenModel() {
  const command = String(process.env.RUNNER_RESTART_COMMAND || process.env.VGGT_RESTART_COMMAND || '').trim()
  if (!command) return false
  const timeoutMs = Math.max(1000, Math.trunc(finiteNumber(process.env.VGGT_RESTART_TIMEOUT_MS, 120000)))
  await Promise.race([
    runShellCommand(command, {
      cwd: findRepoRoot(),
      env: { ...process.env },
    }),
    new Promise((_resolve, reject) => setTimeout(() => reject(new Error(`restart command timed out after ${Math.round(timeoutMs / 1000)}s`)), timeoutMs)),
  ])
  return true
}

async function ensureWorldgenModel(endpoint, token) {
  try {
    return await checkWorldgenHealth(endpoint, token)
  } catch (firstError) {
    const restarted = await restartWorldgenModel().catch((restartError) => {
      const message = restartError instanceof Error ? restartError.message : 'restart failed'
      throw new Error(`${firstError instanceof Error ? firstError.message : 'VGGT health check failed'} Restart failed: ${message}`)
    })
    if (!restarted) throw firstError
    await new Promise((resolve) => setTimeout(resolve, 2500))
    try {
      return await checkWorldgenHealth(endpoint, token)
    } catch (secondError) {
      const message = secondError instanceof Error ? secondError.message : 'health check still failing'
      throw new Error(`VGGT model restart ran, but the model is still unavailable. ${message}`)
    }
  }
}

// First finite value among candidates, else undefined. Used to accept the three
// naming conventions for anisotropic splat fields: renderer (sx/qw), decoded proto
// camelCase (scaleX/rotW), and on-disk preview JSON snake_case (scale_x/rot_w).
function firstFinite(...values) {
  for (const value of values) {
    const number = Number(value)
    if (Number.isFinite(number)) return number
  }
  return undefined
}

// Normalize a splat point to the renderer's field names, carrying anisotropic
// Gaussian parameters (per-axis std devs + orientation quaternion) when present.
// Presence is gated on all three scales being > 0 — a real std dev is strictly
// positive, so proto3's zero-default for absent scalars reads correctly as "no
// anisotropy" and the viewer falls back to an isotropic splat of radius `scale`.
function normalizeSplatPoint(point) {
  const base = {
    x: finiteNumber(point?.x),
    y: finiteNumber(point?.y),
    z: finiteNumber(point?.z),
    r: Math.min(1, Math.max(0, finiteNumber(point?.r, 0.7))),
    g: Math.min(1, Math.max(0, finiteNumber(point?.g, 0.7))),
    b: Math.min(1, Math.max(0, finiteNumber(point?.b, 0.7))),
    opacity: Math.min(1, Math.max(0, finiteNumber(point?.opacity, 0.8))),
    scale: Math.max(0, finiteNumber(point?.scale, 0.02)),
  }
  const sx = firstFinite(point?.sx, point?.scaleX, point?.scale_x)
  const sy = firstFinite(point?.sy, point?.scaleY, point?.scale_y)
  const sz = firstFinite(point?.sz, point?.scaleZ, point?.scale_z)
  if (sx > 0 && sy > 0 && sz > 0) {
    base.sx = sx
    base.sy = sy
    base.sz = sz
    const qx = firstFinite(point?.qx, point?.rotX, point?.rot_x)
    const qy = firstFinite(point?.qy, point?.rotY, point?.rot_y)
    const qz = firstFinite(point?.qz, point?.rotZ, point?.rot_z)
    const qw = firstFinite(point?.qw, point?.rotW, point?.rot_w)
    const validQuat =
      qx !== undefined && qy !== undefined && qz !== undefined && qw !== undefined &&
      qx * qx + qy * qy + qz * qz + qw * qw > 1e-8
    base.qx = validQuat ? qx : 0
    base.qy = validQuat ? qy : 0
    base.qz = validQuat ? qz : 0
    base.qw = validQuat ? qw : 1
  }
  return base
}

function normalizeSplatPreview(preview, fallback = {}) {
  const points = Array.isArray(preview?.points)
    ? preview.points
        .map(normalizeSplatPoint)
        .filter((point) => Number.isFinite(point.x) && Number.isFinite(point.y) && Number.isFinite(point.z))
    : []

  return {
    status: String(preview?.status ?? fallback.status ?? 'complete'),
    plyPath: String(preview?.ply_path ?? fallback.plyPath ?? ''),
    previewJsonPath: String(preview?.preview_json_path ?? fallback.previewJsonPath ?? ''),
    error: String(preview?.error ?? fallback.error ?? ''),
    gaussianCount: finiteNumber(preview?.gaussian_count, fallback.gaussianCount ?? 0),
    previewPointCount: finiteNumber(preview?.preview_point_count, points.length),
    initPointCount: finiteNumber(preview?.init_point_count, fallback.initPointCount ?? 0),
    trainingFrameCount: finiteNumber(preview?.training_frame_count, fallback.trainingFrameCount ?? 0),
    maxSteps: finiteNumber(preview?.max_steps, fallback.maxSteps ?? 0),
    maxGaussians: finiteNumber(preview?.max_gaussians, fallback.maxGaussians ?? 0),
    elapsedSec: finiteNumber(preview?.elapsed_sec, fallback.elapsedSec ?? 0),
    trainer: String(preview?.trainer ?? fallback.trainer ?? ''),
    points,
  }
}

function normalizeRemoteSplatJob(job, fallback = {}) {
  const preview = job?.preview ?? {}
  const previewPoints = normalizeSplatPreview(preview, {}).points
  return {
    jobId: String(job?.job_id ?? job?.jobId ?? fallback.jobId ?? ''),
    status: String(job?.status ?? fallback.status ?? 'queued'),
    stage: String(job?.stage ?? fallback.stage ?? ''),
    message: String(job?.message ?? fallback.message ?? ''),
    progress: Math.min(1, Math.max(0, finiteNumber(job?.progress, fallback.progress ?? 0))),
    currentStep: finiteNumber(job?.current_step, fallback.currentStep ?? 0),
    maxSteps: finiteNumber(job?.max_steps, fallback.maxSteps ?? 0),
    gaussianCount: finiteNumber(job?.gaussian_count, fallback.gaussianCount ?? 0),
    previewPointCount: finiteNumber(job?.preview_point_count ?? preview?.preview_point_count, previewPoints.length),
    initPointCount: finiteNumber(job?.init_point_count, fallback.initPointCount ?? 0),
    trainingFrameCount: finiteNumber(job?.training_frame_count, fallback.trainingFrameCount ?? 0),
    elapsedSec: finiteNumber(job?.elapsed_sec, fallback.elapsedSec ?? 0),
    plyPath: String(job?.ply_path ?? fallback.plyPath ?? ''),
    previewJsonPath: String(job?.preview_json_path ?? fallback.previewJsonPath ?? ''),
    error: String(job?.error ?? fallback.error ?? ''),
    trainer: 'remote-vggt-service',
    points: previewPoints,
  }
}

async function pollWorldgenSplat(jobId) {
  if (!jobId || typeof jobId !== 'string') throw new Error('Splat job id is required.')
  const token = worldgenToken()
  const endpoint = worldgenEndpoint()
  const response = await nodeHttpRequest(`${endpoint}/splat/${encodeURIComponent(jobId)}?include_preview=true`, {
    method: 'GET',
    headers: {
      ...runnerHeaders(token),
      Accept: 'application/json',
    },
    timeoutMs: Math.max(1000, Math.trunc(finiteNumber(process.env.VGGT_HEALTH_TIMEOUT_MS, DEFAULT_VGGT_HEALTH_TIMEOUT_MS))),
  })
  if (!response.ok) {
    throw new Error(`Splat status failed with HTTP ${response.status}${await readResponseDetail(response)}`)
  }
  const normalized = normalizeRemoteSplatJob(await response.json(), { jobId })
  const destinations = worldgenSplatDestinations.get(jobId)
  if (normalized.status === 'complete' && destinations && worldgenUsesRunner()) {
    await nodeDownloadToFile(
      `${endpoint}/splat/${encodeURIComponent(jobId)}/artifact/ply`,
      destinations.plyPath,
      { headers: runnerHeaders(token) },
    )
    normalized.plyPath = destinations.plyPath
    normalized.previewJsonPath = destinations.previewPath
  }
  return normalized
}

async function runWorldgenSplat(vggtPath, recordingPath, options = {}) {
  const paths = worldgenSplatPaths(vggtPath)
  if (worldgenSplatDisabled()) {
    return normalizeSplatPreview(null, {
      status: 'skipped',
      plyPath: paths.plyPath,
      previewJsonPath: paths.previewPath,
      error: 'WORLDGEN_SPLAT is disabled.',
    })
  }
  if (options.windowSize > 0 && options.windowSize < options.frameCount) {
    return normalizeSplatPreview(null, {
      status: 'skipped',
      plyPath: paths.plyPath,
      previewJsonPath: paths.previewPath,
      error: `Splat training requires one coherent VGGT window. Current VGGT window ${options.windowSize} splits ${options.frameCount} frames; unset VGGT_WINDOW or set it to 0.`,
    })
  }

  const repoRoot = findRepoRoot()
  const serverDir = path.join(repoRoot, 'server')
  const scriptPath = path.join(serverDir, 'worldgen', 'scripts', 'train_vggt_splat.py')
  if (!fs.existsSync(scriptPath)) {
    return normalizeSplatPreview(null, {
      status: 'failed',
      plyPath: paths.plyPath,
      previewJsonPath: paths.previewPath,
      error: `Splat trainer not found at ${scriptPath}`,
    })
  }

  const scriptArgs = [
    scriptPath,
    '--vggt-pb', vggtPath,
    '--recording', recordingPath,
    '--workspace', paths.workspacePath,
    '--output-ply', paths.plyPath,
    '--preview-json', paths.previewPath,
  ]
  const python = String(process.env.WORLDGEN_SPLAT_PYTHON || '').trim()
  const command = python || 'uv'
  const args = python ? scriptArgs : ['run', 'python', ...scriptArgs]

  await runProcess(command, args, {
    cwd: serverDir,
    env: {
      ...process.env,
      PYTHONUNBUFFERED: '1',
    },
  })

  if (!fs.existsSync(paths.previewPath)) {
    throw new Error(`Splat trainer completed but did not write ${paths.previewPath}`)
  }
  const preview = JSON.parse(fs.readFileSync(paths.previewPath, 'utf8'))
  return normalizeSplatPreview(preview, {
    status: 'complete',
    plyPath: paths.plyPath,
    previewJsonPath: paths.previewPath,
  })
}

function frameIdentifierFor(frame, fallbackFrameNumber) {
  const identifier = frame?.frameIdentifier ?? {}
  return {
    timestampNs: timestampString(identifier.timestampNs),
    frameNumber: finiteNumber(identifier.frameNumber, fallbackFrameNumber),
    deviceId: String(identifier.deviceId ?? ''),
  }
}

function worldgenPreview(responseJson, sourceFrames, outputPath, request, splatInfo = null) {
  const metadata = responseJson?.metadata ?? {}
  const camera = responseJson?.camera ?? {}
  const pointClouds = Array.isArray(responseJson?.point_clouds) ? responseJson.point_clouds : []
  const perFrameCap = Math.max(1, Math.floor(MAX_WORLDGEN_PREVIEW_POINTS / Math.max(1, pointClouds.length)))
  const points = []
  const frames = []
  let totalPointCount = 0

  for (const [cloudIndex, cloud] of pointClouds.entries()) {
    const sourceFrame = sourceFrames[Number(cloud.sampled_frame_index) || 0] ?? sourceFrames[0]
    const imageSize = metadata.image_sizes_hw?.[cloudIndex] ?? metadata.image_sizes_hw?.[Number(cloud.sampled_frame_index) || 0] ?? []
    const imageHeight = finiteNumber(imageSize[0])
    const imageWidth = finiteNumber(imageSize[1])
    const xyz = Array.isArray(cloud.xyz) ? cloud.xyz : []
    const rgb = Array.isArray(cloud.rgb) ? cloud.rgb : []
    const uv = Array.isArray(cloud.uv) ? cloud.uv : []
    const conf = Array.isArray(cloud.conf) ? cloud.conf : []
    totalPointCount += Number(cloud.num_points) || xyz.length
    const stride = Math.max(1, Math.ceil(xyz.length / perFrameCap))
    let returnedPointCount = 0

    frames.push({
      sampledFrameIndex: Number(cloud.sampled_frame_index) || cloudIndex,
      frameIndex: sourceFrame?.sourceFrameIndex ?? 0,
      frameNumber: sourceFrame?.frameIdentifier?.frameNumber ?? 0,
      imageWidth,
      imageHeight,
      pointCount: Number(cloud.num_points) || xyz.length,
      returnedPointCount: Number(cloud.returned_points) || xyz.length,
    })

    for (let index = 0; index < xyz.length; index += stride) {
      const point = xyz[index]
      if (!Array.isArray(point) || point.length < 3) continue
      const color = Array.isArray(rgb[index]) ? rgb[index] : [0.35, 0.66, 0.9]
      const imagePoint = Array.isArray(uv[index]) ? uv[index] : [0, 0]
      points.push({
        x: finiteNumber(point[0]),
        y: finiteNumber(point[1]),
        z: finiteNumber(point[2]),
        r: Math.min(1, Math.max(0, finiteNumber(color[0], 0.35))),
        g: Math.min(1, Math.max(0, finiteNumber(color[1], 0.66))),
        b: Math.min(1, Math.max(0, finiteNumber(color[2], 0.9))),
        confidence: finiteNumber(conf[index], 0),
        frameIndex: sourceFrame?.sourceFrameIndex ?? 0,
        frameNumber: sourceFrame?.frameIdentifier?.frameNumber ?? 0,
        framePointIndex: index,
        u: finiteNumber(imagePoint[0]),
        v: finiteNumber(imagePoint[1]),
      })
      returnedPointCount += 1
    }

    frames[frames.length - 1].returnedPointCount = returnedPointCount
  }

  const centers = Array.isArray(responseJson?.camera?.camera_centers) ? responseJson.camera.camera_centers : []
  const cameras = centers
    .map((center, index) => {
      const sourceFrame = sourceFrames[index] ?? sourceFrames[0]
      if (!Array.isArray(center) || center.length < 3) return null
      const matrix = flattenNumbers(camera.camera_to_world?.[index] ?? [])
      const intrinsics = flattenNumbers(camera.intrinsics?.[index] ?? [])
      return {
        frameIndex: sourceFrame?.sourceFrameIndex ?? index,
        frameNumber: sourceFrame?.frameIdentifier?.frameNumber ?? index,
        x: finiteNumber(center[0]),
        y: finiteNumber(center[1]),
        z: finiteNumber(center[2]),
        matrix,
        intrinsics,
      }
    })
    .filter(Boolean)

  return {
    id: request.requestId,
    outputPath,
    markerStart: request.markerStart,
    markerEnd: request.markerEnd,
    startFrameIndex: request.startFrameIndex,
    endFrameIndex: request.endFrameIndex,
    frameCount: sourceFrames.length,
    pointCount: totalPointCount,
    returnedPointCount: points.length,
    model: String(responseJson?.metadata?.model ?? 'VGGT-Omega'),
    elapsedSec: finiteNumber(responseJson?.metadata?.elapsed_sec),
    frames,
    points,
    cameras,
    splat: splatInfo
      ? {
          status: splatInfo.status,
          jobId: splatInfo.jobId,
          stage: splatInfo.stage,
          message: splatInfo.message,
          progress: splatInfo.progress,
          currentStep: splatInfo.currentStep,
          plyPath: splatInfo.plyPath,
          previewJsonPath: splatInfo.previewJsonPath,
          error: splatInfo.error,
          gaussianCount: splatInfo.gaussianCount,
          previewPointCount: splatInfo.previewPointCount,
          initPointCount: splatInfo.initPointCount,
          trainingFrameCount: splatInfo.trainingFrameCount,
          maxSteps: splatInfo.maxSteps,
          maxGaussians: splatInfo.maxGaussians,
          elapsedSec: splatInfo.elapsedSec,
          trainer: splatInfo.trainer,
        }
      : null,
    splatPoints: splatInfo?.points ?? [],
  }
}

function encodeWorldgenResponse(responseJson, sourceFrames, request, endpoint, splatInfo = null) {
  const metadata = responseJson?.metadata ?? {}
  const camera = responseJson?.camera ?? {}
  const pointClouds = Array.isArray(responseJson?.point_clouds) ? responseJson.point_clouds : []

  const cameras = sourceFrames.map((sourceFrame, index) => ({
    frameIdentifier: sourceFrame.frameIdentifier,
    sourceFrameIndex: sourceFrame.sourceFrameIndex,
    sampledFrameIndex: index,
    extrinsicsCfw: flattenNumbers(camera.extrinsics?.[index] ?? []),
    intrinsics: flattenNumbers(camera.intrinsics?.[index] ?? []),
    cameraToWorld: flattenNumbers(camera.camera_to_world?.[index] ?? []),
    cameraCenter: flattenNumbers(camera.camera_centers?.[index] ?? []),
  }))

  const pointCloudFrames = pointClouds.map((cloud, index) => {
    const sourceFrame = sourceFrames[Number(cloud.sampled_frame_index) || index] ?? sourceFrames[index] ?? sourceFrames[0]
    const imageSize = metadata.image_sizes_hw?.[index] ?? metadata.image_sizes_hw?.[Number(cloud.sampled_frame_index) || index] ?? []
    return {
      frameIdentifier: sourceFrame?.frameIdentifier,
      sourceFrameIndex: sourceFrame?.sourceFrameIndex ?? index,
      sampledFrameIndex: Number(cloud.sampled_frame_index) || index,
      pointCount: Number(cloud.num_points) || 0,
      returnedPointCount: Number(cloud.returned_points) || (Array.isArray(cloud.xyz) ? cloud.xyz.length : 0),
      xyzF32Le: float32Bytes(cloud.xyz ?? []),
      rgbF32Le: float32Bytes(cloud.rgb ?? []),
      uvF32Le: float32Bytes(cloud.uv ?? []),
      confidenceF32Le: float32Bytes(cloud.conf ?? []),
      imageWidth: finiteNumber(imageSize[1]),
      imageHeight: finiteNumber(imageSize[0]),
    }
  })

  return getVggtResponseType().create({
    requestId: request.requestId,
    model: String(metadata.model ?? 'VGGT-Omega'),
    checkpoint: String(metadata.checkpoint ?? ''),
    device: String(metadata.device ?? ''),
    sourceRecordingPath: request.recordingPath,
    markerStart: request.markerStart,
    markerEnd: request.markerEnd,
    startFrameIndex: request.startFrameIndex,
    endFrameIndex: request.endFrameIndex,
    frameCount: sourceFrames.length,
    cameras,
    pointClouds: pointCloudFrames,
    cameraConvention: String(metadata.camera_convention ?? ''),
    pointcloudSpace: String(metadata.pointcloud_space ?? ''),
    elapsedSec: finiteNumber(metadata.elapsed_sec),
    endpoint,
    gaussianSplat: splatInfo
      ? {
          status: splatInfo.status,
          plyPath: splatInfo.plyPath,
          previewJsonPath: splatInfo.previewJsonPath,
          error: splatInfo.error,
          gaussianCount: splatInfo.gaussianCount,
          previewPointCount: splatInfo.previewPointCount,
          initPointCount: splatInfo.initPointCount,
          trainingFrameCount: splatInfo.trainingFrameCount,
          maxSteps: splatInfo.maxSteps,
          maxGaussians: splatInfo.maxGaussians,
          elapsedSec: splatInfo.elapsedSec,
          trainer: splatInfo.trainer,
          jobId: splatInfo.jobId,
          stage: splatInfo.stage,
          message: splatInfo.message,
          progress: splatInfo.progress,
          currentStep: splatInfo.currentStep,
          previewPoints: encodeSplatPreviewPoints(splatInfo.points),
        }
      : undefined,
    resolution: finiteNumber(metadata.resolution),
    preprocessMode: String(metadata.preprocess_mode ?? ''),
    confidenceThreshold: finiteNumber(metadata.conf_thresh),
    maxPointsPerFrame: finiteNumber(request.maxPointsPerFrame),
  })
}

function legacyWorldgenPaths(recordingPath, canonicalPath) {
  const dir = path.dirname(recordingPath)
  const baseName = path.basename(recordingPath).slice(0, -'.vis.pb'.length)
  let entries = []
  try {
    entries = fs.readdirSync(dir, { withFileTypes: true })
  } catch {
    return []
  }
  return entries
    .filter((entry) => entry.isFile() && entry.name.startsWith(`${baseName}.`) && entry.name.endsWith('.vggt.pb'))
    .map((entry) => path.join(dir, entry.name))
    .filter((filePath) => path.resolve(filePath) !== path.resolve(canonicalPath))
    .map((filePath) => ({ filePath, modifiedMs: safeStat(filePath)?.mtimeMs ?? 0 }))
    .sort((left, right) => left.modifiedMs - right.modifiedMs)
    .map((entry) => entry.filePath)
}

function persistWorldgenComputation(recordingPath, outputPath, message) {
  const type = getVggtResponseType()
  let history = []
  if (fs.existsSync(outputPath)) {
    history = readLengthDelimitedProtos(outputPath, type)
  } else {
    // First write to the canonical file also folds in old marker-pair files so
    // projects created by earlier builds keep all of their computed frames.
    for (const legacyPath of legacyWorldgenPaths(recordingPath, outputPath)) {
      history.push(...readLengthDelimitedProtos(legacyPath, type))
    }
  }
  const nextHistory = uniqueWorldgenMessages([...history, message])
  writeLengthDelimitedProtos(outputPath, type, nextHistory)
  return nextHistory
}

async function runWorldgen(request) {
  if (!request?.recordingPath || typeof request.recordingPath !== 'string') {
    throw new Error('World Modeling requires a selected recording.')
  }
  if (!request.recordingPath.endsWith('.vis.pb')) {
    throw new Error('World Modeling requires a .vis.pb recording.')
  }

  const token = worldgenToken()

  const resolved = path.resolve(request.recordingPath)
  const offsets = getFrameOffsets(resolved)
  if (!offsets.length) throw new Error('The selected recording has no frames.')

  const startFrameIndex = Math.max(0, Math.min(offsets.length - 1, Math.trunc(Number(request.startFrameIndex) || 0)))
  const endFrameIndex = Math.max(0, Math.min(offsets.length - 1, Math.trunc(Number(request.endFrameIndex) || 0)))
  const firstIndex = Math.min(startFrameIndex, endFrameIndex)
  const lastIndex = Math.max(startFrameIndex, endFrameIndex)
  const requestedFrameCount = lastIndex - firstIndex + 1
  if (requestedFrameCount <= 0) throw new Error('The selected marker range is empty.')

  const sourceFrames = []
  const frameUploads = []
  const fd = fs.openSync(resolved, 'r')
  try {
    for (let frameIndex = firstIndex; frameIndex <= lastIndex; frameIndex += 1) {
      const frame = decodedFrameAt(resolved, offsets, frameIndex, fd)
      const jpeg = rgbFrameJpeg(frame)
      if (!jpeg) continue
      const frameIdentifier = frameIdentifierFor(frame, frameIndex)
      sourceFrames.push({ sourceFrameIndex: frameIndex, frameIdentifier })
      frameUploads.push({
        name: 'frames',
        filename: `frame_${String(frameIndex).padStart(6, '0')}.jpg`,
        contentType: 'image/jpeg',
        value: Buffer.from(jpeg),
      })
    }
  } finally {
    fs.closeSync(fd)
  }

  if (!sourceFrames.length) throw new Error('No JPEG RGB frames were available in the selected marker range.')

  const firstTimestamp = sourceFrames[0].frameIdentifier.timestampNs
  const lastTimestamp = sourceFrames[sourceFrames.length - 1].frameIdentifier.timestampNs
  const duration = timestampDeltaSeconds(firstTimestamp, lastTimestamp)
  const fps = duration > 0 && sourceFrames.length > 1 ? (sourceFrames.length - 1) / duration : 30
  const resolution = Math.max(1, Math.trunc(Number(request.resolution) || 512))
  const confidenceThreshold = Number.isFinite(Number(request.confidenceThreshold)) ? Number(request.confidenceThreshold) : 0.5
  const defaultMaxPoints = finiteNumber(process.env.VGGT_MAX_POINTS_PER_FRAME, DEFAULT_WORLDGEN_POINTS_PER_FRAME)
  const maxPointsPerFrame = Math.max(0, Math.trunc(finiteNumber(request.maxPointsPerFrame, defaultMaxPoints)))
  const windowSize = Math.max(0, Math.trunc(finiteNumber(request.windowSize, finiteNumber(process.env.VGGT_WINDOW, 0))))

  const multipart = buildMultipartBody([
    ...frameUploads,
    { name: 'fps', value: String(fps) },
    { name: 'max_frames', value: '0' },
    { name: 'resolution', value: String(resolution) },
    { name: 'preprocess_mode', value: 'balanced' },
    { name: 'conf_thresh', value: String(confidenceThreshold) },
    { name: 'window', value: String(windowSize) },
    { name: 'max_points_per_frame', value: String(maxPointsPerFrame) },
    { name: 'response_format', value: 'json' },
    { name: 'start_splat', value: 'true' },
  ])

  const endpoint = worldgenEndpoint()
  await ensureWorldgenModel(endpoint, token)

  const requestTimeoutMs = Math.max(1000, Math.trunc(finiteNumber(process.env.VGGT_REQUEST_TIMEOUT_MS, DEFAULT_VGGT_REQUEST_TIMEOUT_MS)))
  let response
  try {
    response = await nodeHttpRequest(`${endpoint}/infer`, {
      method: 'POST',
      headers: {
        ...runnerHeaders(token),
        Accept: 'application/json',
        'Content-Type': multipart.contentType,
      },
      body: multipart.body,
      timeoutMs: requestTimeoutMs,
    })
  } catch (err) {
    const message = err instanceof Error ? err.message : 'connection failed'
    throw new Error(`VGGT inference connection failed at ${endpoint}: ${message}`)
  }

  if (!response.ok) {
    throw new Error(`VGGT request failed with HTTP ${response.status}${await readResponseDetail(response)}`)
  }

  const responseJson = await response.json()
  const baseName = path.basename(resolved).slice(0, -'.vis.pb'.length)
  const outputPath = path.join(path.dirname(resolved), `${baseName}.vggt.pb`)
  const settledRequest = {
    ...request,
    startFrameIndex: firstIndex,
    endFrameIndex: lastIndex,
    maxPointsPerFrame,
  }
  const splatInfo = responseJson?.splat_job
    ? normalizeRemoteSplatJob(responseJson.splat_job)
    : normalizeRemoteSplatJob(null, {
        status: 'skipped',
        message: 'VGGT response did not include a remote splat job.',
      })
  if (splatInfo.jobId) worldgenSplatDestinations.set(splatInfo.jobId, worldgenSplatPaths(outputPath))

  const message = encodeWorldgenResponse(
    responseJson,
    sourceFrames,
    settledRequest,
    endpoint,
    splatInfo,
  )
  const history = persistWorldgenComputation(resolved, outputPath, message)
  return worldgenPreviewFromMessages(history, outputPath)
}

if (isElectronRuntime) {
  app.setName(APP_NAME)

  ipcMain.handle('project:select', async () => {
    const result = await dialog.showOpenDialog(mainWindow, {
      title: 'Open BayesMech Vision Project',
      properties: ['openDirectory'],
    })

    if (result.canceled || !result.filePaths[0]) {
      return { cancelled: true }
    }

    return scanProject(result.filePaths[0])
  })

  ipcMain.handle('project:scan', (_event, projectPath) => scanProject(projectPath))

  ipcMain.handle('vis:select-files', async () => {
    const result = await dialog.showOpenDialog(mainWindow, {
      title: 'Open BayesMech Vision Recordings',
      properties: ['openFile', 'multiSelections'],
      filters: [
        { name: 'BayesMech protobuf recordings', extensions: ['pb'] },
        { name: 'All files', extensions: ['*'] },
      ],
    })

    if (result.canceled || result.filePaths.length === 0) {
      return { cancelled: true }
    }

    return scanVisFiles(result.filePaths)
  })

  ipcMain.handle('vis:summary', (_event, filePath) => readVisSummary(filePath))

  ipcMain.handle('vis:frame', (_event, filePath, frameIndex) => readVisFrame(filePath, frameIndex))
  ipcMain.handle('vis:sensors', (_event, filePath) => readVisSensors(filePath))
  ipcMain.handle('idoslam:read', (_event, filePath) => readIdoSlam(filePath))

  ipcMain.handle('seg:masks', (_event, filePath, frameNumber) => readSegmentationMasks(filePath, frameNumber))
  ipcMain.handle('seg:labels', (_event, filePath) => readSegmentationLabels(filePath))
  ipcMain.handle('motioncap:frame', (_event, filePath, frameNumber) => readMotionCapture(filePath, frameNumber))
  ipcMain.handle('chat:thread', (_event, recordingPath) => readChatThread(recordingPath))
  ipcMain.handle('chat-workspace:load', (_event, videoId, recordingPath) => loadChatWorkspace(videoId, recordingPath))
  ipcMain.handle('chat-workspace:create', (_event, videoId, recordingPath) => createChatSession(videoId, recordingPath))
  ipcMain.handle('chat-workspace:save', (_event, videoId, recordingPath, session) => saveChatSession(videoId, recordingPath, session))
  ipcMain.handle('chat-workspace:activate', (_event, videoId, recordingPath, chatId) => setActiveChatSession(videoId, recordingPath, chatId))
  ipcMain.handle('worldgen:run', (_event, request) => runWorldgen(request))
  ipcMain.handle('worldgen:read', (_event, filePath) => readWorldgen(filePath))
  ipcMain.handle('worldgen:splat-status', (_event, jobId) => pollWorldgenSplat(jobId))
  ipcMain.handle('worldgen:save-splat', (_event, filePath, splat) => saveWorldgenSplat(filePath, splat))
  ipcMain.handle('runner:health', () => readRunnerHealth())
  ipcMain.handle('runner:capabilities', () => readRunnerCapabilities())
  ipcMain.handle('runner:submit', (_event, request) => submitRunnerJob(request))
  ipcMain.handle('runner:run', (_event, request) => runRunnerJob(request))
  ipcMain.handle('runner:job', (_event, jobId) => readRunnerJob(jobId))
  ipcMain.handle('runner:cancel', (_event, jobId) => cancelRunnerJob(jobId))
  ipcMain.handle('runner:download-artifact', (_event, jobId, artifactId, destinationPath) => (
    downloadRunnerArtifact(jobId, artifactId, destinationPath)
  ))

  ipcMain.handle('path:reveal', async (_event, filePath) => {
    if (!filePath || typeof filePath !== 'string') return false
    shell.showItemInFolder(filePath)
    return true
  })

  ipcMain.handle('window:action', (event, action) => {
    const window = BrowserWindow.fromWebContents(event.sender)
    if (!window) return false
    switch (action) {
      case 'reload':
        window.webContents.reload()
        return true
      case 'toggle-devtools':
        window.webContents.toggleDevTools()
        return true
      case 'reset-zoom':
        window.webContents.setZoomFactor(1)
        return true
      case 'zoom-in':
        window.webContents.setZoomFactor(Math.min(3, window.webContents.getZoomFactor() * 1.1))
        return true
      case 'zoom-out':
        window.webContents.setZoomFactor(Math.max(0.4, window.webContents.getZoomFactor() / 1.1))
        return true
      case 'toggle-fullscreen':
        window.setFullScreen(!window.isFullScreen())
        return true
      case 'minimize':
        window.minimize()
        return true
      case 'close':
        window.close()
        return true
      default:
        return false
    }
  })

  app.whenReady().then(() => {
    installMenu()
    createWindow()

    app.on('activate', () => {
      if (BrowserWindow.getAllWindows().length === 0) createWindow()
    })
  })

  app.on('window-all-closed', () => {
    if (process.platform !== 'darwin') app.quit()
  })
}

module.exports = {
  scanProject,
  scanVisFiles,
  readVisSummary,
  readVisFrame,
  readVisSensors,
  readIdoSlam,
  readSegmentationMasks,
  readMotionCapture,
  readChatThread,
  loadChatWorkspace,
  createChatSession,
  saveChatSession,
  setActiveChatSession,
  runWorldgen,
  readWorldgen,
  pollWorldgenSplat,
  saveWorldgenSplat,
  readRunnerHealth,
  readRunnerCapabilities,
  submitRunnerJob,
  runRunnerJob,
  readRunnerJob,
  cancelRunnerJob,
  downloadRunnerArtifact,
}
