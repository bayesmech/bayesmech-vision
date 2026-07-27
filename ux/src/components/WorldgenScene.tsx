import {
  MouseEvent,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type Dispatch,
  type KeyboardEvent,
  type SetStateAction,
} from 'react'
import { RotateCcw, ZoomIn, ZoomOut } from 'lucide-react'
import * as THREE from 'three'
import { TrackballControls } from 'three/examples/jsm/controls/TrackballControls.js'
import type {
  VideoPlaybackState,
  VisFrame,
  WorldgenCamera,
  WorldgenPoint,
  WorldgenResult,
  WorldgenSplatPoint,
} from '../types'
import { compactNumber, shortPath } from '../lib/format'
import { useFrameSource } from '../lib/frameSource'
import {
  createSplatRenderer,
  type SplatHandle,
  type SplatRenderJobState,
} from '../lib/splatRenderer'

type WorldgenSceneProps = {
  result: WorldgenResult | null
  currentFrameIndex: number
  onVideoStateChange: Dispatch<SetStateAction<VideoPlaybackState>>
}

type SceneHandles = {
  camera: THREE.PerspectiveCamera
  renderer: THREE.WebGLRenderer
  controls: TrackballControls
  dataGroup: THREE.Group
  pointCloudGroup: THREE.Group
  cameraGroup: THREE.Group
  resizeObserver: ResizeObserver
  animationId: number
  splat: SplatHandle | null
}

type WorldgenViewMode = 'splat' | 'vggt'

type RenderedPointCloud = {
  resultId: string
  allFrames: boolean
  frameIndex: number | null
  pointCount: number
}

function pointKey(point: Pick<WorldgenPoint, 'frameIndex' | 'framePointIndex'>): string {
  return `${point.frameIndex}:${point.framePointIndex}`
}

function cameraPoint(camera: WorldgenCamera): THREE.Vector3 {
  return new THREE.Vector3(camera.x, camera.y, camera.z)
}

function transformCameraLocal(camera: WorldgenCamera, x: number, y: number, z: number): THREE.Vector3 {
  const m = camera.matrix
  if (m.length < 16) return cameraPoint(camera).add(new THREE.Vector3(x, y, z))
  return new THREE.Vector3(
    m[0] * x + m[1] * y + m[2] * z + m[3],
    m[4] * x + m[5] * y + m[6] * z + m[7],
    m[8] * x + m[9] * y + m[10] * z + m[11],
  )
}

type ScenePoint = Pick<WorldgenPoint | WorldgenSplatPoint, 'x' | 'y' | 'z'>

function finiteScenePoints(points: ScenePoint[], maxSamples = 12_000): THREE.Vector3[] {
  // Camera fitting only needs a representative robust box. Sampling keeps this
  // UI-side calculation bounded even when the splat preview has hundreds of
  // thousands of Gaussians; the full render data stays untouched in the worker.
  const stride = Math.max(1, Math.ceil(points.length / maxSamples))
  const finite: THREE.Vector3[] = []
  for (let index = 0; index < points.length; index += stride) {
    const point = points[index]
    if (Number.isFinite(point.x) && Number.isFinite(point.y) && Number.isFinite(point.z)) {
      finite.push(new THREE.Vector3(point.x, point.y, point.z))
    }
  }
  return finite
}

function quantile(values: number[], amount: number): number {
  if (!values.length) return 0
  const index = Math.max(0, Math.min(values.length - 1, Math.floor((values.length - 1) * amount)))
  return values[index]
}

function robustPointBox(points: ScenePoint[], low = 0.01, high = 0.99): THREE.Box3 | null {
  const finite = finiteScenePoints(points)
  if (!finite.length) return null
  if (finite.length < 64) return new THREE.Box3().setFromPoints(finite)

  const xs = finite.map((point) => point.x).sort((a, b) => a - b)
  const ys = finite.map((point) => point.y).sort((a, b) => a - b)
  const zs = finite.map((point) => point.z).sort((a, b) => a - b)
  const min = new THREE.Vector3(quantile(xs, low), quantile(ys, low), quantile(zs, low))
  const max = new THREE.Vector3(quantile(xs, high), quantile(ys, high), quantile(zs, high))
  const box = new THREE.Box3(min, max)
  return box.isEmpty() ? new THREE.Box3().setFromPoints(finite) : box
}

