import type {
  MotionCaptureOverlay,
  MotionTrajectoryMode,
  SegMask,
} from '../types'

type SegmentationRequest = {
  type: 'segmentation'
  jobId: number
  frameNumber: number
  masks: SegMask[]
  selectedLabel: string | null
}

type MotionRequest = {
  type: 'motion'
  jobId: number
  frameNumber: number
  motion: MotionCaptureOverlay | null
  trajectoryMode: MotionTrajectoryMode
}

type PrepareRequest = SegmentationRequest | MotionRequest

type WorkerScope = {
  onmessage: ((event: MessageEvent<PrepareRequest>) => void) | null
  postMessage: (message: unknown, transfer?: Transferable[]) => void
}

type DecodedPackedMask = {
  width: number
  height: number
  packed: Uint8Array
  objectId: number
  label: string
  color: [number, number, number]
}

const MASK_ALPHA = 140
const SELECTED_MASK_DIM_ALPHA = 209
const PALETTE: Array<[number, number, number]> = [
  [90, 169, 230],
  [98, 210, 162],
  [240, 179, 90],
  [215, 104, 125],
  [168, 132, 230],
  [120, 200, 120],
  [235, 130, 90],
  [90, 200, 220],
  [225, 120, 180],
  [180, 190, 90],
]
const MOTION_TRACK_COLORS: Array<[number, number, number]> = [
  [255, 200, 0],
  [50, 255, 50],
  [80, 80, 255],
  [200, 50, 255],
  [0, 220, 255],
  [255, 100, 100],
  [200, 255, 0],
  [255, 0, 200],
  [0, 180, 255],
  [255, 128, 0],
]

const workerScope = self as unknown as WorkerScope

function colorForObject(objectId: number): [number, number, number] {
  const index = ((objectId % PALETTE.length) + PALETTE.length) % PALETTE.length
  return PALETTE[index]
}

function trackColor(trackId: number, segmentation: boolean): [number, number, number] {
  const colorIndex = trackId + (segmentation ? 5 : 0)
  const index = (
    (colorIndex % MOTION_TRACK_COLORS.length) + MOTION_TRACK_COLORS.length
  ) % MOTION_TRACK_COLORS.length
  return MOTION_TRACK_COLORS[index]
}

function normalizedLabel(label: string): string {
  return label.replace(/_/g, ' ').trim().replace(/\s+/g, ' ').toLowerCase()
}

function base64ToBytes(base64: string): Uint8Array {
  const binary = atob(base64)
  const bytes = new Uint8Array(binary.length)
  for (let index = 0; index < binary.length; index += 1) {
    bytes[index] = binary.charCodeAt(index)
  }
  return bytes
}

async function inflate(bytes: Uint8Array): Promise<Uint8Array> {
  const stream = new Blob([new Uint8Array(bytes)])
    .stream()
    .pipeThrough(new DecompressionStream('deflate'))
  return new Uint8Array(await new Response(stream).arrayBuffer())
}

async function decodePackedMask(mask: SegMask): Promise<DecodedPackedMask | null> {
  const raw = base64ToBytes(mask.maskData)
  if (raw.length < 8) return null
  const view = new DataView(raw.buffer, raw.byteOffset, raw.byteLength)
  const height = view.getUint32(0, true)
  const width = view.getUint32(4, true)
  const total = width * height
  if (total <= 0 || total > 64_000_000) return null
  try {
    const packed = await inflate(raw.subarray(8))
    if (packed.length * 8 < total) return null
    return {
      width,
      height,
      packed,
      objectId: mask.objectId,
      label: mask.label,
      color: colorForObject(mask.objectId),
    }
  } catch {
    return null
  }
}

function maskContains(mask: DecodedPackedMask, pixelIndex: number): boolean {
  return Boolean((mask.packed[pixelIndex >> 3] >> (7 - (pixelIndex & 7))) & 1)
}

