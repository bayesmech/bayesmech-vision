import { useEffect, useMemo, useRef } from 'react'
import * as THREE from 'three'
import { OrbitControls } from 'three/examples/jsm/controls/OrbitControls.js'
import type { SensorSample, Vec3 } from '../types'

type SensorOrientationSceneProps = {
  sample: SensorSample | null
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

const AXIS_LENGTH = 0.88
const AXIS_COLORS = {
  x: 0xd7687d,
  y: 0x62d2a2,
  z: 0x5aa9e6,
}

function quaternionFor(sample: SensorSample): THREE.Quaternion {
  const rotation = sample.cameraPose?.rotation
  if (!rotation) return new THREE.Quaternion()
  const quaternion = new THREE.Quaternion(rotation.x, rotation.y, rotation.z, rotation.w)
  return quaternion.lengthSq() > 1e-10 ? quaternion.normalize() : new THREE.Quaternion()
}

function vector(value: Vec3 | null): THREE.Vector3 | null {
  if (!value) return null
  const result = new THREE.Vector3(value.x, value.y, value.z)
  return result.lengthSq() > 1e-10 ? result : null
}

function inferredNorth(gravity: Vec3 | null, magnetic: Vec3 | null): THREE.Vector3 | null {
  const gravityVector = vector(gravity)
  const magneticVector = vector(magnetic)
  if (!gravityVector || !magneticVector) return null
  const gravityDirection = gravityVector.normalize()
  const horizontalMagnetic = magneticVector.sub(
    gravityDirection.clone().multiplyScalar(magneticVector.dot(gravityDirection)),
  )
  return horizontalMagnetic.lengthSq() > 1e-10 ? horizontalMagnetic.normalize() : null
}

function labelSprite(text: string, color: string, width = 0.68): THREE.Sprite {
  const canvas = document.createElement('canvas')
  canvas.width = 512
  canvas.height = 112
  const context = canvas.getContext('2d')
  if (context) {
    context.clearRect(0, 0, canvas.width, canvas.height)
    context.fillStyle = 'rgba(10, 12, 15, 0.86)'
    context.strokeStyle = 'rgba(114, 123, 136, 0.7)'
    context.lineWidth = 3
    context.beginPath()
    context.roundRect(2, 2, canvas.width - 4, canvas.height - 4, 20)
    context.fill()
    context.stroke()
    context.fillStyle = color
    context.font = '600 42px Inter, system-ui, sans-serif'
    context.textAlign = 'center'
    context.textBaseline = 'middle'
    context.fillText(text, canvas.width / 2, canvas.height / 2 + 1)
  }
  const texture = new THREE.CanvasTexture(canvas)
  texture.colorSpace = THREE.SRGBColorSpace
  const material = new THREE.SpriteMaterial({ map: texture, transparent: true, depthTest: false })
  const sprite = new THREE.Sprite(material)
  sprite.scale.set(width, width * (canvas.height / canvas.width), 1)
  sprite.renderOrder = 20
  return sprite
}

function addArrow(
  group: THREE.Group,
  direction: THREE.Vector3,
  length: number,
  color: number,
  label: string,
  labelColor: string,
) {
  const normalized = direction.clone().normalize()
  group.add(new THREE.ArrowHelper(normalized, new THREE.Vector3(), length, color, 0.13, 0.075))
  const sprite = labelSprite(label, labelColor, Math.max(0.58, label.length * 0.042))
  sprite.position.copy(normalized.multiplyScalar(length + 0.16))
  group.add(sprite)
}

function addDeviceAxes(group: THREE.Group, orientation: THREE.Quaternion) {
  const axes = [
    { key: 'x' as const, direction: new THREE.Vector3(1, 0, 0), label: 'X · right', color: '#d7687d' },
    { key: 'y' as const, direction: new THREE.Vector3(0, 1, 0), label: 'Y · up', color: '#62d2a2' },
    { key: 'z' as const, direction: new THREE.Vector3(0, 0, 1), label: 'Z · out', color: '#5aa9e6' },
  ]
  for (const axis of axes) {
    const direction = axis.direction.applyQuaternion(orientation)
    addArrow(group, direction, AXIS_LENGTH, AXIS_COLORS[axis.key], axis.label, axis.color)
  }
}

function addCamera(group: THREE.Group, orientation: THREE.Quaternion) {
  const cameraGroup = new THREE.Group()
  cameraGroup.quaternion.copy(orientation)

  const body = new THREE.Mesh(
    new THREE.BoxGeometry(0.42, 0.26, 0.12),
    new THREE.MeshBasicMaterial({ color: 0x343a43, transparent: true, opacity: 0.92 }),
  )
  body.position.z = 0.09
  cameraGroup.add(body)
  const bodyOutline = new THREE.LineSegments(
    new THREE.EdgesGeometry(body.geometry),
    new THREE.LineBasicMaterial({ color: 0xeef0f3, transparent: true, opacity: 0.9 }),
  )
  bodyOutline.position.copy(body.position)
  cameraGroup.add(bodyOutline)

  const lens = new THREE.Mesh(
    new THREE.CylinderGeometry(0.075, 0.09, 0.085, 20),
    new THREE.MeshBasicMaterial({ color: 0x08090b }),
  )
  lens.rotation.x = Math.PI / 2
  lens.position.z = -0.065
  cameraGroup.add(lens)

  const depth = 1.18
  const halfWidth = 0.69
  const halfHeight = 0.43
  const origin = new THREE.Vector3(0, 0, -0.11)
  const corners = [
    new THREE.Vector3(-halfWidth, -halfHeight, -depth),
    new THREE.Vector3(halfWidth, -halfHeight, -depth),
    new THREE.Vector3(halfWidth, halfHeight, -depth),
    new THREE.Vector3(-halfWidth, halfHeight, -depth),
  ]
  const points = [
    origin, corners[0], origin, corners[1], origin, corners[2], origin, corners[3],
    corners[0], corners[1], corners[1], corners[2], corners[2], corners[3], corners[3], corners[0],
  ]
  cameraGroup.add(
    new THREE.LineSegments(
      new THREE.BufferGeometry().setFromPoints(points),
      new THREE.LineBasicMaterial({ color: 0xeef0f3, transparent: true, opacity: 0.72 }),
    ),
  )

  const forward = new THREE.ArrowHelper(new THREE.Vector3(0, 0, -1), origin, 0.52, 0xeef0f3, 0.11, 0.065)
  cameraGroup.add(forward)
  group.add(cameraGroup)

  const label = labelSprite('Camera · −Z forward', '#eef0f3', 1.02)
  label.position.copy(new THREE.Vector3(0, 0.22, -1.38).applyQuaternion(orientation))
  group.add(label)
}

function addGyroscopeArc(group: THREE.Group, angularVelocity: THREE.Vector3, orientation: THREE.Quaternion) {
  const magnitude = angularVelocity.length()
  if (magnitude < 1e-6) return
  const axis = angularVelocity.clone().normalize().applyQuaternion(orientation)
  const reference = Math.abs(axis.y) < 0.82 ? new THREE.Vector3(0, 1, 0) : new THREE.Vector3(1, 0, 0)
  const first = new THREE.Vector3().crossVectors(axis, reference).normalize()
  const second = new THREE.Vector3().crossVectors(axis, first).normalize()
  const radius = 0.59
  const startAngle = -Math.PI * 0.18
  const endAngle = Math.PI * 1.28
  const points: THREE.Vector3[] = []
  for (let index = 0; index <= 64; index += 1) {
    const angle = startAngle + ((endAngle - startAngle) * index) / 64
    points.push(
      first.clone().multiplyScalar(Math.cos(angle) * radius)
        .add(second.clone().multiplyScalar(Math.sin(angle) * radius))
        .add(axis.clone().multiplyScalar(0.08)),
    )
  }
  group.add(
    new THREE.Line(
      new THREE.BufferGeometry().setFromPoints(points),
      new THREE.LineBasicMaterial({ color: 0xff66c4, transparent: true, opacity: 0.95 }),
    ),
  )

  const tangent = first.clone().multiplyScalar(-Math.sin(endAngle))
    .add(second.clone().multiplyScalar(Math.cos(endAngle)))
    .normalize()
  const cone = new THREE.Mesh(
    new THREE.ConeGeometry(0.075, 0.18, 18),
    new THREE.MeshBasicMaterial({ color: 0xff66c4 }),
  )
  cone.position.copy(points[points.length - 1])
  cone.quaternion.setFromUnitVectors(new THREE.Vector3(0, 1, 0), tangent)
  group.add(cone)

  const axisArrow = new THREE.ArrowHelper(axis, new THREE.Vector3(), 0.68, 0xff66c4, 0.11, 0.06)
  group.add(axisArrow)
  const label = labelSprite(`ω · ${magnitude.toFixed(3)} rad/s`, '#ff8bd1', 0.98)
  label.position.copy(points[Math.floor(points.length * 0.28)]).add(axis.clone().multiplyScalar(0.16))
  group.add(label)
}

function disposeObject(object: THREE.Object3D) {
  object.traverse((child) => {
    const item = child as THREE.Mesh
    item.geometry?.dispose?.()
    const material = item.material
    const materials = Array.isArray(material) ? material : material ? [material] : []
    for (const candidate of materials) {
      const map = (candidate as THREE.SpriteMaterial).map
      map?.dispose()
      candidate.dispose()
    }
  })
}

function renderSensorSample(group: THREE.Group, sample: SensorSample) {
  const orientation = quaternionFor(sample)
  addCamera(group, orientation)
  addDeviceAxes(group, orientation)

  const gravity = vector(sample.gravity)
  if (gravity) {
    addArrow(
      group,
      gravity.normalize().applyQuaternion(orientation),
      1.2,
      0xf0b35a,
      `Gravity · ${vector(sample.gravity)?.length().toFixed(2) ?? '0.00'} m/s²`,
      '#f0b35a',
    )
  }
  const north = inferredNorth(sample.gravity, sample.magneticField)
  if (north) {
    addArrow(
      group,
      north.applyQuaternion(orientation),
      1.35,
      0x37d7e5,
      'Magnetometer north · N',
      '#64e5ef',
    )
  }
  const angularVelocity = vector(sample.angularVelocity)
  if (angularVelocity) addGyroscopeArc(group, angularVelocity, orientation)
}

export default function SensorOrientationScene({ sample }: SensorOrientationSceneProps) {
  const containerRef = useRef<HTMLDivElement>(null)
  const handlesRef = useRef<SceneHandles | null>(null)
  const latestSampleRef = useRef(sample)
  latestSampleRef.current = sample
  const measurements = useMemo(() => {
    const gravityMagnitude = vector(sample?.gravity ?? null)?.length() ?? 0
    const magneticMagnitude = vector(sample?.magneticField ?? null)?.length() ?? 0
    const gyroMagnitude = vector(sample?.angularVelocity ?? null)?.length() ?? 0
    return { gravityMagnitude, magneticMagnitude, gyroMagnitude }
  }, [sample])

  useEffect(() => {
    const container = containerRef.current
    if (!container) return
    const scene = new THREE.Scene()
    scene.background = new THREE.Color(0x090b0e)
    const camera = new THREE.PerspectiveCamera(42, 1, 0.01, 100)
    camera.position.set(2.55, 1.85, 3.25)
    const renderer = new THREE.WebGLRenderer({ antialias: true })
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2))
    renderer.outputColorSpace = THREE.SRGBColorSpace
    container.appendChild(renderer.domElement)
    const controls = new OrbitControls(camera, renderer.domElement)
    controls.enableDamping = true
    controls.target.set(0, 0, -0.12)
    controls.minDistance = 1.6
    controls.maxDistance = 8
    controls.update()

    const grid = new THREE.GridHelper(5.2, 26, 0x303640, 0x1c2026)
    grid.position.y = -1.08
    scene.add(grid)
    const dataGroup = new THREE.Group()
    scene.add(dataGroup)

    let needsRender = true
    const resize = () => {
      const width = Math.max(1, container.clientWidth)
      const height = Math.max(1, container.clientHeight)
      renderer.setSize(width, height, false)
      camera.aspect = width / height
      camera.updateProjectionMatrix()
      needsRender = true
    }
    const resizeObserver = new ResizeObserver(resize)
    resizeObserver.observe(container)
    resize()

    const handles: SceneHandles = {
      scene,
      camera,
      renderer,
      controls,
      dataGroup,
      resizeObserver,
      animationId: 0,
    }
    let renderedSample: SensorSample | null | undefined
    let lastSampleRender = Number.NEGATIVE_INFINITY
    const animate = (now: number) => {
      handles.animationId = requestAnimationFrame(animate)
      const latestSample = latestSampleRef.current
      // Rebuilding arrows and their text textures for every 30/60 fps playhead
      // tick overwhelms the renderer. Consume the latest buffered sample at a
      // steady visual cadence instead, dropping stale intermediate revisions.
      if (renderedSample !== latestSample && now - lastSampleRender >= 66) {
        disposeObject(dataGroup)
        dataGroup.clear()
        if (latestSample) renderSensorSample(dataGroup, latestSample)
        renderedSample = latestSample
        lastSampleRender = now
        needsRender = true
      }
      const controlsChanged = controls.update()
      if (needsRender || controlsChanged) {
        renderer.render(scene, camera)
        needsRender = false
      }
    }
    handles.animationId = requestAnimationFrame(animate)
    handlesRef.current = handles

    return () => {
      handlesRef.current = null
      cancelAnimationFrame(handles.animationId)
      resizeObserver.disconnect()
      controls.dispose()
      disposeObject(dataGroup)
      renderer.dispose()
      renderer.domElement.remove()
    }
  }, [])

  return (
    <section className="sensor-orientation-card">
      <header>
        <div>
          <strong>Device orientation in the AR world</strong>
          <span>Drag to orbit · scroll to zoom</span>
        </div>
        <div className="sensor-orientation-values">
          <span><i className="gravity-dot" />g {measurements.gravityMagnitude.toFixed(2)} m/s²</span>
          <span><i className="north-dot" />B {measurements.magneticMagnitude.toFixed(2)} µT</span>
          <span><i className="gyro-dot" />ω {measurements.gyroMagnitude.toFixed(3)} rad/s</span>
        </div>
      </header>
      <div className="sensor-orientation-canvas" ref={containerRef}>
        <div className="sensor-orientation-axis-key" aria-label="Device axes">
          <span className="axis-x">X <small>right</small></span>
          <span className="axis-y">Y <small>up</small></span>
          <span className="axis-z">Z <small>out</small></span>
        </div>
        {!sample && <div className="sensor-orientation-empty">No synchronized orientation sample</div>}
      </div>
      <footer>
        <span><strong>X/Y/Z</strong> are Android device axes transformed by the ARCore camera quaternion.</span>
        <span><strong>North</strong> is the magnetic vector projected onto the plane perpendicular to gravity.</span>
        <span><strong>ω</strong> uses the right-hand rule around the measured gyroscope axis.</span>
      </footer>
    </section>
  )
}
