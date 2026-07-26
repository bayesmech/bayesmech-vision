export type Vec3 = {
  x: number
  y: number
  z: number
}

export type Quat = {
  x: number
  y: number
  z: number
  w: number
}

export type Pose = {
  position: Vec3
  rotation: Quat
}

export type ProjectAnalysis = {
  key: string
  title: string
  kind: string
  source: 'vis' | 'artifact'
  suffix?: string
  path: string
  relativePath: string
  sizeBytes?: number
  sizeLabel?: string
  modifiedMs?: number
}

export type RecordingEntry = {
  id: string
  name: string
  fileStem: string
  path: string
  directoryPath: string
  relativePath: string
  sizeBytes: number
  sizeLabel: string
  modifiedMs: number
  analyses: ProjectAnalysis[]
}

export type ProjectScanResult = {
  rootPath: string
  name: string
  recordings: RecordingEntry[]
  error?: string
}

export type SelectProjectResponse = ProjectScanResult | { cancelled: true; error?: string }

export type SamplePoint = Vec3 & {
  confidence: number
}

export type SamplePlane = {
  type: number
  extentX: number
  extentZ: number
  centerPose: Pose | null
  polygon: Vec3[]
}

export type VisSample = {
  sampleIndex: number
  frameNumber: number
  timestampNs: string
  cameraPose: Pose | null
  points: SamplePoint[]
  planes: SamplePlane[]
  rgb: {
    width: number
    height: number
    format: number
    bytes: number
  } | null
  depth: {
    width: number
    height: number
    format: number
    bytes: number
  } | null
  userTextInput: string
}

export type VisSummary = {
  path: string
  fileName: string
  sizeBytes: number
  sizeLabel: string
  frameCount: number
  decodedFrames: number
  parseErrors: number
  firstTimestampNs: string
  lastTimestampNs: string
  durationSeconds: number
  firstFrameNumber: number
  lastFrameNumber: number
  devices: string[]
  framesWithRgb: number
  framesWithDepth: number
  framesWithPointCloud: number
  framesWithPlanes: number
  sampledPointCount: number
  sampledPlaneCount: number
  rgbPreview: {
    dataUrl: string
    width: number
    height: number
    frameNumber: number
  } | null
  depthStats: {
    width: number
    height: number
    format: number
    minMeters: number
    maxMeters: number
    sampledPixels: number
  } | null
  bounds: {
    min: Vec3
    max: Vec3
  } | null
  samples: VisSample[]
}

export type VisFrame = {
  index: number
  frameCount: number
  frameNumber: number
  timestampNs: string
  width: number
  height: number
  // JPEG data URL for the frame's RGB image, or null when the frame carries no
  // decodable RGB (non-JPEG format or an RGB-less frame).
  dataUrl: string | null
}

export type GpsSample = {
  latitude: number
  longitude: number
  altitude: number
  accuracy: number
  bearing: number
  speed: number
  timestampMs: string
}

export type SensorSample = {
  index: number
  frameNumber: number
  timestampNs: string
  deviceId: string
  linearAcceleration: Vec3 | null
  angularVelocity: Vec3 | null
  gravity: Vec3 | null
  magneticField: Vec3 | null
  cameraPose: Pose | null
  gps: GpsSample | null
}

export type SensorDataSummary = {
  path: string
  frameCount: number
  samples: SensorSample[]
}

export type IdoSlamPose = {
  frameIndex: number
  frameNumber: number
  timestampNs: string
  position: Vec3
  eulerDegrees: Vec3
}

export type IdoSlamPairMotion = {
  frameIndex: number
  status: string
  goodMatchCount: number
  essentialInlierCount: number
  essentialInlierRatio: number
  translationMagnitude: number
  rotationDeg: number
}

export type IdoSlamWidthEstimate = {
  frameIndex: number
  latitude: number
  longitude: number
  widthM: number
  leftOffsetM: number
  rightOffsetM: number
  bikeFraction: number
  method: string
}

