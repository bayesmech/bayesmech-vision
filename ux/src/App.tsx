import { ChangeEvent, useCallback, useEffect, useMemo, useRef, useState } from 'react'
import AIChat from './components/AIChat'
import ProjectExplorer from './components/ProjectExplorer'
import SplitWorkspace from './components/SplitWorkspace'
import TopMenu from './components/TopMenu'
import type {
  ChatThread,
  IdoSlamSummary,
  ProjectScanResult,
  RecordingEntry,
  SegMask,
  SensorDataSummary,
  VideoPlaybackState,
  VisSummary,
  WorkspaceTabRequest,
  WorldgenResult,
} from './types'
import {
  readBrowserChatThread,
  readBrowserIdoSlam,
  readBrowserSegmentationLabels,
  readBrowserSegmentationMasks,
  readBrowserVisSensors,
  readBrowserVisFrame,
  readBrowserVisSummary,
  scanBrowserFiles,
} from './lib/browserProject'
import type { FrameGetter } from './lib/frameSource'
import {
  normalizeSegmentationLabel,
  parseSegmentationCommand,
  parseWorldgenCommand,
  type CommandResult,
  type CommandProgress,
  type OverlayState,
} from './lib/overlay'
import { compactNumber, shortPath } from './lib/format'

const LAST_PROJECT_KEY = 'bayesmech:lastProject'

const initialVideoState = (): VideoPlaybackState => ({
  index: 0,
  playing: false,
  speed: 1,
  markers: [],
})

function requestId() {
  return `request-${Date.now()}-${Math.random().toString(36).slice(2, 7)}`
}

function sleep(ms: number) {
  return new Promise((resolve) => window.setTimeout(resolve, ms))
}

function splatProgressMessage(splat: NonNullable<WorldgenResult['splat']>): string {
  const pct = Math.round(Math.max(0, Math.min(1, splat.progress ?? 0)) * 100)
  if (splat.status === 'complete') {
    return `Gaussian splat complete: ${compactNumber(splat.gaussianCount)} Gaussians, ${compactNumber(splat.previewPointCount)} preview splats.`
  }
  if (splat.status === 'failed') {
    return `Gaussian splat failed: ${splat.error || splat.message || 'unknown error'}`
  }
  const step = splat.currentStep && splat.maxSteps ? ` step ${splat.currentStep}/${splat.maxSteps}` : ''
  const count = splat.gaussianCount ? `, ${compactNumber(splat.gaussianCount)} Gaussians` : ''
  return `${splat.message || 'Optimizing Gaussian splat on the VGGT server.'}${step ? ` (${pct}%${step}${count})` : ` (${pct}%)`}`
}

function isCancelledProjectResponse(value: unknown): value is { cancelled: true; error?: string } {
  return Boolean(value && typeof value === 'object' && 'cancelled' in value)
}

