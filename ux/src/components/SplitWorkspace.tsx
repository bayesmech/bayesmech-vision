import { PanelTop } from 'lucide-react'
import { PointerEvent, useEffect, useRef, useState, type Dispatch, type SetStateAction } from 'react'
import type {
  DomainReconstruction,
  LayoutNode,
  LeafNode,
  IdoSlamSummary,
  RecordingEntry,
  SensorDataSummary,
  VideoPlaybackState,
  VisSummary,
  WorldgenResult,
  WorkspaceTabRequest,
  WorkspaceTabType,
} from '../types'
import {
  activateTab,
  addTabToLeaf,
  createLeaf,
  createTab,
  findTabByAnalysisKey,
  findTabByType,
  firstLeafId,
  refreshTab,
  updateSplitRatio,
  visitLeaves,
} from '../lib/workspace'
import { analysisKeyForTab, iconForAnalysis, tabTypeForAnalysis } from '../lib/analysisTabs'
import VisualizationPanel from './VisualizationPanels'
import { FrameSourceContext, type FrameGetter } from '../lib/frameSource'
import { OverlayContext, type OverlayState } from '../lib/overlay'
import WorkspaceTimeline from './WorkspaceTimeline'

type SplitWorkspaceProps = {
  selectedRecording: RecordingEntry | null
  summary: VisSummary | null
  tabRequest: WorkspaceTabRequest | null
  getFrame: FrameGetter
  getVisSummary: (sourcePath: string) => Promise<VisSummary | null>
  getSensorData: (sourcePath?: string) => Promise<SensorDataSummary | null>
  getIdoSlamData: (artifactPath?: string) => Promise<IdoSlamSummary | null>
  getDomainReconstruction: (filePath: string) => Promise<DomainReconstruction | null>
  overlay: OverlayState
  videoState: VideoPlaybackState
  onVideoStateChange: Dispatch<SetStateAction<VideoPlaybackState>>
  worldgenResults: Record<string, WorldgenResult>
}

type LeafProps = {
  leaf: LeafNode
  selected: boolean
  selectedRecording: RecordingEntry | null
  summary: VisSummary | null
  videoState: VideoPlaybackState
  onVideoStateChange: Dispatch<SetStateAction<VideoPlaybackState>>
  onSelectLeaf: (leafId: string) => void
  onActivateTab: (leafId: string, tabId: string) => void
  getSensorData: (sourcePath?: string) => Promise<SensorDataSummary | null>
  getFrame: FrameGetter
  getVisSummary: (sourcePath: string) => Promise<VisSummary | null>
  getIdoSlamData: (artifactPath?: string) => Promise<IdoSlamSummary | null>
  getDomainReconstruction: (filePath: string) => Promise<DomainReconstruction | null>
  worldgenResults: Record<string, WorldgenResult>
}

function tabLabel(type: WorkspaceTabType) {
  if (type === 'control') return 'Control'
  if (type === 'point-cloud') return 'Point Cloud'
  if (type === 'planes') return 'Surface Estimates'
  if (type === 'video') return 'Video'
  if (type === 'sensors') return 'Sensor Data'
  if (type === 'analysis') return 'Analysis'
  if (type === 'worldgen') return 'World Modeling'
  return 'Scene 3D'
}

function createRecordingLayout(recording: RecordingEntry | null): LayoutNode {
  const showVideoContexts = (recording?.videoContexts?.length ?? 0) > 1
  const recordingTabs = (recording?.analyses ?? [])
    .filter((analysis) => !['control', 'genspark', 'chat', 'point-cloud'].includes(
      analysis.baseKey ?? analysis.key.split(':')[0],
    ))
    .map((analysis) => createTab(
      tabTypeForAnalysis(analysis.key),
      analysis.title,
      analysis.key,
      undefined,
      analysis.path,
      showVideoContexts ? analysis.videoContext : undefined,
    ))
  if (recording?.controlProject) {
    return createLeaf([
      createTab(
        'control',
        'Control',
        'control',
        undefined,
        undefined,
        showVideoContexts ? 'main' : undefined,
      ),
      ...recordingTabs,
    ])
  }
  return createLeaf(recordingTabs.length ? recordingTabs : [createTab('video')])
}

