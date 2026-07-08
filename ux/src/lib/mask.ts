import type { SegMask } from '../types'

export type DecodedMask = {
  width: number
  height: number
  // One byte (0 or 1) per pixel, row-major.
  mask: Uint8Array
}

function base64ToBytes(base64: string): Uint8Array {
  const binary = atob(base64)
  const bytes = new Uint8Array(binary.length)
  for (let i = 0; i < binary.length; i += 1) bytes[i] = binary.charCodeAt(i)
  return bytes
}

// Inflate a zlib (RFC1950) stream. DecompressionStream('deflate') expects the
// zlib header that Python's zlib.compress emits.
async function inflate(bytes: Uint8Array): Promise<Uint8Array> {
  const stream = new Blob([new Uint8Array(bytes)]).stream().pipeThrough(new DecompressionStream('deflate'))
  const buffer = await new Response(stream).arrayBuffer()
  return new Uint8Array(buffer)
}

// Decode a mask payload: [H:u32le][W:u32le][zlib(np.packbits(mask))]. packbits is
// MSB-first, matching numpy, so bit (7 - i%8) of byte i>>3 is pixel i.
export async function decodeMask(maskData: string): Promise<DecodedMask | null> {
  const raw = base64ToBytes(maskData)
  if (raw.length < 8) return null

  const view = new DataView(raw.buffer, raw.byteOffset, raw.byteLength)
  const height = view.getUint32(0, true)
  const width = view.getUint32(4, true)
  const total = width * height
  if (total <= 0 || total > 64_000_000) return null

  let packed: Uint8Array
  try {
    packed = await inflate(raw.subarray(8))
  } catch {
    return null
  }

  const mask = new Uint8Array(total)
  for (let i = 0; i < total; i += 1) {
    const byte = packed[i >> 3] ?? 0
    mask[i] = (byte >> (7 - (i & 7))) & 1
  }
  return { width, height, mask }
}

// Distinct, readable overlay colors. Indexed by object id so an object keeps its
// color across frames (SAM object ids are stable within a recording).
const PALETTE: Array<[number, number, number]> = [
  [90, 169, 230], // blue
  [98, 210, 162], // teal
  [240, 179, 90], // amber
  [215, 104, 125], // rose
  [168, 132, 230], // violet
  [120, 200, 120], // green
  [235, 130, 90], // orange
  [90, 200, 220], // cyan
  [225, 120, 180], // pink
  [180, 190, 90], // lime
]

export function colorForObject(objectId: number): [number, number, number] {
  const index = ((objectId % PALETTE.length) + PALETTE.length) % PALETTE.length
  return PALETTE[index]
}

export type DecodedOverlay = DecodedMask & {
  objectId: number
  label: string
  color: [number, number, number]
}

export async function decodeMasks(masks: SegMask[]): Promise<DecodedOverlay[]> {
  const decoded = await Promise.all(
    masks.map(async (mask) => {
      const result = await decodeMask(mask.maskData)
      if (!result) return null
      return { ...result, objectId: mask.objectId, label: mask.label, color: colorForObject(mask.objectId) }
    }),
  )
  return decoded.filter((item): item is DecodedOverlay => item !== null)
}