export type IdoSlamCenterlinePoint = {
  progressM: number
  centerX: number
  centerY: number
  widthM: number
  leftX: number
  leftY: number
  rightX: number
  rightY: number
}

export type IdoSlamSummary = {
  path: string
  framePoses: IdoSlamPose[]
  refinedFramePoses: IdoSlamPose[]
  pairwiseMotion: IdoSlamPairMotion[]
  planeWidthEstimates: IdoSlamWidthEstimate[]
  triangulatedWidthEstimates: IdoSlamWidthEstimate[]
  canonicalCenterline: IdoSlamCenterlinePoint[]
  groundPointCount: number
  pairDebugCount: number
  correspondenceCount: number
  inlierCount: number
}

export type SegMask = {
  objectId: number
  label: string
  // base64-encoded [H:u32le][W:u32le][zlib(packbits(mask))] payload.
  maskData: string
}

export type MotionCapturePoint = {
  frameIndex: number
  cx: number
  cy: number
  interpolated: boolean
}

export type MotionCaptureTrack = {
  trackId: number
  label: string
  kind: 'motion' | 'segmentation'
  detectedFrames: number
  totalPositions: number
  presenceFraction: number
  points: MotionCapturePoint[]
}

export type MotionCaptureOverlay = {
  frameNumber: number
  heatmapIndex: number
  heatmapData: string | null
  maxMotionRaw: number
  stabilizationMethod: number
  stabilizationConfidence: number
  tracks: MotionCaptureTrack[]
}

export type VideoMarker = {
  id: string
  name: string
  reference: string
  frameIndex: number
  frameNumber: number
  seconds: number
  color: string
}

export type VideoPlaybackState = {
  index: number
  playing: boolean
  speed: number
  markers: VideoMarker[]
}

export type ChatAnalysisParameter = {
  name: string
  value: string
  unit: string
}

export type ChatAnalysis = {
  title: string
  text: string
  parameters: ChatAnalysisParameter[]
}

export type SavedChatTurn = {
  role: 'user' | 'assistant'
  text: string
  timestampNs: string
}

export type ChatThread = {
  analysis: ChatAnalysis | null
  turns: SavedChatTurn[]
}

export type WorkspaceChatMessage = {
  id: string
  role: 'user' | 'assistant' | 'command'
  text: string
  createdAt: string
  status?: 'pending' | 'ok' | 'error'
}

export type WorkspaceChatSession = {
  id: string
  title: string
  createdAt: string
  updatedAt: string
  messages: WorkspaceChatMessage[]
  markers: VideoMarker[]
}

export type VideoChatWorkspace = {
  version: 1
  videoId: string
  recordingPath: string
  activeChatId: string
  chats: WorkspaceChatSession[]
}

export type WorldgenPoint = Vec3 & {
  r: number
  g: number
  b: number
  confidence: number
  frameIndex: number
  frameNumber: number
  framePointIndex: number
  u: number
  v: number
}

export type WorldgenSplatPoint = Vec3 & {
  r: number
  g: number
  b: number
  opacity: number
  scale: number
  // Optional anisotropic Gaussian parameters. When present the splat renderer
  // draws a true oriented ellipsoid; when absent it falls back to an isotropic
  // Gaussian of radius `scale`. sx/sy/sz are linear world-space std devs;
  // qx/qy/qz/qw is the orientation quaternion.
  sx?: number
  sy?: number
  sz?: number
  qx?: number
  qy?: number
  qz?: number
  qw?: number
}

export type WorldgenCamera = Vec3 & {
  frameIndex: number
  frameNumber: number
  matrix: number[]
  intrinsics: number[]
}

export type WorldgenFrame = {
  sampledFrameIndex: number
  frameIndex: number
  frameNumber: number
  imageWidth: number
  imageHeight: number
  pointCount: number
  returnedPointCount: number
}

