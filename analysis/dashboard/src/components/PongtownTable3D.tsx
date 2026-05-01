import React, { useEffect, useMemo, useRef, useState } from 'react'
import * as THREE from 'three'
import { OrbitControls } from 'three/examples/jsm/controls/OrbitControls.js'
import type { PongtownFrameRecord, PongtownResponse } from '../types'

interface PongtownTable3DProps {
  summary?: PongtownResponse
  frames?: PongtownFrameRecord[]
  currentFrame?: PongtownFrameRecord
  currentFrameIndex?: number
  currentFrameNumber?: number
}

interface BounceMarker {
  frameIdx: number
  frameNumber: number
  xM: number
  zM: number
  insideTable: boolean
  correctionApplied?: boolean
}

interface SnookerMarker {
  objectId: number
  label: string
  xM: number
  zM: number
  insideTable: boolean
  color: number
}

interface TimedSnookerFrame {
  frame: PongtownFrameRecord
  timeS: number
  markers: SnookerMarker[]
}

interface SceneState {
  scene: THREE.Scene
  camera: THREE.PerspectiveCamera
  renderer: THREE.WebGLRenderer
  controls: OrbitControls
  markers: THREE.Group
  resizeObserver: ResizeObserver
  animationId: number
}

const DEFAULT_TABLE_LENGTH_MM = 2740
const DEFAULT_TABLE_WIDTH_MM = 1525
const NET_HEIGHT_M = 0.1525
const TABLE_THICKNESS_M = 0.07
const CORRECTION_OUTLIER_TABLE_LENGTHS = 2
const CORRECTION_MAX_AXIS_NORM = 2
const CORRECTION_INFLUENCE_POWER = 1.15
const SNOOKER_BLACK_END_X_SIGN = -1
const SNOOKER_MOTION_WINDOW_S = 5
const SNOOKER_STATIC_LOOKAHEAD_S = 10
const SNOOKER_YELLOW_MOVEMENT_THRESHOLD_SCALE = 0.5
const SNOOKER_FALLBACK_JITTER_THRESHOLD_M = 0.01
const FALLBACK_FRAME_RATE_FPS = 30

type SportKind = 'pingpong' | 'snooker' | 'unknown'

const clamp = (value: number, min: number, max: number): number => (
  Math.min(Math.max(value, min), max)
)

const distanceOutsideTable = (
  xM: number,
  zM: number,
  halfLengthM: number,
  halfWidthM: number,
): number => {
  const dx = Math.max(0, Math.abs(xM) - halfLengthM)
  const dz = Math.max(0, Math.abs(zM) - halfWidthM)
  return Math.hypot(dx, dz)
}

const correctAxisCoordinate = (
  valueM: number,
  halfExtentM: number,
  maxAbsUsedM: number,
): number => {
  if (halfExtentM <= 0 || maxAbsUsedM <= halfExtentM) {
    return clamp(valueM, -halfExtentM, halfExtentM)
  }

  const cappedMaxAbsM = Math.min(maxAbsUsedM, halfExtentM * CORRECTION_MAX_AXIS_NORM)
  const scale = halfExtentM / cappedMaxAbsM
  const normalized = Math.abs(valueM) / halfExtentM
  const maxNormalized = cappedMaxAbsM / halfExtentM
  const influence = Math.pow(
    clamp(normalized / maxNormalized, 0, 1),
    CORRECTION_INFLUENCE_POWER,
  )
  const adjustedScale = 1 - influence * (1 - scale)
  return clamp(valueM * adjustedScale, -halfExtentM, halfExtentM)
}

