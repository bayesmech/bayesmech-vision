import React, { useEffect, useMemo, useRef, useState } from 'react'
import { useDashboard } from '../context/DashboardContext'
import { fetchRecordingMotioncapData } from '../services/api'
import { decodeMotionHeatmapData } from '../services/proto'
import type { MotioncapData, MotioncapFrameRecord, MotioncapTrackLegendItem } from '../types'

const MOTIONCAP_TAIL_LENGTH = 30
const MAX_RENDERED_HEATMAP_CACHE_SIZE = 12

interface RenderedHeatmap {
  width: number
  height: number
  imageData: ImageData
}

const colorToCss = (color: [number, number, number], alpha = 1): string =>
  `rgba(${color[0]}, ${color[1]}, ${color[2]}, ${alpha})`

const scaledColorToCss = (color: [number, number, number], scale: number): string =>
  `rgb(${Math.round(color[0] * scale)}, ${Math.round(color[1] * scale)}, ${Math.round(color[2] * scale)})`

const clamp01 = (value: number): number => Math.max(0, Math.min(1, value))

function writeJetColor(value: number, pixels: Uint8ClampedArray, offset: number): void {
  const v = value / 255
  pixels[offset] = Math.round(255 * clamp01(1.5 - Math.abs(4 * v - 3)))
  pixels[offset + 1] = Math.round(255 * clamp01(1.5 - Math.abs(4 * v - 2)))
  pixels[offset + 2] = Math.round(255 * clamp01(1.5 - Math.abs(4 * v - 1)))
  pixels[offset + 3] = 255
}

function renderHeatmap(record: MotioncapFrameRecord): RenderedHeatmap {
  const decoded = decodeMotionHeatmapData(record.heatmapData)
  const imageData = new ImageData(decoded.width, decoded.height)
  const pixels = imageData.data

  for (let i = 0; i < decoded.values.length; i++) {
    writeJetColor(decoded.values[i], pixels, i * 4)
  }

  return { width: decoded.width, height: decoded.height, imageData }
}

function getRenderedHeatmap(
  record: MotioncapFrameRecord,
  cache: Map<number, RenderedHeatmap>,
): RenderedHeatmap {
  const cached = cache.get(record.heatmapIndex)
  if (cached) {
    cache.delete(record.heatmapIndex)
    cache.set(record.heatmapIndex, cached)
    return cached
  }

  const rendered = renderHeatmap(record)
  cache.set(record.heatmapIndex, rendered)
  if (cache.size > MAX_RENDERED_HEATMAP_CACHE_SIZE) {
    const oldestKey = cache.keys().next().value
    if (oldestKey !== undefined) cache.delete(oldestKey)
  }
  return rendered
}

function clearCanvas(canvas: HTMLCanvasElement | null): void {
  if (!canvas) return
  const ctx = canvas.getContext('2d')
  if (!ctx) return
  ctx.clearRect(0, 0, canvas.width, canvas.height)
}

function drawImageToCanvas(canvas: HTMLCanvasElement, image: HTMLImageElement): void {
  const ctx = canvas.getContext('2d')
  if (!ctx) return
  canvas.width = image.naturalWidth || 1
  canvas.height = image.naturalHeight || 1
  ctx.clearRect(0, 0, canvas.width, canvas.height)
  ctx.drawImage(image, 0, 0, canvas.width, canvas.height)
}

function drawHeatmapToCanvas(canvas: HTMLCanvasElement, heatmap: RenderedHeatmap): void {
  const ctx = canvas.getContext('2d')
  if (!ctx) return
  canvas.width = heatmap.width
  canvas.height = heatmap.height
  ctx.clearRect(0, 0, canvas.width, canvas.height)
  ctx.putImageData(heatmap.imageData, 0, 0)
}

const MotioncapLegend: React.FC<{
  tracks: MotioncapTrackLegendItem[]
  available: boolean | null
  hasRecording: boolean
  isLive: boolean
}> = ({ tracks, available, hasRecording, isLive }) => (
  <div className="stream-card" style={{ display: 'flex', flexDirection: 'column' }}>
    <div className="stream-header">
      <span className="stream-title">Tracks</span>
    </div>
    <div className="motioncap-legend-list">
      {isLive && (
        <div className="motioncap-empty-state">
          Motion capture overlays are available for recordings only.
        </div>
      )}
      {!isLive && hasRecording && available === null && (
        <div className="motioncap-empty-state">
          Loading motion capture tracks...
        </div>
      )}
      {!isLive && hasRecording && available === false && (
        <div className="motioncap-empty-state">
          No motion capture file found for this recording.
        </div>
      )}
      {!isLive && hasRecording && available === true && tracks.length === 0 && (
        <div className="motioncap-empty-state">
          No motion tracks detected.
        </div>
      )}
      {!isLive && hasRecording && available === true && tracks.map((track) => (
        <div key={track.track_id} className="motioncap-legend-row">
          <span
            className="motioncap-legend-swatch"
            style={{ background: colorToCss(track.color), boxShadow: `0 0 0 1px ${colorToCss(track.color, 0.35)}` }}
          />
          <span className="motioncap-legend-label">Track {track.track_id}</span>
          <span className="motioncap-legend-meta">
            {(track.presence_fraction * 100).toFixed(0)}%
          </span>
        </div>
      ))}
    </div>
  </div>
)