export default function App() {
  const bridge = window.bayesmech
  const bridgeAvailable = Boolean(bridge)
  const fileInputRef = useRef<HTMLInputElement>(null)
  const folderInputRef = useRef<HTMLInputElement>(null)
  const browserFilesRef = useRef<Map<string, File>>(new Map())
  const [project, setProject] = useState<ProjectScanResult | null>(null)
  const [selectedRecording, setSelectedRecording] = useState<RecordingEntry | null>(null)
  const [summary, setSummary] = useState<VisSummary | null>(null)
  const [browserSourceFiles, setBrowserSourceFiles] = useState<File[]>([])
  const [filter, setFilter] = useState('')
  const [loadingProject, setLoadingProject] = useState(false)
  const [loadingSummary, setLoadingSummary] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [tabRequest, setTabRequest] = useState<WorkspaceTabRequest | null>(null)
  const [videoState, setVideoState] = useState<VideoPlaybackState>(() => initialVideoState())
  const [worldgenResults, setWorldgenResults] = useState<Record<string, WorldgenResult>>({})
  const [chatThread, setChatThread] = useState<ChatThread | null>(null)
  const [chatThreadLoading, setChatThreadLoading] = useState(false)
  const [chatThreadError, setChatThreadError] = useState<string | null>(null)

  const selectRecording = useCallback(
    async (recording: RecordingEntry) => {
      setSelectedRecording(recording)
      setSummary(null)
      setLoadingSummary(true)
      setError(null)
      try {
        const browserFile = browserFilesRef.current.get(recording.path)
        const nextSummary = browserFile
          ? await readBrowserVisSummary(recording, browserFile)
          : await bridge!.readVisSummary(recording.path)
        setSummary(nextSummary)
      } catch (err) {
        const message = err instanceof Error ? err.message : 'Failed to read .vis.pb summary.'
        setError(message)
      } finally {
        setLoadingSummary(false)
      }
    },
    [bridge],
  )

  const applyProject = useCallback(
    async (nextProject: ProjectScanResult) => {
      setProject(nextProject)
      if (nextProject.rootPath && !nextProject.rootPath.startsWith('browser://')) {
        localStorage.setItem(LAST_PROJECT_KEY, nextProject.rootPath)
      }
      if (nextProject.error) setError(nextProject.error)
      const firstRecording = nextProject.recordings[0] ?? null
      if (firstRecording) {
        await selectRecording(firstRecording)
      } else {
        setSelectedRecording(null)
        setSummary(null)
      }
    },
    [selectRecording],
  )

  const openProject = useCallback(async () => {
    if (!bridge) {
      folderInputRef.current?.click()
      return
    }

    setLoadingProject(true)
    setError(null)
    try {
      const response = await bridge.selectProject()
      if (isCancelledProjectResponse(response)) return
      browserFilesRef.current = new Map()
      setBrowserSourceFiles([])
      await applyProject(response)
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Failed to open project.'
      setError(message)
    } finally {
      setLoadingProject(false)
    }
  }, [applyProject, bridge])

  const openFiles = useCallback(async () => {
    if (!bridge) {
      fileInputRef.current?.click()
      return
    }

    setLoadingProject(true)
    setError(null)
    try {
      const response = await bridge.selectVisFiles()
      if (isCancelledProjectResponse(response)) return
      browserFilesRef.current = new Map()
      setBrowserSourceFiles([])
      await applyProject(response)
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Failed to open .vis.pb files.'
      setError(message)
    } finally {
      setLoadingProject(false)
    }
  }, [applyProject, bridge])

  const rescanProject = useCallback(async () => {
    if (!project?.rootPath) return
    setLoadingProject(true)
    setError(null)
    try {
      if (project.rootPath.startsWith('browser://')) {
        const scanned = scanBrowserFiles(browserSourceFiles)
        browserFilesRef.current = scanned.filesByPath
        await applyProject(scanned.project)
      } else {
        if (!bridge) throw new Error('Electron bridge is not available.')
        const nextProject = await bridge.scanProject(project.rootPath)
        await applyProject(nextProject)
      }
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Failed to rescan project.'
      setError(message)
    } finally {
      setLoadingProject(false)
    }
  }, [applyProject, bridge, browserSourceFiles, project?.rootPath])

  useEffect(() => {
    if (!bridge) return
    return bridge.onOpenProject(() => {
      void openProject()
    })
  }, [bridge, openProject])

  useEffect(() => {
    if (!bridge) return
    const lastProject = localStorage.getItem(LAST_PROJECT_KEY)
    if (!lastProject) return

    let cancelled = false
    setLoadingProject(true)
    bridge
      .scanProject(lastProject)
      .then((nextProject) => {
        if (!cancelled) void applyProject(nextProject)
      })
      .catch(() => {
        if (!cancelled) localStorage.removeItem(LAST_PROJECT_KEY)
      })
      .finally(() => {
        if (!cancelled) setLoadingProject(false)
      })

    return () => {
      cancelled = true
    }
  }, [applyProject, bridge])

  const loadBrowserFiles = async (event: ChangeEvent<HTMLInputElement>) => {
    const files = event.currentTarget.files
    if (!files?.length) return

    setLoadingProject(true)
    setError(null)
    try {
      const sourceFiles = Array.from(files)
      const scanned = scanBrowserFiles(sourceFiles)
      setBrowserSourceFiles(sourceFiles)
      browserFilesRef.current = scanned.filesByPath
      await applyProject(scanned.project)
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Failed to load selected .vis.pb files.'
      setError(message)
    } finally {
      setLoadingProject(false)
      event.currentTarget.value = ''
    }
  }

  // Random-access frame fetch for the video player: routes to the in-browser
  // decoder (no-bridge mode) or the Electron main process for the selected file.
  const getFrame = useCallback<FrameGetter>(
    async (index) => {
      const recording = selectedRecording
      if (!recording) return null
      const browserFile = browserFilesRef.current.get(recording.path)
      if (browserFile) return readBrowserVisFrame(browserFile, index)
      if (bridge?.readVisFrame) return bridge.readVisFrame(recording.path, index)
      return null
    },
    [selectedRecording, bridge],
  )

  const getSensorData = useCallback(async (): Promise<SensorDataSummary | null> => {
    const recording = selectedRecording
    if (!recording) return null
    const browserFile = browserFilesRef.current.get(recording.path)
    if (browserFile) return readBrowserVisSensors(recording, browserFile)
    if (bridge?.readVisSensors) return bridge.readVisSensors(recording.path)
    return null
  }, [bridge, selectedRecording])

  const getIdoSlamData = useCallback(async (): Promise<IdoSlamSummary | null> => {
    const analysis = selectedRecording?.analyses.find((item) => item.key === 'idoslam')
    if (!analysis) return null
    const browserFile = browserFilesRef.current.get(analysis.path)
    if (browserFile) return readBrowserIdoSlam(analysis.path, browserFile)
    if (bridge?.readIdoSlam) return bridge.readIdoSlam(analysis.path)
    return null
  }, [bridge, selectedRecording])

  // Overlay/command state shared by the AI chat box and the video command bar.
  const [segmentationOn, setSegmentationOn] = useState(false)
  const [segmentationMaskLabel, setSegmentationMaskLabel] = useState<string | null>(null)

  // Recording-scoped view state resets whenever the selected recording changes.
  // World Modeling is an always-open tab when its artifact exists, so load it without
  // requiring a second explorer action.
  useEffect(() => {
    setSegmentationOn(false)
    setSegmentationMaskLabel(null)
    setVideoState(initialVideoState())
    setWorldgenResults({})
    const worldgen = selectedRecording?.analyses.find((analysis) => analysis.key === 'worldgen')
    if (!worldgen || !bridge?.readWorldgen) return
    let cancelled = false
    bridge.readWorldgen(worldgen.path)
      .then((result) => {
        if (!cancelled) setWorldgenResults({ [result.id]: result })
      })
      .catch((err) => {
        if (!cancelled) setError(err instanceof Error ? err.message : 'Failed to load World Modeling.')
      })
    return () => {
      cancelled = true
    }
  }, [bridge, selectedRecording])

  useEffect(() => {
    setChatThread(null)
    setChatThreadError(null)
    setChatThreadLoading(false)
    if (!selectedRecording) return

    const genspark = selectedRecording.analyses.find((analysis) => analysis.key === 'genspark')
    const chat = selectedRecording.analyses.find((analysis) => analysis.key === 'chat')
    if (!genspark && !chat) return

    let cancelled = false
    let request: Promise<ChatThread> | null = null
    const gensparkFile = genspark ? browserFilesRef.current.get(genspark.path) : undefined
    const chatFile = chat ? browserFilesRef.current.get(chat.path) : undefined
    if (gensparkFile || chatFile) {
      request = readBrowserChatThread(gensparkFile, chatFile)
    } else if (bridge?.readChatThread) {
      request = bridge.readChatThread(selectedRecording.path)
    }
    if (!request) return

    setChatThreadLoading(true)
    request
      .then((thread) => {
        if (!cancelled) setChatThread(thread)
      })
      .catch((err) => {
        if (!cancelled) {
          setChatThreadError(err instanceof Error ? err.message : 'Failed to load the saved chat thread.')
        }
      })
      .finally(() => {
        if (!cancelled) setChatThreadLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [bridge, selectedRecording])

  const openSegmentationView = useCallback(() => {
    setTabRequest({
      requestId: requestId(),
      type: 'analysis',
      title: 'Segmentation',
      analysisKey: 'segmentation',
    })
  }, [])

  const selectedSegmentationArtifact = useCallback(
    () => selectedRecording?.analyses.find((analysis) => analysis.key === 'segmentation') ?? null,
    [selectedRecording],
  )

  const getSegmentationLabels = useCallback(async (): Promise<string[]> => {
    const seg = selectedSegmentationArtifact()
    if (!seg) return []
    const browserFile = browserFilesRef.current.get(seg.path)
    if (browserFile) return readBrowserSegmentationLabels(browserFile)
    if (bridge?.readSegmentationLabels) return bridge.readSegmentationLabels(seg.path)
    return []
  }, [bridge, selectedSegmentationArtifact])

  const runCommand = useCallback(
    async (text: string, onProgress?: (progress: CommandProgress) => void): Promise<CommandResult> => {
      const trimmed = text.trim()
      const worldgenCommand = parseWorldgenCommand(trimmed)
      if (worldgenCommand) {
        if (worldgenCommand.action === 'invalid') return { ok: false, message: worldgenCommand.message }
        if (!selectedRecording) return { ok: false, message: 'No recording is selected.' }
        if (!bridge?.runWorldgen) return { ok: false, message: 'World Modeling requires the native Electron app.' }
        if (selectedRecording.path.startsWith('browser://')) {
          return { ok: false, message: 'World Modeling requires a local filesystem recording, not browser preview mode.' }
        }

        const markers = videoState.markers
        const markerForRef = (ref: string) => markers.find((marker) => marker.reference.toLowerCase() === ref.toLowerCase())
        const startMarker = markerForRef(worldgenCommand.startRef)
        const endMarker = markerForRef(worldgenCommand.endRef)
        if (!startMarker || !endMarker) {
          const available = markers.length ? ` Available markers: ${markers.map((marker) => `@${marker.reference}`).join(', ')}` : ''
          const missing = [!startMarker ? `@${worldgenCommand.startRef}` : null, !endMarker ? `@${worldgenCommand.endRef}` : null]
            .filter(Boolean)
            .join(', ')
          return { ok: false, message: `Marker not found: ${missing}.${available}` }
        }
        if (startMarker.id === endMarker.id) {
          return { ok: false, message: 'World Modeling needs two different markers.' }
        }

        const worldgenRequestId = requestId()
        try {
          const result = await bridge.runWorldgen({
            requestId: worldgenRequestId,
            recordingPath: selectedRecording.path,
            markerStart: startMarker.reference,
            markerEnd: endMarker.reference,
            startFrameIndex: startMarker.frameIndex,
            endFrameIndex: endMarker.frameIndex,
          })
          setWorldgenResults({ [result.id]: result })
          setTabRequest({
            requestId: requestId(),
            type: 'worldgen',
            title: 'World Modeling',
            worldgenResultId: result.id,
          })
          const splat = result.splat
          const firstMessage = `VGGT point cloud ready: ${result.frameCount} frames, ${compactNumber(result.returnedPointCount)} rendered points. Saved ${shortPath(result.outputPath, 72)}.`
          onProgress?.({ ok: true, message: firstMessage })
          if (splat?.jobId && bridge.pollWorldgenSplat) {
            onProgress?.({ append: true, message: splatProgressMessage(splat) })
            let latestSplat = splat
            let pollFailures = 0
            for (;;) {
              await sleep(2500)
              let status
              try {
                status = await bridge.pollWorldgenSplat(splat.jobId)
                pollFailures = 0
              } catch (err) {
                pollFailures += 1
                const message = err instanceof Error ? err.message : 'status request failed'
                if (pollFailures >= 120) {
                  return { ok: false, message: `Gaussian splat status polling failed: ${message}` }
                }
                onProgress?.({ message: `Waiting for Gaussian splat status: ${message}` })
                continue
              }
              latestSplat = {
                ...latestSplat,
                status: status.status,
                stage: status.stage,
                message: status.message,
                progress: status.progress,
                currentStep: status.currentStep,
                maxSteps: status.maxSteps ?? latestSplat.maxSteps,
                gaussianCount: status.gaussianCount ?? latestSplat.gaussianCount,
                previewPointCount: status.previewPointCount ?? latestSplat.previewPointCount,
                elapsedSec: status.elapsedSec ?? latestSplat.elapsedSec,
                error: status.error ?? latestSplat.error,
                plyPath: status.plyPath ?? latestSplat.plyPath,
                previewJsonPath: status.previewJsonPath ?? latestSplat.previewJsonPath,
              }
              setWorldgenResults((current) => {
                const existing = current[result.id] ?? result
                return {
                  ...current,
                  [result.id]: {
                    ...existing,
                    splat: latestSplat,
                    splatPoints: status.points?.length ? status.points : existing.splatPoints,
                  },
                }
              })
              if (status.status === 'complete') {
                const persisted = bridge.saveWorldgenSplat
                  ? await bridge.saveWorldgenSplat(result.outputPath, { ...latestSplat, jobId: splat.jobId, points: status.points })
                  : null
                if (persisted) {
                  setWorldgenResults({ [persisted.id]: persisted })
                }
                const completeMessage = splatProgressMessage(latestSplat)
                onProgress?.({ ok: true, message: completeMessage })
                return { ok: true, message: completeMessage }
              }
              if (status.status === 'failed') {
                if (bridge.saveWorldgenSplat) {
                  await bridge.saveWorldgenSplat(result.outputPath, { ...latestSplat, jobId: splat.jobId, points: status.points })
                }
                const failedMessage = splatProgressMessage(latestSplat)
                onProgress?.({ ok: false, message: failedMessage })
                return { ok: false, message: failedMessage }
              }
              onProgress?.({ message: splatProgressMessage(latestSplat) })
            }
          }
          const splatMessage = splat ? ` ${splatProgressMessage(splat)}` : ''
          return {
            ok: true,
            message: `${firstMessage}${splatMessage}`,
          }
        } catch (err) {
          const message = err instanceof Error ? err.message : 'World Modeling failed.'
          return { ok: false, message }
        }
      }

      const command = parseSegmentationCommand(trimmed)
      if (!command) return { ok: false, message: `Unknown command: ${trimmed}` }
      if (command.action === 'invalid') return { ok: false, message: command.message }
      if (command.action === 'unknown') {
        return { ok: false, message: `Unknown segmentation command: /segmentation:${command.subcommand}` }
      }

      const seg = selectedSegmentationArtifact()
      if (command.action === 'unmask') {
        setSegmentationOn(false)
        setSegmentationMaskLabel(null)
        return { ok: true, message: 'Segmentation masks cleared.' }
      }
      if (!selectedRecording) return { ok: false, message: 'No recording is selected.' }
      if (!seg) return { ok: false, message: 'No segmentation artifact for this recording.' }

      if (command.action === 'list') {
        const labels = await getSegmentationLabels()
        return {
          ok: true,
          message: labels.length ? `Segmentation labels: ${labels.join(', ')}` : 'No segmentation labels are available.',
        }
      }

      if (command.action === 'mask') {
        const labels = await getSegmentationLabels()
        const target = normalizeSegmentationLabel(command.label)
        const firstTokenTarget = normalizeSegmentationLabel(command.label.split(/\s+/)[0] ?? '')
        const matchedLabel = labels.find((label) => normalizeSegmentationLabel(label) === target)
          ?? labels.find((label) => normalizeSegmentationLabel(label) === firstTokenTarget)
        if (!matchedLabel) {
          const suffix = labels.length ? ` Available labels: ${labels.join(', ')}` : ''
          return { ok: false, message: `Segmentation label not found: ${command.label}.${suffix}` }
        }
        setSegmentationOn(true)
        setSegmentationMaskLabel(matchedLabel)
        openSegmentationView()
        return { ok: true, message: `Showing only segmentation mask: ${matchedLabel}.` }
      }

      setSegmentationOn(true)
      setSegmentationMaskLabel(null)
      openSegmentationView()
      return { ok: true, message: 'Segmentation overlay enabled.' }
    },
    [bridge, getSegmentationLabels, openSegmentationView, selectedRecording, selectedSegmentationArtifact, videoState.markers],
  )

  // Segmentation masks for a video frame number, routed like getFrame. Fetching
  // is independent of the regular Video tab's optional overlay because the
  // dedicated Segmentation tab always renders masks.
  const getSegmentation = useCallback(
    async (frameNumber: number): Promise<SegMask[] | null> => {
      if (!selectedRecording) return null
      const seg = selectedRecording.analyses.find((analysis) => analysis.key === 'segmentation')
      if (!seg) return null
      const browserFile = browserFilesRef.current.get(seg.path)
      if (browserFile) return readBrowserSegmentationMasks(browserFile, frameNumber)
      if (bridge?.readSegmentationMasks) return bridge.readSegmentationMasks(seg.path, frameNumber)
      return null
    },
    [selectedRecording, bridge],
  )

  const overlay = useMemo<OverlayState>(
    () => ({ segmentation: segmentationOn, segmentationMaskLabel, runCommand, getSegmentation, getSegmentationLabels }),
    [segmentationOn, segmentationMaskLabel, runCommand, getSegmentation, getSegmentationLabels],
  )

  const status = useMemo(() => {
    if (loadingProject) return 'Scanning project'
    if (loadingSummary) return 'Reading recording'
    if (error) return error
    if (summary) {
      return `${compactNumber(summary.frameCount)} frames from ${summary.fileName}`
    }
    return bridgeAvailable ? 'Ready' : 'Ready in browser preview'
  }, [bridgeAvailable, error, loadingProject, loadingSummary, summary])

  return (
    <div className="app-shell">
      <TopMenu
        projectName={project?.name}
        loading={loadingProject || loadingSummary}
        runtimeLabel={bridgeAvailable ? 'Electron' : 'Browser'}
        onOpenProject={openProject}
        onOpenFiles={openFiles}
        onRescanProject={rescanProject}
      />

      <input
        ref={fileInputRef}
        className="native-file-input"
        type="file"
        accept=".pb,.vis.pb"
        multiple
        onChange={loadBrowserFiles}
      />
      <input
        ref={folderInputRef}
        className="native-file-input"
        type="file"
        multiple
        onChange={loadBrowserFiles}
        {...({ webkitdirectory: 'true' } as Record<string, string>)}
      />

      <main className="workbench">
        <ProjectExplorer
          project={project}
          selectedRecordingId={selectedRecording?.id}
          filter={filter}
          onFilterChange={setFilter}
          onOpenProject={openProject}
          onOpenFiles={openFiles}
          onSelectRecording={selectRecording}
        />

        <div className="editor-region">
          <AIChat
            selectedRecording={selectedRecording}
            summary={summary}
            markers={videoState.markers}
            savedThread={chatThread}
            savedThreadLoading={chatThreadLoading}
            savedThreadError={chatThreadError}
            onRunCommand={runCommand}
          />
          <SplitWorkspace
            selectedRecording={selectedRecording}
            summary={summary}
            tabRequest={tabRequest}
            getFrame={getFrame}
            getSensorData={getSensorData}
            getIdoSlamData={getIdoSlamData}
            overlay={overlay}
            videoState={videoState}
            onVideoStateChange={setVideoState}
            worldgenResults={worldgenResults}
          />
        </div>
      </main>

      <footer className="status-bar">
        <span>{status}</span>
        <span>{selectedRecording ? shortPath(selectedRecording.path, 92) : 'No recording selected'}</span>
      </footer>
    </div>
  )
}
