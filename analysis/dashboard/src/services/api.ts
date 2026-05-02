import type {
  MotioncapData,
  MotioncapFrameRecord,
  PongtownData,
  PongtownFrameRecord,
  StreamStats,
  RecordingInfo,
} from '../types'
import { bayesmech } from '../proto/bundle'
import { decodeIdoSlamResponse, decodeMotioncapRecords, decodePongtownRecords } from './proto'

export type GensparkAnalysisResponse = bayesmech.vision.IGensparkResponse
export type GensparkChatHistory = bayesmech.vision.IChatHistory
export type GensparkChatTurn = bayesmech.vision.IChatTurn
export type GensparkSummary = bayesmech.vision.IGensparkSummary
export type GensparkToolCall = bayesmech.vision.IGensparkToolCall

const MOTIONCAP_TRACK_COLORS: [number, number, number][] = [
  [255, 200, 0],
  [50, 255, 50],
  [80, 80, 255],
  [200, 50, 255],
  [0, 220, 255],
  [255, 100, 100],
  [200, 255, 0],
  [255, 0, 200],
  [0, 180, 255],
  [255, 128, 0],
]

const motioncapColorForTrack = (trackId: number): [number, number, number] =>
  MOTIONCAP_TRACK_COLORS[((trackId % MOTIONCAP_TRACK_COLORS.length) + MOTIONCAP_TRACK_COLORS.length) % MOTIONCAP_TRACK_COLORS.length]

const motioncapColorForSegmentationTrack = (trackId: number): [number, number, number] =>
  motioncapColorForTrack(trackId + 5)

const numberFromLong = (value: number | { toNumber?: () => number } | null | undefined): number => {
  if (typeof value === 'number') return value
  if (value && typeof value.toNumber === 'function') return value.toNumber()
  return Number(value ?? 0)
}

export async function fetchStreamStats(): Promise<StreamStats> {
  const res = await fetch('/api/stream')
  if (!res.ok) throw new Error(`Failed to fetch stream stats: ${res.status}`)
  return res.json() as Promise<StreamStats>
}

export async function fetchRecordings(): Promise<{ recordings: RecordingInfo[] }> {
  const res = await fetch('/api/recordings')
  if (!res.ok) throw new Error(`Failed to fetch recordings: ${res.status}`)
  return res.json() as Promise<{ recordings: RecordingInfo[] }>
}

export async function startPlayback(name: string, speed = 1.0, loop = false): Promise<void> {
  const res = await fetch('/api/playback/start', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ name, speed, loop }),
  })
  if (!res.ok) throw new Error(`Failed to start playback: ${res.status}`)
}

export async function stopPlayback(): Promise<void> {
  const res = await fetch('/api/playback/stop', { method: 'POST' })
  if (!res.ok) throw new Error(`Failed to stop playback: ${res.status}`)
}

export async function switchToLive(): Promise<void> {
  const res = await fetch('/api/playback/live', { method: 'POST' })
  if (!res.ok) throw new Error(`Failed to switch to live: ${res.status}`)
}

export async function fetchIdoSlam(fileName?: string): Promise<bayesmech.vision.IdoSlamResponse | null> {
  const query = fileName ? `?file=${encodeURIComponent(fileName)}` : ''
  const res = await fetch(`/api/idoslam${query}`)
  if (res.status === 404) return null
  if (!res.ok) throw new Error(`Failed to fetch idoslam data: ${res.status}`)
  return decodeIdoSlamResponse(new Uint8Array(await res.arrayBuffer()))
}

export async function uploadRecording(file: File): Promise<void> {
  const formData = new FormData()
  formData.append('file', file)
  const res = await fetch('/api/upload_recording', { method: 'POST', body: formData })
  if (!res.ok) throw new Error(`Failed to upload recording: ${res.status}`)
}

export async function fetchRecordingMotioncapData(recordingName: string): Promise<MotioncapData | null> {
  const res = await fetch(
    `/api/analysis/recordings/${encodeURIComponent(recordingName)}/analyses/motioncap/records?include_summary=true`,
  )
  if (res.status === 404) return null
  if (!res.ok) throw new Error(`Failed to fetch motion capture records: ${res.status}`)

  const records = decodeMotioncapRecords(new Uint8Array(await res.arrayBuffer()))
  const frames: MotioncapFrameRecord[] = []
  let summaryTracks: bayesmech.vision.IMotionTrack[] = []
  let summarySegmentationTrajectories: bayesmech.vision.IMotionTrack[] = []

  for (const record of records) {
    if (record.tracks?.length) {
      summaryTracks = record.tracks
    }
    if (record.segmentationTrajectories?.length) {
      summarySegmentationTrajectories = record.segmentationTrajectories
    }

    const heatmapData = record.heatmap?.heatmapData
    if (!heatmapData || heatmapData.length === 0) continue

    const heatmapIndex = frames.length
    frames.push({
      heatmapIndex,
      frameNumber: record.frameIdentifier?.frameNumber ?? heatmapIndex,
      timestampNs: numberFromLong(record.frameIdentifier?.timestampNs),
      heatmapData: heatmapData as Uint8Array,
    })
  }

  if (frames.length === 0 && summaryTracks.length === 0 && summarySegmentationTrajectories.length === 0) {
    return null
  }

  const byFrameNumber = new Map<number, MotioncapFrameRecord>()
  const byHeatmapIndex = new Map<number, MotioncapFrameRecord>()
  for (const frame of frames) {
    byFrameNumber.set(frame.frameNumber, frame)
    byHeatmapIndex.set(frame.heatmapIndex, frame)
  }

  const mapTrack = (
    track: bayesmech.vision.IMotionTrack,
    colorForTrack: (trackId: number) => [number, number, number],
  ) => {
    const trackId = track.trackId ?? 0
    return {
      track_id: trackId,
      label: track.label ?? '',
      color: colorForTrack(trackId),
      detected_frames: track.detectedFrames ?? 0,
      total_positions: track.totalPositions ?? 0,
      presence_fraction: track.presenceFraction ?? 0,
      positions: (track.positions ?? []).map((position) => ({
        frame_idx: position.frameIdx ?? 0,
        cx: position.cx ?? 0,
        cy: position.cy ?? 0,
        area: position.area ?? 0,
        interpolated: position.interpolated ?? false,
      })),
    }
  }

  const tracks = [...summaryTracks]
    .sort((a, b) => (a.trackId ?? 0) - (b.trackId ?? 0))
    .map((track) => mapTrack(track, motioncapColorForTrack))

  const segmentation_trajectories = [...summarySegmentationTrajectories]
    .sort((a, b) => (a.trackId ?? 0) - (b.trackId ?? 0))
    .map((track) => mapTrack(track, motioncapColorForSegmentationTrack))

  return { frames, byFrameNumber, byHeatmapIndex, tracks, segmentation_trajectories }
}

