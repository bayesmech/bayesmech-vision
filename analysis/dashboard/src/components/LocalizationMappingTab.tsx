import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import type { bayesmech } from '../proto/bundle'
import { useDashboard } from '../context/DashboardContext'
import type { DecodedAnnotation, DecodedFrame } from '../types'

const MAP_WIDTH = 900
const MAP_HEIGHT = 560
const VIDEO_WIDTH = 900
const VIDEO_HEIGHT = 506
const ROAD_LABELS = new Set(['road', 'pavement'])

type Point2 = { x: number; y: number }
type Point3 = { x: number; y: number; z: number }
type ImageRect = { x: number; y: number; width: number; height: number }
type SlamPose = bayesmech.vision.IIdoSlamFramePose
type SlamPairDebug = bayesmech.vision.IIdoSlamPairDebug
type SlamResponse = bayesmech.vision.IdoSlamResponse

interface MaskRaster {
  width: number
  height: number
  mask: Uint8Array
}

interface RoadProjectionData {
  road: MaskRaster
  bike: MaskRaster | null
  projector: GroundProjector | null
  leftImage: Point2[]
  rightImage: Point2[]
  midImage: Point2[]
  roadGround: Point2[]
  leftGround: Point2[]
  rightGround: Point2[]
  pitchDeg: number
  cameraHeightM: number
}

class GroundProjector {
  private readonly fx: number
  private readonly fy: number
  private readonly cx: number
  private readonly cy: number
  private readonly cameraHeightM: number
  private readonly camToGround: number[][]

  constructor(frame: DecodedFrame, imageWidth: number, imageHeight: number, pitchDeg: number, cameraHeightM: number) {
    const intr = frame.camera_intrinsics
    if (!intr) throw new Error('Missing camera intrinsics')
    const srcWidth = intr.image_width || frame.rgb_width || imageWidth
    const srcHeight = intr.image_height || frame.rgb_height || imageHeight
    const scaleX = imageWidth / Math.max(srcWidth, 1)
    const scaleY = imageHeight / Math.max(srcHeight, 1)
    this.fx = intr.fx * scaleX
    this.fy = intr.fy * scaleY
    this.cx = intr.cx * scaleX
    this.cy = intr.cy * scaleY
    this.cameraHeightM = cameraHeightM

    const pitch = pitchDeg * Math.PI / 180
    const c = Math.cos(pitch)
    const s = Math.sin(pitch)
    const r0 = [
      [1, 0, 0],
      [0, 0, 1],
      [0, -1, 0],
    ]
    const rx = [
      [1, 0, 0],
      [0, c, s],
      [0, -s, c],
    ]
    this.camToGround = multiply3(rx, r0)
  }

  imageToGround(u: number, v: number): Point2 | null {
    const ray = [(u - this.cx) / this.fx, (v - this.cy) / this.fy, 1]
    const groundRay = multiplyVec3(this.camToGround, ray)
    if (groundRay[2] >= -1e-6) return null
    const scale = -this.cameraHeightM / groundRay[2]
    return { x: scale * groundRay[0], y: scale * groundRay[1] }
  }
}

function multiply3(a: number[][], b: number[][]): number[][] {
  return a.map(row => b[0].map((_, col) => row[0] * b[0][col] + row[1] * b[1][col] + row[2] * b[2][col]))
}

function multiplyVec3(m: number[][], v: number[]): number[] {
  return [
    m[0][0] * v[0] + m[0][1] * v[1] + m[0][2] * v[2],
    m[1][0] * v[0] + m[1][1] * v[1] + m[1][2] * v[2],
    m[2][0] * v[0] + m[2][1] * v[1] + m[2][2] * v[2],
  ]
}

function poseToVec3(pose: SlamPose): Point3 | null {
  const pos = pose.worldPose?.position
  if (!pos) return null
  return { x: pos.x ?? 0, y: pos.y ?? 0, z: pos.z ?? 0 }
}

function groundPointToVec3(point: bayesmech.vision.IVector3 | null | undefined): Point3 | null {
  if (!point) return null
  return { x: point.x ?? 0, y: point.y ?? 0, z: point.z ?? 0 }
}

function dot3(a: Point3, b: Point3): number {
  return a.x * b.x + a.y * b.y + a.z * b.z
}

