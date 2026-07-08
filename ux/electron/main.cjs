const electron = require('electron')
const fs = require('node:fs')
const path = require('node:path')
const protobuf = require('protobufjs')

const isElectronRuntime = typeof electron !== 'string'
const { app, BrowserWindow, Menu, dialog, ipcMain, shell, nativeImage } = isElectronRuntime ? electron : {}

const APP_NAME = 'BayesMech Vision'
const FRAME_SIZE_LIMIT = 10 * 1024 * 1024
const MAX_SAMPLE_FRAMES = 96
const MAX_POINTS_PER_FRAME = 220
const MAX_WORLDGEN_PREVIEW_POINTS = 120000
const DEFAULT_VGGT_ENDPOINT = 'https://anonymous-versus-underground-twice.trycloudflare.com'

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
  { key: 'point-cloud', title: 'Point Clouds', kind: 'geometry', source: 'vis' },
  { key: 'surface-planes', title: 'Surface Estimates', kind: 'geometry', source: 'vis' },
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
    title: 'Localization and Mapping',
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
let vggtResponseType = null

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
    webPreferences: {
      preload: path.join(__dirname, 'preload.cjs'),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: false,
    },
  })
  if (icon) mainWindow.setIcon(icon)

  if (process.env.VITE_DEV_SERVER_URL) {
    mainWindow.loadURL(process.env.VITE_DEV_SERVER_URL)
  } else {
    mainWindow.loadFile(path.join(__dirname, '..', 'dist', 'index.html'))
  }
}

function installMenu() {
  const template = [
    {
      label: 'File',
      submenu: [
        {
          label: 'Open Project...',
          accelerator: 'CmdOrCtrl+O',
          click: () => mainWindow?.webContents.send('menu:open-project'),
        },
        { type: 'separator' },
        { role: 'quit' },
      ],
    },
    {
      label: 'View',
      submenu: [
        { role: 'reload' },
        { role: 'toggleDevTools' },
        { type: 'separator' },
        { role: 'resetZoom' },
        { role: 'zoomIn' },
        { role: 'zoomOut' },
        { type: 'separator' },
        { role: 'togglefullscreen' },
      ],
    },
    {
      label: 'Window',
      submenu: [{ role: 'minimize' }, { role: 'close' }],
    },
  ]
  Menu.setApplicationMenu(Menu.buildFromTemplate(template))
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

function readFrameIndex(filePath) {
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
      if (length === 0 || length > FRAME_SIZE_LIMIT || position + 4 + length > stat.size) {
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

function safeFilePart(value) {
  const cleaned = String(value ?? '').replace(/[^A-Za-z0-9]+/g, '')
  return cleaned || 'Marker'
}

function writeLengthDelimitedProto(filePath, payload) {
  const header = Buffer.allocUnsafe(4)
  header.writeUInt32BE(payload.length, 0)
  fs.writeFileSync(filePath, Buffer.concat([header, Buffer.from(payload)]))
}

function worldgenEndpoint() {
  return String(process.env.VGGT_ENDPOINT || DEFAULT_VGGT_ENDPOINT).replace(/\/+$/g, '')
}

function worldgenToken() {
  return String(process.env.VGGT_API_TOKEN || '').trim()
}

function frameIdentifierFor(frame, fallbackFrameNumber) {
  const identifier = frame?.frameIdentifier ?? {}
  return {
    timestampNs: timestampString(identifier.timestampNs),
    frameNumber: finiteNumber(identifier.frameNumber, fallbackFrameNumber),
    deviceId: String(identifier.deviceId ?? ''),
  }
}

function worldgenPreview(responseJson, sourceFrames, outputPath, request) {
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
  }
}

function encodeWorldgenResponse(responseJson, sourceFrames, request, endpoint) {
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
  })
}

