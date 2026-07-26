import {
  Activity,
  Bot,
  CarFront,
  ChevronDown,
  ChevronUp,
  CircleAlert,
  CircleCheck,
  CircleX,
  CornerDownLeft,
  Hand,
  LoaderCircle,
  MapPin,
  Mic,
  Plane,
  Smartphone,
  Sparkles,
  Terminal,
  UserRound,
} from 'lucide-react'
import {
  FormEvent,
  KeyboardEvent,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
} from 'react'
import type {
  ChatAnalysis,
  AgentChatResult,
  ControlDevicePreset,
  RecordingEntry,
  RunnerBackgroundJob,
  VideoMarker,
  VisSummary,
  WorkspaceChatMessage,
  WorkspaceChatSession,
} from '../types'
import { isCommand, type CommandProgress, type CommandResult } from '../lib/overlay'

type AIChatProps = {
  selectedRecording: RecordingEntry | null
  summary: VisSummary | null
  markers: VideoMarker[]
  analysis: ChatAnalysis | null
  chatSession: WorkspaceChatSession | null
  chatLoading: boolean
  chatError: string | null
  backgroundJobs: RunnerBackgroundJob[]
  addingDevice: boolean
  onAddDevice: (preset: ControlDevicePreset) => void
  onMessagesChange: (messages: WorkspaceChatMessage[]) => void
  onSendMessage: (
    text: string,
    history: WorkspaceChatMessage[],
  ) => Promise<AgentChatResult>
  onRunCommand: (text: string, onProgress?: (progress: CommandProgress) => void) => Promise<CommandResult>
}

type SuggestionContext = {
  kind: 'command' | 'marker'
  start: number
  end: number
  query: string
}

type ComposerSuggestion = {
  id: string
  label: string
  detail: string
  insertText: string
  kind: 'command' | 'marker'
}

const COMMAND_SUGGESTIONS: ComposerSuggestion[] = [
  {
    id: 'segmentation-show',
    label: '/segmentation',
    detail: 'Show the segmentation overlay',
    insertText: '/segmentation ',
    kind: 'command',
  },
  {
    id: 'segmentation-list',
    label: '/segmentation:list',
    detail: 'List available segmentation labels',
    insertText: '/segmentation:list ',
    kind: 'command',
  },
  {
    id: 'segmentation-mask',
    label: '/segmentation:mask',
    detail: 'Show only one segmentation label',
    insertText: '/segmentation:mask ',
    kind: 'command',
  },
  {
    id: 'segmentation-unmask',
    label: '/segmentation:unmask',
    detail: 'Clear the active segmentation mask',
    insertText: '/segmentation:unmask ',
    kind: 'command',
  },
  {
    id: 'worldgen',
    label: '/worldgen',
    detail: 'Build a world model between two markers',
    insertText: '/worldgen ',
    kind: 'command',
  },
]

function activeSuggestionContext(value: string, caret: number): SuggestionContext | null {
  const beforeCaret = value.slice(0, caret)
  const markerMatch = /(^|[\s-])@([A-Za-z0-9_]*)$/.exec(beforeCaret)
  if (markerMatch) {
    return {
      kind: 'marker',
      start: caret - markerMatch[2].length - 1,
      end: caret,
      query: markerMatch[2],
    }
  }
  const commandMatch = /(^|\s)\/([^\s]*)$/.exec(beforeCaret)
  if (!commandMatch) return null
  return {
    kind: 'command',
    start: caret - commandMatch[2].length - 1,
    end: caret,
    query: commandMatch[2],
  }
}