function activeFitBox(result: WorldgenResult, viewMode: WorldgenViewMode): THREE.Box3 | null {
  const boxes: THREE.Box3[] = []
  if (viewMode === 'vggt' && result.points.length) {
    const box = robustPointBox(result.points, 0.01, 0.99)
    if (box) boxes.push(box)
  }
  if (viewMode === 'splat' && result.splatPoints.length) {
    const box = robustPointBox(result.splatPoints, 0.10, 0.90)
    if (box) boxes.push(box)
  }
  if (!boxes.length && result.cameras.length) {
    boxes.push(new THREE.Box3().setFromPoints(result.cameras.map(cameraPoint)))
  }
  if (!boxes.length) return null
  const out = boxes[0].clone()
  for (const box of boxes.slice(1)) out.union(box)
  return out
}

function fitCamera(handles: SceneHandles, result: WorldgenResult | null, viewMode: WorldgenViewMode) {
  const { camera, controls } = handles
  camera.up.set(0, 1, 0)
  if (!result || (result.points.length === 0 && result.splatPoints.length === 0)) {
    camera.position.set(2.8, 2.0, 3.4)
    controls.target.set(0, 0, 0)
    controls.minDistance = 0.02
    controls.maxDistance = 200
    controls.update()
    return
  }

  const box = activeFitBox(result, viewMode)
  if (!box) return

  const center = new THREE.Vector3()
  const size = new THREE.Vector3()
  box.getCenter(center)
  box.getSize(size)
  const radius = Math.max(size.length() * 0.72, 0.65)

  camera.position.copy(center).add(new THREE.Vector3(radius * 1.06, radius * 0.62, radius * 1.18))
  camera.near = Math.max(0.01, radius / 240)
  camera.far = Math.max(100, radius * 80)
  camera.updateProjectionMatrix()
  controls.target.copy(center)
  controls.minDistance = Math.max(0.01, radius * 0.05)
  controls.maxDistance = Math.max(50, radius * 80)
  controls.update()
}