function normalize3(v: Point3): Point3 {
  const len = Math.hypot(v.x, v.y, v.z)
  if (len < 1e-9) return { x: 1, y: 0, z: 0 }
  return { x: v.x / len, y: v.y / len, z: v.z / len }
}

function covarianceMultiply(cov: number[][], v: Point3): Point3 {
  return {
    x: cov[0][0] * v.x + cov[0][1] * v.y + cov[0][2] * v.z,
    y: cov[1][0] * v.x + cov[1][1] * v.y + cov[1][2] * v.z,
    z: cov[2][0] * v.x + cov[2][1] * v.y + cov[2][2] * v.z,
  }
}

function dominantEigenVector(cov: number[][], seed: Point3): Point3 {
  let v = normalize3(seed)
  for (let i = 0; i < 24; i += 1) {
    v = normalize3(covarianceMultiply(cov, v))
  }
  return v
}

function createPcaProjector(points: Point3[]): (p: Point3) => Point2 {
  if (points.length < 2) return p => ({ x: p.x, y: p.z })
  const mean = points.reduce(
    (acc, p) => ({ x: acc.x + p.x, y: acc.y + p.y, z: acc.z + p.z }),
    { x: 0, y: 0, z: 0 },
  )
  mean.x /= points.length
  mean.y /= points.length
  mean.z /= points.length

  const cov = [
    [0, 0, 0],
    [0, 0, 0],
    [0, 0, 0],
  ]
  for (const p of points) {
    const x = p.x - mean.x
    const y = p.y - mean.y
    const z = p.z - mean.z
    cov[0][0] += x * x; cov[0][1] += x * y; cov[0][2] += x * z
    cov[1][0] += y * x; cov[1][1] += y * y; cov[1][2] += y * z
    cov[2][0] += z * x; cov[2][1] += z * y; cov[2][2] += z * z
  }
  const invN = 1 / Math.max(points.length, 1)
  for (let r = 0; r < 3; r += 1) {
    for (let c = 0; c < 3; c += 1) cov[r][c] *= invN
  }

  const axis1 = dominantEigenVector(cov, { x: 1, y: 0.3, z: 0.1 })
  const lambda1 = dot3(axis1, covarianceMultiply(cov, axis1))
  const deflated = cov.map((row, r) => row.map((value, c) => {
    const ar = r === 0 ? axis1.x : r === 1 ? axis1.y : axis1.z
    const ac = c === 0 ? axis1.x : c === 1 ? axis1.y : axis1.z
    return value - lambda1 * ar * ac
  }))
  const axis2 = dominantEigenVector(deflated, { x: -axis1.y, y: axis1.x, z: 0.2 })

  return p => {
    const centered = { x: p.x - mean.x, y: p.y - mean.y, z: p.z - mean.z }
    return { x: dot3(centered, axis1), y: dot3(centered, axis2) }
  }
}

function fitPoints(points: Point2[], width: number, height: number, margin = 36): (p: Point2) => Point2 {
  if (points.length === 0) return p => ({ x: p.x, y: p.y })
  let minX = Infinity
  let minY = Infinity
  let maxX = -Infinity
  let maxY = -Infinity
  for (const p of points) {
    minX = Math.min(minX, p.x)
    minY = Math.min(minY, p.y)
    maxX = Math.max(maxX, p.x)
    maxY = Math.max(maxY, p.y)
  }
  const spanX = Math.max(maxX - minX, 1e-6)
  const spanY = Math.max(maxY - minY, 1e-6)
  const scale = Math.min((width - margin * 2) / spanX, (height - margin * 2) / spanY)
  const usedW = spanX * scale
  const usedH = spanY * scale
  const ox = margin + (width - margin * 2 - usedW) / 2
  const oy = margin + (height - margin * 2 - usedH) / 2
  return p => ({
    x: ox + (p.x - minX) * scale,
    y: height - (oy + (p.y - minY) * scale),
  })
}

function drawPolyline(ctx: CanvasRenderingContext2D, points: Point2[], map: (p: Point2) => Point2, color: string, width: number): void {
  if (points.length < 2) return
  ctx.strokeStyle = color
  ctx.lineWidth = width
  ctx.beginPath()
  points.forEach((p, i) => {
    const q = map(p)
    if (i === 0) ctx.moveTo(q.x, q.y)
    else ctx.lineTo(q.x, q.y)
  })
  ctx.stroke()
}

