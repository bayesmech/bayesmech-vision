import React, { useEffect, useMemo, useState } from 'react'
import { useDashboard } from '../context/DashboardContext'
import { fetchRecordingPongtownData } from '../services/api'
import type { PongtownData, PongtownFrameRecord } from '../types'

type PoseOverlayMode = 'hull' | 'pnp' | 'global'

const POSE_OVERLAY_MODES: { id: PoseOverlayMode; label: string }[] = [
  { id: 'hull', label: 'Hull Generation' },
  { id: 'pnp', label: 'PnP Estimates' },
  { id: 'global', label: 'Global Pose' },
]

const TABLE_HEIGHT_MM = 1525

interface Point2 {
  x: number
  y: number
}

interface OverlayGeometry {
  tableQuad: Point2[]
  netQuad: Point2[]
  midline: Point2[]
  iou?: number
  offScreen?: boolean
}

const isFinitePoint = (point: Point2): boolean => Number.isFinite(point.x) && Number.isFinite(point.y)

const asNumbers = (value: unknown): number[] => {
  if (!value) return []
  if (Array.isArray(value)) return value.map(Number).filter(Number.isFinite)
  if (ArrayBuffer.isView(value)) {
    return Array.from(value as unknown as ArrayLike<number>, Number).filter(Number.isFinite)
  }
  return []
}

const pointsFromFlat = (value: unknown): Point2[] => {
  const nums = asNumbers(value)
  const points: Point2[] = []
  for (let i = 0; i + 1 < nums.length; i += 2) {
    const point = { x: nums[i], y: nums[i + 1] }
    if (isFinitePoint(point)) points.push(point)
  }
  return points
}

const firstNumberList = (source: unknown, names: string[]): number[] => {
  const obj = source as Record<string, unknown> | null | undefined
  if (!obj) return []
  for (const name of names) {
    const nums = asNumbers(obj[name])
    if (nums.length > 0) return nums
  }
  return []
}

const projectTableMidline = (transform: number[], cameraMatrix: number[]): Point2[] => {
  if (transform.length < 16 || cameraMatrix.length < 9) return []

  const fx = cameraMatrix[0]
  const fy = cameraMatrix[4]
  const cx = cameraMatrix[2]
  const cy = cameraMatrix[5]
  const points3d = [
    [0, -TABLE_HEIGHT_MM / 2, 0],
    [0, TABLE_HEIGHT_MM / 2, 0],
  ]

  const projected: Point2[] = []
  for (const [x, y, z] of points3d) {
    const camX = transform[0] * x + transform[1] * y + transform[2] * z + transform[3]
    const camY = transform[4] * x + transform[5] * y + transform[6] * z + transform[7]
    const camZ = transform[8] * x + transform[9] * y + transform[10] * z + transform[11]
    if (Math.abs(camZ) < 1e-6) return []
    const point = {
      x: fx * (camX / camZ) + cx,
      y: fy * (camY / camZ) + cy,
    }
    if (!isFinitePoint(point)) return []
    projected.push(point)
  }
  return projected
}

const pathFor = (points: Point2[], closed = true): string => {
  if (points.length === 0) return ''
  const head = `M ${points[0].x} ${points[0].y}`
  const rest = points.slice(1).map((point) => `L ${point.x} ${point.y}`).join(' ')
  return `${head}${rest ? ` ${rest}` : ''}${closed ? ' Z' : ''}`
}

const getOverlayGeometry = (
  frame: PongtownFrameRecord | undefined,
  mode: PoseOverlayMode,
): OverlayGeometry => {
  const record = frame?.record
  const debug = record?.pnpFrameDebug?.[0]
  const output = record?.frameOutput
  const cameraMatrix = firstNumberList(debug, ['cameraMatrix'])

  if (mode === 'hull') {
    return {
      tableQuad: pointsFromFlat(debug?.imagePlaneTableQuadImg),
      netQuad: pointsFromFlat(debug?.imagePlaneNetQuadImg),
      midline: pointsFromFlat(debug?.imagePlaneMidlineImg),
      iou: debug?.imagePlaneQuadQuality ?? undefined,
    }
  }

  if (mode === 'pnp') {
    const pnpTableTransform = firstNumberList(debug, [
      'pnp_TTableToCamera',
      'pnpTTableToCamera',
      'pnpTableToCamera',
    ])
    return {
      tableQuad: pointsFromFlat(debug?.pnpTableQuadImg),
      netQuad: pointsFromFlat(debug?.pnpOverlayNetQuadImg).length > 0
        ? pointsFromFlat(debug?.pnpOverlayNetQuadImg)
        : pointsFromFlat(debug?.pnpNetQuadImg),
      midline: projectTableMidline(pnpTableTransform, cameraMatrix),
      iou: debug?.pnpTableIou ?? undefined,
    }
  }

  const outputTransform = firstNumberList(output, [
    'TTableToCamera',
    'tTableToCamera',
    'tableToCamera',
  ])
  return {
    tableQuad: pointsFromFlat(output?.tableQuadImg),
    netQuad: pointsFromFlat(output?.netQuadImg),
    midline: projectTableMidline(outputTransform, cameraMatrix),
    iou: output?.globalIou ?? undefined,
    offScreen: output?.offScreen ?? undefined,
  }
}