// Render Gaussians as true (anisotropic-capable) EWA splats. The returned handle
// observes camera changes while a worker prepares the next depth-sorted frame.
function addSplatPoints(
  group: THREE.Group,
  points: WorldgenSplatPoint[],
  onJobStateChange: (state: SplatRenderJobState) => void,
): SplatHandle | null {
  const handle = createSplatRenderer(points, 1, onJobStateChange)
  if (handle) group.add(handle.object)
  return handle
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

function pointsByFrame(points: WorldgenPoint[]): Map<number, WorldgenPoint[]> {
  const grouped = new Map<number, WorldgenPoint[]>()
  for (const point of points) {
    const current = grouped.get(point.frameIndex) ?? []
    current.push(point)
    grouped.set(point.frameIndex, current)
  }
  return grouped
}

function addWorldgenPoints(
  group: THREE.Group,
  grouped: Map<number, WorldgenPoint[]>,
  selectedFrameIndex: number | null,
  showAllFrames: boolean,
  highlightedKey: string | null,
  opacityScale = 1,
) {
  for (const [frameIndex, framePoints] of grouped.entries()) {
    if (!showAllFrames && selectedFrameIndex !== frameIndex) continue
    const positions: number[] = []
    const colors: number[] = []

    for (const point of framePoints) {
      const highlighted = highlightedKey === pointKey(point)
      positions.push(point.x, point.y, point.z)
      if (highlighted) colors.push(1, 0.92, 0.25)
      else colors.push(point.r, point.g, point.b)
    }

    const geometry = new THREE.BufferGeometry()
    geometry.setAttribute('position', new THREE.Float32BufferAttribute(positions, 3))
    geometry.setAttribute('color', new THREE.Float32BufferAttribute(colors, 3))
    group.add(
      new THREE.Points(
        geometry,
        new THREE.PointsMaterial({
          size: showAllFrames ? 0.014 : 0.022,
          vertexColors: true,
          sizeAttenuation: true,
          transparent: true,
          opacity: (showAllFrames ? 0.38 : 1) * opacityScale,
          depthWrite: false,
        }),
      ),
    )
  }

  const separator = highlightedKey?.lastIndexOf(':') ?? -1
  const highlightedFrameIndex = separator >= 0 ? Number(highlightedKey?.slice(0, separator)) : Number.NaN
  const highlighted = Number.isFinite(highlightedFrameIndex)
    ? grouped.get(highlightedFrameIndex)?.find((point) => highlightedKey === pointKey(point))
    : undefined
  if (highlighted) {
    const marker = new THREE.Mesh(
      new THREE.SphereGeometry(0.055, 18, 18),
      new THREE.MeshBasicMaterial({ color: 0xffeb3b }),
    )
    marker.position.set(highlighted.x, highlighted.y, highlighted.z)
    group.add(marker)
  }
}

function addCameras(group: THREE.Group, result: WorldgenResult, selectedFrameIndex: number | null) {
  const cameraPoints = result.cameras.map(cameraPoint)
  if (cameraPoints.length > 1) {
    group.add(
      new THREE.Line(
        new THREE.BufferGeometry().setFromPoints(cameraPoints),
        new THREE.LineBasicMaterial({ color: 0xf0b35a, transparent: true, opacity: 0.95 }),
      ),
    )
  }

  const stride = Math.max(1, Math.ceil(result.cameras.length / 80))
  for (let index = 0; index < result.cameras.length; index += stride) {
    const camera = result.cameras[index]
    const selected = selectedFrameIndex === camera.frameIndex
    const depth = selected ? 0.22 : 0.15
    const halfWidth = depth * 0.65
    const halfHeight = depth * 0.42
    const origin = cameraPoint(camera)
    const corners = [
      transformCameraLocal(camera, -halfWidth, -halfHeight, depth),
      transformCameraLocal(camera, halfWidth, -halfHeight, depth),
      transformCameraLocal(camera, halfWidth, halfHeight, depth),
      transformCameraLocal(camera, -halfWidth, halfHeight, depth),
    ]
    const linePoints = [
      origin, corners[0], origin, corners[1], origin, corners[2], origin, corners[3],
      corners[0], corners[1], corners[1], corners[2], corners[2], corners[3], corners[3], corners[0],
    ]
    group.add(
      new THREE.LineSegments(
        new THREE.BufferGeometry().setFromPoints(linePoints),
        new THREE.LineBasicMaterial({
          color: selected ? 0xffeb3b : 0x62d2a2,
          transparent: true,
          opacity: selected ? 1 : 0.56,
        }),
      ),
    )
  }
}

export default function WorldgenScene({ result, currentFrameIndex, onVideoStateChange }: WorldgenSceneProps) {
  const getFrame = useFrameSource()
  const containerRef = useRef<HTMLDivElement>(null)
  const handlesRef = useRef<SceneHandles | null>(null)
  const fitKeyRef = useRef('')
  const initializedResultRef = useRef<string | null>(null)
  const imageCanvasRef = useRef<HTMLCanvasElement>(null)
  const imageRef = useRef<HTMLImageElement | null>(null)
  const [showAllFrames, setShowAllFrames] = useState(false)
  const [viewMode, setViewMode] = useState<WorldgenViewMode>('vggt')
  const [splatJob, setSplatJob] = useState<SplatRenderJobState | null>(null)
  const [highlightedKey, setHighlightedKey] = useState<string | null>(null)
  const [frameImage, setFrameImage] = useState<VisFrame | null>(null)
  const [imageTick, setImageTick] = useState(0)
  const [renderedPointCloud, setRenderedPointCloud] = useState<RenderedPointCloud | null>(null)

  const selectedFrame = useMemo(() => {
    if (!result?.frames.length) return null
    return result.frames.find((frame) => frame.frameIndex === currentFrameIndex) ?? null
  }, [currentFrameIndex, result])
  const selectedFrameIndex = selectedFrame?.frameIndex ?? null

  const groupedPoints = useMemo(() => pointsByFrame(result?.points ?? []), [result?.points])
  const selectedPoints = selectedFrameIndex == null ? [] : groupedPoints.get(selectedFrameIndex) ?? []
  const renderedFrameIndex = showAllFrames ? null : selectedFrameIndex

  useEffect(() => {
    setViewMode(result?.splat?.status === 'complete' && result.splatPoints.length ? 'splat' : 'vggt')
    setShowAllFrames(Boolean(result?.frames.length))
    setSplatJob(null)
    setHighlightedKey(null)
    fitKeyRef.current = ''
  }, [result?.id, result?.splat?.status, result?.splatPoints.length])

  useEffect(() => {
    if (!result) {
      initializedResultRef.current = null
      return
    }
    if (!result.frames.length || initializedResultRef.current === result.id) return
    initializedResultRef.current = result.id
    if (result.frames.some((frame) => frame.frameIndex === currentFrameIndex)) return

    const nearestFrame = result.frames.reduce((nearest, frame) => (
      Math.abs(frame.frameIndex - currentFrameIndex) < Math.abs(nearest.frameIndex - currentFrameIndex)
        ? frame
        : nearest
    ))
    onVideoStateChange((current) => ({
      ...current,
      index: nearestFrame.frameIndex,
      playing: false,
    }))
  }, [currentFrameIndex, onVideoStateChange, result])

  useEffect(() => {
    if (!getFrame || selectedFrameIndex == null) {
      setFrameImage(null)
      return
    }
    let cancelled = false
    getFrame(selectedFrameIndex)
      .then((frame) => {
        if (!cancelled) setFrameImage(frame)
      })
      .catch(() => {
        if (!cancelled) setFrameImage(null)
      })
    return () => {
      cancelled = true
    }
  }, [getFrame, selectedFrameIndex])

  useEffect(() => {
    if (!frameImage?.dataUrl) {
      imageRef.current = null
      setImageTick((tick) => tick + 1)
      return
    }
    const image = new Image()
    let active = true
    image.onload = () => {
      if (!active) return
      imageRef.current = image
      setImageTick((tick) => tick + 1)
    }
    image.src = frameImage.dataUrl
    return () => {
      active = false
    }
  }, [frameImage?.dataUrl])

  useEffect(() => {
    const canvas = imageCanvasRef.current
    if (!canvas || !selectedFrame) return
    const ctx = canvas.getContext('2d')
    if (!ctx) return
    const image = imageRef.current
    const width = image?.naturalWidth || frameImage?.width || selectedFrame.imageWidth || 480
    const height = image?.naturalHeight || frameImage?.height || selectedFrame.imageHeight || 270
    if (canvas.width !== width) canvas.width = width
    if (canvas.height !== height) canvas.height = height
    ctx.clearRect(0, 0, width, height)
    ctx.fillStyle = '#101114'
    ctx.fillRect(0, 0, width, height)
    if (image) ctx.drawImage(image, 0, 0, width, height)

    const sourceWidth = selectedFrame.imageWidth || width
    const sourceHeight = selectedFrame.imageHeight || height
    for (const point of selectedPoints) {
      const x = (point.u / sourceWidth) * width
      const y = (point.v / sourceHeight) * height
      const highlighted = highlightedKey === pointKey(point)
      ctx.beginPath()
      ctx.arc(x, y, highlighted ? 5.5 : 2.2, 0, Math.PI * 2)
      ctx.fillStyle = highlighted ? '#ffeb3b' : `rgba(${Math.round(point.r * 255)}, ${Math.round(point.g * 255)}, ${Math.round(point.b * 255)}, 0.88)`
      ctx.fill()
      if (highlighted) {
        ctx.lineWidth = 2
        ctx.strokeStyle = '#101114'
        ctx.stroke()
      }
    }
  }, [frameImage?.height, frameImage?.width, highlightedKey, imageTick, selectedFrame, selectedPoints])

  const handleImageClick = useCallback(
    (event: MouseEvent<HTMLCanvasElement>) => {
      const canvas = imageCanvasRef.current
      if (!canvas || !selectedFrame || selectedPoints.length === 0) return
      const rect = canvas.getBoundingClientRect()
      const x = (event.clientX - rect.left) * (canvas.width / rect.width)
      const y = (event.clientY - rect.top) * (canvas.height / rect.height)
      const sourceWidth = selectedFrame.imageWidth || canvas.width
      const sourceHeight = selectedFrame.imageHeight || canvas.height
      const u = (x / canvas.width) * sourceWidth
      const v = (y / canvas.height) * sourceHeight

      let nearest: WorldgenPoint | null = null
      let nearestDistance = Number.POSITIVE_INFINITY
      for (const point of selectedPoints) {
        const dx = point.u - u
        const dy = point.v - v
        const distance = dx * dx + dy * dy
        if (distance < nearestDistance) {
          nearestDistance = distance
          nearest = point
        }
      }
      if (nearest) setHighlightedKey(pointKey(nearest))
      if (nearest && viewMode === 'splat') setViewMode('vggt')
    },
    [selectedFrame, selectedPoints, viewMode],
  )

  const resetView = useCallback(() => {
    const handles = handlesRef.current
    if (!handles) return
    fitCamera(handles, result, viewMode)
  }, [result, viewMode])

  const zoomView = useCallback((factor: number) => {
    const handles = handlesRef.current
    if (!handles) return
    const { camera, controls } = handles
    const eye = new THREE.Vector3().subVectors(camera.position, controls.target)
    const distance = Math.max(controls.minDistance || 0.01, Math.min(controls.maxDistance || 1000, eye.length() * factor))
    if (!Number.isFinite(distance) || distance <= 0) return
    eye.setLength(distance)
    camera.position.copy(controls.target).add(eye)
    camera.updateProjectionMatrix()
    controls.update()
  }, [])

  const navigateView = useCallback((event: KeyboardEvent<HTMLDivElement>) => {
    const key = event.key.toLowerCase()
    const movementKey = key === 'w' || key === 'a' || key === 's' || key === 'd'
    const lookKey = event.key === 'ArrowLeft' || event.key === 'ArrowRight' || event.key === 'ArrowUp' || event.key === 'ArrowDown'
    if (!movementKey && !lookKey) return
    const handles = handlesRef.current
    if (!handles) return

    event.preventDefault()
    event.stopPropagation()
    const { camera, controls } = handles
    const worldUp = new THREE.Vector3(0, 1, 0)
    const distance = Math.max(0.1, camera.position.distanceTo(controls.target))

    // TrackballControls owns the pose while the mouse is active. Read the
    // camera's actual post-trackball orientation here so keyboard movement
    // continues from exactly what is on screen, including pitch and roll.
    camera.updateMatrixWorld(true)
    const forward = new THREE.Vector3()
    camera.getWorldDirection(forward).normalize()
    const right = new THREE.Vector3(1, 0, 0).applyQuaternion(camera.quaternion).normalize()

    if (movementKey) {
      if (forward.lengthSq() < 1e-8) forward.set(0, 0, -1)
      const direction = new THREE.Vector3()
      if (key === 'w') direction.copy(forward)
      if (key === 's') direction.copy(forward).multiplyScalar(-1)
      if (key === 'd') direction.copy(right)
      if (key === 'a') direction.copy(right).multiplyScalar(-1)
      const step = Math.max(0.025, Math.min(2, distance * 0.055))
      direction.multiplyScalar(step)
      camera.position.add(direction)
      controls.target.add(direction)
    } else {
      const radians = THREE.MathUtils.degToRad(3.2)
      const nextDirection = forward.multiplyScalar(distance)
      if (event.key === 'ArrowLeft' || event.key === 'ArrowRight') {
        nextDirection.applyAxisAngle(worldUp, event.key === 'ArrowLeft' ? radians : -radians)
      } else {
        if (right.lengthSq() > 1e-8) {
          const candidate = nextDirection.clone().applyAxisAngle(right, event.key === 'ArrowUp' ? radians : -radians)
          if (Math.abs(candidate.clone().normalize().dot(worldUp)) < 0.985) nextDirection.copy(candidate)
        }
      }
      controls.target.copy(camera.position).add(nextDirection)
    }

    // Do not reset camera.up: TrackballControls may have established a rolled
    // pose, and snapping it back to world-up is the jump this path must avoid.
    controls.update()
  }, [])

  useEffect(() => {
    const container = containerRef.current
    if (!container) return

    const scene = new THREE.Scene()
    scene.background = new THREE.Color(0x101114)

    const camera = new THREE.PerspectiveCamera(52, 1, 0.01, 1000)
    camera.position.set(2.8, 2.0, 3.4)

    const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: false })
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2))
    renderer.setSize(container.clientWidth, container.clientHeight)
    container.appendChild(renderer.domElement)

    const controls = new TrackballControls(camera, renderer.domElement)
    controls.noZoom = false
    controls.noPan = false
    controls.noRotate = false
    controls.rotateSpeed = 4.2
    controls.zoomSpeed = 1.35
    controls.panSpeed = 0.72
    controls.dynamicDampingFactor = 0.12
    // The camera should remain where the pointer leaves it. Inertial trackball
    // rotation can otherwise continue into the first WASD event and move the
    // camera a second time from stale mouse deltas.
    controls.staticMoving = true
    controls.keys = ['', '', '']
    controls.target.set(0, 0, 0)

    scene.add(new THREE.AmbientLight(0xffffff, 0.7))
    const key = new THREE.DirectionalLight(0xffffff, 1.1)
    key.position.set(4, 7, 5)
    scene.add(key)

    const grid = new THREE.GridHelper(8, 16, 0x3a3d44, 0x24272d)
    grid.position.y = -0.02
    scene.add(grid)
    scene.add(new THREE.AxesHelper(0.85))

    const dataGroup = new THREE.Group()
    const pointCloudGroup = new THREE.Group()
    const cameraGroup = new THREE.Group()
    dataGroup.add(pointCloudGroup, cameraGroup)
    scene.add(dataGroup)

    const resizeObserver = new ResizeObserver(() => {
      const width = Math.max(1, container.clientWidth)
      const height = Math.max(1, container.clientHeight)
      renderer.setSize(width, height)
      camera.aspect = width / height
      camera.updateProjectionMatrix()
      controls.handleResize()
    })
    resizeObserver.observe(container)

    const viewportPx = new THREE.Vector2()
    const render = () => {
      controls.update()
      const splat = handlesRef.current?.splat
      if (splat) {
        renderer.getDrawingBufferSize(viewportPx)
        splat.update(camera, viewportPx)
        // The two data layers are mutually exclusive. A stale/hidden splat means
        // the worker is still preparing the latest POV, so show the point cloud.
        handlesRef.current!.pointCloudGroup.visible = !splat.object.visible
      }
      renderer.render(scene, camera)
      handlesRef.current!.animationId = window.requestAnimationFrame(render)
    }

    handlesRef.current = {
      camera,
      renderer,
      controls,
      dataGroup,
      pointCloudGroup,
      cameraGroup,
      resizeObserver,
      animationId: window.requestAnimationFrame(render),
      splat: null,
    }
    container.focus({ preventScroll: true })

    return () => {
      const handles = handlesRef.current
      if (!handles) return
      window.cancelAnimationFrame(handles.animationId)
      const splat = handles.splat
      handles.splat = null
      splat?.dispose()
      disposeObject3D(handles.pointCloudGroup)
      disposeObject3D(handles.cameraGroup)
      handles.resizeObserver.disconnect()
      handles.controls.dispose()
      handles.renderer.dispose()
      handles.renderer.domElement.remove()
      handlesRef.current = null
    }
  }, [])

  useEffect(() => {
    const handles = handlesRef.current
    if (!handles) return
    let confirmationFrame = 0
    const renderFrame = window.requestAnimationFrame(() => {
      disposeObject3D(handles.pointCloudGroup)
      handles.pointCloudGroup.clear()
      if (result) {
        addWorldgenPoints(handles.pointCloudGroup, groupedPoints, renderedFrameIndex, showAllFrames, highlightedKey)
      }

      // Confirm on the following animation frame. The WebGL render loop has
      // drawn the replacement geometry by then, so this state describes what
      // is actually on the canvas rather than what the timeline requested.
      confirmationFrame = window.requestAnimationFrame(() => {
        if (handlesRef.current !== handles) return
        setRenderedPointCloud(result
          ? {
              resultId: result.id,
              allFrames: showAllFrames,
              frameIndex: renderedFrameIndex,
              pointCount: showAllFrames
                ? result.points.length
                : groupedPoints.get(renderedFrameIndex ?? -1)?.length ?? 0,
            }
          : null)
      })
    })
    return () => {
      window.cancelAnimationFrame(renderFrame)
      if (confirmationFrame) window.cancelAnimationFrame(confirmationFrame)
    }
  }, [result?.id, groupedPoints, renderedFrameIndex, showAllFrames, highlightedKey])

  useEffect(() => {
    const handles = handlesRef.current
    if (!handles) return
    disposeObject3D(handles.cameraGroup)
    handles.cameraGroup.clear()
    if (result) addCameras(handles.cameraGroup, result, selectedFrameIndex)
  }, [result?.id, result?.cameras, selectedFrameIndex])

  useEffect(() => {
    const handles = handlesRef.current
    if (!handles) return

    const previous = handles.splat
    if (previous) {
      handles.dataGroup.remove(previous.object)
      previous.dispose()
      handles.splat = null
    }
    setSplatJob(null)
    handles.pointCloudGroup.visible = true

    if (!result || viewMode !== 'splat' || !result.splatPoints.length) return

    setSplatJob({ phase: 'preparing', message: 'Preparing Gaussian data in the background' })
    let handle: SplatHandle | null = null
    const onJobStateChange = (state: SplatRenderJobState) => {
      if (handlesRef.current !== handles || handles.splat !== handle) return
      setSplatJob(state)
      handles.pointCloudGroup.visible = state.phase !== 'ready'
    }
    handle = addSplatPoints(handles.dataGroup, result.splatPoints, onJobStateChange)
    handles.splat = handle
    if (!handle) {
      setSplatJob({ phase: 'failed', message: 'No Gaussian splat data is available; showing the point cloud' })
    }

    return () => {
      if (handle && handles.splat === handle) {
        handles.dataGroup.remove(handle.object)
        handle.dispose()
        handles.splat = null
        handles.pointCloudGroup.visible = true
      }
    }
  }, [result?.id, result?.splatPoints, viewMode])

  useEffect(() => {
    const handles = handlesRef.current
    if (!handles) return
    const nextFitKey = `${result?.id ?? 'empty'}:${viewMode}:${result?.splatPoints.length ?? 0}:${result?.points.length ?? 0}`
    if (fitKeyRef.current !== nextFitKey) {
      fitCamera(handles, result, viewMode)
      fitKeyRef.current = nextFitKey
    }
  }, [result, viewMode])

  const renderedPointCloudIsCurrent = Boolean(
    result &&
    renderedPointCloud?.resultId === result.id &&
    renderedPointCloud.allFrames === showAllFrames &&
    (showAllFrames || renderedPointCloud.frameIndex === selectedFrameIndex),
  )
  const splatIsRendered = viewMode === 'splat' && splatJob?.phase === 'ready'
  const renderedPointCloudLabel = renderedPointCloud?.allFrames
    ? `Rendered all frames · ${compactNumber(renderedPointCloud.pointCount)} points`
    : renderedPointCloud?.frameIndex != null
      ? `Rendered frame ${renderedPointCloud.frameIndex + 1} · ${compactNumber(renderedPointCloud.pointCount)} points`
      : 'No VGGT point cloud rendered'
  const requestedPointCloudLabel = showAllFrames
    ? 'Requested all frames'
    : selectedFrame
      ? `Requested frame ${selectedFrame.frameIndex + 1}`
      : `Requested frame ${currentFrameIndex + 1} · no VGGT data`

  const sceneSummary = useMemo(() => {
    if (!result) return 'VGGT reconstruction'
    if (splatIsRendered && result.splat?.status === 'complete') {
      return `${compactNumber(result.splat.gaussianCount)} Gaussians, ${compactNumber(result.splat.previewPointCount)} preview splats`
    }
    if (renderedPointCloud?.resultId === result.id) {
      if (renderedPointCloud.allFrames) {
        return `${compactNumber(renderedPointCloud.pointCount)} rendered VGGT points, all frames`
      }
      if (renderedPointCloud.frameIndex != null) {
        return `Rendered frame ${renderedPointCloud.frameIndex + 1}, ${compactNumber(renderedPointCloud.pointCount)} points`
      }
    }
    return 'Preparing VGGT point cloud'
  }, [renderedPointCloud, result, splatIsRendered])

  const visibleSplatJob = viewMode === 'splat' && splatJob?.phase !== 'ready' ? splatJob : null

  return (
    <div className="worldgen-player" onKeyDown={navigateView}>
      <div className={`scene-shell${visibleSplatJob ? ' has-render-job' : ''}`}>
      <div
        className="scene-canvas"
        ref={containerRef}
        data-testid="worldgen-canvas"
        tabIndex={0}
        onPointerDown={(event) => event.currentTarget.focus({ preventScroll: true })}
        aria-label="World Modeling 3D view. Use W A S D to move and arrow keys to look around."
      />
      <div className="scene-overlay">
        <span>{result ? 'World Modeling' : 'No world model'}</span>
        <span>{sceneSummary}</span>
        {result ? <span title={result.outputPath}>{shortPath(result.outputPath, 76)}</span> : null}
      </div>
      {visibleSplatJob ? (
        <div
          className={`worldgen-background-job ${visibleSplatJob.phase}`}
          role="status"
          aria-live="polite"
        >
          <span className="worldgen-background-job-indicator" aria-hidden="true" />
          <span>
            {visibleSplatJob.phase === 'failed' ? visibleSplatJob.message : `Background job: ${visibleSplatJob.message}`}
          </span>
        </div>
      ) : null}
      <div className="worldgen-view-tools" aria-label="World Modeling view controls">
        <button type="button" onClick={() => zoomView(0.72)} title="Zoom in" aria-label="Zoom in">
          <ZoomIn size={15} aria-hidden="true" />
        </button>
        <button type="button" onClick={() => zoomView(1.38)} title="Zoom out" aria-label="Zoom out">
          <ZoomOut size={15} aria-hidden="true" />
        </button>
        <button type="button" onClick={resetView} title="Reset view" aria-label="Reset view">
          <RotateCcw size={15} aria-hidden="true" />
        </button>
      </div>
      <div className="worldgen-navigation-hint">WASD move · arrows look</div>
      <div
        className={`worldgen-render-state${!splatIsRendered && !renderedPointCloudIsCurrent ? ' is-stale' : ''}`}
        role="status"
        aria-live="polite"
      >
        <span className="worldgen-render-state-dot" aria-hidden="true" />
        <strong>{splatIsRendered ? 'Rendered Gaussian splat · global reconstruction' : renderedPointCloudLabel}</strong>
        {splatIsRendered ? (
          <span>Timeline frame {currentFrameIndex + 1}</span>
        ) : !renderedPointCloudIsCurrent ? (
          <span>{requestedPointCloudLabel} · updating…</span>
        ) : null}
      </div>
      {result && !selectedFrame && !showAllFrames ? (
        <div className="worldgen-frame-unavailable" role="status">
          Frame {currentFrameIndex + 1} has no generated VGGT or Gaussian data
        </div>
      ) : null}

      {result ? (
        <div className="worldgen-image-panel">
          <div className="worldgen-frame-controls">
            {result.splat ? (
              <div className="worldgen-mode-controls" role="group" aria-label="World Modeling display">
                <button
                  type="button"
                  className={viewMode === 'splat' ? 'active' : ''}
                  disabled={!result.splatPoints.length}
                  onClick={() => setViewMode('splat')}
                  title={result.splat.status === 'failed' ? result.splat.error : result.splat.plyPath}
                >
                  Splat
                </button>
                <button
                  type="button"
                  className={viewMode === 'vggt' ? 'active' : ''}
                  onClick={() => setViewMode('vggt')}
                  title="VGGT point cloud"
                >
                  VGGT
                </button>
              </div>
            ) : null}
            <label>
              <input type="checkbox" checked={showAllFrames} onChange={(event) => setShowAllFrames(event.target.checked)} />
              <span>All reconstructed frames ({result.frames.length})</span>
            </label>
            {result.frames.length ? (
              <label>
                <span>Frame</span>
                <select
                  value={selectedFrameIndex ?? ''}
                  onChange={(event) => {
                    const frameIndex = Number(event.target.value)
                    if (!Number.isFinite(frameIndex)) return
                    setShowAllFrames(false)
                    onVideoStateChange((current) => ({
                      ...current,
                      index: frameIndex,
                      playing: false,
                    }))
                  }}
                  aria-label="Reconstructed frame"
                >
                  {!selectedFrame ? <option value="">Choose reconstructed frame</option> : null}
                  {result.frames.map((frame) => (
                    <option value={frame.frameIndex} key={`${frame.frameIndex}:${frame.frameNumber}`}>
                      Timeline {frame.frameIndex + 1} · source {frame.frameNumber}
                    </option>
                  ))}
                </select>
              </label>
            ) : null}
          </div>
          {selectedFrame ? (
            <>
              <canvas ref={imageCanvasRef} className="worldgen-image-canvas" onClick={handleImageClick} />
              <div className="worldgen-frame-meta">
                <span>{compactNumber(selectedPoints.length)} image points</span>
                <span>{selectedFrame.imageWidth}x{selectedFrame.imageHeight}</span>
              </div>
            </>
          ) : null}
        </div>
      ) : null}
      </div>
    </div>
  )
}