function WorkspaceLeaf({
  leaf,
  selected,
  selectedRecording,
  summary,
  videoState,
  onVideoStateChange,
  onSelectLeaf,
  onActivateTab,
  getSensorData,
  getFrame,
  getVisSummary,
  getIdoSlamData,
  getDomainReconstruction,
  worldgenResults,
}: LeafProps) {
  const activeTab = leaf.tabs.find((tab) => tab.id === leaf.activeTabId) ?? leaf.tabs[0]

  return (
    <section className={selected ? 'workspace-leaf is-selected' : 'workspace-leaf'} onPointerDown={() => onSelectLeaf(leaf.id)}>
      <div className="tab-strip">
        <div className="tabs" role="tablist">
          {leaf.tabs.map((tab) => {
            const Icon = iconForAnalysis(analysisKeyForTab(tab.type, tab.analysisKey))
            return (
              <button
                type="button"
                role="tab"
                aria-selected={tab.id === activeTab.id}
                className={tab.id === activeTab.id ? 'tab is-active' : 'tab'}
                key={tab.id}
                onClick={() => onActivateTab(leaf.id, tab.id)}
                title={tab.title}
              >
                <Icon className="tab-icon" size={14} aria-hidden="true" />
                <span className="tab-label">{tab.title}</span>
                {tab.contextLabel ? (
                  <span className="tab-context">{tab.contextLabel}</span>
                ) : null}
              </button>
            )
          })}
        </div>
      </div>

      <div className="workspace-content">
        {activeTab ? (
          <VisualizationPanel
            tab={activeTab}
            selectedRecording={selectedRecording}
            summary={summary}
            videoState={videoState}
            onVideoStateChange={onVideoStateChange}
            getSensorData={getSensorData}
            getFrame={getFrame}
            getVisSummary={getVisSummary}
            getIdoSlamData={getIdoSlamData}
            getDomainReconstruction={getDomainReconstruction}
            worldgenResults={worldgenResults}
          />
        ) : (
          <div className="empty-panel">
            <PanelTop size={28} aria-hidden="true" />
            <span>{tabLabel('video')}</span>
          </div>
        )}
      </div>
    </section>
  )
}

type NodeProps = {
  node: LayoutNode
  selectedLeafId: string
  selectedRecording: RecordingEntry | null
  summary: VisSummary | null
  videoState: VideoPlaybackState
  onVideoStateChange: Dispatch<SetStateAction<VideoPlaybackState>>
  onSelectLeaf: (leafId: string) => void
  onActivateTab: (leafId: string, tabId: string) => void
  onResizeSplit: (splitId: string, ratio: number) => void
  getSensorData: (sourcePath?: string) => Promise<SensorDataSummary | null>
  getFrame: FrameGetter
  getVisSummary: (sourcePath: string) => Promise<VisSummary | null>
  getIdoSlamData: (artifactPath?: string) => Promise<IdoSlamSummary | null>
  getDomainReconstruction: (filePath: string) => Promise<DomainReconstruction | null>
  worldgenResults: Record<string, WorldgenResult>
}

