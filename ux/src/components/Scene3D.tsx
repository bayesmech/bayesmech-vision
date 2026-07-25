import { useEffect, useMemo, useRef } from 'react'
import * as THREE from 'three'
import { OrbitControls } from 'three/examples/jsm/controls/OrbitControls.js'
import type { SamplePlane, VisSummary } from '../types'

type Scene3DProps = {
  summary: VisSummary | null
  mode: 'scene' | 'point-cloud' | 'planes'
}

type SceneHandles = {
  scene: THREE.Scene
  camera: THREE.PerspectiveCamera
  renderer: THREE.WebGLRenderer
  controls: OrbitControls
  dataGroup: THREE.Group
  resizeObserver: ResizeObserver
  animationId: number
}

function rotateByQuat(vector: THREE.Vector3, rotation: { x: number; y: number; z: number; w: number }) {
  return vector.applyQuaternion(new THREE.Quaternion(rotation.x, rotation.y, rotation.z, rotation.w))
}

function planeWorldPoints(plane: SamplePlane): THREE.Vector3[] {
  if (!plane.centerPose || plane.polygon.length < 3) return []
  const origin = plane.centerPose.position
  const rotation = plane.centerPose.rotation
  return plane.polygon.map((point) => {
    const rotated = rotateByQuat(new THREE.Vector3(point.x, point.y, point.z), rotation)
    return rotated.add(new THREE.Vector3(origin.x, origin.y, origin.z))
  })
}

function buildPlaneMesh(plane: SamplePlane, index: number) {
  const vertices = planeWorldPoints(plane)
  if (vertices.length < 3) return null

  const triangles: number[] = []
  for (let i = 1; i < vertices.length - 1; i += 1) {
    triangles.push(0, i, i + 1)
  }

  const geometry = new THREE.BufferGeometry().setFromPoints(vertices)
  geometry.setIndex(triangles)
  geometry.computeVertexNormals()

  const palette = [0x62d2a2, 0x5aa9e6, 0xd3a84f, 0xd7687d]
  const material = new THREE.MeshBasicMaterial({
    color: palette[index % palette.length],
    transparent: true,
    opacity: 0.32,
    side: THREE.DoubleSide,
    depthWrite: false,
  })
  const mesh = new THREE.Mesh(geometry, material)

  const outlineGeometry = new THREE.BufferGeometry().setFromPoints([...vertices, vertices[0]])
  const outline = new THREE.Line(
    outlineGeometry,
    new THREE.LineBasicMaterial({ color: palette[index % palette.length], transparent: true, opacity: 0.82 }),
  )
  const group = new THREE.Group()
  group.add(mesh)
  group.add(outline)
  return group
}

function addCameraPath(group: THREE.Group, summary: VisSummary) {
  const points = summary.samples
    .map((sample) => sample.cameraPose?.position)
    .filter((point): point is NonNullable<typeof point> => Boolean(point))
    .map((point) => new THREE.Vector3(point.x, point.y, point.z))

  if (points.length < 2) return

  const geometry = new THREE.BufferGeometry().setFromPoints(points)
  const line = new THREE.Line(
    geometry,
    new THREE.LineBasicMaterial({ color: 0xf0b35a, transparent: true, opacity: 0.95 }),
  )
  group.add(line)

  const startMarker = new THREE.Mesh(
    new THREE.SphereGeometry(0.045, 16, 16),
    new THREE.MeshBasicMaterial({ color: 0xf0b35a }),
  )
  startMarker.position.copy(points[0])
  group.add(startMarker)

  const endMarker = new THREE.Mesh(
    new THREE.SphereGeometry(0.06, 16, 16),
    new THREE.MeshBasicMaterial({ color: 0xd7687d }),
  )
  endMarker.position.copy(points[points.length - 1])
  group.add(endMarker)
}