function drawDot(ctx: CanvasRenderingContext2D, p: Point2, color: string, radius: number): void {
  ctx.fillStyle = color
  ctx.beginPath()
  ctx.arc(p.x, p.y, radius, 0, Math.PI * 2)
  ctx.fill()
}

function drawCanvasBackground(ctx: CanvasRenderingContext2D, width: number, height: number): void {
  ctx.fillStyle = '#030303'
  ctx.fillRect(0, 0, width, height)
  ctx.strokeStyle = '#161616'
  ctx.lineWidth = 1
  for (let x = 0; x <= width; x += 60) {
    ctx.beginPath()
    ctx.moveTo(x, 0)
    ctx.lineTo(x, height)
    ctx.stroke()
  }
  for (let y = 0; y <= height; y += 60) {
    ctx.beginPath()
    ctx.moveTo(0, y)
    ctx.lineTo(width, y)
    ctx.stroke()
  }
}

function currentPoseForFrame(poses: SlamPose[], frame: DecodedFrame | null, currentIndex: number): SlamPose | null {
  if (poses.length === 0) return null
  if (frame) {
    const byFrameNumber = poses.find(p => Number(p.frameId?.frameNumber ?? -1) === frame.frame_number)
    if (byFrameNumber) return byFrameNumber
  }
  return poses[Math.max(0, Math.min(currentIndex, poses.length - 1))] ?? null
}

const SlamMapCanvas: React.FC<{
  title: string
  badge: string
  poses: SlamPose[]
  groundPoints: Point3[]
  currentPose: SlamPose | null
  showCurrent: boolean
}> = ({ title, badge, poses, groundPoints, currentPose, showCurrent }) => {
  const canvasRef = useRef<HTMLCanvasElement>(null)

  useEffect(() => {
    const canvas = canvasRef.current
    const ctx = canvas?.getContext('2d')
    if (!canvas || !ctx) return
    drawCanvasBackground(ctx, MAP_WIDTH, MAP_HEIGHT)

    const path3 = poses.map(poseToVec3).filter((p): p is Point3 => p !== null)
    const current3 = currentPose ? poseToVec3(currentPose) : null
    const allPoints3 = [...path3, ...groundPoints, ...(current3 ? [current3] : [])]
    if (allPoints3.length === 0) {
      drawEmptyCanvasLabel(ctx, 'No SLAM map data')
      return
    }
    const project = createPcaProjector(allPoints3)
    const path = path3.map(project)
    const mapGroundPoints = groundPoints.map(project)
    const current = current3 ? project(current3) : null
    const allPoints = [...path, ...mapGroundPoints, ...(current ? [current] : [])]
    const map = fitPoints(allPoints, MAP_WIDTH, MAP_HEIGHT)

    ctx.fillStyle = 'rgba(47, 136, 255, 0.42)'
    for (let i = 0; i < mapGroundPoints.length; i += Math.max(1, Math.ceil(mapGroundPoints.length / 4000))) {
      const p = map(mapGroundPoints[i])
      ctx.fillRect(p.x - 1, p.y - 1, 2, 2)
    }

    drawPolyline(ctx, path, map, '#00ff88', 2)
    if (path.length > 0) drawDot(ctx, map(path[0]), '#f7f7f7', 4)
    if (showCurrent && current) {
      const p = map(current)
      drawDot(ctx, p, '#ffd400', 7)
      ctx.strokeStyle = '#101010'
      ctx.lineWidth = 2
      ctx.stroke()
    }
  }, [poses, groundPoints, currentPose, showCurrent])

  return (
    <div className="stream-card">
      <div className="stream-header">
        <span className="stream-title">{title}</span>
        <span className="stream-badge">{badge}</span>
      </div>
      <canvas className="slam-canvas" ref={canvasRef} width={MAP_WIDTH} height={MAP_HEIGHT} />
    </div>
  )
}

function drawEmptyCanvasLabel(ctx: CanvasRenderingContext2D, label: string): void {
  ctx.fillStyle = '#707070'
  ctx.font = '13px monospace'
  ctx.textAlign = 'center'
  ctx.textBaseline = 'middle'
  ctx.fillText(label, ctx.canvas.width / 2, ctx.canvas.height / 2)
}