function WorkspaceNode({
  node,
  selectedLeafId,
  selectedRecording,
  summary,
  videoState,
  onVideoStateChange,
  onSelectLeaf,
  onActivateTab,
  onResizeSplit,
  getSensorData,
  getFrame,
  getVisSummary,
  getIdoSlamData,
  getDomainReconstruction,
  worldgenResults,
}: NodeProps) {
  const splitRef = useRef<HTMLDivElement>(null)

  if (node.type === 'leaf') {
    return (
      <WorkspaceLeaf
        leaf={node}
        selected={selectedLeafId === node.id}
        selectedRecording={selectedRecording}
        summary={summary}
        videoState={videoState}
        onVideoStateChange={onVideoStateChange}
        onSelectLeaf={onSelectLeaf}
        onActivateTab={onActivateTab}
        getSensorData={getSensorData}
        getFrame={getFrame}
        getVisSummary={getVisSummary}
        getIdoSlamData={getIdoSlamData}
        getDomainReconstruction={getDomainReconstruction}
        worldgenResults={worldgenResults}
      />
    )
  }

  const handlePointerDown = (event: PointerEvent<HTMLDivElement>) => {
    event.preventDefault()
    const element = splitRef.current
    if (!element) return
    const rect = element.getBoundingClientRect()

    const move = (moveEvent: globalThis.PointerEvent) => {
      const rawRatio =
        node.direction === 'row'
          ? (moveEvent.clientX - rect.left) / rect.width
          : (moveEvent.clientY - rect.top) / rect.height
      onResizeSplit(node.id, rawRatio)
    }

    const up = () => {
      window.removeEventListener('pointermove', move)
      window.removeEventListener('pointerup', up)
    }

    window.addEventListener('pointermove', move)
    window.addEventListener('pointerup', up)
  }

  const firstStyle = node.direction === 'row' ? { width: `${node.ratio * 100}%` } : { height: `${node.ratio * 100}%` }
  const secondStyle =
    node.direction === 'row' ? { width: `${(1 - node.ratio) * 100}%` } : { height: `${(1 - node.ratio) * 100}%` }

  return (
    <div className={`workspace-split ${node.direction}`} ref={splitRef}>
      <div className="split-child" style={firstStyle}>
        <WorkspaceNode
          node={node.first}
          selectedLeafId={selectedLeafId}
          selectedRecording={selectedRecording}
          summary={summary}
          videoState={videoState}
          onVideoStateChange={onVideoStateChange}
          onSelectLeaf={onSelectLeaf}
          onActivateTab={onActivateTab}
          onResizeSplit={onResizeSplit}
          getSensorData={getSensorData}
          getFrame={getFrame}
          getVisSummary={getVisSummary}
          getIdoSlamData={getIdoSlamData}
          getDomainReconstruction={getDomainReconstruction}
          worldgenResults={worldgenResults}
        />
      </div>
      <div className="split-handle" onPointerDown={handlePointerDown} />
      <div className="split-child" style={secondStyle}>
        <WorkspaceNode
          node={node.second}
          selectedLeafId={selectedLeafId}
          selectedRecording={selectedRecording}
          summary={summary}
          videoState={videoState}
          onVideoStateChange={onVideoStateChange}
          onSelectLeaf={onSelectLeaf}
          onActivateTab={onActivateTab}
          onResizeSplit={onResizeSplit}
          getSensorData={getSensorData}
          getFrame={getFrame}
          getVisSummary={getVisSummary}
          getIdoSlamData={getIdoSlamData}
          getDomainReconstruction={getDomainReconstruction}
          worldgenResults={worldgenResults}
        />
      </div>
    </div>
  )
}

