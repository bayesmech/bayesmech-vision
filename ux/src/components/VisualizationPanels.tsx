import { Activity, Boxes, Database, FileCode2, MapPinned, ScanLine } from 'lucide-react'
import { useCallback, useEffect, useMemo, useState, type Dispatch, type SetStateAction } from 'react'
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
import WorkspaceTimeline from './WorkspaceTimeline'

const EMPTY_WORLDGEN_RESULTS: Record<string, WorldgenResult> = {}

type TimestampedIndex = {
  index: number
  timestampNs: bigint
}

function summaryTimeline(summary: VisSummary): TimestampedIndex[] {
  const samples = summary.samples
    .map((sample) => {
      try {
        return { index: sample.sampleIndex, timestampNs: BigInt(sample.timestampNs) }
      } catch {
        return null
      }
    })
    .filter((sample): sample is TimestampedIndex => sample !== null)
    .sort((left, right) => left.index - right.index)
  if (samples.length) return samples
  try {
    return [
      { index: 0, timestampNs: BigInt(summary.firstTimestampNs) },
      {
        index: Math.max(0, summary.frameCount - 1),
        timestampNs: BigInt(summary.lastTimestampNs),
      },
    ]
  } catch {
    return []
  }
}

function timestampForIndex(summary: VisSummary, requestedIndex: number): bigint | null {
  const samples = summaryTimeline(summary)
  if (!samples.length) return null
  const index = Math.max(0, Math.min(summary.frameCount - 1, Math.trunc(requestedIndex)))
  if (index <= samples[0].index) return samples[0].timestampNs
  const last = samples.at(-1)!
  if (index >= last.index) return last.timestampNs
  let low = 0
  let high = samples.length - 1
  while (low + 1 < high) {
    const middle = Math.floor((low + high) / 2)
    if (samples[middle].index <= index) low = middle
    else high = middle
  }
  const left = samples[low]
  const right = samples[high]
  const width = right.index - left.index
  if (width <= 0) return left.timestampNs
  return left.timestampNs
    + ((right.timestampNs - left.timestampNs) * BigInt(index - left.index)) / BigInt(width)
}

function indexForTimestamp(summary: VisSummary, timestampNs: bigint): number {
  const samples = summaryTimeline(summary)
  if (!samples.length) return 0
  if (timestampNs <= samples[0].timestampNs) return samples[0].index
  const last = samples.at(-1)!
  if (timestampNs >= last.timestampNs) return last.index
  let low = 0
  let high = samples.length - 1
  while (low + 1 < high) {
    const middle = Math.floor((low + high) / 2)
    if (samples[middle].timestampNs <= timestampNs) low = middle
    else high = middle
  }
  const left = samples[low]
  const right = samples[high]
  const duration = Number(right.timestampNs - left.timestampNs)
  if (!Number.isFinite(duration) || duration <= 0) return left.index
  const progress = Number(timestampNs - left.timestampNs) / duration
  return Math.max(
    0,
    Math.min(
      summary.frameCount - 1,
      Math.round(left.index + progress * (right.index - left.index)),
    ),
  )
}

function timestampFromPlaybackState(
  state: VideoPlaybackState,
  timelineSummary: VisSummary,
): bigint | null {
  if (state.playbackTimestampNs) {
    try {
      return BigInt(state.playbackTimestampNs)
    } catch {
      // Fall back to the primary frame index for older persisted state.
    }
  }
  return timestampForIndex(timelineSummary, state.index)
}

