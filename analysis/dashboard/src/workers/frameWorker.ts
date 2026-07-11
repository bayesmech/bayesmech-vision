import { asNumber, getProtoRoot, protoToObject, splitDelimited, vectorToPlain } from '$lib/protoRoot';
import type { DecodedFrameAsset, FrameResponseMeta, WorkerRequest, WorkerResponse } from '$lib/types';

type PerceiverFrameObject = Record<string, any>;

const root = getProtoRoot();
const PerceiverDataFrame = root.lookupType('bayesmech.vision.PerceiverDataFrame');
let cachedIntrinsics: DecodedFrameAsset['cameraIntrinsics'];

self.onmessage = async (event: MessageEvent<WorkerRequest>) => {
  const request = event.data;
  try {
    if (request.type === 'reset-recording') {
      cachedIntrinsics = undefined;
      respond(request, 'recording-reset', {}, []);
      return;
    }
    if (request.type === 'decode-frame-range') {
      const payload = request.payload as {
        bytes: ArrayBuffer;
        metas: FrameResponseMeta[];
        delimited: boolean;
      };
      const records = payload.delimited ? splitDelimited(payload.bytes) : [new Uint8Array(payload.bytes)];
      const transfer: Transferable[] = [];
      const frames: DecodedFrameAsset[] = [];
      for (let index = 0; index < records.length; index += 1) {
        const frame = await decodeFrame(records[index], payload.metas[index], index);
        if (frame.rgbBitmap) transfer.push(frame.rgbBitmap);
        if (frame.depthBitmap) transfer.push(frame.depthBitmap);
        frames.push(frame);
      }
      respond(request, 'frame-range-decoded', { frames }, transfer);
      return;
    }
    respond(request, `${request.type}:ignored`, {}, []);
  } catch (error) {
    fail(request, error);
  }
};

async function decodeFrame(bytes: Uint8Array, meta: FrameResponseMeta | undefined, fallbackIndex: number): Promise<DecodedFrameAsset> {
  const object = protoToObject<PerceiverFrameObject>(PerceiverDataFrame, bytes);
  const fid = object.frame_identifier ?? {};
  const rgb = object.rgb_frame;
  const depth = object.depth_frame;
  const frameIndex = meta?.frame_index ?? fallbackIndex;
  const frameNumber = meta?.frame_number ?? asNumber(fid.frame_number, frameIndex);
  const timestampNs = meta?.timestamp_ns ?? asNumber(fid.timestamp_ns);
  const relativeTimestampNs = meta?.relative_timestamp_ns ?? 0;
  const rgbBytes = rgb?.data instanceof Uint8Array ? rgb.data : undefined;
  const rgbSize = rgbBytes ? jpegDimensions(rgbBytes) : undefined;
  const rgbWidth = asNumber(rgb?.width, rgbSize?.width ?? 0);
  const rgbHeight = asNumber(rgb?.height, rgbSize?.height ?? 0);
  const rgbBitmap = rgbBytes && rgbBytes.byteLength ? await imageBitmapFromBytes(rgbBytes, rgb?.format) : undefined;
  const intrinsics = decodeIntrinsics(object.camera_intrinsics, rgbWidth || rgbBitmap?.width, rgbHeight || rgbBitmap?.height);
  const geometry = decodeGeometry(object.inferred_geometry);
  const pose = object.camera_pose
    ? {
        position: vectorToPlain(object.camera_pose.position),
        rotation: object.camera_pose.rotation
          ? {
              x: asNumber(object.camera_pose.rotation.x),
              y: asNumber(object.camera_pose.rotation.y),
              z: asNumber(object.camera_pose.rotation.z),
              w: asNumber(object.camera_pose.rotation.w)
            }
          : undefined
      }
    : undefined;

  return {
    frameIndex,
    frameNumber,
    timestampNs,
    relativeTimestampNs,
    metadata: {
      frameIndex,
      frameNumber,
      timestampNs,
      relativeTimestampNs,
      deviceId: String(fid.device_id ?? ''),
      hasRgb: Boolean(rgbBytes?.byteLength),
      hasDepth: Boolean(depth?.data?.byteLength),
      hasPose: Boolean(object.camera_pose),
      hasImu: Boolean(object.imu_data),
      hasGps: Boolean(object.gps_location),
      hasGeometry: Boolean(geometry?.pointCloud.length || geometry?.planes.length),
      rgbWidth: rgbWidth || undefined,
      rgbHeight: rgbHeight || undefined,
      depthWidth: asNumber(depth?.width) || undefined,
      depthHeight: asNumber(depth?.height) || undefined
    },
    rgbBitmap,
    rgbWidth: rgbWidth || rgbBitmap?.width,
    rgbHeight: rgbHeight || rgbBitmap?.height,
    cameraIntrinsics: intrinsics,
    pose,
    imu: decodeImu(object.imu_data),
    gps: decodeGps(object.gps_location),
    geometry
  };
}

