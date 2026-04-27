import React, { useState, useEffect, useCallback } from 'react'
import { fetchRecordings } from '../services/api'
import { useDashboard } from '../context/DashboardContext'
import type { RecordingInfo } from '../types'

const LoadButton: React.FC = () => {
  const { loadRecording, isLive, switchToLive } = useDashboard()
  const [open, setOpen] = useState(false)
  const [recordings, setRecordings] = useState<RecordingInfo[]>([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const load = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const data = await fetchRecordings()
      setRecordings(data.recordings)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to load recordings')
    } finally {
      setLoading(false)
    }
  }, [])

  const handleOpen = () => {
    setOpen(true)
    load()
  }

  const handleSelect = async (name: string) => {
    setOpen(false)
    try {
      await loadRecording(name)
    } catch (e) {
      const msg = e instanceof Error ? e.message : 'Unknown error'
      alert(`Playback failed: ${msg}`)
    }
  }

  const handleSwitchToLive = async () => {
    setOpen(false)
    try {
      await switchToLive()
    } catch (e) {
      const msg = e instanceof Error ? e.message : 'Unknown error'
      alert(`Switch to live failed: ${msg}`)
    }
  }

  useEffect(() => {
    if (!open) return
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setOpen(false)
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [open])

  const formatDate = (ts: number) => {
    const d = new Date(ts * 1000)
    return d.toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric' })
      + ' ' + d.toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' })
  }

  return (
    <>
      <button onClick={handleOpen} style={btnStyle}>LOAD</button>

      {open && (
        <div
          onClick={() => setOpen(false)}
          style={{
            position: 'fixed',
            inset: 0,
            background: 'rgba(0, 0, 0, 0.85)',
            zIndex: 9999,
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
          }}
        >
          <div
            onClick={(e) => e.stopPropagation()}
            style={{
              background: '#0a0a0a',
              border: '1px solid #1a1a1a',
              width: '100%',
              maxWidth: 560,
              maxHeight: '70vh',
              display: 'flex',
              flexDirection: 'column',
              overflow: 'hidden',
            }}
          >
            {/* Header */}
            <div style={{
              padding: '1rem 1.25rem',
              borderBottom: '1px solid #1a1a1a',
              display: 'flex',
              justifyContent: 'space-between',
              alignItems: 'center',
            }}>
              <span style={{
                fontSize: '0.8rem',
                fontWeight: 600,
                color: '#00ff88',
                textTransform: 'uppercase',
                letterSpacing: '0.1em',
              }}>
                Recordings
              </span>
              <button
                onClick={() => setOpen(false)}
                style={{
                  background: 'none',
                  border: 'none',
                  color: '#707070',
                  fontSize: '1rem',
                  cursor: 'pointer',
                  padding: '0.25rem',
                  lineHeight: 1,
                }}
              >
                ✕
              </button>
            </div>

            {/* Content */}
            <div style={{ overflowY: 'auto', flex: 1 }}>
              {/* Live stream option */}
              <div
                onClick={isLive ? () => setOpen(false) : handleSwitchToLive}
                style={{
                  padding: '0.75rem 1.25rem',
                  borderBottom: '1px solid #222',
                  cursor: 'pointer',
                  display: 'flex',
                  alignItems: 'center',
                  gap: '0.75rem',
                  transition: 'background 0.1s',
                  background: isLive ? 'rgba(238, 0, 51, 0.08)' : 'transparent',
                }}
                onMouseEnter={(e) => e.currentTarget.style.background = isLive ? 'rgba(238, 0, 51, 0.12)' : '#111'}
                onMouseLeave={(e) => e.currentTarget.style.background = isLive ? 'rgba(238, 0, 51, 0.08)' : 'transparent'}
              >
                <span style={{
                  width: 8,
                  height: 8,
                  borderRadius: '50%',
                  background: isLive ? '#e03' : '#555',
                  flexShrink: 0,
                  boxShadow: isLive ? '0 0 6px rgba(238, 0, 51, 0.6)' : 'none',
                }} />
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div style={{
                    fontSize: '0.82rem',
                    color: isLive ? '#ff6680' : '#e0e0e0',
                    fontWeight: 600,
                  }}>
                    Live Stream
                  </div>
                  <div style={{
                    fontSize: '0.68rem',
                    color: '#505050',
                    marginTop: '0.2rem',
                  }}>
                    {isLive ? 'Currently active' : 'Switch to live camera feed'}
                  </div>
                </div>
                {isLive && (
                  <span style={{
                    fontSize: '0.6rem',
                    fontWeight: 600,
                    padding: '0.15rem 0.4rem',
                    border: '1px solid #e03',
                    color: '#e03',
                    letterSpacing: '0.08em',
                    flexShrink: 0,
                  }}>
                    ACTIVE
                  </span>
                )}
              </div>

              {loading && (
                <div style={msgStyle}>Loading...</div>
              )}
              {error && (
                <div style={{ ...msgStyle, color: '#ff3344' }}>{error}</div>
              )}
              {!loading && !error && recordings.length === 0 && (
                <div style={msgStyle}>No recordings found</div>
              )}
              {!loading && !error && recordings.map((rec) => (
                <div
                  key={rec.name}
                  onClick={() => handleSelect(rec.name)}
                  style={{
                    padding: '0.75rem 1.25rem',
                    borderBottom: '1px solid #111',
                    cursor: 'pointer',
                    display: 'flex',
                    alignItems: 'center',
                    gap: '0.75rem',
                    transition: 'background 0.1s',
                  }}
                  onMouseEnter={(e) => e.currentTarget.style.background = '#111'}
                  onMouseLeave={(e) => e.currentTarget.style.background = 'transparent'}
                >
                  <div style={{ flex: 1, minWidth: 0 }}>
                    <div style={{
                      fontSize: '0.82rem',
                      color: '#e0e0e0',
                      whiteSpace: 'nowrap',
                      overflow: 'hidden',
                      textOverflow: 'ellipsis',
                    }}>
                      {rec.title}
                    </div>
                    <div style={{
                      fontSize: '0.68rem',
                      color: '#505050',
                      marginTop: '0.2rem',
                    }}>
                      {rec.size_mb} MB &middot; {formatDate(rec.recorded_at)}
                    </div>
                  </div>
                  {rec.has_segmentation && (
                    <span style={{
                      fontSize: '0.6rem',
                      fontWeight: 600,
                      padding: '0.15rem 0.4rem',
                      border: '1px solid #00ff88',
                      color: '#00ff88',
                      letterSpacing: '0.08em',
                      flexShrink: 0,
                    }}>
                      SEG
                    </span>
                  )}
                  {rec.has_idoslam && (
                    <span style={{
                      fontSize: '0.6rem',
                      fontWeight: 600,
                      padding: '0.15rem 0.4rem',
                      border: '1px solid #2f88ff',
                      color: '#69a8ff',
                      letterSpacing: '0.08em',
                      flexShrink: 0,
                    }}>
                      SLAM
                    </span>
                  )}
                </div>
              ))}
            </div>
          </div>
        </div>
      )}
    </>
  )
}

const btnStyle: React.CSSProperties = {
  background: 'transparent',
  border: '1px solid #00ff88',
  color: '#00ff88',
  fontSize: '0.75rem',
  fontWeight: 600,
  cursor: 'pointer',
  padding: '0.4rem 0.8rem',
  letterSpacing: '0.08em',
  fontFamily: 'inherit',
  marginRight: '0.5rem',
}

const msgStyle: React.CSSProperties = {
  padding: '2rem',
  textAlign: 'center',
  fontSize: '0.8rem',
  color: '#707070',
}

export default LoadButton
