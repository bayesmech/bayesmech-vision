import type { MotioncapData, MotioncapFrameRecord, StreamStats, RecordingInfo } from '../types'
import type { bayesmech } from '../proto/bundle'
import { decodeIdoSlamResponse, decodeMotioncapRecords } from './proto'

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

  for (const record of records) {
    if (record.tracks?.length) {
      summaryTracks = record.tracks
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

  if (frames.length === 0 && summaryTracks.length === 0) {
    return null
  }

  const byFrameNumber = new Map<number, MotioncapFrameRecord>()
  const byHeatmapIndex = new Map<number, MotioncapFrameRecord>()
  for (const frame of frames) {
    byFrameNumber.set(frame.frameNumber, frame)
    byHeatmapIndex.set(frame.heatmapIndex, frame)
  }

  const tracks = [...summaryTracks]
    .sort((a, b) => (a.trackId ?? 0) - (b.trackId ?? 0))
    .map((track) => {
      const trackId = track.trackId ?? 0
      return {
        track_id: trackId,
        color: motioncapColorForTrack(trackId),
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
    })

  return { frames, byFrameNumber, byHeatmapIndex, tracks }
}
