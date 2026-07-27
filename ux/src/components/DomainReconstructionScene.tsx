import { useEffect, useMemo, useRef, useState } from 'react'
import { Boxes, CircleDot, LoaderCircle, Triangle } from 'lucide-react'
import * as THREE from 'three'
import { OrbitControls } from 'three/examples/jsm/controls/OrbitControls.js'
import type {
  DomainReconstruction,
  DomainReconstructionFrame,
  DomainReconstructionPoint,
} from '../types'
import { compactNumber, shortPath } from '../lib/format'

type DomainReconstructionSceneProps = {
  sourcePath: string
  currentFrameIndex: number
  getDomainReconstruction: (filePath: string) => Promise<DomainReconstruction | null>
}

type SceneHandles = {
  camera: THREE.PerspectiveCamera
  renderer: THREE.WebGLRenderer
  controls: OrbitControls
  staticGroup: THREE.Group
  dynamicGroup: THREE.Group
  resizeObserver: ResizeObserver
  animationId: number
}

const BALL_COLORS: Record<string, number> = {
  red: 0xdb4252,
  yellow: 0xf4d35e,
  green: 0x55b978,
  brown: 0x8b5a2b,
  blue: 0x3d77d3,
  pink: 0xf08bae,
  black: 0x18191d,
  white: 0xf4f1e8,
  orange: 0xf2a65a,
}

function pointPosition(point: DomainReconstructionPoint, lift = 0.03): THREE.Vector3 {
  return new THREE.Vector3(
    point.xMm / 1000,
    Math.max(0, point.zMm / 1000) + lift,
    -point.yMm / 1000,
  )
}

function ballColor(label: string): number {
  const normalized = label.toLowerCase()
  const entry = Object.entries(BALL_COLORS).find(([name]) => normalized.includes(name))
  return entry?.[1] ?? 0xf4f1e8
}

function ballsOnTable(
  data: DomainReconstruction,
  balls: DomainReconstructionPoint[],
): DomainReconstructionPoint[] {
  const halfLengthMm = data.tableWidthMm / 2
  const halfWidthMm = data.tableHeightMm / 2
  if (data.sportMode === 'PINGPONG') {
    return balls.filter((ball) => (
      ball.insideTable
      && Math.abs(ball.xMm) <= halfLengthMm + 40
      && Math.abs(ball.yMm) <= halfWidthMm + 40
    ))
  }

  const radiusMm = 27
  const snapMarginMm = 120
  return balls
    .map((ball) => {
      const outsideX = Math.max(0, Math.abs(ball.xMm) - halfLengthMm)
      const outsideY = Math.max(0, Math.abs(ball.yMm) - halfWidthMm)
      if (Math.hypot(outsideX, outsideY) > snapMarginMm) return null
      return {
        ...ball,
        xMm: THREE.MathUtils.clamp(
          ball.xMm,
          -Math.max(0, halfLengthMm - radiusMm),
          Math.max(0, halfLengthMm - radiusMm),
        ),
        yMm: THREE.MathUtils.clamp(
          ball.yMm,
          -Math.max(0, halfWidthMm - radiusMm),
          Math.max(0, halfWidthMm - radiusMm),
        ),
      }
    })
    .filter((ball): ball is DomainReconstructionPoint => ball !== null)
}

function disposeObject3D(object: THREE.Object3D) {
  object.traverse((child) => {
    const mesh = child as THREE.Mesh
    mesh.geometry?.dispose?.()
    const material = mesh.material
    if (Array.isArray(material)) material.forEach((item) => item.dispose())
    else material?.dispose?.()
  })
}

function addRail(
  group: THREE.Group,
  width: number,
  height: number,
  depth: number,
  x: number,
  y: number,
  z: number,
  color: number,
) {
  const rail = new THREE.Mesh(
    new THREE.BoxGeometry(width, height, depth),
    new THREE.MeshStandardMaterial({ color, roughness: 0.72, metalness: 0.06 }),
  )
  rail.position.set(x, y, z)
  group.add(rail)
}

