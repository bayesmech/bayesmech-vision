import type { RecordingEntry } from '../types'

const RECORDING_DATE_PREFIX = /^(\d{4})(\d{2})(\d{2})[_-](\d{2})(\d{2})(\d{2})(?:[_-]+)?/

function titleWord(word: string): string {
  const normalized = word.toLowerCase()
  if (normalized === 'tabletennis') return 'Table Tennis'
  if (normalized === 'gps') return 'GPS'
  if (normalized === 'idoslam') return 'IDO SLAM'
  return normalized.charAt(0).toUpperCase() + normalized.slice(1)
}

function cleanCandidate(candidate: string): string {
  const withoutExtension = candidate.replace(/\.vis\.pb$/i, '')
  const withoutDate = withoutExtension.replace(RECORDING_DATE_PREFIX, '')
  return withoutDate
    .replace(/([a-z0-9])([A-Z])/g, '$1 $2')
    .split(/[_\-\s]+/)
    .filter(Boolean)
    .map(titleWord)
    .join(' ')
}

export function recordingDisplayName(recording: RecordingEntry): string {
  if (recording.displayName?.trim()) return recording.displayName.trim()
  const directoryName = recording.directoryPath.split(/[\\/]/).filter(Boolean).at(-1) ?? ''
  const candidates = [
    ...(RECORDING_DATE_PREFIX.test(directoryName) ? [directoryName] : []),
    recording.name,
    recording.fileStem,
    directoryName,
  ]
  for (const candidate of candidates) {
    const display = cleanCandidate(candidate.split(/[\\/]/).at(-1) ?? candidate)
    if (display && display !== 'Recording') return display
  }
  return 'Recording'
}

export function recordingTimestampMs(recording: RecordingEntry): number {
  const candidates = [
    recording.directoryPath.split(/[\\/]/).filter(Boolean).at(-1) ?? '',
    recording.name,
    recording.fileStem,
  ]
  for (const candidate of candidates) {
    const match = RECORDING_DATE_PREFIX.exec(candidate)
    if (!match) continue
    const [, year, month, day, hour, minute, second] = match
    const value = new Date(
      Number(year),
      Number(month) - 1,
      Number(day),
      Number(hour),
      Number(minute),
      Number(second),
    ).getTime()
    if (Number.isFinite(value)) return value
  }
  return recording.modifiedMs
}

export function recordingVideoId(recording: RecordingEntry): string {
  if (recording.path && !recording.path.startsWith('browser://')) return recording.path
  return recording.fileStem || recording.id
}