export type WorldgenSplat = {
  status: string
  jobId?: string
  stage?: string
  message?: string
  progress?: number
  currentStep?: number
  plyPath: string
  previewJsonPath: string
  error: string
  gaussianCount: number
  previewPointCount: number
  initPointCount: number
  trainingFrameCount: number
  maxSteps: number
  maxGaussians: number
  elapsedSec: number
  trainer: string
}

export type WorldgenResult = {
  id: string
  vggtJobId?: string
  outputPath: string
  markerStart: string
  markerEnd: string
  startFrameIndex: number
  endFrameIndex: number
  frameCount: number
  pointCount: number
  returnedPointCount: number
  model: string
  elapsedSec: number
  frames: WorldgenFrame[]
  points: WorldgenPoint[]
  cameras: WorldgenCamera[]
  splat: WorldgenSplat | null
  splatPoints: WorldgenSplatPoint[]
}

export type WorldgenRequest = {
  requestId: string
  recordingPath: string
  markerStart: string
  markerEnd: string
  startFrameIndex: number
  endFrameIndex: number
  resolution?: number
  confidenceThreshold?: number
  maxPointsPerFrame?: number
  windowSize?: number
}

export type WorldgenSplatStatus = {
  jobId: string
  status: string
  stage?: string
  message?: string
  progress?: number
  currentStep?: number
  maxSteps?: number
  gaussianCount?: number
  previewPointCount?: number
  elapsedSec?: number
  error?: string
  plyPath?: string
  previewJsonPath?: string
  points?: WorldgenSplatPoint[]
}

export type WorkspaceTabType = 'scene' | 'point-cloud' | 'planes' | 'video' | 'sensors' | 'analysis' | 'worldgen'

export type WorkspaceTabRequest = {
  requestId: string
  type: WorkspaceTabType
  title: string
  analysisKey?: string
  worldgenResultId?: string
}

export type WorkspaceTab = {
  id: string
  type: WorkspaceTabType
  title: string
  analysisKey?: string
  worldgenResultId?: string
}

export type LeafNode = {
  id: string
  type: 'leaf'
  activeTabId: string
  tabs: WorkspaceTab[]
}

export type SplitNode = {
  id: string
  type: 'split'
  direction: 'row' | 'column'
  ratio: number
  first: LayoutNode
  second: LayoutNode
}

export type LayoutNode = LeafNode | SplitNode

export type WindowAction =
  | 'reload'
  | 'toggle-devtools'
  | 'reset-zoom'
  | 'zoom-in'
  | 'zoom-out'
  | 'toggle-fullscreen'
  | 'minimize'
  | 'close'

export type RunnerHealth = {
  ok: boolean
  service: string
  version: string
  hostname: string
  auth_required: boolean
  max_workers: number
  jobs: number
  disk_free_bytes: number
}

export type RunnerCapability = {
  name: string
  title: string
  description: string
  input_suffixes: string[]
  available: boolean
}

export type RunnerCapabilities = {
  runner_version: string
  jobs: RunnerCapability[]
  services: Array<{ name: string; title: string; endpoint: string }>
  mcp?: {
    transport: 'streamable-http'
    endpoint: string
  }
}

export type RunnerBackgroundJob = {
  jobId: string
  type: string
  title: string
  source: string
  status: string
  stage: string
  message: string
  progress: number
  currentStep: number
  maxSteps: number
  parentJobId: string
  childJobIds: string[]
  requestId: string
  markerStart: string
  markerEnd: string
  recordingPath: string
  createdAt: string | number
  updatedAt: string | number
  error: string
  revision: number
}

export type RunnerMcpTool = {
  name: string
  title?: string
  description?: string
  inputSchema: Record<string, unknown>
  outputSchema?: Record<string, unknown>
}