function addTable(group: THREE.Group, data: DomainReconstruction) {
  const length = data.tableWidthMm / 1000
  const width = data.tableHeightMm / 1000
  const snooker = data.sportMode === 'SNOOKER'
  const surfaceColor = snooker ? 0x176a4b : 0x174f87
  const railColor = snooker ? 0x41291c : 0x192631
  const surface = new THREE.Mesh(
    new THREE.BoxGeometry(length, 0.065, width),
    new THREE.MeshStandardMaterial({
      color: surfaceColor,
      roughness: 0.82,
      metalness: 0,
    }),
  )
  surface.position.y = -0.033
  group.add(surface)

  const edge = 0.075
  addRail(group, length + edge * 2, 0.09, edge, 0, 0.012, width / 2 + edge / 2, railColor)
  addRail(group, length + edge * 2, 0.09, edge, 0, 0.012, -width / 2 - edge / 2, railColor)
  addRail(group, edge, 0.09, width, length / 2 + edge / 2, 0.012, 0, railColor)
  addRail(group, edge, 0.09, width, -length / 2 - edge / 2, 0.012, 0, railColor)

  const outline = new THREE.LineSegments(
    new THREE.EdgesGeometry(new THREE.BoxGeometry(length, 0.066, width)),
    new THREE.LineBasicMaterial({ color: 0xdce8ee, transparent: true, opacity: 0.4 }),
  )
  outline.position.y = -0.032
  group.add(outline)

  if (data.hasNet) {
    const netHeight = Math.max(0.12, data.netHeightMm / 1000)
    const netSpan = width + (2 * data.netOverhangMm) / 1000
    const net = new THREE.Mesh(
      new THREE.PlaneGeometry(netSpan, netHeight, 20, 4),
      new THREE.MeshBasicMaterial({
        color: 0xe8eef2,
        transparent: true,
        opacity: 0.45,
        side: THREE.DoubleSide,
        wireframe: true,
      }),
    )
    net.rotation.y = Math.PI / 2
    net.position.y = netHeight / 2
    group.add(net)

    const centerLine = new THREE.Line(
      new THREE.BufferGeometry().setFromPoints([
        new THREE.Vector3(0, 0.004, -width / 2),
        new THREE.Vector3(0, 0.004, width / 2),
      ]),
      new THREE.LineBasicMaterial({ color: 0xe8eef2, transparent: true, opacity: 0.55 }),
    )
    group.add(centerLine)
  }

  const pockets = data.pockets.length
    ? data.pockets
    : snooker
      ? [
          { xMm: -data.tableWidthMm / 2, yMm: -data.tableHeightMm / 2, kind: 'CORNER' },
          { xMm: 0, yMm: -data.tableHeightMm / 2, kind: 'MIDDLE' },
          { xMm: data.tableWidthMm / 2, yMm: -data.tableHeightMm / 2, kind: 'CORNER' },
          { xMm: -data.tableWidthMm / 2, yMm: data.tableHeightMm / 2, kind: 'CORNER' },
          { xMm: 0, yMm: data.tableHeightMm / 2, kind: 'MIDDLE' },
          { xMm: data.tableWidthMm / 2, yMm: data.tableHeightMm / 2, kind: 'CORNER' },
        ]
      : []
  for (const pocket of pockets) {
    const marker = new THREE.Mesh(
      new THREE.CircleGeometry(pocket.kind === 'MIDDLE' ? 0.052 : 0.058, 24),
      new THREE.MeshBasicMaterial({ color: 0x08090b, side: THREE.DoubleSide }),
    )
    marker.rotation.x = -Math.PI / 2
    marker.position.set(pocket.xMm / 1000, 0.004, -pocket.yMm / 1000)
    group.add(marker)
  }
}