const MotioncapPanel: React.FC = () => {
  const {
    displayedFrame,
    currentIndex,
    isLive,
    currentRecordingName,
  } = useDashboard()
  const [motioncapData, setMotioncapData] = useState<MotioncapData | null>(null)
  const [motioncapAvailable, setMotioncapAvailable] = useState<boolean | null>(
    currentRecordingName ? null : false,
  )
  const [hasBaseFrame, setHasBaseFrame] = useState(false)
  const [hasHeatmap, setHasHeatmap] = useState(false)
  const [heatmapSize, setHeatmapSize] = useState<{ width: number; height: number } | null>(null)
  const baseCanvasRef = useRef<HTMLCanvasElement>(null)
  const heatmapCanvasRef = useRef<HTMLCanvasElement>(null)
  const heatmapCacheRef = useRef<Map<number, RenderedHeatmap>>(new Map())

  useEffect(() => {
    heatmapCacheRef.current.clear()
    clearCanvas(baseCanvasRef.current)
    clearCanvas(heatmapCanvasRef.current)
    setMotioncapData(null)
    setHasHeatmap(false)
    setHasBaseFrame(false)
    setHeatmapSize(null)
    setMotioncapAvailable(currentRecordingName && !isLive ? null : false)
  }, [currentRecordingName, isLive])

  useEffect(() => {
    if (isLive || !currentRecordingName) {
      return
    }

    let cancelled = false
    fetchRecordingMotioncapData(currentRecordingName)
      .then((data) => {
        if (cancelled) return
        heatmapCacheRef.current.clear()
        setMotioncapData(data)
        setMotioncapAvailable(data !== null && data.frames.length > 0)
      })
      .catch(() => {
        if (cancelled) return
        heatmapCacheRef.current.clear()
        setMotioncapData(null)
        setMotioncapAvailable(false)
      })

    return () => {
      cancelled = true
    }
  }, [currentRecordingName, isLive])

  const currentMotionFrame = useMemo(() => {
    if (!motioncapData) return undefined
    const frameNumber = displayedFrame?.frame_number
    if (frameNumber !== undefined) {
      const record = motioncapData.byFrameNumber.get(frameNumber)
      if (record) return record
    }
    return motioncapData.byHeatmapIndex.get(currentIndex)
  }, [currentIndex, displayedFrame?.frame_number, motioncapData])

  useEffect(() => {
    const url = displayedFrame?.rgbBlobUrl
    if (!url || motioncapAvailable !== true) {
      clearCanvas(baseCanvasRef.current)
      setHasBaseFrame(false)
      return
    }

    let cancelled = false
    const image = new Image()
    image.onload = () => {
      if (cancelled) return
      const canvas = baseCanvasRef.current
      if (!canvas) return
      drawImageToCanvas(canvas, image)
      setHasBaseFrame(true)
    }
    image.onerror = () => {
      if (!cancelled) {
        clearCanvas(baseCanvasRef.current)
        setHasBaseFrame(false)
      }
    }
    image.src = url

    return () => {
      cancelled = true
    }
  }, [displayedFrame?.rgbBlobUrl, motioncapAvailable])

  useEffect(() => {
    if (motioncapAvailable !== true || !currentMotionFrame) {
      clearCanvas(heatmapCanvasRef.current)
      setHasHeatmap(false)
      setHeatmapSize(null)
      return
    }

    try {
      const rendered = getRenderedHeatmap(currentMotionFrame, heatmapCacheRef.current)
      const canvas = heatmapCanvasRef.current
      if (!canvas) return
      drawHeatmapToCanvas(canvas, rendered)
      setHeatmapSize({ width: rendered.width, height: rendered.height })
      setHasHeatmap(true)
    } catch {
      clearCanvas(heatmapCanvasRef.current)
      setHasHeatmap(false)
      setHeatmapSize(null)
    }
  }, [currentMotionFrame, motioncapAvailable])

  const tracks = motioncapData?.tracks ?? []
  const frameWidth = displayedFrame?.rgb_width ?? heatmapSize?.width ?? 1920
  const frameHeight = displayedFrame?.rgb_height ?? heatmapSize?.height ?? 1080
  const motionFrameIndex = currentMotionFrame?.heatmapIndex ?? currentIndex

  const visibleTracks = useMemo(() => {
    if (!currentMotionFrame) return []
    const tailStart = Math.max(0, motionFrameIndex - MOTIONCAP_TAIL_LENGTH)
    return tracks
      .map((track) => ({
        ...track,
        visiblePositions: track.positions
          .filter((position) => position.frame_idx >= tailStart && position.frame_idx <= motionFrameIndex)
          .sort((a, b) => a.frame_idx - b.frame_idx),
      }))
      .filter((track) => track.visiblePositions.length > 0)
  }, [currentMotionFrame, motionFrameIndex, tracks])

  const placeholderText = isLive
    ? 'Motion capture overlays are available for recordings only.'
    : !currentRecordingName
      ? 'Load a recording to inspect motion capture overlays.'
      : motioncapAvailable === null
        ? 'Loading motion capture overlay...'
        : !motioncapAvailable
        ? 'No motion capture file found for this recording.'
        : !currentMotionFrame
        ? 'No motion capture heatmap for this frame.'
        : 'Loading motion capture overlay...'

  return (
    <div className="dashboard-motioncap-grid">
      <div className="stream-card">
        <div className="stream-header">
          <span className="stream-title">Motion Capture</span>
        </div>
        <div className="motioncap-viewer">
          {motioncapAvailable === true ? (
            <>
              <canvas
                ref={baseCanvasRef}
                aria-label="Motion capture base frame"
                role="img"
                className="motioncap-layer"
                style={{ display: hasBaseFrame ? 'block' : 'none' }}
              />
              <canvas
                ref={heatmapCanvasRef}
                aria-label="Motion capture heatmap"
                role="img"
                className="motioncap-layer motioncap-heatmap"
                style={{ display: hasHeatmap ? 'block' : 'none' }}
              />
              <svg
                className="motioncap-svg"
                viewBox={`0 0 ${frameWidth} ${frameHeight}`}
                preserveAspectRatio="xMidYMid meet"
              >
                {visibleTracks.map((track) => {
                  const currentPosition = track.visiblePositions.find(
                    (position) => position.frame_idx === motionFrameIndex,
                  )
                  const tailDenom = Math.max(track.visiblePositions.length - 1, 1)
                  return (
                    <g key={track.track_id}>
                      {track.visiblePositions.slice(1).map((pos, idx) => {
                        const prev = track.visiblePositions[idx]
                        const fade = ((idx + 1) / tailDenom) ** 1.5
                        return (
                          <line
                            key={`${track.track_id}-${pos.frame_idx}`}
                            x1={prev.cx}
                            y1={prev.cy}
                            x2={pos.cx}
                            y2={pos.cy}
                            stroke={scaledColorToCss(track.color, fade)}
                            strokeWidth={1}
                            strokeLinecap="round"
                          />
                        )
                      })}
                      {currentPosition && (
                        currentPosition.interpolated ? (
                          <g stroke={colorToCss(track.color, 0.95)} strokeWidth={1}>
                            <line x1={currentPosition.cx - 5} y1={currentPosition.cy} x2={currentPosition.cx + 5} y2={currentPosition.cy} />
                            <line x1={currentPosition.cx} y1={currentPosition.cy - 5} x2={currentPosition.cx} y2={currentPosition.cy + 5} />
                          </g>
                        ) : (
                          <circle
                            cx={currentPosition.cx}
                            cy={currentPosition.cy}
                            r={6}
                            fill="none"
                            stroke={colorToCss(track.color, 0.95)}
                            strokeWidth={2}
                          />
                        )
                      )}
                      {currentPosition && (
                        <text
                          x={currentPosition.cx + 8}
                          y={currentPosition.cy - 8}
                          fill={colorToCss(track.color, 0.95)}
                          fontSize={13}
                          fontFamily="Arial, sans-serif"
                        >
                          {`T${track.track_id}`}
                        </text>
                      )}
                    </g>
                  )
                })}
              </svg>
              {!hasBaseFrame && !hasHeatmap && (
                <div className="no-stream" style={{ textAlign: 'center', opacity: 0.5 }}>
                  <div>{placeholderText}</div>
                </div>
              )}
            </>
          ) : (
            <div className="no-stream" style={{ textAlign: 'center', opacity: 0.5 }}>
              <div>{placeholderText}</div>
            </div>
          )}
        </div>
      </div>

      <MotioncapLegend
        tracks={tracks}
        available={motioncapAvailable}
        hasRecording={!!currentRecordingName}
        isLive={isLive}
      />
    </div>
  )
}

export default MotioncapPanel
