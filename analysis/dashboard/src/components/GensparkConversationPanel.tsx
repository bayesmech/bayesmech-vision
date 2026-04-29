import { useCallback, useEffect, useMemo, useRef, useState, type FormEvent } from 'react'
import { useDashboard } from '../context/DashboardContext'
import {
  fetchGensparkChatHistory,
  fetchGensparkResponse,
  regenerateGensparkAnalysis,
  sendGensparkMessage,
} from '../services/api'
import type {
  GensparkAnalysisResponse,
  GensparkChatHistory,
  GensparkChatTurn,
  GensparkSummary,
  GensparkToolCall,
} from '../services/api'
import MarkdownContent from './MarkdownContent'

const hasSummary = (summary?: GensparkSummary): summary is GensparkSummary =>
  !!summary && (!!(summary.title ?? '').trim() || !!(summary.text ?? '').trim() || (summary.parameters?.length ?? 0) > 0)

const summaryMarkdown = (summary: GensparkSummary): string => {
  const parts: string[] = []
  const title = (summary.title ?? '').trim()
  const text = (summary.text ?? '').trim()
  const parameters = summary.parameters ?? []
  if (title) parts.push(`## ${title}`)
  if (text) parts.push(text)
  if (parameters.length > 0) {
    parts.push([
      '| Parameter | Value | Unit |',
      '|:---|:---|:---|',
      ...parameters.map((param) => `| ${param.name ?? ''} | ${param.value ?? ''} | ${param.unit ?? ''} |`),
    ].join('\n'))
  }
  return parts.join('\n\n')
}

const formatToolArguments = (value: string): string => {
  try {
    return JSON.stringify(JSON.parse(value), null, 2)
  } catch {
    return value
  }
}

const ToolCallBlock = ({ call }: { call: GensparkToolCall }) => (
  <details className="genspark-tool-call">
    <summary>
      <span className="genspark-tool-label">Tool</span>
      <span className="genspark-tool-name">{call.toolName || 'unknown'}</span>
    </summary>
    {call.argumentsJson && (
      <pre className="genspark-tool-pre">{formatToolArguments(call.argumentsJson)}</pre>
    )}
    {call.result && (
      <div className="genspark-tool-result">
        <MarkdownContent text={call.result ?? ''} />
      </div>
    )}
  </details>
)

const MessageBlock = ({
  role,
  text,
  toolCalls = [],
}: {
  role: 'model' | 'user'
  text: string
  toolCalls?: GensparkToolCall[]
}) => {
  if (!text.trim() && toolCalls.length === 0) return null
  return (
    <article className={`genspark-message is-${role}`}>
      <div className="genspark-message-role">{role === 'user' ? 'You' : 'Genspark'}</div>
      {text.trim() && <MarkdownContent text={text} />}
      {toolCalls.length > 0 && (
        <div className="genspark-tool-stack">
          {toolCalls.map((call, index) => (
            <ToolCallBlock key={`${call.toolName}-${index}`} call={call} />
          ))}
        </div>
      )}
    </article>
  )
}

const fallbackInitialTurn = (
  response: GensparkAnalysisResponse | null,
  chatHistory: GensparkChatHistory | null,
): GensparkChatTurn | null | undefined => {
  if (response && (response.turns?.length ?? 0) > 0) return undefined
  return chatHistory?.initialTurn
}

const timestampKey = (value: GensparkChatTurn['timestampNs']): string => {
  if (typeof value === 'number') return String(value)
  if (value && typeof value.toString === 'function') return value.toString()
  return '0'
}

