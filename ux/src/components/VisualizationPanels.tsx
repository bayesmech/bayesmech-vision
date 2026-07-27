import { Activity, Boxes, Database, FileCode2, MapPinned, ScanLine } from 'lucide-react'
import { useCallback, useEffect, useState, type Dispatch, type SetStateAction } from 'react'
import type {
  DomainReconstruction,
  IdoSlamSummary,
  ProjectAnalysis,
  RecordingEntry,
  SensorDataSummary,
  VideoPlaybackState,
  VisSummary,
  WorkspaceTab,
  WorldgenResult,
} from '../types'
import { compactNumber, shortPath } from '../lib/format'
import Scene3D from './Scene3D'
import MapGenerationPanel from './MapGenerationPanel'
import SensorDataPanel from './SensorDataPanel'
import VideoPlayer from './VideoPlayer'
import WorldgenScene from './WorldgenScene'
import ControlPanel from './ControlPanel'
import DomainReconstructionScene from './DomainReconstructionScene'
import { FrameSourceContext, type FrameGetter } from '../lib/frameSource'
import { OverlayContext, useOverlay, type OverlayState } from '../lib/overlay'
import { baseAnalysisKey } from '../lib/analysisTabs'

type PanelProps = {
  tab: WorkspaceTab
  selectedRecording: RecordingEntry | null
  summary: VisSummary | null
  videoState: VideoPlaybackState
  onVideoStateChange: Dispatch<SetStateAction<VideoPlaybackState>>
  getSensorData: (sourcePath?: string) => Promise<SensorDataSummary | null>
  getFrame: FrameGetter
  getVisSummary: (sourcePath: string) => Promise<VisSummary | null>
  getIdoSlamData: (artifactPath?: string) => Promise<IdoSlamSummary | null>
  getDomainReconstruction: (filePath: string) => Promise<DomainReconstruction | null>
  worldgenResults: Record<string, WorldgenResult>
}

function metric(label: string, value: string, icon: typeof Activity) {
  const Icon = icon
  return (
    <div className="metric">
      <Icon size={15} aria-hidden="true" />
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  )
}

function analysisForTab(recording: RecordingEntry | null, tab: WorkspaceTab): ProjectAnalysis | undefined {
  if (!recording) return undefined
  if (tab.analysisKey) return recording.analyses.find((analysis) => analysis.key === tab.analysisKey)
  return undefined
}

function AnalysisPanel({ recording, summary, tab }: { recording: RecordingEntry | null; summary: VisSummary | null; tab: WorkspaceTab }) {
  const analysis = analysisForTab(recording, tab)

  if (!recording) {
    return <EmptyPanel title="Analysis" />
  }

  return (
    <div className="inspector-view">
      <div className="analysis-heading">
        <FileCode2 size={18} aria-hidden="true" />
        <div>
          <h3>{analysis?.title ?? 'Recording Analysis'}</h3>
          <p title={analysis?.path ?? recording.path}>{shortPath(analysis?.relativePath ?? recording.relativePath)}</p>
        </div>
      </div>

      <div className="metrics-grid">
        {metric('Artifacts', compactNumber(recording.analyses.filter((item) => item.source === 'artifact').length), Database)}
        {metric('Frames', summary ? compactNumber(summary.frameCount) : '0', ScanLine)}
        {metric('Point samples', summary ? compactNumber(summary.sampledPointCount) : '0', Boxes)}
        {metric('Plane samples', summary ? compactNumber(summary.sampledPlaneCount) : '0', MapPinned)}
      </div>

      <div className="artifact-list">
        {recording.analyses.map((item) => (
          <div className="artifact-row" key={item.key}>
            <span>{item.title}</span>
            <strong>{item.source === 'vis' ? 'native' : item.sizeLabel}</strong>
          </div>
        ))}
      </div>
    </div>
  )
}

function EmptyPanel({ title }: { title: string }) {
  return (
    <div className="empty-panel">
      <Database size={28} aria-hidden="true" />
      <span>{title}</span>
    </div>
  )
}

function StreamVideoPanel({
  sourcePath,
  initialSummary,
  live,
  getFrame,
  getVisSummary,
  videoState,
  onVideoStateChange,
  segmentationViewer = false,
  motionCaptureViewer = false,
  overlayOverride,
}: {
  sourcePath: string | undefined
  initialSummary: VisSummary | null
  live: boolean
  getFrame: FrameGetter
  getVisSummary: (sourcePath: string) => Promise<VisSummary | null>
  videoState: VideoPlaybackState
  onVideoStateChange: Dispatch<SetStateAction<VideoPlaybackState>>
  segmentationViewer?: boolean
  motionCaptureViewer?: boolean
  overlayOverride?: OverlayState | null
}) {
  const [sourceSummary, setSourceSummary] = useState(initialSummary)
  const sourceFrame = useCallback(
    (index: number) => getFrame(index, sourcePath),
    [getFrame, sourcePath],
  )

  useEffect(() => {
    setSourceSummary(initialSummary)
    if (!sourcePath) return
    let cancelled = false
    const refresh = () => {
      void getVisSummary(sourcePath)
        .then((next) => {
          if (!cancelled && next) setSourceSummary(next)
        })
        .catch(() => {
          // A lazily-created augmented stream may not exist until its first frame.
        })
    }
    refresh()
    const timer = live ? window.setInterval(refresh, 1000) : null
    return () => {
      cancelled = true
      if (timer !== null) window.clearInterval(timer)
    }
  }, [getVisSummary, initialSummary, live, sourcePath])

  const player = (
    <FrameSourceContext.Provider value={sourceFrame}>
      <VideoPlayer
        summary={sourceSummary}
        videoState={videoState}
        onVideoStateChange={onVideoStateChange}
        segmentationViewer={segmentationViewer}
        motionCaptureViewer={motionCaptureViewer}
      />
    </FrameSourceContext.Provider>
  )
  return overlayOverride
    ? <OverlayContext.Provider value={overlayOverride}>{player}</OverlayContext.Provider>
    : player
}