function selectPair(pairDebug: SlamPairDebug[], currentFrameIndex: number): SlamPairDebug | null {
  if (pairDebug.length === 0) return null
  let best = pairDebug[0]
  let bestDistance = Math.abs((best.frameIndex ?? 0) - currentFrameIndex)
  for (const pair of pairDebug) {
    const distance = Math.abs((pair.frameIndex ?? 0) - currentFrameIndex)
    if (distance < bestDistance) {
      best = pair
      bestDistance = distance
    }
  }
  return best
}

function drawImageContain(
  ctx: CanvasRenderingContext2D,
  image: HTMLImageElement,
  width: number,
  height: number,
): ImageRect {
  const iw = image.naturalWidth || 1
  const ih = image.naturalHeight || 1
  const scale = Math.min(width / iw, height / ih)
  const dw = iw * scale
  const dh = ih * scale
  const x = (width - dw) / 2
  const y = (height - dh) / 2
  ctx.drawImage(image, x, y, dw, dh)
  return { x, y, width: dw, height: dh }
}

function imageRectFor(canvasWidth: number, canvasHeight: number, imageWidth: number, imageHeight: number): ImageRect {
  const scale = Math.min(canvasWidth / Math.max(imageWidth, 1), canvasHeight / Math.max(imageHeight, 1))
  const width = imageWidth * scale
  const height = imageHeight * scale
  return { x: (canvasWidth - width) / 2, y: (canvasHeight - height) / 2, width, height }
}

const SiftCorrespondencePanel: React.FC<{ slam: SlamResponse | null }> = ({ slam }) => {
  const { displayedFrame, currentIndex } = useDashboard()
  const canvasRef = useRef<HTMLCanvasElement>(null)

  const currentFrameIndex = useMemo(() => {
    const poses = slam?.framePoses ?? []
    const pose = currentPoseForFrame(poses, displayedFrame, currentIndex)
    return pose?.frameIndex ?? currentIndex
  }, [slam, displayedFrame, currentIndex])

  const pair = useMemo(
    () => selectPair(slam?.pairDebug ?? [], currentFrameIndex),
    [slam, currentFrameIndex],
  )

  useEffect(() => {
    const canvas = canvasRef.current
    const ctx = canvas?.getContext('2d')
    if (!canvas || !ctx) return
    drawCanvasBackground(ctx, VIDEO_WIDTH, VIDEO_HEIGHT)
    if (!displayedFrame?.rgbBlobUrl || !pair) {
      drawEmptyCanvasLabel(ctx, 'No SIFT correspondence data')
      return
    }

    const image = new Image()
    image.onload = () => {
      drawCanvasBackground(ctx, VIDEO_WIDTH, VIDEO_HEIGHT)
      const rect = drawImageContain(ctx, image, VIDEO_WIDTH, VIDEO_HEIGHT)
      const scaleX = rect.width / Math.max(image.naturalWidth, 1)
      const scaleY = rect.height / Math.max(image.naturalHeight, 1)
      const correspondences = pair.correspondences ?? []
      const stride = Math.max(1, Math.ceil(correspondences.length / 900))

      ctx.lineWidth = 1
      for (let i = 0; i < correspondences.length; i += stride) {
        const corr = correspondences[i]
        const road = !!corr.onRoad || !!corr.side
        const sx = rect.x + (corr.sourceX ?? 0) * scaleX
        const sy = rect.y + (corr.sourceY ?? 0) * scaleY
        const tx = rect.x + (corr.targetX ?? 0) * scaleX
        const ty = rect.y + (corr.targetY ?? 0) * scaleY
        ctx.strokeStyle = road ? 'rgba(255, 64, 64, 0.55)' : 'rgba(255, 255, 255, 0.22)'
        ctx.beginPath()
        ctx.moveTo(sx, sy)
        ctx.lineTo(tx, ty)
        ctx.stroke()
      }

      for (let i = 0; i < correspondences.length; i += stride) {
        const corr = correspondences[i]
        const road = !!corr.onRoad || !!corr.side
        const sx = rect.x + (corr.sourceX ?? 0) * scaleX
        const sy = rect.y + (corr.sourceY ?? 0) * scaleY
        drawDot(ctx, { x: sx, y: sy }, road ? '#ff3838' : '#ffffff', road ? 2.8 : 2)
      }
    }
    image.src = displayedFrame.rgbBlobUrl
  }, [displayedFrame, pair])

  return (
    <div className="stream-card">
      <div className="stream-header">
        <span className="stream-title">SIFT Features + Correspondences</span>
        <span className="stream-badge">{pair ? `${pair.onRoadCount ?? 0}/${pair.goodMatchCount ?? 0}` : 'SIFT'}</span>
      </div>
      <canvas className="slam-canvas" ref={canvasRef} width={VIDEO_WIDTH} height={VIDEO_HEIGHT} />
    </div>
  )
}