const SportUnderstandingPanel: React.FC = () => {
  const {
    displayedFrame,
    displayedPongtownFrame,
    currentIndex,
    isLive,
    currentRecordingName,
    requestPongtownFrame,
  } = useDashboard()
  const [mode, setMode] = useState<PoseOverlayMode>('global')
  const [pongtownData, setPongtownData] = useState<PongtownData | null>(null)
  const [pongtownAvailable, setPongtownAvailable] = useState<boolean | null>(
    currentRecordingName ? null : false,
  )
  const displayedFrameNumber = displayedFrame?.frame_number

  useEffect(() => {
    setPongtownData(null)
    setPongtownAvailable(currentRecordingName && !isLive ? null : false)
  }, [currentRecordingName, isLive])

  useEffect(() => {
    if (displayedPongtownFrame) setPongtownAvailable(true)
  }, [displayedPongtownFrame])

  useEffect(() => {
    if (isLive || !currentRecordingName) return

    let cancelled = false
    fetchRecordingPongtownData(currentRecordingName)
      .then((data) => {
        if (cancelled) return
        setPongtownData(data)
        setPongtownAvailable(data !== null && data.frames.length > 0)
      })
      .catch(() => {
        if (cancelled) return
        setPongtownData(null)
        setPongtownAvailable(false)
      })

    return () => {
      cancelled = true
    }
  }, [currentRecordingName, isLive])

  useEffect(() => {
    if (isLive || displayedFrameNumber === undefined) return
    requestPongtownFrame(displayedFrameNumber)
  }, [displayedFrameNumber, isLive, requestPongtownFrame])

  const currentPongtownFrame = useMemo(() => {
    if (
      displayedPongtownFrame
      && (displayedFrameNumber === undefined || displayedPongtownFrame.frameNumber === displayedFrameNumber)
    ) {
      return displayedPongtownFrame
    }
    if (!pongtownData) return undefined
    if (displayedFrameNumber !== undefined) {
      const record = pongtownData.byFrameNumber.get(displayedFrameNumber)
      if (record) return record
    }
    return pongtownData.byFrameIndex.get(currentIndex)
  }, [currentIndex, displayedFrameNumber, displayedPongtownFrame, pongtownData])

  const geometry = useMemo(
    () => getOverlayGeometry(currentPongtownFrame, mode),
    [currentPongtownFrame, mode],
  )

  const frameWidth = displayedFrame?.rgb_width
    ?? currentPongtownFrame?.record.pnpFrameDebug?.[0]?.cameraIntrinsics?.imageWidth
    ?? 1920
  const frameHeight = displayedFrame?.rgb_height
    ?? currentPongtownFrame?.record.pnpFrameDebug?.[0]?.cameraIntrinsics?.imageHeight
    ?? 1080

  const placeholderText = isLive
    ? 'Sport understanding overlays are available for recordings only.'
    : !currentRecordingName
      ? 'Load a recording to inspect surface pose estimation.'
      : pongtownAvailable === null && !currentPongtownFrame
        ? 'Loading surface pose estimation...'
        : !pongtownAvailable && !currentPongtownFrame
          ? 'No Pongtown proto found for this recording.'
          : !currentPongtownFrame
            ? 'No Pongtown record for this frame.'
            : 'Waiting for RGB frame...'

  const showOverlay = !!currentPongtownFrame && !!displayedFrame?.rgbBlobUrl
  const hasGeometry =
    geometry.tableQuad.length >= 2 ||
    geometry.netQuad.length >= 2 ||
    geometry.midline.length >= 2

  return (
    <div className="sport-understanding-layout">
      <div className="stream-card surface-pose-card">
        <div className="stream-header surface-pose-header">
          <span className="stream-title">Surface Pose Estimation</span>
          <div
            className="surface-pose-mode-control"
            role="tablist"
            aria-label="Surface pose overlay mode"
          >
            {POSE_OVERLAY_MODES.map((option) => (
              <button
                key={option.id}
                type="button"
                role="tab"
                aria-selected={mode === option.id}
                className={`surface-pose-mode${mode === option.id ? ' is-active' : ''}`}
                onClick={() => setMode(option.id)}
              >
                {option.label}
              </button>
            ))}
          </div>
        </div>

        <div className="surface-pose-viewer">
          {showOverlay ? (
            <>
              <img
                src={displayedFrame.rgbBlobUrl}
                alt="Surface pose estimation frame"
                className="surface-pose-layer"
              />
              <svg
                className="surface-pose-svg"
                viewBox={`0 0 ${frameWidth} ${frameHeight}`}
                preserveAspectRatio="xMidYMid meet"
              >
                {geometry.tableQuad.length >= 3 && (
                  <path
                    d={pathFor(geometry.tableQuad)}
                    className="surface-pose-table"
                  />
                )}
                {geometry.netQuad.length >= 3 && (
                  <path
                    d={pathFor(geometry.netQuad)}
                    className="surface-pose-net"
                  />
                )}
                {geometry.midline.length >= 2 && (
                  <path
                    d={pathFor(geometry.midline.slice(0, 2), false)}
                    className="surface-pose-midline"
                  />
                )}
                {geometry.offScreen && (
                  <rect
                    x={4}
                    y={4}
                    width={Math.max(0, frameWidth - 8)}
                    height={Math.max(0, frameHeight - 8)}
                    className="surface-pose-offscreen"
                  />
                )}
              </svg>
              {!hasGeometry && (
                <div className="surface-pose-overlay-message">
                  No geometry for this view.
                </div>
              )}
            </>
          ) : (
            <div className="no-stream" style={{ textAlign: 'center', opacity: 0.5 }}>
              <div>{placeholderText}</div>
            </div>
          )}
        </div>

        <div className="surface-pose-footer">
          <span>
            {currentPongtownFrame
              ? `Frame ${currentPongtownFrame.frameNumber}`
              : 'Frame N/A'}
          </span>
          <span>
            {geometry.iou !== undefined
              ? `Score ${geometry.iou.toFixed(3)}`
              : 'Score N/A'}
          </span>
        </div>
      </div>
    </div>
  )
}

export default SportUnderstandingPanel
