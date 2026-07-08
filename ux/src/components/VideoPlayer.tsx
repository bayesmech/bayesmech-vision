import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type Dispatch,
  type KeyboardEvent,
  type SetStateAction,
} from 'react'
import { ChevronLeft, ChevronRight, Film, Pause, Play, Plus, Tag, Trash2 } from 'lucide-react'
import type { VideoMarker, VideoPlaybackState, VisFrame, VisSummary } from '../types'
import { useFrameSource } from '../lib/frameSource'
import { normalizeSegmentationLabel, useOverlay } from '../lib/overlay'
import { decodeMasks, type DecodedOverlay } from '../lib/mask'

type VideoPlayerProps = {
  summary: VisSummary | null
  videoState: VideoPlaybackState
  onVideoStateChange: Dispatch<SetStateAction<VideoPlaybackState>>
}

const SPEEDS = [0.25, 0.5, 1, 2, 4]
const MASK_ALPHA = 140 // 0..255
const MARKER_COLORS = ['#5aa9e6', '#62d2a2', '#f0b35a', '#d7687d', '#a884e6', '#78c878']

function clampFps(frameCount: number, durationSeconds: number): number {
  if (!Number.isFinite(durationSeconds) || durationSeconds <= 0 || frameCount <= 1) return 30
  const fps = (frameCount - 1) / durationSeconds
  if (!Number.isFinite(fps) || fps <= 0) return 30
  return Math.min(60, Math.max(1, fps))
}

function timeLabel(seconds: number): string {
  if (!Number.isFinite(seconds) || seconds < 0) seconds = 0
  const total = Math.floor(seconds)
  const mm = Math.floor(total / 60)
  const ss = total % 60
  return `${mm}:${ss.toString().padStart(2, '0')}`
}

function titleCaseMarkerName(name: string, fallback = 'Marker'): string {
  const words = name.match(/[A-Za-z0-9]+/g) ?? []
  const value = words
    .map((word) => {
      if (/^\d+$/.test(word)) return word
      return `${word.charAt(0).toUpperCase()}${word.slice(1).toLowerCase()}`
    })
    .join('')
  return value || fallback
}

function uniqueMarkerReference(name: string, markers: VideoMarker[], exceptId?: string): string {
  const base = titleCaseMarkerName(name)
  const used = new Set(markers.filter((marker) => marker.id !== exceptId).map((marker) => marker.reference))
  if (!used.has(base)) return base
  let index = 2
  while (used.has(`${base}${index}`)) index += 1
  return `${base}${index}`
}

function createMarkerId(): string {
  return `marker-${Date.now()}-${Math.random().toString(36).slice(2, 7)}`
}

