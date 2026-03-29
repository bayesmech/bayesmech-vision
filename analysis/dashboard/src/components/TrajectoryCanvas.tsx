import React, { useRef, useEffect, useMemo } from 'react'
import { useDashboard } from '../context/DashboardContext'
import { useTrajectory } from '../hooks/useTrajectory'
import { drawTrajectory, rescaleIfNeeded } from '../utils/trajectory'

const CANVAS_WIDTH = 800
const CANVAS_HEIGHT = 600

const TrajectoryCanvas: React.FC = () => {
  const { displayedFrame, isLive, trajectoryPositions, currentIndex } = useDashboard()
  const canvasRef = useRef<HTMLCanvasElement>(null)
  const liveTrajectory = useTrajectory(CANVAS_WIDTH, CANVAS_HEIGHT)

  // Live mode: accumulate positions from displayedFrame
  useEffect(() => {
    if (!isLive) return
    if (!displayedFrame?.camera_pose) return
    liveTrajectory.addPosition(displayedFrame.camera_pose, displayedFrame.imu)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [displayedFrame, isLive])

  // File mode: compute visible points and stable scaling from all trajectory positions
  const fileView = useMemo(() => {
    if (isLive || trajectoryPositions.length === 0) return null
    const visible = trajectoryPositions.slice(0, currentIndex + 1)
    // Scale computed from ALL positions for a stable viewport
    const { scale, offsetX, offsetY } = rescaleIfNeeded(
      trajectoryPositions, CANVAS_WIDTH, CANVAS_HEIGHT, 1
    )
    return { visible, scale, offset: { x: offsetX, y: offsetY } }
  }, [isLive, trajectoryPositions, currentIndex])

  // Draw
  useEffect(() => {
    const canvas = canvasRef.current
    if (!canvas) return
    const ctx = canvas.getContext('2d')
    if (!ctx) return

    if (isLive) {
      drawTrajectory(ctx, canvas, liveTrajectory.points, liveTrajectory.scale, liveTrajectory.offset)
    } else if (fileView) {
      drawTrajectory(ctx, canvas, fileView.visible, fileView.scale, fileView.offset)
    }
  }, [isLive, liveTrajectory.points, liveTrajectory.scale, liveTrajectory.offset, fileView])

  const currentPos = isLive
    ? liveTrajectory.currentPosition
    : (fileView && fileView.visible.length > 0
        ? fileView.visible[fileView.visible.length - 1]
        : null)

  const pointCount = isLive ? liveTrajectory.points.length : (fileView?.visible.length ?? 0)
  const scaleVal = isLive ? liveTrajectory.scale : (fileView?.scale ?? 1)

  return (
    <div className="stream-card">
      <div className="stream-header">
        <span className="stream-title">Camera Trajectory</span>
        {isLive && (
          <button
            className="theme-toggle"
            onClick={liveTrajectory.clear}
            style={{ marginLeft: 'auto', fontSize: '0.8rem', padding: '0.25rem 0.5rem' }}
          >
            Clear
          </button>
        )}
      </div>
      <div style={{ padding: '0.5rem' }}>
        <canvas
          ref={canvasRef}
          width={CANVAS_WIDTH}
          height={CANVAS_HEIGHT}
          style={{ width: '100%', height: 'auto' }}
        />
        <div
          className="trajectory-info"
          style={{
            display: 'flex',
            gap: '1.5rem',
            fontSize: '0.8rem',
            opacity: 0.7,
            marginTop: '0.5rem',
            fontFamily: 'monospace',
          }}
        >
          <span>
            Position: {currentPos ? `(${currentPos.x.toFixed(3)}, ${currentPos.y.toFixed(3)})` : 'N/A'}
          </span>
          <span>Points: {pointCount}</span>
          <span>Scale: {scaleVal.toFixed(2)}</span>
        </div>
      </div>
    </div>
  )
}

export default TrajectoryCanvas
