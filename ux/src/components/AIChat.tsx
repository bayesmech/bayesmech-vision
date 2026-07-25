import { CircleAlert, CornerDownLeft, Cpu, LoaderCircle, Sparkles, Terminal, UserRound } from 'lucide-react'
import { FormEvent, KeyboardEvent, useEffect, useMemo, useRef, useState } from 'react'
import type { ChatThread, RecordingEntry, VideoMarker, VisSummary } from '../types'
import { compactNumber, secondsLabel } from '../lib/format'
import { isCommand, type CommandProgress, type CommandResult } from '../lib/overlay'

type ChatMessage = {
  id: string
  role: 'assistant' | 'user' | 'command'
  text: string
  status?: 'pending' | 'ok' | 'error'
}

type AIChatProps = {
  selectedRecording: RecordingEntry | null
  summary: VisSummary | null
  markers: VideoMarker[]
  savedThread: ChatThread | null
  savedThreadLoading: boolean
  savedThreadError: string | null
  onRunCommand: (text: string, onProgress?: (progress: CommandProgress) => void) => Promise<CommandResult>
}

function summaryLine(summary: VisSummary | null): string {
  if (!summary) return 'No recording loaded.'
  return `${compactNumber(summary.frameCount)} frames, ${secondsLabel(summary.durationSeconds)}, ${compactNumber(summary.sampledPointCount)} sampled point observations, ${compactNumber(summary.sampledPlaneCount)} sampled surface observations.`
}

function markerLine(markers: VideoMarker[]): string {
  if (markers.length === 0) return ''
  const sorted = [...markers].sort((a, b) => a.frameIndex - b.frameIndex)
  const markerRefs = sorted.map((marker) => `@${marker.reference} ${secondsLabel(marker.seconds)}`)
  const segmentRefs = sorted
    .slice(0, -1)
    .map((marker, index) => `@${marker.reference}-@${sorted[index + 1].reference}`)
  return `Markers: ${markerRefs.join(', ')}.${segmentRefs.length ? ` Segments: ${segmentRefs.join(', ')}.` : ''}`
}

