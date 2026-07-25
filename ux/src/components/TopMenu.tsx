import { Bot, FileCode2, FolderOpen, PanelLeft, RefreshCw } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'

type TopMenuProps = {
  projectName?: string
  loading: boolean
  runtimeLabel: string
  onOpenProject: () => void
  onOpenFiles: () => void
  onRescanProject: () => void
}

export default function TopMenu({
  projectName,
  loading,
  runtimeLabel,
  onOpenProject,
  onOpenFiles,
  onRescanProject,
}: TopMenuProps) {
  const [fileMenuOpen, setFileMenuOpen] = useState(false)
  const fileMenuRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!fileMenuOpen) return

    const closeOnOutsideClick = (event: MouseEvent) => {
      if (!fileMenuRef.current?.contains(event.target as Node)) {
        setFileMenuOpen(false)
      }
    }
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setFileMenuOpen(false)
    }

    document.addEventListener('mousedown', closeOnOutsideClick)
    document.addEventListener('keydown', closeOnEscape)
    return () => {
      document.removeEventListener('mousedown', closeOnOutsideClick)
      document.removeEventListener('keydown', closeOnEscape)
    }
  }, [fileMenuOpen])

  const openProject = () => {
    setFileMenuOpen(false)
    onOpenProject()
  }

  return (
    <header className="top-menu">
      <div className="app-mark">
        <img className="app-logo" src="/logo.png" alt="" aria-hidden="true" />
        <span>BayesMech Vision</span>
      </div>

      <nav className="menu-strip" aria-label="Application menu">
        <div className="menu-dropdown" ref={fileMenuRef}>
          <button
            type="button"
            className={fileMenuOpen ? 'menu-item is-open' : 'menu-item'}
            aria-haspopup="menu"
            aria-expanded={fileMenuOpen}
            onClick={() => setFileMenuOpen((open) => !open)}
          >
            File
          </button>
          {fileMenuOpen && (
            <div className="menu-popover" role="menu">
              <button type="button" role="menuitem" onClick={openProject} disabled={loading}>
                <FolderOpen size={14} aria-hidden="true" />
                <span>Open</span>
              </button>
            </div>
          )}
        </div>
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
