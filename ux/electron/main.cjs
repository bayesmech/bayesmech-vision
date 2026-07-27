const electron = require('electron')
const { spawn } = require('node:child_process')
const crypto = require('node:crypto')
const fs = require('node:fs')
const http = require('node:http')
const https = require('node:https')
const os = require('node:os')
const path = require('node:path')
const protobuf = require('protobufjs')
const { Client: McpClient } = require('@modelcontextprotocol/sdk/client/index.js')
const { StreamableHTTPClientTransport } = require('@modelcontextprotocol/sdk/client/streamableHttp.js')

const isElectronRuntime = typeof electron !== 'string'
const { app, BrowserWindow, Menu, dialog, ipcMain, shell, nativeImage } = isElectronRuntime ? electron : {}

const APP_NAME = 'BayesMech Vision'
const FRAME_SIZE_LIMIT = 10 * 1024 * 1024
const IDOSLAM_FRAME_SIZE_LIMIT = 512 * 1024 * 1024
const MAX_SAMPLE_FRAMES = 96
const MAX_GEMMA_UPLOAD_FRAMES = 32
const DEFAULT_GEMMA_SAMPLE_FPS = 4
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
const DEFAULT_GEMMA_REQUEST_TIMEOUT_MS = 30 * 60 * 1000
const DEFAULT_STREAMLOG_ENDPOINT = 'http://127.0.0.1:8080'

let managedStreamlogProcess = null
let managedStreamlogStart = null
let managedStreamlogError = ''

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
    title: 'Domain specific reconstruction',
    kind: 'protobuf',
    suffixes: ['snook.pb'],
  },
  {
    key: 'pongtown',
    title: 'Domain specific reconstruction',
    kind: 'protobuf',
    suffixes: ['pongtown.pb'],
  },
]

let mainWindow = null
let perceiverType = null
let controlProjectType = null
let segmentationType = null
let motionCaptureType = null
let vggtResponseType = null
let gensparkResponseType = null
let chatHistoryType = null
let idoSlamType = null
let pongtownType = null
let snookestownType = null
const worldgenSplatDestinations = new Map()
const runnerBackgroundJobs = new Map()
let runnerJobEventRequest = null
let runnerJobEventReconnectTimer = null

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

function streamlogEndpoint() {
  return String(
    process.env.STREAMLOG_ENDPOINT
      || process.env.VITE_STREAMLOG_ENDPOINT
      || DEFAULT_STREAMLOG_ENDPOINT,
  ).replace(/\/+$/, '')
}

function isLocalStreamlogEndpoint(endpoint) {
  try {
    const parsed = new URL(endpoint)
    return (
      parsed.protocol === 'http:'
      && ['127.0.0.1', 'localhost', '::1'].includes(parsed.hostname)
    )
  } catch {
    return false
  }
}

async function streamlogHealth(endpoint = streamlogEndpoint()) {
  try {
    const response = await nodeHttpRequest(`${endpoint}/api/health`, {
      headers: { Accept: 'application/json' },
      timeoutMs: 1000,
    })
    return response.ok
  } catch {
    return false
  }
}

function stopManagedStreamlog() {
  const child = managedStreamlogProcess
  managedStreamlogProcess = null
  managedStreamlogStart = null
  if (child && child.exitCode == null && !child.killed) child.kill('SIGTERM')
}