function decodeIntrinsics(value: any, rgbWidth?: number, rgbHeight?: number): DecodedFrameAsset['cameraIntrinsics'] {
  if (value) {
    cachedIntrinsics = {
      fx: asNumber(value.fx),
      fy: asNumber(value.fy),
      cx: asNumber(value.cx),
      cy: asNumber(value.cy),
      imageWidth: asNumber(value.image_width, rgbWidth ?? 0),
      imageHeight: asNumber(value.image_height, rgbHeight ?? 0),
      depthWidth: asNumber(value.depth_width) || undefined,
      depthHeight: asNumber(value.depth_height) || undefined
    };
  }
  if (!cachedIntrinsics) return undefined;
  if (!rgbWidth || !rgbHeight || !cachedIntrinsics.imageWidth || !cachedIntrinsics.imageHeight) return cachedIntrinsics;
  if (cachedIntrinsics.imageWidth === rgbWidth && cachedIntrinsics.imageHeight === rgbHeight) return cachedIntrinsics;
  const scaleX = rgbWidth / Math.max(cachedIntrinsics.imageWidth, 1);
  const scaleY = rgbHeight / Math.max(cachedIntrinsics.imageHeight, 1);
  return {
    ...cachedIntrinsics,
    fx: cachedIntrinsics.fx * scaleX,
    fy: cachedIntrinsics.fy * scaleY,
    cx: cachedIntrinsics.cx * scaleX,
    cy: cachedIntrinsics.cy * scaleY,
    imageWidth: rgbWidth,
    imageHeight: rgbHeight
  };
}

async function imageBitmapFromBytes(bytes: Uint8Array, format: number | undefined): Promise<ImageBitmap | undefined> {
  if (typeof createImageBitmap !== 'function') return undefined;
  const type = format === 4 ? 'image/jpeg' : 'application/octet-stream';
  try {
    const part = bytes.buffer.slice(bytes.byteOffset, bytes.byteOffset + bytes.byteLength) as ArrayBuffer;
    return await createImageBitmap(new Blob([part], { type }));
  } catch {
    return undefined;
  }
}

function jpegDimensions(bytes: Uint8Array): { width: number; height: number } | undefined {
  if (bytes.length < 4 || bytes[0] !== 0xff || bytes[1] !== 0xd8) return undefined;
  let offset = 2;
  while (offset + 9 < bytes.length) {
    if (bytes[offset] !== 0xff) {
      offset += 1;
      continue;
    }
    const marker = bytes[offset + 1];
    const length = (bytes[offset + 2] << 8) + bytes[offset + 3];
    if (length < 2) return undefined;
    if (marker >= 0xc0 && marker <= 0xc3) {
      return {
        height: (bytes[offset + 5] << 8) + bytes[offset + 6],
        width: (bytes[offset + 7] << 8) + bytes[offset + 8]
      };
    }
    offset += 2 + length;
  }
  return undefined;
}

function decodeImu(value: any): DecodedFrameAsset['imu'] {
  if (!value) return undefined;
  return {
    angularVelocity: vectorToPlain(value.angular_velocity),
    linearAcceleration: vectorToPlain(value.linear_acceleration),
    gravity: vectorToPlain(value.gravity),
    magneticField: vectorToPlain(value.magnetic_field)
  };
}

function decodeGps(value: any): DecodedFrameAsset['gps'] {
  if (!value) return undefined;
  return {
    latitude: asNumber(value.latitude),
    longitude: asNumber(value.longitude),
    altitude: asNumber(value.altitude),
    accuracy: asNumber(value.accuracy),
    bearing: asNumber(value.bearing),
    speed: asNumber(value.speed),
    timestampMs: asNumber(value.timestamp_ms)
  };
}

function decodeGeometry(value: any): DecodedFrameAsset['geometry'] {
  if (!value) return undefined;
  return {
    pointCloud: (value.point_cloud ?? []).slice(0, 5000).map((point: any) => ({
      ...(vectorToPlain(point.point) ?? { x: 0, y: 0, z: 0 }),
      confidence: asNumber(point.confidence)
    })),
    planes: (value.planes ?? []).map((plane: any) => ({
      type: asNumber(plane.type),
      extentX: asNumber(plane.extent_x),
      extentZ: asNumber(plane.extent_z),
      polygon: (plane.polygon ?? []).map(vectorToPlain).filter(Boolean),
      center: plane.center_pose ? { position: vectorToPlain(plane.center_pose.position) } : undefined
    }))
  };
}

function respond<T>(request: WorkerRequest, type: string, payload: T, transfer: Transferable[]) {
  const response: WorkerResponse<T> = {
    requestId: request.requestId,
    recordingId: request.recordingId,
    generation: request.generation,
    type,
    ok: true,
    payload
  };
  self.postMessage(response, { transfer });
}

function fail(request: WorkerRequest, error: unknown) {
  const response: WorkerResponse = {
    requestId: request.requestId,
    recordingId: request.recordingId,
    generation: request.generation,
    type: `${request.type}:error`,
    ok: false,
    error: error instanceof Error ? error.message : String(error)
  };
  self.postMessage(response);
}
