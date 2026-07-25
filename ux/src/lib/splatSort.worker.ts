type InitializeMessage = {
  type: 'initialize'
  center: Float32Array
  color: Float32Array
  opacity: Float32Array
  scale: Float32Array
  quat: Float32Array
}

type SortMessage = {
  type: 'sort'
  jobId: number
  revision: number
  cameraPosition: [number, number, number]
  cameraForward: [number, number, number]
}

type WorkerRequest = InitializeMessage | SortMessage

type PackedSplats = {
  center: Float32Array
  color: Float32Array
  opacity: Float32Array
  scale: Float32Array
  quat: Float32Array
}

type WorkerScope = {
  onmessage: ((event: MessageEvent<WorkerRequest>) => void) | null
  postMessage: (message: unknown, transfer?: Transferable[]) => void
}

const workerScope = self as unknown as WorkerScope
let source: PackedSplats | null = null

function percentile(sorted: number[], amount: number): number {
  if (!sorted.length) return 0
  const index = Math.max(0, Math.min(sorted.length - 1, Math.round(amount * (sorted.length - 1))))
  return sorted[index]
}

function robustBounds(center: Float32Array, count: number): Array<{ min: number; max: number }> | null {
  if (count < 128) return null
  const bounds: Array<{ min: number; max: number }> = []
  for (let axis = 0; axis < 3; axis += 1) {
    const values: number[] = []
    for (let index = 0; index < count; index += 1) {
      const value = center[index * 3 + axis]
      if (Number.isFinite(value)) values.push(value)
    }
    values.sort((a, b) => a - b)
    if (!values.length) return null
    const low = percentile(values, 0.005)
    const high = percentile(values, 0.995)
    const midpoint = (low + high) * 0.5
    const halfExtent = Math.max(1e-3, (high - low) * 0.5) * 1.5
    bounds.push({ min: midpoint - halfExtent, max: midpoint + halfExtent })
  }
  return bounds
}

function initialize(message: InitializeMessage) {
  const count = message.opacity.length
  if (
    message.center.length !== count * 3 ||
    message.color.length !== count * 3 ||
    message.scale.length !== count * 3 ||
    message.quat.length !== count * 4
  ) {
    throw new Error('Gaussian splat attribute buffers have inconsistent lengths')
  }

  const bounds = robustBounds(message.center, count)
  const keep = new Uint8Array(count)
  let keptCount = 0
  let culledFloaters = 0

  for (let index = 0; index < count; index += 1) {
    const offset = index * 3
    const x = message.center[offset]
    const y = message.center[offset + 1]
    const z = message.center[offset + 2]
    if (!Number.isFinite(x) || !Number.isFinite(y) || !Number.isFinite(z) || message.opacity[index] <= 0.012) {
      continue
    }
    const insideBounds = !bounds || (
      x >= bounds[0].min && x <= bounds[0].max &&
      y >= bounds[1].min && y <= bounds[1].max &&
      z >= bounds[2].min && z <= bounds[2].max
    )
    if (!insideBounds) {
      culledFloaters += 1
      continue
    }
    keep[index] = 1
    keptCount += 1
  }

  const packed: PackedSplats = {
    center: new Float32Array(keptCount * 3),
    color: new Float32Array(keptCount * 3),
    opacity: new Float32Array(keptCount),
    scale: new Float32Array(keptCount * 3),
    quat: new Float32Array(keptCount * 4),
  }
  let destination = 0
  for (let index = 0; index < count; index += 1) {
    if (!keep[index]) continue
    packed.center.set(message.center.subarray(index * 3, index * 3 + 3), destination * 3)
    packed.color.set(message.color.subarray(index * 3, index * 3 + 3), destination * 3)
    packed.opacity[destination] = message.opacity[index]
    packed.scale.set(message.scale.subarray(index * 3, index * 3 + 3), destination * 3)
    packed.quat.set(message.quat.subarray(index * 4, index * 4 + 4), destination * 4)
    destination += 1
  }
  source = packed
  workerScope.postMessage({ type: 'initialized', renderedCount: keptCount, culledFloaters })
}

function reordered(sourceArray: Float32Array, order: Uint32Array, stride: number): Float32Array {
  const output = new Float32Array(order.length * stride)
  for (let destination = 0; destination < order.length; destination += 1) {
    const sourceOffset = order[destination] * stride
    const destinationOffset = destination * stride
    for (let component = 0; component < stride; component += 1) {
      output[destinationOffset + component] = sourceArray[sourceOffset + component]
    }
  }
  return output
}

function sortSplats(message: SortMessage) {
  if (!source) throw new Error('Gaussian splat worker was asked to sort before initialization')
  const count = source.opacity.length
  const order = new Uint32Array(count)
  const depth = new Float32Array(count)
  const [cameraX, cameraY, cameraZ] = message.cameraPosition
  const [forwardX, forwardY, forwardZ] = message.cameraForward

  for (let index = 0; index < count; index += 1) {
    order[index] = index
    const offset = index * 3
    depth[index] =
      (source.center[offset] - cameraX) * forwardX +
      (source.center[offset + 1] - cameraY) * forwardY +
      (source.center[offset + 2] - cameraZ) * forwardZ
  }
  order.sort((left, right) => depth[right] - depth[left])

  const sorted: PackedSplats = {
    center: reordered(source.center, order, 3),
    color: reordered(source.color, order, 3),
    opacity: reordered(source.opacity, order, 1),
    scale: reordered(source.scale, order, 3),
    quat: reordered(source.quat, order, 4),
  }
  workerScope.postMessage(
    {
      type: 'sorted',
      jobId: message.jobId,
      revision: message.revision,
      ...sorted,
    },
    [
      sorted.center.buffer,
      sorted.color.buffer,
      sorted.opacity.buffer,
      sorted.scale.buffer,
      sorted.quat.buffer,
    ],
  )
}

workerScope.onmessage = (event) => {
  try {
    if (event.data.type === 'initialize') initialize(event.data)
    else sortSplats(event.data)
  } catch (error) {
    workerScope.postMessage({
      type: 'failed',
      jobId: event.data.type === 'sort' ? event.data.jobId : undefined,
      message: error instanceof Error ? error.message : String(error),
    })
  }
}