function addTrajectory(
  group: THREE.Group,
  data: DomainReconstruction,
  trajectory: DomainReconstructionPoint[],
) {
  const points = trajectory
    .filter((point) => (
      point.insideTable
      && Number.isFinite(point.xMm)
      && Number.isFinite(point.yMm)
      && Math.abs(point.xMm) <= data.tableWidthMm / 2 + 40
      && Math.abs(point.yMm) <= data.tableHeightMm / 2 + 40
    ))
    .map((point) => pointPosition(point, 0.024))
  const runs: THREE.Vector3[][] = []
  for (const point of points) {
    const run = runs.at(-1)
    if (!run || run.at(-1)!.distanceTo(point) > 0.72) runs.push([point])
    else run.push(point)
  }
  for (const run of runs) {
    if (run.length < 2) continue
    group.add(
      new THREE.Line(
        new THREE.BufferGeometry().setFromPoints(run),
        new THREE.LineBasicMaterial({
          color: 0xf5c451,
          transparent: true,
          opacity: 0.58,
        }),
      ),
    )
  }
  if (points.length) {
    group.add(
      new THREE.Points(
        new THREE.BufferGeometry().setFromPoints(points),
        new THREE.PointsMaterial({
          color: 0xffd76c,
          size: 0.018,
          sizeAttenuation: true,
          transparent: true,
          opacity: 0.82,
        }),
      ),
    )
  }
}

function addBalls(
  group: THREE.Group,
  data: DomainReconstruction,
  balls: DomainReconstructionPoint[],
) {
  const radius = data.sportMode === 'SNOOKER' ? 0.027 : 0.02
  const geometry = new THREE.SphereGeometry(radius, 24, 18)
  for (const ball of balls) {
    const material = new THREE.MeshStandardMaterial({
      color: ballColor(ball.label),
      emissive: ballColor(ball.label),
      emissiveIntensity: data.sportMode === 'PINGPONG' ? 0.2 : 0.06,
      roughness: 0.28,
      metalness: 0.04,
    })
    const mesh = new THREE.Mesh(geometry, material)
    mesh.position.copy(pointPosition(ball, radius))
    group.add(mesh)
  }
}

function addBounces(group: THREE.Group, bounces: DomainReconstructionPoint[]) {
  for (const bounce of bounces) {
    const ring = new THREE.Mesh(
      new THREE.RingGeometry(0.038, 0.068, 28),
      new THREE.MeshBasicMaterial({
        color: bounce.insideTable ? 0xff5f57 : 0xf5c451,
        side: THREE.DoubleSide,
        transparent: true,
        opacity: 0.96,
      }),
    )
    ring.rotation.x = -Math.PI / 2
    ring.position.copy(pointPosition(bounce, 0.006))
    group.add(ring)

    const pin = new THREE.Mesh(
      new THREE.SphereGeometry(0.018, 16, 12),
      new THREE.MeshBasicMaterial({ color: bounce.insideTable ? 0xff5f57 : 0xf5c451 }),
    )
    pin.position.copy(pointPosition(bounce, 0.025))
    group.add(pin)
  }
}

function populateStaticScene(group: THREE.Group, data: DomainReconstruction) {
  addTable(group, data)
}

function populateDynamicScene(
  group: THREE.Group,
  data: DomainReconstruction,
  balls: DomainReconstructionPoint[],
  trajectory: DomainReconstructionPoint[],
  bounces: DomainReconstructionPoint[],
) {
  addTrajectory(group, data, trajectory)
  addBalls(group, data, balls)
  addBounces(group, bounces)
}

function domainFrameAtIndex(
  frames: DomainReconstructionFrame[],
  targetIndex: number,
): DomainReconstructionFrame | null {
  if (frames.length === 0) return null
  const target = Math.max(0, Math.trunc(targetIndex))
  if (target <= frames[0].frameIndex) return frames[0]
  if (target >= frames[frames.length - 1].frameIndex) return frames[frames.length - 1]
  let low = 0
  let high = frames.length - 1
  while (low < high) {
    const middle = (low + high + 1) >> 1
    if (frames[middle].frameIndex <= target) low = middle
    else high = middle - 1
  }
  return frames[low]
}

