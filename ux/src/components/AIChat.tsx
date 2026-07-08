import { CornerDownLeft, Cpu, Sparkles, Terminal, UserRound } from 'lucide-react'
import { FormEvent, KeyboardEvent, useMemo, useState } from 'react'
import type { RecordingEntry, VideoMarker, VisSummary } from '../types'
import { compactNumber, secondsLabel } from '../lib/format'
import { isCommand, type CommandResult } from '../lib/overlay'

type ChatMessage = {
  id: string
  role: 'assistant' | 'user' | 'command'
  text: string
}

type AIChatProps = {
  selectedRecording: RecordingEntry | null
  summary: VisSummary | null
  markers: VideoMarker[]
  onRunCommand: (text: string) => Promise<CommandResult>
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

export default function AIChat({ selectedRecording, summary, markers, onRunCommand }: AIChatProps) {
  const [draft, setDraft] = useState('')
  const [messages, setMessages] = useState<ChatMessage[]>([
    {
      id: 'seed',
      role: 'assistant',
      text: 'Load a recording and ask about the scene, geometry, or available analysis artifacts.',
    },
  ])

  const context = useMemo(() => {
    const markerContext = markerLine(markers)
    return markerContext ? `${summaryLine(summary)} ${markerContext}` : summaryLine(summary)
  }, [markers, summary])

  const submitDraft = async () => {
    const text = draft.trim()
    if (!text) return
    setDraft('')

    // Commands (e.g. "/segmentation") are intercepted and run against the
    // workspace instead of being sent to the assistant.
    if (isCommand(text)) {
      const result = await onRunCommand(text)
      setMessages((current) => [
        ...current,
        { id: `user-${Date.now()}`, role: 'user', text },
        { id: `command-${Date.now()}`, role: 'command', text: result.message },
      ])
      return
    }

    const userMessage: ChatMessage = {
      id: `user-${Date.now()}`,
      role: 'user',
      text,
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
        <div>
          <div className="eyebrow">AI</div>
          <h2>Chat</h2>
        </div>
        <div className="context-token" title={selectedRecording?.path ?? 'No recording selected'}>
          <Cpu size={14} aria-hidden="true" />
          <span>{selectedRecording?.fileStem ?? 'No file'}</span>
        </div>
      </div>

      <div className="chat-context">
        <Sparkles size={14} aria-hidden="true" />
        <span>{context}</span>
      </div>

      <div className="message-list" aria-live="polite">
        {messages.map((message) => {
          const Icon = message.role === 'command' ? Terminal : message.role === 'assistant' ? Sparkles : UserRound
          return (
            <article className={`chat-message ${message.role}`} key={message.id}>
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
