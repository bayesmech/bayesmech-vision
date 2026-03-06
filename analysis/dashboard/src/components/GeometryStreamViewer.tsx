import React, { useRef, useEffect } from 'react'
import type { CameraPose, CameraIntrinsics, InferredGeometry } from '../types'

interface Props {
  title: string
  badge: string
  placeholderIcon: string
  placeholderText: string
  mode: 'point_cloud' | 'planes'
  cameraPose?: CameraPose
  cameraIntrinsics?: CameraIntrinsics
  geometry?: InferredGeometry
}

// ---------------------------------------------------------------------------
// Math helpers
// ---------------------------------------------------------------------------

/** Rotate vector (vx, vy, vz) by unit quaternion (qx, qy, qz, qw). */
function rotateByQuat(
  vx: number, vy: number, vz: number,
  qx: number, qy: number, qz: number, qw: number,
): [number, number, number] {
  const t0 = 2 * (qy * vz - qz * vy)
  const t1 = 2 * (qz * vx - qx * vz)
  const t2 = 2 * (qx * vy - qy * vx)
  return [
    vx + qw * t0 + qy * t2 - qz * t1,
    vy + qw * t1 + qz * t0 - qx * t2,
    vz + qw * t2 + qx * t1 - qy * t0,
  ]
}

/**
 * Project a world-space point to image-frame pixel coordinates.
 *
 * ARCore convention:
 *  - Camera pose quaternion rotates FROM camera frame TO world frame.
 *  - Camera looks in -Z direction in its local frame.
 *  - Points in front of the camera have cam_z < 0.
 *
 * Pinhole projection: u = fx * (X_cam / Z_cam) + cx
 *                     v = fy * (Y_cam / Z_cam) + cy
 *   where Z_cam = -cam_z (positive depth).
 *
 * Returns null if the point is behind the camera.
 */
function worldToPixel(
  wx: number, wy: number, wz: number,
  pose: CameraPose,
  intr: CameraIntrinsics,
): [number, number] | null {
  // Vector from camera origin to world point
  const dx = wx - pose.position.x
  const dy = wy - pose.position.y
  const dz = wz - pose.position.z

  // Rotate into camera frame (inverse rotation = conjugate of unit quaternion)
  const { x: qx, y: qy, z: qz, w: qw } = pose.rotation
  const [camX, camY, camZ] = rotateByQuat(dx, dy, dz, -qx, -qy, -qz, qw)

  if (camZ >= 0) return null // behind camera

  const depth = -camZ
  const u = intr.fx * (camX / depth) + intr.cx
  const v = intr.fy * (camY / depth) + intr.cy

  return [u, v]
}

// ---------------------------------------------------------------------------
// Renderers
// ---------------------------------------------------------------------------

function drawPointCloud(
  ctx: CanvasRenderingContext2D,
  W: number, H: number,
  pose: CameraPose,
  intr: CameraIntrinsics,
  geometry: InferredGeometry,
): void {
  ctx.fillStyle = 'rgb(31, 188, 210)'

  for (const pt of geometry.point_cloud) {
    const proj = worldToPixel(pt.x, pt.y, pt.z, pose, intr)
    if (!proj) continue
    const [px, py] = proj
    if (px < 0 || px > W || py < 0 || py > H) continue

    const r = Math.max(1.5, 4 * pt.confidence)
    ctx.beginPath()
    ctx.arc(px, py, r, 0, Math.PI * 2)
    ctx.fill()
  }
}

const PLANE_COLORS: Record<number, { fill: string; stroke: string }> = {
  1: { fill: 'rgba(64, 156, 255, 0.25)',  stroke: 'rgba(64, 156, 255, 0.8)'  },
  2: { fill: 'rgba(255, 200, 64, 0.25)',  stroke: 'rgba(255, 200, 64, 0.8)'  },
  3: { fill: 'rgba(100, 220, 100, 0.25)', stroke: 'rgba(100, 220, 100, 0.8)' },
}
const PLANE_COLOR_DEFAULT = { fill: 'rgba(200, 200, 200, 0.2)', stroke: 'rgba(200, 200, 200, 0.6)' }

function drawPlanes(
  ctx: CanvasRenderingContext2D,
  _W: number, _H: number,
  cameraPose: CameraPose,
  intr: CameraIntrinsics,
  geometry: InferredGeometry,
): void {
  for (const plane of geometry.planes) {
    if (!plane.center_pose || plane.polygon.length < 3) continue

    const { x: pqx, y: pqy, z: pqz, w: pqw } = plane.center_pose.rotation
    const { x: ppx, y: ppy, z: ppz } = plane.center_pose.position

    const pixels: [number, number][] = []
    let culled = false

    for (const v of plane.polygon) {
      const [lx, ly, lz] = rotateByQuat(v.x, v.y, v.z, pqx, pqy, pqz, pqw)
      const proj = worldToPixel(lx + ppx, ly + ppy, lz + ppz, cameraPose, intr)
      if (!proj) { culled = true; break }
      pixels.push(proj)
    }

    if (culled || pixels.length < 3) continue

    const { fill, stroke } = PLANE_COLORS[plane.type] ?? PLANE_COLOR_DEFAULT

    ctx.beginPath()
    ctx.moveTo(pixels[0][0], pixels[0][1])
    for (let i = 1; i < pixels.length; i++) ctx.lineTo(pixels[i][0], pixels[i][1])
    ctx.closePath()

    ctx.fillStyle = fill
    ctx.fill()
    ctx.strokeStyle = stroke
    ctx.lineWidth = 2
    ctx.stroke()
  }
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

const GeometryStreamViewer: React.FC<Props> = ({
  title,
  badge,
  placeholderIcon,
  placeholderText,
  mode,
  cameraPose,
  cameraIntrinsics,
  geometry,
}) => {
  const canvasRef = useRef<HTMLCanvasElement>(null)

  const hasData = !!(cameraPose && cameraIntrinsics && geometry &&
    (geometry.point_cloud.length > 0 || geometry.planes.length > 0))

  // Canvas dimensions match the camera's native image resolution
  const canvasW = Math.round(cameraIntrinsics?.image_width ?? 1280)
  const canvasH = Math.round(cameraIntrinsics?.image_height ?? 720)

  useEffect(() => {
    const canvas = canvasRef.current
    if (!canvas) return
    const ctx = canvas.getContext('2d')
    if (!ctx) return

    const W = canvas.width
    const H = canvas.height
    ctx.clearRect(0, 0, W, H)

    if (!cameraPose || !cameraIntrinsics || !geometry) return

    if (mode === 'point_cloud') {
      drawPointCloud(ctx, W, H, cameraPose, cameraIntrinsics, geometry)
    } else {
      drawPlanes(ctx, W, H, cameraPose, cameraIntrinsics, geometry)
    }
  }, [cameraPose, cameraIntrinsics, geometry, mode])

  return (
    <div className="stream-card">
      <div className="stream-header">
        <span className="stream-title">{title}</span>
        <span className="stream-badge">{badge}</span>
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
        }}
      >
        {hasData ? (
          <canvas
            ref={canvasRef}
            width={canvasW}
            height={canvasH}
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

export default GeometryStreamViewer
