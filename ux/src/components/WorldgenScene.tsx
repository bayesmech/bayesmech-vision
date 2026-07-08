import { MouseEvent, useCallback, useEffect, useMemo, useRef, useState } from 'react'
import * as THREE from 'three'
import { OrbitControls } from 'three/examples/jsm/controls/OrbitControls.js'
import type { VisFrame, WorldgenCamera, WorldgenFrame, WorldgenPoint, WorldgenResult } from '../types'
import { compactNumber, shortPath } from '../lib/format'
import { useFrameSource } from '../lib/frameSource'

type WorldgenSceneProps = {
  result: WorldgenResult | null
}

type SceneHandles = {
  camera: THREE.PerspectiveCamera
  renderer: THREE.WebGLRenderer
  controls: OrbitControls
  dataGroup: THREE.Group
  resizeObserver: ResizeObserver
  animationId: number
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

function fitCamera(handles: SceneHandles, result: WorldgenResult | null) {
  const { camera, controls } = handles
  if (!result || result.points.length === 0) {
    camera.position.set(2.8, 2.0, 3.4)
    controls.target.set(0, 0, 0)
    controls.update()
    return
  }

  const box = new THREE.Box3()
  for (const point of result.points) box.expandByPoint(new THREE.Vector3(point.x, point.y, point.z))
  for (const center of result.cameras) box.expandByPoint(cameraPoint(center))

  const center = new THREE.Vector3()
  const size = new THREE.Vector3()
  box.getCenter(center)
  box.getSize(size)
  const radius = Math.max(size.length() * 0.62, 1.0)

  camera.position.copy(center).add(new THREE.Vector3(radius * 1.1, radius * 0.78, radius * 1.25))
  camera.near = Math.max(0.01, radius / 240)
  camera.far = Math.max(100, radius * 120)
  camera.updateProjectionMatrix()
  controls.target.copy(center)
  controls.update()
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
  result: WorldgenResult,
  selectedFrameIndex: number | null,
  showAllFrames: boolean,
  highlightedKey: string | null,
) {
  const grouped = pointsByFrame(result.points)
  for (const [frameIndex, framePoints] of grouped.entries()) {
    if (!showAllFrames && selectedFrameIndex !== frameIndex) continue
    const selected = selectedFrameIndex == null || selectedFrameIndex === frameIndex
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
          size: selected ? 0.022 : 0.014,
          vertexColors: true,
          sizeAttenuation: true,
          transparent: true,
          opacity: selected ? 1 : 0.32,
        }),
      ),
    )
  }

  const highlighted = result.points.find((point) => highlightedKey === pointKey(point))
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

function frameLabel(frame: WorldgenFrame): string {
  return `Frame ${frame.frameNumber || frame.frameIndex + 1}`
}

export default function WorldgenScene({ result }: WorldgenSceneProps) {
  const getFrame = useFrameSource()
  const containerRef = useRef<HTMLDivElement>(null)
  const handlesRef = useRef<SceneHandles | null>(null)
  const imageCanvasRef = useRef<HTMLCanvasElement>(null)
  const imageRef = useRef<HTMLImageElement | null>(null)
  const [selectedFrameIndex, setSelectedFrameIndex] = useState<number | null>(null)
  const [showAllFrames, setShowAllFrames] = useState(true)
  const [highlightedKey, setHighlightedKey] = useState<string | null>(null)
  const [frameImage, setFrameImage] = useState<VisFrame | null>(null)
  const [imageTick, setImageTick] = useState(0)
  const sceneKey = useMemo(
    () => `${result?.id ?? 'empty'}:${selectedFrameIndex ?? 'none'}:${showAllFrames}:${highlightedKey ?? 'none'}`,
    [highlightedKey, result?.id, selectedFrameIndex, showAllFrames],
  )

  const selectedFrame = useMemo(() => {
    if (!result?.frames.length) return null
    if (selectedFrameIndex == null) return result.frames[0]
    return result.frames.find((frame) => frame.frameIndex === selectedFrameIndex) ?? result.frames[0]
  }, [result, selectedFrameIndex])

  const selectedPoints = useMemo(() => {
    if (!result || !selectedFrame) return []
    return result.points.filter((point) => point.frameIndex === selectedFrame.frameIndex)
  }, [result, selectedFrame])

  useEffect(() => {
    const firstFrame = result?.frames[0] ?? null
    setSelectedFrameIndex(firstFrame?.frameIndex ?? null)
    setHighlightedKey(null)
  }, [result?.id])

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
    },
    [selectedFrame, selectedPoints],
  )

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

    const controls = new OrbitControls(camera, renderer.domElement)
    controls.enableDamping = true
    controls.dampingFactor = 0.08

    scene.add(new THREE.AmbientLight(0xffffff, 0.7))
    const key = new THREE.DirectionalLight(0xffffff, 1.1)
    key.position.set(4, 7, 5)
    scene.add(key)

    const grid = new THREE.GridHelper(8, 16, 0x3a3d44, 0x24272d)
    grid.position.y = -0.02
    scene.add(grid)
    scene.add(new THREE.AxesHelper(0.85))

    const dataGroup = new THREE.Group()
    scene.add(dataGroup)

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
      handlesRef.current!.animationId = window.requestAnimationFrame(render)
    }

    handlesRef.current = {
      camera,
      renderer,
      controls,
      dataGroup,
      resizeObserver,
      animationId: window.requestAnimationFrame(render),
    }

    return () => {
      const handles = handlesRef.current
      if (!handles) return
      window.cancelAnimationFrame(handles.animationId)
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
    disposeObject3D(handles.dataGroup)
    handles.dataGroup.clear()

    if (result) {
      addWorldgenPoints(handles.dataGroup, result, selectedFrameIndex, showAllFrames, highlightedKey)
      addCameras(handles.dataGroup, result, selectedFrameIndex)
    }

    fitCamera(handles, result)
  }, [sceneKey, result, selectedFrameIndex, showAllFrames, highlightedKey])

  return (
    <div className="scene-shell">
      <div className="scene-canvas" ref={containerRef} data-testid="worldgen-canvas" />
      <div className="scene-overlay">
        <span>{result ? `${result.markerStart}-${result.markerEnd}` : 'No worldgen result'}</span>
        <span>
          {result
            ? `${compactNumber(result.frameCount)} frames, ${compactNumber(result.returnedPointCount)} rendered points`
            : 'VGGT reconstruction'}
        </span>
        {result ? <span title={result.outputPath}>{shortPath(result.outputPath, 76)}</span> : null}
      </div>

      {result && selectedFrame ? (
        <div className="worldgen-image-panel">
          <div className="worldgen-frame-controls">
            <select
              value={selectedFrame.frameIndex}
              onChange={(event) => {
                setSelectedFrameIndex(Number(event.target.value))
                setHighlightedKey(null)
              }}
              title="Frame"
            >
              {result.frames.map((frame) => (
                <option key={frame.frameIndex} value={frame.frameIndex}>
                  {frameLabel(frame)}
                </option>
              ))}
            </select>
            <label>
              <input type="checkbox" checked={showAllFrames} onChange={(event) => setShowAllFrames(event.target.checked)} />
              <span>All frames</span>
            </label>
          </div>
          <canvas ref={imageCanvasRef} className="worldgen-image-canvas" onClick={handleImageClick} />
          <div className="worldgen-frame-meta">
            <span>{compactNumber(selectedPoints.length)} image points</span>
            <span>{selectedFrame.imageWidth}x{selectedFrame.imageHeight}</span>
          </div>
        </div>
      ) : null}
    </div>
  )
}