function combineMasks(annotation: DecodedAnnotation | null, labels: Set<string>): MaskRaster | null {
  if (!annotation) return null
  const masks = annotation.masks.filter(mask => labels.has(mask.label.toLowerCase()))
  if (masks.length === 0) return null
  const { width, height } = masks[0]
  const out = new Uint8Array(width * height)
  for (const decoded of masks) {
    if (decoded.width !== width || decoded.height !== height) continue
    const data = decoded.mask
    for (let i = 0; i < out.length; i += 1) {
      if (data[i]) out[i] = 1
    }
  }
  return { width, height, mask: out }
}

function firstMask(annotation: DecodedAnnotation | null, label: string): MaskRaster | null {
  const mask = annotation?.masks.find(m => m.label.toLowerCase() === label)
  if (!mask) return null
  return { width: mask.width, height: mask.height, mask: mask.mask }
}

function estimateBikeCenterX(bike: MaskRaster | null, defaultX: number): number {
  if (!bike) return defaultX
  let sum = 0
  let count = 0
  for (let y = 0; y < bike.height; y += 1) {
    const row = y * bike.width
    for (let x = 0; x < bike.width; x += 1) {
      if (bike.mask[row + x]) {
        sum += x
        count += 1
      }
    }
  }
  return count > 0 ? sum / count : defaultX
}

function segmentsForRow(mask: Uint8Array, width: number, y: number, minWidthPx: number): [number, number][] {
  const segments: [number, number][] = []
  let start = -1
  const row = y * width
  for (let x = 0; x < width; x += 1) {
    if (mask[row + x]) {
      if (start < 0) start = x
    } else if (start >= 0) {
      if (x - 1 - start >= minWidthPx) segments.push([start, x - 1])
      start = -1
    }
  }
  if (start >= 0 && width - 1 - start >= minWidthPx) segments.push([start, width - 1])
  return segments
}

function chooseSegment(segments: [number, number][], anchorX: number): [number, number] | null {
  if (segments.length === 0) return null
  const containing = segments.filter(([left, right]) => left <= anchorX && anchorX <= right)
  const choices = containing.length > 0 ? containing : segments
  return choices.reduce((best, seg) => (seg[1] - seg[0] > best[1] - best[0] ? seg : best), choices[0])
}

function maskValue(mask: MaskRaster | null, x: number, y: number): number {
  if (!mask || x < 0 || y < 0 || x >= mask.width || y >= mask.height) return 0
  return mask.mask[y * mask.width + x]
}

function extractRoadEdges(road: MaskRaster, bike: MaskRaster | null, anchorX: number): { left: Point2[]; right: Point2[]; mid: Point2[] } {
  const left: Point2[] = []
  const right: Point2[] = []
  const mid: Point2[] = []
  const minSegmentPx = Math.max(8, Math.floor(road.width * 0.02))
  const minTotalWidthPx = Math.max(60, Math.floor(road.width * 0.16))
  let prevLeft: number | null = null
  let prevRight: number | null = null

  for (let y = road.height - 24; y >= Math.floor(0.16 * road.height); y -= 6) {
    const segments = segmentsForRow(road.mask, road.width, y, minSegmentPx)
    if (segments.length === 0) continue
    const outerLeft = Math.min(...segments.map(seg => seg[0]))
    const outerRight = Math.max(...segments.map(seg => seg[1]))
    const chosen = chooseSegment([[outerLeft, outerRight]], anchorX)
    if (!chosen || chosen[1] - chosen[0] < minTotalWidthPx) continue
    const [l, r] = chosen
    if (prevLeft !== null && Math.abs(l - prevLeft) > 160) continue
    if (prevRight !== null && Math.abs(r - prevRight) > 160) continue
    const leftBlocked = maskValue(bike, l, y) || maskValue(bike, l - 1, y) || maskValue(bike, l + 1, y)
    const rightBlocked = maskValue(bike, r, y) || maskValue(bike, r - 1, y) || maskValue(bike, r + 1, y)
    if (!leftBlocked) {
      left.push({ x: l, y })
      prevLeft = l
    }
    if (!rightBlocked) {
      right.push({ x: r, y })
      prevRight = r
    }
    if (!leftBlocked && !rightBlocked) mid.push({ x: (l + r) / 2, y })
  }
  return { left, right, mid }
}

