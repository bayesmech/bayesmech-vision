import {
  Box,
  Boxes,
  BrainCircuit,
  ChevronRight,
  CircleDot,
  Cloud,
  Database,
  FileCode2,
  Film,
  FolderOpen,
  Image,
  Layers3,
  Map,
  MessageSquare,
  ScanSearch,
  Search,
  Waves,
} from 'lucide-react'
import type { ProjectAnalysis, ProjectScanResult, RecordingEntry, WorkspaceTabType } from '../types'
import { dateTimeLabel, shortPath } from '../lib/format'

type ProjectExplorerProps = {
  project: ProjectScanResult | null
  selectedRecordingId?: string
  filter: string
  onFilterChange: (value: string) => void
  onOpenProject: () => void
  onOpenFiles: () => void
  onSelectRecording: (recording: RecordingEntry) => void
  onOpenAnalysis: (recording: RecordingEntry, analysis: ProjectAnalysis) => void
}

const iconForAnalysis = (key: string) => {
  switch (key) {
    case 'video':
      return Film
    case 'rgb':
      return Image
    case 'depth':
      return Waves
    case 'point-cloud':
      return Cloud
    case 'surface-planes':
      return Layers3
    case 'segmentation':
      return Boxes
    case 'motioncap':
      return CircleDot
    case 'idoslam':
      return Map
    case 'genspark':
      return BrainCircuit
    case 'chat':
      return MessageSquare
    case 'reconstruction':
      return Box
    case 'worldgen':
      return ScanSearch
    default:
      return FileCode2
  }
}

export function tabTypeForAnalysis(key: string): WorkspaceTabType {
  if (key === 'point-cloud') return 'point-cloud'
  if (key === 'surface-planes') return 'planes'
  if (key === 'video' || key === 'rgb' || key === 'depth') return 'video'
  if (key === 'worldgen') return 'worldgen'
  return 'analysis'
}

export default function ProjectExplorer({
  project,
  selectedRecordingId,
  filter,
  onFilterChange,
  onOpenProject,
  onOpenFiles,
  onSelectRecording,
  onOpenAnalysis,
}: ProjectExplorerProps) {
  const normalizedFilter = filter.trim().toLowerCase()
  const recordings = (project?.recordings ?? []).filter((recording) => {
    if (!normalizedFilter) return true
    return (
      recording.name.toLowerCase().includes(normalizedFilter) ||
      recording.relativePath.toLowerCase().includes(normalizedFilter) ||
      recording.analyses.some((analysis) => analysis.title.toLowerCase().includes(normalizedFilter))
    )
  })

  return (
    <aside className="project-explorer">
      <div className="panel-header">
        <div>
          <div className="eyebrow">Explorer</div>
          <h2>Project</h2>
        </div>
        <button type="button" className="icon-button" onClick={onOpenProject} title="Open project">
          <FolderOpen size={15} aria-hidden="true" />
        </button>
      </div>

      <label className="search-box">
        <Search size={14} aria-hidden="true" />
        <input
          value={filter}
          onChange={(event) => onFilterChange(event.target.value)}
          placeholder="Filter recordings"
        />
      </label>

      {project ? (
        <div className="project-summary">
          <div className="project-name">{project.name}</div>
          <div className="project-path" title={project.rootPath}>
            {shortPath(project.rootPath)}
          </div>
          <div className="project-count">
            {project.recordings.length} .vis.pb {project.recordings.length === 1 ? 'file' : 'files'}
          </div>
        </div>
      ) : (
        <div className="empty-project-group">
          <button type="button" className="empty-project" onClick={onOpenProject}>
            <Database size={18} aria-hidden="true" />
            <span>Open a project folder</span>
          </button>
          <button type="button" className="empty-project" onClick={onOpenFiles}>
            <FileCode2 size={18} aria-hidden="true" />
            <span>Open .vis.pb files</span>
          </button>
        </div>
      )}

      <div className="recording-list" aria-label="Recordings">
        {recordings.map((recording) => {
          const selected = recording.id === selectedRecordingId
          return (
            <section className={selected ? 'recording-item is-selected' : 'recording-item'} key={recording.id}>
              <button
                type="button"
                className="recording-main"
                onClick={() => onSelectRecording(recording)}
                title={recording.path}
              >
                <ChevronRight size={14} aria-hidden="true" />
                <span className="recording-name">{recording.name}</span>
                <span className="recording-size">{recording.sizeLabel}</span>
              </button>
              <div className="recording-meta">
                <span>{dateTimeLabel(recording.modifiedMs)}</span>
                <span title={recording.relativePath}>{shortPath(recording.relativePath, 46)}</span>
              </div>
              <div className="analysis-list">
                {recording.analyses.map((analysis) => {
                  const Icon = iconForAnalysis(analysis.key)
                  return (
                    <button
                      type="button"
                      className={analysis.source === 'vis' ? 'analysis-chip is-native' : 'analysis-chip'}
                      key={`${recording.id}:${analysis.key}:${analysis.path}`}
                      onClick={() => onOpenAnalysis(recording, analysis)}
                      title={analysis.relativePath}
                    >
                      <Icon size={13} aria-hidden="true" />
                      <span>{analysis.title}</span>
                    </button>
                  )
                })}
              </div>
            </section>
          )
        })}
      </div>
    </aside>
  )
}
