import React, { useRef, useEffect, useState } from 'react'

interface StreamViewerProps {
  title: string
  badge: string
  blobUrl?: string
  placeholderIcon: string
  placeholderText: string
  headerExtra?: React.ReactNode
  /** Keep showing the last valid image for this many ms before falling back to placeholder. */
  holdLastMs?: number
}

const StreamViewer: React.FC<StreamViewerProps> = ({
  title,
  badge,
  blobUrl,
  placeholderIcon,
  placeholderText,
  headerExtra,
  holdLastMs = 0,
}) => {
  const lastValidUrl = useRef<string | undefined>(undefined)
  const lastValidTime = useRef(0)
  const [showPlaceholder, setShowPlaceholder] = useState(!blobUrl)

  // Track the last valid URL
  if (blobUrl) {
    lastValidUrl.current = blobUrl
    lastValidTime.current = Date.now()
  }

  const displayUrl = blobUrl ?? (holdLastMs > 0 ? lastValidUrl.current : undefined)

  // When blobUrl goes falsy and we have a holdLastMs, start a timer
  useEffect(() => {
    if (blobUrl) {
      setShowPlaceholder(false)
      return
    }
    if (holdLastMs <= 0 || !lastValidUrl.current) {
      setShowPlaceholder(true)
      return
    }
    // Hold the last image; only show placeholder after the hold period
    setShowPlaceholder(false)
    const elapsed = Date.now() - lastValidTime.current
    const remaining = Math.max(0, holdLastMs - elapsed)
    const timer = setTimeout(() => setShowPlaceholder(true), remaining)
    return () => clearTimeout(timer)
  }, [blobUrl, holdLastMs])

  const showImage = displayUrl && !showPlaceholder

  return (
    <div className="stream-card">
      <div className="stream-header">
        <span className="stream-title">{title}</span>
        <span className="stream-badge">{badge}</span>
        {headerExtra && <div className="stream-header-extra">{headerExtra}</div>}
      </div>
      <div
        className="stream-viewer"
        style={{
          aspectRatio: '16 / 9',
          backgroundColor: '#000',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          borderRadius: 0,
          overflow: 'hidden',
          position: 'relative',
        }}
      >
        {showImage ? (
          <img
            src={displayUrl}
            alt={title}
            style={{ width: '100%', height: '100%', objectFit: 'contain' }}
          />
        ) : (
          <div className="no-stream" style={{ textAlign: 'center', opacity: 0.5 }}>
            <div style={{ fontSize: '2rem', marginBottom: '0.5rem' }}>{placeholderIcon}</div>
            <div>{placeholderText}</div>
          </div>
        )}
      </div>
    </div>
  )
}

export default StreamViewer
