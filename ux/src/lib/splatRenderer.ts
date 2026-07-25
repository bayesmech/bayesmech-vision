// Asynchronous 3D Gaussian Splatting renderer for three.js.
//
// Gaussian preparation and camera-depth sorting run in a Web Worker. The mesh is
// deliberately hidden whenever its ordering is stale, allowing the viewer to show
// a lightweight point-cloud fallback while interaction remains responsive.

import * as THREE from 'three'
import type { WorldgenSplatPoint } from '../types'

export type SplatRenderJobPhase = 'preparing' | 'moving' | 'rendering' | 'ready' | 'failed'

export type SplatRenderJobState = {
  phase: SplatRenderJobPhase
  message: string
  jobId?: number
}

export type SplatHandle = {
  object: THREE.Object3D
  // Called every frame. It only observes the camera and updates a uniform; all
  // expensive preparation and depth sorting is delegated to the worker.
  update: (camera: THREE.PerspectiveCamera, viewportPx: THREE.Vector2) => void
  dispose: () => void
  readonly renderedCount: number
  readonly culledFloaters: number
}

type SplatJobListener = (state: SplatRenderJobState) => void

type WorkerInitializedMessage = {
  type: 'initialized'
  renderedCount: number
  culledFloaters: number
}

type WorkerSortedMessage = {
  type: 'sorted'
  jobId: number
  revision: number
  center: Float32Array
  color: Float32Array
  opacity: Float32Array
  scale: Float32Array
  quat: Float32Array
}

type WorkerFailedMessage = {
  type: 'failed'
  jobId?: number
  message: string
}

type SplatWorkerMessage = WorkerInitializedMessage | WorkerSortedMessage | WorkerFailedMessage

// Quad half-extent in units of sigma. 3 sigma captures 98.9% of a Gaussian's mass.
const SIGMA_EXTENT = 3.0
const CAMERA_SETTLE_MS = 110
const PACK_CHUNK_SIZE = 4096

const VERTEX_SHADER = /* glsl */ `
  precision highp float;

  attribute vec3 iCenter;
  attribute vec3 iColor;
  attribute float iOpacity;
  attribute vec3 iScale;
  attribute vec4 iQuat;

  uniform vec2 uViewport;

  varying vec3 vColor;
  varying float vOpacity;
  varying vec2 vLocal;

  mat3 quatToMat(vec4 q) {
    q = normalize(q);
    float x = q.x, y = q.y, z = q.z, w = q.w;
    float x2 = x + x, y2 = y + y, z2 = z + z;
    float xx = x * x2, yy = y * y2, zz = z * z2;
    float xy = x * y2, xz = x * z2, yz = y * z2;
    float wx = w * x2, wy = w * y2, wz = w * z2;
    return mat3(
      1.0 - (yy + zz), xy + wz,         xz - wy,
      xy - wz,         1.0 - (xx + zz), yz + wx,
      xz + wy,         yz - wx,         1.0 - (xx + yy));
  }

  void main() {
    vColor = iColor;
    vOpacity = iOpacity;
    vec4 camCenter = viewMatrix * modelMatrix * vec4(iCenter, 1.0);

    if (camCenter.z > -0.01) {
      gl_Position = vec4(2.0, 2.0, 2.0, 1.0);
      return;
    }

    mat3 R = quatToMat(iQuat);
    mat3 S = mat3(iScale.x, 0.0, 0.0, 0.0, iScale.y, 0.0, 0.0, 0.0, iScale.z);
    mat3 M = R * S;
    mat3 Sigma = M * transpose(M);
    mat3 W = mat3(viewMatrix) * mat3(modelMatrix);
    mat3 Vcov = W * Sigma * transpose(W);

    float fx = projectionMatrix[0][0] * uViewport.x * 0.5;
    float fy = projectionMatrix[1][1] * uViewport.y * 0.5;
    float invz = 1.0 / camCenter.z;
    mat3 J = mat3(
      fx * invz, 0.0,       0.0,
      0.0,       fy * invz, 0.0,
      -fx * camCenter.x * invz * invz, -fy * camCenter.y * invz * invz, 0.0);

    mat3 cov = J * Vcov * transpose(J);
    float a = cov[0][0] + 0.3;
    float b = cov[0][1];
    float c = cov[1][1] + 0.3;
    float mid = 0.5 * (a + c);
    float disc = sqrt(max(0.0, mid * mid - (a * c - b * b)));
    float l1 = mid + disc;
    float l2 = max(mid - disc, 0.0);
    float major = min(sqrt(l1), 1024.0);
    float minor = min(sqrt(l2), 1024.0);
    vec2 e1 = (abs(b) < 1e-6 && abs(l1 - a) < 1e-6) ? vec2(1.0, 0.0) : normalize(vec2(b, l1 - a));
    vec2 e2 = vec2(-e1.y, e1.x);

    vec2 corner = position.xy;
    vec2 offsetPix = corner.x * major * e1 + corner.y * minor * e2;
    vec4 clip = projectionMatrix * camCenter;
    clip.xy += (2.0 * offsetPix / uViewport) * clip.w;
    gl_Position = clip;
    vLocal = corner;
  }
`