const applyBouncePoseCorrections = (
  bounces: BounceMarker[],
  tableLengthM: number,
  tableWidthM: number,
): BounceMarker[] => {
  if (bounces.length === 0) return bounces

  const halfLengthM = tableLengthM / 2
  const halfWidthM = tableWidthM / 2
  const outlierDistanceM = tableLengthM * CORRECTION_OUTLIER_TABLE_LENGTHS
  const scalingCandidates = bounces.filter((bounce) => (
    distanceOutsideTable(bounce.xM, bounce.zM, halfLengthM, halfWidthM) <= outlierDistanceM
  ))
  const maxAbsUsedXM = Math.max(
    halfLengthM,
    ...scalingCandidates.map((bounce) => Math.abs(bounce.xM)),
  )
  const maxAbsUsedZM = Math.max(
    halfWidthM,
    ...scalingCandidates.map((bounce) => Math.abs(bounce.zM)),
  )

  return bounces.map((bounce) => {
    const isOutlier =
      distanceOutsideTable(bounce.xM, bounce.zM, halfLengthM, halfWidthM) > outlierDistanceM
    const correctedXM = isOutlier
      ? clamp(bounce.xM, -halfLengthM, halfLengthM)
      : correctAxisCoordinate(bounce.xM, halfLengthM, maxAbsUsedXM)
    const correctedZM = isOutlier
      ? clamp(bounce.zM, -halfWidthM, halfWidthM)
      : correctAxisCoordinate(bounce.zM, halfWidthM, maxAbsUsedZM)

    return {
      ...bounce,
      xM: correctedXM,
      zM: correctedZM,
      insideTable: true,
      correctionApplied:
        Math.abs(correctedXM - bounce.xM) > 0.001 || Math.abs(correctedZM - bounce.zM) > 0.001,
    }
  })
}

const numberList = (value: unknown): number[] => {
  if (!value) return []
  if (Array.isArray(value)) return value.map(Number).filter(Number.isFinite)
  if (ArrayBuffer.isView(value)) {
    return Array.from(value as unknown as ArrayLike<number>, Number).filter(Number.isFinite)
  }
  return []
}

const sportKind = (...records: (PongtownResponse | null | undefined)[]): SportKind => {
  for (const record of records) {
    if (!record) continue
    if (record.tracking === 'snookerTracking' || record.sportMode === 2) return 'snooker'
    if (record.tracking === 'pingpongTracking' || record.sportMode === 1) return 'pingpong'
    if (record.ballTrajectory || (record.ballPositions?.length ?? 0) > 0) return 'pingpong'
  }
  return 'unknown'
}

const snookerBallColor = (label: string): number => {
  const normalized = label.toLowerCase()
  if (normalized.includes('white')) return 0xf5f0dc
  if (normalized.includes('yellow')) return 0xf3d23b
  if (normalized.includes('green')) return 0x2fa84f
  if (normalized.includes('brown')) return 0x8b5a2b
  if (normalized.includes('blue')) return 0x3388ff
  if (normalized.includes('pink')) return 0xff8fc5
  if (normalized.includes('black')) return 0x111111
  if (normalized.includes('red')) return 0xd92626
  return 0xffd84d
}

const snookerLabelKey = (label: string): string => (
  label.toLowerCase().replace(/\s+/g, ' ').trim()
)

const markerIdentityKey = (marker: SnookerMarker): string => (
  `${snookerLabelKey(marker.label)}#${marker.objectId}`
)

const markerDistanceM = (a: SnookerMarker, b: SnookerMarker): number => (
  Math.hypot(a.xM - b.xM, a.zM - b.zM)
)

const median = (values: number[]): number => {
  if (values.length === 0) return 0
  const sorted = [...values].sort((a, b) => a - b)
  return sorted[Math.floor(sorted.length / 2)]
}

const snookerMarkersForFrame = (frame?: PongtownFrameRecord): SnookerMarker[] => (
  (frame?.record.snookerTracking?.ballPositions ?? [])
    .map((ball) => {
      const table = numberList(ball.tableXyzMm)
      if (table.length < 2) return null
      const label = ball.label ?? `ball ${ball.objectId ?? ''}`
      return {
        objectId: Number(ball.objectId ?? 0),
        label,
        xM: table[0] / 1000,
        zM: table[1] / 1000,
        insideTable: Boolean(ball.insideTable),
        color: snookerBallColor(label),
      }
    })
    .filter((item): item is SnookerMarker => item !== null)
)

const buildObjectIdCounts = (timedFrames: TimedSnookerFrame[]): Map<string, number> => {
  const counts = new Map<string, number>()
  for (const timedFrame of timedFrames) {
    for (const marker of timedFrame.markers) {
      if (marker.objectId <= 0) continue
      const key = markerIdentityKey(marker)
      counts.set(key, (counts.get(key) ?? 0) + 1)
    }
  }
  return counts
}

const markerUsesObjectIdentity = (
  marker: SnookerMarker,
  objectIdCounts: Map<string, number>,
): boolean => (
  marker.objectId > 0 && (objectIdCounts.get(markerIdentityKey(marker)) ?? 0) > 1
)

