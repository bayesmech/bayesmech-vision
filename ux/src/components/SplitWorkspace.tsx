import { Columns2, CopyPlus, PanelTop, Plus, Rows2, X } from 'lucide-react'
import { PointerEvent, useEffect, useMemo, useRef, useState, type Dispatch, type SetStateAction } from 'react'
import type {
  LayoutNode,
  LeafNode,
  RecordingEntry,
  VideoPlaybackState,
  VisSummary,
  WorldgenResult,
  WorkspaceTabRequest,
  WorkspaceTabType,
} from '../types'
import {
  activateTab,
  addTabToLeaf,
  closeTab,
  createInitialLayout,
  findTabByType,
  firstLeafId,
  splitLeaf,
  updateSplitRatio,
  visitLeaves,
} from '../lib/workspace'
import VisualizationPanel from './VisualizationPanels'
import { FrameSourceContext, type FrameGetter } from '../lib/frameSource'
import { OverlayContext, type OverlayState } from '../lib/overlay'

type SplitWorkspaceProps = {
  selectedRecording: RecordingEntry | null
  summary: VisSummary | null
  tabRequest: WorkspaceTabRequest | null
  getFrame: FrameGetter
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
  onCloseTab: (leafId: string, tabId: string) => void
  onAddTab: (leafId: string, type: WorkspaceTabType) => void
  onSplit: (leafId: string, direction: 'row' | 'column') => void
  worldgenResults: Record<string, WorldgenResult>
}