const GensparkConversationPanel = () => {
  const { currentRecordingName, isLive } = useDashboard()
  const [response, setResponse] = useState<GensparkAnalysisResponse | null>(null)
  const [chatHistory, setChatHistory] = useState<GensparkChatHistory | null>(null)
  const [status, setStatus] = useState<'idle' | 'loading' | 'ready' | 'empty' | 'error'>('idle')
  const [error, setError] = useState<string | null>(null)
  const [message, setMessage] = useState('')
  const [sessionId, setSessionId] = useState<string | null>(null)
  const [isSending, setIsSending] = useState(false)
  const [isRegenerating, setIsRegenerating] = useState(false)
  const scrollRef = useRef<HTMLDivElement>(null)

  const loadConversation = useCallback(async () => {
    if (!currentRecordingName || isLive) {
      setResponse(null)
      setChatHistory(null)
      setStatus('empty')
      setError(null)
      return
    }

    setStatus('loading')
    setError(null)
    try {
      const [nextResponse, nextChatHistory] = await Promise.all([
        fetchGensparkResponse(currentRecordingName),
        fetchGensparkChatHistory(currentRecordingName),
      ])
      setResponse(nextResponse)
      setChatHistory(nextChatHistory)
      setStatus(nextResponse || nextChatHistory.initialTurn || (nextChatHistory.turns?.length ?? 0) > 0 ? 'ready' : 'empty')
    } catch (e) {
      setStatus('error')
      setError(e instanceof Error ? e.message : 'Failed to load Genspark')
    }
  }, [currentRecordingName, isLive])

  useEffect(() => {
    void loadConversation()
  }, [loadConversation])

  useEffect(() => {
    const target = scrollRef.current
    if (!target) return
    target.scrollTop = target.scrollHeight
  }, [response, chatHistory, isSending])

  const initialTurn = useMemo(
    () => fallbackInitialTurn(response, chatHistory),
    [chatHistory, response],
  )
  const responseSummary = response?.summary ?? undefined

  const canChat = !!currentRecordingName && !isLive && !!response && !isSending && !isRegenerating

  const handleSubmit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    const trimmed = message.trim()
    if (!trimmed || !currentRecordingName || !canChat) return

    setIsSending(true)
    setError(null)
    try {
      const result = await sendGensparkMessage(currentRecordingName, trimmed, sessionId ?? undefined)
      setSessionId(result.sessionId)
      setChatHistory((previous) => {
        const base = previous ?? {
          fileName: currentRecordingName,
          turns: [],
          geminiCacheName: '',
          threadCreatedTimestampNs: 0,
        }
        const turns = base.turns ?? []
        return {
          ...base,
          turns: [
            ...turns,
            { role: 'user', text: trimmed, timestampNs: result.userTimestampNs },
            { role: 'model', text: result.response, timestampNs: result.responseTimestampNs },
          ],
        }
      })
      setMessage('')
      setStatus('ready')
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to send message')
    } finally {
      setIsSending(false)
    }
  }

  const handleRegenerate = async () => {
    if (!currentRecordingName || isLive || isRegenerating) return
    setIsRegenerating(true)
    setError(null)
    try {
      await regenerateGensparkAnalysis(currentRecordingName)
      setSessionId(null)
      await loadConversation()
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to regenerate Genspark')
    } finally {
      setIsRegenerating(false)
    }
  }

  return (
    <section className="stream-card genspark-panel" aria-label="Genspark conversation">
      <div className="stream-header genspark-header">
        <div>
          <span className="stream-title">Genspark</span>
          {currentRecordingName && !isLive && (
            <span className="genspark-recording-name">{currentRecordingName}</span>
          )}
        </div>
        <button
          type="button"
          className="genspark-action-button"
          onClick={handleRegenerate}
          disabled={!currentRecordingName || isLive || isRegenerating}
        >
          {isRegenerating ? 'Regenerating' : 'Regenerate'}
        </button>
      </div>

      <div className="genspark-scroll" ref={scrollRef}>
        {status === 'loading' && <div className="genspark-empty">Loading Genspark...</div>}
        {status === 'empty' && (
          <div className="genspark-empty">
            {currentRecordingName && !isLive ? 'No Genspark analysis for this recording.' : 'Load a recording to view Genspark.'}
          </div>
        )}
        {status === 'error' && <div className="genspark-empty is-error">{error}</div>}

        {initialTurn && <MessageBlock role="model" text={initialTurn.text ?? ''} />}

        {(response?.turns ?? []).map((turn, index) => (
          <MessageBlock
            key={`genspark-turn-${index}`}
            role="model"
            text={turn.text ?? ''}
            toolCalls={turn.toolCalls ?? []}
          />
        ))}

        {hasSummary(responseSummary) && (
          <MessageBlock role="model" text={summaryMarkdown(responseSummary)} />
        )}

        {(chatHistory?.turns ?? []).map((turn, index) => (
          <MessageBlock
            key={`chat-turn-${timestampKey(turn.timestampNs)}-${index}`}
            role={turn.role === 'user' ? 'user' : 'model'}
            text={turn.text ?? ''}
          />
        ))}

        {isSending && <div className="genspark-empty">Sending...</div>}
      </div>

      {error && status !== 'error' && <div className="genspark-inline-error">{error}</div>}

      <form className="genspark-input-row" onSubmit={handleSubmit}>
        <textarea
          value={message}
          onChange={(event) => setMessage(event.target.value)}
          placeholder="Message Genspark..."
          rows={2}
          disabled={!canChat}
          onKeyDown={(event) => {
            if (event.key === 'Enter' && !event.shiftKey) {
              event.preventDefault()
              event.currentTarget.form?.requestSubmit()
            }
          }}
        />
        <button type="submit" disabled={!canChat || !message.trim()}>
          Send
        </button>
      </form>
    </section>
  )
}

export default GensparkConversationPanel