export default function AIChat({
  selectedRecording,
  summary,
  markers,
  savedThread,
  savedThreadLoading,
  savedThreadError,
  onRunCommand,
}: AIChatProps) {
  const [draft, setDraft] = useState('')
  const messageListRef = useRef<HTMLDivElement>(null)
  const [messages, setMessages] = useState<ChatMessage[]>([])

  const context = useMemo(() => {
    const markerContext = markerLine(markers)
    return markerContext ? `${summaryLine(summary)} ${markerContext}` : summaryLine(summary)
  }, [markers, summary])

  const savedMessages = useMemo<ChatMessage[]>(() => (savedThread?.turns ?? []).map((turn, index) => ({
    id: `saved-${turn.timestampNs}-${index}`,
    role: turn.role,
    text: turn.text,
  })), [savedThread])
  const visibleMessages = useMemo(() => [...savedMessages, ...messages], [messages, savedMessages])

  useEffect(() => {
    setDraft('')
    setMessages([])
  }, [selectedRecording?.id])

  useEffect(() => {
    const list = messageListRef.current
    if (!list) return
    list.scrollTop = list.scrollHeight
  }, [savedThread, savedThreadLoading, visibleMessages])

  const submitDraft = async () => {
    const text = draft.trim()
    if (!text) return
    setDraft('')

    const userMessage: ChatMessage = {
      id: `user-${Date.now()}`,
      role: 'user',
      text,
    }

    // Commands (e.g. "/segmentation") are intercepted and run against the
    // workspace instead of being sent to the assistant.
    if (isCommand(text)) {
      const commandId = `command-${Date.now()}`
      let activeCommandId = commandId
      setMessages((current) => [
        ...current,
        userMessage,
        { id: commandId, role: 'command', status: 'pending', text: `Running ${text.split(/\s+/)[0]}...` },
      ])
      const handleProgress = (progress: CommandProgress) => {
        const status = progress.ok === false ? 'error' : progress.ok === true ? 'ok' : 'pending'
        if (progress.append) {
          const nextId = `command-${Date.now()}-${Math.random().toString(36).slice(2, 6)}`
          activeCommandId = nextId
          setMessages((current) => [
            ...current,
            { id: nextId, role: 'command', status, text: progress.message },
          ])
          return
        }
        setMessages((current) => current.map((message) => (
          message.id === activeCommandId
            ? { ...message, status, text: progress.message }
            : message
        )))
      }
      try {
        const result = await onRunCommand(text, handleProgress)
        setMessages((current) => current.map((message) => (
          message.id === activeCommandId
            ? { ...message, status: result.ok ? 'ok' : 'error', text: result.message }
            : message
        )))
      } catch (err) {
        const message = err instanceof Error ? err.message : 'Command failed.'
        setMessages((current) => current.map((item) => (
          item.id === activeCommandId
            ? { ...item, status: 'error', text: message }
            : item
        )))
      }
      return
    }

    const assistantMessage: ChatMessage = {
      id: `assistant-${Date.now()}`,
      role: 'assistant',
      text: selectedRecording
        ? `Current recording: ${selectedRecording.name}. ${context}`
        : 'No project recording is selected yet.',
    }
    setMessages((current) => [...current, userMessage, assistantMessage])
  }

  const handleSubmit = (event: FormEvent) => {
    event.preventDefault()
    void submitDraft()
  }

  const handleKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.key !== 'Enter') return
    if (event.ctrlKey) return
    event.preventDefault()
    void submitDraft()
  }

  return (
    <section className="chat-panel">
      <div className="panel-header">
        <h2>Chat</h2>
        <div className="context-token" title={selectedRecording?.path ?? 'No recording selected'}>
          <Cpu size={14} aria-hidden="true" />
          <span>{selectedRecording?.fileStem ?? 'No file'}</span>
        </div>
      </div>

      <div className="message-list" ref={messageListRef} aria-live="polite">
        {savedThreadLoading ? (
          <div className="chat-thread-state">
            <LoaderCircle className="spin" size={15} aria-hidden="true" />
            <span>Loading saved analysis and chat…</span>
          </div>
        ) : null}
        {savedThreadError ? (
          <div className="chat-thread-state is-error">
            <CircleAlert size={15} aria-hidden="true" />
            <span>{savedThreadError}</span>
          </div>
        ) : null}
        {savedThread?.analysis ? (
          <article className="chat-analysis-section">
            <header>
              <span className="chat-analysis-mark">
                <Sparkles size={14} aria-hidden="true" />
                Genspark analysis
              </span>
              <span>Initial context</span>
            </header>
            <h3>{savedThread.analysis.title}</h3>
            {savedThread.analysis.text ? <p>{savedThread.analysis.text}</p> : null}
            {savedThread.analysis.parameters.length ? (
              <div className="chat-analysis-parameters">
                {savedThread.analysis.parameters.map((parameter, index) => (
                  <div key={`${parameter.name}-${index}`}>
                    <span>{parameter.name}</span>
                    <strong>
                      {parameter.value}
                      {parameter.unit ? <small> {parameter.unit}</small> : null}
                    </strong>
                  </div>
                ))}
              </div>
            ) : null}
          </article>
        ) : null}
        {visibleMessages.map((message) => {
          const Icon = message.status === 'pending'
            ? LoaderCircle
            : message.status === 'error'
              ? CircleAlert
              : message.role === 'command'
                ? Terminal
                : message.role === 'assistant'
                  ? Sparkles
                  : UserRound
          return (
            <article className={`chat-message ${message.role}${message.status ? ` ${message.status}` : ''}`} key={message.id}>
              <div className="message-avatar">
                <Icon size={14} aria-hidden="true" />
              </div>
              <p>{message.text}</p>
            </article>
          )
        })}
      </div>

      <form className="chat-input" onSubmit={handleSubmit}>
        <textarea
          value={draft}
          onChange={(event) => setDraft(event.target.value)}
          onKeyDown={handleKeyDown}
          placeholder="Ask about this recording, /segmentation, or /worldgen @MarkerA-@MarkerB"
          rows={3}
        />
        <button type="submit" className="send-button" title="Send message">
          <CornerDownLeft size={16} aria-hidden="true" />
        </button>
      </form>
    </section>
  )
}