function textareaCaretOffset(textarea: HTMLTextAreaElement, caret: number) {
  const computed = window.getComputedStyle(textarea)
  const mirror = document.createElement('div')
  const copiedProperties = [
    'borderBottomWidth',
    'borderLeftWidth',
    'borderRightWidth',
    'borderTopWidth',
    'boxSizing',
    'fontFamily',
    'fontSize',
    'fontStyle',
    'fontWeight',
    'letterSpacing',
    'lineHeight',
    'paddingBottom',
    'paddingLeft',
    'paddingRight',
    'paddingTop',
    'textIndent',
    'textTransform',
    'wordSpacing',
  ] as const
  mirror.style.position = 'fixed'
  mirror.style.left = '-9999px'
  mirror.style.top = '0'
  mirror.style.visibility = 'hidden'
  mirror.style.whiteSpace = 'pre-wrap'
  mirror.style.overflowWrap = 'break-word'
  mirror.style.width = `${textarea.clientWidth}px`
  for (const property of copiedProperties) {
    mirror.style[property] = computed[property]
  }
  mirror.textContent = textarea.value.slice(0, caret)
  const marker = document.createElement('span')
  marker.textContent = textarea.value.slice(caret, caret + 1) || '\u200b'
  mirror.appendChild(marker)
  document.body.appendChild(mirror)
  const offset = { left: marker.offsetLeft, top: marker.offsetTop }
  mirror.remove()
  return offset
}

