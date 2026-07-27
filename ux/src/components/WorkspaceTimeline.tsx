import { ChevronLeft, ChevronRight, MapPin, Pause, Play, Plus } from 'lucide-react'
import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  type Dispatch,
  type KeyboardEvent,
  type SetStateAction,
} from 'react'
import type { VideoMarker, VideoPlaybackState, VisSummary, WorldgenResult } from '../types'

type WorkspaceTimelineProps = {
  summary: VisSummary | null
  videoState: VideoPlaybackState
  onVideoStateChange: Dispatch<SetStateAction<VideoPlaybackState>>
  worldgenResults: Record<string, WorldgenResult>
}

type FrameRange = {
  start: number
  end: number
}

const SPEEDS = [0.25, 0.5, 1, 2, 4]
const MARKER_COLORS = ['#5aa9e6', '#62d2a2', '#f0b35a', '#d7687d', '#a884e6', '#78c878']

function clampFps(frameCount: number, durationSeconds: number): number {
  if (!Number.isFinite(durationSeconds) || durationSeconds <= 0 || frameCount <= 1) return 30
  const fps = (frameCount - 1) / durationSeconds
  if (!Number.isFinite(fps) || fps <= 0) return 30
  return Math.min(60, Math.max(1, fps))
}

function timeLabel(seconds: number): string {
  const safeSeconds = Number.isFinite(seconds) && seconds > 0 ? seconds : 0
  const total = Math.floor(safeSeconds)
  const minutes = Math.floor(total / 60)
  const remainder = total % 60
  return `${minutes}:${remainder.toString().padStart(2, '0')}`
}

function markerId(): string {
  return `marker-${Date.now()}-${Math.random().toString(36).slice(2, 7)}`
}

function nextMarkerReference(markers: VideoMarker[]): string {
  const used = new Set(markers.map((marker) => marker.reference.toLowerCase()))
  let number = markers.length + 1
  while (used.has(`marker${number}`.toLowerCase())) number += 1
  return `Marker${number}`
}

function generatedFrameRanges(frameCount: number, results: WorldgenResult[]): FrameRange[] {
  if (frameCount <= 0 || !results.length) return []
  const available = new Uint8Array(frameCount)
  for (const result of results) {
    for (const frame of result.frames) {
      if (frame.frameIndex >= 0 && frame.frameIndex < frameCount) available[frame.frameIndex] = 1
    }
    for (const camera of result.cameras) {
      if (camera.frameIndex >= 0 && camera.frameIndex < frameCount) available[camera.frameIndex] = 1
    }
  }

  const ranges: FrameRange[] = []
  let start = -1
  for (let index = 0; index <= frameCount; index += 1) {
    if (index < frameCount && available[index]) {
      if (start < 0) start = index
    } else if (start >= 0) {
      ranges.push({ start, end: index - 1 })
      start = -1
    }
  }
  return ranges
}

function generatedFrameIndexes(frameCount: number, results: WorldgenResult[]): number[] {
  const indexes = new Set<number>()
  for (const result of results) {
    for (const frame of result.frames) {
      if (frame.frameIndex >= 0 && frame.frameIndex < frameCount) indexes.add(frame.frameIndex)
    }
    for (const camera of result.cameras) {
      if (camera.frameIndex >= 0 && camera.frameIndex < frameCount) indexes.add(camera.frameIndex)
    }
  }
  return [...indexes].sort((left, right) => left - right)
}