async function runWorldgen(request) {
  if (!request?.recordingPath || typeof request.recordingPath !== 'string') {
    throw new Error('Worldgen requires a selected recording.')
  }
  if (!request.recordingPath.endsWith('.vis.pb')) {
    throw new Error('Worldgen requires a .vis.pb recording.')
  }
  if (typeof fetch !== 'function' || typeof FormData !== 'function' || typeof Blob !== 'function') {
    throw new Error('This Electron runtime does not provide fetch/FormData support.')
  }

  const token = worldgenToken()
  if (!token) {
    throw new Error('VGGT_API_TOKEN is not set. Add it to the environment before starting the native app.')
  }

  const resolved = path.resolve(request.recordingPath)
  const offsets = getFrameOffsets(resolved)
  if (!offsets.length) throw new Error('The selected recording has no frames.')

  const startFrameIndex = Math.max(0, Math.min(offsets.length - 1, Math.trunc(Number(request.startFrameIndex) || 0)))
  const endFrameIndex = Math.max(0, Math.min(offsets.length - 1, Math.trunc(Number(request.endFrameIndex) || 0)))
  const firstIndex = Math.min(startFrameIndex, endFrameIndex)
  const lastIndex = Math.max(startFrameIndex, endFrameIndex)
  const requestedFrameCount = lastIndex - firstIndex + 1
  if (requestedFrameCount <= 0) throw new Error('The selected marker range is empty.')

  const form = new FormData()
  const sourceFrames = []
  const fd = fs.openSync(resolved, 'r')
  try {
    for (let frameIndex = firstIndex; frameIndex <= lastIndex; frameIndex += 1) {
      const frame = decodedFrameAt(resolved, offsets, frameIndex, fd)
      const jpeg = rgbFrameJpeg(frame)
      if (!jpeg) continue
      const frameIdentifier = frameIdentifierFor(frame, frameIndex)
      sourceFrames.push({ sourceFrameIndex: frameIndex, frameIdentifier })
      form.append('frames', new Blob([jpeg], { type: 'image/jpeg' }), `frame_${String(frameIndex).padStart(6, '0')}.jpg`)
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
  const maxPointsPerFrame = Math.max(0, Math.trunc(Number(request.maxPointsPerFrame) || Number(process.env.VGGT_MAX_POINTS_PER_FRAME) || 20000))
  const windowSize = Math.max(1, Math.trunc(Number(request.windowSize) || Number(process.env.VGGT_WINDOW) || 1))

  form.append('fps', String(fps))
  form.append('max_frames', '0')
  form.append('resolution', String(resolution))
  form.append('preprocess_mode', 'balanced')
  form.append('conf_thresh', String(confidenceThreshold))
  form.append('window', String(windowSize))
  form.append('max_points_per_frame', String(maxPointsPerFrame))
  form.append('response_format', 'json')

  const endpoint = worldgenEndpoint()
  const response = await fetch(`${endpoint}/infer`, {
    method: 'POST',
    headers: {
      Authorization: `Bearer ${token}`,
      Accept: 'application/json',
    },
    body: form,
  })

  if (!response.ok) {
    const detail = await response.text().catch(() => '')
    throw new Error(`VGGT request failed with HTTP ${response.status}${detail ? `: ${detail.slice(0, 300)}` : ''}`)
  }

  const responseJson = await response.json()
  const safeSegment = `${safeFilePart(request.markerStart)}To${safeFilePart(request.markerEnd)}`
  const baseName = path.basename(resolved).slice(0, -'.vis.pb'.length)
  const outputPath = path.join(path.dirname(resolved), `${baseName}.${safeSegment}.vggt.pb`)
  const message = encodeWorldgenResponse(
    responseJson,
    sourceFrames,
    { ...request, startFrameIndex: firstIndex, endFrameIndex: lastIndex },
    endpoint,
  )
  const payload = getVggtResponseType().encode(message).finish()
  writeLengthDelimitedProto(outputPath, payload)

  return worldgenPreview(
    responseJson,
    sourceFrames,
    outputPath,
    { ...request, startFrameIndex: firstIndex, endFrameIndex: lastIndex },
  )
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

  ipcMain.handle('seg:masks', (_event, filePath, frameNumber) => readSegmentationMasks(filePath, frameNumber))
  ipcMain.handle('seg:labels', (_event, filePath) => readSegmentationLabels(filePath))
  ipcMain.handle('worldgen:run', (_event, request) => runWorldgen(request))

  ipcMain.handle('path:reveal', async (_event, filePath) => {
    if (!filePath || typeof filePath !== 'string') return false
    shell.showItemInFolder(filePath)
    return true
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
  readSegmentationMasks,
  runWorldgen,
}