function parsePlaneSummary(slam: SlamResponse | null): { pitchDeg: number; cameraHeightM: number } {
  if (!slam?.planeWidthSummaryJson) return { pitchDeg: 18, cameraHeightM: 1.45 }
  try {
    const parsed = JSON.parse(slam.planeWidthSummaryJson) as Record<string, unknown>
    const pitch = typeof parsed.pitch_deg === 'number' ? parsed.pitch_deg : 18
    const height = typeof parsed.camera_height_m === 'number' ? parsed.camera_height_m : 1.45
    return { pitchDeg: pitch, cameraHeightM: height }
  } catch {
    return { pitchDeg: 18, cameraHeightM: 1.45 }
  }
}

function buildRoadProjectionData(
  frame: DecodedFrame | null,
  annotation: DecodedAnnotation | null,
  slam: SlamResponse | null,
): RoadProjectionData | null {
  if (!frame) return null
  const road = combineMasks(annotation, ROAD_LABELS)
  if (!road) return null
  const bike = firstMask(annotation, 'bike')
  const { pitchDeg, cameraHeightM } = parsePlaneSummary(slam)
  let projector: GroundProjector | null = null
  if (frame.camera_intrinsics) {
    try {
      projector = new GroundProjector(frame, road.width, road.height, pitchDeg, cameraHeightM)
    } catch {
      projector = null
    }
  }

  const anchorX = estimateBikeCenterX(bike, road.width / 2)
  const edges = extractRoadEdges(road, bike, anchorX)
  const roadGround: Point2[] = []
  const leftGround: Point2[] = []
  const rightGround: Point2[] = []

  if (projector) {
    const step = Math.max(6, Math.floor(Math.sqrt(road.width * road.height / 3000)))
    for (let y = 0; y < road.height; y += step) {
      const row = y * road.width
      for (let x = 0; x < road.width; x += step) {
        if (!road.mask[row + x]) continue
        const p = projector.imageToGround(x, y)
        if (p && p.y >= 0 && p.y <= 45 && Math.abs(p.x) <= 25) roadGround.push(p)
      }
    }
    for (const p of edges.left) {
      const ground = projector.imageToGround(p.x, p.y)
      if (ground) leftGround.push(ground)
    }
    for (const p of edges.right) {
      const ground = projector.imageToGround(p.x, p.y)
      if (ground) rightGround.push(ground)
    }
  }

  return {
    road,
    bike,
    projector,
    leftImage: edges.left,
    rightImage: edges.right,
    midImage: edges.mid,
    roadGround,
    leftGround,
    rightGround,
    pitchDeg,
    cameraHeightM,
  }
}

function drawMaskOverlay(ctx: CanvasRenderingContext2D, rect: ImageRect, mask: MaskRaster, color: [number, number, number, number]): void {
  const overlay = document.createElement('canvas')
  overlay.width = mask.width
  overlay.height = mask.height
  const overlayCtx = overlay.getContext('2d')
  if (!overlayCtx) return
  const imageData = overlayCtx.createImageData(mask.width, mask.height)
  for (let i = 0; i < mask.mask.length; i += 1) {
    if (!mask.mask[i]) continue
    const off = i * 4
    imageData.data[off] = color[0]
    imageData.data[off + 1] = color[1]
    imageData.data[off + 2] = color[2]
    imageData.data[off + 3] = color[3]
  }
  overlayCtx.putImageData(imageData, 0, 0)
  ctx.drawImage(overlay, rect.x, rect.y, rect.width, rect.height)
}

function drawImagePointList(ctx: CanvasRenderingContext2D, rect: ImageRect, points: Point2[], source: MaskRaster, color: string, radius: number): void {
  ctx.fillStyle = color
  for (const p of points) {
    drawDot(ctx, {
      x: rect.x + p.x / source.width * rect.width,
      y: rect.y + p.y / source.height * rect.height,
    }, color, radius)
  }
}