export default function WorkspaceTimeline({
  summary,
  videoState,
  onVideoStateChange,
  worldgenResults,
}: WorkspaceTimelineProps) {
  const { index, markers, playing, speed } = videoState
  const frameCount = summary?.frameCount ?? 0
  const maximumIndex = Math.max(0, frameCount - 1)
  const fps = useMemo(
    () => clampFps(frameCount, summary?.durationSeconds ?? 0),
    [frameCount, summary?.durationSeconds],
  )
  const currentSeconds = index / fps
  const totalSeconds = summary && summary.durationSeconds > 0 ? summary.durationSeconds : maximumIndex / fps
  const sortedMarkers = useMemo(() => [...markers].sort((left, right) => left.frameIndex - right.frameIndex), [markers])
  const results = useMemo(() => Object.values(worldgenResults), [worldgenResults])
  const coverageRanges = useMemo(() => generatedFrameRanges(frameCount, results), [frameCount, results])
  const coverageFrames = useMemo(() => generatedFrameIndexes(frameCount, results), [frameCount, results])
  const showCoverage = coverageFrames.length > 0
  const nextPlaybackDeadlineRef = useRef(0)

  const updateIndex = useCallback(
    (next: number | ((current: number) => number), stopPlayback = true) => {
      onVideoStateChange((current) => {
        const requested = typeof next === 'function' ? next(current.index) : next
        return {
          ...current,
          index: Math.max(0, Math.min(maximumIndex, Math.trunc(requested))),
          playing: stopPlayback ? false : current.playing,
        }
      })
    },
    [maximumIndex, onVideoStateChange],
  )

  const togglePlayback = useCallback(() => {
    if (frameCount <= 1) return
    onVideoStateChange((current) => ({
      ...current,
      index: !current.playing && current.index >= maximumIndex ? 0 : current.index,
      playing: !current.playing,
    }))
  }, [frameCount, maximumIndex, onVideoStateChange])

  useEffect(() => {
    if (index <= maximumIndex) return
    updateIndex(maximumIndex)
  }, [index, maximumIndex, updateIndex])

  useLayoutEffect(() => {
    if (!playing || frameCount <= 1) {
      nextPlaybackDeadlineRef.current = 0
      return
    }
    const interval = 1000 / (fps * speed)
    const now = performance.now()
    if (nextPlaybackDeadlineRef.current <= 0) {
      nextPlaybackDeadlineRef.current = now + interval
    }
    const delay = Math.max(0, nextPlaybackDeadlineRef.current - now)
    const timer = window.setTimeout(() => {
      const tickTime = performance.now()
      const overdue = Math.max(0, tickTime - nextPlaybackDeadlineRef.current)
      const steps = Math.max(1, Math.floor(overdue / interval) + 1)
      nextPlaybackDeadlineRef.current += steps * interval
      onVideoStateChange((current) => {
        const nextIndex = current.index + steps
        if (nextIndex >= maximumIndex) {
          nextPlaybackDeadlineRef.current = 0
          return { ...current, index: maximumIndex, playing: false }
        }
        return { ...current, index: nextIndex }
      })
    }, delay)
    return () => window.clearTimeout(timer)
  }, [fps, frameCount, index, maximumIndex, onVideoStateChange, playing, speed])

  const addMarker = useCallback(() => {
    if (!frameCount) return
    onVideoStateChange((current) => {
      const reference = nextMarkerReference(current.markers)
      const marker: VideoMarker = {
        id: markerId(),
        name: reference,
        reference,
        frameIndex: current.index,
        frameNumber: (summary?.firstFrameNumber ?? 0) + current.index,
        seconds: current.index / fps,
        color: MARKER_COLORS[current.markers.length % MARKER_COLORS.length],
      }
      return {
        ...current,
        markers: [...current.markers, marker].sort((left, right) => left.frameIndex - right.frameIndex),
      }
    })
  }, [fps, frameCount, onVideoStateChange, summary?.firstFrameNumber])

  const onKeyDown = useCallback(
    (event: KeyboardEvent<HTMLElement>) => {
      if (
        event.target instanceof HTMLInputElement ||
        event.target instanceof HTMLButtonElement ||
        event.target instanceof HTMLSelectElement
      ) return
      if (event.key === ' ') {
        event.preventDefault()
        togglePlayback()
      } else if (event.key === 'ArrowLeft' || event.key === 'ArrowRight') {
        event.preventDefault()
        updateIndex((current) => current + (event.key === 'ArrowLeft' ? -1 : 1))
      }
    },
    [togglePlayback, updateIndex],
  )

  if (!summary || frameCount === 0) return null

  return (
    <section className="workspace-timeline" tabIndex={0} onKeyDown={onKeyDown} aria-label="Recording timeline">
      <div className="timeline-playback-controls">
        <button type="button" className="icon-button" onClick={() => updateIndex((current) => current - 1)} title="Previous frame" disabled={index <= 0}>
          <ChevronLeft size={16} aria-hidden="true" />
        </button>
        <button type="button" className="icon-button play-button" onClick={togglePlayback} title={playing ? 'Pause' : 'Play'}>
          {playing ? <Pause size={16} aria-hidden="true" /> : <Play size={16} aria-hidden="true" />}
        </button>
        <button type="button" className="icon-button" onClick={() => updateIndex((current) => current + 1)} title="Next frame" disabled={index >= maximumIndex}>
          <ChevronRight size={16} aria-hidden="true" />
        </button>
      </div>

      <div
        className={`timeline-track${showCoverage ? ' has-worldgen-coverage' : ''}`}
        title={showCoverage ? 'Colored frames have generated VGGT or Gaussian splat data; grey frames do not' : 'Recording timeline'}
      >
        <div className="timeline-availability" aria-hidden="true">
          {!showCoverage ? <span className="timeline-available-range full" /> : null}
          {coverageRanges.map((range) => (
            <span
              className="timeline-available-range"
              key={`${range.start}-${range.end}`}
              style={{
                left: `${(range.start / frameCount) * 100}%`,
                width: `${((range.end - range.start + 1) / frameCount) * 100}%`,
              }}
            />
          ))}
        </div>
        <div className="timeline-worldgen-frames" aria-label={`${coverageFrames.length} reconstructed World Modeling frames`}>
          {coverageFrames.map((frameIndex) => (
            <button
              type="button"
              className="timeline-worldgen-frame"
              key={frameIndex}
              onClick={() => updateIndex(frameIndex)}
              style={{ left: `${maximumIndex > 0 ? (frameIndex / maximumIndex) * 100 : 0}%` }}
              title={`Open reconstructed frame ${frameIndex + 1}`}
              aria-label={`Open reconstructed frame ${frameIndex + 1}`}
            />
          ))}
        </div>
        <div
          className="timeline-progress"
          style={{ width: `${maximumIndex > 0 ? (index / maximumIndex) * 100 : 0}%` }}
          aria-hidden="true"
        />
        <input
          className="timeline-seek"
          type="range"
          min={0}
          max={maximumIndex}
          step={1}
          value={index}
          onChange={(event) => updateIndex(Number(event.target.value))}
          aria-label="Seek recording"
        />
        <div className="timeline-markers" aria-label="Markers">
          {sortedMarkers.map((marker) => (
            <button
              type="button"
              className="timeline-marker"
              key={marker.id}
              onClick={() => updateIndex(marker.frameIndex)}
              style={{ left: `${maximumIndex > 0 ? (marker.frameIndex / maximumIndex) * 100 : 0}%`, color: marker.color }}
              aria-label={`Go to marker ${marker.name}`}
            >
              <MapPin size={18} fill="currentColor" aria-hidden="true" />
              <span className="timeline-marker-tooltip" role="tooltip">
                @{marker.reference} · {timeLabel(marker.seconds)} · frame {marker.frameIndex + 1}
              </span>
            </button>
          ))}
        </div>
      </div>

      <button type="button" className="icon-button timeline-add-marker" onClick={addMarker} title={`Add marker at frame ${index + 1}`} aria-label="Add marker">
        <Plus size={16} aria-hidden="true" />
      </button>
      <span className="video-time">{timeLabel(currentSeconds)} / {timeLabel(totalSeconds)}</span>
      <span className="video-frame-count" title="Frame number">{index + 1} / {frameCount}</span>
      <label className="video-speed" title="Playback speed">
        <select
          value={speed}
          onChange={(event) => onVideoStateChange((current) => ({ ...current, speed: Number(event.target.value) }))}
          aria-label="Playback speed"
        >
          {SPEEDS.map((value) => <option key={value} value={value}>{value}x</option>)}
        </select>
      </label>
    </section>
  )
}
