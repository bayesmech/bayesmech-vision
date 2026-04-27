import type { MotioncapTrackLegendItem, StreamStats, RecordingInfo } from '../types'
import type { bayesmech } from '../proto/bundle'
import { decodeIdoSlamResponse } from './proto'

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

export async function fetchPlaybackMotioncapTracks(): Promise<{
  available: boolean
  tracks: MotioncapTrackLegendItem[]
}> {
  const res = await fetch('/api/analysis/playback/analyses/motioncap/views/tracks')
  if (!res.ok) throw new Error(`Failed to fetch motion capture tracks: ${res.status}`)
  return res.json() as Promise<{ available: boolean; tracks: MotioncapTrackLegendItem[] }>
}

export async function fetchPlaybackMotioncapHeatmap(index: number): Promise<Blob> {
  const res = await fetch(`/api/analysis/playback/analyses/motioncap/views/heatmap?frame_index=${index}`)
  if (!res.ok) throw new Error(`Failed to fetch motion capture heatmap: ${res.status}`)
  return res.blob()
}
