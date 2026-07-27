const { contextBridge, ipcRenderer } = require('electron')

contextBridge.exposeInMainWorld('bayesmech', {
  selectProject: () => ipcRenderer.invoke('project:select'),
  createProject: () => ipcRenderer.invoke('project:create'),
  createControlProject: (preset) => ipcRenderer.invoke('project:create-control', preset),
  addDeviceToProject: (recordingPath, preset) => (
    ipcRenderer.invoke('project:add-device', recordingPath, preset)
  ),
  renameProject: (recordingPath, displayName) => (
    ipcRenderer.invoke('project:rename', recordingPath, displayName)
  ),
  loadWorkspaceState: () => ipcRenderer.invoke('workspace:load'),
  saveWorkspaceState: (projectPaths, activeProjectPath) => (
    ipcRenderer.invoke('workspace:save', projectPaths, activeProjectPath)
  ),
  loadProjectState: (projectPath) => ipcRenderer.invoke('project-state:load', projectPath),
  saveProjectState: (projectPath, patch) => (
    ipcRenderer.invoke('project-state:save', projectPath, patch)
  ),
  ensureControlService: () => ipcRenderer.invoke('control-service:ensure'),
  selectVisFiles: () => ipcRenderer.invoke('vis:select-files'),
  scanProject: (projectPath) => ipcRenderer.invoke('project:scan', projectPath),
  readVisSummary: (filePath) => ipcRenderer.invoke('vis:summary', filePath),
  readVisFrame: (filePath, frameIndex) => ipcRenderer.invoke('vis:frame', filePath, frameIndex),
  readVisSensors: (filePath) => ipcRenderer.invoke('vis:sensors', filePath),
  readIdoSlam: (filePath) => ipcRenderer.invoke('idoslam:read', filePath),
  readSegmentationMasks: (filePath, frameNumber) => ipcRenderer.invoke('seg:masks', filePath, frameNumber),
  readSegmentationLabels: (filePath) => ipcRenderer.invoke('seg:labels', filePath),
  readMotionCapture: (filePath, frameNumber) => ipcRenderer.invoke('motioncap:frame', filePath, frameNumber),
  readDomainReconstruction: (filePath) => ipcRenderer.invoke('domain:reconstruction', filePath),
  readDomainTriangulation: (filePath, frameNumber) => (
    ipcRenderer.invoke('domain:triangulation', filePath, frameNumber)
  ),
  readChatThread: (recordingPath) => ipcRenderer.invoke('chat:thread', recordingPath),
  loadChatWorkspace: (videoId, recordingPath) => ipcRenderer.invoke('chat-workspace:load', videoId, recordingPath),
  createChatSession: (videoId, recordingPath) => ipcRenderer.invoke('chat-workspace:create', videoId, recordingPath),
  deleteChatSession: (videoId, recordingPath, chatId) => (
    ipcRenderer.invoke('chat-workspace:delete', videoId, recordingPath, chatId)
  ),
  saveChatSession: (videoId, recordingPath, session) => ipcRenderer.invoke('chat-workspace:save', videoId, recordingPath, session),
  setActiveChatSession: (videoId, recordingPath, chatId) => ipcRenderer.invoke('chat-workspace:activate', videoId, recordingPath, chatId),
  sendAgentMessage: (request) => ipcRenderer.invoke('agent:chat', request),
  runWorldgen: (request) => ipcRenderer.invoke('worldgen:run', request),
  readWorldgen: (filePath) => ipcRenderer.invoke('worldgen:read', filePath),
  pollWorldgenSplat: (jobId) => ipcRenderer.invoke('worldgen:splat-status', jobId),
  saveWorldgenSplat: (filePath, splat) => ipcRenderer.invoke('worldgen:save-splat', filePath, splat),
  readRunnerHealth: () => ipcRenderer.invoke('runner:health'),
  readRunnerCapabilities: () => ipcRenderer.invoke('runner:capabilities'),
  readRunnerBackgroundJobs: () => ipcRenderer.invoke('runner:background-jobs'),
  onRunnerJobState: (callback) => {
    const listener = (_event, job) => callback(job)
    ipcRenderer.on('runner:job-state', listener)
    return () => ipcRenderer.removeListener('runner:job-state', listener)
  },
  listRunnerMcpTools: () => ipcRenderer.invoke('runner:mcp-list-tools'),
  callRunnerMcpTool: (name, args, timeoutMs) => (
    ipcRenderer.invoke('runner:mcp-call-tool', name, args, timeoutMs)
  ),
  submitRunnerJob: (request) => ipcRenderer.invoke('runner:submit', request),
  runRunnerJob: (request) => ipcRenderer.invoke('runner:run', request),
  readRunnerJob: (jobId) => ipcRenderer.invoke('runner:job', jobId),
  cancelRunnerJob: (jobId) => ipcRenderer.invoke('runner:cancel', jobId),
  downloadRunnerArtifact: (jobId, artifactId, destinationPath) => (
    ipcRenderer.invoke('runner:download-artifact', jobId, artifactId, destinationPath)
  ),
  revealPath: (filePath) => ipcRenderer.invoke('path:reveal', filePath),
  performWindowAction: (action) => ipcRenderer.invoke('window:action', action),
})
