import React from 'react'
import { useDashboard } from '../context/DashboardContext'

const formatTime = (seconds: number): string => {
  const m = Math.floor(seconds / 60)
  const s = Math.floor(seconds % 60)
  return `${m}:${s.toString().padStart(2, '0')}`
}

const PlaybackControls: React.FC = () => {
  const {
    currentIndex, totalFrames, frameCount, isPlaying, isLive,
    serverFps, play, pause, seekTo, skipForward, skipBackward,
    displayedFrame, firstTimestampNs, lastTimestampNs,
  } = useDashboard()

  // ── Live mode: minimal controls ──────────────────────────────────────
  if (isLive) {
    return (
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: '0.75rem',
          padding: '0.625rem 1rem',
          background: 'var(--bg-card, #0a0a0a)',
          border: '1px solid var(--border, #1a1a1a)',
          marginBottom: '1.5rem',
        }}
      >
        <span
          style={{
            fontSize: '0.7rem',
            fontWeight: 700,
            letterSpacing: '0.05em',
            padding: '0.15rem 0.45rem',
            background: isPlaying ? '#e03' : '#555',
            color: '#fff',
            flexShrink: 0,
          }}
        >
          LIVE
        </span>

        <button
          onClick={isPlaying ? pause : play}
          style={btnStyle}
          title={isPlaying ? 'Pause' : 'Play'}
        >
          {isPlaying ? '⏸' : '▶'}
        </button>

        <span
          style={{
            fontSize: '0.82rem',
            opacity: 0.7,
            whiteSpace: 'nowrap',
            fontVariantNumeric: 'tabular-nums',
          }}
        >
          {isPlaying ? `${frameCount} frames` : 'PAUSED'}
        </span>
      </div>
    )
  }

  // ── File mode: full controls ─────────────────────────────────────────
  const effectiveTotal = Math.max(totalFrames, 1)

  const formatPos = () => {
    if (displayedFrame && firstTimestampNs > 0 && lastTimestampNs > firstTimestampNs) {
      const currentSec = (displayedFrame.timestamp_ns - firstTimestampNs) / 1e9
      const totalSec = (lastTimestampNs - firstTimestampNs) / 1e9
      return `${formatTime(Math.max(0, currentSec))} / ${formatTime(totalSec)}`
    }
    return `${currentIndex + 1} / ${effectiveTotal}`
  }

  return (
    <div
      style={{
        display: 'flex',
        alignItems: 'center',
        gap: '0.75rem',
        padding: '0.625rem 1rem',
        background: 'var(--bg-card, #0a0a0a)',
        border: '1px solid var(--border, #1a1a1a)',
        marginBottom: '1.5rem',
      }}
    >
      <button onClick={skipBackward} style={btnStyle} title="Skip back 10s">⏪</button>

      <button
        onClick={isPlaying ? pause : play}
        style={btnStyle}
        title={isPlaying ? 'Pause' : 'Play'}
      >
        {isPlaying ? '⏸' : '▶'}
      </button>

      <button onClick={skipForward} style={btnStyle} title="Skip forward 10s">⏩</button>

      <span
        style={{
          fontSize: '0.82rem',
          opacity: 0.7,
          whiteSpace: 'nowrap',
          minWidth: '7rem',
          textAlign: 'center',
          fontVariantNumeric: 'tabular-nums',
        }}
      >
        {formatPos()}
        {serverFps > 0 && (
          <span style={{ opacity: 0.6, marginLeft: '0.4rem' }}>@ {serverFps.toFixed(0)} fps</span>
        )}
      </span>

      <input
        type="range"
        min={0}
        max={effectiveTotal - 1}
        value={currentIndex}
        onChange={(e) => {
          if (isPlaying) pause()
          seekTo(Number(e.target.value))
        }}
        style={{ flex: 1, cursor: 'pointer', accentColor: 'var(--accent)' }}
      />
    </div>
  )
}

const btnStyle: React.CSSProperties = {
  background: 'none',
  border: 'none',
  color: 'inherit',
  fontSize: '1.2rem',
  cursor: 'pointer',
  padding: '0.25rem 0.4rem',
  borderRadius: 0,
  lineHeight: 1,
  flexShrink: 0,
}

export default PlaybackControls
