import {
  ChevronRight,
  Database,
  FileCode2,
  FolderOpen,
  Pencil,
  Plus,
  Search,
  Trash2,
  X,
} from 'lucide-react'
import { KeyboardEvent, useState } from 'react'
import type {
  ProjectScanResult,
  RecordingEntry,
  VideoChatWorkspace,
  WorkspaceChatSession,
} from '../types'
import { dateTimeLabel, shortPath } from '../lib/format'
import {
  recordingDisplayName,
  recordingTimestampMs,
  recordingVideoId,
} from '../lib/recordingNames'

type ProjectExplorerProps = {
  project: ProjectScanResult | null
  selectedRecordingId?: string
  selectedChatId?: string
  chatWorkspaces: Record<string, VideoChatWorkspace>
  filter: string
  onFilterChange: (value: string) => void
  onOpenProject: () => void
  onOpenFiles: () => void
  onCreateProject: () => void
  creatingProject?: boolean
  onSelectRecording: (recording: RecordingEntry) => void
  onRenameProject: (recording: RecordingEntry, title: string) => void
  onSelectChat: (recording: RecordingEntry, chat: WorkspaceChatSession) => void
  onCreateChat: (recording: RecordingEntry) => void
  onRenameChat: (recording: RecordingEntry, chat: WorkspaceChatSession, title: string) => void
  onDeleteChat: (recording: RecordingEntry, chat: WorkspaceChatSession) => void
  onCloseRecording: (recording: RecordingEntry) => void
}