async function ensureStreamlogRunning() {
  const endpoint = streamlogEndpoint()
  if (!isLocalStreamlogEndpoint(endpoint)) {
    return { endpoint, ready: await streamlogHealth(endpoint), managed: false }
  }
  if (await streamlogHealth(endpoint)) {
    return { endpoint, ready: true, managed: Boolean(managedStreamlogProcess) }
  }
  if (managedStreamlogStart) return managedStreamlogStart

  managedStreamlogStart = (async () => {
    const serverDirectory = path.join(findRepoRoot(), 'server')
    const venvPython = path.join(serverDirectory, '.venv', 'bin', 'python')
    if (!fs.existsSync(path.join(serverDirectory, 'streamlog', 'main.py'))) {
      return {
        endpoint,
        ready: false,
        managed: false,
        error: 'The local Streamlog server source is unavailable.',
      }
    }
    const command = fs.existsSync(venvPython) ? venvPython : 'uv'
    const parsedEndpoint = new URL(endpoint)
    const bindHost = parsedEndpoint.hostname === 'localhost'
      ? '127.0.0.1'
      : parsedEndpoint.hostname
    const bindPort = parsedEndpoint.port || '80'
    const args = fs.existsSync(venvPython)
      ? ['-m', 'uvicorn', 'streamlog.main:app', '--host', bindHost, '--port', bindPort]
      : ['run', 'uvicorn', 'streamlog.main:app', '--host', bindHost, '--port', bindPort]
    managedStreamlogError = ''
    const child = spawn(command, args, {
      cwd: serverDirectory,
      env: { ...process.env, PYTHONUNBUFFERED: '1' },
      stdio: ['ignore', 'ignore', 'pipe'],
    })
    managedStreamlogProcess = child
    child.stderr?.on('data', (chunk) => {
      managedStreamlogError = `${managedStreamlogError}${chunk.toString('utf8')}`.slice(-8000)
    })
    child.on('error', (error) => {
      managedStreamlogError = error.message
    })
    child.on('close', () => {
      if (managedStreamlogProcess === child) managedStreamlogProcess = null
    })

    for (let attempt = 0; attempt < 40; attempt += 1) {
      if (await streamlogHealth(endpoint)) {
        return { endpoint, ready: true, managed: true }
      }
      if (child.exitCode != null) break
      await new Promise((resolve) => setTimeout(resolve, 200))
    }
    return {
      endpoint,
      ready: false,
      managed: true,
      error: managedStreamlogError.trim() || 'Streamlog did not become ready.',
    }
  })()

  try {
    return await managedStreamlogStart
  } finally {
    managedStreamlogStart = null
  }
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

function getControlProjectType() {
  if (controlProjectType) return controlProjectType

  const protoDir = path.join(findRepoRoot(), 'proto')
  const root = new protobuf.Root()
  root.resolvePath = (_origin, target) => path.join(protoDir, target)
  root.loadSync(['control.proto'], { keepCase: false })
  root.resolveAll()
  controlProjectType = root.lookupType('bayesmech.vision.ControlProject')
  return controlProjectType
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

function getPongtownType() {
  if (pongtownType) return pongtownType

  const protoDir = path.join(findRepoRoot(), 'proto')
  const root = new protobuf.Root()
  root.resolvePath = (_origin, target) => path.join(protoDir, target)
  root.loadSync(['pongtown.proto'], { keepCase: false })
  root.resolveAll()
  pongtownType = root.lookupType('bayesmech.vision.PongtownResponse')
  return pongtownType
}

function getSnookestownType() {
  if (snookestownType) return snookestownType

  const protoDir = path.join(findRepoRoot(), 'proto')
  const root = new protobuf.Root()
  root.resolvePath = (_origin, target) => path.join(protoDir, target)
  root.loadSync(['snookestown.proto'], { keepCase: false })
  root.resolveAll()
  snookestownType = root.lookupType('bayesmech.vision.SnookerResponse')
  return snookestownType
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

function projectDisplayName(directoryPath) {
  const metadata = readJsonFile(path.join(directoryPath, '.project.json'), null)
  const displayName = String(metadata?.displayName || '').trim()
  return displayName || undefined
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

function walkForControlFiles(rootPath) {
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
      if (entry.isFile() && entry.name.endsWith('.control.pb')) {
        results.push(entryPath)
      }
    }
  }

  return results.sort((a, b) => a.localeCompare(b))
}

function safeControlRecordingPath(directoryPath, recordingFile) {
  const name = String(recordingFile || '').trim()
  if (!name || !name.endsWith('.vis.pb') || path.isAbsolute(name)) return null
  const resolved = path.resolve(directoryPath, name)
  const relative = path.relative(directoryPath, resolved)
  if (!relative || relative.startsWith('..') || path.isAbsolute(relative)) return null
  return resolved
}

function readControlProject(controlPath) {
  const resolved = path.resolve(controlPath)
  const type = getControlProjectType()
  const decoded = type.decode(fs.readFileSync(resolved))
  const project = type.toObject(decoded, {
    longs: Number,
    enums: String,
    defaults: true,
    arrays: true,
  })
  return {
    ...project,
    manifestPath: resolved,
    directoryPath: path.dirname(resolved),
  }
}

function recordingForControlProject(projectPath, controlPath, index) {
  let manifest
  try {
    manifest = readControlProject(controlPath)
  } catch (error) {
    return { error: `Could not read ${path.basename(controlPath)}: ${error.message}` }
  }
  const enabledDevices = manifest.devices.filter((device) => device.enabled)
  const primary = enabledDevices.find((device) => device.role === 'PRIMARY_DEVICE')
    ?? enabledDevices[0]
  const primaryPath = primary
    ? safeControlRecordingPath(manifest.directoryPath, primary.recordingFile)
    : null
  if (!primary || !primaryPath) {
    return { error: `${path.basename(controlPath)} does not declare a primary .vis.pb stream.` }
  }

  const controlStat = safeStat(controlPath)
  const analyses = [{
    key: 'control',
    title: 'Control',
    kind: 'control',
    source: 'artifact',
    baseKey: 'control',
    videoContext: 'main',
    sourceVideoPath: primaryPath,
    suffix: 'control.pb',
    path: controlPath,
    relativePath: path.relative(projectPath, controlPath),
    sizeBytes: controlStat?.size ?? 0,
    sizeLabel: byteSizeLabel(controlStat?.size ?? 0),
    modifiedMs: controlStat?.mtimeMs ?? 0,
  }]
  const claimedVisPaths = []
  const videoContexts = []
  const usedContextNames = new Set()
  let streamSize = 0
  let streamModifiedMs = 0
  for (const device of enabledDevices) {
    const streamPath = safeControlRecordingPath(manifest.directoryPath, device.recordingFile)
    if (!streamPath) continue
    claimedVisPaths.push(streamPath)
    const streamStat = safeStat(streamPath)
    if (!streamStat?.isFile()) continue
    streamSize += streamStat.size
    streamModifiedMs = Math.max(streamModifiedMs, streamStat.mtimeMs)
    const contextRoot = device === primary
      ? 'main'
      : normalizedVideoContextName(
        device.deviceType === 'PHONE_DEVICE'
          ? 'phone'
          : device.deviceType === 'ROBOT_HAND_DEVICE'
            ? 'hand'
            : device.deviceType === 'DRONE_DEVICE'
              ? 'drone'
              : device.deviceType === 'ROBOT_CAR_DEVICE'
                ? 'robot-car'
                : device.displayName || device.deviceId,
      ) || 'device'
    let contextName = contextRoot
    let contextSuffix = 2
    while (usedContextNames.has(contextName)) {
      contextName = `${contextRoot}-${contextSuffix}`
      contextSuffix += 1
    }
    usedContextNames.add(contextName)
    videoContexts.push({
      name: contextName,
      displayName: contextName,
      path: streamPath,
      fileStem: path.basename(streamPath).slice(0, -'.vis.pb'.length),
      relativePath: path.relative(projectPath, streamPath),
      isMain: device === primary,
    })
    if (device.deviceType !== 'ROBOT_CAR_DEVICE') {
      analyses.push({
        key: `video:${device.deviceId}`,
        title: 'Video',
        kind: 'video',
        source: 'vis',
        baseKey: 'video',
        videoContext: contextName,
        sourceVideoPath: streamPath,
        path: streamPath,
        relativePath: path.relative(projectPath, streamPath),
        sizeBytes: streamStat.size,
        sizeLabel: byteSizeLabel(streamStat.size),
        modifiedMs: streamStat.mtimeMs,
      })
    }
  }

  return {
    recording: {
      id: manifest.projectId || `${path.basename(controlPath)}:${index}`,
      name: manifest.displayName || path.basename(manifest.directoryPath),
      displayName: projectDisplayName(manifest.directoryPath),
      fileStem: manifest.projectId || path.basename(manifest.directoryPath),
      path: primaryPath,
      directoryPath: manifest.directoryPath,
      projectRootPath: projectPath,
      relativePath: path.relative(projectPath, manifest.directoryPath),
      sizeBytes: streamSize,
      sizeLabel: byteSizeLabel(streamSize),
      modifiedMs: Math.max(controlStat?.mtimeMs ?? 0, streamModifiedMs),
      analyses,
      videoContexts,
      controlProject: manifest,
    },
    claimedVisPaths,
  }
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

function normalizedVideoContextName(value) {
  return String(value || '')
    .trim()
    .replace(/^[_\-.]+|[_\-.]+$/g, '')
    .replace(/[^A-Za-z0-9]+/g, '-')
    .replace(/^-+|-+$/g, '')
    .toLowerCase()
}

function groupVisFilesByContext(visFiles) {
  const byDirectory = new Map()
  for (const visPath of visFiles) {
    const dir = path.dirname(visPath)
    const stem = path.basename(visPath).slice(0, -'.vis.pb'.length)
    const items = byDirectory.get(dir) ?? []
    items.push({ path: visPath, stem })
    byDirectory.set(dir, items)
  }

  const groups = []
  for (const items of byDirectory.values()) {
    const stems = new Set(items.map((item) => item.stem))
    const grouped = new Map()
    for (const item of items) {
      const baseStem = [...stems]
        .filter((candidate) => (
          candidate !== item.stem
          && item.stem.startsWith(candidate)
          && /^[_\-.]/.test(item.stem.slice(candidate.length))
        ))
        .sort((left, right) => right.length - left.length)[0] ?? item.stem
      const members = grouped.get(baseStem) ?? []
      const rawSuffix = item.stem === baseStem ? '' : item.stem.slice(baseStem.length)
      members.push({
        ...item,
        contextName: rawSuffix ? normalizedVideoContextName(rawSuffix) : 'main',
      })
      grouped.set(baseStem, members)
    }
    for (const [baseStem, members] of grouped.entries()) {
      const main = members.find((member) => member.stem === baseStem)
      if (!main) {
        for (const member of members) {
          groups.push({ baseStem: member.stem, members: [{ ...member, contextName: 'main' }] })
        }
        continue
      }
      const usedNames = new Set(['main'])
      const normalizedMembers = members
        .sort((left, right) => (
          Number(right.contextName === 'main') - Number(left.contextName === 'main')
          || left.contextName.localeCompare(right.contextName)
        ))
        .map((member) => {
          if (member.contextName === 'main') return member
          const rootName = member.contextName || 'camera'
          let contextName = rootName
          let suffix = 2
          while (usedNames.has(contextName)) {
            contextName = `${rootName}-${suffix}`
            suffix += 1
          }
          usedNames.add(contextName)
          return { ...member, contextName }
        })
      groups.push({ baseStem, members: normalizedMembers })
    }
  }
  return groups.sort((left, right) => left.members[0].path.localeCompare(right.members[0].path))
}

function contextualAnalyses(projectPath, members) {
  const contextOrder = new Map(members.map((member, index) => [member.contextName, index]))
  const analysisOrder = [
    'video',
    'surface-planes',
    'sensors',
    ...ANALYSIS_DEFS.map((definition) => definition.key),
    'worldgen',
  ]
  const analyses = members.flatMap((member) => (
    analysesForVis(projectPath, member.path).map((analysis) => {
      const contextName = member.contextName
      return {
        ...analysis,
        key: contextName === 'main' ? analysis.key : `${analysis.key}:${contextName}`,
        title: analysis.title,
        baseKey: analysis.key,
        videoContext: contextName,
        sourceVideoPath: member.path,
      }
    })
  ))
  return analyses.sort((left, right) => {
    const leftOrder = analysisOrder.indexOf(left.baseKey)
    const rightOrder = analysisOrder.indexOf(right.baseKey)
    const analysisDifference = (
      (leftOrder < 0 ? analysisOrder.length : leftOrder)
      - (rightOrder < 0 ? analysisOrder.length : rightOrder)
    )
    if (analysisDifference) return analysisDifference
    return (contextOrder.get(left.videoContext) ?? 0) - (contextOrder.get(right.videoContext) ?? 0)
  })
}

function recordingForVisGroup(rootPath, group, index) {
  const main = group.members.find((member) => member.contextName === 'main') ?? group.members[0]
  const visStat = safeStat(main.path)
  const dir = path.dirname(main.path)
  const folderName = path.basename(dir)
  return {
    id: `${group.baseStem}:${index}`,
    name: folderName === group.baseStem ? group.baseStem : `${folderName}/${group.baseStem}`,
    displayName: projectDisplayName(dir),
    fileStem: group.baseStem,
    path: main.path,
    directoryPath: dir,
    projectRootPath: rootPath,
    relativePath: path.relative(rootPath, main.path),
    sizeBytes: visStat?.size ?? 0,
    sizeLabel: byteSizeLabel(visStat?.size ?? 0),
    modifiedMs: visStat?.mtimeMs ?? 0,
    videoContexts: group.members.map((member) => ({
      name: member.contextName,
      displayName: member.contextName,
      path: member.path,
      fileStem: member.stem,
      relativePath: path.relative(rootPath, member.path),
      isMain: member.contextName === 'main',
    })),
    analyses: contextualAnalyses(rootPath, group.members),
  }
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

  const controlResults = walkForControlFiles(rootPath)
    .map((controlPath, index) => recordingForControlProject(rootPath, controlPath, index))
  const claimedVisPaths = new Set(
    controlResults.flatMap((result) => result.claimedVisPaths ?? []).map((item) => path.resolve(item)),
  )
  const controlRecordings = controlResults
    .map((result) => result.recording)
    .filter(Boolean)
  const visFiles = walkForVisFiles(rootPath)
    .filter((visPath) => !claimedVisPaths.has(path.resolve(visPath)))
  const recordings = [
    ...controlRecordings,
    ...groupVisFilesByContext(visFiles).map(
      (group, index) => recordingForVisGroup(rootPath, group, index),
    ),
  ]
  const controlError = controlResults.find((result) => result.error)?.error

  return {
    rootPath,
    name: projectDisplayName(rootPath) ?? path.basename(rootPath),
    recordings,
    error: recordings.length
      ? controlError
      : controlError ?? 'This project does not contain any .vis.pb or .control.pb files.',
  }
}

const CONTROL_PRESETS = {
  robot_car: {
    displayName: 'Robot Car',
    projectType: 'ROBOT_CAR',
    primarySuffix: 'car',
    devices: [
      {
        deviceId: 'robocar-1',
        displayName: 'Robot Car',
        deviceType: 'ROBOT_CAR_DEVICE',
        role: 'PRIMARY_DEVICE',
        controlHost: '192.168.4.1',
        controlPort: 80,
        controlPath: '/api',
        controlTransport: 'HTTP_CONTROL',
        streamHost: '192.168.4.2',
        streamPort: 81,
        streamPath: '/stream',
        streamTransport: 'RGB565_HTTP_STREAM',
        capabilities: ['video', 'drive', 'ultrasonic'],
        enabled: true,
      },
    ],
  },
  robot_hand: {
    displayName: 'Robot Hand',
    projectType: 'ROBOT_HAND',
    primarySuffix: 'hand',
    devices: [
      {
        deviceId: 'robot-hand-1',
        displayName: 'Robot Hand',
        deviceType: 'ROBOT_HAND_DEVICE',
        role: 'PRIMARY_DEVICE',
        controlHost: 'robot-hand.local',
        controlPort: 80,
        controlPath: '/api',
        controlTransport: 'HTTP_CONTROL',
        streamHost: 'robot-hand.local',
        streamPort: 81,
        streamPath: '/stream',
        streamTransport: 'RGB565_HTTP_STREAM',
        capabilities: ['video', 'actuators'],
        enabled: true,
      },
    ],
  },
  drone_control: {
    displayName: 'Drone Control',
    projectType: 'DRONE_CONTROL',
    primarySuffix: 'drone',
    devices: [
      {
        deviceId: 'drone-1',
        displayName: 'Drone',
        deviceType: 'DRONE_DEVICE',
        role: 'PRIMARY_DEVICE',
        controlHost: '0.0.0.0',
        controlPort: 14550,
        controlPath: '/',
        controlTransport: 'MAVLINK_UDP_CONTROL',
        streamHost: '0.0.0.0',
        streamPort: 5600,
        streamPath: '/',
        streamTransport: 'RTP_VIDEO_STREAM',
        capabilities: ['video', 'flight'],
        enabled: true,
      },
    ],
  },
}

const CONTROL_DEVICE_PRESETS = {
  robot_car: {
    ...CONTROL_PRESETS.robot_car.devices[0],
    idBase: 'robocar',
    fileSuffix: 'car',
    projectType: 'ROBOT_CAR',
  },
  phone_camera: {
    deviceId: 'phone-1',
    displayName: 'Phone Camera',
    deviceType: 'PHONE_DEVICE',
    role: 'AUGMENTED_DEVICE',
    controlHost: '',
    controlPort: 0,
    controlPath: '',
    controlTransport: 'CONTROL_TRANSPORT_UNSPECIFIED',
    streamHost: '0.0.0.0',
    streamPort: 8080,
    streamPath: '/ar-stream',
    streamTransport: 'PERCEIVER_WEBSOCKET_STREAM',
    capabilities: ['video', 'imu', 'depth', 'gps'],
    enabled: true,
    idBase: 'phone',
    fileSuffix: 'phone',
    projectType: 'CONTROL_PROJECT_TYPE_UNSPECIFIED',
  },
  robot_hand: {
    ...CONTROL_PRESETS.robot_hand.devices[0],
    idBase: 'robot-hand',
    fileSuffix: 'hand',
    projectType: 'ROBOT_HAND',
  },
  drone: {
    ...CONTROL_PRESETS.drone_control.devices[0],
    idBase: 'drone',
    fileSuffix: 'drone',
    projectType: 'DRONE_CONTROL',
  },
}

function localTimestamp(date = new Date()) {
  const pad = (value) => String(value).padStart(2, '0')
  return `${date.getFullYear()}${pad(date.getMonth() + 1)}${pad(date.getDate())}_${pad(date.getHours())}${pad(date.getMinutes())}${pad(date.getSeconds())}`
}

function createProject(recordingsRootOverride) {
  const recordingsRoot = recordingsRootOverride
    ? path.resolve(recordingsRootOverride)
    : path.join(findRepoRoot(), 'recordings')
  fs.mkdirSync(recordingsRoot, { recursive: true })
  const timestamp = localTimestamp()
  let projectId = `${timestamp}_project`
  let projectPath = path.join(recordingsRoot, projectId)
  let suffix = 2
  while (fs.existsSync(projectPath)) {
    projectId = `${timestamp}_project_${suffix}`
    projectPath = path.join(recordingsRoot, projectId)
    suffix += 1
  }
  fs.mkdirSync(projectPath, { recursive: false })
  fs.closeSync(fs.openSync(path.join(projectPath, `${projectId}.vis.pb`), 'wx'))
  return scanProject(projectPath)
}

function createControlProject(presetName, recordingsRootOverride) {
  const preset = CONTROL_PRESETS[presetName]
  if (!preset) throw new Error(`Unknown control preset: ${presetName}`)

  const recordingsRoot = recordingsRootOverride
    ? path.resolve(recordingsRootOverride)
    : path.join(findRepoRoot(), 'recordings')
  fs.mkdirSync(recordingsRoot, { recursive: true })
  const timestamp = localTimestamp()
  let projectId = `${timestamp}_${presetName}`
  let projectPath = path.join(recordingsRoot, projectId)
  let suffix = 2
  while (fs.existsSync(projectPath)) {
    projectId = `${timestamp}_${presetName}_${suffix}`
    projectPath = path.join(recordingsRoot, projectId)
    suffix += 1
  }
  fs.mkdirSync(projectPath, { recursive: false })

  const primaryFile = `${projectId}.${preset.primarySuffix}.vis.pb`
  const phoneFile = `${projectId}.phone.vis.pb`
  const devices = [
    {
      ...preset.devices[0],
      recordingFile: primaryFile,
    },
    {
      ...CONTROL_DEVICE_PRESETS.phone_camera,
      deviceId: 'phone',
      displayName: 'Augmented Phone',
      recordingFile: phoneFile,
    },
  ]
  const payload = {
    projectId,
    displayName: `${preset.displayName} · ${timestamp}`,
    projectType: preset.projectType,
    createdTimestampMs: Date.now(),
    devices,
  }
  const type = getControlProjectType()
  const message = type.fromObject(payload)
  const error = type.verify(message)
  if (error) throw new Error(`Invalid control project preset: ${error}`)
  const manifestPath = path.join(projectPath, `${projectId}.control.pb`)
  fs.writeFileSync(manifestPath, type.encode(message).finish())
  fs.closeSync(fs.openSync(path.join(projectPath, primaryFile), 'wx'))
  return scanProject(projectPath)
}

function nextDeviceIdentity(devices, preset) {
  const usedIds = new Set(devices.map((device) => String(device.deviceId)))
  let index = 1
  while (usedIds.has(`${preset.idBase}-${index}`)) index += 1
  return {
    deviceId: `${preset.idBase}-${index}`,
    fileSuffix: index === 1 ? preset.fileSuffix : `${preset.fileSuffix}-${index}`,
  }
}

function controlManifestForRecording(directoryPath, recordingName) {
  const candidates = fs.readdirSync(directoryPath, { withFileTypes: true })
    .filter((entry) => entry.isFile() && entry.name.endsWith('.control.pb'))
    .map((entry) => path.join(directoryPath, entry.name))
  const manifests = candidates.map((manifestPath) => readControlProject(manifestPath))
  const matched = manifests.find((manifest) => (
    manifest.devices.some((device) => device.recordingFile === recordingName)
  ))
  if (matched) return matched
  if (manifests.length === 1) return manifests[0]
  if (manifests.length > 1) {
    throw new Error('This recording directory contains multiple control manifests.')
  }
  return null
}

function writeControlProject(manifestPath, payload) {
  const type = getControlProjectType()
  const message = type.fromObject(payload)
  const error = type.verify(message)
  if (error) throw new Error(`Invalid control project: ${error}`)
  const temporaryPath = `${manifestPath}.${process.pid}.${Date.now()}.tmp`
  fs.writeFileSync(temporaryPath, type.encode(message).finish())
  fs.renameSync(temporaryPath, manifestPath)
}

function addDeviceToProject(recordingPath, presetName) {
  const preset = CONTROL_DEVICE_PRESETS[presetName]
  if (!preset) throw new Error(`Unknown device preset: ${presetName}`)
  if (!recordingPath || typeof recordingPath !== 'string' || !recordingPath.endsWith('.vis.pb')) {
    throw new Error('Select a .vis.pb project recording before adding a device.')
  }
  const resolvedRecordingPath = path.resolve(recordingPath)
  const recordingStat = safeStat(resolvedRecordingPath)
  if (!recordingStat?.isFile()) throw new Error(`Recording not found: ${resolvedRecordingPath}`)

  const directoryPath = path.dirname(resolvedRecordingPath)
  const recordingName = path.basename(resolvedRecordingPath)
  const existing = controlManifestForRecording(directoryPath, recordingName)
  const baseProjectId = path.basename(recordingName, '.vis.pb')
  const project = existing
    ? {
        projectId: existing.projectId,
        displayName: existing.displayName,
        projectType: existing.projectType,
        createdTimestampMs: existing.createdTimestampMs,
        devices: existing.devices.map((device) => ({ ...device })),
      }
    : {
        projectId: baseProjectId,
        displayName: path.basename(directoryPath),
        projectType: 'CONTROL_PROJECT_TYPE_UNSPECIFIED',
        createdTimestampMs: Date.now(),
        devices: [],
      }
  const identity = nextDeviceIdentity(project.devices, preset)
  const firstDevice = project.devices.length === 0
  const recordingFile = firstDevice
    ? recordingName
    : `${project.projectId}.${identity.fileSuffix}.vis.pb`
  project.devices.push({
    ...preset,
    deviceId: identity.deviceId,
    role: firstDevice ? 'PRIMARY_DEVICE' : 'AUGMENTED_DEVICE',
    recordingFile,
  })
  if (
    project.projectType === 'CONTROL_PROJECT_TYPE_UNSPECIFIED'
    && preset.projectType !== 'CONTROL_PROJECT_TYPE_UNSPECIFIED'
  ) {
    project.projectType = preset.projectType
  }

  const manifestPath = existing?.manifestPath
    ?? path.join(directoryPath, `${project.projectId}.control.pb`)
  writeControlProject(manifestPath, project)
  const streamPath = path.join(directoryPath, recordingFile)
  if (!safeStat(streamPath)) fs.closeSync(fs.openSync(streamPath, 'wx'))
  return scanProject(directoryPath)
}

function renameProject(recordingPath, requestedName) {
  if (!recordingPath || typeof recordingPath !== 'string' || !recordingPath.endsWith('.vis.pb')) {
    throw new Error('Select a .vis.pb project recording before renaming the project.')
  }
  const resolvedRecordingPath = path.resolve(recordingPath)
  if (!safeStat(resolvedRecordingPath)?.isFile()) {
    throw new Error(`Recording not found: ${resolvedRecordingPath}`)
  }
  const displayName = String(requestedName || '').trim().replace(/\s+/g, ' ')
  if (!displayName) throw new Error('Project name cannot be empty.')
  if (displayName.length > 120) throw new Error('Project name must be 120 characters or fewer.')

  const directoryPath = path.dirname(resolvedRecordingPath)
  const recordingName = path.basename(resolvedRecordingPath)
  const controlProject = controlManifestForRecording(directoryPath, recordingName)
  if (controlProject) {
    writeControlProject(controlProject.manifestPath, {
      projectId: controlProject.projectId,
      displayName,
      projectType: controlProject.projectType,
      createdTimestampMs: controlProject.createdTimestampMs,
      devices: controlProject.devices,
    })
  }
  writeJsonAtomic(path.join(directoryPath, '.project.json'), {
    version: 1,
    displayName,
  })
  savePersistentProjectState(directoryPath, { displayName })
  return scanProject(directoryPath)
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

  const recordings = groupVisFilesByContext(visFiles).map(
    (group, index) => recordingForVisGroup(rootPath, group, index),
  )

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

function buildEvenSampleIndexes(count, sampleCount) {
  const boundedCount = Math.max(0, Math.trunc(finiteNumber(count)))
  const boundedSamples = Math.min(
    boundedCount,
    Math.max(0, Math.trunc(finiteNumber(sampleCount))),
  )
  if (boundedSamples <= 0) return []
  if (boundedSamples >= boundedCount) {
    return Array.from({ length: boundedCount }, (_value, index) => index)
  }
  if (boundedSamples === 1) return [0]

  const indexes = new Set()
  for (let index = 0; index < boundedSamples; index += 1) {
    indexes.add(Math.round((index * (boundedCount - 1)) / (boundedSamples - 1)))
  }
  return [...indexes].sort((left, right) => left - right)
}

function buildGemmaSampleIndexes(
  frameCount,
  durationSeconds,
  targetFps = DEFAULT_GEMMA_SAMPLE_FPS,
  maxFrames = MAX_GEMMA_UPLOAD_FRAMES,
  fallbackSourceFps = 30,
) {
  const count = Math.max(0, Math.trunc(finiteNumber(frameCount)))
  const hardLimit = Math.min(
    MAX_GEMMA_UPLOAD_FRAMES,
    Math.max(0, Math.trunc(finiteNumber(maxFrames))),
  )
  if (!count || !hardLimit) return []

  const sampleFps = Math.max(0.1, finiteNumber(targetFps, DEFAULT_GEMMA_SAMPLE_FPS))
  const duration = finiteNumber(durationSeconds)
  const desiredSamples = duration > 0
    ? Math.floor(duration * sampleFps) + 1
    : Math.ceil(
        (count / Math.max(0.1, finiteNumber(fallbackSourceFps, 30))) * sampleFps,
      )
  return buildEvenSampleIndexes(
    count,
    Math.min(count, hardLimit, Math.max(1, desiredSamples)),
  )
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
  const ultrasonic = frame.ultrasonicSensorData
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

const domainReconstructionCache = new Map()

function numericArray(value) {
  return Array.isArray(value) || ArrayBuffer.isView(value)
    ? Array.from(value, (item) => finiteNumber(item))
    : []
}

function pongtownPoint(
  value,
  fallbackLabel = '',
  fallbackFrameIndex = 0,
  fallbackFrameNumber = 0,
) {
  const coordinates = numericArray(value?.tableXyzMm)
  if (!value?.hasTablePosition || coordinates.length < 2) return null
  return {
    xMm: coordinates[0],
    yMm: coordinates[1],
    zMm: coordinates[2] ?? 0,
    label: normalizedLabel(value.label) || fallbackLabel,
    confidence: finiteNumber(value.confidence),
    frameIndex: finiteNumber(value.frameIdx, fallbackFrameIndex),
    frameNumber: finiteNumber(value.frameNumber, fallbackFrameNumber),
    insideTable: Boolean(value.insideTable),
  }
}

function pongtownFrameIndex(record, fallbackIndex) {
  const debug = Array.isArray(record.pnpFrameDebug) ? record.pnpFrameDebug[0] : null
  return finiteNumber(
    record.frameOutput?.frameIdx,
    finiteNumber(debug?.frameIdx, fallbackIndex),
  )
}

function pongtownTriangulation(record) {
  const frameOutput = record.frameOutput
  const debug = Array.isArray(record.pnpFrameDebug) ? record.pnpFrameDebug[0] : null
  const tablePose = record.tablePose
  const tableQuad = numericArray(
    frameOutput?.tableQuadImg?.length >= 8
      ? frameOutput.tableQuadImg
      : debug?.pnpTableQuadImg?.length >= 8
        ? debug.pnpTableQuadImg
        : tablePose?.quadImgGlobal?.length >= 8
          ? tablePose.quadImgGlobal
          : tablePose?.quadImg,
  ).slice(0, 8)
  const netQuad = numericArray(
    frameOutput?.netQuadImg?.length >= 8
      ? frameOutput.netQuadImg
      : debug?.pnpOverlayNetQuadImg?.length >= 8
        ? debug.pnpOverlayNetQuadImg
        : debug?.pnpNetQuadImg?.length >= 8
          ? debug.pnpNetQuadImg
          : debug?.imagePlaneNetQuadImg,
  ).slice(0, 8)
  if (tableQuad.length < 8 && netQuad.length < 8) return null
  return {
    frameNumber: finiteNumber(record.frameIdentifier?.frameNumber),
    tableQuad,
    netQuad,
    method: String(tablePose?.method || debug?.imagePlaneMethod || 'UNKNOWN'),
    quality: Math.max(
      finiteNumber(frameOutput?.globalIou),
      finiteNumber(debug?.pnpTableIou),
      finiteNumber(tablePose?.quadQuality),
    ),
  }
}

function inverseHomography(values) {
  const matrix = numericArray(values)
  if (matrix.length < 9) return null
  const [a, b, c, d, e, f, g, h, i] = matrix
  const A = e * i - f * h
  const B = c * h - b * i
  const C = b * f - c * e
  const D = f * g - d * i
  const E = a * i - c * g
  const F = c * d - a * f
  const G = d * h - e * g
  const H = b * g - a * h
  const I = a * e - b * d
  const determinant = a * A + b * D + c * G
  if (!Number.isFinite(determinant) || Math.abs(determinant) < 1e-9) return null
  return [A, B, C, D, E, F, G, H, I].map((value) => value / determinant)
}

function projectHomography(matrix, x, y) {
  const scale = matrix[6] * x + matrix[7] * y + matrix[8]
  if (!Number.isFinite(scale) || Math.abs(scale) < 1e-9) return null
  return [
    (matrix[0] * x + matrix[1] * y + matrix[2]) / scale,
    (matrix[3] * x + matrix[4] * y + matrix[5]) / scale,
  ]
}

function snookestownTriangulation(record, tableWidthMm, tableHeightMm) {
  const inverse = inverseHomography(record.tablePose?.homographyImgToTableMm)
  if (!inverse || tableWidthMm <= 0 || tableHeightMm <= 0) return null
  const halfWidth = tableWidthMm / 2
  const halfHeight = tableHeightMm / 2
  const corners = [
    [-halfWidth, -halfHeight],
    [halfWidth, -halfHeight],
    [halfWidth, halfHeight],
    [-halfWidth, halfHeight],
  ]
  const projected = corners.map(([x, y]) => projectHomography(inverse, x, y))
  if (projected.some((point) => !point)) return null
  return {
    frameNumber: finiteNumber(record.frameIdentifier?.frameNumber),
    tableQuad: projected.flat(),
    netQuad: [],
    method: String(record.tablePose?.method || 'UNKNOWN'),
    quality: finiteNumber(record.tablePose?.quality),
  }
}

function buildPongtownDomain(messages, sourcePath) {
  const records = messages.map((message) => getPongtownType().toObject(message, {
    arrays: true,
    defaults: false,
    enums: String,
    longs: String,
  }))
  const summary = [...records].reverse().find((record) => record.globalTablePose?.hasPose)
    ?? records.at(-1)
    ?? {}
  const frames = records.filter((record) => record.frameIdentifier)
  const trajectory = summary.pingpongTracking?.ballTrajectory ?? summary.ballTrajectory ?? {}
  const hasNet = Boolean(summary.globalTablePose?.hasNetPose)
    || finiteNumber(summary.netHeightMm) > 0
  const sportMode = summary.sportMode === 'PINGPONG' || hasNet ? 'PINGPONG' : 'SNOOKER'
  const tableWidthMm = finiteNumber(summary.tableWidthMm)
    || (sportMode === 'PINGPONG' ? 2740 : 3569)
  const tableHeightMm = finiteNumber(summary.tableHeightMm)
    || (sportMode === 'PINGPONG' ? 1525 : 1778)
  const firstFrameNumber = finiteNumber(frames[0]?.frameIdentifier?.frameNumber)
  const inferredTrajectoryFrameIndex = (value) => (
    value?.frameIdx == null
      ? Math.max(0, finiteNumber(value?.frameNumber) - firstFrameNumber)
      : finiteNumber(value.frameIdx)
  )

  const trajectoryPoints = sportMode === 'PINGPONG'
    ? (trajectory.positions || [])
      .map((position) => pongtownPoint(
        position,
        'Ball',
        inferredTrajectoryFrameIndex(position),
      ))
      .filter(Boolean)
    : []
  const bounces = sportMode === 'PINGPONG'
    ? (trajectory.bounces || [])
      .map((bounce, index) => pongtownPoint(
        bounce,
        `Bounce ${index + 1}`,
        inferredTrajectoryFrameIndex(bounce),
      ))
      .filter(Boolean)
    : []
  const trajectoryByFrameIndex = new Map()
  const trajectoryByFrameNumber = new Map()
  for (const point of trajectoryPoints) {
    const byIndex = trajectoryByFrameIndex.get(point.frameIndex) ?? []
    byIndex.push(point)
    trajectoryByFrameIndex.set(point.frameIndex, byIndex)
    const byNumber = trajectoryByFrameNumber.get(point.frameNumber) ?? []
    byNumber.push(point)
    trajectoryByFrameNumber.set(point.frameNumber, byNumber)
  }
  const domainFrames = frames.map((record, fallbackIndex) => {
    const frameIndex = pongtownFrameIndex(record, fallbackIndex)
    const frameNumber = finiteNumber(record.frameIdentifier?.frameNumber, frameIndex)
    const observations = sportMode === 'SNOOKER'
      ? record.snookerTracking?.ballPositions || []
      : record.pingpongTracking?.ballPositions || record.ballPositions || []
    let frameBalls = observations
      .map((ball) => pongtownPoint(ball, 'Ball', frameIndex, frameNumber))
      .filter(Boolean)
    if (sportMode === 'PINGPONG' && frameBalls.length === 0) {
      frameBalls = trajectoryByFrameIndex.get(frameIndex)
        ?? trajectoryByFrameNumber.get(frameNumber)
        ?? []
    }
    return { frameIndex, frameNumber, balls: frameBalls }
  }).sort((left, right) => left.frameIndex - right.frameIndex)
  const finalWindow = frames.slice(-Math.max(1, Math.ceil(frames.length * 0.15)))
  const finalBallFrame = finalWindow.reduce((best, record) => {
    const count = (record.snookerTracking?.ballPositions || [])
      .filter((ball) => ball.hasTablePosition).length
    const bestCount = (best?.snookerTracking?.ballPositions || [])
      .filter((ball) => ball.hasTablePosition).length
    return count >= bestCount ? record : best
  }, null)
  const balls = sportMode === 'SNOOKER'
    ? (finalBallFrame?.snookerTracking?.ballPositions || [])
      .map((ball) => pongtownPoint(
        ball,
        'Ball',
        pongtownFrameIndex(finalBallFrame, Math.max(0, frames.length - 1)),
        finiteNumber(finalBallFrame?.frameIdentifier?.frameNumber),
      ))
      .filter(Boolean)
    : []
  const byFrame = new Map()
  for (const record of frames) {
    const overlay = pongtownTriangulation(record)
    if (overlay) byFrame.set(overlay.frameNumber, overlay)
  }
  const snapshotFrameNumber = finiteNumber(finalBallFrame?.frameIdentifier?.frameNumber)
    || finiteNumber(frames.at(-1)?.frameIdentifier?.frameNumber)

  return {
    reconstruction: {
      sourcePath,
      sourceKind: 'pongtown',
      sportMode,
      tableWidthMm,
      tableHeightMm,
      netHeightMm: finiteNumber(summary.netHeightMm),
      netOverhangMm: finiteNumber(summary.netOverhangMm),
      hasNet,
      frameCount: frames.length,
      snapshotFrameNumber,
      poseQuality: finiteNumber(summary.globalTablePose?.meanIou),
      balls,
      bounces,
      trajectory: trajectoryPoints,
      pockets: [],
      frames: domainFrames,
    },
    byFrame,
    sortedFrames: [...byFrame.keys()].sort((left, right) => left - right),
  }
}

function buildSnookestownDomain(messages, sourcePath) {
  const records = messages.map((message) => getSnookestownType().toObject(message, {
    arrays: true,
    defaults: false,
    enums: String,
    longs: String,
  }))
  const summary = [...records].reverse().find((record) => (
    record.tracks?.length || finiteNumber(record.totalFrames) > 0
  )) ?? records.at(-1) ?? {}
  const frames = records.filter((record) => record.frameIdentifier)
  const tableWidthMm = finiteNumber(summary.tableWidthMm) || 3569
  const tableHeightMm = finiteNumber(summary.tableHeightMm) || 1778
  const trackColors = new Map(
    (summary.tracks || []).map((track) => [finiteNumber(track.trackId), String(track.color || '')]),
  )
  const latestByTrack = new Map()
  const domainFrames = frames.map((record, frameIndex) => {
    const frameNumber = finiteNumber(record.frameIdentifier?.frameNumber)
    const balls = (record.balls || []).map((ball) => {
      const point = {
        xMm: finiteNumber(ball.xMm),
        yMm: finiteNumber(ball.yMm),
        zMm: 0,
        label: trackColors.get(finiteNumber(ball.trackId)) || `Ball ${finiteNumber(ball.trackId)}`,
        confidence: finiteNumber(ball.confidence),
        frameIndex,
        frameNumber,
        insideTable: true,
      }
      latestByTrack.set(finiteNumber(ball.trackId), point)
      return point
    })
    return {
      frameIndex,
      frameNumber,
      balls,
    }
  })
  const byFrame = new Map()
  for (const record of frames) {
    const overlay = snookestownTriangulation(record, tableWidthMm, tableHeightMm)
    if (overlay) byFrame.set(overlay.frameNumber, overlay)
  }

  return {
    reconstruction: {
      sourcePath,
      sourceKind: 'snookestown',
      sportMode: 'SNOOKER',
      tableWidthMm,
      tableHeightMm,
      netHeightMm: 0,
      netOverhangMm: 0,
      hasNet: false,
      frameCount: finiteNumber(summary.totalFrames) || frames.length,
      snapshotFrameNumber: Math.max(
        0,
        ...[...latestByTrack.values()].map((ball) => ball.frameNumber),
      ),
      poseQuality: 0,
      balls: [...latestByTrack.values()],
      bounces: [],
      trajectory: [],
      pockets: (summary.canonicalPockets || []).map((pocket) => ({
        xMm: finiteNumber(pocket.xMm),
        yMm: finiteNumber(pocket.yMm),
        kind: String(pocket.kind || 'UNKNOWN'),
      })),
      frames: domainFrames,
    },
    byFrame,
    sortedFrames: [...byFrame.keys()].sort((left, right) => left - right),
  }
}

function getDomainReconstructionIndex(filePath) {
  if (
    !filePath
    || (!filePath.endsWith('.pongtown.pb') && !filePath.endsWith('.snook.pb'))
  ) {
    throw new Error('Expected a .pongtown.pb or .snook.pb reconstruction file.')
  }
  const resolved = path.resolve(filePath)
  const stat = fs.statSync(resolved)
  const key = `${stat.size}:${stat.mtimeMs}`
  const cached = domainReconstructionCache.get(resolved)
  if (cached?.key === key) return cached.index

  const pongtown = resolved.endsWith('.pongtown.pb')
  const type = pongtown ? getPongtownType() : getSnookestownType()
  const messages = readLengthDelimitedProtos(resolved, type)
  const index = pongtown
    ? buildPongtownDomain(messages, resolved)
    : buildSnookestownDomain(messages, resolved)
  domainReconstructionCache.set(resolved, { key, index })
  return index
}

function readDomainReconstruction(filePath) {
  return getDomainReconstructionIndex(filePath).reconstruction
}

function readDomainTriangulation(filePath, frameNumber) {
  const index = getDomainReconstructionIndex(filePath)
  const targetFrame = nearestSegFrame(
    index.sortedFrames,
    Math.trunc(Number(frameNumber) || 0),
  )
  return targetFrame == null ? null : index.byFrame.get(targetFrame) ?? null
}

function analysisFromInitialTurn(turn) {
  const raw = String(turn?.text || '').trim()
  if (!raw) return null
  const titleMatch = /^##\s+([^\n]+)\n*/.exec(raw)
  return {
    title: titleMatch?.[1]?.trim() || 'AI Analysis',
    text: titleMatch ? raw.slice(titleMatch[0].length).trim() : raw,
    parameters: [],
    turns: [],
  }
}

function parseToolArguments(argumentsJson) {
  const raw = String(argumentsJson || '').trim()
  if (!raw) return {}
  try {
    const parsed = JSON.parse(raw)
    return parsed && typeof parsed === 'object' && !Array.isArray(parsed)
      ? parsed
      : { value: parsed }
  } catch {
    return { raw }
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
    const response = types.gensparkResponseType.toObject(decoded, {
      defaults: true,
      longs: String,
      arrays: true,
    })
    const summary = response.summary
    const turns = (response.turns || []).map((turn) => ({
      text: String(turn.text || ''),
      toolCalls: (turn.toolCalls || []).map((call) => ({
        name: String(call.toolName || ''),
        arguments: parseToolArguments(call.argumentsJson),
        result: String(call.result || ''),
      })),
    }))
    if (
      (summary && (summary.title || summary.text || summary.parameters?.length))
      || turns.length
    ) {
      analysis = {
        title: String(summary?.title || 'Genspark analysis'),
        text: String(summary?.text || ''),
        parameters: (summary?.parameters || []).map((parameter) => ({
          name: String(parameter.name || ''),
          value: String(parameter.value || ''),
          unit: String(parameter.unit || ''),
        })),
        turns,
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
const DESKTOP_WORKSPACE_VERSION = 2

function safeWorkspaceId(value, fallback) {
  const cleaned = String(value || '')
    .trim()
    .replace(/[^A-Za-z0-9._-]+/g, '-')
    .replace(/^[.-]+|[.-]+$/g, '')
    .slice(0, 160)
  return cleaned || fallback
}

function bayesmechStateDirectory() {
  return path.resolve(
    process.env.BAYESMECH_STATE_HOME || path.join(os.homedir(), '.bayesmech'),
  )
}

function stateKeyForPath(filePath, fallback = 'project') {
  const resolved = path.resolve(filePath)
  const name = safeWorkspaceId(path.basename(resolved), fallback).slice(0, 72)
  const digest = crypto.createHash('sha256').update(resolved).digest('hex').slice(0, 16)
  return `${name}-${digest}`
}

function persistentProjectDirectory(projectPath) {
  return path.join(
    bayesmechStateDirectory(),
    'projects',
    stateKeyForPath(projectPath),
  )
}

function projectStateFile(projectPath) {
  return path.join(persistentProjectDirectory(projectPath), 'project.json')
}

function normalizeProjectPaths(values) {
  if (!Array.isArray(values)) return []
  return [...new Set(values
    .filter((value) => typeof value === 'string' && value.trim())
    .filter((value) => !value.startsWith('browser://') && !value.startsWith('workspace://'))
    .map((value) => path.resolve(value)))]
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

function loadDesktopWorkspaceState() {
  const stored = readJsonFile(path.join(bayesmechStateDirectory(), 'workspace.json'), {})
  const loadedProjectPaths = normalizeProjectPaths(
    stored.loadedProjectPaths ?? stored.projectPaths,
  )
  const requestedActivePath = typeof stored.activeProjectPath === 'string' && stored.activeProjectPath
    ? path.resolve(stored.activeProjectPath)
    : ''
  return {
    version: DESKTOP_WORKSPACE_VERSION,
    loadedProjectPaths,
    activeProjectPath: loadedProjectPaths.includes(requestedActivePath)
      ? requestedActivePath
      : loadedProjectPaths[0] ?? '',
    updatedAt: String(stored.updatedAt || ''),
  }
}

function saveDesktopWorkspaceState(projectPaths, activeProjectPath = '') {
  const loadedProjectPaths = normalizeProjectPaths(projectPaths)
  const resolvedActivePath = typeof activeProjectPath === 'string' && activeProjectPath
    ? path.resolve(activeProjectPath)
    : ''
  const state = {
    version: DESKTOP_WORKSPACE_VERSION,
    loadedProjectPaths,
    activeProjectPath: loadedProjectPaths.includes(resolvedActivePath)
      ? resolvedActivePath
      : loadedProjectPaths[0] ?? '',
    updatedAt: new Date().toISOString(),
  }
  writeJsonAtomic(path.join(bayesmechStateDirectory(), 'workspace.json'), state)
  return state
}

function normalizePersistentRecording(recording) {
  const recordingPath = typeof recording?.recordingPath === 'string' && recording.recordingPath
    ? path.resolve(recording.recordingPath)
    : ''
  if (!recordingPath) return null
  return {
    videoId: String(recording.videoId || path.basename(recordingPath, '.vis.pb')),
    recordingPath,
    activeChatId: safeWorkspaceId(recording.activeChatId, ''),
    updatedAt: String(recording.updatedAt || ''),
  }
}

function loadPersistentProjectState(projectPath) {
  const rootPath = path.resolve(projectPath)
  const stored = readJsonFile(projectStateFile(rootPath), {})
  const recordings = Array.isArray(stored.recordings)
    ? stored.recordings.map(normalizePersistentRecording).filter(Boolean)
    : []
  const requestedRecordingPath = typeof stored.activeRecordingPath === 'string' && stored.activeRecordingPath
    ? path.resolve(stored.activeRecordingPath)
    : ''
  return {
    version: DESKTOP_WORKSPACE_VERSION,
    projectId: stateKeyForPath(rootPath),
    rootPath,
    displayName: String(stored.displayName || ''),
    activeRecordingPath: requestedRecordingPath,
    activeChatId: safeWorkspaceId(stored.activeChatId, ''),
    recordings,
    updatedAt: String(stored.updatedAt || ''),
  }
}

function savePersistentProjectState(projectPath, patch = {}) {
  const rootPath = path.resolve(projectPath)
  const current = loadPersistentProjectState(rootPath)
  const activeRecordingPath = typeof patch.activeRecordingPath === 'string'
    ? patch.activeRecordingPath ? path.resolve(patch.activeRecordingPath) : ''
    : current.activeRecordingPath
  const activeChatId = patch.activeChatId === undefined
    ? current.activeChatId
    : safeWorkspaceId(patch.activeChatId, '')
  const recordings = Array.isArray(patch.recordings)
    ? patch.recordings.map(normalizePersistentRecording).filter(Boolean)
    : current.recordings
  const state = {
    ...current,
    displayName: patch.displayName === undefined
      ? current.displayName
      : String(patch.displayName || '').trim(),
    activeRecordingPath,
    activeChatId,
    recordings,
    updatedAt: new Date().toISOString(),
  }
  writeJsonAtomic(projectStateFile(rootPath), state)
  return state
}

function registerPersistentRecording(videoId, recordingPath, activeChatId) {
  const resolvedRecordingPath = path.resolve(recordingPath)
  const rootPath = path.dirname(resolvedRecordingPath)
  const project = loadPersistentProjectState(rootPath)
  const entry = {
    videoId: String(videoId || path.basename(resolvedRecordingPath, '.vis.pb')),
    recordingPath: resolvedRecordingPath,
    activeChatId: safeWorkspaceId(activeChatId, ''),
    updatedAt: new Date().toISOString(),
  }
  const recordings = project.recordings.some(
    (recording) => recording.recordingPath === resolvedRecordingPath,
  )
    ? project.recordings.map((recording) => (
        recording.recordingPath === resolvedRecordingPath ? entry : recording
      ))
    : [...project.recordings, entry]
  savePersistentProjectState(rootPath, { recordings })
}

function legacyWorkspaceVideoDirectory(videoId) {
  return path.join(bayesmechStateDirectory(), safeWorkspaceId(videoId, 'video'))
}

function migrateLegacyChatWorkspace(videoId, recordingPath, videoDirectory) {
  if (fs.existsSync(videoDirectory)) return
  const legacyIds = [
    videoId,
    path.basename(recordingPath, '.vis.pb'),
  ]
  const legacyDirectories = [...new Set(legacyIds)].map(legacyWorkspaceVideoDirectory)
  try {
    for (const entry of fs.readdirSync(bayesmechStateDirectory(), { withFileTypes: true })) {
      if (!entry.isDirectory() || entry.name === 'projects') continue
      legacyDirectories.push(path.join(bayesmechStateDirectory(), entry.name))
    }
  } catch {
    // A new state directory has no legacy chat folders to inspect.
  }
  for (const legacyDirectory of [...new Set(legacyDirectories)]) {
    const legacyManifest = readJsonFile(path.join(legacyDirectory, 'video.json'), null)
    if (!safeStat(legacyDirectory)?.isDirectory() || !legacyManifest) continue
    if (
      legacyManifest.recordingPath
      && path.resolve(legacyManifest.recordingPath) !== path.resolve(recordingPath)
    ) continue
    fs.mkdirSync(path.dirname(videoDirectory), { recursive: true })
    fs.cpSync(legacyDirectory, videoDirectory, { recursive: true })
    return
  }
}

function workspaceVideoDirectory(videoId, recordingPath) {
  if (!recordingPath || typeof recordingPath !== 'string') {
    throw new Error('A recording path is required for project-scoped chat state.')
  }
  const resolvedRecordingPath = path.resolve(recordingPath)
  const projectDirectory = persistentProjectDirectory(path.dirname(resolvedRecordingPath))
  const videoDirectory = path.join(
    projectDirectory,
    'recordings',
    stateKeyForPath(resolvedRecordingPath, safeWorkspaceId(videoId, 'video')),
  )
  migrateLegacyChatWorkspace(videoId, resolvedRecordingPath, videoDirectory)
  return videoDirectory
}

function defaultChatTitle(createdAt) {
  return new Date(createdAt).toLocaleString(undefined, {
    dateStyle: 'medium',
    timeStyle: 'short',
  })
}

function normalizeWorkspaceToolCall(call) {
  const argumentsValue = call?.arguments
  return {
    name: String(call?.name || 'Tool call'),
    arguments: argumentsValue && typeof argumentsValue === 'object' && !Array.isArray(argumentsValue)
      ? argumentsValue
      : typeof argumentsValue === 'string'
        ? parseToolArguments(argumentsValue)
        : {},
    result: call?.result ?? null,
  }
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
    ...(Array.isArray(message?.toolCalls) && message.toolCalls.length
      ? { toolCalls: message.toolCalls.map(normalizeWorkspaceToolCall) }
      : {}),
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
    videoContext: normalizedVideoContextName(session?.videoContext) || 'main',
    ...(session?.source === 'legacy-chat-pb' ? { source: session.source } : {}),
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
    videoContext: normalized.videoContext,
    ...(normalized.source ? { source: normalized.source } : {}),
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

function legacyChatPath(recordingPath) {
  return `${path.resolve(recordingPath).slice(0, -'.vis.pb'.length)}.chat.pb`
}

function legacyChatTitle(messages) {
  const firstUserText = messages
    .find((message) => message.role === 'user')
    ?.text.replace(/\s+/g, ' ')
    .trim()
  if (!firstUserText) return 'Imported legacy chat'
  return firstUserText.length > 72
    ? `${firstUserText.slice(0, 69).trimEnd()}…`
    : firstUserText
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
  registerPersistentRecording(videoId, recordingPath, activeChatId)
}

function loadChatWorkspace(videoId, recordingPath) {
  if (!recordingPath || typeof recordingPath !== 'string' || !recordingPath.endsWith('.vis.pb')) {
    throw new Error('Expected a .vis.pb recording.')
  }

  const normalizedVideoId = String(videoId || path.basename(recordingPath, '.vis.pb'))
  const videoDirectory = workspaceVideoDirectory(normalizedVideoId, recordingPath)
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

  let importedLegacyChatId = ''
  const sourceChatPath = legacyChatPath(recordingPath)
  const importReceiptPath = path.join(videoDirectory, 'legacy-chat-import.json')
  if (fs.existsSync(sourceChatPath) && !fs.existsSync(importReceiptPath)) {
    const existingImport = chats.find((chat) => (
      chat.source === 'legacy-chat-pb'
      || chat.messages.some((message) => message.id.startsWith('legacy-'))
    ))
    if (existingImport) {
      importedLegacyChatId = existingImport.id
    } else {
      const messages = legacyChatMessages(recordingPath)
      if (messages.length) {
        const createdAt = messages[0].createdAt
        const imported = writeChatSession(videoDirectory, {
          id: `legacy-chat-${stateKeyForPath(sourceChatPath, 'chat').slice(-16)}`,
          title: legacyChatTitle(messages),
          createdAt,
          updatedAt: messages.at(-1)?.createdAt ?? createdAt,
          messages,
          markers: [],
          source: 'legacy-chat-pb',
        })
        chats.push(imported)
        importedLegacyChatId = imported.id
      }
    }
    if (importedLegacyChatId) {
      writeJsonAtomic(importReceiptPath, {
        version: 1,
        sourcePath: sourceChatPath,
        importedChatId: importedLegacyChatId,
        importedAt: new Date().toISOString(),
      })
    }
  }

  if (chats.length === 0 && !Array.isArray(manifest.chatOrder)) {
    const createdAt = new Date()
    chats.push(writeChatSession(videoDirectory, {
      id: chatIdForDate(createdAt),
      title: defaultChatTitle(createdAt.toISOString()),
      createdAt: createdAt.toISOString(),
      updatedAt: createdAt.toISOString(),
      messages: [],
      markers: [],
    }))
  }

  const activeChatId = importedLegacyChatId
    || (chats.some((chat) => chat.id === manifest.activeChatId)
      ? manifest.activeChatId
      : chats[0]?.id ?? '')
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
  const videoDirectory = workspaceVideoDirectory(videoId, recordingPath)
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
  savePersistentProjectState(path.dirname(path.resolve(recordingPath)), {
    activeRecordingPath: recordingPath,
    activeChatId: session.id,
  })
  return { ...workspace, activeChatId: session.id, chats }
}

function deleteChatSession(videoId, recordingPath, chatId) {
  const workspace = loadChatWorkspace(videoId, recordingPath)
  const normalizedChatId = safeWorkspaceId(chatId, '')
  if (!normalizedChatId || normalizedChatId !== chatId) {
    throw new Error('Invalid chat id.')
  }
  const deleteIndex = workspace.chats.findIndex((chat) => chat.id === normalizedChatId)
  if (deleteIndex < 0) throw new Error('Chat not found.')

  const videoDirectory = workspaceVideoDirectory(videoId, recordingPath)
  const chatDirectory = path.join(videoDirectory, normalizedChatId)
  fs.rmSync(chatDirectory, { recursive: true, force: false })
  const chats = workspace.chats.filter((chat) => chat.id !== normalizedChatId)
  const activeChatId = workspace.activeChatId === normalizedChatId
    ? chats[Math.min(deleteIndex, chats.length - 1)]?.id ?? ''
    : workspace.activeChatId
  writeWorkspaceManifest(videoDirectory, videoId, recordingPath, activeChatId, chats)
  savePersistentProjectState(path.dirname(path.resolve(recordingPath)), {
    activeRecordingPath: recordingPath,
    activeChatId,
  })
  return { ...workspace, activeChatId, chats }
}

function saveChatSession(videoId, recordingPath, session) {
  const workspace = loadChatWorkspace(videoId, recordingPath)
  const videoDirectory = workspaceVideoDirectory(videoId, recordingPath)
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
    workspaceVideoDirectory(videoId, recordingPath),
    videoId,
    recordingPath,
    chatId,
    workspace.chats,
  )
  savePersistentProjectState(path.dirname(path.resolve(recordingPath)), {
    activeRecordingPath: recordingPath,
    activeChatId: chatId,
  })
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
  const type = getVggtResponseType()
  const messages = readLengthDelimitedProtos(resolved, type)
  const polledJobs = new Set()
  let changed = false

  for (const message of messages) {
    const saved = savedSplatInfo(message?.gaussianSplat)
    if (!saved?.jobId || polledJobs.has(saved.jobId)) continue
    const missingCompletedPly = saved.status === 'complete' && (
      !saved.plyPath || !fs.existsSync(saved.plyPath)
    )
    if (['complete', 'failed', 'skipped'].includes(saved.status) && !missingCompletedPly) continue

    polledJobs.add(saved.jobId)
    try {
      worldgenSplatDestinations.set(
        saved.jobId,
        worldgenSplatPaths(resolved, saved.jobId),
      )
      const remote = await pollWorldgenSplat(saved.jobId)
      changed = applyWorldgenSplatToMessages(resolved, messages, remote) || changed
    } catch {
      // Keep every saved VGGT point cloud usable even when one remote splat
      // status endpoint is unavailable.
    }
  }

  if (changed) writeLengthDelimitedProtos(resolved, type, messages)
  return worldgenPreviewFromMessages(messages, resolved)
}

function applyWorldgenSplatToMessages(resolved, messages, splat) {
  if (!messages.length) return false
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
  const paths = worldgenSplatPaths(resolved, jobId)
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
    fs.mkdirSync(path.dirname(paths.previewPath), { recursive: true })
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
  return true
}

async function saveWorldgenSplat(filePath, splat) {
  if (!filePath || typeof filePath !== 'string' || !filePath.endsWith('.vggt.pb')) {
    throw new Error('Expected a .vggt.pb file.')
  }
  const resolved = path.resolve(filePath)
  const type = getVggtResponseType()
  const messages = readLengthDelimitedProtos(resolved, type)
  applyWorldgenSplatToMessages(resolved, messages, splat)
  writeLengthDelimitedProtos(resolved, type, messages)
  return worldgenPreviewFromMessages(messages, resolved)
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

function worldgenSplatPaths(vggtPath, jobId = '') {
  const dir = path.dirname(vggtPath)
  const name = path.basename(vggtPath)
  const stem = name.endsWith('.vggt.pb') ? name.slice(0, -'.vggt.pb'.length) : path.parse(name).name
  if (jobId) {
    const safeJobId = String(jobId).replace(/[^A-Za-z0-9._-]+/g, '_')
    const jobDir = path.join(dir, `${stem}.splats`, safeJobId)
    return {
      workspacePath: path.join(jobDir, 'workspace'),
      plyPath: path.join(jobDir, 'model.splat.ply'),
      previewPath: path.join(jobDir, 'preview.json'),
    }
  }
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

function normalizeRunnerBackgroundJob(raw) {
  const status = String(raw?.status || 'queued')
  return {
    jobId: String(raw?.job_id || raw?.id || ''),
    type: String(raw?.type || 'runner'),
    title: String(raw?.title || raw?.type || 'Runner job'),
    source: String(raw?.source || 'runner'),
    status,
    stage: String(raw?.stage || status),
    message: String(raw?.message || raw?.error || ''),
    progress: Math.min(1, Math.max(0, finiteNumber(raw?.progress, ['complete', 'succeeded'].includes(status) ? 1 : 0))),
    currentStep: finiteNumber(raw?.current_step ?? raw?.currentStep),
    maxSteps: finiteNumber(raw?.max_steps ?? raw?.maxSteps),
    parentJobId: String(raw?.parent_job_id || raw?.parentJobId || ''),
    childJobIds: Array.isArray(raw?.child_job_ids)
      ? raw.child_job_ids.map((value) => String(value))
      : [],
    requestId: String(raw?.request_id || raw?.requestId || ''),
    markerStart: String(raw?.marker_start || raw?.markerStart || ''),
    markerEnd: String(raw?.marker_end || raw?.markerEnd || ''),
    recordingPath: String(raw?.recording_path || raw?.recordingPath || ''),
    createdAt: raw?.created_at ?? raw?.createdAt ?? '',
    updatedAt: raw?.updated_at ?? raw?.updatedAt ?? '',
    error: String(raw?.error || ''),
    revision: finiteNumber(raw?.revision),
  }
}

function publishRunnerBackgroundJob(raw) {
  const job = normalizeRunnerBackgroundJob(raw)
  if (!job.jobId) return
  runnerBackgroundJobs.set(job.jobId, job)
  if (mainWindow && !mainWindow.isDestroyed()) {
    mainWindow.webContents.send('runner:job-state', job)
  }
}

async function readRunnerBackgroundJobs() {
  const response = await nodeHttpRequest(`${runnerEndpoint()}/api/v1/jobs/state`, {
    headers: { ...runnerHeaders(), Accept: 'application/json' },
    timeoutMs: Math.max(1000, Math.trunc(finiteNumber(process.env.RUNNER_HEALTH_TIMEOUT_MS, DEFAULT_VGGT_HEALTH_TIMEOUT_MS))),
  })
  if (!response.ok) {
    throw new Error(`Runner background jobs failed with HTTP ${response.status}${await readResponseDetail(response)}`)
  }
  const payload = await response.json()
  const jobs = Array.isArray(payload?.jobs) ? payload.jobs.map(normalizeRunnerBackgroundJob) : []
  for (const job of jobs) {
    if (job.jobId) runnerBackgroundJobs.set(job.jobId, job)
  }
  return jobs
}

function scheduleRunnerJobEventReconnect() {
  if (runnerJobEventReconnectTimer || !isElectronRuntime) return
  runnerJobEventReconnectTimer = setTimeout(() => {
    runnerJobEventReconnectTimer = null
    startRunnerJobEventStream()
  }, 2000)
}

function parseRunnerJobEventBlock(block) {
  const data = block
    .split(/\r?\n/)
    .filter((line) => line.startsWith('data:'))
    .map((line) => line.slice(5).trimStart())
    .join('\n')
  if (!data) return
  try {
    publishRunnerBackgroundJob(JSON.parse(data))
  } catch {
    // Ignore one malformed event; the next state update is a full snapshot.
  }
}

function startRunnerJobEventStream() {
  if (!isElectronRuntime || runnerJobEventRequest) return
  const parsed = new URL(`${runnerEndpoint()}/api/v1/jobs/events`)
  const transport = parsed.protocol === 'https:' ? https : http
  let buffer = ''
  const req = transport.request(parsed, {
    method: 'GET',
    headers: {
      ...runnerHeaders(),
      Accept: 'text/event-stream',
      'Cache-Control': 'no-cache',
    },
  }, (res) => {
    if ((Number(res.statusCode) || 0) < 200 || (Number(res.statusCode) || 0) >= 300) {
      res.resume()
      runnerJobEventRequest = null
      scheduleRunnerJobEventReconnect()
      return
    }
    res.setEncoding('utf8')
    res.on('data', (chunk) => {
      buffer += chunk
      for (;;) {
        const match = /\r?\n\r?\n/.exec(buffer)
        if (!match) break
        const block = buffer.slice(0, match.index)
        buffer = buffer.slice(match.index + match[0].length)
        parseRunnerJobEventBlock(block)
      }
    })
    res.on('end', () => {
      runnerJobEventRequest = null
      scheduleRunnerJobEventReconnect()
    })
    res.on('error', () => {
      runnerJobEventRequest = null
      scheduleRunnerJobEventReconnect()
    })
  })
  runnerJobEventRequest = req
  req.on('error', () => {
    runnerJobEventRequest = null
    scheduleRunnerJobEventReconnect()
  })
  req.end()
}

function runnerMcpEndpoint() {
  return `${runnerEndpoint()}/mcp/`
}

async function withRunnerMcpClient(operation) {
  const transport = new StreamableHTTPClientTransport(new URL(runnerMcpEndpoint()), {
    requestInit: { headers: runnerHeaders() },
  })
  const client = new McpClient(
    { name: 'bayesmech-vision-ux', version: '0.1.0' },
    { capabilities: {} },
  )
  try {
    await client.connect(transport, {
      timeout: Math.max(
        1000,
        Math.trunc(finiteNumber(process.env.RUNNER_HEALTH_TIMEOUT_MS, DEFAULT_VGGT_HEALTH_TIMEOUT_MS)),
      ),
    })
    return await operation(client)
  } finally {
    await client.close().catch(() => transport.close().catch(() => {}))
  }
}

async function listRunnerMcpTools() {
  return withRunnerMcpClient(async (client) => {
    const tools = []
    let cursor
    do {
      const page = await client.listTools(
        cursor ? { cursor } : undefined,
        { timeout: DEFAULT_VGGT_HEALTH_TIMEOUT_MS },
      )
      tools.push(...page.tools)
      cursor = page.nextCursor
    } while (cursor)
    return tools
  })
}

async function callRunnerMcpTool(name, args = {}, timeoutMs = DEFAULT_VGGT_REQUEST_TIMEOUT_MS) {
  if (!name || typeof name !== 'string') throw new Error('MCP tool name is required.')
  if (args === null || typeof args !== 'object' || Array.isArray(args)) {
    throw new Error('MCP tool arguments must be an object.')
  }
  const requestTimeout = Math.max(1000, Math.trunc(finiteNumber(timeoutMs, DEFAULT_VGGT_REQUEST_TIMEOUT_MS)))
  return withRunnerMcpClient((client) => client.callTool(
    { name, arguments: args },
    undefined,
    { timeout: requestTimeout, maxTotalTimeout: requestTimeout },
  ))
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

function agentHarnessSystemContext(recordingPath) {
  const resolved = path.resolve(recordingPath)
  let controlProject
  try {
    controlProject = controlManifestForRecording(
      path.dirname(resolved),
      path.basename(resolved),
    )
  } catch {
    return ''
  }
  if (!controlProject) return ''

  const robotCars = controlProject.devices.filter(
    (device) => device.enabled && device.deviceType === 'ROBOT_CAR_DEVICE',
  )
  if (!robotCars.length) return ''

  return [
    'The active physical interface includes a robot car.',
    'Its observation inputs are a camera and an ultrasonic distance sensor.',
    'Its actuation output is four independently commanded wheel speeds: left front, right front, left back, and right back.',
    'This describes available hardware, not current observations. Do not claim a camera image, distance reading, or robot motion unless it is present in attached frames, telemetry, or tool results.',
  ].join(' ')
}

function normalizedAgentVideoContexts(request, mainRecordingPath) {
  const contexts = [{
    name: 'main',
    displayName: 'main',
    path: mainRecordingPath,
  }]
  const usedNames = new Set(['main'])
  const usedPaths = new Set([mainRecordingPath])
  for (const value of Array.isArray(request?.videoContexts) ? request.videoContexts : []) {
    if (contexts.length >= 8) break
    const name = normalizedVideoContextName(value?.name)
    const contextPath = path.resolve(String(value?.path || ''))
    if (!name || name === 'main' || usedNames.has(name) || usedPaths.has(contextPath)) continue
    if (!contextPath.endsWith('.vis.pb') || !safeStat(contextPath)?.isFile()) continue
    contexts.push({
      name,
      displayName: String(value?.displayName || name).trim() || name,
      path: contextPath,
    })
    usedNames.add(name)
    usedPaths.add(contextPath)
  }
  return contexts
}

function sampleAgentVideoContext(
  context,
  configuredFrameCount,
  targetSampleFps,
  fallbackSourceFps,
) {
  const offsets = getFrameOffsets(context.path)
  let recordingDurationSeconds = 0
  if (offsets.length > 1 && configuredFrameCount > 0) {
    const fd = fs.openSync(context.path, 'r')
    try {
      const firstFrame = decodedFrameAt(context.path, offsets, 0, fd)
      const lastFrame = decodedFrameAt(context.path, offsets, offsets.length - 1, fd)
      recordingDurationSeconds = timestampDeltaSeconds(
        frameIdentifierFor(firstFrame, 0).timestampNs,
        frameIdentifierFor(lastFrame, offsets.length - 1).timestampNs,
      )
    } finally {
      fs.closeSync(fd)
    }
  }
  const sampledIndexes = buildGemmaSampleIndexes(
    offsets.length,
    recordingDurationSeconds,
    targetSampleFps,
    configuredFrameCount,
    fallbackSourceFps,
  )
  const uploads = []
  const timestampsNs = []
  if (sampledIndexes.length) {
    const fd = fs.openSync(context.path, 'r')
    try {
      for (const frameIndex of sampledIndexes) {
        const frame = decodedFrameAt(context.path, offsets, frameIndex, fd)
        const jpeg = rgbFrameJpeg(frame)
        if (!jpeg) continue
        const identifier = frameIdentifierFor(frame, frameIndex)
        timestampsNs.push(identifier.timestampNs)
        uploads.push({
          name: 'frames',
          filename: `${context.name}_frame_${String(frameIndex).padStart(6, '0')}.jpg`,
          contentType: 'image/jpeg',
          value: jpeg,
        })
      }
    } finally {
      fs.closeSync(fd)
    }
  }
  const firstTimestamp = timestampsNs[0]
  return {
    ...context,
    uploads,
    timestampsSec: timestampsNs.map((value) => timestampDeltaSeconds(firstTimestamp, value)),
  }
}

async function runAgentChat(request) {
  if (!request?.recordingPath || typeof request.recordingPath !== 'string') {
    throw new Error('Gemma chat requires a selected project.')
  }
  if (!request.recordingPath.endsWith('.vis.pb')) {
    throw new Error('Gemma chat requires a project recording.')
  }
  const message = String(request.message || '').trim()
  if (!message) throw new Error('Gemma chat requires a message.')

  const resolved = path.resolve(request.recordingPath)
  const configuredFrameCount = Math.trunc(
    finiteNumber(process.env.GEMMA_VIDEO_MAX_FRAMES, MAX_GEMMA_UPLOAD_FRAMES),
  )
  const targetSampleFps = Math.max(
    0.1,
    finiteNumber(process.env.GEMMA_VIDEO_SAMPLE_FPS, DEFAULT_GEMMA_SAMPLE_FPS),
  )
  const contexts = normalizedAgentVideoContexts(request, resolved).map((context) => (
    sampleAgentVideoContext(
      context,
      configuredFrameCount,
      targetSampleFps,
      finiteNumber(process.env.GEMMA_VIDEO_FALLBACK_FPS, 30),
    )
  ))
  const activeVideoContext = contexts.some(
    (context) => context.name === normalizedVideoContextName(request.activeVideoContext),
  )
    ? normalizedVideoContextName(request.activeVideoContext)
    : 'main'
  const uploads = contexts.flatMap((context) => context.uploads)
  const timestampsSec = contexts.flatMap((context) => context.timestampsSec)
  let frameOffset = 0
  const videoContexts = contexts.map((context) => {
    const value = {
      name: context.name,
      display_name: context.displayName,
      start: frameOffset,
      count: context.uploads.length,
      timestamps_sec: context.timestampsSec,
    }
    frameOffset += context.uploads.length
    return value
  })
  const history = Array.isArray(request.history)
    ? request.history
      .filter((item) => item && ['user', 'assistant'].includes(item.role) && String(item.text || '').trim())
      .slice(-24)
      .map((item) => ({ role: item.role, text: String(item.text).slice(0, 32000) }))
    : []
  const multipart = buildMultipartBody([
    ...uploads,
    { name: 'message', value: message },
    { name: 'timestamps_sec', value: JSON.stringify(timestampsSec) },
    { name: 'video_contexts', value: JSON.stringify(videoContexts) },
    { name: 'active_video_context', value: activeVideoContext },
    { name: 'history', value: JSON.stringify(history) },
    { name: 'request_id', value: String(request.requestId || '') },
    { name: 'chat_id', value: String(request.chatId || '') },
    { name: 'recording_path', value: resolved },
    { name: 'system_context', value: agentHarnessSystemContext(resolved) },
  ])
  const timeoutMs = Math.max(
    1000,
    Math.trunc(finiteNumber(process.env.GEMMA_REQUEST_TIMEOUT_MS, DEFAULT_GEMMA_REQUEST_TIMEOUT_MS)),
  )
  const submittedResponse = await nodeHttpRequest(`${runnerEndpoint()}/api/v1/agent/jobs`, {
    method: 'POST',
    headers: {
      ...runnerHeaders(),
      Accept: 'application/json',
      'Content-Type': multipart.contentType,
    },
    body: multipart.body,
    timeoutMs,
  })
  if (!submittedResponse.ok) {
    throw new Error(`Gemma job submission failed with HTTP ${submittedResponse.status}${await readResponseDetail(submittedResponse)}`)
  }
  const submitted = await submittedResponse.json()
  const jobId = String(submitted?.job_id || submitted?.id || '')
  if (!jobId) throw new Error('Runner accepted Gemma inference without returning a job id.')

  const deadline = Date.now() + timeoutMs
  for (;;) {
    if (Date.now() > deadline) {
      throw new Error(`Gemma job ${jobId} exceeded the ${Math.round(timeoutMs / 1000)}s timeout.`)
    }
    await new Promise((resolveWait) => setTimeout(resolveWait, 750))
    const stateResponse = await nodeHttpRequest(
      `${runnerEndpoint()}/api/v1/agent/jobs/${encodeURIComponent(jobId)}`,
      {
        headers: { ...runnerHeaders(), Accept: 'application/json' },
        timeoutMs: Math.max(
          1000,
          Math.trunc(finiteNumber(process.env.RUNNER_HEALTH_TIMEOUT_MS, DEFAULT_VGGT_HEALTH_TIMEOUT_MS)),
        ),
      },
    )
    if (!stateResponse.ok) {
      throw new Error(`Gemma job status failed with HTTP ${stateResponse.status}${await readResponseDetail(stateResponse)}`)
    }
    const state = await stateResponse.json()
    if (['failed', 'cancelled'].includes(state.status)) {
      throw new Error(state.error || state.message || `Gemma job ${state.status}.`)
    }
    if (state.status !== 'complete') continue

    const resultResponse = await nodeHttpRequest(
      `${runnerEndpoint()}/api/v1/agent/jobs/${encodeURIComponent(jobId)}/result`,
      {
        headers: { ...runnerHeaders(), Accept: 'application/json' },
        timeoutMs,
      },
    )
    if (!resultResponse.ok) {
      throw new Error(`Gemma result download failed with HTTP ${resultResponse.status}${await readResponseDetail(resultResponse)}`)
    }
    const result = await resultResponse.json()
    return {
      jobId,
      text: String(result?.text || ''),
      model: String(result?.model || ''),
      sampledFrameCount: finiteNumber(result?.sampled_frame_count),
      toolCalls: Array.isArray(result?.tool_calls)
        ? result.tool_calls.map((call) => ({
            name: String(call?.name || ''),
            arguments: call?.arguments && typeof call.arguments === 'object' ? call.arguments : {},
            result: call?.result,
          }))
        : [],
      activeVideoContext: normalizedVideoContextName(result?.active_video_context) || activeVideoContext,
    }
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
    { name: 'start_splat', value: worldgenSplatDisabled() ? 'false' : 'true' },
    { name: 'request_id', value: String(request.requestId || '') },
    { name: 'marker_start', value: String(request.markerStart || '') },
    { name: 'marker_end', value: String(request.markerEnd || '') },
    { name: 'recording_path', value: resolved },
  ])

  const endpoint = worldgenEndpoint()
  await ensureWorldgenModel(endpoint, token)

  const requestTimeoutMs = Math.max(1000, Math.trunc(finiteNumber(process.env.VGGT_REQUEST_TIMEOUT_MS, DEFAULT_VGGT_REQUEST_TIMEOUT_MS)))
  let response
  let vggtJobId = ''
  try {
    response = await nodeHttpRequest(`${endpoint}${worldgenUsesRunner() ? '/jobs/vggt' : '/infer'}`, {
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

  let responseJson
  if (worldgenUsesRunner()) {
    const submitted = await response.json()
    vggtJobId = String(submitted?.job_id || submitted?.id || '')
    if (!vggtJobId) throw new Error('Runner accepted VGGT work without returning a job id.')
    const deadline = Date.now() + requestTimeoutMs
    for (;;) {
      if (Date.now() > deadline) {
        throw new Error(`VGGT job ${vggtJobId} exceeded the ${Math.round(requestTimeoutMs / 1000)}s request timeout.`)
      }
      await new Promise((resolveWait) => setTimeout(resolveWait, 1000))
      const stateResponse = await nodeHttpRequest(`${endpoint}/jobs/${encodeURIComponent(vggtJobId)}`, {
        headers: {
          ...runnerHeaders(token),
          Accept: 'application/json',
        },
        timeoutMs: Math.max(1000, Math.trunc(finiteNumber(process.env.RUNNER_HEALTH_TIMEOUT_MS, DEFAULT_VGGT_HEALTH_TIMEOUT_MS))),
      })
      if (!stateResponse.ok) {
        throw new Error(`VGGT job status failed with HTTP ${stateResponse.status}${await readResponseDetail(stateResponse)}`)
      }
      const state = await stateResponse.json()
      if (state.status === 'failed' || state.status === 'cancelled') {
        throw new Error(state.error || state.message || `VGGT job ${state.status}.`)
      }
      if (state.status !== 'complete') continue
      const resultResponse = await nodeHttpRequest(`${endpoint}/jobs/${encodeURIComponent(vggtJobId)}/result`, {
        headers: {
          ...runnerHeaders(token),
          Accept: 'application/json',
        },
        timeoutMs: requestTimeoutMs,
      })
      if (!resultResponse.ok) {
        throw new Error(`VGGT result download failed with HTTP ${resultResponse.status}${await readResponseDetail(resultResponse)}`)
      }
      responseJson = await resultResponse.json()
      break
    }
  } else {
    responseJson = await response.json()
  }
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
  if (splatInfo.jobId) {
    worldgenSplatDestinations.set(
      splatInfo.jobId,
      worldgenSplatPaths(outputPath, splatInfo.jobId),
    )
  }

  const message = encodeWorldgenResponse(
    responseJson,
    sourceFrames,
    settledRequest,
    endpoint,
    splatInfo,
  )
  const history = persistWorldgenComputation(resolved, outputPath, message)
  return {
    ...worldgenPreviewFromMessages(history, outputPath),
    vggtJobId,
  }
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
  ipcMain.handle('project:create', () => createProject())
  ipcMain.handle('project:create-control', async (_event, preset) => {
    const created = createControlProject(preset)
    void ensureStreamlogRunning()
    return created
  })
  ipcMain.handle('project:add-device', async (_event, recordingPath, preset) => {
    const updated = addDeviceToProject(recordingPath, preset)
    void ensureStreamlogRunning()
    return updated
  })
  ipcMain.handle('project:rename', (_event, recordingPath, displayName) => (
    renameProject(recordingPath, displayName)
  ))
  ipcMain.handle('workspace:load', () => loadDesktopWorkspaceState())
  ipcMain.handle('workspace:save', (_event, projectPaths, activeProjectPath) => (
    saveDesktopWorkspaceState(projectPaths, activeProjectPath)
  ))
  ipcMain.handle('project-state:load', (_event, projectPath) => (
    loadPersistentProjectState(projectPath)
  ))
  ipcMain.handle('project-state:save', (_event, projectPath, patch) => (
    savePersistentProjectState(projectPath, patch)
  ))
  ipcMain.handle('control-service:ensure', () => ensureStreamlogRunning())

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
  ipcMain.handle('domain:reconstruction', (_event, filePath) => readDomainReconstruction(filePath))
  ipcMain.handle('domain:triangulation', (_event, filePath, frameNumber) => (
    readDomainTriangulation(filePath, frameNumber)
  ))
  ipcMain.handle('chat:thread', (_event, recordingPath) => readChatThread(recordingPath))
  ipcMain.handle('chat-workspace:load', (_event, videoId, recordingPath) => loadChatWorkspace(videoId, recordingPath))
  ipcMain.handle('chat-workspace:create', (_event, videoId, recordingPath) => createChatSession(videoId, recordingPath))
  ipcMain.handle('chat-workspace:delete', (_event, videoId, recordingPath, chatId) => (
    deleteChatSession(videoId, recordingPath, chatId)
  ))
  ipcMain.handle('chat-workspace:save', (_event, videoId, recordingPath, session) => saveChatSession(videoId, recordingPath, session))
  ipcMain.handle('chat-workspace:activate', (_event, videoId, recordingPath, chatId) => setActiveChatSession(videoId, recordingPath, chatId))
  ipcMain.handle('agent:chat', (_event, request) => runAgentChat(request))
  ipcMain.handle('worldgen:run', (_event, request) => runWorldgen(request))
  ipcMain.handle('worldgen:read', (_event, filePath) => readWorldgen(filePath))
  ipcMain.handle('worldgen:splat-status', (_event, jobId) => pollWorldgenSplat(jobId))
  ipcMain.handle('worldgen:save-splat', (_event, filePath, splat) => saveWorldgenSplat(filePath, splat))
  ipcMain.handle('runner:health', () => readRunnerHealth())
  ipcMain.handle('runner:capabilities', () => readRunnerCapabilities())
  ipcMain.handle('runner:background-jobs', () => readRunnerBackgroundJobs())
  ipcMain.handle('runner:mcp-list-tools', () => listRunnerMcpTools())
  ipcMain.handle('runner:mcp-call-tool', (_event, name, args, timeoutMs) => (
    callRunnerMcpTool(name, args, timeoutMs)
  ))
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
    startRunnerJobEventStream()
    void ensureStreamlogRunning()

    app.on('activate', () => {
      if (BrowserWindow.getAllWindows().length === 0) createWindow()
      startRunnerJobEventStream()
    })
  })

  app.on('window-all-closed', () => {
    if (runnerJobEventReconnectTimer) clearTimeout(runnerJobEventReconnectTimer)
    runnerJobEventReconnectTimer = null
    runnerJobEventRequest?.destroy()
    runnerJobEventRequest = null
    if (process.platform !== 'darwin') app.quit()
  })

  app.on('before-quit', stopManagedStreamlog)
}

module.exports = {
  createProject,
  createControlProject,
  addDeviceToProject,
  agentHarnessSystemContext,
  renameProject,
  loadDesktopWorkspaceState,
  saveDesktopWorkspaceState,
  loadPersistentProjectState,
  savePersistentProjectState,
  workspaceVideoDirectory,
  ensureStreamlogRunning,
  stopManagedStreamlog,
  readControlProject,
  scanProject,
  scanVisFiles,
  readVisSummary,
  readVisFrame,
  readVisSensors,
  readIdoSlam,
  readSegmentationMasks,
  readMotionCapture,
  readDomainReconstruction,
  readDomainTriangulation,
  readChatThread,
  loadChatWorkspace,
  createChatSession,
  deleteChatSession,
  saveChatSession,
  setActiveChatSession,
  runAgentChat,
  runWorldgen,
  readWorldgen,
  pollWorldgenSplat,
  saveWorldgenSplat,
  readRunnerHealth,
  readRunnerCapabilities,
  readRunnerBackgroundJobs,
  submitRunnerJob,
  runRunnerJob,
  readRunnerJob,
  cancelRunnerJob,
  downloadRunnerArtifact,
  getVggtResponseType,
  persistWorldgenComputation,
  worldgenPreviewFromMessages,
  worldgenSplatPaths,
  buildGemmaSampleIndexes,
}
