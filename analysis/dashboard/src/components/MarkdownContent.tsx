import type { ReactNode } from 'react'

type Block =
  | { type: 'heading'; level: number; text: string }
  | { type: 'paragraph'; text: string }
  | { type: 'code'; language: string; text: string }
  | { type: 'math'; text: string }
  | { type: 'table'; headers: string[]; rows: string[][] }
  | { type: 'list'; ordered: boolean; items: string[] }
  | { type: 'quote'; text: string }
  | { type: 'rule' }

const tableSeparatorPattern = /^\s*\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)+\|?\s*$/

const isTableSeparator = (line: string): boolean => tableSeparatorPattern.test(line)

const splitTableRow = (line: string): string[] => {
  let trimmed = line.trim()
  if (trimmed.startsWith('|')) trimmed = trimmed.slice(1)
  if (trimmed.endsWith('|')) trimmed = trimmed.slice(0, -1)
  return trimmed.split('|').map((cell) => cell.trim())
}

const startsBlock = (lines: string[], index: number): boolean => {
  const line = lines[index] ?? ''
  const next = lines[index + 1] ?? ''
  return (
    /^\s*```/.test(line) ||
    /^\s*\$\$/.test(line) ||
    /^#{1,6}\s+/.test(line) ||
    /^\s*([-*+])\s+/.test(line) ||
    /^\s*\d+\.\s+/.test(line) ||
    /^\s*>/.test(line) ||
    /^\s*(-{3,}|\*{3,}|_{3,})\s*$/.test(line) ||
    (line.includes('|') && isTableSeparator(next))
  )
}

const parseBlocks = (markdown: string): Block[] => {
  const lines = markdown.replace(/\r\n/g, '\n').split('\n')
  const blocks: Block[] = []
  let i = 0

  while (i < lines.length) {
    const line = lines[i]
    if (!line.trim()) {
      i += 1
      continue
    }

    const fence = line.match(/^\s*```([A-Za-z0-9_-]*)\s*$/)
    if (fence) {
      const language = fence[1] ?? ''
      const codeLines: string[] = []
      i += 1
      while (i < lines.length && !/^\s*```/.test(lines[i])) {
        codeLines.push(lines[i])
        i += 1
      }
      if (i < lines.length) i += 1
      blocks.push({ type: 'code', language, text: codeLines.join('\n') })
      continue
    }

    if (/^\s*\$\$/.test(line)) {
      const mathLines: string[] = []
      const first = line.replace(/^\s*\$\$\s?/, '')
      if (first.trim().endsWith('$$')) {
        blocks.push({ type: 'math', text: first.replace(/\s?\$\$\s*$/, '') })
        i += 1
        continue
      }
      if (first) mathLines.push(first)
      i += 1
      while (i < lines.length && !/\$\$\s*$/.test(lines[i])) {
        mathLines.push(lines[i])
        i += 1
      }
      if (i < lines.length) {
        mathLines.push(lines[i].replace(/\s?\$\$\s*$/, ''))
        i += 1
      }
      blocks.push({ type: 'math', text: mathLines.join('\n').trim() })
      continue
    }

    const heading = line.match(/^(#{1,6})\s+(.+)$/)
    if (heading) {
      blocks.push({ type: 'heading', level: heading[1].length, text: heading[2].trim() })
      i += 1
      continue
    }

    if (/^\s*(-{3,}|\*{3,}|_{3,})\s*$/.test(line)) {
      blocks.push({ type: 'rule' })
      i += 1
      continue
    }

    if (line.includes('|') && isTableSeparator(lines[i + 1] ?? '')) {
      const headers = splitTableRow(line)
      const rows: string[][] = []
      i += 2
      while (i < lines.length && lines[i].includes('|') && lines[i].trim()) {
        rows.push(splitTableRow(lines[i]))
        i += 1
      }
      blocks.push({ type: 'table', headers, rows })
      continue
    }

    const unordered = line.match(/^\s*[-*+]\s+(.+)$/)
    const ordered = line.match(/^\s*\d+\.\s+(.+)$/)
    if (unordered || ordered) {
      const isOrdered = !!ordered
      const items: string[] = []
      while (i < lines.length) {
        const item = lines[i].match(isOrdered ? /^\s*\d+\.\s+(.+)$/ : /^\s*[-*+]\s+(.+)$/)
        if (!item) break
        items.push(item[1].trim())
        i += 1
      }
      blocks.push({ type: 'list', ordered: isOrdered, items })
      continue
    }

    if (/^\s*>/.test(line)) {
      const quoteLines: string[] = []
      while (i < lines.length && /^\s*>/.test(lines[i])) {
        quoteLines.push(lines[i].replace(/^\s*>\s?/, ''))
        i += 1
      }
      blocks.push({ type: 'quote', text: quoteLines.join('\n') })
      continue
    }

    const paragraphLines: string[] = []
    while (i < lines.length && lines[i].trim() && !startsBlock(lines, i)) {
      paragraphLines.push(lines[i].trim())
      i += 1
    }
    if (paragraphLines.length > 0) {
      blocks.push({ type: 'paragraph', text: paragraphLines.join(' ') })
    } else {
      i += 1
    }
  }

  return blocks
}

const safeHref = (href: string): string | null => {
  const trimmed = href.trim()
  if (/^(https?:|mailto:|\/|#)/i.test(trimmed)) return trimmed
  return null
}

const nextTokenOffset = (text: string): number => {
  const candidates = ['`', '\\(', '$', '[', '**', '*']
    .map((token) => text.indexOf(token))
    .filter((index) => index >= 0)
  return candidates.length ? Math.min(...candidates) : text.length
}

const renderInline = (text: string, keyPrefix: string): ReactNode[] => {
  const nodes: ReactNode[] = []
  let index = 0

  while (index < text.length) {
    const rest = text.slice(index)
    const code = rest.match(/^`([^`]+)`/)
    if (code) {
      nodes.push(<code key={`${keyPrefix}-code-${index}`}>{code[1]}</code>)
      index += code[0].length
      continue
    }

    const parenMath = rest.match(/^\\\((.+?)\\\)/)
    if (parenMath) {
      nodes.push(<span key={`${keyPrefix}-math-${index}`} className="markdown-math-inline">{parenMath[1]}</span>)
      index += parenMath[0].length
      continue
    }

    const dollarMath = rest.match(/^\$(\S(?:.*?\S)?)\$/)
    if (dollarMath) {
      nodes.push(<span key={`${keyPrefix}-math-${index}`} className="markdown-math-inline">{dollarMath[1]}</span>)
      index += dollarMath[0].length
      continue
    }

    const link = rest.match(/^\[([^\]]+)]\(([^)]+)\)/)
    if (link) {
      const href = safeHref(link[2])
      nodes.push(
        href ? (
          <a key={`${keyPrefix}-link-${index}`} href={href} target="_blank" rel="noreferrer">
            {renderInline(link[1], `${keyPrefix}-link-${index}`)}
          </a>
        ) : (
          <span key={`${keyPrefix}-link-${index}`}>{renderInline(link[1], `${keyPrefix}-link-${index}`)}</span>
        ),
      )
      index += link[0].length
      continue
    }

    const strong = rest.match(/^\*\*([^*]+)\*\*/)
    if (strong) {
      nodes.push(<strong key={`${keyPrefix}-strong-${index}`}>{renderInline(strong[1], `${keyPrefix}-strong-${index}`)}</strong>)
      index += strong[0].length
      continue
    }

    const emphasis = rest.match(/^\*([^*]+)\*/)
    if (emphasis) {
      nodes.push(<em key={`${keyPrefix}-em-${index}`}>{renderInline(emphasis[1], `${keyPrefix}-em-${index}`)}</em>)
      index += emphasis[0].length
      continue
    }

    const offset = Math.max(1, nextTokenOffset(rest.slice(1)) + 1)
    nodes.push(text.slice(index, index + offset))
    index += offset
  }

  return nodes
}

const headingTag = (level: number, content: ReactNode[], key: string): ReactNode => {
  if (level <= 1) return <h2 key={key}>{content}</h2>
  if (level === 2) return <h3 key={key}>{content}</h3>
  if (level === 3) return <h4 key={key}>{content}</h4>
  return <h5 key={key}>{content}</h5>
}

interface MarkdownContentProps {
  text: string
}

const MarkdownContent = ({ text }: MarkdownContentProps) => {
  const blocks = parseBlocks(text)

  return (
    <div className="markdown-content">
      {blocks.map((block, index) => {
        const key = `block-${index}`
        if (block.type === 'heading') {
          return headingTag(block.level, renderInline(block.text, key), key)
        }
        if (block.type === 'paragraph') {
          return <p key={key}>{renderInline(block.text, key)}</p>
        }
        if (block.type === 'code') {
          return (
            <pre key={key} className="markdown-code-block">
              <code>{block.text}</code>
            </pre>
          )
        }
        if (block.type === 'math') {
          return <div key={key} className="markdown-math-block">{block.text}</div>
        }
        if (block.type === 'table') {
          return (
            <div key={key} className="markdown-table-wrap">
              <table className="markdown-table">
                <thead>
                  <tr>
                    {block.headers.map((header, headerIndex) => (
                      <th key={`${key}-h-${headerIndex}`}>{renderInline(header, `${key}-h-${headerIndex}`)}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {block.rows.map((row, rowIndex) => (
                    <tr key={`${key}-r-${rowIndex}`}>
                      {block.headers.map((_, cellIndex) => (
                        <td key={`${key}-r-${rowIndex}-c-${cellIndex}`}>
                          {renderInline(row[cellIndex] ?? '', `${key}-r-${rowIndex}-c-${cellIndex}`)}
                        </td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )
        }
        if (block.type === 'list') {
          const ListTag = block.ordered ? 'ol' : 'ul'
          return (
            <ListTag key={key}>
              {block.items.map((item, itemIndex) => (
                <li key={`${key}-item-${itemIndex}`}>{renderInline(item, `${key}-item-${itemIndex}`)}</li>
              ))}
            </ListTag>
          )
        }
        if (block.type === 'quote') {
          return <blockquote key={key}>{renderInline(block.text, key)}</blockquote>
        }
        return <hr key={key} />
      })}
    </div>
  )
}

export default MarkdownContent