function transcriptionEndpoint() {
  return `${(localStorage.getItem('bayesmech:control-endpoint') || 'http://127.0.0.1:8080').replace(/\/+$/, '')}/api/transcribe`
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
  addingDevice,
  onAddDevice,
  onMessagesChange,
  onSendMessage,
  onRunCommand,
}: AIChatProps) {
  const [draft, setDraft] = useState('')
  const [jobsExpanded, setJobsExpanded] = useState(false)
  const [deviceMenuOpen, setDeviceMenuOpen] = useState(false)
  const messageListRef = useRef<HTMLDivElement>(null)
  const deviceMenuRef = useRef<HTMLDivElement>(null)
  const composerRef = useRef<HTMLDivElement>(null)
  const textareaRef = useRef<HTMLTextAreaElement>(null)
  const recorderRef = useRef<MediaRecorder | null>(null)
  const recorderStreamRef = useRef<MediaStream | null>(null)
  const audioChunksRef = useRef<Blob[]>([])
  const discardVoiceRef = useRef(false)
  const [messages, setMessages] = useState<WorkspaceChatMessage[]>([])
  const [sending, setSending] = useState(false)
  const [voiceState, setVoiceState] = useState<'idle' | 'recording' | 'transcribing'>('idle')
  const [voiceError, setVoiceError] = useState<string | null>(null)
  const [suggestionContext, setSuggestionContext] = useState<SuggestionContext | null>(null)
  const [suggestionIndex, setSuggestionIndex] = useState(0)
  const [suggestionPosition, setSuggestionPosition] = useState({ left: 8, top: 0 })

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
  const suggestions = useMemo<ComposerSuggestion[]>(() => {
    if (!suggestionContext) return []
    const query = suggestionContext.query.toLowerCase()
    if (suggestionContext.kind === 'command') {
      return COMMAND_SUGGESTIONS.filter((item) => (
        `${item.label} ${item.detail}`.toLowerCase().includes(query)
      ))
    }
    return markers
      .filter((marker) => (
        `${marker.reference} ${marker.name}`.toLowerCase().includes(query)
      ))
      .map((marker) => ({
        id: marker.id,
        label: `@${marker.reference}`,
        detail: `${marker.name} · frame ${marker.frameNumber}`,
        insertText: `@${marker.reference} `,
        kind: 'marker' as const,
      }))
  }, [markers, suggestionContext])

  useEffect(() => {
    if (activeJobCount > 0) setJobsExpanded(true)
  }, [activeJobCount])

  useEffect(() => {
    if (!deviceMenuOpen) return
    const closeOnOutsideClick = (event: MouseEvent) => {
      if (!deviceMenuRef.current?.contains(event.target as Node)) setDeviceMenuOpen(false)
    }
    const closeOnEscape = (event: globalThis.KeyboardEvent) => {
      if (event.key === 'Escape') setDeviceMenuOpen(false)
    }
    document.addEventListener('mousedown', closeOnOutsideClick)
    document.addEventListener('keydown', closeOnEscape)
    return () => {
      document.removeEventListener('mousedown', closeOnOutsideClick)
      document.removeEventListener('keydown', closeOnEscape)
    }
  }, [deviceMenuOpen])

  useEffect(() => {
    setDraft('')
    setMessages(chatSession?.messages ?? [])
    setSending(false)
    setSuggestionContext(null)
    setVoiceError(null)
  }, [chatSession?.id])

  useEffect(() => () => {
    discardVoiceRef.current = true
    if (recorderRef.current?.state === 'recording') recorderRef.current.stop()
    recorderStreamRef.current?.getTracks().forEach((track) => track.stop())
  }, [])

  useEffect(() => {
    setSuggestionIndex((current) => Math.min(current, Math.max(0, suggestions.length - 1)))
  }, [suggestions.length])

  useLayoutEffect(() => {
    const textarea = textareaRef.current
    const composer = composerRef.current
    if (!textarea || !composer || !suggestionContext) return
    const caretOffset = textareaCaretOffset(textarea, suggestionContext.end)
    const availableWidth = composer.clientWidth
    setSuggestionPosition({
      left: Math.max(8, Math.min(caretOffset.left - textarea.scrollLeft, availableWidth - 286)),
      top: Math.max(8, textarea.offsetTop + caretOffset.top - textarea.scrollTop),
    })
  }, [draft, suggestionContext])

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

  const refreshSuggestions = (value: string, caret: number | null) => {
    const next = activeSuggestionContext(value, caret ?? value.length)
    setSuggestionContext(next)
    setSuggestionIndex(0)
  }

  const applySuggestion = (suggestion: ComposerSuggestion) => {
    if (!suggestionContext) return
    const nextDraft = (
      draft.slice(0, suggestionContext.start)
      + suggestion.insertText
      + draft.slice(suggestionContext.end)
    )
    const nextCaret = suggestionContext.start + suggestion.insertText.length
    setDraft(nextDraft)
    setSuggestionContext(null)
    window.requestAnimationFrame(() => {
      const textarea = textareaRef.current
      textarea?.focus()
      textarea?.setSelectionRange(nextCaret, nextCaret)
    })
  }

  const toggleVoiceCapture = async () => {
    if (voiceState === 'recording') {
      recorderRef.current?.stop()
      return
    }
    if (voiceState === 'transcribing') return
    setVoiceError(null)
    if (!navigator.mediaDevices?.getUserMedia || typeof MediaRecorder === 'undefined') {
      setVoiceError('Voice input is not available on this device.')
      return
    }
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true })
      const preferredType = [
        'audio/webm;codecs=opus',
        'audio/webm',
        'audio/ogg;codecs=opus',
      ].find((type) => MediaRecorder.isTypeSupported(type))
      const recorder = preferredType
        ? new MediaRecorder(stream, { mimeType: preferredType })
        : new MediaRecorder(stream)
      recorderRef.current = recorder
      recorderStreamRef.current = stream
      audioChunksRef.current = []
      discardVoiceRef.current = false
      recorder.ondataavailable = (event) => {
        if (event.data.size > 0) audioChunksRef.current.push(event.data)
      }
      recorder.onerror = () => {
        stream.getTracks().forEach((track) => track.stop())
        recorderRef.current = null
        recorderStreamRef.current = null
        setVoiceState('idle')
        setVoiceError('The microphone stopped unexpectedly.')
      }
      recorder.onstop = async () => {
        stream.getTracks().forEach((track) => track.stop())
        recorderRef.current = null
        recorderStreamRef.current = null
        if (discardVoiceRef.current) return
        const blob = new Blob(audioChunksRef.current, {
          type: recorder.mimeType || 'audio/webm',
        })
        audioChunksRef.current = []
        if (!blob.size) {
          setVoiceState('idle')
          setVoiceError('No audio was recorded.')
          return
        }
        setVoiceState('transcribing')
        try {
          const extension = blob.type.includes('ogg')
            ? 'ogg'
            : blob.type.includes('mp4')
              ? 'm4a'
              : 'webm'
          const form = new FormData()
          form.append('file', blob, `voice.${extension}`)
          const response = await fetch(transcriptionEndpoint(), {
            method: 'POST',
            body: form,
          })
          const payload = await response.json().catch(() => ({})) as {
            text?: string
            detail?: string
          }
          if (!response.ok) throw new Error(payload.detail || `Transcription failed (${response.status})`)
          const transcript = String(payload.text || '').trim()
          if (!transcript) throw new Error('The transcription was empty.')
          setDraft((current) => current
            ? `${current}${/\s$/.test(current) ? '' : ' '}${transcript}`
            : transcript)
          window.requestAnimationFrame(() => textareaRef.current?.focus())
        } catch (error) {
          setVoiceError(error instanceof Error ? error.message : 'Could not transcribe the recording.')
        } finally {
          setVoiceState('idle')
        }
      }
      recorder.start()
      setVoiceState('recording')
    } catch (error) {
      setVoiceState('idle')
      setVoiceError(error instanceof Error ? error.message : 'Microphone access was denied.')
    }
  }

  const submitDraft = async () => {
    const text = draft.trim()
    if (!text || !chatSession) return
    setDraft('')
    setSuggestionContext(null)
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

    const assistantId = `assistant-${Date.now()}`
    const assistantMessage: WorkspaceChatMessage = {
      id: assistantId,
      role: 'assistant',
      status: 'pending',
      text: 'Sampling the recording and waiting for Gemma…',
      createdAt: new Date().toISOString(),
    }
    updateMessages((current) => [...current, userMessage, assistantMessage])
    setSending(true)
    try {
      const result = await onSendMessage(text, messages)
      updateMessages((current) => current.map((item) => (
        item.id === assistantId
          ? { ...item, status: 'ok', text: result.text || 'Gemma returned an empty response.' }
          : item
      )))
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Gemma video inference failed.'
      updateMessages((current) => current.map((item) => (
        item.id === assistantId
          ? { ...item, status: 'error', text: message }
          : item
      )))
    } finally {
      setSending(false)
    }
  }

  const handleSubmit = (event: FormEvent) => {
    event.preventDefault()
    void submitDraft()
  }

  const handleKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if (suggestionContext && suggestions.length) {
      if (event.key === 'ArrowDown') {
        event.preventDefault()
        setSuggestionIndex((current) => (current + 1) % suggestions.length)
        return
      }
      if (event.key === 'ArrowUp') {
        event.preventDefault()
        setSuggestionIndex((current) => (current - 1 + suggestions.length) % suggestions.length)
        return
      }
      if (event.key === 'Enter' || event.key === 'Tab') {
        event.preventDefault()
        applySuggestion(suggestions[suggestionIndex])
        return
      }
    }
    if (event.key === 'Escape' && suggestionContext) {
      event.preventDefault()
      setSuggestionContext(null)
      return
    }
    if (event.key !== 'Enter') return
    if (event.ctrlKey) return
    event.preventDefault()
    void submitDraft()
  }

  const addDevice = (preset: ControlDevicePreset) => {
    setDeviceMenuOpen(false)
    onAddDevice(preset)
  }

  return (
    <section className="chat-panel">
      <div className="panel-header">
        <h2>Chat</h2>
        <div className="add-device-menu" ref={deviceMenuRef}>
          <button
            type="button"
            className={deviceMenuOpen ? 'toolbar-button add-device-trigger is-active' : 'toolbar-button add-device-trigger'}
            onClick={() => setDeviceMenuOpen((current) => !current)}
            title={selectedRecording ? 'Add a device to this project' : 'Open or create a project first'}
            aria-label="Add device"
            aria-haspopup="menu"
            aria-expanded={deviceMenuOpen}
            disabled={!selectedRecording || addingDevice}
          >
            <Bot size={14} aria-hidden="true" />
            <span>{addingDevice ? 'Adding…' : 'Add device'}</span>
          </button>
          {deviceMenuOpen ? (
            <div className="device-preset-popover" role="menu" aria-label="Devices to add">
              <span className="device-preset-title">Add to this project</span>
              <button type="button" role="menuitem" onClick={() => addDevice('robot_car')}>
                <CarFront size={16} aria-hidden="true" />
                <span><strong>Robot Car</strong><small>Camera, drive and ultrasonic telemetry</small></span>
              </button>
              <button type="button" role="menuitem" onClick={() => addDevice('phone_camera')}>
                <Smartphone size={16} aria-hidden="true" />
                <span><strong>Phone Camera</strong><small>Perceiver video, depth, IMU and GPS</small></span>
              </button>
              <button type="button" role="menuitem" onClick={() => addDevice('robot_hand')}>
                <Hand size={16} aria-hidden="true" />
                <span><strong>Robot Hand</strong><small>Actuators and camera stream</small></span>
              </button>
              <button type="button" role="menuitem" onClick={() => addDevice('drone')}>
                <Plane size={16} aria-hidden="true" />
                <span><strong>Drone</strong><small>Flight control and video stream</small></span>
              </button>
            </div>
          ) : null}
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
        <div className="chat-compose" ref={composerRef}>
          {suggestionContext ? (
            <div
              id="chat-composer-suggestions"
              className="composer-suggestions"
              role="listbox"
              aria-label={suggestionContext.kind === 'command' ? 'Commands' : 'Markers'}
              style={{
                left: suggestionPosition.left,
                top: suggestionPosition.top,
              }}
            >
              <div className="composer-suggestions-heading">
                {suggestionContext.kind === 'command' ? 'Commands' : 'Markers'}
                <span>↑↓ choose · Enter insert</span>
              </div>
              {suggestions.length ? suggestions.map((suggestion, index) => {
                const Icon = suggestion.kind === 'command' ? Terminal : MapPin
                return (
                  <button
                    type="button"
                    role="option"
                    aria-selected={index === suggestionIndex}
                    className={index === suggestionIndex ? 'is-selected' : ''}
                    key={suggestion.id}
                    onMouseDown={(event) => event.preventDefault()}
                    onClick={() => applySuggestion(suggestion)}
                  >
                    <Icon size={14} aria-hidden="true" />
                    <span>
                      <strong>{suggestion.label}</strong>
                      <small>{suggestion.detail}</small>
                    </span>
                  </button>
                )
              }) : (
                <div className="composer-suggestions-empty">
                  {suggestionContext.kind === 'marker'
                    ? 'No matching markers in this chat'
                    : 'No matching commands'}
                </div>
              )}
            </div>
          ) : null}
          <textarea
            ref={textareaRef}
            value={draft}
            onChange={(event) => {
              setDraft(event.target.value)
              refreshSuggestions(event.target.value, event.target.selectionStart)
            }}
            onSelect={(event) => {
              refreshSuggestions(event.currentTarget.value, event.currentTarget.selectionStart)
            }}
            onKeyDown={handleKeyDown}
            disabled={!chatSession || sending}
            placeholder="Ask about this recording, type / for commands, or @ for markers"
            rows={3}
            aria-autocomplete="list"
            aria-controls={suggestionContext ? 'chat-composer-suggestions' : undefined}
            aria-expanded={Boolean(suggestionContext)}
          />
          <div className="chat-compose-actions">
            <button
              type="button"
              className={`voice-button${voiceState === 'recording' ? ' is-recording' : ''}`}
              title={
                voiceError
                  ?? (voiceState === 'recording'
                    ? 'Stop and transcribe'
                    : voiceState === 'transcribing'
                      ? 'Transcribing voice…'
                      : 'Dictate a message')
              }
              aria-label={voiceState === 'recording' ? 'Stop voice recording' : 'Start voice recording'}
              aria-pressed={voiceState === 'recording'}
              disabled={!chatSession || sending || voiceState === 'transcribing'}
              onClick={() => void toggleVoiceCapture()}
            >
              {voiceState === 'transcribing'
                ? <LoaderCircle className="spin" size={16} aria-hidden="true" />
                : <Mic size={16} aria-hidden="true" />}
            </button>
            <button
              type="submit"
              className="send-button"
              title="Send message"
              disabled={!chatSession || sending || !draft.trim()}
            >
              <CornerDownLeft size={16} aria-hidden="true" />
            </button>
          </div>
        </div>
        {voiceError ? <span className="chat-compose-error">{voiceError}</span> : null}
      </form>
    </section>
  )
}