function drawImagePolyline(ctx: CanvasRenderingContext2D, rect: ImageRect, points: Point2[], source: MaskRaster, color: string, width: number): void {
  if (points.length < 2) return
  ctx.strokeStyle = color
  ctx.lineWidth = width
  ctx.beginPath()
  points.forEach((p, i) => {
    const x = rect.x + p.x / source.width * rect.width
    const y = rect.y + p.y / source.height * rect.height
    if (i === 0) ctx.moveTo(x, y)
    else ctx.lineTo(x, y)
  })
  ctx.stroke()
}

const RoadProjectionPanels: React.FC<{ slam: SlamResponse | null }> = ({ slam }) => {
  const { displayedFrame, displayedAnnotation } = useDashboard()
  const imageCanvasRef = useRef<HTMLCanvasElement>(null)
  const groundCanvasRef = useRef<HTMLCanvasElement>(null)
  const [selectedImagePoint, setSelectedImagePoint] = useState<Point2 | null>(null)
  const [selectedGroundPoint, setSelectedGroundPoint] = useState<Point2 | null>(null)

  const projection = useMemo(
    () => buildRoadProjectionData(displayedFrame, displayedAnnotation, slam),
    [displayedFrame, displayedAnnotation, slam],
  )

  useEffect(() => {
    setSelectedImagePoint(null)
    setSelectedGroundPoint(null)
  }, [displayedFrame?.frame_number])

  useEffect(() => {
    const canvas = imageCanvasRef.current
    const ctx = canvas?.getContext('2d')
    if (!canvas || !ctx) return
    drawCanvasBackground(ctx, VIDEO_WIDTH, VIDEO_HEIGHT)
    if (!displayedFrame?.rgbBlobUrl || !projection) {
      drawEmptyCanvasLabel(ctx, 'No road estimate data')
      return
    }
    const image = new Image()
    image.onload = () => {
      drawCanvasBackground(ctx, VIDEO_WIDTH, VIDEO_HEIGHT)
      const rect = drawImageContain(ctx, image, VIDEO_WIDTH, VIDEO_HEIGHT)
      drawMaskOverlay(ctx, rect, projection.road, [47, 136, 255, 95])
      drawImagePolyline(ctx, rect, projection.leftImage, projection.road, '#ff5bd5', 3)
      drawImagePolyline(ctx, rect, projection.rightImage, projection.road, '#46d884', 3)
      drawImagePolyline(ctx, rect, projection.midImage, projection.road, 'rgba(255, 255, 255, 0.75)', 2)
      drawImagePointList(ctx, rect, projection.leftImage, projection.road, '#ff5bd5', 2.5)
      drawImagePointList(ctx, rect, projection.rightImage, projection.road, '#46d884', 2.5)
      if (selectedImagePoint) {
        drawDot(ctx, {
          x: rect.x + selectedImagePoint.x / projection.road.width * rect.width,
          y: rect.y + selectedImagePoint.y / projection.road.height * rect.height,
        }, '#ffd400', 7)
      }
    }
    image.src = displayedFrame.rgbBlobUrl
  }, [displayedFrame, projection, selectedImagePoint])

  useEffect(() => {
    const canvas = groundCanvasRef.current
    const ctx = canvas?.getContext('2d')
    if (!canvas || !ctx) return
    drawCanvasBackground(ctx, VIDEO_WIDTH, VIDEO_HEIGHT)
    if (!projection?.projector) {
      drawEmptyCanvasLabel(ctx, 'No ground projection data')
      return
    }
    const allPoints = [
      ...projection.roadGround,
      ...projection.leftGround,
      ...projection.rightGround,
      ...(selectedGroundPoint ? [selectedGroundPoint] : []),
    ]
    if (allPoints.length === 0) {
      drawEmptyCanvasLabel(ctx, 'No projected road points')
      return
    }
    const map = fitPoints(allPoints, VIDEO_WIDTH, VIDEO_HEIGHT, 38)
    ctx.fillStyle = 'rgba(47, 136, 255, 0.58)'
    for (const point of projection.roadGround) {
      const p = map(point)
      ctx.fillRect(p.x - 1.2, p.y - 1.2, 2.4, 2.4)
    }
    drawPolyline(ctx, projection.leftGround, map, '#ff5bd5', 2.5)
    drawPolyline(ctx, projection.rightGround, map, '#46d884', 2.5)
    if (selectedGroundPoint) drawDot(ctx, map(selectedGroundPoint), '#ffd400', 7)

    ctx.fillStyle = '#707070'
    ctx.font = '11px monospace'
    ctx.textAlign = 'left'
    ctx.fillText(`pitch ${projection.pitchDeg.toFixed(1)} deg  height ${projection.cameraHeightM.toFixed(2)} m`, 14, VIDEO_HEIGHT - 16)
  }, [projection, selectedGroundPoint])

  const handleRoadClick = useCallback((event: React.MouseEvent<HTMLCanvasElement>) => {
    if (!projection?.projector || !displayedFrame) return
    const canvas = imageCanvasRef.current
    if (!canvas) return
    const bounds = canvas.getBoundingClientRect()
    const x = (event.clientX - bounds.left) * (VIDEO_WIDTH / bounds.width)
    const y = (event.clientY - bounds.top) * (VIDEO_HEIGHT / bounds.height)
    const rgbWidth = displayedFrame.rgb_width || projection.road.width
    const rgbHeight = displayedFrame.rgb_height || projection.road.height
    const rect = imageRectFor(VIDEO_WIDTH, VIDEO_HEIGHT, rgbWidth, rgbHeight)
    if (x < rect.x || y < rect.y || x > rect.x + rect.width || y > rect.y + rect.height) return
    const rgbU = (x - rect.x) / rect.width * rgbWidth
    const rgbV = (y - rect.y) / rect.height * rgbHeight
    const u = rgbU * projection.road.width / Math.max(rgbWidth, 1)
    const v = rgbV * projection.road.height / Math.max(rgbHeight, 1)
    const imagePoint = { x: u, y: v }
    setSelectedImagePoint(imagePoint)
    setSelectedGroundPoint(projection.projector.imageToGround(u, v))
  }, [displayedFrame, projection])

  return (
    <div className="slam-two-column">
      <div className="stream-card">
        <div className="stream-header">
          <span className="stream-title">Road Mask + Edge Estimates</span>
          <span className="stream-badge">ROAD</span>
        </div>
        <canvas
          className="slam-canvas clickable"
          ref={imageCanvasRef}
          width={VIDEO_WIDTH}
          height={VIDEO_HEIGHT}
          onClick={handleRoadClick}
        />
      </div>
      <div className="stream-card">
        <div className="stream-header">
          <span className="stream-title">Ground Plane Projection</span>
          <span className="stream-badge">GROUND</span>
        </div>
        <canvas className="slam-canvas" ref={groundCanvasRef} width={VIDEO_WIDTH} height={VIDEO_HEIGHT} />
      </div>
    </div>
  )
}