function addPointCloud(group: THREE.Group, summary: VisSummary) {
  const positions: number[] = []
  const colors: number[] = []
  const low = new THREE.Color(0x5aa9e6)
  const high = new THREE.Color(0x62d2a2)
  const accent = new THREE.Color(0xf0b35a)

  for (const sample of summary.samples) {
    for (const point of sample.points) {
      positions.push(point.x, point.y, point.z)
      const color = low.clone().lerp(point.confidence > 0.65 ? high : accent, Math.min(1, point.confidence))
      colors.push(color.r, color.g, color.b)
    }
  }

  if (!positions.length) return

  const geometry = new THREE.BufferGeometry()
  geometry.setAttribute('position', new THREE.Float32BufferAttribute(positions, 3))
  geometry.setAttribute('color', new THREE.Float32BufferAttribute(colors, 3))
  const material = new THREE.PointsMaterial({
    size: 0.026,
    vertexColors: true,
    sizeAttenuation: true,
  })
  group.add(new THREE.Points(geometry, material))
}

function addPlanes(group: THREE.Group, summary: VisSummary) {
  let planeIndex = 0
  for (const sample of summary.samples) {
    for (const plane of sample.planes) {
      const mesh = buildPlaneMesh(plane, planeIndex)
      if (mesh) {
        group.add(mesh)
        planeIndex += 1
      }
    }
  }
}

function fitCamera(handles: SceneHandles, summary: VisSummary | null) {
  const { camera, controls } = handles
  if (!summary?.bounds) {
    camera.position.set(3.4, 2.4, 4.4)
    controls.target.set(0, 0, 0)
    controls.update()
    return
  }

  const min = summary.bounds.min
  const max = summary.bounds.max
  const center = new THREE.Vector3(
    (min.x + max.x) / 2,
    (min.y + max.y) / 2,
    (min.z + max.z) / 2,
  )
  const size = new THREE.Vector3(max.x - min.x, max.y - min.y, max.z - min.z)
  const radius = Math.max(size.length() * 0.7, 1.2)
  camera.position.copy(center).add(new THREE.Vector3(radius * 1.15, radius * 0.82, radius * 1.35))
  camera.near = Math.max(0.01, radius / 200)
  camera.far = Math.max(100, radius * 100)
  camera.updateProjectionMatrix()
  controls.target.copy(center)
  controls.update()
}

function disposeObject3D(object: THREE.Object3D) {
  object.traverse((child) => {
    const mesh = child as THREE.Mesh
    mesh.geometry?.dispose?.()
    const material = mesh.material
    if (Array.isArray(material)) {
      material.forEach((item) => item.dispose())
    } else {
      material?.dispose?.()
    }
  })
}

export default function Scene3D({ summary, mode }: Scene3DProps) {
  const containerRef = useRef<HTMLDivElement>(null)
  const handlesRef = useRef<SceneHandles | null>(null)

  const sceneKey = useMemo(() => `${summary?.path ?? 'empty'}:${mode}`, [summary?.path, mode])

  useEffect(() => {
    const container = containerRef.current
    if (!container) return

    const scene = new THREE.Scene()
    scene.background = new THREE.Color(0x101114)

    const camera = new THREE.PerspectiveCamera(52, 1, 0.01, 1000)
    camera.position.set(3.4, 2.4, 4.4)

    const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: false })
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2))
    renderer.setSize(container.clientWidth, container.clientHeight)
    container.appendChild(renderer.domElement)

    const controls = new OrbitControls(camera, renderer.domElement)
    controls.enableDamping = true
    controls.dampingFactor = 0.08
    controls.target.set(0, 0, 0)

    const ambient = new THREE.AmbientLight(0xffffff, 0.72)
    const key = new THREE.DirectionalLight(0xffffff, 1.2)
    key.position.set(4, 8, 6)
    scene.add(ambient, key)

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
      scene,
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

    if (summary) {
      if (mode === 'scene' || mode === 'point-cloud' || mode === 'planes') addPointCloud(handles.dataGroup, summary)
      if (mode === 'scene' || mode === 'planes') addPlanes(handles.dataGroup, summary)
      addCameraPath(handles.dataGroup, summary)
    }

    fitCamera(handles, summary)
  }, [sceneKey, summary, mode])

  return (
    <div className="scene-shell">
      <div className="scene-canvas" ref={containerRef} data-testid="scene-canvas" />
      <div className="scene-overlay">
        <span>{summary ? summary.fileName : 'No recording selected'}</span>
        <span>{mode === 'planes' ? 'Point cloud + surface estimates' : mode === 'point-cloud' ? 'Point cloud' : 'Scene'}</span>
      </div>
    </div>
  )
}
