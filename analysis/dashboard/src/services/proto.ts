/**
 * Protobuf decode helpers for the binary dashboard WebSocket protocol.
 *
 * Wire format from server:
 *   Binary: 1-byte prefix + length-delimited protobuf(s)
 *     0x01 = PerceiverDataFrame(s)
 *     0x02 = SegmentationResponse(s)
 *     0x03 = PongtownResponse(s)
 *   Text (JSON): stats / control responses
 *
 * Length-delimited: [uint32 BE = N] [N bytes of proto] repeated
 */

import pako from 'pako'
import { bayesmech } from '../proto/bundle'

const {
  PerceiverDataFrame,
  SegmentationResponse,
  IdoSlamResponse,
  MotionCaptureResponse,
  PongtownResponse,
} = bayesmech.vision

export const PREFIX_FRAME = 0x01
export const PREFIX_ANNOTATION = 0x02
export const PREFIX_PONGTOWN = 0x03

/**
 * Decode all length-delimited messages from a buffer (after the prefix byte).
 */
function readDelimited<T>(
  buf: Uint8Array,
  decode: (reader: Uint8Array) => T,
): T[] {
  const results: T[] = []
  const view = new DataView(buf.buffer, buf.byteOffset, buf.byteLength)
  let offset = 0

  while (offset + 4 <= buf.length) {
    const len = view.getUint32(offset)
    offset += 4
    if (offset + len > buf.length) break
    const slice = buf.subarray(offset, offset + len)
    results.push(decode(slice))
    offset += len
  }
  return results
}

export function decodeFrames(payload: Uint8Array): bayesmech.vision.PerceiverDataFrame[] {
  return readDelimited(payload, (b) => PerceiverDataFrame.decode(b))
}

export function decodeAnnotations(payload: Uint8Array): bayesmech.vision.SegmentationResponse[] {
  return readDelimited(payload, (b) => SegmentationResponse.decode(b))
}

export function decodeMotioncapRecords(payload: Uint8Array): bayesmech.vision.MotionCaptureResponse[] {
  return readDelimited(payload, (b) => MotionCaptureResponse.decode(b))
}

export function decodePongtownRecords(payload: Uint8Array): bayesmech.vision.PongtownResponse[] {
  return readDelimited(payload, (b) => PongtownResponse.decode(b))
}

export function decodeIdoSlamRecords(payload: Uint8Array): bayesmech.vision.IdoSlamResponse[] {
  return readDelimited(payload, (b) => IdoSlamResponse.decode(b))
}

export function decodeIdoSlamResponse(payload: Uint8Array): bayesmech.vision.IdoSlamResponse {
  return IdoSlamResponse.decode(payload)
}

/**
 * Create an object URL from raw JPEG/PNG bytes. Caller must revoke when done.
 */
export function bytesToBlobUrl(data: Uint8Array, mime: string = 'image/jpeg'): string {
  const copy = new Uint8Array(data)
  const blob = new Blob([copy], { type: mime })
  return URL.createObjectURL(blob)
}

/**
 * Decode a compressed binary mask: [H:u32le][W:u32le][zlib(np.packbits(mask))].
 * Returns the dimensions and a flat Uint8Array where 1 = masked pixel.
 */
export function decodeMask(maskData: Uint8Array): { height: number; width: number; mask: Uint8Array } {
  const view = new DataView(maskData.buffer, maskData.byteOffset, maskData.byteLength)
  const height = view.getUint32(0, true)
  const width = view.getUint32(4, true)
  const compressed = maskData.subarray(8)
  const packed = pako.inflate(compressed)

  // Unpackbits: each byte has 8 bits, MSB first (matching numpy packbits)
  const totalBits = height * width
  const mask = new Uint8Array(totalBits)
  for (let i = 0; i < totalBits; i++) {
    const byteIdx = i >>> 3
    const bitIdx = 7 - (i & 7)
    mask[i] = (packed[byteIdx] >> bitIdx) & 1
  }
  return { height, width, mask }
}

/**
 * Decode a compressed motion heatmap: [H:u32le][W:u32le][zlib(uint8 heatmap)].
 * Returns the full-resolution flat uint8 heatmap without expanding other frames.
 */