function tabLabel(type: WorkspaceTabType) {
  if (type === 'point-cloud') return 'Point Cloud'
  if (type === 'planes') return 'Surface Estimates'
  if (type === 'video') return 'Video'
  if (type === 'analysis') return 'Analysis'
  if (type === 'worldgen') return 'Worldgen'
  return 'Scene 3D'
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
  onCloseTab,
  onAddTab,
  onSplit,
  worldgenResults,
}: LeafProps) {
  const [newTabType, setNewTabType] = useState<WorkspaceTabType>('video')
  const activeTab = leaf.tabs.find((tab) => tab.id === leaf.activeTabId) ?? leaf.tabs[0]

  return (
    <section className={selected ? 'workspace-leaf is-selected' : 'workspace-leaf'} onPointerDown={() => onSelectLeaf(leaf.id)}>
      <div className="tab-strip">
        <div className="tabs" role="tablist">
          {leaf.tabs.map((tab) => (
            <button
              type="button"
              role="tab"
              aria-selected={tab.id === activeTab.id}
              className={tab.id === activeTab.id ? 'tab is-active' : 'tab'}
              key={tab.id}
              onClick={() => onActivateTab(leaf.id, tab.id)}
              title={tab.title}
            >
              <span>{tab.title}</span>
              {leaf.tabs.length > 1 ? (
                <span
                  className="tab-close"
                  role="button"
                  tabIndex={0}
                  title="Close tab"
                  onClick={(event) => {
                    event.stopPropagation()
                    onCloseTab(leaf.id, tab.id)
                  }}
                >
                  <X size={12} aria-hidden="true" />
                </span>
              ) : null}
            </button>
          ))}
        </div>

        <div className="tab-actions">
          <label className="tab-select" title="New tab type">
            <CopyPlus size={13} aria-hidden="true" />
            <select
              value={newTabType}
              onChange={(event) => setNewTabType(event.target.value as WorkspaceTabType)}
            >
              <option value="video">Video</option>
              <option value="point-cloud">Point Cloud</option>
              <option value="planes">Surface Estimates</option>
              <option value="worldgen">Worldgen</option>
              <option value="analysis">Analysis</option>
            </select>
          </label>
          <button type="button" className="icon-button" onClick={() => onAddTab(leaf.id, newTabType)} title="Add tab">
            <Plus size={14} aria-hidden="true" />
          </button>
          <button type="button" className="icon-button" onClick={() => onSplit(leaf.id, 'row')} title="Split right">
            <Columns2 size={14} aria-hidden="true" />
          </button>
          <button type="button" className="icon-button" onClick={() => onSplit(leaf.id, 'column')} title="Split down">
            <Rows2 size={14} aria-hidden="true" />
          </button>
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
            worldgenResults={worldgenResults}
          />
        ) : (
          <div className="empty-panel">
            <PanelTop size={28} aria-hidden="true" />
            <span>{tabLabel(newTabType)}</span>
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
  onCloseTab: (leafId: string, tabId: string) => void
  onAddTab: (leafId: string, type: WorkspaceTabType) => void
  onSplit: (leafId: string, direction: 'row' | 'column') => void
  onResizeSplit: (splitId: string, ratio: number) => void
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
  onCloseTab,
  onAddTab,
  onSplit,
  onResizeSplit,
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
        onCloseTab={onCloseTab}
        onAddTab={onAddTab}
        onSplit={onSplit}
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
          onCloseTab={onCloseTab}
          onAddTab={onAddTab}
          onSplit={onSplit}
          onResizeSplit={onResizeSplit}
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
          onCloseTab={onCloseTab}
          onAddTab={onAddTab}
          onSplit={onSplit}
          onResizeSplit={onResizeSplit}
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
  overlay,
  videoState,
  onVideoStateChange,
  worldgenResults,
}: SplitWorkspaceProps) {
  const initialLayout = useMemo(() => createInitialLayout(), [])
  const [layout, setLayout] = useState<LayoutNode>(initialLayout)
  const [selectedLeafId, setSelectedLeafId] = useState(() => firstLeafId(initialLayout))
  const consumedRequestRef = useRef<string | null>(null)

  useEffect(() => {
    let leafStillExists = false
    visitLeaves(layout, (leaf) => {
      if (leaf.id === selectedLeafId) leafStillExists = true
    })
    if (!leafStillExists) setSelectedLeafId(firstLeafId(layout))
  }, [layout, selectedLeafId])

  // Video is a singleton: a request to open one focuses the existing tab.
  const focusExistingVideo = (): boolean => {
    const existing = findTabByType(layout, 'video')
    if (!existing) return false
    setSelectedLeafId(existing.leafId)
    setLayout((current) => activateTab(current, existing.leafId, existing.tabId))
    return true
  }

  useEffect(() => {
    if (!tabRequest || consumedRequestRef.current === tabRequest.requestId) return
    consumedRequestRef.current = tabRequest.requestId
    if (tabRequest.type === 'video' && focusExistingVideo()) return
    if (tabRequest.type === 'worldgen') {
      const video = findTabByType(layout, 'video')
      if (video) {
        setSelectedLeafId(video.leafId)
        setLayout((current) => addTabToLeaf(current, video.leafId, tabRequest))
        return
      }
    }
    setLayout((current) => addTabToLeaf(current, selectedLeafId, tabRequest))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedLeafId, tabRequest])

  const addTab = (leafId: string, type: WorkspaceTabType) => {
    if (type === 'video' && focusExistingVideo()) return
    setLayout((current) =>
      addTabToLeaf(current, leafId, {
        requestId: `local-${Date.now()}`,
        type,
        title: tabLabel(type),
      }),
    )
  }

  return (
    <FrameSourceContext.Provider value={getFrame}>
      <OverlayContext.Provider value={overlay}>
      <section className="visual-workspace">
        <WorkspaceNode
          node={layout}
          selectedLeafId={selectedLeafId}
          selectedRecording={selectedRecording}
          summary={summary}
          videoState={videoState}
          onVideoStateChange={onVideoStateChange}
          onSelectLeaf={setSelectedLeafId}
          onActivateTab={(leafId, tabId) => setLayout((current) => activateTab(current, leafId, tabId))}
          onCloseTab={(leafId, tabId) => setLayout((current) => closeTab(current, leafId, tabId))}
          onAddTab={addTab}
          onSplit={(leafId, direction) => {
            setLayout((current) => splitLeaf(current, leafId, direction))
          }}
          onResizeSplit={(splitId, ratio) => setLayout((current) => updateSplitRatio(current, splitId, ratio))}
          worldgenResults={worldgenResults}
        />
      </section>
      </OverlayContext.Provider>
    </FrameSourceContext.Provider>
  )
}