const nearestMatchingMarker = (
  reference: SnookerMarker,
  candidates: SnookerMarker[],
  objectIdCounts: Map<string, number>,
): SnookerMarker | null => {
  const labelKey = snookerLabelKey(reference.label)
  const useObjectIdentity = markerUsesObjectIdentity(reference, objectIdCounts)
  let best: SnookerMarker | null = null
  let bestDistance = Number.POSITIVE_INFINITY

  for (const candidate of candidates) {
    if (snookerLabelKey(candidate.label) !== labelKey) continue
    if (useObjectIdentity && candidate.objectId !== reference.objectId) continue
    const distance = markerDistanceM(reference, candidate)
    if (distance < bestDistance) {
      best = candidate
      bestDistance = distance
    }
  }
  return best
}

const frameTimeS = (frame: PongtownFrameRecord): number => {
  if (frame.timestampNs > 0) return frame.timestampNs / 1_000_000_000
  return frame.frameIndex / FALLBACK_FRAME_RATE_FPS
}

const buildTimedSnookerFrames = (frames?: PongtownFrameRecord[]): TimedSnookerFrame[] => (
  (frames ?? [])
    .map((frame) => ({
      frame,
      timeS: frameTimeS(frame),
      markers: snookerMarkersForFrame(frame),
    }))
    .filter((timedFrame) => timedFrame.markers.length > 0)
    .sort((a, b) => a.timeS - b.timeS || a.frame.frameIndex - b.frame.frameIndex)
)

const computeSnookerJitterThresholdM = (
  timedFrames: TimedSnookerFrame[],
  objectIdCounts: Map<string, number>,
): number => {
  let previousYellow: SnookerMarker | null = null
  let maxYellowMoveM = 0

  for (const timedFrame of timedFrames) {
    const yellowMarkers = timedFrame.markers.filter((marker) => (
      snookerLabelKey(marker.label).includes('yellow')
    ))
    if (yellowMarkers.length === 0) continue

    if (previousYellow === null) {
      previousYellow = yellowMarkers[0]
      continue
    }

    const matched = nearestMatchingMarker(previousYellow, yellowMarkers, objectIdCounts)
    if (matched === null) continue
    maxYellowMoveM = Math.max(maxYellowMoveM, markerDistanceM(previousYellow, matched))
    previousYellow = matched
  }

  return maxYellowMoveM > 0
    ? maxYellowMoveM * SNOOKER_YELLOW_MOVEMENT_THRESHOLD_SCALE
    : SNOOKER_FALLBACK_JITTER_THRESHOLD_M
}

const hasSnookerMotionNearFrame = (
  marker: SnookerMarker,
  currentFrameIndex: number,
  timedFrames: TimedSnookerFrame[],
  thresholdM: number,
  objectIdCounts: Map<string, number>,
): boolean => {
  const currentTimeS = timedFrames[currentFrameIndex]?.timeS
  if (currentTimeS === undefined) return false

  let previous = marker
  for (let i = currentFrameIndex - 1; i >= 0; i -= 1) {
    const timedFrame = timedFrames[i]
    if (currentTimeS - timedFrame.timeS > SNOOKER_MOTION_WINDOW_S) break
    const matched = nearestMatchingMarker(previous, timedFrame.markers, objectIdCounts)
    if (matched === null) continue
    if (markerDistanceM(marker, matched) > thresholdM) return true
    previous = matched
  }

  let next = marker
  for (let i = currentFrameIndex + 1; i < timedFrames.length; i += 1) {
    const timedFrame = timedFrames[i]
    if (timedFrame.timeS - currentTimeS > SNOOKER_MOTION_WINDOW_S) break
    const matched = nearestMatchingMarker(next, timedFrame.markers, objectIdCounts)
    if (matched === null) continue
    if (markerDistanceM(marker, matched) > thresholdM) return true
    next = matched
  }

  return false
}

