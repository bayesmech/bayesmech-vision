export function compactNumber(value: number): string {
  if (!Number.isFinite(value)) return '0'
  return new Intl.NumberFormat('en-US', { maximumFractionDigits: 0 }).format(value)
}

export function secondsLabel(value: number): string {
  if (!Number.isFinite(value) || value <= 0) return '0s'
  if (value < 60) return `${value.toFixed(1)}s`
  const minutes = Math.floor(value / 60)
  const seconds = Math.round(value % 60)
  return `${minutes}m ${seconds}s`
}

export function dateTimeLabel(ms: number): string {
  if (!Number.isFinite(ms) || ms <= 0) return 'Unknown'
  return new Intl.DateTimeFormat(undefined, {
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  }).format(new Date(ms))
}

export function shortPath(path: string, max = 72): string {
  if (path.length <= max) return path
  const head = Math.floor((max - 3) * 0.35)
  const tail = max - 3 - head
  return `${path.slice(0, head)}...${path.slice(-tail)}`
}
