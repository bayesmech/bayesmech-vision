import { Bot, FileCode2, FolderOpen, PanelLeft, RefreshCw } from 'lucide-react'

type TopMenuProps = {
  projectName?: string
  loading: boolean
  runtimeLabel: string
  onOpenProject: () => void
  onOpenFiles: () => void
  onRescanProject: () => void
  onOpenView: () => void
  onOpenAnalysis: () => void
}

export default function TopMenu({
  projectName,
  loading,
  runtimeLabel,
  onOpenProject,
  onOpenFiles,
  onRescanProject,
  onOpenView,
  onOpenAnalysis,
}: TopMenuProps) {
  return (
    <header className="top-menu">
      <div className="app-mark">
        <img className="app-logo" src="/logo.png" alt="" aria-hidden="true" />
        <span>BayesMech Vision</span>
      </div>

      <nav className="menu-strip" aria-label="Application menu">
        <button type="button" className="menu-item" onClick={onOpenProject}>
          File
        </button>
        <button type="button" className="menu-item" onClick={onOpenView}>
          View
        </button>
        <button type="button" className="menu-item" onClick={onOpenAnalysis}>
          Analysis
        </button>
      </nav>

      <div className="top-actions">
        <button
          type="button"
          className="toolbar-button"
          onClick={onOpenProject}
          disabled={loading}
          title="Open project"
        >
          <FolderOpen size={15} aria-hidden="true" />
          <span>Project</span>
        </button>
        <button
          type="button"
          className="toolbar-button"
          onClick={onOpenFiles}
          disabled={loading}
          title="Open .vis.pb files"
        >
          <FileCode2 size={15} aria-hidden="true" />
          <span>Files</span>
        </button>
        <button
          type="button"
          className="icon-button"
          onClick={onRescanProject}
          disabled={loading || !projectName}
          title="Rescan project"
        >
          <RefreshCw size={15} aria-hidden="true" />
        </button>
        <div className="project-pill" title={projectName ?? 'No project loaded'}>
          <PanelLeft size={14} aria-hidden="true" />
          <span>{projectName ?? 'No project'}</span>
        </div>
        <div className="bridge-state is-ready">
          <Bot size={14} aria-hidden="true" />
          <span>{runtimeLabel}</span>
        </div>
      </div>
    </header>
  )
}