const stableSnookerPosition = (
  marker: SnookerMarker,
  currentFrameIndex: number,
  timedFrames: TimedSnookerFrame[],
  thresholdM: number,
  objectIdCounts: Map<string, number>,
): Pick<SnookerMarker, 'xM' | 'zM'> => {
  if (timedFrames[currentFrameIndex] === undefined) return marker

  let segmentStartIndex = currentFrameIndex
  let segmentStartMarker = marker

  let previous = marker
  for (let i = currentFrameIndex - 1; i >= 0; i -= 1) {
    const timedFrame = timedFrames[i]
    const matched = nearestMatchingMarker(previous, timedFrame.markers, objectIdCounts)
    if (matched === null) continue
    if (markerDistanceM(marker, matched) > thresholdM) break
    segmentStartIndex = i
    segmentStartMarker = matched
    previous = matched
  }

  const segmentStartTimeS = timedFrames[segmentStartIndex].timeS
  const positions: SnookerMarker[] = [segmentStartMarker]
  let next = segmentStartMarker
  for (let i = segmentStartIndex + 1; i < timedFrames.length; i += 1) {
    const timedFrame = timedFrames[i]
    if (timedFrame.timeS - segmentStartTimeS > SNOOKER_STATIC_LOOKAHEAD_S) break
    const matched = nearestMatchingMarker(next, timedFrame.markers, objectIdCounts)
    if (matched === null) continue
    if (markerDistanceM(segmentStartMarker, matched) > thresholdM) break
    positions.push(matched)
    next = matched
  }

  return {
    xM: median(positions.map((position) => position.xM)),
    zM: median(positions.map((position) => position.zM)),
  }
}

const smoothSnookerMarkers = (
  currentFrame: PongtownFrameRecord | undefined,
  timedFrames: TimedSnookerFrame[],
): SnookerMarker[] => {
  const currentMarkers = snookerMarkersForFrame(currentFrame)
  if (!currentFrame || currentMarkers.length === 0 || timedFrames.length === 0) {
    return currentMarkers
  }

  const currentTimedFrameIndex = timedFrames.findIndex((timedFrame) => (
    timedFrame.frame.frameIndex === currentFrame.frameIndex
    || timedFrame.frame.frameNumber === currentFrame.frameNumber
  ))
  if (currentTimedFrameIndex < 0) return currentMarkers

  const objectIdCounts = buildObjectIdCounts(timedFrames)
  const thresholdM = computeSnookerJitterThresholdM(timedFrames, objectIdCounts)

  return currentMarkers.map((marker) => {
    if (hasSnookerMotionNearFrame(marker, currentTimedFrameIndex, timedFrames, thresholdM, objectIdCounts)) {
      return marker
    }
    const stablePosition = stableSnookerPosition(
      marker,
      currentTimedFrameIndex,
      timedFrames,
      thresholdM,
      objectIdCounts,
    )
    return { ...marker, ...stablePosition }
  })
}

const makeLabelTexture = (text: string, active: boolean): THREE.CanvasTexture => {
  const canvas = document.createElement('canvas')
  canvas.width = 256
  canvas.height = 96
  const ctx = canvas.getContext('2d')
  if (ctx) {
    ctx.clearRect(0, 0, canvas.width, canvas.height)
    ctx.fillStyle = active ? 'rgba(0, 255, 136, 0.94)' : 'rgba(8, 12, 10, 0.88)'
    ctx.strokeStyle = active ? 'rgba(255, 255, 255, 0.92)' : 'rgba(255, 255, 255, 0.34)'
    ctx.lineWidth = 4
    ctx.beginPath()
    ctx.roundRect(6, 10, canvas.width - 12, canvas.height - 20, 10)
    ctx.fill()
    ctx.stroke()
    ctx.fillStyle = active ? '#001b0f' : '#f1f5f2'
    ctx.font = '700 34px Arial'
    ctx.textAlign = 'center'
    ctx.textBaseline = 'middle'
    ctx.fillText(text, canvas.width / 2, canvas.height / 2 + 2)
  }
  const texture = new THREE.CanvasTexture(canvas)
  texture.minFilter = THREE.LinearFilter
  texture.magFilter = THREE.LinearFilter
  return texture
}

const disposeObject = (obj: THREE.Object3D): void => {
  obj.traverse((child) => {
    const mesh = child as THREE.Mesh
    const material = mesh.material as THREE.Material | THREE.Material[] | undefined
    const geometry = mesh.geometry as THREE.BufferGeometry | undefined
    if (geometry) geometry.dispose()
    if (Array.isArray(material)) {
      material.forEach((item) => item.dispose())
    } else if (material) {
      const matWithMap = material as THREE.Material & { map?: THREE.Texture }
      if (matWithMap.map) matWithMap.map.dispose()
      material.dispose()
    }
  })
}

const addSurfaceStrip = (
  parent: THREE.Group,
  xM: number,
  zM: number,
  widthM: number,
  depthM: number,
  color: number,
  heightM = 0.006,
): void => {
  const strip = new THREE.Mesh(
    new THREE.BoxGeometry(widthM, heightM, depthM),
    new THREE.MeshStandardMaterial({ color, roughness: 0.58 }),
  )
  strip.position.set(xM, TABLE_THICKNESS_M / 2 + heightM / 2 + 0.001, zM)
  parent.add(strip)
}