async function prepareSegmentation(message: SegmentationRequest) {
  const decoded = (await Promise.all(message.masks.map(decodePackedMask)))
    .filter((mask): mask is DecodedPackedMask => mask !== null)
  const metadata = decoded.map(({ objectId, label, color }) => ({ objectId, label, color }))
  const first = decoded[0]
  if (!first) {
    workerScope.postMessage({
      type: 'prepared',
      kind: 'segmentation',
      jobId: message.jobId,
      frameNumber: message.frameNumber,
      width: 0,
      height: 0,
      bitmap: null,
      metadata,
    })
    return
  }

  const width = first.width
  const height = first.height
  const total = width * height
  const selected = message.selectedLabel
    ? normalizedLabel(message.selectedLabel)
    : null
  const pixels = new Uint8ClampedArray(total * 4)
  if (selected) {
    for (let pixelIndex = 0; pixelIndex < total; pixelIndex += 1) {
      const target = pixelIndex * 4
      pixels[target + 3] = SELECTED_MASK_DIM_ALPHA
    }
  }

  for (const mask of decoded) {
    if (mask.width !== width || mask.height !== height) continue
    if (selected && normalizedLabel(mask.label) !== selected) continue
    const [red, green, blue] = mask.color
    for (let pixelIndex = 0; pixelIndex < total; pixelIndex += 1) {
      if (!maskContains(mask, pixelIndex)) continue
      const target = pixelIndex * 4
      pixels[target] = red
      pixels[target + 1] = green
      pixels[target + 2] = blue
      pixels[target + 3] = MASK_ALPHA
    }
  }

  const canvas = new OffscreenCanvas(width, height)
  const context = canvas.getContext('2d')
  if (!context) throw new Error('Segmentation overlay canvas is unavailable')
  context.putImageData(new ImageData(pixels, width, height), 0, 0)
  const bitmap = canvas.transferToImageBitmap()
  workerScope.postMessage({
    type: 'prepared',
    kind: 'segmentation',
    jobId: message.jobId,
    frameNumber: message.frameNumber,
    width,
    height,
    bitmap,
    metadata,
  }, [bitmap])
}

function jetColor(value: number): [number, number, number] {
  const normalized = value / 255
  const clamp = (channel: number) => Math.max(0, Math.min(1, channel))
  return [
    Math.round(255 * clamp(1.5 - Math.abs(4 * normalized - 3))),
    Math.round(255 * clamp(1.5 - Math.abs(4 * normalized - 2))),
    Math.round(255 * clamp(1.5 - Math.abs(4 * normalized - 1))),
  ]
}

const JET_COLORS = Array.from({ length: 256 }, (_value, index) => jetColor(index))

async function decodeHeatmap(heatmapData: string) {
  const raw = base64ToBytes(heatmapData)
  if (raw.length < 8) return null
  const view = new DataView(raw.buffer, raw.byteOffset, raw.byteLength)
  const height = view.getUint32(0, true)
  const width = view.getUint32(4, true)
  const total = width * height
  if (total <= 0 || total > 64_000_000) return null
  try {
    const values = await inflate(raw.subarray(8))
    if (values.length < total) return null
    return { width, height, values: values.subarray(0, total) }
  } catch {
    return null
  }
}