export default function SplitWorkspace({
  selectedRecording,
  summary,
  tabRequest,
  getFrame,
  getVisSummary,
  getSensorData,
  getIdoSlamData,
  getDomainReconstruction,
  overlay,
  videoState,
  onVideoStateChange,
  worldgenResults,
}: SplitWorkspaceProps) {
  const [layout, setLayout] = useState<LayoutNode>(() => createRecordingLayout(selectedRecording))
  const [selectedLeafId, setSelectedLeafId] = useState(() => firstLeafId(layout))
  const consumedRequestRef = useRef<string | null>(null)
  const controlProjectActive = Boolean(selectedRecording?.controlProject)
  const recordingLayoutKey = `${selectedRecording?.path ?? ''}:${controlProjectActive ? 'control' : 'recording'}`
  const analysisLayoutKey = (selectedRecording?.analyses ?? [])
    .map((analysis) => `${analysis.key}:${analysis.path}`)
    .join('|')

  useEffect(() => {
    const nextLayout = createRecordingLayout(selectedRecording)
    setLayout(nextLayout)
    setSelectedLeafId(firstLeafId(nextLayout))
    consumedRequestRef.current = null
    // Recording content can refresh every few seconds while devices stream.
    // Rebuild only when the selected recording or its control mode changes;
    // otherwise the active tab would repeatedly jump back to Control.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [recordingLayoutKey])

  useEffect(() => {
    const analyses = (selectedRecording?.analyses ?? []).filter((analysis) => (
      !['control', 'genspark', 'chat', 'point-cloud'].includes(
        analysis.baseKey ?? analysis.key.split(':')[0],
      )
    ))
    if (!analyses.length) return
    setLayout((current) => {
      let next = current
      for (const analysis of analyses) {
        if (findTabByAnalysisKey(next, analysis.key)) continue
        const targetLeafId = findTabByType(next, 'video')?.leafId ?? firstLeafId(next)
        next = addTabToLeaf(next, targetLeafId, {
          requestId: `discovered:${analysis.key}:${analysis.path}`,
          type: tabTypeForAnalysis(analysis.key),
          title: analysis.title,
          analysisKey: analysis.key,
          sourcePath: analysis.path,
        }, false)
      }
      return next
    })
  }, [analysisLayoutKey, selectedRecording?.analyses])

  useEffect(() => {
    let leafStillExists = false
    visitLeaves(layout, (leaf) => {
      if (leaf.id === selectedLeafId) leafStillExists = true
    })
    if (!leafStillExists) setSelectedLeafId(firstLeafId(layout))
  }, [layout, selectedLeafId])

  useEffect(() => {
    if (!tabRequest || consumedRequestRef.current === tabRequest.requestId) return
    consumedRequestRef.current = tabRequest.requestId
    const existing = tabRequest.analysisKey
      ? findTabByAnalysisKey(layout, tabRequest.analysisKey)
      : findTabByType(layout, tabRequest.type)
    if (existing) {
      setSelectedLeafId(existing.leafId)
      setLayout((current) => tabRequest.worldgenResultId
        ? refreshTab(current, existing.leafId, existing.tabId, tabRequest)
        : activateTab(current, existing.leafId, existing.tabId))
      return
    }
    const targetLeafId = findTabByType(layout, 'video')?.leafId ?? selectedLeafId
    setSelectedLeafId(targetLeafId)
    setLayout((current) => addTabToLeaf(current, targetLeafId, tabRequest))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedLeafId, tabRequest])

  return (
    <FrameSourceContext.Provider value={getFrame}>
      <OverlayContext.Provider value={overlay}>
      <section className={controlProjectActive ? 'visual-workspace is-control' : 'visual-workspace'}>
        <div className="workspace-viewers">
          <WorkspaceNode
            node={layout}
            selectedLeafId={selectedLeafId}
            selectedRecording={selectedRecording}
            summary={summary}
            videoState={videoState}
            onVideoStateChange={onVideoStateChange}
            onSelectLeaf={setSelectedLeafId}
            onActivateTab={(leafId, tabId) => setLayout((current) => activateTab(current, leafId, tabId))}
            onResizeSplit={(splitId, ratio) => setLayout((current) => updateSplitRatio(current, splitId, ratio))}
            getSensorData={getSensorData}
            getFrame={getFrame}
            getVisSummary={getVisSummary}
            getIdoSlamData={getIdoSlamData}
            getDomainReconstruction={getDomainReconstruction}
            worldgenResults={worldgenResults}
          />
        </div>
        <WorkspaceTimeline
          summary={summary}
          videoState={videoState}
          onVideoStateChange={onVideoStateChange}
          worldgenResults={worldgenResults}
        />
      </section>
      </OverlayContext.Provider>
    </FrameSourceContext.Provider>
  )
}