function contextPlaybackState(
  state: VideoPlaybackState,
  sourceSummary: VisSummary,
  timelineSummary: VisSummary,
): VideoPlaybackState {
  const timestampNs = timestampFromPlaybackState(state, timelineSummary)
  const sourceStart = timestampForIndex(sourceSummary, 0)
  return {
    ...state,
    index: timestampNs == null ? 0 : indexForTimestamp(sourceSummary, timestampNs),
    markers: state.markers.map((marker) => {
      const markerTimestamp = timestampForIndex(timelineSummary, marker.frameIndex)
      const sourceIndex = markerTimestamp == null
        ? 0
        : indexForTimestamp(sourceSummary, markerTimestamp)
      return {
        ...marker,
        frameIndex: sourceIndex,
        frameNumber: sourceSummary.firstFrameNumber + sourceIndex,
        seconds: markerTimestamp != null && sourceStart != null
          ? Number(markerTimestamp - sourceStart) / 1e9
          : 0,
      }
    }),
  }
}

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
  timelineSummary,
  live,
  getFrame,
  getVisSummary,
  videoState,
  onVideoStateChange,
  segmentationViewer = false,
  motionCaptureViewer = false,
  showPlaybackControls = false,
  overlayOverride,
}: {
  sourcePath: string | undefined
  initialSummary: VisSummary | null
  timelineSummary?: VisSummary | null
  live: boolean
  getFrame: FrameGetter
  getVisSummary: (sourcePath: string) => Promise<VisSummary | null>
  videoState: VideoPlaybackState
  onVideoStateChange: Dispatch<SetStateAction<VideoPlaybackState>>
  segmentationViewer?: boolean
  motionCaptureViewer?: boolean
  showPlaybackControls?: boolean
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
          if (!cancelled && next) {
            setSourceSummary((current) => (
              current
              && current.path === next.path
              && current.frameCount === next.frameCount
              && current.lastTimestampNs === next.lastTimestampNs
              && current.sizeBytes === next.sizeBytes
                ? current
                : next
            ))
          }
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

  const mappedVideoState = useMemo(
    () => sourceSummary && timelineSummary
      ? contextPlaybackState(videoState, sourceSummary, timelineSummary)
      : videoState,
    [sourceSummary, timelineSummary, videoState],
  )
  const handleMappedVideoStateChange = useCallback<Dispatch<SetStateAction<VideoPlaybackState>>>(
    (update) => {
      if (!sourceSummary || !timelineSummary) {
        onVideoStateChange(update)
        return
      }
      onVideoStateChange((current) => {
        const contextCurrent = contextPlaybackState(current, sourceSummary, timelineSummary)
        const contextNext = typeof update === 'function' ? update(contextCurrent) : update
        const timestampNs = timestampForIndex(sourceSummary, contextNext.index)
          ?? timestampFromPlaybackState(current, timelineSummary)
        const primaryIndex = timestampNs == null
          ? current.index
          : indexForTimestamp(timelineSummary, timestampNs)
        const existingMarkerIds = new Set(current.markers.map((marker) => marker.id))
        const primaryStart = timestampForIndex(timelineSummary, 0)
        const addedMarkers = contextNext.markers
          .filter((marker) => !existingMarkerIds.has(marker.id))
          .map((marker) => {
            const markerTimestamp = timestampForIndex(sourceSummary, marker.frameIndex)
            const markerIndex = markerTimestamp == null
              ? primaryIndex
              : indexForTimestamp(timelineSummary, markerTimestamp)
            return {
              ...marker,
              frameIndex: markerIndex,
              frameNumber: timelineSummary.firstFrameNumber + markerIndex,
              seconds: markerTimestamp != null && primaryStart != null
                ? Number(markerTimestamp - primaryStart) / 1e9
                : 0,
            }
          })
        return {
          ...current,
          index: primaryIndex,
          playing: contextNext.playing,
          speed: contextNext.speed,
          markers: [...current.markers, ...addedMarkers]
            .sort((left, right) => left.frameIndex - right.frameIndex),
          playbackTimestampNs: timestampNs?.toString(),
        }
      })
    },
    [onVideoStateChange, sourceSummary, timelineSummary],
  )

  const videoPlayer = (
    <FrameSourceContext.Provider value={sourceFrame}>
      <VideoPlayer
        summary={sourceSummary}
        videoState={mappedVideoState}
        onVideoStateChange={handleMappedVideoStateChange}
        segmentationViewer={segmentationViewer}
        motionCaptureViewer={motionCaptureViewer}
      />
    </FrameSourceContext.Provider>
  )
  const player = showPlaybackControls ? (
    <div className="stream-video-playback">
      {videoPlayer}
      <WorkspaceTimeline
        summary={sourceSummary}
        videoState={mappedVideoState}
        onVideoStateChange={handleMappedVideoStateChange}
        worldgenResults={EMPTY_WORLDGEN_RESULTS}
      />
    </div>
  ) : videoPlayer
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
        timelineSummary={selectedRecording?.controlProject ? summary : null}
        live={Boolean(selectedRecording?.controlProject)}
        getFrame={getFrame}
        getVisSummary={getVisSummary}
        videoState={videoState}
        onVideoStateChange={onVideoStateChange}
        showPlaybackControls={Boolean(selectedRecording?.controlProject)}
      />
    )
  }
  if (tab.type === 'sensors') {
    return (
      <SensorDataPanel
        currentFrameIndex={videoState.index}
        sourcePath={sourceVideoPath}
        getSensorData={getSensorData}
      />
    )
  }
  if (analysisBaseKey === 'idoslam') {
    return (
      <MapGenerationPanel
        currentFrameIndex={videoState.index}
        sourcePath={sourceVideoPath}
        artifactPath={analysis?.path}
        getSensorData={getSensorData}
        getIdoSlamData={getIdoSlamData}
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
