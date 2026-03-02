/**
 * Protobuf decode helpers for the binary dashboard WebSocket protocol.
 *
 * Wire format from server:
 *   Binary: 1-byte prefix + length-delimited protobuf(s)
 *     0x01 = PerceiverDataFrame(s)
 *     0x02 = SegmentationResponse(s)
 *   Text (JSON): stats / control responses
 *
 * Length-delimited: [uint32 BE = N] [N bytes of proto] repeated
 */

import pako from 'pako'
import { bayesmech } from '../proto/bundle'

const { PerceiverDataFrame, SegmentationResponse } = bayesmech.vision

export const PREFIX_FRAME = 0x01
export const PREFIX_ANNOTATION = 0x02

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

// Object colors matching annotate_direct.py (RGBA)
const MASK_COLORS = [
  [255, 0, 0, 128], [0, 255, 0, 128], [0, 0, 255, 128],
  [255, 255, 0, 128], [255, 0, 255, 128], [0, 255, 255, 128],
]

/**
 * Composite all masks from a SegmentationResponse into a single RGBA overlay.
 * Returns a data URL suitable for <img src>.
 */
export function compositeMasksToDataUrl(
  masks: bayesmech.vision.SegmentationResponse.ISegmentationMask[],
): string | null {
  let width = 0, height = 0
  const decoded: { objId: number; mask: Uint8Array }[] = []

  for (const m of masks) {
    if (!m.maskData || m.maskData.length < 9) continue
    const d = decodeMask(m.maskData as Uint8Array)
    width = d.width
    height = d.height
    decoded.push({ objId: m.objectId ?? 0, mask: d.mask })
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

  for (const { objId, mask } of decoded) {
    const color = MASK_COLORS[(objId - 1) % MASK_COLORS.length]
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