function SourceScenePanel({
  sourcePath,
  initialSummary,
  getVisSummary,
}: {
  sourcePath: string | undefined
  initialSummary: VisSummary | null
  getVisSummary: (sourcePath: string) => Promise<VisSummary | null>
}) {
  const [sourceSummary, setSourceSummary] = useState(initialSummary)
  useEffect(() => {
    setSourceSummary(initialSummary)
    if (!sourcePath || initialSummary?.path === sourcePath) return
    let cancelled = false
    void getVisSummary(sourcePath).then((next) => {
      if (!cancelled) setSourceSummary(next)
    })
    return () => {
      cancelled = true
    }
  }, [getVisSummary, initialSummary, sourcePath])
  return <Scene3D summary={sourceSummary} mode="planes" />
}

export default function VisualizationPanel({
  tab,
  selectedRecording,
  summary,
  videoState,
  onVideoStateChange,
  getSensorData,
  getFrame,
  getVisSummary,
  getIdoSlamData,
  getDomainReconstruction,
  worldgenResults,
}: PanelProps) {
  const overlay = useOverlay()
  const analysis = analysisForTab(selectedRecording, tab)
  const analysisBaseKey = baseAnalysisKey(tab.analysisKey ?? '')
  const sourceVideoPath = analysis?.sourceVideoPath ?? selectedRecording?.path
  const sourceInitialSummary = sourceVideoPath === selectedRecording?.path ? summary : null
  if (tab.type === 'control') {
    return <ControlPanel controlProject={selectedRecording?.controlProject ?? null} />
  }
  if (tab.type === 'video') {
    const sourcePath = tab.sourcePath ?? analysis?.sourceVideoPath ?? analysis?.path
    const initialSummary = !sourcePath || sourcePath === selectedRecording?.path ? summary : null
    return (
      <StreamVideoPanel
        sourcePath={sourcePath}
        initialSummary={initialSummary}
        live={Boolean(selectedRecording?.controlProject)}
        getFrame={getFrame}
        getVisSummary={getVisSummary}
        videoState={videoState}
        onVideoStateChange={onVideoStateChange}
      />
    )
  }
  if (tab.type === 'sensors') {
    return (
      <SensorDataPanel
        currentFrameIndex={videoState.index}
        getSensorData={() => getSensorData(sourceVideoPath)}
      />
    )
  }
  if (analysisBaseKey === 'idoslam') {
    return (
      <MapGenerationPanel
        currentFrameIndex={videoState.index}
        getSensorData={() => getSensorData(sourceVideoPath)}
        getIdoSlamData={() => getIdoSlamData(analysis?.path)}
      />
    )
  }
  if (analysisBaseKey === 'segmentation') {
    const contextualOverlay = overlay && analysis
      ? {
          ...overlay,
          getSegmentation: (frameNumber: number) => (
            overlay.getSegmentation(frameNumber, analysis.path)
          ),
          getSegmentationLabels: () => overlay.getSegmentationLabels(analysis.path),
        }
      : overlay
    return (
      <StreamVideoPanel
        sourcePath={sourceVideoPath}
        initialSummary={sourceInitialSummary}
        live={Boolean(selectedRecording?.controlProject)}
        getFrame={getFrame}
        getVisSummary={getVisSummary}
        videoState={videoState}
        onVideoStateChange={onVideoStateChange}
        segmentationViewer
        overlayOverride={contextualOverlay}
      />
    )
  }
  if (analysisBaseKey === 'motioncap') {
    const contextualOverlay = overlay && analysis
      ? {
          ...overlay,
          getMotionCapture: (frameNumber: number) => (
            overlay.getMotionCapture(frameNumber, analysis.path)
          ),
        }
      : overlay
    return (
      <StreamVideoPanel
        sourcePath={sourceVideoPath}
        initialSummary={sourceInitialSummary}
        live={Boolean(selectedRecording?.controlProject)}
        getFrame={getFrame}
        getVisSummary={getVisSummary}
        videoState={videoState}
        onVideoStateChange={onVideoStateChange}
        motionCaptureViewer
        overlayOverride={contextualOverlay}
      />
    )
  }
  if (analysisBaseKey === 'pongtown' || analysisBaseKey === 'snookestown') {
    return analysis ? (
      <DomainReconstructionScene
        sourcePath={analysis.path}
        currentFrameIndex={videoState.index}
        getDomainReconstruction={getDomainReconstruction}
      />
    ) : <EmptyPanel title="Domain specific reconstruction" />
  }
  if (tab.type === 'worldgen') {
    const result = tab.worldgenResultId
      ? worldgenResults[tab.worldgenResultId] ?? null
      : Object.values(worldgenResults).find((item) => item.outputPath === analysis?.path)
        ?? Object.values(worldgenResults)[0]
        ?? null
    return (
      <WorldgenScene
        result={result}
        currentFrameIndex={videoState.index}
        onVideoStateChange={onVideoStateChange}
      />
    )
  }
  if (tab.type === 'scene') return <Scene3D summary={sourceInitialSummary} mode="scene" />
  if (tab.type === 'point-cloud' || tab.type === 'planes') {
    return (
      <SourceScenePanel
        sourcePath={sourceVideoPath}
        initialSummary={sourceInitialSummary}
        getVisSummary={getVisSummary}
      />
    )
  }
  return <AnalysisPanel recording={selectedRecording} summary={summary} tab={tab} />
}
