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
import { Activity, Boxes, Film, LoaderCircle, Triangle } from 'lucide-react'
import type {
  DomainTriangulation,
  MotionTrajectoryMode,
  VideoPlaybackState,
  VisFrame,
  VisSummary,
} from '../types'
import { useFrameSource } from '../lib/frameSource'
import { normalizeSegmentationLabel, useOverlay } from '../lib/overlay'
import { colorForObject } from '../lib/mask'
import { usePreparedOverlays } from '../lib/usePreparedOverlays'

type VideoPlayerProps = {
  summary: VisSummary | null
  videoState: VideoPlaybackState
  onVideoStateChange: Dispatch<SetStateAction<VideoPlaybackState>>
  segmentationViewer?: boolean
  motionCaptureViewer?: boolean
}

// Keep roughly two seconds of ordinary 30 fps playback ready, with a smaller
// rewind window. Frame reads are deliberately concurrency-limited because the
// Electron reader performs synchronous protobuf work in the main process.
const FRAME_PREFETCH_AHEAD = 60
const FRAME_PREFETCH_BEHIND = 16
const FRAME_CACHE_LIMIT = 112
const DECODED_IMAGE_CACHE_LIMIT = 48
const FRAME_LOAD_CONCURRENCY = 4

export default function VideoPlayer({
  summary,
  videoState,
  onVideoStateChange,
  segmentationViewer = false,
  motionCaptureViewer = false,
}: VideoPlayerProps) {
  const getFrame = useFrameSource()
  const overlay = useOverlay()
  const getSegmentation = overlay?.getSegmentation
  const getSegmentationLabels = overlay?.getSegmentationLabels
  const getDomainTriangulation = overlay?.getDomainTriangulation
  const getMotionCapture = overlay?.getMotionCapture
  const segmentationOn = segmentationViewer || (overlay?.segmentation ?? false)
  const [selectedEntity, setSelectedEntity] = useState<string | null>(null)
  const segmentationMaskLabel = segmentationViewer ? selectedEntity : overlay?.segmentationMaskLabel ?? null
  const { index, playing } = videoState

  const frameCount = summary?.frameCount ?? 0

  const [loadedFrame, setLoadedFrame] = useState<VisFrame | null>(null)
  const [entityLabels, setEntityLabels] = useState<string[]>([])
  const [labelsLoading, setLabelsLoading] = useState(false)
  const [triangulationOn, setTriangulationOn] = useState(false)
  const [triangulation, setTriangulation] = useState<DomainTriangulation | null>(null)
  const [triangulationLoading, setTriangulationLoading] = useState(false)
  const [motionTrajectoryMode, setMotionTrajectoryMode] =
    useState<MotionTrajectoryMode>('motion')

  // Base RGB frames retain their small random-access cache here. Segmentation
  // and motion layers are rasterized off-thread by usePreparedOverlays.
  const cacheRef = useRef<Map<number, VisFrame>>(new Map())
  const decodedImageCacheRef = useRef<Map<number, HTMLImageElement>>(new Map())
  const pendingImageDecodesRef = useRef(new Set<number>())
  const queuedFrameLoadsRef = useRef(new Set<number>())
  const frameLoadQueueRef = useRef<number[]>([])
  const activeFrameLoadsRef = useRef(0)
  const frameSourceGenerationRef = useRef(0)
  const pumpFrameQueueRef = useRef<() => void>(() => {})
  const getFrameRef = useRef(getFrame)
  const onVideoStateChangeRef = useRef(onVideoStateChange)
  const triangulationCacheRef = useRef<Map<number, DomainTriangulation | null>>(new Map())
  const canvasRef = useRef<HTMLCanvasElement>(null)
  const baseImgRef = useRef<HTMLImageElement | null>(null)
  const currentIndexRef = useRef(index)
  const drawCanvasRef = useRef<() => void>(() => {})
  getFrameRef.current = getFrame
  onVideoStateChangeRef.current = onVideoStateChange
  currentIndexRef.current = index
  const frameForCurrentRender = cacheRef.current.get(index) ?? loadedFrame
  const {
    segmentationLayer,
    segmentationMetadata: overlays,
    segmentationLoading,
    motionLayer,
    motionCapture,
    motionLoading,
    preparedCount,
    preparationTarget,
  } = usePreparedOverlays({
    summary,
    currentFrameNumber: frameForCurrentRender?.frameNumber ?? null,
    currentFrameIndex: index,
    segmentationEnabled: segmentationOn,
    selectedSegmentationLabel: segmentationMaskLabel,
    motionEnabled: motionCaptureViewer,
    motionTrajectoryMode,
    getSegmentation,
    getMotionCapture,
  })

  const clearCaches = useCallback(() => {
    for (const cached of cacheRef.current.values()) {
      if (cached.dataUrl && cached.dataUrl.startsWith('blob:')) URL.revokeObjectURL(cached.dataUrl)
    }
    cacheRef.current.clear()
    decodedImageCacheRef.current.clear()
    pendingImageDecodesRef.current.clear()
    queuedFrameLoadsRef.current.clear()
    frameLoadQueueRef.current = []
    activeFrameLoadsRef.current = 0
    frameSourceGenerationRef.current += 1
    triangulationCacheRef.current.clear()
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

  // The path is the durable source identity. Live project rescans legitimately
  // replace callback objects as file sizes and artifacts change; treating those
  // callbacks as a new video used to flush the buffer and stop playback.
  useEffect(() => {
    clearCaches()
    setLoadedFrame(null)
    setTriangulation(null)
    setTriangulationOn(false)
    onVideoStateChangeRef.current((current) => ({ ...current, playing: false }))
  }, [
    summary?.path,
    summary?.firstTimestampNs,
    summary?.firstFrameNumber,
    clearCaches,
  ])

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

  const decodeFrameImage = useCallback((targetFrame: VisFrame) => {
    if (
      !targetFrame.dataUrl
      || decodedImageCacheRef.current.has(targetFrame.index)
      || pendingImageDecodesRef.current.has(targetFrame.index)
    ) return
    pendingImageDecodesRef.current.add(targetFrame.index)
    const image = new Image()
    image.onload = () => {
      pendingImageDecodesRef.current.delete(targetFrame.index)
      decodedImageCacheRef.current.set(targetFrame.index, image)
      while (decodedImageCacheRef.current.size > DECODED_IMAGE_CACHE_LIMIT) {
        const candidates = [...decodedImageCacheRef.current.keys()]
          .filter((frameIndex) => frameIndex !== currentIndexRef.current)
          .sort(
            (left, right) =>
              Math.abs(right - currentIndexRef.current)
              - Math.abs(left - currentIndexRef.current),
          )
        const evicted = candidates[0]
        if (evicted === undefined) break
        decodedImageCacheRef.current.delete(evicted)
      }
      if (currentIndexRef.current === targetFrame.index) {
        baseImgRef.current = image
        setLoadedFrame((current) => (
          current?.index === targetFrame.index ? current : targetFrame
        ))
        drawCanvasRef.current()
      }
    }
    image.onerror = () => {
      pendingImageDecodesRef.current.delete(targetFrame.index)
    }
    image.src = targetFrame.dataUrl
  }, [])

  const rememberFrame = useCallback((frame: VisFrame) => {
    if (!cacheRef.current.has(frame.index)) cacheRef.current.set(frame.index, frame)
    while (cacheRef.current.size > FRAME_CACHE_LIMIT) {
      const candidates = [...cacheRef.current.keys()]
        .filter((frameIndex) => frameIndex !== currentIndexRef.current)
        .sort((left, right) => {
          const leftInWindow = left >= currentIndexRef.current - FRAME_PREFETCH_BEHIND
            && left <= currentIndexRef.current + FRAME_PREFETCH_AHEAD
          const rightInWindow = right >= currentIndexRef.current - FRAME_PREFETCH_BEHIND
            && right <= currentIndexRef.current + FRAME_PREFETCH_AHEAD
          if (leftInWindow !== rightInWindow) return leftInWindow ? 1 : -1
          return Math.abs(right - currentIndexRef.current)
            - Math.abs(left - currentIndexRef.current)
        })
      const evicted = candidates[0]
      if (evicted === undefined) break
      const stale = cacheRef.current.get(evicted)
      cacheRef.current.delete(evicted)
      decodedImageCacheRef.current.delete(evicted)
      if (stale?.dataUrl?.startsWith('blob:')) URL.revokeObjectURL(stale.dataUrl)
    }
  }, [])

  const pumpFrameQueue = useCallback(() => {
    const frameGetter = getFrameRef.current
    if (!frameGetter) return
    const generation = frameSourceGenerationRef.current
    while (
      activeFrameLoadsRef.current < FRAME_LOAD_CONCURRENCY
      && frameLoadQueueRef.current.length > 0
    ) {
      const target = frameLoadQueueRef.current.shift()
      if (target === undefined) break
      if (cacheRef.current.has(target)) {
        queuedFrameLoadsRef.current.delete(target)
        continue
      }
      activeFrameLoadsRef.current += 1
      void frameGetter(target)
        .then((frame) => {
          if (generation !== frameSourceGenerationRef.current || !frame) return
          rememberFrame(frame)
          decodeFrameImage(frame)
          if (currentIndexRef.current === frame.index) {
            setLoadedFrame((current) => current?.index === frame.index ? current : frame)
          }
        })
        .catch(() => {
          // A corrupt or still-being-written live frame must not interrupt the
          // rest of the forward buffer.
        })
        .finally(() => {
          if (generation !== frameSourceGenerationRef.current) return
          activeFrameLoadsRef.current = Math.max(0, activeFrameLoadsRef.current - 1)
          queuedFrameLoadsRef.current.delete(target)
          pumpFrameQueueRef.current()
        })
    }
  }, [decodeFrameImage, rememberFrame])
  pumpFrameQueueRef.current = pumpFrameQueue

  const queueFrame = useCallback((target: number, urgent = false) => {
    if (target < 0 || target >= frameCount || cacheRef.current.has(target)) return
    if (queuedFrameLoadsRef.current.has(target)) {
      if (urgent) {
        const position = frameLoadQueueRef.current.indexOf(target)
        if (position > 0) {
          frameLoadQueueRef.current.splice(position, 1)
          frameLoadQueueRef.current.unshift(target)
        }
      }
      return
    }
    queuedFrameLoadsRef.current.add(target)
    if (urgent) frameLoadQueueRef.current.unshift(target)
    else frameLoadQueueRef.current.push(target)
    pumpFrameQueueRef.current()
  }, [frameCount])

  // Load the current image without clearing the last presented frame, then keep
  // a bounded forward/rewind window warm. Superseded queued reads are discarded
  // before they can crowd out the frames immediately ahead of the playhead.
  useEffect(() => {
    if (!getFrameRef.current || frameCount === 0) return
    const cached = cacheRef.current.get(index)
    if (cached) {
      setLoadedFrame((current) => current?.index === cached.index ? current : cached)
      const decoded = decodedImageCacheRef.current.get(index)
      if (decoded) {
        baseImgRef.current = decoded
        drawCanvasRef.current()
      } else {
        decodeFrameImage(cached)
      }
    }

    const minimum = Math.max(0, index - FRAME_PREFETCH_BEHIND)
    const maximum = Math.min(frameCount - 1, index + FRAME_PREFETCH_AHEAD)
    frameLoadQueueRef.current = frameLoadQueueRef.current.filter((target) => {
      const keep = target >= minimum && target <= maximum
      if (!keep) queuedFrameLoadsRef.current.delete(target)
      return keep
    })
    queueFrame(index, true)
    for (let target = index + 1; target <= maximum; target += 1) {
      queueFrame(target)
    }
    for (let target = index - 1; target >= minimum; target -= 1) {
      queueFrame(target)
    }
  }, [decodeFrameImage, index, frameCount, queueFrame])

  const frame = frameForCurrentRender
  const displayUrl = frame?.dataUrl ?? summary?.rgbPreview?.dataUrl ?? null

  useEffect(() => {
    if (!segmentationViewer || !triangulationOn || !getDomainTriangulation || !frame) {
      setTriangulation(null)
      setTriangulationLoading(false)
      return
    }
    if (triangulationCacheRef.current.has(frame.frameNumber)) {
      setTriangulation(triangulationCacheRef.current.get(frame.frameNumber) ?? null)
      return
    }

    let cancelled = false
    setTriangulationLoading(true)
    getDomainTriangulation(frame.frameNumber)
      .then((result) => {
        if (cancelled) return
        triangulationCacheRef.current.set(frame.frameNumber, result)
        setTriangulation(result)
      })
      .catch(() => {
        if (!cancelled) setTriangulation(null)
      })
      .finally(() => {
        if (!cancelled) setTriangulationLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [frame, getDomainTriangulation, segmentationViewer, triangulationOn])

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

  const motionLayerRef = useRef(motionLayer)
  const segmentationLayerRef = useRef(segmentationLayer)
  const triangulationRef = useRef(triangulation)
  motionLayerRef.current = motionLayer
  segmentationLayerRef.current = segmentationLayer
  triangulationRef.current = triangulation

  // Composite a decoded base frame with already-rasterized analysis layers.
  // The cache and refs are the source of truth, avoiding a React state update
  // for every frame during playback.
  const drawCanvas = useCallback(() => {
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
    ctx.globalAlpha = 1

    ctx.save()
    ctx.imageSmoothingEnabled = true
    try {
      if (motionLayerRef.current) ctx.drawImage(motionLayerRef.current, 0, 0, w, h)
      if (segmentationLayerRef.current) {
        ctx.drawImage(segmentationLayerRef.current, 0, 0, w, h)
      }
    } catch {
      // A just-evicted worker bitmap can overlap one React paint during a rapid
      // seek. The next prepared revision replaces it without taking down the UI.
    }
    ctx.restore()

    if (triangulationRef.current) {
      const drawQuad = (
        coordinates: number[],
        color: string,
        label: string,
        dash: number[] = [],
      ) => {
        if (coordinates.length < 8) return
        ctx.save()
        ctx.strokeStyle = 'rgba(0, 0, 0, 0.88)'
        ctx.lineWidth = 6
        ctx.lineJoin = 'round'
        ctx.beginPath()
        ctx.moveTo(coordinates[0], coordinates[1])
        for (let point = 1; point < 4; point += 1) {
          ctx.lineTo(coordinates[point * 2], coordinates[point * 2 + 1])
        }
        ctx.closePath()
        ctx.stroke()
        ctx.strokeStyle = color
        ctx.lineWidth = 2.5
        ctx.setLineDash(dash)
        ctx.stroke()
        ctx.setLineDash([])
        ctx.font = '600 12px Inter, sans-serif'
        ctx.lineWidth = 4
        ctx.strokeStyle = 'rgba(0, 0, 0, 0.86)'
        ctx.strokeText(label, coordinates[0] + 7, coordinates[1] - 7)
        ctx.fillStyle = color
        ctx.fillText(label, coordinates[0] + 7, coordinates[1] - 7)
        ctx.restore()
      }
      drawQuad(triangulationRef.current.tableQuad, '#62d2a2', 'table')
      drawQuad(triangulationRef.current.netQuad, '#f0b35a', 'net', [7, 4])
    }
  }, [])
  drawCanvasRef.current = drawCanvas

  useEffect(() => {
    if (frame?.dataUrl) {
      const decoded = decodedImageCacheRef.current.get(frame.index)
      if (decoded) {
        baseImgRef.current = decoded
        drawCanvas()
      } else {
        decodeFrameImage(frame)
      }
      return
    }
    const previewUrl = summary?.rgbPreview?.dataUrl
    if (!previewUrl) {
      baseImgRef.current = null
      drawCanvas()
      return
    }
    const img = new Image()
    let active = true
    img.onload = () => {
      if (!active) return
      baseImgRef.current = img
      drawCanvas()
    }
    img.src = previewUrl
    return () => {
      active = false
    }
  }, [
    decodeFrameImage,
    drawCanvas,
    frame?.dataUrl,
    frame?.index,
    summary?.rgbPreview?.dataUrl,
  ])

  useEffect(() => {
    drawCanvas()
  }, [drawCanvas, motionLayer, segmentationLayer, triangulation])

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
    const EmptyIcon = motionCaptureViewer ? Activity : segmentationViewer ? Boxes : Film
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
          {segmentationMaskLabel
            ? `mask: ${segmentationMaskLabel} (${visibleOverlays.length})`
            : `segmentation (${overlays.length})`}
          {preparationTarget > 0 && preparedCount < preparationTarget
            ? ` · preparing ${preparedCount}/${preparationTarget}`
            : ' · buffered'}
        </div>
      ) : null}
      {motionCaptureViewer ? (
        <div className="motion-capture-toolbar">
          <div className="video-overlay-badge motion-capture">
            {motionLoading
              ? 'motion capture · loading'
              : `motion capture · heatmap · ${
                  motionCapture?.tracks.filter(
                    (track) => track.kind === motionTrajectoryMode,
                  ).length ?? 0
                } ${
                  motionCapture?.tracks.filter(
                    (track) => track.kind === motionTrajectoryMode,
                  ).length === 1 ? 'track' : 'tracks'
                }`}
            {preparationTarget > 0 && preparedCount < preparationTarget
              ? ` · preparing ${preparedCount}/${preparationTarget}`
              : ' · buffered'}
          </div>
          <div
            className="motion-trajectory-mode-control"
            role="tablist"
            aria-label="Trajectory source"
          >
            <button
              type="button"
              role="tab"
              aria-selected={motionTrajectoryMode === 'motion'}
              className={motionTrajectoryMode === 'motion' ? 'is-active' : ''}
              onClick={() => setMotionTrajectoryMode('motion')}
            >
              RAFT
            </button>
            <button
              type="button"
              role="tab"
              aria-selected={motionTrajectoryMode === 'segmentation'}
              className={motionTrajectoryMode === 'segmentation' ? 'is-active' : ''}
              onClick={() => setMotionTrajectoryMode('segmentation')}
            >
              Segmentation
            </button>
          </div>
        </div>
      ) : null}
      {segmentationViewer && triangulationOn ? (
        <div className="video-overlay-badge triangulation">
          {triangulationLoading
            ? 'triangulation · loading'
            : triangulation
              ? `triangulation · ${triangulation.netQuad.length >= 8 ? 'table + net' : 'table'}`
              : 'triangulation · unavailable'}
        </div>
      ) : null}
      {!frame || segmentationLoading || motionLoading || triangulationLoading ? (
        <div className="video-loading" aria-hidden="true" />
      ) : null}
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
          className={triangulationOn ? 'segmentation-triangulation-toggle is-active' : 'segmentation-triangulation-toggle'}
          role="switch"
          aria-checked={triangulationOn}
          disabled={!overlay?.triangulationAvailable}
          onClick={() => setTriangulationOn((current) => !current)}
          title={
            overlay?.triangulationAvailable
              ? 'Overlay generated table and net rectangles'
              : 'No Pongtown or Snookerstown geometry is available'
          }
        >
          <span className="segmentation-triangulation-icon">
            <Triangle size={14} aria-hidden="true" />
          </span>
          <span className="segmentation-entity-copy">
            <strong>Triangulation</strong>
            <small>
              {overlay?.triangulationAvailable
                ? 'Generated table and net rectangles'
                : 'No domain geometry available'}
            </small>
          </span>
          <span className="segmentation-toggle-state">{triangulationOn ? 'On' : 'Off'}</span>
        </button>

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