const addLine = (
  parent: THREE.Group,
  xM: number,
  zM: number,
  widthM: number,
  depthM: number,
): void => {
  addSurfaceStrip(parent, xM, zM, widthM, depthM, 0xf8fbf4)
}

const addDisc = (
  parent: THREE.Group,
  xM: number,
  zM: number,
  radiusM: number,
  color: number,
  heightM = 0.008,
): void => {
  const disc = new THREE.Mesh(
    new THREE.CylinderGeometry(radiusM, radiusM, heightM, 40),
    new THREE.MeshStandardMaterial({ color, roughness: 0.62 }),
  )
  disc.position.set(xM, TABLE_THICKNESS_M / 2 + heightM / 2 + 0.003, zM)
  parent.add(disc)
}

const addSnookerArc = (
  parent: THREE.Group,
  xM: number,
  zM: number,
  radiusM: number,
  bulgeXSign: -1 | 1,
): void => {
  const startAngle = bulgeXSign > 0 ? -Math.PI / 2 : Math.PI / 2
  const endAngle = bulgeXSign > 0 ? Math.PI / 2 : (3 * Math.PI) / 2
  const curve = new THREE.EllipseCurve(xM, zM, radiusM, radiusM, startAngle, endAngle)
  const points = curve.getPoints(72).map((point) => (
    new THREE.Vector3(point.x, TABLE_THICKNESS_M / 2 + 0.012, point.y)
  ))
  const arc = new THREE.Line(
    new THREE.BufferGeometry().setFromPoints(points),
    new THREE.LineBasicMaterial({ color: 0xf8fbf4 }),
  )
  parent.add(arc)
}

const addSnookerMarkings = (
  parent: THREE.Group,
  tableLengthM: number,
  tableWidthM: number,
  cushionM: number,
): void => {
  const playableWidthM = Math.max(0.1, tableWidthM - cushionM * 2)
  const baulkDistanceM = Math.min(0.737, tableLengthM * 0.28)
  const blackEndSign = SNOOKER_BLACK_END_X_SIGN
  const baulkXM = -blackEndSign * (tableLengthM / 2 - baulkDistanceM)
  const dRadiusM = Math.min(0.292, playableWidthM * 0.32, baulkDistanceM * 0.66)

  addLine(parent, baulkXM, 0, 0.014, playableWidthM)
  addSnookerArc(parent, baulkXM, 0, dRadiusM, blackEndSign)

  const blackXM = blackEndSign * (tableLengthM / 2 - Math.min(0.324, tableLengthM * 0.12))
  const pinkXM = blackEndSign * (tableLengthM / 4)
  const spotRadiusM = Math.min(0.022, tableWidthM * 0.014)
  for (const [xM, zM] of [
    [blackXM, 0],
    [pinkXM, 0],
    [0, 0],
    [baulkXM, 0],
    [baulkXM, -dRadiusM],
    [baulkXM, dRadiusM],
  ]) {
    addDisc(parent, xM, zM, spotRadiusM, 0xf1e2bd, 0.006)
  }

  const pocketRadiusM = Math.min(0.082, Math.max(0.055, tableWidthM * 0.037))
  const pocketInsetM = cushionM * 0.45
  for (const [xM, zM] of [
    [-tableLengthM / 2 + pocketInsetM, -tableWidthM / 2 + pocketInsetM],
    [tableLengthM / 2 - pocketInsetM, -tableWidthM / 2 + pocketInsetM],
    [-tableLengthM / 2 + pocketInsetM, tableWidthM / 2 - pocketInsetM],
    [tableLengthM / 2 - pocketInsetM, tableWidthM / 2 - pocketInsetM],
    [0, -tableWidthM / 2 + pocketInsetM],
    [0, tableWidthM / 2 - pocketInsetM],
  ]) {
    addDisc(parent, xM, zM, pocketRadiusM, 0x050505, 0.012)
  }
}