export function decodeMotionHeatmapData(
  heatmapData: Uint8Array,
): { height: number; width: number; values: Uint8Array } {
  if (heatmapData.length < 8) {
    throw new Error('Motion heatmap payload is too small')
  }

  const view = new DataView(heatmapData.buffer, heatmapData.byteOffset, heatmapData.byteLength)
  const height = view.getUint32(0, true)
  const width = view.getUint32(4, true)
  const total = height * width
  const values = pako.inflate(heatmapData.subarray(8))

  if (values.length < total) {
    throw new Error(`Motion heatmap payload has ${values.length} pixels, expected ${total}`)
  }

  return { height, width, values: values.subarray(0, total) }
}

export type MaskColor = [number, number, number, number]

const SEMANTIC_MASK_COLORS: { token: string; color: MaskColor }[] = [
  { token: 'person', color: [145, 145, 145, 150] },
  { token: 'wooden', color: [92, 55, 28, 150] },
  { token: 'black', color: [24, 24, 24, 165] },
  { token: 'white', color: [248, 248, 248, 150] },
  { token: 'red', color: [239, 68, 68, 145] },
  { token: 'green', color: [34, 197, 94, 145] },
  { token: 'blue', color: [59, 130, 246, 145] },
  { token: 'brown', color: [150, 91, 42, 150] },
]

const FALLBACK_MASK_COLORS: MaskColor[] = [
  [255, 99, 132, 140],
  [54, 162, 235, 140],
  [255, 206, 86, 140],
  [75, 192, 192, 140],
  [153, 102, 255, 140],
  [255, 159, 64, 140],
  [46, 204, 113, 140],
  [231, 76, 60, 140],
  [52, 152, 219, 140],
  [241, 196, 15, 140],
  [155, 89, 182, 140],
  [26, 188, 156, 140],
  [230, 126, 34, 140],
  [149, 165, 166, 140],
  [244, 114, 182, 140],
  [14, 165, 233, 140],
  [132, 204, 22, 140],
  [168, 85, 247, 140],
  [251, 146, 60, 140],
  [45, 212, 191, 140],
  [250, 204, 21, 140],
  [129, 140, 248, 140],
]

const hashMaskKey = (label: string, objectId: number): number => {
  const key = `${label.toLowerCase()}#${objectId}`
  let hash = 2166136261
  for (let i = 0; i < key.length; i += 1) {
    hash ^= key.charCodeAt(i)
    hash = Math.imul(hash, 16777619)
  }
  return hash >>> 0
}

export function maskColorForLabel(label: string | null | undefined, objectId = 0): MaskColor {
  const normalized = (label ?? '').toLowerCase()
  const personColor = SEMANTIC_MASK_COLORS[0]
  if (normalized.includes(personColor.token)) return personColor.color

  let bestMatch: { index: number; color: MaskColor } | null = null
  for (const { token, color } of SEMANTIC_MASK_COLORS.slice(1)) {
    const index = normalized.indexOf(token)
    if (index >= 0 && (!bestMatch || index < bestMatch.index)) {
      bestMatch = { index, color }
    }
  }
  if (bestMatch) return bestMatch.color

  return FALLBACK_MASK_COLORS[hashMaskKey(normalized, objectId) % FALLBACK_MASK_COLORS.length]
}

/**
 * Composite all masks from a SegmentationResponse into a single RGBA overlay.
 * Returns a data URL suitable for <img src>.
 */
export function compositeMasksToDataUrl(
  masks: bayesmech.vision.SegmentationResponse.ISegmentationMask[],
): string | null {
  let width = 0, height = 0
  const decoded: { color: MaskColor; mask: Uint8Array }[] = []

  for (const m of masks) {
    if (!m.maskData || m.maskData.length < 9) continue
    const d = decodeMask(m.maskData as Uint8Array)
    const objectId = m.objectId ?? 0
    width = d.width
    height = d.height
    decoded.push({ color: maskColorForLabel(m.label, objectId), mask: d.mask })
  }

  if (decoded.length === 0 || width === 0 || height === 0) return null

  const canvas = document.createElement('canvas')
  canvas.width = width
  canvas.height = height
  const ctx = canvas.getContext('2d')
  if (!ctx) return null

  const imageData = ctx.createImageData(width, height)
  const px = imageData.data
  const n = width * height

  for (const { color, mask } of decoded) {
    for (let i = 0; i < n; i++) {
      if (mask[i]) {
        const off = i * 4
        px[off] = color[0]
        px[off + 1] = color[1]
        px[off + 2] = color[2]
        px[off + 3] = color[3]
      }
    }
  }

  ctx.putImageData(imageData, 0, 0)
  return canvas.toDataURL('image/png')
}