const LocalizationMappingTab: React.FC = () => {
  const { currentIndex, displayedFrame, idoslamData, idoslamError } = useDashboard()
  const groundPoints = useMemo(
    () => (idoslamData?.groundPoints ?? [])
      .map(point => groundPointToVec3(point.point))
      .filter((p): p is Point3 => p !== null),
    [idoslamData],
  )
  const rawCurrentPose = useMemo(
    () => currentPoseForFrame(idoslamData?.framePoses ?? [], displayedFrame, currentIndex),
    [idoslamData, displayedFrame, currentIndex],
  )
  const refinedCurrentPose = useMemo(
    () => currentPoseForFrame(idoslamData?.refinedFramePoses ?? [], displayedFrame, currentIndex),
    [idoslamData, displayedFrame, currentIndex],
  )

  if (idoslamError) {
    return <div className="stream-card"><span className="stream-title">{idoslamError}</span></div>
  }

  return (
    <section className="stream-section">
      <div className="slam-two-column">
        <SlamMapCanvas
          title="Pre-GPS Optimization Map"
          badge="VO"
          poses={idoslamData?.framePoses ?? []}
          groundPoints={groundPoints}
          currentPose={rawCurrentPose}
          showCurrent={false}
        />
        <SlamMapCanvas
          title="Post-GPS Optimization Map"
          badge="GPS"
          poses={idoslamData?.refinedFramePoses ?? []}
          groundPoints={groundPoints}
          currentPose={refinedCurrentPose}
          showCurrent
        />
      </div>

      <SiftCorrespondencePanel slam={idoslamData} />
      <RoadProjectionPanels slam={idoslamData} />
    </section>
  )
}

export default LocalizationMappingTab
