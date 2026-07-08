const { contextBridge, ipcRenderer } = require('electron')

contextBridge.exposeInMainWorld('bayesmech', {
  selectProject: () => ipcRenderer.invoke('project:select'),
  selectVisFiles: () => ipcRenderer.invoke('vis:select-files'),
  scanProject: (projectPath) => ipcRenderer.invoke('project:scan', projectPath),
  readVisSummary: (filePath) => ipcRenderer.invoke('vis:summary', filePath),
  readVisFrame: (filePath, frameIndex) => ipcRenderer.invoke('vis:frame', filePath, frameIndex),
  readSegmentationMasks: (filePath, frameNumber) => ipcRenderer.invoke('seg:masks', filePath, frameNumber),
  readSegmentationLabels: (filePath) => ipcRenderer.invoke('seg:labels', filePath),
  runWorldgen: (request) => ipcRenderer.invoke('worldgen:run', request),
  revealPath: (filePath) => ipcRenderer.invoke('path:reveal', filePath),
  onOpenProject: (callback) => {
    const handler = () => callback()
    ipcRenderer.on('menu:open-project', handler)
    return () => ipcRenderer.removeListener('menu:open-project', handler)
  },
})