export type RunnerMcpToolResult = {
  content: Array<
    | { type: 'text'; text: string }
    | { type: 'image'; data: string; mimeType: string }
    | { type: 'audio'; data: string; mimeType: string }
    | Record<string, unknown>
  >
  structuredContent?: Record<string, unknown>
  isError?: boolean
}

export type RunnerArtifact = {
  id: string
  name: string
  relative_path: string
  size: number
  sha256: string
}

export type RunnerJob = {
  id: string
  type: string
  status: 'uploading' | 'queued' | 'running' | 'succeeded' | 'failed' | 'cancelled'
  created_at: string
  started_at?: string | null
  finished_at?: string | null
  inputs: Array<{ name: string; size: number; sha256: string }>
  artifacts: RunnerArtifact[]
  exit_code?: number | null
  error?: string
  stdout_tail?: string
  stderr_tail?: string
  local_artifacts?: Array<RunnerArtifact & { local_path: string }>
}

export type RunnerSubmitRequest = {
  jobType: string
  recordingPath: string
  arguments?: string[]
  inputPaths?: string[]
}

export type BridgeApi = {
  selectProject: () => Promise<SelectProjectResponse>
  selectVisFiles: () => Promise<SelectProjectResponse>
  scanProject: (projectPath: string) => Promise<ProjectScanResult>
  readVisSummary: (filePath: string) => Promise<VisSummary>
  readVisFrame: (filePath: string, frameIndex: number) => Promise<VisFrame | null>
  readVisSensors: (filePath: string) => Promise<SensorDataSummary>
  readIdoSlam: (filePath: string) => Promise<IdoSlamSummary>
  readSegmentationMasks: (filePath: string, frameNumber: number) => Promise<SegMask[] | null>
  readSegmentationLabels: (filePath: string) => Promise<string[]>
  readMotionCapture: (filePath: string, frameNumber: number) => Promise<MotionCaptureOverlay | null>
  readChatThread: (recordingPath: string) => Promise<ChatThread>
  loadChatWorkspace: (videoId: string, recordingPath: string) => Promise<VideoChatWorkspace>
  createChatSession: (videoId: string, recordingPath: string) => Promise<VideoChatWorkspace>
  saveChatSession: (
    videoId: string,
    recordingPath: string,
    session: WorkspaceChatSession,
  ) => Promise<boolean>
  setActiveChatSession: (videoId: string, recordingPath: string, chatId: string) => Promise<boolean>
  runWorldgen: (request: WorldgenRequest) => Promise<WorldgenResult>
  readWorldgen: (filePath: string) => Promise<WorldgenResult>
  pollWorldgenSplat: (jobId: string) => Promise<WorldgenSplatStatus>
  saveWorldgenSplat: (filePath: string, splat: WorldgenSplatStatus) => Promise<WorldgenResult>
  readRunnerHealth: () => Promise<RunnerHealth>
  readRunnerCapabilities: () => Promise<RunnerCapabilities>
  readRunnerBackgroundJobs: () => Promise<RunnerBackgroundJob[]>
  onRunnerJobState: (callback: (job: RunnerBackgroundJob) => void) => () => void
  listRunnerMcpTools: () => Promise<RunnerMcpTool[]>
  callRunnerMcpTool: (
    name: string,
    args?: Record<string, unknown>,
    timeoutMs?: number,
  ) => Promise<RunnerMcpToolResult>
  submitRunnerJob: (request: RunnerSubmitRequest) => Promise<RunnerJob>
  runRunnerJob: (request: RunnerSubmitRequest) => Promise<RunnerJob>
  readRunnerJob: (jobId: string) => Promise<RunnerJob>
  cancelRunnerJob: (jobId: string) => Promise<RunnerJob>
  downloadRunnerArtifact: (jobId: string, artifactId: string, destinationPath: string) => Promise<string>
  revealPath: (filePath: string) => Promise<boolean>
  performWindowAction: (action: WindowAction) => Promise<boolean>
}

declare global {
  interface Window {
    bayesmech?: BridgeApi
  }
}
