import {
  Activity,
  ChevronDown,
  ChevronUp,
  CircleAlert,
  CircleCheck,
  CircleX,
  CornerDownLeft,
  Cpu,
  LoaderCircle,
  Sparkles,
  Terminal,
  UserRound,
} from 'lucide-react'
import { FormEvent, KeyboardEvent, useEffect, useMemo, useRef, useState } from 'react'
import type {
  ChatAnalysis,
  RecordingEntry,
  RunnerBackgroundJob,
  VideoMarker,
  VisSummary,
  WorkspaceChatMessage,
  WorkspaceChatSession,
} from '../types'
import { compactNumber, secondsLabel } from '../lib/format'
import { isCommand, type CommandProgress, type CommandResult } from '../lib/overlay'
import { recordingDisplayName } from '../lib/recordingNames'

type AIChatProps = {
  selectedRecording: RecordingEntry | null
  summary: VisSummary | null
  markers: VideoMarker[]
  analysis: ChatAnalysis | null
  chatSession: WorkspaceChatSession | null
  chatLoading: boolean
  chatError: string | null
  backgroundJobs: RunnerBackgroundJob[]
  onMessagesChange: (messages: WorkspaceChatMessage[]) => void
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

function jobTimestamp(value: string | number): number {
  if (typeof value === 'number') return value < 1_000_000_000_000 ? value * 1000 : value
  const parsed = new Date(value).getTime()
  return Number.isFinite(parsed) ? parsed : 0
}

export default function AIChat({
  selectedRecording,
  summary,
  markers,
  analysis,
  chatSession,
  chatLoading,
  chatError,
  backgroundJobs,
  onMessagesChange,
  onRunCommand,
}: AIChatProps) {
  const [draft, setDraft] = useState('')
  const [jobsExpanded, setJobsExpanded] = useState(false)
  const messageListRef = useRef<HTMLDivElement>(null)
  const [messages, setMessages] = useState<WorkspaceChatMessage[]>([])

  const context = useMemo(() => {
    const markerContext = markerLine(markers)
    return markerContext ? `${summaryLine(summary)} ${markerContext}` : summaryLine(summary)
  }, [markers, summary])

  const orderedJobs = useMemo(() => {
    const active = (job: RunnerBackgroundJob) => !['complete', 'succeeded', 'failed', 'cancelled'].includes(job.status)
    return [...backgroundJobs]
      .sort((left, right) => {
        const activeDifference = Number(active(right)) - Number(active(left))
        if (activeDifference) return activeDifference
        return jobTimestamp(right.updatedAt || right.createdAt || 0)
          - jobTimestamp(left.updatedAt || left.createdAt || 0)
      })
      .slice(0, 24)
  }, [backgroundJobs])
  const activeJobCount = orderedJobs.filter(
    (job) => !['complete', 'succeeded', 'failed', 'cancelled'].includes(job.status),
  ).length

  useEffect(() => {
    if (activeJobCount > 0) setJobsExpanded(true)
  }, [activeJobCount])

  useEffect(() => {
    setDraft('')
    setMessages(chatSession?.messages ?? [])
  }, [chatSession?.id])

  useEffect(() => {
    const list = messageListRef.current
    if (!list) return
    list.scrollTop = list.scrollHeight
  }, [analysis, chatLoading, messages])

  const updateMessages = (
    update: (current: WorkspaceChatMessage[]) => WorkspaceChatMessage[],
  ) => {
    setMessages((current) => {
      const next = update(current)
      onMessagesChange(next)
      return next
    })
  }

  const submitDraft = async () => {
    const text = draft.trim()
    if (!text || !chatSession) return
    setDraft('')
    const createdAt = new Date().toISOString()

    const userMessage: WorkspaceChatMessage = {
      id: `user-${Date.now()}`,
      role: 'user',
      text,
      createdAt,
    }

    // Commands (e.g. "/segmentation") are intercepted and run against the
    // workspace instead of being sent to the assistant.
    if (isCommand(text)) {
      const commandId = `command-${Date.now()}`
      let activeCommandId = commandId
      updateMessages((current) => [
        ...current,
        userMessage,
        {
          id: commandId,
          role: 'command',
          status: 'pending',
          text: `Running ${text.split(/\s+/)[0]}...`,
          createdAt,
        },
      ])
      const handleProgress = (progress: CommandProgress) => {
        const status = progress.ok === false ? 'error' : progress.ok === true ? 'ok' : 'pending'
        if (progress.append) {
          const nextId = `command-${Date.now()}-${Math.random().toString(36).slice(2, 6)}`
          activeCommandId = nextId
          updateMessages((current) => [
            ...current,
            {
              id: nextId,
              role: 'command',
              status,
              text: progress.message,
              createdAt: new Date().toISOString(),
            },
          ])
          return
        }
        updateMessages((current) => current.map((message) => (
          message.id === activeCommandId
            ? { ...message, status, text: progress.message }
            : message
        )))
      }
      try {
        const result = await onRunCommand(text, handleProgress)
        updateMessages((current) => current.map((message) => (
          message.id === activeCommandId
            ? { ...message, status: result.ok ? 'ok' : 'error', text: result.message }
            : message
        )))
      } catch (err) {
        const message = err instanceof Error ? err.message : 'Command failed.'
        updateMessages((current) => current.map((item) => (
          item.id === activeCommandId
            ? { ...item, status: 'error', text: message }
            : item
        )))
      }
      return
    }

    const assistantMessage: WorkspaceChatMessage = {
      id: `assistant-${Date.now()}`,
      role: 'assistant',
      text: selectedRecording
        ? `Current recording: ${recordingDisplayName(selectedRecording)}. ${context}`
        : 'No project recording is selected yet.',
      createdAt: new Date().toISOString(),
    }
    updateMessages((current) => [...current, userMessage, assistantMessage])
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
          <span>{chatSession?.title ?? 'No chat'}</span>
        </div>
      </div>

      <div className="message-list" ref={messageListRef} aria-live="polite">
        {chatLoading ? (
          <div className="chat-thread-state">
            <LoaderCircle className="spin" size={15} aria-hidden="true" />
            <span>Loading saved chat…</span>
          </div>
        ) : null}
        {chatError ? (
          <div className="chat-thread-state is-error">
            <CircleAlert size={15} aria-hidden="true" />
            <span>{chatError}</span>
          </div>
        ) : null}
        {analysis ? (
          <article className="chat-analysis-section">
            <header>
              <span className="chat-analysis-mark">
                <Sparkles size={14} aria-hidden="true" />
                Genspark analysis
              </span>
              <span>Initial context</span>
            </header>
            <h3>{analysis.title}</h3>
            {analysis.text ? <p>{analysis.text}</p> : null}
            {analysis.parameters.length ? (
              <div className="chat-analysis-parameters">
                {analysis.parameters.map((parameter, index) => (
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
        {messages.map((message) => {
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

      {orderedJobs.length ? (
        <section className={`background-jobs-ribbon${jobsExpanded ? ' is-expanded' : ''}`}>
          <button
            type="button"
            className="background-jobs-toggle"
            aria-expanded={jobsExpanded}
            onClick={() => setJobsExpanded((current) => !current)}
          >
            <span>
              <Activity size={14} aria-hidden="true" />
              Background jobs
            </span>
            <span className="background-jobs-summary">
              {activeJobCount
                ? `${activeJobCount} running`
                : `${orderedJobs.filter((job) => ['complete', 'succeeded'].includes(job.status)).length} complete`}
              {jobsExpanded
                ? <ChevronDown size={14} aria-hidden="true" />
                : <ChevronUp size={14} aria-hidden="true" />}
            </span>
          </button>
          {jobsExpanded ? (
            <div className="background-jobs-list">
              {orderedJobs.map((job) => {
                const percent = Math.round(Math.max(0, Math.min(1, job.progress)) * 100)
                const complete = ['complete', 'succeeded'].includes(job.status)
                const failed = ['failed', 'cancelled'].includes(job.status)
                const StatusIcon = complete ? CircleCheck : failed ? CircleX : LoaderCircle
                const markerRange = job.markerStart && job.markerEnd
                  ? `@${job.markerStart}–@${job.markerEnd}`
                  : ''
                return (
                  <article
                    className={`background-job${complete ? ' is-complete' : ''}${failed ? ' is-failed' : ''}`}
                    key={job.jobId}
                  >
                    <div className="background-job-heading">
                      <StatusIcon className={!complete && !failed ? 'spin' : ''} size={13} aria-hidden="true" />
                      <strong>{job.title}</strong>
                      <span>{markerRange}</span>
                      <em>{percent}%</em>
                    </div>
                    <div className="background-job-progress" aria-label={`${job.title} ${percent}%`}>
                      <span style={{ width: `${percent}%` }} />
                    </div>
                    <div className="background-job-detail">
                      <span>{job.message || job.stage}</span>
                      <code title={job.jobId}>{job.jobId}</code>
                    </div>
                  </article>
                )
              })}
            </div>
          ) : null}
        </section>
      ) : null}

      <form className="chat-input" onSubmit={handleSubmit}>
        <textarea
          value={draft}
          onChange={(event) => setDraft(event.target.value)}
          onKeyDown={handleKeyDown}
          disabled={!chatSession}
          placeholder="Ask about this recording, /segmentation, or /worldgen @MarkerA-@MarkerB"
          rows={3}
        />
        <button type="submit" className="send-button" title="Send message" disabled={!chatSession}>
          <CornerDownLeft size={16} aria-hidden="true" />
        </button>
      </form>
    </section>
  )
}