const buildTable = (
  scene: THREE.Scene,
  tableLengthM: number,
  tableWidthM: number,
  drawNet: boolean,
  sport: SportKind,
): void => {
  const table = new THREE.Group()
  const isSnooker = sport === 'snooker'

  const top = new THREE.Mesh(
    new THREE.BoxGeometry(tableLengthM, TABLE_THICKNESS_M, tableWidthM),
    new THREE.MeshStandardMaterial({
      color: isSnooker ? 0x0f5638 : 0x145c40,
      roughness: 0.68,
      metalness: 0.02,
    }),
  )
  top.position.y = 0
  table.add(top)

  if (isSnooker) {
    const cushionM = Math.min(0.095, Math.max(0.07, tableWidthM * 0.052))
    addSurfaceStrip(table, 0, -tableWidthM / 2 + cushionM / 2, tableLengthM, cushionM, 0x0a3b29, 0.03)
    addSurfaceStrip(table, 0, tableWidthM / 2 - cushionM / 2, tableLengthM, cushionM, 0x0a3b29, 0.03)
    addSurfaceStrip(table, -tableLengthM / 2 + cushionM / 2, 0, cushionM, tableWidthM, 0x0a3b29, 0.03)
    addSurfaceStrip(table, tableLengthM / 2 - cushionM / 2, 0, cushionM, tableWidthM, 0x0a3b29, 0.03)
    addSnookerMarkings(table, tableLengthM, tableWidthM, cushionM)
  } else {
    const rail = 0.025
    addLine(table, 0, -tableWidthM / 2 + rail / 2, tableLengthM, rail)
    addLine(table, 0, tableWidthM / 2 - rail / 2, tableLengthM, rail)
    addLine(table, -tableLengthM / 2 + rail / 2, 0, rail, tableWidthM)
    addLine(table, tableLengthM / 2 - rail / 2, 0, rail, tableWidthM)
    if (drawNet) addLine(table, 0, 0, tableLengthM, 0.012)
  }

  const legMaterial = new THREE.MeshStandardMaterial({ color: 0x161b18, roughness: 0.82 })
  const legGeometry = new THREE.BoxGeometry(0.07, 0.68, 0.07)
  for (const x of [-tableLengthM * 0.38, tableLengthM * 0.38]) {
    for (const z of [-tableWidthM * 0.38, tableWidthM * 0.38]) {
      const leg = new THREE.Mesh(legGeometry.clone(), legMaterial.clone())
      leg.position.set(x, -0.375, z)
      table.add(leg)
    }
  }

  if (drawNet) {
    const net = new THREE.Mesh(
      new THREE.PlaneGeometry(tableWidthM + 0.305, NET_HEIGHT_M),
      new THREE.MeshStandardMaterial({
        color: 0x2c2f35,
        side: THREE.DoubleSide,
        transparent: true,
        opacity: 0.68,
        roughness: 0.7,
      }),
    )
    net.rotation.y = Math.PI / 2
    net.position.set(0, TABLE_THICKNESS_M / 2 + NET_HEIGHT_M / 2, 0)
    table.add(net)

    const netTop = new THREE.Mesh(
      new THREE.BoxGeometry(0.026, 0.018, tableWidthM + 0.335),
      new THREE.MeshStandardMaterial({ color: 0xf2f2f2, roughness: 0.44 }),
    )
    netTop.position.set(0, TABLE_THICKNESS_M / 2 + NET_HEIGHT_M + 0.008, 0)
    table.add(netTop)
  }

  scene.add(table)
}