function fitCamera(handles: SceneHandles, data: DomainReconstruction | null) {
  const length = Math.max(1, (data?.tableWidthMm ?? 2800) / 1000)
  const width = Math.max(0.6, (data?.tableHeightMm ?? 1500) / 1000)
  const radius = Math.max(length, width)
  handles.camera.position.set(radius * 0.72, radius * 0.72, radius * 0.82)
  handles.camera.near = 0.01
  handles.camera.far = 100
  handles.camera.updateProjectionMatrix()
  handles.controls.target.set(0, 0, 0)
  handles.controls.minDistance = 0.35
  handles.controls.maxDistance = 25
  handles.controls.update()
}

export default function DomainReconstructionScene({
  sourcePath,
  currentFrameIndex,
  getDomainReconstruction,
}: DomainReconstructionSceneProps) {
  const containerRef = useRef<HTMLDivElement>(null)
  const handlesRef = useRef<SceneHandles | null>(null)
  const [data, setData] = useState<DomainReconstruction | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    setError('')
    getDomainReconstruction(sourcePath)
      .then((result) => {
        if (!cancelled) setData(result)
      })
      .catch((reason) => {
        if (!cancelled) {
          setData(null)
          setError(reason instanceof Error ? reason.message : 'Could not read domain reconstruction.')
        }
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [getDomainReconstruction, sourcePath])

  useEffect(() => {
    const container = containerRef.current
    if (!container) return
    const scene = new THREE.Scene()
    scene.background = new THREE.Color(0x0d1114)
    scene.fog = new THREE.Fog(0x0d1114, 8, 24)

    const camera = new THREE.PerspectiveCamera(48, 1, 0.01, 100)
    const renderer = new THREE.WebGLRenderer({ antialias: true })
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2))
    renderer.setSize(Math.max(1, container.clientWidth), Math.max(1, container.clientHeight))
    renderer.outputColorSpace = THREE.SRGBColorSpace
    container.appendChild(renderer.domElement)

    const controls = new OrbitControls(camera, renderer.domElement)
    controls.enableDamping = true
    controls.dampingFactor = 0.075

    const ambient = new THREE.HemisphereLight(0xf5f8ff, 0x172127, 1.15)
    const key = new THREE.DirectionalLight(0xffffff, 1.7)
    key.position.set(4, 7, 5)
    scene.add(ambient, key)

    const floor = new THREE.GridHelper(10, 20, 0x2f4854, 0x1d2a30)
    floor.position.y = -0.09
    scene.add(floor)

    const staticGroup = new THREE.Group()
    const dynamicGroup = new THREE.Group()
    scene.add(staticGroup, dynamicGroup)
    const resizeObserver = new ResizeObserver(() => {
      const width = Math.max(1, container.clientWidth)
      const height = Math.max(1, container.clientHeight)
      renderer.setSize(width, height)
      camera.aspect = width / height
      camera.updateProjectionMatrix()
    })
    resizeObserver.observe(container)

    const render = () => {
      controls.update()
      renderer.render(scene, camera)
      if (handlesRef.current) {
        handlesRef.current.animationId = window.requestAnimationFrame(render)
      }
    }
    handlesRef.current = {
      camera,
      renderer,
      controls,
      staticGroup,
      dynamicGroup,
      resizeObserver,
      animationId: window.requestAnimationFrame(render),
    }
    fitCamera(handlesRef.current, null)

    return () => {
      const handles = handlesRef.current
      if (!handles) return
      window.cancelAnimationFrame(handles.animationId)
      handles.resizeObserver.disconnect()
      handles.controls.dispose()
      disposeObject3D(handles.staticGroup)
      disposeObject3D(handles.dynamicGroup)
      handles.renderer.dispose()
      handles.renderer.domElement.remove()
      handlesRef.current = null
    }
  }, [])

  useEffect(() => {
    const handles = handlesRef.current
    if (!handles) return
    disposeObject3D(handles.staticGroup)
    handles.staticGroup.clear()
    if (data) populateStaticScene(handles.staticGroup, data)
    fitCamera(handles, data)
  }, [data])

  const activeFrame = useMemo(
    () => data ? domainFrameAtIndex(data.frames, currentFrameIndex) : null,
    [currentFrameIndex, data],
  )
  const visibleTrajectory = useMemo(() => {
    if (!data || data.sportMode !== 'PINGPONG') return []
    return data.trajectory.filter((point) => point.frameIndex <= currentFrameIndex)
  }, [currentFrameIndex, data])
  const visibleBounces = useMemo(() => {
    if (!data || data.sportMode !== 'PINGPONG') return []
    return data.bounces.filter((point) => point.frameIndex <= currentFrameIndex)
  }, [currentFrameIndex, data])
  const visibleBalls = useMemo(() => {
    if (!data) return []
    if (activeFrame) {
      if (activeFrame.balls.length > 0 || data.sportMode === 'SNOOKER') {
        return activeFrame.balls
      }
    }
    if (data.sportMode === 'PINGPONG') {
      const latest = visibleTrajectory.at(-1)
      return latest ? [latest] : []
    }
    return data.balls
  }, [activeFrame, data, visibleTrajectory])
  const renderedBalls = useMemo(
    () => data ? ballsOnTable(data, visibleBalls) : [],
    [data, visibleBalls],
  )

  useEffect(() => {
    const handles = handlesRef.current
    if (!handles) return
    disposeObject3D(handles.dynamicGroup)
    handles.dynamicGroup.clear()
    if (data) {
      populateDynamicScene(
        handles.dynamicGroup,
        data,
        renderedBalls,
        visibleTrajectory,
        visibleBounces,
      )
    }
  }, [data, renderedBalls, visibleBounces, visibleTrajectory])

  const tableSize = useMemo(() => (
    data ? `${(data.tableWidthMm / 1000).toFixed(2)} × ${(data.tableHeightMm / 1000).toFixed(2)} m` : '—'
  ), [data])
  const displayedFrameIndex = activeFrame?.frameIndex ?? currentFrameIndex
  const displayedFrameNumber = activeFrame?.frameNumber ?? data?.snapshotFrameNumber ?? 0

  return (
    <div
      className="domain-reconstruction"
      data-frame-index={displayedFrameIndex}
      data-frame-number={displayedFrameNumber}
      data-ball-count={renderedBalls.length}
    >
      <div className="domain-reconstruction-canvas" ref={containerRef} />
      <div className="domain-reconstruction-header">
        <span className="eyebrow">Canonical table space</span>
        <strong>{data?.sportMode === 'PINGPONG' ? 'Table tennis reconstruction' : 'Snooker reconstruction'}</strong>
        <small title={sourcePath}>{shortPath(sourcePath, 72)}</small>
      </div>
      {loading ? (
        <div className="domain-reconstruction-state">
          <LoaderCircle className="spin" size={18} aria-hidden="true" />
          <span>Reading domain geometry…</span>
        </div>
      ) : null}
      {!loading && (error || !data) ? (
        <div className="domain-reconstruction-state is-error">
          <Triangle size={18} aria-hidden="true" />
          <span>{error || 'This reconstruction requires the native desktop app.'}</span>
        </div>
      ) : null}
      {data ? (
        <>
          <div className="domain-reconstruction-metrics">
            <div><Triangle size={13} /><span>Table</span><strong>{tableSize}</strong></div>
            <div>
              <CircleDot size={13} />
              <span>{data.sportMode === 'PINGPONG' ? 'Bounces' : 'Balls'}</span>
              <strong>
                {data.sportMode === 'PINGPONG'
                  ? `${compactNumber(visibleBounces.length)} / ${compactNumber(data.bounces.length)}`
                  : compactNumber(renderedBalls.length)}
              </strong>
            </div>
            <div>
              <Boxes size={13} />
              <span>Frame</span>
              <strong>{compactNumber(displayedFrameIndex + 1)} / {compactNumber(data.frameCount)}</strong>
            </div>
          </div>
          <div className="domain-reconstruction-legend">
            <span><i className="is-surface" />Table geometry</span>
            {data.sportMode === 'PINGPONG' ? (
              <>
                <span><i className="is-trajectory" />Ball trajectory</span>
                <span><i className="is-bounce" />Bounce location</span>
              </>
            ) : (
              <span><i className="is-ball" />Live detected balls · source frame {displayedFrameNumber}</span>
            )}
          </div>
        </>
      ) : null}
    </div>
  )
}