const FRAGMENT_SHADER = /* glsl */ `
  precision highp float;

  uniform float uOpacityScale;

  varying vec3 vColor;
  varying float vOpacity;
  varying vec2 vLocal;

  void main() {
    float power = -0.5 * dot(vLocal, vLocal);
    float alpha = vOpacity * uOpacityScale * exp(power);
    if (alpha < 0.00393) discard;
    gl_FragColor = vec4(vColor * alpha, alpha);
  }
`

function finite(value: number | undefined, fallback: number): number {
  return Number.isFinite(value) ? (value as number) : fallback
}

function clamped(value: number | undefined, fallback: number, minimum: number, maximum: number): number {
  return Math.min(maximum, Math.max(minimum, finite(value, fallback)))
}

export function createSplatRenderer(
  points: WorldgenSplatPoint[],
  opacityScale = 1,
  onJobStateChange?: SplatJobListener,
): SplatHandle | null {
  if (!points.length) return null

  const geometry = new THREE.InstancedBufferGeometry()
  const extent = SIGMA_EXTENT
  geometry.setAttribute(
    'position',
    new THREE.Float32BufferAttribute(
      [-extent, -extent, 0, extent, -extent, 0, extent, extent, 0, -extent, extent, 0],
      3,
    ),
  )
  geometry.setIndex([0, 1, 2, 0, 2, 3])
  geometry.instanceCount = 0

  const material = new THREE.ShaderMaterial({
    uniforms: {
      uViewport: { value: new THREE.Vector2(1, 1) },
      uOpacityScale: { value: opacityScale },
    },
    vertexShader: VERTEX_SHADER,
    fragmentShader: FRAGMENT_SHADER,
    transparent: true,
    depthTest: true,
    depthWrite: false,
    blending: THREE.CustomBlending,
    blendEquation: THREE.AddEquation,
    blendSrc: THREE.OneFactor,
    blendDst: THREE.OneMinusSrcAlphaFactor,
    blendEquationAlpha: THREE.AddEquation,
    blendSrcAlpha: THREE.OneFactor,
    blendDstAlpha: THREE.OneMinusSrcAlphaFactor,
  })

  const mesh = new THREE.Mesh(geometry, material)
  mesh.frustumCulled = false
  mesh.renderOrder = 10
  mesh.visible = false

  let worker: Worker | null = null
  let packTimer: number | null = null
  let disposed = false
  let initialized = false
  let workerBusy = false
  let failed = false
  let cameraSeen = false
  let attributesReady = false
  let cameraRevision = 0
  let sortedRevision = -1
  let nextJobId = 1
  let activeJobId = 0
  let lastCameraChangeMs = 0
  let renderedCount = 0
  let culledFloaters = 0
  let lastStateKey = ''
  const observedPosition = new THREE.Vector3()
  const observedQuaternion = new THREE.Quaternion()
  const cameraPosition = new THREE.Vector3()
  const cameraQuaternion = new THREE.Quaternion()
  const cameraForward = new THREE.Vector3()

  let centerAttribute: THREE.InstancedBufferAttribute | null = null
  let colorAttribute: THREE.InstancedBufferAttribute | null = null
  let opacityAttribute: THREE.InstancedBufferAttribute | null = null
  let scaleAttribute: THREE.InstancedBufferAttribute | null = null
  let quatAttribute: THREE.InstancedBufferAttribute | null = null

  function emit(state: SplatRenderJobState) {
    const key = `${state.phase}:${state.jobId ?? 0}:${state.message}`
    if (key === lastStateKey || disposed) return
    lastStateKey = key
    onJobStateChange?.(state)
  }

  function markFailed(message: string) {
    if (disposed) return
    failed = true
    workerBusy = false
    mesh.visible = false
    if (packTimer != null) {
      window.clearTimeout(packTimer)
      packTimer = null
    }
    worker?.terminate()
    worker = null
    emit({ phase: 'failed', message })
  }

  function dynamicAttribute(array: Float32Array, itemSize: number): THREE.InstancedBufferAttribute {
    const attribute = new THREE.InstancedBufferAttribute(array, itemSize)
    attribute.setUsage(THREE.DynamicDrawUsage)
    return attribute
  }

  function applySortedAttributes(message: WorkerSortedMessage) {
    if (!attributesReady) {
      centerAttribute = dynamicAttribute(message.center, 3)
      colorAttribute = dynamicAttribute(message.color, 3)
      opacityAttribute = dynamicAttribute(message.opacity, 1)
      scaleAttribute = dynamicAttribute(message.scale, 3)
      quatAttribute = dynamicAttribute(message.quat, 4)
      geometry.setAttribute('iCenter', centerAttribute)
      geometry.setAttribute('iColor', colorAttribute)
      geometry.setAttribute('iOpacity', opacityAttribute)
      geometry.setAttribute('iScale', scaleAttribute)
      geometry.setAttribute('iQuat', quatAttribute)
      attributesReady = true
    } else {
      centerAttribute!.array = message.center
      colorAttribute!.array = message.color
      opacityAttribute!.array = message.opacity
      scaleAttribute!.array = message.scale
      quatAttribute!.array = message.quat
      centerAttribute!.needsUpdate = true
      colorAttribute!.needsUpdate = true
      opacityAttribute!.needsUpdate = true
      scaleAttribute!.needsUpdate = true
      quatAttribute!.needsUpdate = true
    }
    geometry.instanceCount = message.opacity.length
  }

  function handleWorkerMessage(event: MessageEvent<SplatWorkerMessage>) {
    if (disposed) return
    const message = event.data
    if (message.type === 'failed') {
      markFailed(`Gaussian splat render failed: ${message.message}`)
      return
    }
    if (message.type === 'initialized') {
      initialized = true
      renderedCount = message.renderedCount
      culledFloaters = message.culledFloaters
      if (!renderedCount) markFailed('No visible Gaussian splats were available; showing the point cloud')
      return
    }

    workerBusy = false
    if (message.jobId !== activeJobId) return
    const now = performance.now()
    if (message.revision !== cameraRevision || now - lastCameraChangeMs < CAMERA_SETTLE_MS) {
      mesh.visible = false
      emit({ phase: 'moving', message: 'POV changed; queuing the latest Gaussian splat' })
      return
    }
    applySortedAttributes(message)
    sortedRevision = message.revision
    mesh.visible = true
    emit({ phase: 'ready', jobId: message.jobId, message: 'Gaussian splat ready' })
  }

  try {
    worker = new Worker(new URL('./splatSort.worker.ts', import.meta.url), { type: 'module' })
    worker.onmessage = handleWorkerMessage
    worker.onerror = (event) => {
      markFailed(`Gaussian splat worker failed: ${event.message || 'unknown worker error'}`)
    }
  } catch (error) {
    markFailed(`Gaussian splat worker could not start: ${error instanceof Error ? error.message : String(error)}`)
  }

  if (worker && !failed) {
    const center = new Float32Array(points.length * 3)
    const color = new Float32Array(points.length * 3)
    const opacity = new Float32Array(points.length)
    const scale = new Float32Array(points.length * 3)
    const quat = new Float32Array(points.length * 4)
    let packedCount = 0

    const packChunk = () => {
      if (disposed || failed || !worker) return
      const end = Math.min(points.length, packedCount + PACK_CHUNK_SIZE)
      for (let index = packedCount; index < end; index += 1) {
        const point = points[index]
        const offset3 = index * 3
        const offset4 = index * 4
        center[offset3] = Number(point.x)
        center[offset3 + 1] = Number(point.y)
        center[offset3 + 2] = Number(point.z)
        color[offset3] = clamped(point.r, 0.7, 0, 1)
        color[offset3 + 1] = clamped(point.g, 0.7, 0, 1)
        color[offset3 + 2] = clamped(point.b, 0.7, 0, 1)
        opacity[index] = clamped(point.opacity, 0, 0, 1)
        const fallbackScale = Math.max(1e-5, finite(point.scale, 0.02))
        scale[offset3] = Math.max(1e-5, finite(point.sx, fallbackScale))
        scale[offset3 + 1] = Math.max(1e-5, finite(point.sy, fallbackScale))
        scale[offset3 + 2] = Math.max(1e-5, finite(point.sz, fallbackScale))
        const qx = finite(point.qx, 0)
        const qy = finite(point.qy, 0)
        const qz = finite(point.qz, 0)
        const qw = finite(point.qw, 1)
        const quaternionLength = Math.hypot(qx, qy, qz, qw)
        if (quaternionLength > 1e-8) {
          quat[offset4] = qx / quaternionLength
          quat[offset4 + 1] = qy / quaternionLength
          quat[offset4 + 2] = qz / quaternionLength
          quat[offset4 + 3] = qw / quaternionLength
        } else {
          quat[offset4 + 3] = 1
        }
      }
      packedCount = end
      if (packedCount < points.length) {
        packTimer = window.setTimeout(packChunk, 0)
        return
      }
      worker.postMessage(
        { type: 'initialize', center, color, opacity, scale, quat },
        [center.buffer, color.buffer, opacity.buffer, scale.buffer, quat.buffer],
      )
    }
    packTimer = window.setTimeout(packChunk, 0)
  }

  return {
    object: mesh,
    get renderedCount() {
      return renderedCount
    },
    get culledFloaters() {
      return culledFloaters
    },
    update(camera, viewportPx) {
      material.uniforms.uViewport.value.copy(viewportPx)
      if (disposed || failed) return

      camera.updateMatrixWorld()
      camera.getWorldPosition(cameraPosition)
      camera.getWorldQuaternion(cameraQuaternion)
      const cameraChanged = !cameraSeen ||
        cameraPosition.distanceToSquared(observedPosition) > 1e-10 ||
        1 - Math.abs(cameraQuaternion.dot(observedQuaternion)) > 1e-10

      const now = performance.now()
      if (cameraChanged) {
        cameraSeen = true
        observedPosition.copy(cameraPosition)
        observedQuaternion.copy(cameraQuaternion)
        cameraRevision += 1
        lastCameraChangeMs = now
        mesh.visible = false
        if (initialized) emit({ phase: 'moving', message: 'POV changed; rendering will continue in the background' })
      }

      if (!initialized || workerBusy || sortedRevision === cameraRevision) return
      if (now - lastCameraChangeMs < CAMERA_SETTLE_MS) return

      camera.getWorldDirection(cameraForward)
      const jobId = nextJobId
      nextJobId += 1
      activeJobId = jobId
      workerBusy = true
      emit({ phase: 'rendering', jobId, message: 'Rendering Gaussian splat for the current POV' })
      worker?.postMessage({
        type: 'sort',
        jobId,
        revision: cameraRevision,
        cameraPosition: [cameraPosition.x, cameraPosition.y, cameraPosition.z],
        cameraForward: [cameraForward.x, cameraForward.y, cameraForward.z],
      })
    },
    dispose() {
      disposed = true
      if (packTimer != null) window.clearTimeout(packTimer)
      worker?.terminate()
      worker = null
      geometry.dispose()
      material.dispose()
    },
  }
}