export default function VideoPlayer({ summary, videoState, onVideoStateChange }: VideoPlayerProps) {
  const getFrame = useFrameSource()
  const overlay = useOverlay()
  const segmentationOn = overlay?.segmentation ?? false
  const segmentationMaskLabel = overlay?.segmentationMaskLabel ?? null
  const { index, markers, playing, speed } = videoState

  const frameCount = summary?.frameCount ?? 0
  const fps = useMemo(
    () => clampFps(frameCount, summary?.durationSeconds ?? 0),
    [frameCount, summary?.durationSeconds],
  )

  const [frame, setFrame] = useState<VisFrame | null>(null)
  const [imageUrl, setImageUrl] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)
  const [overlays, setOverlays] = useState<DecodedOverlay[]>([])
  const [markerDraft, setMarkerDraft] = useState('')
  const [markerColor, setMarkerColor] = useState(MARKER_COLORS[0])

  // Caches keyed by frame index (images) / frame number (decoded overlays). In
  // browser mode image dataUrls are object URLs, so evictions must revoke them.
  const cacheRef = useRef<Map<number, VisFrame>>(new Map())
  const overlayCacheRef = useRef<Map<number, DecodedOverlay[]>>(new Map())
  const canvasRef = useRef<HTMLCanvasElement>(null)
  const baseImgRef = useRef<HTMLImageElement | null>(null)
  const [drawTick, setDrawTick] = useState(0)

  const clearCaches = useCallback(() => {
    for (const cached of cacheRef.current.values()) {
      if (cached.dataUrl && cached.dataUrl.startsWith('blob:')) URL.revokeObjectURL(cached.dataUrl)
    }
    cacheRef.current.clear()
    overlayCacheRef.current.clear()
  }, [])

  const setVideoIndex = useCallback(
    (next: number | ((current: number) => number)) => {
      onVideoStateChange((current) => {
        const raw = typeof next === 'function' ? next(current.index) : next
        const max = Math.max(0, frameCount - 1)
        return { ...current, index: Math.min(max, Math.max(0, Math.trunc(raw))) }
      })
    },
    [frameCount, onVideoStateChange],
  )

  const setVideoPlaying = useCallback(
    (next: boolean | ((current: boolean) => boolean)) => {
      onVideoStateChange((current) => ({
        ...current,
        playing: typeof next === 'function' ? next(current.playing) : next,
      }))
    },
    [onVideoStateChange],
  )

  const setVideoSpeed = useCallback(
    (next: number) => {
      onVideoStateChange((current) => ({ ...current, speed: next }))
    },
    [onVideoStateChange],
  )

  // Reset when the recording (frame source) or file changes.
  useEffect(() => {
    clearCaches()
    setFrame(null)
    setImageUrl(null)
    setOverlays([])
    setVideoPlaying(false)
  }, [getFrame, summary?.path, clearCaches, setVideoPlaying])

  useEffect(() => () => clearCaches(), [clearCaches])

  useEffect(() => {
    if (frameCount > 0 && index > frameCount - 1) setVideoIndex(frameCount - 1)
  }, [frameCount, index, setVideoIndex])

  const prefetch = useCallback(
    (target: number) => {
      if (!getFrame || target < 0 || target >= frameCount || cacheRef.current.has(target)) return
      getFrame(target)
        .then((f) => {
          if (f && !cacheRef.current.has(f.index)) cacheRef.current.set(f.index, f)
        })
        .catch(() => {})
    },
    [getFrame, frameCount],
  )

  // Load the current frame image (from cache or the frame source), prefetch ahead.
  useEffect(() => {
    if (!getFrame || frameCount === 0) return
    const cached = cacheRef.current.get(index)
    if (cached) {
      setFrame(cached)
      if (cached.dataUrl) setImageUrl(cached.dataUrl)
      prefetch(index + 1)
      prefetch(index + 2)
      return
    }

    let cancelled = false
    setLoading(true)
    getFrame(index)
      .then((f) => {
        if (cancelled) return
        if (f) {
          if (!cacheRef.current.has(f.index)) cacheRef.current.set(f.index, f)
          setFrame(f)
          if (f.dataUrl) setImageUrl(f.dataUrl)
        }
        setLoading(false)
        prefetch(index + 1)
        prefetch(index + 2)
      })
      .catch(() => {
        if (!cancelled) setLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [index, getFrame, frameCount, prefetch])

  // Fetch + decode segmentation masks for the current frame when the overlay is on.
  useEffect(() => {
    if (!segmentationOn || !overlay || !frame) {
      setOverlays([])
      return
    }
    const frameNumber = frame.frameNumber
    const cached = overlayCacheRef.current.get(frameNumber)
    if (cached) {
      setOverlays(cached)
      return
    }

    let cancelled = false
    overlay
      .getSegmentation(frameNumber)
      .then(async (masks) => {
        if (cancelled) return
        const decoded = masks && masks.length ? await decodeMasks(masks) : []
        if (cancelled) return
        overlayCacheRef.current.set(frameNumber, decoded)
        setOverlays(decoded)
      })
      .catch(() => {
        if (!cancelled) setOverlays([])
      })
    return () => {
      cancelled = true
    }
  }, [segmentationOn, overlay, frame])

  const displayUrl = imageUrl ?? summary?.rgbPreview?.dataUrl ?? null

  // Decode the base image off the current URL, then trigger a canvas redraw.
  useEffect(() => {
    if (!displayUrl) {
      baseImgRef.current = null
      setDrawTick((tick) => tick + 1)
      return
    }
    const img = new Image()
    let active = true
    img.onload = () => {
      if (!active) return
      baseImgRef.current = img
      setDrawTick((tick) => tick + 1)
    }
    img.src = displayUrl
    return () => {
      active = false
    }
  }, [displayUrl])

  const visibleOverlays = useMemo(() => {
    if (!segmentationMaskLabel) return overlays
    const target = normalizeSegmentationLabel(segmentationMaskLabel)
    return overlays.filter((item) => normalizeSegmentationLabel(item.label) === target)
  }, [overlays, segmentationMaskLabel])

  // Composite the base frame and any mask overlays onto the canvas.
  useEffect(() => {
    const canvas = canvasRef.current
    if (!canvas) return
    const ctx = canvas.getContext('2d')
    if (!ctx) return
    const img = baseImgRef.current
    if (!img) {
      ctx.clearRect(0, 0, canvas.width, canvas.height)
      return
    }
    const w = img.naturalWidth || img.width
    const h = img.naturalHeight || img.height
    if (canvas.width !== w) canvas.width = w
    if (canvas.height !== h) canvas.height = h
    ctx.clearRect(0, 0, w, h)
    ctx.drawImage(img, 0, 0, w, h)

    const maskCanvasFor = (ov: DecodedOverlay, alpha = MASK_ALPHA) => {
      const tmp = document.createElement('canvas')
      tmp.width = ov.width
      tmp.height = ov.height
      const tctx = tmp.getContext('2d')
      if (!tctx) return null
      const imageData = tctx.createImageData(ov.width, ov.height)
      const data = imageData.data
      const [r, g, b] = ov.color
      for (let i = 0; i < ov.mask.length; i += 1) {
        if (ov.mask[i]) {
          const j = i * 4
          data[j] = r
          data[j + 1] = g
          data[j + 2] = b
          data[j + 3] = alpha
        }
      }
      tctx.putImageData(imageData, 0, 0)
      return tmp
    }

    if (segmentationMaskLabel) {
      const dim = document.createElement('canvas')
      dim.width = w
      dim.height = h
      const dctx = dim.getContext('2d')
      if (dctx) {
        dctx.fillStyle = 'rgba(0, 0, 0, 0.82)'
        dctx.fillRect(0, 0, w, h)
        dctx.globalCompositeOperation = 'destination-out'
        for (const ov of visibleOverlays) {
          const tmp = maskCanvasFor(ov, 255)
          if (tmp) dctx.drawImage(tmp, 0, 0, w, h)
        }
        dctx.globalCompositeOperation = 'source-over'
        ctx.drawImage(dim, 0, 0)
      }
    }

    for (const ov of visibleOverlays) {
      const tmp = maskCanvasFor(ov)
      if (!tmp) continue
      ctx.drawImage(tmp, 0, 0, w, h)
    }
  }, [drawTick, segmentationMaskLabel, visibleOverlays])

  // Playback loop: advance frames at fps * speed, stop at the end.
  useEffect(() => {
    if (!playing || frameCount <= 1) return
    let raf = 0
    let last = performance.now()
    let acc = 0
    const interval = 1000 / (fps * speed)
    const tick = (now: number) => {
      acc += now - last
      last = now
      if (acc >= interval) {
        const steps = Math.floor(acc / interval)
        acc -= steps * interval
        setVideoIndex((prev) => {
          const next = prev + steps
          if (next >= frameCount - 1) {
            setVideoPlaying(false)
            return frameCount - 1
          }
          return next
        })
      }
      raf = requestAnimationFrame(tick)
    }
    raf = requestAnimationFrame(tick)
    return () => cancelAnimationFrame(raf)
  }, [playing, speed, fps, frameCount, setVideoIndex, setVideoPlaying])

  const togglePlay = useCallback(() => {
    if (frameCount <= 1) return
    if (!playing && index >= frameCount - 1) setVideoIndex(0)
    setVideoPlaying(!playing)
  }, [frameCount, index, playing, setVideoIndex, setVideoPlaying])

  const step = useCallback(
    (delta: number) => {
      setVideoPlaying(false)
      setVideoIndex((prev) => prev + delta)
    },
    [setVideoIndex, setVideoPlaying],
  )

  const onKeyDown = useCallback(
    (event: KeyboardEvent) => {
      if (
        event.target instanceof HTMLInputElement ||
        event.target instanceof HTMLButtonElement ||
        event.target instanceof HTMLSelectElement
      ) {
        return
      }
      if (event.key === ' ') {
        event.preventDefault()
        togglePlay()
      } else if (event.key === 'ArrowLeft') {
        event.preventDefault()
        step(-1)
      } else if (event.key === 'ArrowRight') {
        event.preventDefault()
        step(1)
      }
    },
    [togglePlay, step],
  )

  const currentSeconds = index / fps
  const totalSeconds = summary && summary.durationSeconds > 0 ? summary.durationSeconds : (frameCount - 1) / fps
  const sortedMarkers = useMemo(() => [...markers].sort((a, b) => a.frameIndex - b.frameIndex), [markers])
  const markerSegments = useMemo(
    () => sortedMarkers.slice(0, -1).map((marker, idx) => [marker, sortedMarkers[idx + 1]] as const),
    [sortedMarkers],
  )

  const addMarker = useCallback(() => {
    if (frameCount === 0) return
    const name = titleCaseMarkerName(markerDraft, `Marker${markers.length + 1}`)
    const marker: VideoMarker = {
      id: createMarkerId(),
      name,
      reference: uniqueMarkerReference(name, markers),
      frameIndex: index,
      frameNumber: frame?.frameNumber ?? index,
      seconds: currentSeconds,
      color: markerColor,
    }
    onVideoStateChange((current) => ({
      ...current,
      markers: [...current.markers, marker].sort((a, b) => a.frameIndex - b.frameIndex),
    }))
    setMarkerDraft('')
    setMarkerColor(MARKER_COLORS[(markers.length + 1) % MARKER_COLORS.length])
  }, [currentSeconds, frame?.frameNumber, frameCount, index, markerColor, markerDraft, markers, onVideoStateChange])

  const updateMarker = useCallback(
    (id: string, patch: Partial<Pick<VideoMarker, 'color' | 'name'>>) => {
      onVideoStateChange((current) => ({
        ...current,
        markers: current.markers
          .map((marker) => {
            if (marker.id !== id) return marker
            const name = patch.name !== undefined ? titleCaseMarkerName(patch.name, marker.reference) : marker.name
            return {
              ...marker,
              ...patch,
              name,
              reference: patch.name !== undefined ? uniqueMarkerReference(name, current.markers, id) : marker.reference,
            }
          })
          .sort((a, b) => a.frameIndex - b.frameIndex),
      }))
    },
    [onVideoStateChange],
  )

  const deleteMarker = useCallback(
    (id: string) => {
      onVideoStateChange((current) => ({
        ...current,
        markers: current.markers.filter((marker) => marker.id !== id),
      }))
    },
    [onVideoStateChange],
  )

  if (!summary || frameCount === 0) {
    return (
      <div className="empty-panel">
        <Film size={28} aria-hidden="true" />
        <span>No frames to play</span>
      </div>
    )
  }

  return (
    <div className="video-player" tabIndex={0} onKeyDown={onKeyDown}>
      <div className="video-stage">
        <canvas ref={canvasRef} className={displayUrl ? '' : 'is-empty'} />
        {!displayUrl ? (
          <div className="video-stage-empty">
            <Film size={30} aria-hidden="true" />
            <span>{frame && frame.dataUrl === null ? 'This frame has no decodable RGB image' : 'Loading frame…'}</span>
          </div>
        ) : null}
        {segmentationOn ? (
          <div className="video-overlay-badge">
            {segmentationMaskLabel ? `mask: ${segmentationMaskLabel} (${visibleOverlays.length})` : `segmentation (${overlays.length})`}
          </div>
        ) : null}
        {loading ? <div className="video-loading" aria-hidden="true" /> : null}
      </div>

      <div className="video-controls">
        <button type="button" className="icon-button" onClick={() => step(-1)} title="Previous frame" disabled={index <= 0}>
          <ChevronLeft size={16} aria-hidden="true" />
        </button>
        <button type="button" className="icon-button play-button" onClick={togglePlay} title={playing ? 'Pause' : 'Play'}>
          {playing ? <Pause size={16} aria-hidden="true" /> : <Play size={16} aria-hidden="true" />}
        </button>
        <button
          type="button"
          className="icon-button"
          onClick={() => step(1)}
          title="Next frame"
          disabled={index >= frameCount - 1}
        >
          <ChevronRight size={16} aria-hidden="true" />
        </button>

        <input
          className="video-seek"
          type="range"
          min={0}
          max={frameCount - 1}
          step={1}
          value={index}
          onChange={(event) => {
            setVideoPlaying(false)
            setVideoIndex(Number(event.target.value))
          }}
          aria-label="Seek"
        />

        <span className="video-time">
          {timeLabel(currentSeconds)} / {timeLabel(totalSeconds)}
        </span>
        <span className="video-frame-count" title="Frame number">
          {index + 1} / {frameCount}
        </span>

        <label className="video-speed" title="Playback speed">
          <select value={speed} onChange={(event) => setVideoSpeed(Number(event.target.value))}>
            {SPEEDS.map((value) => (
              <option key={value} value={value}>
                {value}x
              </option>
            ))}
          </select>
        </label>
      </div>

      <div className="video-marker-panel">
        <div className="marker-create-row">
          <div className="marker-panel-title">
            <Tag size={14} aria-hidden="true" />
            <span>Markers</span>
          </div>
          <input
            className="marker-name-input"
            value={markerDraft}
            onChange={(event) => setMarkerDraft(event.target.value)}
            placeholder="NameOfMarker"
            spellCheck={false}
          />
          <input
            className="marker-color-input"
            type="color"
            value={markerColor}
            onChange={(event) => setMarkerColor(event.target.value)}
            aria-label="Marker color"
          />
          <button type="button" className="toolbar-button marker-insert" onClick={addMarker} title="Insert marker">
            <Plus size={14} aria-hidden="true" />
            <span>Insert</span>
          </button>
        </div>

        <div className="marker-track">
          {sortedMarkers.map((marker) => (
            <button
              type="button"
              className="marker-track-pin"
              key={marker.id}
              onClick={() => setVideoIndex(marker.frameIndex)}
              style={{
                left: `${frameCount > 1 ? (marker.frameIndex / (frameCount - 1)) * 100 : 0}%`,
                backgroundColor: marker.color,
              }}
              title={`@${marker.reference}`}
            />
          ))}
        </div>

        <div className="marker-list">
          {sortedMarkers.length === 0 ? (
            <span className="marker-empty">No markers</span>
          ) : (
            sortedMarkers.map((marker) => (
              <div className="marker-row" key={marker.id}>
                <button
                  type="button"
                  className="marker-ref"
                  onClick={() => setVideoIndex(marker.frameIndex)}
                  title={`Frame ${marker.frameIndex + 1}`}
                >
                  <span className="marker-swatch" style={{ backgroundColor: marker.color }} />
                  <span>@{marker.reference}</span>
                </button>
                <input
                  className="marker-name-edit"
                  value={marker.name}
                  onChange={(event) => updateMarker(marker.id, { name: event.target.value })}
                  spellCheck={false}
                  aria-label="Marker name"
                />
                <input
                  className="marker-color-input"
                  type="color"
                  value={marker.color}
                  onChange={(event) => updateMarker(marker.id, { color: event.target.value })}
                  aria-label="Marker color"
                />
                <span className="marker-time">{timeLabel(marker.seconds)}</span>
                <button type="button" className="icon-button" onClick={() => deleteMarker(marker.id)} title="Delete marker">
                  <Trash2 size={13} aria-hidden="true" />
                </button>
              </div>
            ))
          )}
        </div>

        {markerSegments.length ? (
          <div className="marker-segments">
            {markerSegments.map(([start, end]) => (
              <button
                type="button"
                className="marker-segment"
                key={`${start.id}-${end.id}`}
                onClick={() => setVideoIndex(start.frameIndex)}
                title={`${timeLabel(start.seconds)} - ${timeLabel(end.seconds)}`}
              >
                @{start.reference}-@{end.reference}
              </button>
            ))}
          </div>
        ) : null}
      </div>
    </div>
  )
}
