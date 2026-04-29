import React, { useEffect, useMemo, useRef } from 'react'
import * as THREE from 'three'
import { OrbitControls } from 'three/examples/jsm/controls/OrbitControls.js'
import type { PongtownResponse } from '../types'

interface PongtownTable3DProps {
  summary?: PongtownResponse
  currentFrameIndex?: number
  currentFrameNumber?: number
}

interface BounceMarker {
  frameIdx: number
  frameNumber: number
  xM: number
  zM: number
  insideTable: boolean
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

const numberList = (value: unknown): number[] => {
  if (!value) return []
  if (Array.isArray(value)) return value.map(Number).filter(Number.isFinite)
  if (ArrayBuffer.isView(value)) {
    return Array.from(value as unknown as ArrayLike<number>, Number).filter(Number.isFinite)
  }
  return []
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

const addLine = (
  parent: THREE.Group,
  xM: number,
  zM: number,
  widthM: number,
  depthM: number,
): void => {
  const line = new THREE.Mesh(
    new THREE.BoxGeometry(widthM, 0.006, depthM),
    new THREE.MeshStandardMaterial({ color: 0xf8fbf4, roughness: 0.58 }),
  )
  line.position.set(xM, TABLE_THICKNESS_M / 2 + 0.006, zM)
  parent.add(line)
}

const buildTable = (scene: THREE.Scene, tableLengthM: number, tableWidthM: number): void => {
  const table = new THREE.Group()

  const top = new THREE.Mesh(
    new THREE.BoxGeometry(tableLengthM, TABLE_THICKNESS_M, tableWidthM),
    new THREE.MeshStandardMaterial({ color: 0x145c40, roughness: 0.68, metalness: 0.02 }),
  )
  top.position.y = 0
  table.add(top)

  const rail = 0.025
  addLine(table, 0, -tableWidthM / 2 + rail / 2, tableLengthM, rail)
  addLine(table, 0, tableWidthM / 2 - rail / 2, tableLengthM, rail)
  addLine(table, -tableLengthM / 2 + rail / 2, 0, rail, tableWidthM)
  addLine(table, tableLengthM / 2 - rail / 2, 0, rail, tableWidthM)
  addLine(table, 0, 0, tableLengthM, 0.012)

  const legMaterial = new THREE.MeshStandardMaterial({ color: 0x161b18, roughness: 0.82 })
  const legGeometry = new THREE.BoxGeometry(0.07, 0.68, 0.07)
  for (const x of [-tableLengthM * 0.38, tableLengthM * 0.38]) {
    for (const z of [-tableWidthM * 0.38, tableWidthM * 0.38]) {
      const leg = new THREE.Mesh(legGeometry.clone(), legMaterial.clone())
      leg.position.set(x, -0.375, z)
      table.add(leg)
    }
  }

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

  scene.add(table)
}

const PongtownTable3D: React.FC<PongtownTable3DProps> = ({
  summary,
  currentFrameIndex,
  currentFrameNumber,
}) => {
  const containerRef = useRef<HTMLDivElement | null>(null)
  const sceneRef = useRef<SceneState | null>(null)

  const tableLengthM = (Number(summary?.tableWidthMm) || DEFAULT_TABLE_LENGTH_MM) / 1000
  const tableWidthM = (Number(summary?.tableHeightMm) || DEFAULT_TABLE_WIDTH_MM) / 1000

  const bounces = useMemo<BounceMarker[]>(() => {
    const raw = summary?.ballTrajectory?.bounces ?? []
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

  const visibleBounces = useMemo(() => {
    if (currentFrameNumber !== undefined && bounces.some((bounce) => bounce.frameNumber > 0)) {
      return bounces.filter((bounce) => bounce.frameNumber <= currentFrameNumber)
    }
    if (currentFrameIndex === undefined) return bounces
    return bounces.filter((bounce) => bounce.frameIdx <= currentFrameIndex)
  }, [bounces, currentFrameIndex, currentFrameNumber])

  useEffect(() => {
    const container = containerRef.current
    if (!container) return

    const scene = new THREE.Scene()
    scene.background = new THREE.Color(0x050706)

    const camera = new THREE.PerspectiveCamera(42, 1, 0.01, 100)
    camera.position.set(2.7, 2.0, 2.55)

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
    controls.maxDistance = 7
    controls.maxPolarAngle = Math.PI * 0.48

    scene.add(new THREE.AmbientLight(0xffffff, 0.58))
    const key = new THREE.DirectionalLight(0xffffff, 1.15)
    key.position.set(2.2, 3.8, 2.3)
    scene.add(key)
    const fill = new THREE.DirectionalLight(0x7fffd2, 0.24)
    fill.position.set(-3, 1.6, -1.6)
    scene.add(fill)

    const floor = new THREE.Mesh(
      new THREE.PlaneGeometry(6.2, 4.4),
      new THREE.MeshStandardMaterial({ color: 0x111611, roughness: 0.9 }),
    )
    floor.rotation.x = -Math.PI / 2
    floor.position.y = -0.72
    scene.add(floor)

    buildTable(scene, tableLengthM, tableWidthM)

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
  }, [tableLengthM, tableWidthM])

  useEffect(() => {
    const state = sceneRef.current
    if (!state) return
    while (state.markers.children.length > 0) {
      const child = state.markers.children.pop()
      if (child) disposeObject(child)
    }

    const lastVisibleFrame = visibleBounces.at(-1)?.frameIdx
    for (const bounce of visibleBounces) {
      const marker = new THREE.Group()
      const active = bounce.frameIdx === lastVisibleFrame
      const markerColor = active ? 0xff5d35 : bounce.insideTable ? 0xffd84d : 0xa7acb2

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
  }, [visibleBounces])

  return (
    <div className="stream-card table-3d-card">
      <div className="stream-header table-3d-header">
        <span className="stream-title">3D Bounce Table</span>
        <span className="table-3d-status">
          {currentFrameNumber !== undefined ? `Frame ${currentFrameNumber}` : 'Frame N/A'}
        </span>
      </div>
      <div className="table-3d-viewer" ref={containerRef} />
      <div className="surface-pose-footer">
        <span>{`Bounces ${visibleBounces.length}/${bounces.length}`}</span>
        <span>{visibleBounces.length ? `Latest F${visibleBounces.at(-1)?.frameNumber}` : 'Latest N/A'}</span>
      </div>
    </div>
  )
}

export default PongtownTable3D
