import { Braces, Wrench } from 'lucide-react'
import { useState } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import type { ChatToolCall } from '../types'

function formattedValue(value: unknown): string {
  if (typeof value === 'string') {
    const trimmed = value.trim()
    if (!trimmed) return '(empty)'
    try {
      return JSON.stringify(JSON.parse(trimmed), null, 2)
    } catch {
      return value
    }
  }
  if (value === undefined) return '(undefined)'
  try {
    return JSON.stringify(value, null, 2)
  } catch {
    return String(value)
  }
}

export function MarkdownContent({
  text,
  className = '',
}: {
  text: string
  className?: string
}) {
  if (!text) return null
  return (
    <div className={`markdown-content${className ? ` ${className}` : ''}`}>
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={{
          a: ({ children, ...props }) => (
            <a {...props} target="_blank" rel="noreferrer">
              {children}
            </a>
          ),
        }}
      >
        {text}
      </ReactMarkdown>
    </div>
  )
}

function ToolCallDetails({ call }: { call: ChatToolCall }) {
  const [expanded, setExpanded] = useState(false)
  return (
    <details
      className="chat-tool-call"
      open={expanded}
      onToggle={(event) => setExpanded(event.currentTarget.open)}
    >
      <summary>
        <Wrench size={13} aria-hidden="true" />
        <strong>{call.name || 'Tool call'}</strong>
        <span>Input/output</span>
      </summary>
      {expanded ? (
        <div className="chat-tool-call-content">
          <section>
            <header>
              <Braces size={12} aria-hidden="true" />
              Input parameters
            </header>
            <pre><code>{formattedValue(call.arguments)}</code></pre>
          </section>
          <section>
            <header>
              <Braces size={12} aria-hidden="true" />
              Output
            </header>
            <pre><code>{formattedValue(call.result)}</code></pre>
          </section>
        </div>
      ) : null}
    </details>
  )
}

export function ToolCallList({ calls }: { calls?: ChatToolCall[] }) {
  if (!calls?.length) return null
  return (
    <div className="chat-tool-call-list">
      {calls.map((call, index) => (
        <ToolCallDetails call={call} key={`${call.name}-${index}`} />
      ))}
    </div>
  )
}