function drawMotionTracks(
  context: OffscreenCanvasRenderingContext2D,
  motion: MotionCaptureOverlay,
  trajectoryMode: MotionTrajectoryMode,
) {
  context.save()
  context.lineCap = 'round'
  context.lineJoin = 'round'
  for (const track of motion.tracks.filter((item) => item.kind === trajectoryMode)) {
    const segmentation = track.kind === 'segmentation'
    const [red, green, blue] = trackColor(track.trackId, segmentation)
    const points = track.points
    const tailDenominator = Math.max(points.length - 1, 1)
    for (let pointIndex = 1; pointIndex < points.length; pointIndex += 1) {
      const previous = points[pointIndex - 1]
      const point = points[pointIndex]
      const fade = (pointIndex / tailDenominator) ** 1.5
      context.globalAlpha = 1
      context.strokeStyle = `rgb(${Math.round(red * fade)} ${Math.round(green * fade)} ${Math.round(blue * fade)})`
      context.lineWidth = segmentation ? 2 : 1
      context.beginPath()
      context.moveTo(previous.cx, previous.cy)
      context.lineTo(point.cx, point.cy)
      context.stroke()
    }
    const current = points.find((point) => point.frameIndex === motion.heatmapIndex)
    if (!current) continue
    const color = `rgba(${red}, ${green}, ${blue}, 0.95)`
    context.globalAlpha = 1
    context.strokeStyle = color
    context.lineWidth = current.interpolated ? 1 : 2
    if (current.interpolated) {
      context.beginPath()
      if (segmentation) {
        context.moveTo(current.cx - 5, current.cy - 5)
        context.lineTo(current.cx + 5, current.cy + 5)
        context.moveTo(current.cx - 5, current.cy + 5)
        context.lineTo(current.cx + 5, current.cy - 5)
      } else {
        context.moveTo(current.cx - 5, current.cy)
        context.lineTo(current.cx + 5, current.cy)
        context.moveTo(current.cx, current.cy - 5)
        context.lineTo(current.cx, current.cy + 5)
      }
      context.stroke()
    } else if (segmentation) {
      context.save()
      context.translate(current.cx, current.cy)
      context.rotate(Math.PI / 4)
      context.strokeRect(-5, -5, 10, 10)
      context.restore()
    } else {
      context.beginPath()
      context.arc(current.cx, current.cy, 6, 0, Math.PI * 2)
      context.stroke()
    }
    const label = `${segmentation ? 'S' : 'T'}${track.trackId}`
    context.font = '13px Arial, sans-serif'
    context.fillStyle = color
    context.fillText(
      label,
      current.cx + 8,
      segmentation ? current.cy + 14 : current.cy - 8,
    )
  }
  context.restore()
}

async function prepareMotion(message: MotionRequest) {
  const motion = message.motion
  const heatmap = motion?.heatmapData
    ? await decodeHeatmap(motion.heatmapData)
    : null
  if (!motion || !heatmap) {
    workerScope.postMessage({
      type: 'prepared',
      kind: 'motion',
      jobId: message.jobId,
      frameNumber: message.frameNumber,
      width: 0,
      height: 0,
      bitmap: null,
      motion,
    })
    return
  }

  const { width, height, values } = heatmap
  const pixels = new Uint8ClampedArray(width * height * 4)
  for (let pixelIndex = 0; pixelIndex < values.length; pixelIndex += 1) {
    const value = values[pixelIndex]
    const target = pixelIndex * 4
    const [red, green, blue] = JET_COLORS[value]
    pixels[target] = red
    pixels[target + 1] = green
    pixels[target + 2] = blue
    // The original dashboard draws an opaque Jet heatmap canvas at 50%
    // opacity. Baking that opacity here preserves the same composition while
    // keeping each replay frame ready for a single drawImage call.
    pixels[target + 3] = 128
  }
  const canvas = new OffscreenCanvas(width, height)
  const context = canvas.getContext('2d')
  if (!context) throw new Error('Motion capture overlay canvas is unavailable')
  context.putImageData(new ImageData(pixels, width, height), 0, 0)
  drawMotionTracks(context, motion, message.trajectoryMode)
  const bitmap = canvas.transferToImageBitmap()
  workerScope.postMessage({
    type: 'prepared',
    kind: 'motion',
    jobId: message.jobId,
    frameNumber: message.frameNumber,
    width,
    height,
    bitmap,
    motion: { ...motion, heatmapData: null },
  }, [bitmap])
}

workerScope.onmessage = (event) => {
  const message = event.data
  void (message.type === 'segmentation'
    ? prepareSegmentation(message)
    : prepareMotion(message))
    .catch((error) => {
      workerScope.postMessage({
        type: 'failed',
        kind: message.type,
        jobId: message.jobId,
        frameNumber: message.frameNumber,
        message: error instanceof Error ? error.message : String(error),
      })
    })
}
