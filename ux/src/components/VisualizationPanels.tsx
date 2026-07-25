import { Activity, Boxes, Database, FileCode2, MapPinned, ScanLine } from 'lucide-react'
import type { Dispatch, SetStateAction } from 'react'
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

type PanelProps = {
  tab: WorkspaceTab
  selectedRecording: RecordingEntry | null
  summary: VisSummary | null
  videoState: VideoPlaybackState
  onVideoStateChange: Dispatch<SetStateAction<VideoPlaybackState>>
  getSensorData: () => Promise<SensorDataSummary | null>
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

export default function VisualizationPanel({
  tab,
  selectedRecording,
  summary,
  videoState,
  onVideoStateChange,
  getSensorData,
  getIdoSlamData,
  worldgenResults,
}: PanelProps) {
  if (tab.type === 'video') return <VideoPlayer summary={summary} videoState={videoState} onVideoStateChange={onVideoStateChange} />
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
