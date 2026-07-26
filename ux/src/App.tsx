import { ChangeEvent, useCallback, useEffect, useMemo, useRef, useState } from 'react'
import AIChat from './components/AIChat'
import ProjectExplorer from './components/ProjectExplorer'
import SplitWorkspace from './components/SplitWorkspace'
import TopMenu from './components/TopMenu'
import type {
  AgentChatResult,
  ChatThread,
  ControlDevicePreset,
  IdoSlamSummary,
  MotionCaptureOverlay,
  ProjectScanResult,
  RecordingEntry,
  RunnerBackgroundJob,
  SavedChatTurn,
  SegMask,
  SensorDataSummary,
  VideoChatWorkspace,
  VideoMarker,
  VideoPlaybackState,
  VisSummary,
  WorkspaceChatMessage,
  WorkspaceChatSession,
  WorkspaceTabRequest,
  WindowAction,
  WorldgenResult,
} from './types'
import {
  readBrowserChatThread,
  readBrowserIdoSlam,
  readBrowserMotionCapture,
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
import { recordingDisplayName, recordingVideoId } from './lib/recordingNames'

const LAST_PROJECT_KEY = 'bayesmech:lastProject'
const PROJECT_PATHS_KEY = 'bayesmech:projectPaths'
const BROWSER_CHAT_WORKSPACE_KEY = 'bayesmech:chat-workspace'

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

function backgroundJobTimestamp(job: RunnerBackgroundJob): number {
  const value = job.updatedAt || job.createdAt || 0
  if (typeof value === 'number') return value < 1_000_000_000_000 ? value * 1000 : value
  const parsed = new Date(value).getTime()
  return Number.isFinite(parsed) ? parsed : 0
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

function newChatId() {
  return `chat-${new Date().toISOString().replace(/\D/g, '').slice(0, 17)}-${Math.random().toString(36).slice(2, 7)}`
}

function initialChatTitle(createdAt: string) {
  return new Date(createdAt).toLocaleString(undefined, {
    dateStyle: 'medium',
    timeStyle: 'short',
  })
}

function legacyMessages(turns: SavedChatTurn[]): WorkspaceChatMessage[] {
  return turns.map((turn, index) => {
    const timestampMs = Math.trunc(Number(turn.timestampNs) / 1e6)
    return {
      id: `legacy-${index + 1}`,
      role: turn.role,
      text: turn.text,
      createdAt: Number.isFinite(timestampMs) && timestampMs > 0
        ? new Date(timestampMs).toISOString()
        : new Date().toISOString(),
    }
  })
}

function createBrowserWorkspace(
  recording: RecordingEntry,
  turns: SavedChatTurn[] = [],
): VideoChatWorkspace {
  const createdAt = new Date().toISOString()
  const chat: WorkspaceChatSession = {
    id: newChatId(),
    title: initialChatTitle(createdAt),
    createdAt,
    updatedAt: createdAt,
    messages: legacyMessages(turns),
    markers: [],
  }
  return {
    version: 1,
    videoId: recordingVideoId(recording),
    recordingPath: recording.path,
    activeChatId: chat.id,
    chats: [chat],
  }
}

function browserWorkspaceKey(videoId: string) {
  return `${BROWSER_CHAT_WORKSPACE_KEY}:${videoId}`
}

function markersEqual(left: VideoMarker[], right: VideoMarker[]) {
  if (left.length !== right.length) return false
  return left.every((marker, index) => {
    const other = right[index]
    return other
      && marker.id === other.id
      && marker.name === other.name
      && marker.reference === other.reference
      && marker.frameIndex === other.frameIndex
      && marker.frameNumber === other.frameNumber
      && marker.seconds === other.seconds
      && marker.color === other.color
  })
}

function mergeProjectScanResults(results: ProjectScanResult[]): ProjectScanResult {
  const recordingsByPath = new Map<string, RecordingEntry>()
  for (const result of results) {
    for (const recording of result.recordings) {
      recordingsByPath.set(recording.path, { ...recording, id: recording.path })
    }
  }
  const recordings = [...recordingsByPath.values()]
  const sourceResults = results.filter((result) => result.recordings.length > 0)
  const errors = results.map((result) => result.error).filter(Boolean)
  return {
    rootPath: sourceResults.length === 1 ? sourceResults[0].rootPath : 'workspace://videos',
    name: recordings.length === 1 ? sourceResults[0]?.name ?? 'Video Workspace' : `${recordings.length} videos`,
    recordings,
    error: recordings.length ? undefined : errors[0] ?? 'No .vis.pb recordings are loaded.',
  }
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
  const [addingDevice, setAddingDevice] = useState(false)
  const [loadingSummary, setLoadingSummary] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [tabRequest, setTabRequest] = useState<WorkspaceTabRequest | null>(null)
  const [videoState, setVideoState] = useState<VideoPlaybackState>(() => initialVideoState())
  const [worldgenResults, setWorldgenResults] = useState<Record<string, WorldgenResult>>({})
  const [backgroundJobs, setBackgroundJobs] = useState<Record<string, RunnerBackgroundJob>>({})
  const [chatThread, setChatThread] = useState<ChatThread | null>(null)
  const [chatWorkspaces, setChatWorkspaces] = useState<Record<string, VideoChatWorkspace>>({})
  const [selectedChatId, setSelectedChatId] = useState<string | null>(null)
  const [chatThreadLoading, setChatThreadLoading] = useState(false)
  const [chatThreadError, setChatThreadError] = useState<string | null>(null)
  const selectedRecordingRef = useRef<RecordingEntry | null>(null)
  const projectRef = useRef<ProjectScanResult | null>(null)
  const projectRootsRef = useRef<string[]>([])
  const restoredProjectsRef = useRef(false)
  const chatSaveQueueRef = useRef<Promise<unknown>>(Promise.resolve())

  useEffect(() => {
    if (!bridge?.readRunnerBackgroundJobs || !bridge.onRunnerJobState) return
    let cancelled = false
    bridge.readRunnerBackgroundJobs()
      .then((jobs) => {
        if (cancelled) return
        setBackgroundJobs((current) => {
          const next = { ...current }
          for (const job of jobs) {
            const previous = next[job.jobId]
            if (!previous || backgroundJobTimestamp(job) >= backgroundJobTimestamp(previous)) {
              next[job.jobId] = job
            }
          }
          return next
        })
      })
      .catch(() => {
        // The runner may be offline while the user works with local recordings.
      })
    const unsubscribe = bridge.onRunnerJobState((job) => {
      if (cancelled || !job.jobId) return
      setBackgroundJobs((current) => {
        const previous = current[job.jobId]
        if (
          previous
          && backgroundJobTimestamp(previous) > backgroundJobTimestamp(job)
        ) return current
        return { ...current, [job.jobId]: job }
      })
    })
    return () => {
      cancelled = true
      unsubscribe()
    }
  }, [bridge])

  const loadRecordingWorkspace = useCallback(
    async (recording: RecordingEntry): Promise<VideoChatWorkspace> => {
      const videoId = recordingVideoId(recording)
      if (bridge?.loadChatWorkspace) {
        return bridge.loadChatWorkspace(videoId, recording.path)
      }

      const storageKey = browserWorkspaceKey(videoId)
      try {
        const saved = JSON.parse(localStorage.getItem(storageKey) || 'null') as VideoChatWorkspace | null
        if (saved?.version === 1 && saved.chats?.length) return saved
      } catch {
        localStorage.removeItem(storageKey)
      }

      const genspark = recording.analyses.find((analysis) => analysis.key === 'genspark')
      const chat = recording.analyses.find((analysis) => analysis.key === 'chat')
      const gensparkFile = genspark ? browserFilesRef.current.get(genspark.path) : undefined
      const chatFile = chat ? browserFilesRef.current.get(chat.path) : undefined
      const thread = gensparkFile || chatFile
        ? await readBrowserChatThread(gensparkFile, chatFile)
        : null
      const workspace = createBrowserWorkspace(recording, thread?.turns)
      localStorage.setItem(storageKey, JSON.stringify(workspace))
      return workspace
    },
    [bridge],
  )

  const selectRecording = useCallback(
    async (
      recording: RecordingEntry,
      preferredChatId?: string,
      knownWorkspace?: VideoChatWorkspace,
    ) => {
      const previousRecordingId = selectedRecordingRef.current?.id
      selectedRecordingRef.current = recording
      setSelectedRecording(recording)
      setSummary(null)
      setLoadingSummary(true)
      setError(null)
      setChatThreadLoading(true)
      let workspace = knownWorkspace
      try {
        workspace = workspace ?? await loadRecordingWorkspace(recording)
        const resolvedWorkspace = workspace
        const videoId = recordingVideoId(recording)
        setChatWorkspaces((current) => ({ ...current, [videoId]: resolvedWorkspace }))
        const session = resolvedWorkspace.chats.find((chat) => chat.id === preferredChatId)
          ?? resolvedWorkspace.chats.find((chat) => chat.id === resolvedWorkspace.activeChatId)
          ?? resolvedWorkspace.chats[0]
        setSelectedChatId(session?.id ?? null)
        setVideoState((current) => previousRecordingId === recording.id
          ? { ...current, playing: false, markers: session?.markers ?? [] }
          : { ...initialVideoState(), markers: session?.markers ?? [] })
        if (session) {
          if (bridge?.setActiveChatSession) {
            void bridge.setActiveChatSession(videoId, recording.path, session.id)
          } else {
            localStorage.setItem(
              browserWorkspaceKey(videoId),
              JSON.stringify({ ...resolvedWorkspace, activeChatId: session.id }),
            )
          }
        }

        const browserFile = browserFilesRef.current.get(recording.path)
        const nextSummary = browserFile
          ? await readBrowserVisSummary(recording, browserFile)
          : await bridge?.readVisSummary(recording.path)
        if (!nextSummary) throw new Error('No recording reader is available.')
        setSummary(nextSummary)
      } catch (err) {
        const message = err instanceof Error ? err.message : 'Failed to read .vis.pb summary.'
        setError(message)
        setChatThreadError(message)
      } finally {
        setLoadingSummary(false)
        setChatThreadLoading(false)
      }
    },
    [bridge, loadRecordingWorkspace],
  )

  const applyProject = useCallback(
    async (
      nextProject: ProjectScanResult,
      options: { replace?: boolean; remember?: boolean } = {},
    ) => {
      const replace = options.replace ?? false
      const remember = options.remember ?? true
      const combinedProject = mergeProjectScanResults(
        replace || !projectRef.current
          ? [nextProject]
          : [projectRef.current, nextProject],
      )
      projectRef.current = combinedProject
      setProject(combinedProject)
      if (
        remember
        && nextProject.rootPath
        && !nextProject.rootPath.startsWith('browser://')
        && !nextProject.rootPath.startsWith('workspace://')
      ) {
        const roots = [...new Set([...projectRootsRef.current, nextProject.rootPath])]
        projectRootsRef.current = roots
        localStorage.setItem(PROJECT_PATHS_KEY, JSON.stringify(roots))
        localStorage.setItem(LAST_PROJECT_KEY, nextProject.rootPath)
      }
      if (nextProject.error) setError(nextProject.error)
      const loadedWorkspaces: Record<string, VideoChatWorkspace> = {}
      const workspaceResults = await Promise.allSettled(
        nextProject.recordings.map(async (recording) => {
          const workspace = await loadRecordingWorkspace(recording)
          loadedWorkspaces[recordingVideoId(recording)] = workspace
        }),
      )
      setChatWorkspaces((current) => (replace ? loadedWorkspaces : { ...current, ...loadedWorkspaces }))
      const firstFailure = workspaceResults.find((result) => result.status === 'rejected')
      if (firstFailure?.status === 'rejected') {
        setChatThreadError(
          firstFailure.reason instanceof Error
            ? firstFailure.reason.message
            : 'Failed to load a saved chat workspace.',
        )
      }
      const firstRecording = nextProject.recordings[0]
        ? { ...nextProject.recordings[0], id: nextProject.recordings[0].path }
        : null
      if (firstRecording) {
        const workspace = loadedWorkspaces[recordingVideoId(firstRecording)]
        await selectRecording(firstRecording, workspace?.activeChatId, workspace)
      } else if (combinedProject.recordings.length === 0) {
        selectedRecordingRef.current = null
        setSelectedRecording(null)
        setSelectedChatId(null)
        setSummary(null)
      }
    },
    [loadRecordingWorkspace, selectRecording],
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
      await applyProject(response)
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Failed to open project.'
      setError(message)
    } finally {
      setLoadingProject(false)
    }
  }, [applyProject, bridge])

  const createProject = useCallback(async () => {
    if (!bridge?.createProject) {
      const message = bridge
        ? 'The desktop app was updated while it was running. Restart BayesMech Vision once, then create the project again.'
        : 'Creating projects requires the BayesMech Vision desktop app.'
      setError(message)
      window.alert(message)
      return
    }
    setLoadingProject(true)
    setError(null)
    try {
      const created = await bridge.createProject()
      await applyProject(created)
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Failed to create the project.'
      setError(message)
      window.alert(`Could not create the project: ${message}`)
    } finally {
      setLoadingProject(false)
    }
  }, [applyProject, bridge])

  const addDeviceToProject = useCallback(async (preset: ControlDevicePreset) => {
    const recording = selectedRecordingRef.current
    if (!recording) {
      window.alert('Open or create a project before adding a device.')
      return
    }
    if (!bridge?.addDeviceToProject) {
      const message = bridge
        ? 'The desktop app was updated while it was running. Restart BayesMech Vision once, then add the device again.'
        : 'Adding devices requires the BayesMech Vision desktop app.'
      setError(message)
      window.alert(message)
      return
    }

    setAddingDevice(true)
    setError(null)
    try {
      const updated = await bridge.addDeviceToProject(recording.path, preset)
      if (bridge.ensureControlService) {
        void bridge.ensureControlService().catch(() => {
          // The Control tab reports the service state if startup is still pending.
        })
      }
      const updatedRecordings = updated.recordings.map((item) => ({ ...item, id: item.path }))
      const replacement = updatedRecordings.find((item) => item.path === recording.path)
        ?? updatedRecordings.find((item) => item.controlProject)
      if (!replacement) throw new Error('The updated control project has no recording.')

      const currentProject = projectRef.current
      const nextProject = currentProject
        ? {
            ...currentProject,
            recordings: [
              ...currentProject.recordings.filter(
                (item) => item.directoryPath !== recording.directoryPath,
              ),
              ...updatedRecordings,
            ],
            error: undefined,
          }
        : { ...updated, recordings: updatedRecordings }
      projectRef.current = nextProject
      setProject(nextProject)

      const videoId = recordingVideoId(replacement)
      const workspace = chatWorkspaces[videoId] ?? await loadRecordingWorkspace(replacement)
      setChatWorkspaces((current) => ({ ...current, [videoId]: workspace }))
      await selectRecording(replacement, selectedChatId ?? workspace.activeChatId, workspace)
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Failed to add the device.'
      setError(message)
      window.alert(`Could not add the device: ${message}`)
    } finally {
      setAddingDevice(false)
    }
  }, [
    bridge,
    chatWorkspaces,
    loadRecordingWorkspace,
    selectRecording,
    selectedChatId,
  ])

  const renameProject = useCallback(async (recording: RecordingEntry, displayName: string) => {
    if (!bridge?.renameProject) {
      const message = bridge
        ? 'The desktop app was updated while it was running. Restart BayesMech Vision once, then rename the project again.'
        : 'Renaming projects requires the BayesMech Vision desktop app.'
      setError(message)
      window.alert(message)
      return
    }
    setError(null)
    try {
      const updated = await bridge.renameProject(recording.path, displayName)
      const updatedRecordings = updated.recordings.map((item) => ({ ...item, id: item.path }))
      const replacement = updatedRecordings.find((item) => item.path === recording.path)
        ?? updatedRecordings.find((item) => item.controlProject)
      if (!replacement) throw new Error('The renamed project has no recording.')

      const currentProject = projectRef.current
      const nextProject = currentProject
        ? {
            ...currentProject,
            recordings: [
              ...currentProject.recordings.filter(
                (item) => item.directoryPath !== recording.directoryPath,
              ),
              ...updatedRecordings,
            ],
            error: undefined,
          }
        : { ...updated, recordings: updatedRecordings }
      projectRef.current = nextProject
      setProject(nextProject)
      if (selectedRecordingRef.current?.path === recording.path) {
        selectedRecordingRef.current = replacement
        setSelectedRecording(replacement)
      }
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Failed to rename the project.'
      setError(message)
      window.alert(`Could not rename the project: ${message}`)
    }
  }, [bridge])

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
      await applyProject(response, { remember: false })
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Failed to open .vis.pb files.'
      setError(message)
    } finally {
      setLoadingProject(false)
    }
  }, [applyProject, bridge])

  const rescanProject = useCallback(async () => {
    if (!project) return
    setLoadingProject(true)
    setError(null)
    try {
      if (!bridge) {
        const scanned = scanBrowserFiles(browserSourceFiles)
        browserFilesRef.current = scanned.filesByPath
        await applyProject(scanned.project, { replace: true, remember: false })
      } else {
        const roots = projectRootsRef.current
        if (!roots.length) return
        const scans = await Promise.all(roots.map((rootPath) => bridge.scanProject(rootPath)))
        await applyProject(mergeProjectScanResults(scans), { replace: true, remember: false })
      }
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Failed to rescan project.'
      setError(message)
    } finally {
      setLoadingProject(false)
    }
  }, [applyProject, bridge, browserSourceFiles, project])

  useEffect(() => {
    if (!bridge || restoredProjectsRef.current) return
    restoredProjectsRef.current = true
    let projectPaths: string[] = []
    try {
      const parsed = JSON.parse(localStorage.getItem(PROJECT_PATHS_KEY) || '[]')
      if (Array.isArray(parsed)) projectPaths = parsed.filter((item): item is string => typeof item === 'string')
    } catch {
      localStorage.removeItem(PROJECT_PATHS_KEY)
    }
    const lastProject = localStorage.getItem(LAST_PROJECT_KEY)
    if (!projectPaths.length && lastProject) projectPaths = [lastProject]
    projectPaths = [...new Set(projectPaths)]
    if (!projectPaths.length) return
    projectRootsRef.current = projectPaths

    setLoadingProject(true)
    Promise.allSettled(projectPaths.map((projectPath) => bridge.scanProject(projectPath)))
      .then((results) => {
        const scans = results
          .filter((result): result is PromiseFulfilledResult<ProjectScanResult> => result.status === 'fulfilled')
          .map((result) => result.value)
        if (scans.length) void applyProject(mergeProjectScanResults(scans), { replace: true, remember: false })
      })
      .catch(() => {
        localStorage.removeItem(LAST_PROJECT_KEY)
        localStorage.removeItem(PROJECT_PATHS_KEY)
      })
      .finally(() => {
        setLoadingProject(false)
      })
  }, [applyProject, bridge])

  useEffect(() => {
    const controlProject = selectedRecording?.controlProject
    if (!bridge?.scanProject || !controlProject) return
    let cancelled = false
    let requestInFlight = false
    const refreshDeviceStreams = async () => {
      if (requestInFlight) return
      requestInFlight = true
      try {
        const scanned = await bridge.scanProject(controlProject.directoryPath)
        if (cancelled) return
        const next = scanned.recordings.find(
          (recording) => recording.controlProject?.projectId === controlProject.projectId,
        )
        if (!next) return
        const previousSignature = selectedRecording.analyses
          .map((analysis) => `${analysis.key}:${analysis.path}`)
          .concat(
            selectedRecording.controlProject?.devices.map((device) => (
              `${device.deviceId}:${device.enabled}:${device.controlHost}:${device.controlPort}:${device.streamHost}:${device.streamPort}`
            )) ?? [],
          )
          .join('|')
        const nextSignature = next.analyses
          .map((analysis) => `${analysis.key}:${analysis.path}`)
          .concat(
            next.controlProject?.devices.map((device) => (
              `${device.deviceId}:${device.enabled}:${device.controlHost}:${device.controlPort}:${device.streamHost}:${device.streamPort}`
            )) ?? [],
          )
          .join('|')
        if (previousSignature === nextSignature) return
        const nextRecording = { ...next, id: selectedRecording.id }
        selectedRecordingRef.current = nextRecording
        setSelectedRecording(nextRecording)
        setProject((current) => {
          if (!current) return current
          const updated = {
            ...current,
            recordings: current.recordings.map((recording) => (
              recording.id === selectedRecording.id ? nextRecording : recording
            )),
          }
          projectRef.current = updated
          return updated
        })
      } catch {
        // Stream files are created lazily; a transient scan failure is retried.
      } finally {
        requestInFlight = false
      }
    }
    const timer = window.setInterval(() => void refreshDeviceStreams(), 1500)
    void refreshDeviceStreams()
    return () => {
      cancelled = true
      window.clearInterval(timer)
    }
  }, [bridge, selectedRecording])

  const loadBrowserFiles = async (event: ChangeEvent<HTMLInputElement>) => {
    const files = event.currentTarget.files
    if (!files?.length) return

    setLoadingProject(true)
    setError(null)
    try {
      const sourceFiles = Array.from(files)
      const mergedFiles = [...browserSourceFiles]
      const knownFiles = new Set(mergedFiles.map((file) => `${file.webkitRelativePath || file.name}:${file.size}:${file.lastModified}`))
      for (const file of sourceFiles) {
        const key = `${file.webkitRelativePath || file.name}:${file.size}:${file.lastModified}`
        if (!knownFiles.has(key)) {
          knownFiles.add(key)
          mergedFiles.push(file)
        }
      }
      const scanned = scanBrowserFiles(mergedFiles)
      setBrowserSourceFiles(mergedFiles)
      browserFilesRef.current = scanned.filesByPath
      await applyProject(scanned.project, { replace: true, remember: false })
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Failed to load selected .vis.pb files.'
      setError(message)
    } finally {
      setLoadingProject(false)
      event.currentTarget.value = ''
    }
  }

  const activeWorkspace = useMemo(() => (
    selectedRecording
      ? chatWorkspaces[recordingVideoId(selectedRecording)] ?? null
      : null
  ), [chatWorkspaces, selectedRecording])
  const activeChat = useMemo(() => (
    activeWorkspace?.chats.find((chat) => chat.id === selectedChatId) ?? null
  ), [activeWorkspace, selectedChatId])

  const persistChatSession = useCallback(
    (recording: RecordingEntry, session: WorkspaceChatSession) => {
      const videoId = recordingVideoId(recording)
      const savedSession = {
        ...session,
        updatedAt: new Date().toISOString(),
      }
      setChatWorkspaces((current) => {
        const workspace = current[videoId]
        if (!workspace) return current
        const chats = workspace.chats.some((chat) => chat.id === savedSession.id)
          ? workspace.chats.map((chat) => (chat.id === savedSession.id ? savedSession : chat))
          : [...workspace.chats, savedSession]
        const nextWorkspace = { ...workspace, chats }
        if (!bridge?.saveChatSession) {
          localStorage.setItem(browserWorkspaceKey(videoId), JSON.stringify(nextWorkspace))
        }
        return { ...current, [videoId]: nextWorkspace }
      })
      if (bridge?.saveChatSession) {
        chatSaveQueueRef.current = chatSaveQueueRef.current
          .catch(() => undefined)
          .then(() => bridge.saveChatSession(videoId, recording.path, savedSession))
          .catch((err) => {
            setChatThreadError(err instanceof Error ? err.message : 'Failed to save the chat session.')
          })
      }
    },
    [bridge],
  )

  const selectChat = useCallback(
    (recording: RecordingEntry, chat: WorkspaceChatSession) => {
      const videoId = recordingVideoId(recording)
      const workspace = chatWorkspaces[videoId]
      if (selectedRecordingRef.current?.id !== recording.id) {
        void selectRecording(recording, chat.id, workspace)
        return
      }
      setSelectedChatId(chat.id)
      setVideoState((current) => ({ ...current, playing: false, markers: chat.markers }))
      setChatWorkspaces((current) => {
        const currentWorkspace = current[videoId]
        if (!currentWorkspace) return current
        const nextWorkspace = { ...currentWorkspace, activeChatId: chat.id }
        if (!bridge?.setActiveChatSession) {
          localStorage.setItem(browserWorkspaceKey(videoId), JSON.stringify(nextWorkspace))
        }
        return { ...current, [videoId]: nextWorkspace }
      })
      if (bridge?.setActiveChatSession) {
        void bridge.setActiveChatSession(videoId, recording.path, chat.id)
      }
    },
    [bridge, chatWorkspaces, selectRecording],
  )

  const createChat = useCallback(
    async (recording: RecordingEntry) => {
      const videoId = recordingVideoId(recording)
      setChatThreadLoading(true)
      setChatThreadError(null)
      try {
        let workspace: VideoChatWorkspace
        if (bridge?.createChatSession) {
          workspace = await bridge.createChatSession(videoId, recording.path)
        } else {
          const current = chatWorkspaces[videoId] ?? createBrowserWorkspace(recording)
          const createdAt = new Date().toISOString()
          const chat: WorkspaceChatSession = {
            id: newChatId(),
            title: initialChatTitle(createdAt),
            createdAt,
            updatedAt: createdAt,
            messages: [],
            markers: [],
          }
          workspace = {
            ...current,
            activeChatId: chat.id,
            chats: [...current.chats, chat],
          }
          localStorage.setItem(browserWorkspaceKey(videoId), JSON.stringify(workspace))
        }
        setChatWorkspaces((current) => ({ ...current, [videoId]: workspace }))
        const chat = workspace.chats.find((item) => item.id === workspace.activeChatId)
          ?? workspace.chats.at(-1)
        if (!chat) return
        if (selectedRecordingRef.current?.id === recording.id) {
          setSelectedChatId(chat.id)
          setVideoState((current) => ({ ...current, playing: false, markers: [] }))
        } else {
          await selectRecording(recording, chat.id, workspace)
        }
      } catch (err) {
        setChatThreadError(err instanceof Error ? err.message : 'Failed to create a new chat.')
      } finally {
        setChatThreadLoading(false)
      }
    },
    [bridge, chatWorkspaces, selectRecording],
  )

  const renameChat = useCallback(
    (recording: RecordingEntry, chat: WorkspaceChatSession, title: string) => {
      persistChatSession(recording, { ...chat, title })
    },
    [persistChatSession],
  )

  const deleteChat = useCallback(
    async (recording: RecordingEntry, chat: WorkspaceChatSession) => {
      const videoId = recordingVideoId(recording)
      const selected = selectedRecordingRef.current?.id === recording.id
      if (selected) {
        setChatThreadLoading(true)
        setChatThreadError(null)
      }
      try {
        await chatSaveQueueRef.current.catch(() => undefined)
        let workspace: VideoChatWorkspace
        if (bridge?.deleteChatSession) {
          workspace = await bridge.deleteChatSession(videoId, recording.path, chat.id)
        } else {
          const current = chatWorkspaces[videoId]
          if (!current) throw new Error('The chat workspace is not loaded.')
          const deleteIndex = current.chats.findIndex((item) => item.id === chat.id)
          if (deleteIndex < 0) throw new Error('Chat not found.')
          const chats = current.chats.filter((item) => item.id !== chat.id)
          const activeChatId = current.activeChatId === chat.id
            ? chats[Math.min(deleteIndex, chats.length - 1)]?.id ?? ''
            : current.activeChatId
          workspace = { ...current, activeChatId, chats }
          localStorage.setItem(browserWorkspaceKey(videoId), JSON.stringify(workspace))
        }
        setChatWorkspaces((current) => ({ ...current, [videoId]: workspace }))
        if (selected) {
          const nextChat = workspace.chats.find((item) => item.id === workspace.activeChatId)
            ?? workspace.chats[0]
            ?? null
          setSelectedChatId(nextChat?.id ?? null)
          setVideoState((current) => ({
            ...current,
            playing: false,
            markers: nextChat?.markers ?? [],
          }))
        }
      } catch (err) {
        const message = err instanceof Error ? err.message : 'Failed to delete the chat.'
        setChatThreadError(message)
        window.alert(`Could not delete the chat: ${message}`)
      } finally {
        if (selected) setChatThreadLoading(false)
      }
    },
    [bridge, chatWorkspaces],
  )

  const saveActiveMessages = useCallback(
    (messages: WorkspaceChatMessage[]) => {
      if (!selectedRecording || !activeChat) return
      const recording = selectedRecording
      const videoId = recordingVideoId(recording)
      const chatId = activeChat.id
      setChatWorkspaces((current) => {
        const workspace = current[videoId]
        const currentChat = workspace?.chats.find((chat) => chat.id === chatId)
        if (!workspace || !currentChat) return current
        const savedSession = {
          ...currentChat,
          messages,
          updatedAt: new Date().toISOString(),
        }
        const nextWorkspace = {
          ...workspace,
          chats: workspace.chats.map((chat) => (chat.id === chatId ? savedSession : chat)),
        }
        if (bridge?.saveChatSession) {
          chatSaveQueueRef.current = chatSaveQueueRef.current
            .catch(() => undefined)
            .then(() => bridge.saveChatSession(videoId, recording.path, savedSession))
            .catch((err) => {
              setChatThreadError(err instanceof Error ? err.message : 'Failed to save chat messages.')
            })
        } else {
          localStorage.setItem(browserWorkspaceKey(videoId), JSON.stringify(nextWorkspace))
        }
        return { ...current, [videoId]: nextWorkspace }
      })
    },
    [activeChat, bridge, selectedRecording],
  )

  const sendChatMessage = useCallback(
    async (
      message: string,
      history: WorkspaceChatMessage[],
    ): Promise<AgentChatResult> => {
      if (!selectedRecording || !activeChat) {
        throw new Error('Select a recording and chat before sending a message.')
      }
      if (!bridge?.sendAgentMessage) {
        throw new Error('Gemma video chat requires the native Electron app.')
      }
      if (selectedRecording.path.startsWith('browser://')) {
        throw new Error('Gemma video chat requires a local filesystem recording.')
      }
      return bridge.sendAgentMessage({
        requestId: requestId(),
        recordingPath: selectedRecording.path,
        chatId: activeChat.id,
        message,
        history: history
          .filter((item) => item.role === 'user' || item.role === 'assistant')
          .map((item) => ({ role: item.role, text: item.text })),
      })
    },
    [activeChat, bridge, selectedRecording],
  )

  const closeRecording = useCallback(
    (recording: RecordingEntry) => {
      if (!project) return
      const closingIndex = project.recordings.findIndex((item) => item.id === recording.id)
      const remaining = project.recordings.filter((item) => item.id !== recording.id)
      const nextProject = mergeProjectScanResults([{ ...project, recordings: remaining }])
      projectRef.current = nextProject
      setProject(nextProject)
      const videoId = recordingVideoId(recording)
      setChatWorkspaces((current) => {
        const next = { ...current }
        delete next[videoId]
        return next
      })
      if (selectedRecordingRef.current?.id !== recording.id) return
      const nextRecording = remaining[Math.min(Math.max(closingIndex, 0), remaining.length - 1)]
      if (nextRecording) {
        const nextWorkspace = chatWorkspaces[recordingVideoId(nextRecording)]
        void selectRecording(nextRecording, nextWorkspace?.activeChatId, nextWorkspace)
      } else {
        selectedRecordingRef.current = null
        setSelectedRecording(null)
        setSelectedChatId(null)
        setSummary(null)
        setChatThread(null)
        setVideoState(initialVideoState())
      }
    },
    [chatWorkspaces, project, selectRecording],
  )

  const performWindowAction = useCallback(
    (action: WindowAction) => {
      if (bridge?.performWindowAction) {
        void bridge.performWindowAction(action)
        return
      }
      if (action === 'reload') {
        window.location.reload()
      } else if (action === 'toggle-fullscreen') {
        if (document.fullscreenElement) void document.exitFullscreen()
        else void document.documentElement.requestFullscreen()
      } else if (action === 'close') {
        window.close()
      }
    },
    [bridge],
  )

  useEffect(() => {
    if (!selectedRecording || !activeChat) return
    if (markersEqual(activeChat.markers, videoState.markers)) return
    persistChatSession(selectedRecording, { ...activeChat, markers: videoState.markers })
  }, [activeChat, persistChatSession, selectedRecording, videoState.markers])

  // Random-access frame fetch for the video player: routes to the in-browser
  // decoder (no-bridge mode) or the Electron main process for the selected file.
  const getFrame = useCallback<FrameGetter>(
    async (index, sourcePath) => {
      const recording = selectedRecording
      if (!recording) return null
      const recordingPath = sourcePath || recording.path
      const browserFile = browserFilesRef.current.get(recordingPath)
      if (browserFile) return readBrowserVisFrame(browserFile, index)
      if (bridge?.readVisFrame) return bridge.readVisFrame(recordingPath, index)
      return null
    },
    [selectedRecording, bridge],
  )

  const getVisSummary = useCallback(async (sourcePath: string): Promise<VisSummary | null> => {
    const recording = selectedRecording
    if (!recording) return null
    const browserFile = browserFilesRef.current.get(sourcePath)
    if (browserFile) {
      const sourceAnalysis = recording.analyses.find((analysis) => analysis.path === sourcePath)
      return readBrowserVisSummary(
        {
          ...recording,
          path: sourcePath,
          fileStem: sourceAnalysis?.title ?? recording.fileStem,
        },
        browserFile,
      )
    }
    if (bridge?.readVisSummary) return bridge.readVisSummary(sourcePath)
    return null
  }, [bridge, selectedRecording])

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

  const getMotionCapture = useCallback(
    async (frameNumber: number): Promise<MotionCaptureOverlay | null> => {
      const motionCapture = selectedRecording?.analyses.find((analysis) => analysis.key === 'motioncap')
      if (!motionCapture) return null
      const browserFile = browserFilesRef.current.get(motionCapture.path)
      if (browserFile) return readBrowserMotionCapture(browserFile, frameNumber)
      if (bridge?.readMotionCapture) return bridge.readMotionCapture(motionCapture.path, frameNumber)
      return null
    },
    [bridge, selectedRecording],
  )

  const overlay = useMemo<OverlayState>(
    () => ({
      segmentation: segmentationOn,
      segmentationMaskLabel,
      runCommand,
      getSegmentation,
      getSegmentationLabels,
      getMotionCapture,
    }),
    [
      segmentationOn,
      segmentationMaskLabel,
      runCommand,
      getSegmentation,
      getSegmentationLabels,
      getMotionCapture,
    ],
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
        projectName={
          project?.recordings.length === 1
            ? recordingDisplayName(project.recordings[0])
            : project?.name
        }
        loading={loadingProject || loadingSummary}
        onOpenProject={openProject}
        onOpenFiles={openFiles}
        onRescanProject={rescanProject}
        onWindowAction={performWindowAction}
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
          selectedChatId={selectedChatId ?? undefined}
          chatWorkspaces={chatWorkspaces}
          filter={filter}
          onFilterChange={setFilter}
          onOpenProject={openProject}
          onOpenFiles={openFiles}
          onCreateProject={createProject}
          creatingProject={loadingProject}
          onSelectRecording={selectRecording}
          onRenameProject={renameProject}
          onSelectChat={selectChat}
          onCreateChat={createChat}
          onRenameChat={renameChat}
          onDeleteChat={deleteChat}
          onCloseRecording={closeRecording}
        />

        <div className="editor-region">
          <AIChat
            selectedRecording={selectedRecording}
            summary={summary}
            markers={videoState.markers}
            analysis={chatThread?.analysis ?? null}
            chatSession={activeChat}
            chatLoading={chatThreadLoading}
            chatError={chatThreadError}
            backgroundJobs={Object.values(backgroundJobs)}
            addingDevice={addingDevice}
            onAddDevice={addDeviceToProject}
            onMessagesChange={saveActiveMessages}
            onSendMessage={sendChatMessage}
            onRunCommand={runCommand}
          />
          <SplitWorkspace
            selectedRecording={selectedRecording}
            summary={summary}
            tabRequest={tabRequest}
            getFrame={getFrame}
            getVisSummary={getVisSummary}
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
