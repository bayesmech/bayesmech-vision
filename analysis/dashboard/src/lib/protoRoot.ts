import protobuf from 'protobufjs';

import primitivesProto from '../../../../proto/primitives.proto?raw';
import spatialProto from '../../../../proto/spatial.proto?raw';
import perceiverProto from '../../../../proto/perceiver.proto?raw';
import segmentationProto from '../../../../proto/segmentation.proto?raw';
import motioncapProto from '../../../../proto/motioncap.proto?raw';
import pongtownProto from '../../../../proto/pongtown.proto?raw';
import idoslamProto from '../../../../proto/idoslam.proto?raw';
import insightgenProto from '../../../../proto/insightgen.proto?raw';

let cachedRoot: protobuf.Root | null = null;

export function getProtoRoot(): protobuf.Root {
  if (cachedRoot) return cachedRoot;
  const root = new protobuf.Root();
  for (const source of [
    primitivesProto,
    spatialProto,
    perceiverProto,
    segmentationProto,
    motioncapProto,
    pongtownProto,
    idoslamProto,
    insightgenProto
  ]) {
    protobuf.parse(source, root, { keepCase: true });
  }
  root.resolveAll();
  cachedRoot = root;
  return root;
}

export function protoToObject<T>(messageType: protobuf.Type, bytes: Uint8Array): T {
  return messageType.toObject(messageType.decode(bytes), {
    bytes: Uint8Array,
    defaults: false,
    arrays: true,
    objects: true,
    longs: Number,
    enums: Number
  }) as T;
}

export function splitDelimited(buffer: ArrayBuffer | Uint8Array): Uint8Array[] {
  const bytes = buffer instanceof Uint8Array ? buffer : new Uint8Array(buffer);
  if (bytes.byteLength < 4) return bytes.byteLength ? [bytes] : [];
  const records: Uint8Array[] = [];
  let offset = 0;
  while (offset + 4 <= bytes.byteLength) {
    const length =
      (bytes[offset] << 24) |
      (bytes[offset + 1] << 16) |
      (bytes[offset + 2] << 8) |
      bytes[offset + 3];
    if (length <= 0 || offset + 4 + length > bytes.byteLength) {
      return records.length ? records : [bytes];
    }
    records.push(bytes.subarray(offset + 4, offset + 4 + length));
    offset += 4 + length;
  }
  return records;
}

export function asNumber(value: unknown, fallback = 0): number {
  if (typeof value === 'number' && Number.isFinite(value)) return value;
  if (typeof value === 'string') {
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed : fallback;
  }
  if (value && typeof value === 'object' && 'toNumber' in value && typeof value.toNumber === 'function') {
    const parsed = value.toNumber();
    return Number.isFinite(parsed) ? parsed : fallback;
  }
  return fallback;
}

export function vectorToPlain(value: unknown): { x: number; y: number; z: number } | undefined {
  if (!value || typeof value !== 'object') return undefined;
  const row = value as Record<string, unknown>;
  return {
    x: asNumber(row.x),
    y: asNumber(row.y),
    z: asNumber(row.z)
  };
}