export default function ProjectExplorer({
  project,
  selectedRecordingId,
  selectedChatId,
  chatWorkspaces,
  filter,
  onFilterChange,
  onOpenProject,
  onOpenFiles,
  onCreateProject,
  creatingProject = false,
  onSelectRecording,
  onRenameProject,
  onSelectChat,
  onCreateChat,
  onRenameChat,
  onDeleteChat,
  onCloseRecording,
}: ProjectExplorerProps) {
  const [editingChatKey, setEditingChatKey] = useState<string | null>(null)
  const [titleDraft, setTitleDraft] = useState('')
  const [editingProjectId, setEditingProjectId] = useState<string | null>(null)
  const [projectTitleDraft, setProjectTitleDraft] = useState('')
  const normalizedFilter = filter.trim().toLowerCase()
  const recordings = (project?.recordings ?? []).filter((recording) => {
    if (!normalizedFilter) return true
    const workspace = chatWorkspaces[recordingVideoId(recording)]
    return (
      recordingDisplayName(recording).toLowerCase().includes(normalizedFilter) ||
      recording.relativePath.toLowerCase().includes(normalizedFilter) ||
      workspace?.chats.some((chat) => chat.title.toLowerCase().includes(normalizedFilter)) ||
      recording.analyses.some((analysis) => analysis.title.toLowerCase().includes(normalizedFilter))
    )
  })

  const beginRename = (
    recording: RecordingEntry,
    chat: WorkspaceChatSession,
  ) => {
    setEditingChatKey(`${recording.id}:${chat.id}`)
    setTitleDraft(chat.title)
  }

  const beginProjectRename = (recording: RecordingEntry) => {
    setEditingProjectId(recording.id)
    setProjectTitleDraft(recordingDisplayName(recording))
  }

  const finishProjectRename = (recording: RecordingEntry) => {
    const title = projectTitleDraft.trim()
    setEditingProjectId(null)
    if (title && title !== recordingDisplayName(recording)) onRenameProject(recording, title)
  }

  const handleProjectRenameKeyDown = (
    event: KeyboardEvent<HTMLInputElement>,
    recording: RecordingEntry,
  ) => {
    if (event.key === 'Enter') {
      event.preventDefault()
      finishProjectRename(recording)
    } else if (event.key === 'Escape') {
      event.preventDefault()
      setProjectTitleDraft(recordingDisplayName(recording))
      setEditingProjectId(null)
    }
  }

  const finishRename = (
    recording: RecordingEntry,
    chat: WorkspaceChatSession,
  ) => {
    const title = titleDraft.trim()
    setEditingChatKey(null)
    if (title && title !== chat.title) onRenameChat(recording, chat, title)
  }

  const handleRenameKeyDown = (
    event: KeyboardEvent<HTMLInputElement>,
    recording: RecordingEntry,
    chat: WorkspaceChatSession,
  ) => {
    if (event.key === 'Enter') {
      event.preventDefault()
      finishRename(recording, chat)
    } else if (event.key === 'Escape') {
      event.preventDefault()
      setTitleDraft(chat.title)
      setEditingChatKey(null)
    }
  }

  return (
    <aside className="project-explorer">
      <div className="panel-header">
        <h2>Project</h2>
        <div className="project-header-actions">
          <button
            type="button"
            className="icon-button"
            onClick={onCreateProject}
            title="Create a new project"
            aria-label="Create a new project"
            disabled={creatingProject}
          >
            <Plus size={15} aria-hidden="true" />
          </button>
          <button type="button" className="icon-button" onClick={onOpenProject} title="Open project">
            <FolderOpen size={15} aria-hidden="true" />
          </button>
        </div>
      </div>

      <label className="search-box">
        <Search size={14} aria-hidden="true" />
        <input
          value={filter}
          onChange={(event) => onFilterChange(event.target.value)}
          placeholder="Filter recordings"
        />
      </label>

      {!project ? (
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
      ) : null}

      <div className="recording-list" aria-label="Recordings">
        {recordings.map((recording) => {
          const selected = recording.id === selectedRecordingId
          const videoId = recordingVideoId(recording)
          const workspace = chatWorkspaces[videoId]
          const displayName = recordingDisplayName(recording)
          return (
            <section className={selected ? 'recording-item is-selected' : 'recording-item'} key={recording.id}>
              <div className="recording-heading">
                {editingProjectId === recording.id ? (
                  <input
                    className="recording-project-title-input"
                    value={projectTitleDraft}
                    onChange={(event) => setProjectTitleDraft(event.target.value)}
                    onBlur={() => finishProjectRename(recording)}
                    onKeyDown={(event) => handleProjectRenameKeyDown(event, recording)}
                    aria-label="Project name"
                    autoFocus
                  />
                ) : (
                  <button
                    type="button"
                    className="recording-main"
                    onClick={() => onSelectRecording(recording)}
                    title={recording.path}
                  >
                    <ChevronRight className={selected ? 'is-open' : ''} size={14} aria-hidden="true" />
                    <span className="recording-name">{displayName}</span>
                    <span className="recording-size">{recording.sizeLabel}</span>
                  </button>
                )}
                <div className="recording-actions">
                  <button
                    type="button"
                    className="recording-action"
                    onClick={() => beginProjectRename(recording)}
                    title={`Rename ${displayName}`}
                    aria-label={`Rename ${displayName}`}
                  >
                    <Pencil size={13} aria-hidden="true" />
                  </button>
                  <button
                    type="button"
                    className="recording-action"
                    onClick={() => onCreateChat(recording)}
                    title={`Create a new chat for ${displayName}`}
                    aria-label={`Create a new chat for ${displayName}`}
                  >
                    <Plus size={14} aria-hidden="true" />
                  </button>
                  <button
                    type="button"
                    className="recording-action"
                    onClick={() => onCloseRecording(recording)}
                    title={`Remove ${displayName} from this workspace`}
                    aria-label={`Remove ${displayName} from this workspace`}
                  >
                    <X size={14} aria-hidden="true" />
                  </button>
                </div>
              </div>
              <div className="recording-meta">
                <span>{dateTimeLabel(recordingTimestampMs(recording))}</span>
                <span title={recording.relativePath}>{shortPath(recording.relativePath, 46)}</span>
              </div>
              <div className="recording-chat-list" aria-label={`${displayName} chats`}>
                {(workspace?.chats ?? []).map((chat) => {
                  const chatKey = `${recording.id}:${chat.id}`
                  const active = selected && chat.id === selectedChatId
                  return (
                    <div className={active ? 'recording-chat is-active' : 'recording-chat'} key={chatKey}>
                      {editingChatKey === chatKey ? (
                        <input
                          className="recording-chat-title-input"
                          value={titleDraft}
                          onChange={(event) => setTitleDraft(event.target.value)}
                          onBlur={() => finishRename(recording, chat)}
                          onKeyDown={(event) => handleRenameKeyDown(event, recording, chat)}
                          aria-label="Chat title"
                          autoFocus
                        />
                      ) : (
                        <>
                          <button
                            type="button"
                            className="recording-chat-select"
                            onClick={() => onSelectChat(recording, chat)}
                            onDoubleClick={() => beginRename(recording, chat)}
                            title={chat.title}
                          >
                            <span>{chat.title}</span>
                          </button>
                          <button
                            type="button"
                            className="recording-chat-rename"
                            onClick={() => beginRename(recording, chat)}
                            title="Rename chat"
                            aria-label={`Rename ${chat.title}`}
                          >
                            <Pencil size={12} aria-hidden="true" />
                          </button>
                          <button
                            type="button"
                            className="recording-chat-delete"
                            onClick={() => {
                              if (window.confirm(`Delete “${chat.title}”? This cannot be undone.`)) {
                                if (editingChatKey === chatKey) setEditingChatKey(null)
                                onDeleteChat(recording, chat)
                              }
                            }}
                            title="Delete chat"
                            aria-label={`Delete ${chat.title}`}
                          >
                            <Trash2 size={12} aria-hidden="true" />
                          </button>
                        </>
                      )}
                    </div>
                  )
                })}
                {!workspace ? <span className="recording-chat-loading">Loading chats…</span> : null}
              </div>
            </section>
          )
        })}
      </div>
    </aside>
  )
}
