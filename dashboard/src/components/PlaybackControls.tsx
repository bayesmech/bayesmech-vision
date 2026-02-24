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

  // Effective total: for live use frameCount, for file use totalFrames
  const effectiveTotal = isLive ? Math.max(frameCount, 1) : Math.max(totalFrames, 1)

  // Slider value: when live and playing, pin to the right end
  const sliderValue = isLive && isPlaying ? effectiveTotal - 1 : currentIndex

  const atLiveEdge = isLive && currentIndex >= frameCount - 2

  const formatPos = () => {
    if (isLive) {
      const behind = frameCount - 1 - currentIndex
      if (behind <= 1 || isPlaying) return 'LIVE'
      return `-${behind} frames`
    }
    // File mode: show time
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
      {/* Live badge */}
      {isLive && (
        <span
          style={{
            fontSize: '0.7rem',
            fontWeight: 700,
            letterSpacing: '0.05em',
            padding: '0.15rem 0.45rem',
            background: isPlaying && atLiveEdge ? '#e03' : '#555',
            color: '#fff',
            flexShrink: 0,
          }}
        >
          LIVE
        </span>
      )}

      <button onClick={skipBackward} style={btnStyle} title="Skip back 10s">⏪</button>

      <button
        onClick={isPlaying ? pause : play}
        style={btnStyle}
        title={isPlaying ? 'Pause' : 'Play'}
      >
        {isPlaying ? '⏸' : '▶'}
      </button>

      <button onClick={skipForward} style={btnStyle} title="Skip forward 10s">⏩</button>

      {/* Jump-to-live button when paused behind live edge */}
      {isLive && !isPlaying && !atLiveEdge && (
        <button
          onClick={play}
          style={{ ...btnStyle, fontSize: '0.75rem', opacity: 0.8 }}
          title="Jump to live"
        >
          ⏭ Live
        </button>
      )}

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
        {!isLive && serverFps > 0 && (
          <span style={{ opacity: 0.6, marginLeft: '0.4rem' }}>@ {serverFps.toFixed(0)} fps</span>
        )}
      </span>

      <input
        type="range"
        min={0}
        max={effectiveTotal - 1}
        value={sliderValue}
        onChange={(e) => {
          if (isPlaying) pause()
          seekTo(Number(e.target.value))
        }}
        style={{ flex: 1, cursor: 'pointer', accentColor: isLive ? '#e03' : 'var(--accent)' }}
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
