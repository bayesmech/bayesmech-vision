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
import { Boxes, Film, LoaderCircle } from 'lucide-react'
import type { VideoPlaybackState, VisFrame, VisSummary } from '../types'
import { useFrameSource } from '../lib/frameSource'
import { normalizeSegmentationLabel, useOverlay } from '../lib/overlay'
import { colorForObject, decodeMasks, type DecodedOverlay } from '../lib/mask'

type VideoPlayerProps = {
  summary: VisSummary | null
  videoState: VideoPlaybackState
  onVideoStateChange: Dispatch<SetStateAction<VideoPlaybackState>>
  segmentationViewer?: boolean
}

const MASK_ALPHA = 140 // 0..255

export default function VideoPlayer({ summary, videoState, onVideoStateChange, segmentationViewer = false }: VideoPlayerProps) {
  const getFrame = useFrameSource()
  const overlay = useOverlay()
  const getSegmentation = overlay?.getSegmentation
  const getSegmentationLabels = overlay?.getSegmentationLabels
  const segmentationOn = segmentationViewer || (overlay?.segmentation ?? false)
  const [selectedEntity, setSelectedEntity] = useState<string | null>(null)
  const segmentationMaskLabel = segmentationViewer ? selectedEntity : overlay?.segmentationMaskLabel ?? null
  const { index, playing } = videoState

  const frameCount = summary?.frameCount ?? 0

  const [frame, setFrame] = useState<VisFrame | null>(null)
  const [imageUrl, setImageUrl] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)
  const [overlays, setOverlays] = useState<DecodedOverlay[]>([])
  const [entityLabels, setEntityLabels] = useState<string[]>([])
  const [labelsLoading, setLabelsLoading] = useState(false)

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
    setSelectedEntity(null)
    if (!segmentationViewer || !getSegmentationLabels) {
      setEntityLabels([])
      setLabelsLoading(false)
      return
    }

    let cancelled = false
    setLabelsLoading(true)
    getSegmentationLabels()
      .then((labels) => {
        if (!cancelled) setEntityLabels(labels)
      })
      .catch(() => {
        if (!cancelled) setEntityLabels([])
      })
      .finally(() => {
        if (!cancelled) setLabelsLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [getSegmentationLabels, segmentationViewer, summary?.path])

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
    if (!segmentationOn || !getSegmentation || !frame) {
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
    getSegmentation(frameNumber)
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
  }, [segmentationOn, getSegmentation, frame])

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

  const listedEntities = useMemo(() => {
    const unique = new Map<string, string>()
    for (const label of entityLabels) {
      const key = normalizeSegmentationLabel(label)
      if (key) unique.set(key, label)
    }
    for (const item of overlays) {
      const key = normalizeSegmentationLabel(item.label)
      if (key && !unique.has(key)) unique.set(key, item.label)
    }
    return [...unique.values()]
  }, [entityLabels, overlays])

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

  if (!summary || frameCount === 0) {
    const EmptyIcon = segmentationViewer ? Boxes : Film
    return (
      <div className="empty-panel">
        <EmptyIcon size={28} aria-hidden="true" />
        <span>No frames to play</span>
      </div>
    )
  }

  const videoStage = (
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
  )

  if (!segmentationViewer) {
    return (
      <div className="video-player" tabIndex={0} onKeyDown={onKeyDown}>
        {videoStage}
      </div>
    )
  }

  return (
    <div className="segmentation-viewer" tabIndex={0} onKeyDown={onKeyDown}>
      <div className="segmentation-video-pane">{videoStage}</div>
      <aside className="segmentation-entities" aria-label="Segmented entities">
        <header className="segmentation-entities-header">
          <div>
            <span className="eyebrow">Segmentation</span>
            <h3>Entities</h3>
          </div>
          <span className="segmentation-entity-count">{listedEntities.length}</span>
        </header>

        <button
          type="button"
          className={selectedEntity === null ? 'segmentation-entity is-active' : 'segmentation-entity'}
          aria-pressed={selectedEntity === null}
          onClick={() => setSelectedEntity(null)}
        >
          <span className="segmentation-all-icon"><Boxes size={14} aria-hidden="true" /></span>
          <span className="segmentation-entity-copy">
            <strong>All entities</strong>
            <small>{overlays.length} {overlays.length === 1 ? 'mask' : 'masks'} in this frame</small>
          </span>
        </button>

        <div className="segmentation-entity-list">
          {labelsLoading ? (
            <div className="segmentation-entities-state">
              <LoaderCircle className="spin" size={15} aria-hidden="true" />
              <span>Reading entities…</span>
            </div>
          ) : null}
          {!labelsLoading && listedEntities.length === 0 ? (
            <div className="segmentation-entities-state">No named entities found.</div>
          ) : null}
          {listedEntities.map((label, index) => {
            const normalized = normalizeSegmentationLabel(label)
            const current = overlays.filter((item) => normalizeSegmentationLabel(item.label) === normalized)
            const color = current[0]?.color ?? colorForObject(index)
            const colorCss = `rgb(${color[0]} ${color[1]} ${color[2]})`
            const active = selectedEntity !== null && normalizeSegmentationLabel(selectedEntity) === normalized
            return (
              <button
                type="button"
                className={`${active ? 'segmentation-entity is-active' : 'segmentation-entity'}${current.length ? '' : ' is-absent'}`}
                aria-pressed={active}
                key={normalized}
                onClick={() => setSelectedEntity(active ? null : label)}
                title={current.length ? `${label}: ${current.length} mask${current.length === 1 ? '' : 's'} in this frame` : `${label}: not present in this frame`}
              >
                <span className="segmentation-entity-swatch" style={{ backgroundColor: colorCss }} />
                <span className="segmentation-entity-copy">
                  <strong>{label}</strong>
                  <small>{current.length ? `${current.length} ${current.length === 1 ? 'instance' : 'instances'} in frame` : 'Not in this frame'}</small>
                </span>
              </button>
            )
          })}
        </div>
      </aside>
    </div>
  )
}
