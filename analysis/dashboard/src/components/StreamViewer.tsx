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
  const [displayUrl, setDisplayUrl] = useState<string | undefined>(undefined)
  const [showPlaceholder, setShowPlaceholder] = useState(true)

  useEffect(() => {
    if (blobUrl) {
      let cancelled = false
      const image = new Image()
      image.onload = () => {
        if (cancelled) return
        lastValidUrl.current = blobUrl
        lastValidTime.current = Date.now()
        setDisplayUrl(blobUrl)
        setShowPlaceholder(false)
      }
      image.onerror = () => {
        if (cancelled) return
        if (!lastValidUrl.current) {
          setDisplayUrl(undefined)
          setShowPlaceholder(true)
        }
      }
      image.src = blobUrl
      return () => {
        cancelled = true
      }
    }
    if (holdLastMs <= 0 || !lastValidUrl.current) {
      setDisplayUrl(undefined)
      setShowPlaceholder(true)
      return
    }
    setDisplayUrl(lastValidUrl.current)
    setShowPlaceholder(false)
    const elapsed = Date.now() - lastValidTime.current
    const remaining = Math.max(0, holdLastMs - elapsed)
    const timer = setTimeout(() => {
      setDisplayUrl(undefined)
      setShowPlaceholder(true)
    }, remaining)
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
