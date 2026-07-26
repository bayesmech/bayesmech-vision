import { Activity, Boxes, Database, FileCode2, MapPinned, ScanLine } from 'lucide-react'
import { useCallback, useEffect, useState, type Dispatch, type SetStateAction } from 'react'
import type {
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
import { FrameSourceContext, type FrameGetter } from '../lib/frameSource'

type PanelProps = {
  tab: WorkspaceTab
  selectedRecording: RecordingEntry | null
  summary: VisSummary | null
  videoState: VideoPlaybackState
  onVideoStateChange: Dispatch<SetStateAction<VideoPlaybackState>>
  getSensorData: () => Promise<SensorDataSummary | null>
  getFrame: FrameGetter
  getVisSummary: (sourcePath: string) => Promise<VisSummary | null>
  getIdoSlamData: () => Promise<IdoSlamSummary | null>
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
}: {
  sourcePath: string | undefined
  initialSummary: VisSummary | null
  live: boolean
  getFrame: FrameGetter
  getVisSummary: (sourcePath: string) => Promise<VisSummary | null>
  videoState: VideoPlaybackState
  onVideoStateChange: Dispatch<SetStateAction<VideoPlaybackState>>
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

  return (
    <FrameSourceContext.Provider value={sourceFrame}>
      <VideoPlayer
        summary={sourceSummary}
        videoState={videoState}
        onVideoStateChange={onVideoStateChange}
      />
    </FrameSourceContext.Provider>
  )
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
  worldgenResults,
}: PanelProps) {
  if (tab.type === 'control') {
    return <ControlPanel controlProject={selectedRecording?.controlProject ?? null} />
  }
  if (tab.type === 'video') {
    const sourcePath = tab.sourcePath ?? analysisForTab(selectedRecording, tab)?.path
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
    return <SensorDataPanel currentFrameIndex={videoState.index} getSensorData={getSensorData} />
  }
  if (tab.analysisKey === 'idoslam') {
    return (
      <MapGenerationPanel
        currentFrameIndex={videoState.index}
        getSensorData={getSensorData}
        getIdoSlamData={getIdoSlamData}
      />
    )
  }
  if (tab.analysisKey === 'segmentation') {
    return (
      <VideoPlayer
        summary={summary}
        videoState={videoState}
        onVideoStateChange={onVideoStateChange}
        segmentationViewer
      />
    )
  }
  if (tab.analysisKey === 'motioncap') {
    return (
      <VideoPlayer
        summary={summary}
        videoState={videoState}
        onVideoStateChange={onVideoStateChange}
        motionCaptureViewer
      />
    )
  }
  if (tab.type === 'worldgen') {
    const result = tab.worldgenResultId
      ? worldgenResults[tab.worldgenResultId] ?? null
      : Object.values(worldgenResults)[0] ?? null
    return (
      <WorldgenScene
        result={result}
        currentFrameIndex={videoState.index}
        onVideoStateChange={onVideoStateChange}
      />
    )
  }
  if (tab.type === 'scene') return <Scene3D summary={summary} mode="scene" />
  if (tab.type === 'point-cloud') return <Scene3D summary={summary} mode="planes" />
  if (tab.type === 'planes') return <Scene3D summary={summary} mode="planes" />
  return <AnalysisPanel recording={selectedRecording} summary={summary} tab={tab} />
}