export async function fetchRecordingPongtownData(recordingName: string): Promise<PongtownData | null> {
  const res = await fetch(
    `/api/analysis/recordings/${encodeURIComponent(recordingName)}/analyses/pongtown/records?include_summary=true`,
  )
  if (res.status === 404) return null
  if (!res.ok) throw new Error(`Failed to fetch Pongtown records: ${res.status}`)

  const records = decodePongtownRecords(new Uint8Array(await res.arrayBuffer()))
  const frames: PongtownFrameRecord[] = []
  let summary: bayesmech.vision.PongtownResponse | undefined

  for (const record of records) {
    const hasFrameIdentifier = !!record.frameIdentifier
    if (!hasFrameIdentifier) {
      summary = record
      continue
    }

    const debug = record.pnpFrameDebug?.[0]
    const output = record.frameOutput
    const fallbackIndex = frames.length
    const frameIndex = debug?.frameIdx ?? output?.frameIdx ?? fallbackIndex
    frames.push({
      frameIndex,
      frameNumber: record.frameIdentifier?.frameNumber ?? frameIndex,
      timestampNs: numberFromLong(record.frameIdentifier?.timestampNs),
      record,
    })
  }

  if (frames.length === 0 && !summary) return null

  const byFrameNumber = new Map<number, PongtownFrameRecord>()
  const byFrameIndex = new Map<number, PongtownFrameRecord>()
  for (const frame of frames) {
    byFrameNumber.set(frame.frameNumber, frame)
    byFrameIndex.set(frame.frameIndex, frame)
  }

  return { frames, byFrameNumber, byFrameIndex, summary }
}

export async function fetchGensparkResponse(recordingName: string): Promise<GensparkAnalysisResponse | null> {
  const res = await fetch(
    `/api/analysis/recordings/${encodeURIComponent(recordingName)}/analyses/genspark/artifacts/proto`,
    { cache: 'no-store' },
  )
  if (res.status === 404) return null
  if (!res.ok) throw new Error(`Failed to fetch Model Musings response: ${res.status}`)
  return bayesmech.vision.GensparkResponse.decode(new Uint8Array(await res.arrayBuffer()))
}

export async function fetchGensparkChatHistory(recordingName: string, sinceTimestampNs = 0): Promise<GensparkChatHistory> {
  const res = await fetch(
    `/api/insightgen/chat?file=${encodeURIComponent(recordingName)}&since_timestamp_ns=${sinceTimestampNs}`,
    { cache: 'no-store' },
  )
  if (!res.ok) throw new Error(`Failed to fetch Model Musings chat: ${res.status}`)
  return bayesmech.vision.ChatHistory.decode(new Uint8Array(await res.arrayBuffer()))
}

export async function sendGensparkMessage(
  recordingName: string,
  message: string,
  sessionId?: string,
): Promise<{
  response: string
  sessionId: string
  userTimestampNs: number
  responseTimestampNs: number
}> {
  const res = await fetch('/api/insightgen/chat', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ file: recordingName, message, session_id: sessionId }),
  })
  if (!res.ok) throw new Error(`Failed to send Model Musings message: ${res.status}`)
  const payload = await res.json() as {
    response?: string
    session_id?: string
    user_timestamp_ns?: number
    response_timestamp_ns?: number
  }
  return {
    response: payload.response ?? '',
    sessionId: payload.session_id ?? '',
    userTimestampNs: payload.user_timestamp_ns ?? Date.now() * 1_000_000,
    responseTimestampNs: payload.response_timestamp_ns ?? Date.now() * 1_000_000,
  }
}

export async function regenerateGensparkAnalysis(recordingName: string): Promise<void> {
  const res = await fetch('/api/insightgen/regenerate', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ file: recordingName }),
  })
  if (!res.ok) throw new Error(`Failed to regenerate Model Musings: ${res.status}`)
}
