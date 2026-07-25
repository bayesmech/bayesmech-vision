import {
  Bot,
  Code2,
  FileCode2,
  FolderOpen,
  Maximize2,
  Minus,
  PanelLeft,
  RefreshCw,
  RotateCcw,
  X,
  ZoomIn,
  ZoomOut,
} from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import type { WindowAction } from '../types'

type MenuName = 'file' | 'view' | 'window'

type TopMenuProps = {
  projectName?: string
  loading: boolean
  runtimeLabel: string
  onOpenProject: () => void
  onOpenFiles: () => void
  onRescanProject: () => void
  onWindowAction: (action: WindowAction) => void
}

export default function TopMenu({
  projectName,
  loading,
  runtimeLabel,
  onOpenProject,
  onOpenFiles,
  onRescanProject,
  onWindowAction,
}: TopMenuProps) {
  const [openMenu, setOpenMenu] = useState<MenuName | null>(null)
  const menuStripRef = useRef<HTMLElement>(null)

  useEffect(() => {
    if (!openMenu) return

    const closeOnOutsideClick = (event: MouseEvent) => {
      if (!menuStripRef.current?.contains(event.target as Node)) setOpenMenu(null)
    }
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setOpenMenu(null)
    }

    document.addEventListener('mousedown', closeOnOutsideClick)
    document.addEventListener('keydown', closeOnEscape)
    return () => {
      document.removeEventListener('mousedown', closeOnOutsideClick)
      document.removeEventListener('keydown', closeOnEscape)
    }
  }, [openMenu])

  useEffect(() => {
    const handleShortcut = (event: KeyboardEvent) => {
      if (!(event.ctrlKey || event.metaKey) || event.altKey) return
      if (event.key.toLowerCase() === 'o') {
        event.preventDefault()
        if (loading) return
        if (event.shiftKey) onOpenFiles()
        else onOpenProject()
      }
    }
    document.addEventListener('keydown', handleShortcut)
    return () => document.removeEventListener('keydown', handleShortcut)
  }, [loading, onOpenFiles, onOpenProject])

  const toggleMenu = (menu: MenuName) => {
    setOpenMenu((current) => (current === menu ? null : menu))
  }

  const run = (action: () => void) => {
    setOpenMenu(null)
    action()
  }

  const windowAction = (action: WindowAction) => {
    run(() => onWindowAction(action))
  }

  return (
    <header className="top-menu">
      <div className="app-mark">
        <img className="app-logo" src="/logo.png" alt="" aria-hidden="true" />
        <span>BayesMech Vision</span>
      </div>

      <nav className="menu-strip" aria-label="Application menu" ref={menuStripRef}>
        <div className="menu-dropdown">
          <button
            type="button"
            className={openMenu === 'file' ? 'menu-item is-open' : 'menu-item'}
            aria-haspopup="menu"
            aria-expanded={openMenu === 'file'}
            onClick={() => toggleMenu('file')}
          >
            File
          </button>
          {openMenu === 'file' ? (
            <div className="menu-popover" role="menu">
              <button type="button" role="menuitem" onClick={() => run(onOpenProject)} disabled={loading}>
                <FolderOpen size={14} aria-hidden="true" />
                <span>Open project…</span>
                <kbd>Ctrl O</kbd>
              </button>
              <button type="button" role="menuitem" onClick={() => run(onOpenFiles)} disabled={loading}>
                <FileCode2 size={14} aria-hidden="true" />
                <span>Open recording files…</span>
                <kbd>Ctrl Shift O</kbd>
              </button>
              <div className="menu-separator" role="separator" />
              <button
                type="button"
                role="menuitem"
                onClick={() => run(onRescanProject)}
                disabled={loading || !projectName}
              >
                <RefreshCw size={14} aria-hidden="true" />
                <span>Rescan workspace</span>
              </button>
            </div>
          ) : null}
        </div>

        <div className="menu-dropdown">
          <button
            type="button"
            className={openMenu === 'view' ? 'menu-item is-open' : 'menu-item'}
            aria-haspopup="menu"
            aria-expanded={openMenu === 'view'}
            onClick={() => toggleMenu('view')}
          >
            View
          </button>
          {openMenu === 'view' ? (
            <div className="menu-popover" role="menu">
              <button type="button" role="menuitem" onClick={() => windowAction('reload')}>
                <RefreshCw size={14} aria-hidden="true" />
                <span>Reload</span>
              </button>
              <button type="button" role="menuitem" onClick={() => windowAction('reset-zoom')}>
                <RotateCcw size={14} aria-hidden="true" />
                <span>Actual size</span>
              </button>
              <button type="button" role="menuitem" onClick={() => windowAction('zoom-in')}>
                <ZoomIn size={14} aria-hidden="true" />
                <span>Zoom in</span>
              </button>
              <button type="button" role="menuitem" onClick={() => windowAction('zoom-out')}>
                <ZoomOut size={14} aria-hidden="true" />
                <span>Zoom out</span>
              </button>
              <div className="menu-separator" role="separator" />
              <button type="button" role="menuitem" onClick={() => windowAction('toggle-fullscreen')}>
                <Maximize2 size={14} aria-hidden="true" />
                <span>Toggle full screen</span>
              </button>
              <button type="button" role="menuitem" onClick={() => windowAction('toggle-devtools')}>
                <Code2 size={14} aria-hidden="true" />
                <span>Developer tools</span>
              </button>
            </div>
          ) : null}
        </div>

        <div className="menu-dropdown">
          <button
            type="button"
            className={openMenu === 'window' ? 'menu-item is-open' : 'menu-item'}
            aria-haspopup="menu"
            aria-expanded={openMenu === 'window'}
            onClick={() => toggleMenu('window')}
          >
            Window
          </button>
          {openMenu === 'window' ? (
            <div className="menu-popover" role="menu">
              <button type="button" role="menuitem" onClick={() => windowAction('minimize')}>
                <Minus size={14} aria-hidden="true" />
                <span>Minimize</span>
              </button>
              <button type="button" role="menuitem" onClick={() => windowAction('close')}>
                <X size={14} aria-hidden="true" />
                <span>Close window</span>
              </button>
            </div>
          ) : null}
        </div>
      </nav>

      <div className="top-actions">
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
