import React, { useEffect, useMemo, useRef, useState } from 'react'
import { useDashboard } from '../context/DashboardContext'
import { fetchPlaybackMotioncapHeatmap, fetchPlaybackMotioncapTracks } from '../services/api'
import type { MotioncapTrackLegendItem } from '../types'

const MOTIONCAP_TAIL_LENGTH = 30
const MAX_HEATMAP_CACHE_SIZE = 120

const colorToCss = (color: [number, number, number], alpha = 1): string =>
  `rgba(${color[0]}, ${color[1]}, ${color[2]}, ${alpha})`

const revokeUrls = (cache: Map<number, string>): void => {
  cache.forEach((url) => URL.revokeObjectURL(url))
  cache.clear()
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
      <span className="stream-badge">KEY</span>
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
  const [tracks, setTracks] = useState<MotioncapTrackLegendItem[]>([])
  const [motioncapAvailable, setMotioncapAvailable] = useState<boolean | null>(
    currentRecordingName ? null : false,
  )
  const [heatmapUrl, setHeatmapUrl] = useState<string | undefined>(undefined)
  const heatmapCacheRef = useRef<Map<number, string>>(new Map())
  const requestIdRef = useRef(0)

  useEffect(() => {
    const cache = heatmapCacheRef.current
    return () => revokeUrls(cache)
  }, [])

  useEffect(() => {
    if (isLive || !currentRecordingName) {
      return
    }

    let cancelled = false
    fetchPlaybackMotioncapTracks()
      .then((data) => {
        if (cancelled) return
        setTracks(data.tracks)
        setMotioncapAvailable(data.available)
      })
      .catch(() => {
        if (cancelled) return
        setTracks([])
        setMotioncapAvailable(false)
      })

    return () => {
      cancelled = true
    }
  }, [currentRecordingName, isLive])

  useEffect(() => {
    if (isLive || !currentRecordingName || motioncapAvailable !== true || !displayedFrame?.rgbBlobUrl) {
      return
    }

    const cached = heatmapCacheRef.current.get(currentIndex)
    if (cached) {
      setHeatmapUrl(cached)
      return
    }

    const requestId = requestIdRef.current + 1
    requestIdRef.current = requestId

    fetchPlaybackMotioncapHeatmap(currentIndex)
      .then((blob) => {
        if (requestIdRef.current !== requestId) return
        const url = URL.createObjectURL(blob)
        heatmapCacheRef.current.set(currentIndex, url)
        if (heatmapCacheRef.current.size > MAX_HEATMAP_CACHE_SIZE) {
          const oldestKey = heatmapCacheRef.current.keys().next().value
          if (oldestKey !== undefined) {
            const oldestUrl = heatmapCacheRef.current.get(oldestKey)
            if (oldestUrl) URL.revokeObjectURL(oldestUrl)
            heatmapCacheRef.current.delete(oldestKey)
          }
        }
        setHeatmapUrl(url)
      })
      .catch(() => {
        if (requestIdRef.current !== requestId) return
        setHeatmapUrl(undefined)
      })
  }, [currentIndex, currentRecordingName, displayedFrame?.rgbBlobUrl, isLive, motioncapAvailable])

  const frameWidth = displayedFrame?.rgb_width ?? 1920
  const frameHeight = displayedFrame?.rgb_height ?? 1080

  const visibleTracks = useMemo(() => {
    const tailStart = Math.max(0, currentIndex - MOTIONCAP_TAIL_LENGTH)
    return tracks
      .map((track) => ({
        ...track,
        visiblePositions: track.positions.filter(
          (position) => position.frame_idx >= tailStart && position.frame_idx <= currentIndex,
        ),
      }))
      .filter((track) => track.visiblePositions.length > 1)
  }, [currentIndex, tracks])

  const placeholderText = isLive
    ? 'Motion capture overlays are available for recordings only.'
    : !currentRecordingName
      ? 'Load a recording to inspect motion capture overlays.'
      : motioncapAvailable === null
        ? 'Loading motion capture overlay...'
        : !motioncapAvailable
        ? 'No motion capture file found for this recording.'
        : 'Loading motion capture overlay...'

  return (
    <div className="dashboard-motioncap-grid">
      <div className="stream-card">
        <div className="stream-header">
          <span className="stream-title">Motion Capture</span>
          <span className="stream-badge">MOTION</span>
        </div>
        <div className="motioncap-viewer">
          {displayedFrame?.rgbBlobUrl && motioncapAvailable === true ? (
            <>
              <img
                src={displayedFrame.rgbBlobUrl}
                alt="Motion capture base frame"
                className="motioncap-layer"
              />
              {heatmapUrl && (
                <img
                  src={heatmapUrl}
                  alt="Motion capture heatmap"
                  className="motioncap-layer motioncap-heatmap"
                />
              )}
              <svg
                className="motioncap-svg"
                viewBox={`0 0 ${frameWidth} ${frameHeight}`}
                preserveAspectRatio="xMidYMid meet"
              >
                {visibleTracks.map((track) => (
                  <polyline
                    key={track.track_id}
                    points={track.visiblePositions.map((pos) => `${pos.cx},${pos.cy}`).join(' ')}
                    fill="none"
                    stroke={colorToCss(track.color, 0.95)}
                    strokeWidth={3}
                    strokeLinecap="round"
                    strokeLinejoin="round"
                  />
                ))}
              </svg>
            </>
          ) : (
            <div className="no-stream" style={{ textAlign: 'center', opacity: 0.5 }}>
              <div style={{ fontSize: '2rem', marginBottom: '0.5rem' }}>🔥</div>
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
