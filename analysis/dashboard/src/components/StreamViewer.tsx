import React, { useRef, useEffect, useState } from 'react'

interface StreamViewerProps {
  title: string
  blobUrl?: string
  placeholderIcon: string
  placeholderText: string
  headerExtra?: React.ReactNode
  /** Keep showing the last valid image for this many ms before falling back to placeholder. */
  holdLastMs?: number
}

const StreamViewer: React.FC<StreamViewerProps> = ({
  title,
  blobUrl,
  placeholderIcon,
  placeholderText,
  headerExtra,
  holdLastMs = 250,
}) => {
  const canvasRef = useRef<HTMLCanvasElement>(null)
  const lastValidTime = useRef(0)
  const hasFrameRef = useRef(false)
  const [hasFrame, setHasFrame] = useState(false)
  const [showPlaceholder, setShowPlaceholder] = useState(true)

  useEffect(() => {
    if (blobUrl) {
      let cancelled = false
      const image = new Image()
      image.onload = () => {
        if (cancelled) return
        const canvas = canvasRef.current
        const ctx = canvas?.getContext('2d')
        if (!canvas || !ctx) return
        canvas.width = image.naturalWidth || 1
        canvas.height = image.naturalHeight || 1
        ctx.clearRect(0, 0, canvas.width, canvas.height)
        ctx.drawImage(image, 0, 0, canvas.width, canvas.height)
        lastValidTime.current = Date.now()
        hasFrameRef.current = true
        setHasFrame(true)
        setShowPlaceholder(false)
      }
      image.onerror = () => {
        if (cancelled) return
        if (!hasFrameRef.current) {
          setShowPlaceholder(true)
        }
      }
      image.src = blobUrl
      return () => {
        cancelled = true
      }
    }
    if (holdLastMs <= 0 || !hasFrameRef.current) {
      const canvas = canvasRef.current
      const ctx = canvas?.getContext('2d')
      if (canvas && ctx) ctx.clearRect(0, 0, canvas.width, canvas.height)
      hasFrameRef.current = false
      setHasFrame(false)
      setShowPlaceholder(true)
      return
    }
    setShowPlaceholder(false)
    const elapsed = Date.now() - lastValidTime.current
    const remaining = Math.max(0, holdLastMs - elapsed)
    const timer = setTimeout(() => {
      const canvas = canvasRef.current
      const ctx = canvas?.getContext('2d')
      if (canvas && ctx) ctx.clearRect(0, 0, canvas.width, canvas.height)
      hasFrameRef.current = false
      setHasFrame(false)
      setShowPlaceholder(true)
    }, remaining)
    return () => clearTimeout(timer)
  }, [blobUrl, holdLastMs])

  return (
    <div className="stream-card">
      <div className="stream-header">
        <span className="stream-title">{title}</span>
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
        <canvas
          ref={canvasRef}
          aria-label={title}
          role="img"
          style={{
            width: '100%',
            height: '100%',
            objectFit: 'contain',
            display: hasFrame ? 'block' : 'none',
          }}
        />
        {showPlaceholder && (
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