const PongtownTable3D: React.FC<PongtownTable3DProps> = ({
  summary,
  frames,
  currentFrame,
  currentFrameIndex,
  currentFrameNumber,
}) => {
  const containerRef = useRef<HTMLDivElement | null>(null)
  const sceneRef = useRef<SceneState | null>(null)
  const [applyPoseCorrections, setApplyPoseCorrections] = useState(true)

  const tableSpec = summary ?? currentFrame?.record
  const tableLengthM = (Number(tableSpec?.tableWidthMm) || DEFAULT_TABLE_LENGTH_MM) / 1000
  const tableWidthM = (Number(tableSpec?.tableHeightMm) || DEFAULT_TABLE_WIDTH_MM) / 1000
  const sport = sportKind(currentFrame?.record, summary)
  const drawNet = sport === 'pingpong' && (Number(tableSpec?.netHeightMm) || NET_HEIGHT_M * 1000) > 0

  const bounces = useMemo<BounceMarker[]>(() => {
    const raw = summary?.pingpongTracking?.ballTrajectory?.bounces
      ?? summary?.ballTrajectory?.bounces
      ?? []
    return raw
      .map((bounce) => {
        const table = numberList(bounce.tableXyzMm)
        if (table.length < 2) return null
        return {
          frameIdx: Number(bounce.frameIdx ?? 0),
          frameNumber: Number(bounce.frameNumber ?? bounce.frameIdx ?? 0),
          xM: table[0] / 1000,
          zM: table[1] / 1000,
          insideTable: Boolean(bounce.insideTable),
        }
      })
      .filter((item): item is BounceMarker => item !== null)
      .sort((a, b) => a.frameIdx - b.frameIdx)
  }, [summary])

  const displayBounces = useMemo(() => (
    applyPoseCorrections
      ? applyBouncePoseCorrections(bounces, tableLengthM, tableWidthM)
      : bounces
  ), [applyPoseCorrections, bounces, tableLengthM, tableWidthM])

  const visibleBounces = useMemo(() => {
    if (currentFrameNumber !== undefined && displayBounces.some((bounce) => bounce.frameNumber > 0)) {
      return displayBounces.filter((bounce) => bounce.frameNumber <= currentFrameNumber)
    }
    if (currentFrameIndex === undefined) return displayBounces
    return displayBounces.filter((bounce) => bounce.frameIdx <= currentFrameIndex)
  }, [displayBounces, currentFrameIndex, currentFrameNumber])

  const timedSnookerFrames = useMemo(() => buildTimedSnookerFrames(frames), [frames])
  const snookerBalls = useMemo<SnookerMarker[]>(
    () => smoothSnookerMarkers(currentFrame, timedSnookerFrames),
    [currentFrame, timedSnookerFrames],
  )

  useEffect(() => {
    const container = containerRef.current
    if (!container) return

    const scene = new THREE.Scene()
    scene.background = new THREE.Color(0x050706)

    const camera = new THREE.PerspectiveCamera(42, 1, 0.01, 100)
    const cameraXSign = sport === 'snooker' ? SNOOKER_BLACK_END_X_SIGN : 1
    camera.position.set(
      cameraXSign * Math.max(2.7, tableLengthM * 0.92),
      Math.max(2.0, tableLengthM * 0.58),
      Math.max(2.55, tableWidthM * 1.55),
    )

    const renderer = new THREE.WebGLRenderer({ antialias: true, preserveDrawingBuffer: true })
    renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2))
    renderer.shadowMap.enabled = true
    renderer.outputColorSpace = THREE.SRGBColorSpace
    container.appendChild(renderer.domElement)

    const controls = new OrbitControls(camera, renderer.domElement)
    controls.enableDamping = true
    controls.dampingFactor = 0.08
    controls.target.set(0, 0.02, 0)
    controls.minDistance = 1.5
    controls.maxDistance = Math.max(7, tableLengthM * 2.2)
    controls.maxPolarAngle = Math.PI * 0.48

    scene.add(new THREE.AmbientLight(0xffffff, 0.58))
    const key = new THREE.DirectionalLight(0xffffff, 1.15)
    key.position.set(2.2, 3.8, 2.3)
    scene.add(key)
    const fill = new THREE.DirectionalLight(0x7fffd2, 0.24)
    fill.position.set(-3, 1.6, -1.6)
    scene.add(fill)

    const floor = new THREE.Mesh(
      new THREE.PlaneGeometry(Math.max(6.2, tableLengthM + 1.8), Math.max(4.4, tableWidthM + 1.6)),
      new THREE.MeshStandardMaterial({ color: 0x111611, roughness: 0.9 }),
    )
    floor.rotation.x = -Math.PI / 2
    floor.position.y = -0.72
    scene.add(floor)

    buildTable(scene, tableLengthM, tableWidthM, drawNet, sport)

    const markers = new THREE.Group()
    scene.add(markers)

    const resize = (): void => {
      const width = Math.max(1, container.clientWidth)
      const height = Math.max(1, container.clientHeight)
      renderer.setSize(width, height, false)
      camera.aspect = width / height
      camera.updateProjectionMatrix()
    }
    const resizeObserver = new ResizeObserver(resize)
    resizeObserver.observe(container)
    resize()

    let animationId = 0
    const animate = (): void => {
      controls.update()
      renderer.render(scene, camera)
      animationId = window.requestAnimationFrame(animate)
    }
    animate()

    sceneRef.current = {
      scene,
      camera,
      renderer,
      controls,
      markers,
      resizeObserver,
      animationId,
    }

    return () => {
      const state = sceneRef.current
      if (!state) return
      window.cancelAnimationFrame(state.animationId)
      state.resizeObserver.disconnect()
      state.controls.dispose()
      disposeObject(state.scene)
      state.renderer.dispose()
      state.renderer.domElement.remove()
      sceneRef.current = null
    }
  }, [tableLengthM, tableWidthM, drawNet, sport])

  useEffect(() => {
    const state = sceneRef.current
    if (!state) return
    while (state.markers.children.length > 0) {
      const child = state.markers.children.pop()
      if (child) disposeObject(child)
    }

    if (sport === 'snooker') {
      for (const ballMarker of snookerBalls) {
        const marker = new THREE.Group()
        const ball = new THREE.Mesh(
          new THREE.SphereGeometry(0.045, 28, 18),
          new THREE.MeshStandardMaterial({
            color: ballMarker.color,
            emissive: ballMarker.color,
            emissiveIntensity: 0.08,
            roughness: 0.38,
          }),
        )
        ball.position.set(ballMarker.xM, TABLE_THICKNESS_M / 2 + 0.065, ballMarker.zM)
        marker.add(ball)

        const texture = makeLabelTexture(
          ballMarker.label.replace(' ball', '').slice(0, 7) || `${ballMarker.objectId}`,
          false,
        )
        const label = new THREE.Sprite(new THREE.SpriteMaterial({ map: texture, transparent: true }))
        label.position.set(ballMarker.xM, TABLE_THICKNESS_M / 2 + 0.2, ballMarker.zM)
        label.scale.set(0.34, 0.128, 1)
        marker.add(label)

        state.markers.add(marker)
      }
      return
    }

    const lastVisibleFrame = visibleBounces.at(-1)?.frameIdx
    for (const bounce of visibleBounces) {
      const marker = new THREE.Group()
      const active = bounce.frameIdx === lastVisibleFrame
      const markerColor = active
        ? 0xff5d35
        : bounce.correctionApplied
          ? 0x5fd1ff
          : bounce.insideTable
            ? 0xffd84d
            : 0xa7acb2

      const foot = new THREE.Mesh(
        new THREE.CylinderGeometry(0.062, 0.062, 0.01, 32),
        new THREE.MeshStandardMaterial({
          color: markerColor,
          emissive: markerColor,
          emissiveIntensity: active ? 0.24 : 0.08,
          roughness: 0.42,
        }),
      )
      foot.position.set(bounce.xM, TABLE_THICKNESS_M / 2 + 0.012, bounce.zM)
      marker.add(foot)

      const ball = new THREE.Mesh(
        new THREE.SphereGeometry(active ? 0.06 : 0.048, 28, 18),
        new THREE.MeshStandardMaterial({
          color: markerColor,
          emissive: markerColor,
          emissiveIntensity: active ? 0.32 : 0.1,
          roughness: 0.35,
        }),
      )
      ball.position.set(bounce.xM, TABLE_THICKNESS_M / 2 + 0.105, bounce.zM)
      marker.add(ball)

      const texture = makeLabelTexture(`F${bounce.frameNumber}`, active)
      const label = new THREE.Sprite(new THREE.SpriteMaterial({ map: texture, transparent: true }))
      label.position.set(bounce.xM, TABLE_THICKNESS_M / 2 + 0.27, bounce.zM)
      label.scale.set(0.42, 0.158, 1)
      marker.add(label)

      state.markers.add(marker)
    }
  }, [visibleBounces, snookerBalls, sport])

  return (
    <div className="stream-card table-3d-card">
      <div className="stream-header table-3d-header">
        <span className="stream-title">Trajectory Understanding</span>
        <div className="table-3d-controls">
          {sport !== 'snooker' && (
            <button
              type="button"
              className={`table-3d-toggle${applyPoseCorrections ? ' is-active' : ''}`}
              aria-pressed={applyPoseCorrections}
              onClick={() => setApplyPoseCorrections((value) => !value)}
            >
              Apply Pose Corrections
            </button>
          )}
          <span className="table-3d-status">
            {currentFrameNumber !== undefined ? `Frame ${currentFrameNumber}` : 'Frame N/A'}
          </span>
        </div>
      </div>
      <div className="table-3d-viewer" ref={containerRef} />
      <div className="surface-pose-footer">
        {sport === 'snooker' ? (
          <>
            <span>{`Balls ${snookerBalls.length}`}</span>
            <span>Mode Snooker</span>
          </>
        ) : (
          <>
            <span>{`Bounces ${visibleBounces.length}/${bounces.length}`}</span>
            <span>{visibleBounces.length ? `Latest F${visibleBounces.at(-1)?.frameNumber}` : 'Latest N/A'}</span>
          </>
        )}
      </div>
    </div>
  )
}

export default PongtownTable3D
