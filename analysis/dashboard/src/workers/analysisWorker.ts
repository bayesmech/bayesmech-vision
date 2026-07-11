import pako from 'pako';
import { asNumber, getProtoRoot, protoToObject, splitDelimited, vectorToPlain } from '$lib/protoRoot';
import type {
  LocalizationAsset,
  ModelMusingsData,
  MotionOverlayAsset,
  MotionTrack,
  MotionTrackPoint,
  Pong3DState,
  PongOverlayAsset,
  PongOverlayMode,
  SegmentationAsset,
  SegmentationLegendItem,
  WorkerRequest,
  WorkerResponse
} from '$lib/types';

type SegRecord = {
  frameNumber: number;
  timestampNs: number;
  masks: Array<Record<string, any>>;
  triggerType: number;
};

type PongRecord = Record<string, any> & {
  frameNumber: number;
  timestampNs: number;
  frameIndex: number;
};

type MotionFrameRecord = {
  frameNumber: number;
  timestampNs: number;
  frameIndex: number;
  heatmapData?: Uint8Array;
};

type RecordingAnalysisState = {
  segmentation: SegRecord[];
  motionRecords: Uint8Array[];
  motionFrameCache: Map<number, MotionFrameRecord>;
  raftTracks: MotionTrack[];
  segmentationTracks: MotionTrack[];
  pongFrames: PongRecord[];
  pongSummary?: Record<string, any>;
  pong3d?: Pong3DState;
  localization?: LocalizationAsset;
};

const root = getProtoRoot();
const SegmentationResponse = root.lookupType('bayesmech.vision.SegmentationResponse');
const MotionCaptureResponse = root.lookupType('bayesmech.vision.MotionCaptureResponse');
const PongtownResponse = root.lookupType('bayesmech.vision.PongtownResponse');
const IdoSlamResponse = root.lookupType('bayesmech.vision.IdoSlamResponse');
const GensparkSummary = root.lookupType('bayesmech.vision.GensparkSummary');
const ChatHistory = root.lookupType('bayesmech.vision.ChatHistory');

const states = new Map<string, RecordingAnalysisState>();

self.onmessage = async (event: MessageEvent<WorkerRequest>) => {
  const request = event.data;
  try {
    switch (request.type) {
      case 'reset-recording':
        states.delete(request.recordingId);
        respond(request, 'recording-reset', {});
        return;
      case 'decode-segmentation':
        decodeSegmentation(request);
        return;
      case 'get-segmentation':
        await getSegmentation(request);
        return;
      case 'decode-motioncap':
        decodeMotioncap(request);
        return;
      case 'get-motioncap':
        getMotioncap(request);
        return;
      case 'get-motioncap-heatmap':
        await getMotioncapHeatmap(request);
        return;
      case 'decode-pongtown':
        decodePongtown(request);
        return;
      case 'get-pongtown':
        getPongtown(request);
        return;
      case 'decode-localization':
        decodeLocalization(request);
        return;
      case 'decode-model':
        decodeModel(request);
        return;
      default:
        respond(request, `${request.type}:ignored`, {});
    }
  } catch (error) {
    fail(request, error);
  }
};

function stateFor(recordingId: string): RecordingAnalysisState {
  let state = states.get(recordingId);
  if (!state) {
    state = {
      segmentation: [],
      motionRecords: [],
      motionFrameCache: new Map(),
      raftTracks: [],
      segmentationTracks: [],
      pongFrames: []
    };
    states.set(recordingId, state);
  }
  return state;
}

function decodeSegmentation(request: WorkerRequest) {
  const payload = request.payload as { bytes: ArrayBuffer };
  const records = splitDelimited(payload.bytes).map((bytes) => {
    const row = protoToObject<Record<string, any>>(SegmentationResponse, bytes);
    const fid = row.frame_identifier ?? {};
    return {
      frameNumber: asNumber(fid.frame_number),
      timestampNs: asNumber(fid.timestamp_ns),
      masks: row.masks ?? [],
      triggerType: asNumber(row.trigger_type)
    };
  });
  const state = stateFor(request.recordingId);
  const byFrame = new Map(state.segmentation.map((record) => [record.frameNumber, record]));
  records.forEach((record) => byFrame.set(record.frameNumber, record));
  state.segmentation = [...byFrame.values()].sort((a, b) => a.frameNumber - b.frameNumber);
  respond(request, 'segmentation-decoded', {
    count: state.segmentation.length,
    firstFrameNumber: state.segmentation[0]?.frameNumber,
    lastFrameNumber: state.segmentation.at(-1)?.frameNumber
  });
}

async function getSegmentation(request: WorkerRequest) {
  const payload = request.payload as { frameNumber: number; matchMode: 'exact' | 'floor' | 'nearest'; toleranceFrames?: number };
  const state = stateFor(request.recordingId);
  const record = matchSegmentation(state.segmentation, payload.frameNumber, payload.matchMode, payload.toleranceFrames ?? 30);
  if (!record) {
    respond(request, 'segmentation-current', { asset: undefined });
    return;
  }
  const { bitmap, width, height, legend, masks } = await compositeSegmentation(record);
  const asset: SegmentationAsset = {
    frameNumber: record.frameNumber,
    timestampNs: record.timestampNs,
    matchMode: payload.matchMode,
    bitmap,
    width,
    height,
    legend,
    masks
  };
  const transfers: Transferable[] = [];
  if (bitmap) transfers.push(bitmap);
  for (const mask of masks) transfers.push(mask.values.buffer as ArrayBuffer);
  respond(request, 'segmentation-current', { asset }, transfers);
}

function matchSegmentation(records: SegRecord[], frameNumber: number, mode: 'exact' | 'floor' | 'nearest', toleranceFrames: number): SegRecord | undefined {
  if (mode === 'exact') return records.find((record) => record.frameNumber === frameNumber);
  if (mode === 'floor') {
    let match: SegRecord | undefined;
    for (const record of records) {
      if (record.frameNumber <= frameNumber) match = record;
      else break;
    }
    return match;
  }
  let best: SegRecord | undefined;
  let bestDelta = Infinity;
  for (const record of records) {
    const delta = Math.abs(record.frameNumber - frameNumber);
    if (delta < bestDelta) {
      best = record;
      bestDelta = delta;
    }
  }
  return bestDelta <= toleranceFrames ? best : undefined;
}

async function compositeSegmentation(record: SegRecord): Promise<{
  bitmap?: ImageBitmap;
  width?: number;
  height?: number;
  legend: SegmentationLegendItem[];
  masks: NonNullable<SegmentationAsset['masks']>;
}> {
  const decoded = (
    await Promise.all(record.masks.map((mask, index) => decodeMask(mask, index)))
  ).filter(Boolean) as Array<{
      width: number;
      height: number;
      values?: Uint8Array;
      bitmap?: ImageBitmap;
      rgba: [number, number, number, number];
      legend: SegmentationLegendItem;
    }>;
  const first = decoded.find((mask) => mask.width && mask.height);
  if (!first) return { legend: record.masks.map((mask, index) => legendForMask(mask, index)), masks: [] };
  const canvas = new OffscreenCanvas(first.width, first.height);
  const ctx = canvas.getContext('2d');
  const masks = decoded
    .filter((mask) => mask.values && mask.width && mask.height)
    .map((mask) => ({
      objectId: mask.legend.objectId,
      label: mask.legend.label,
      confidence: mask.legend.confidence,
      pixelCount: mask.legend.pixelCount,
      width: mask.width,
      height: mask.height,
      values: mask.values!
    }));
  if (!ctx) return { legend: decoded.map((mask) => mask.legend), width: first.width, height: first.height, masks };
  const imageData = ctx.createImageData(first.width, first.height);
  for (const mask of decoded) {
    if (mask.bitmap) {
      ctx.globalAlpha = 0.58;
      ctx.drawImage(mask.bitmap, 0, 0, first.width, first.height);
      mask.bitmap.close();
      continue;
    }
    if (!mask.values) continue;
    const [r, g, b, a] = mask.rgba;
    const total = Math.min(first.width * first.height, mask.values.length);
    for (let i = 0; i < total; i += 1) {
      const value = mask.values[i];
      if (!value) continue;
      const px = i * 4;
      const alpha = Math.min(255, Math.round((a * value) / 255));
      imageData.data[px] = blendChannel(imageData.data[px], r, alpha);
      imageData.data[px + 1] = blendChannel(imageData.data[px + 1], g, alpha);
      imageData.data[px + 2] = blendChannel(imageData.data[px + 2], b, alpha);
      imageData.data[px + 3] = Math.max(imageData.data[px + 3], alpha);
    }
  }
  ctx.putImageData(imageData, 0, 0);
  const bitmap = await canvas.convertToBlob().then((blob) => createImageBitmap(blob));
  return {
    bitmap,
    width: first.width,
    height: first.height,
    legend: decoded.map((mask) => mask.legend),
    masks
  };
}

function blendChannel(existing: number, next: number, alpha: number): number {
  const ratio = alpha / 255;
  return Math.round(existing * (1 - ratio) + next * ratio);
}

async function decodeMask(mask: Record<string, any>, index: number) {
  const data = mask.mask_data instanceof Uint8Array ? mask.mask_data : undefined;
  const rgba = rgbaForMask(mask, index);
  const legend = legendForMask(mask, index);
  if (!data || data.byteLength === 0) {
    return { width: 0, height: 0, rgba, legend };
  }
  if (isPng(data) && typeof createImageBitmap === 'function') {
    const part = data.buffer.slice(data.byteOffset, data.byteOffset + data.byteLength) as ArrayBuffer;
    const bitmap = await createImageBitmap(new Blob([part], { type: 'image/png' }));
    const values = await maskValuesFromBitmap(bitmap);
    return {
      width: bitmap.width,
      height: bitmap.height,
      bitmap,
      values,
      rgba,
      legend
    };
  }
  const grid = inflateGrid(data, true);
  if (!grid) return { width: 0, height: 0, rgba, legend };
  return {
    width: grid.width,
    height: grid.height,
    values: grid.values,
    rgba,
    legend
  };
}

async function maskValuesFromBitmap(bitmap: ImageBitmap): Promise<Uint8Array | undefined> {
  const canvas = new OffscreenCanvas(bitmap.width, bitmap.height);
  const ctx = canvas.getContext('2d');
  if (!ctx) return undefined;
  ctx.drawImage(bitmap, 0, 0);
  const data = ctx.getImageData(0, 0, bitmap.width, bitmap.height).data;
  const values = new Uint8Array(bitmap.width * bitmap.height);
  for (let i = 0; i < values.length; i += 1) {
    const px = i * 4;
    values[i] = data[px + 3] > 8 && data[px] + data[px + 1] + data[px + 2] > 8 ? 255 : 0;
  }
  return values;
}

function decodeMotioncap(request: WorkerRequest) {
  const payload = request.payload as { bytes: ArrayBuffer };
  const state = stateFor(request.recordingId);
  const records = splitDelimited(payload.bytes);
  state.motionRecords = records;
  state.motionFrameCache = new Map();
  let raftTracks: MotionTrack[] = [];
  let segmentationTracks: MotionTrack[] = [];
  let frameCount = 0;
  const firstCandidate = Math.max(0, records.length - 8);
  for (let index = records.length - 1; index >= firstCandidate; index -= 1) {
    const row = MotionCaptureResponse.toObject(MotionCaptureResponse.decode(records[index]), {
      defaults: false,
      arrays: true,
      objects: true,
      longs: Number,
      enums: Number
    }) as Record<string, any>;
    const isSummary = (row.tracks?.length ?? 0) > 0 || (row.segmentation_trajectories?.length ?? 0) > 0 || asNumber(row.total_frames) > 0;
    if (isSummary) {
      raftTracks = (row.tracks ?? []).map((track: any, idx: number) => decodeTrack(track, idx, 'T'));
      segmentationTracks = (row.segmentation_trajectories ?? []).map((track: any, idx: number) => decodeTrack(track, idx, 'S'));
      frameCount = asNumber(row.total_frames, records.length);
      break;
    }
  }
  state.raftTracks = raftTracks;
  state.segmentationTracks = segmentationTracks;
  respond(request, 'motioncap-decoded', {
    frameCount,
    tracks: raftTracks,
    segmentationTracks
  });
}

function getMotioncap(request: WorkerRequest) {
  const payload = request.payload as { frameNumber: number; frameIndex: number; mode: 'raft' | 'segmentation' };
  const state = stateFor(request.recordingId);
  const activeTracks = payload.mode === 'raft' ? state.raftTracks : state.segmentationTracks;
  const otherTracks = payload.mode === 'raft' ? state.segmentationTracks : state.raftTracks;
  const currentFrame = payload.frameIndex;
  const asset: MotionOverlayAsset = {
    frameNumber: payload.frameNumber,
    frameIndex: currentFrame,
    raftSegments: payload.mode === 'raft' ? tailTracks(activeTracks, currentFrame) : [],
    segmentationSegments: payload.mode === 'segmentation' ? tailTracks(activeTracks, currentFrame) : []
  };
  respond(request, 'motioncap-current', {
    asset,
    legendTracks: payload.mode === 'raft' ? state.raftTracks : state.segmentationTracks,
    inactiveTrackCount: otherTracks.length
  });
}

async function getMotioncapHeatmap(request: WorkerRequest) {
  const payload = request.payload as { frameNumber: number; frameIndex: number; maxWidth?: number };
  const state = stateFor(request.recordingId);
  const record = motionFrameFor(state, payload.frameIndex, payload.frameNumber);
  if (!record?.heatmapData) {
    respond(request, 'motioncap-heatmap-current', {
      frameNumber: payload.frameNumber,
      frameIndex: payload.frameIndex
    });
    return;
  }
  const heatmap = await heatmapBitmap(record.heatmapData, payload.maxWidth ?? 640);
  respond(
    request,
    'motioncap-heatmap-current',
    {
      frameNumber: record.frameNumber,
      frameIndex: payload.frameIndex,
      heatmapBitmap: heatmap?.bitmap,
      heatmapWidth: heatmap?.width,
      heatmapHeight: heatmap?.height
    },
    heatmap?.bitmap ? [heatmap.bitmap] : []
  );
}

function motionFrameFor(state: RecordingAnalysisState, frameIndex: number, frameNumber: number): MotionFrameRecord | undefined {
  const cached = state.motionFrameCache.get(frameIndex);
  if (cached) return cached;
  const preferred = decodeMotionFrameRecord(state, frameIndex);
  if (preferred && (preferred.frameNumber === frameNumber || preferred.heatmapData)) return preferred;
  const start = Math.max(0, frameIndex - 4);
  const end = Math.min(state.motionRecords.length, frameIndex + 5);
  for (let index = start; index < end; index += 1) {
    const record = decodeMotionFrameRecord(state, index);
    if (record?.frameNumber === frameNumber) return record;
  }
  return preferred;
}

function decodeMotionFrameRecord(state: RecordingAnalysisState, frameIndex: number): MotionFrameRecord | undefined {
  if (state.motionFrameCache.has(frameIndex)) return state.motionFrameCache.get(frameIndex);
  const bytes = state.motionRecords[frameIndex];
  if (!bytes) return undefined;
  const row = protoToObject<Record<string, any>>(MotionCaptureResponse, bytes);
  const isSummary = (row.tracks?.length ?? 0) > 0 || (row.segmentation_trajectories?.length ?? 0) > 0 || asNumber(row.total_frames) > 0;
  if (isSummary) return undefined;
  const fid = row.frame_identifier ?? {};
  const record: MotionFrameRecord = {
    frameNumber: asNumber(fid.frame_number, frameIndex),
    timestampNs: asNumber(fid.timestamp_ns),
    frameIndex,
    heatmapData: row.heatmap?.heatmap_data
  };
  state.motionFrameCache.set(frameIndex, record);
  if (state.motionFrameCache.size > 96) {
    const firstKey = state.motionFrameCache.keys().next().value;
    if (typeof firstKey === 'number') state.motionFrameCache.delete(firstKey);
  }
  return record;
}

function decodePongtown(request: WorkerRequest) {
  const payload = request.payload as { bytes: ArrayBuffer };
  const state = stateFor(request.recordingId);
  const frames: PongRecord[] = [];
  let summary: Record<string, any> | undefined;
  splitDelimited(payload.bytes).forEach((bytes, index) => {
    const row = protoToObject<Record<string, any>>(PongtownResponse, bytes);
    const fid = row.frame_identifier ?? {};
    const isSummary = (asNumber(fid.timestamp_ns) === 0 && asNumber(fid.frame_number) === 0) || row.global_table_pose?.has_pose;
    if (isSummary) {
      summary = row;
      return;
    }
    frames.push({
      ...row,
      frameNumber: asNumber(fid.frame_number),
      timestampNs: asNumber(fid.timestamp_ns),
      frameIndex: asNumber(row.frame_output?.frame_idx, index)
    });
  });
  state.pongFrames = frames;
  state.pongSummary = summary;
  state.pong3d = buildPong3d(summary, frames);
  respond(request, 'pongtown-decoded', {
    frameCount: frames.length,
    state3d: state.pong3d
  });
}

function getPongtown(request: WorkerRequest) {
  const payload = request.payload as { frameNumber: number; frameIndex: number; mode: PongOverlayMode };
  const state = stateFor(request.recordingId);
  const record = state.pongFrames.find((row) => row.frameNumber === payload.frameNumber) ?? state.pongFrames.find((row) => row.frameIndex === payload.frameIndex);
  const asset = record ? pongOverlay(record, payload.mode) : undefined;
  respond(request, 'pongtown-current', { asset, state3d: state.pong3d });
}

function decodeLocalization(request: WorkerRequest) {
  const payload = request.payload as { bytes: ArrayBuffer };
  const records = splitDelimited(payload.bytes);
  const raw = records.at(-1) ?? new Uint8Array(payload.bytes);
  const row = protoToObject<Record<string, any>>(IdoSlamResponse, raw);
  const asset: LocalizationAsset = {
    rawPoses: (row.frame_poses ?? []).map(decodeSlamPose),
    refinedPoses: (row.refined_frame_poses ?? []).map(decodeSlamPose),
    groundPoints: (row.ground_points ?? []).map((point: any) => ({
      frameIndex: asNumber(point.frame_index),
      x: asNumber(point.point?.x),
      y: asNumber(point.point?.y),
      z: asNumber(point.point?.z),
      side: String(point.side ?? '')
    })),
    planeWidthSummary: parsePlaneSummary(row.plane_width_summary_json),
    pairDebug: (row.pair_debug ?? []).map((pair: any) => ({
      frameIndex: asNumber(pair.frame_index),
      pairedFrameIndex: asNumber(pair.paired_frame_index),
      status: String(pair.status ?? ''),
      correspondences: (pair.correspondences ?? []).slice(0, 900).map((corr: any) => ({
        sourceX: asNumber(corr.source_x),
        sourceY: asNumber(corr.source_y),
        targetX: asNumber(corr.target_x),
        targetY: asNumber(corr.target_y),
        onRoad: Boolean(corr.on_road),
        side: String(corr.side ?? '')
      }))
    })),
    canonicalTrack: (row.canonical_frame_tracks ?? []).map((track: any) => ({
      frameIndex: asNumber(track.frame_index),
      x: asNumber(track.canonical_x),
      y: asNumber(track.canonical_y),
      progress: asNumber(track.progress_fraction),
      width: asNumber(track.width_m)
    }))
  };
  stateFor(request.recordingId).localization = asset;
  respond(request, 'localization-decoded', { asset });
}

function parsePlaneSummary(value: unknown): LocalizationAsset['planeWidthSummary'] {
  if (typeof value !== 'string' || !value) return undefined;
  try {
    const parsed = JSON.parse(value) as Record<string, unknown>;
    return {
      pitchDeg: typeof parsed.pitch_deg === 'number' ? parsed.pitch_deg : 18,
      cameraHeightM: typeof parsed.camera_height_m === 'number' ? parsed.camera_height_m : 1.45
    };
  } catch {
    return undefined;
  }
}

function decodeModel(request: WorkerRequest) {
  const payload = request.payload as { summaryBytes?: ArrayBuffer; chatBytes?: ArrayBuffer };
  const data: ModelMusingsData = {
    status: 'ready',
    parameters: [],
    turns: []
  };
  if (payload.summaryBytes) {
    const summary = protoToObject<Record<string, any>>(GensparkSummary, new Uint8Array(payload.summaryBytes));
    data.title = String(summary.title ?? '');
    data.summaryText = String(summary.text ?? '');
    data.parameters = (summary.parameters ?? []).map((param: any) => ({
      name: String(param.name ?? ''),
      value: String(param.value ?? ''),
      unit: String(param.unit ?? '')
    }));
  }
  if (payload.chatBytes) {
    const chat = protoToObject<Record<string, any>>(ChatHistory, new Uint8Array(payload.chatBytes));
    if (chat.initial_turn?.text) {
      data.turns.push({
        role: String(chat.initial_turn.role ?? 'model'),
        text: String(chat.initial_turn.text ?? ''),
        timestampNs: asNumber(chat.initial_turn.timestamp_ns)
      });
    }
    for (const turn of chat.turns ?? []) {
      data.turns.push({
        role: String(turn.role ?? ''),
        text: String(turn.text ?? ''),
        timestampNs: asNumber(turn.timestamp_ns)
      });
    }
  }
  if (!data.summaryText && data.turns.length === 0) data.status = 'empty';
  respond(request, 'model-decoded', { data });
}

function decodeTrack(track: any, index: number, prefix: 'T' | 'S'): MotionTrack {
  return {
    trackId: asNumber(track.track_id, index + 1),
    label: String(track.label || `${prefix}${asNumber(track.track_id, index + 1)}`),
    detectedFrames: asNumber(track.detected_frames),
    totalPositions: asNumber(track.total_positions),
    presenceFraction: asNumber(track.presence_fraction),
    color: trackColor(index),
    positions: (track.positions ?? []).map((point: any): MotionTrackPoint => ({
      frameIdx: asNumber(point.frame_idx),
      cx: asNumber(point.cx),
      cy: asNumber(point.cy),
      area: asNumber(point.area),
      interpolated: Boolean(point.interpolated)
    }))
  };
}

function tailTracks(tracks: MotionTrack[], frameIndex: number): MotionTrack[] {
  return tracks
    .map((track) => ({
      ...track,
      positions: track.positions.filter((point) => point.frameIdx <= frameIndex && point.frameIdx >= frameIndex - 30)
    }))
    .filter((track) => track.positions.length > 0);
}

async function heatmapBitmap(data: Uint8Array, maxWidth: number): Promise<{ bitmap?: ImageBitmap; width: number; height: number } | undefined> {
  const grid = inflateGrid(data, false);
  if (!grid) return undefined;
  const scale = maxWidth > 0 && grid.width > maxWidth ? maxWidth / grid.width : 1;
  const width = Math.max(1, Math.round(grid.width * scale));
  const height = Math.max(1, Math.round(grid.height * scale));
  const canvas = new OffscreenCanvas(width, height);
  const ctx = canvas.getContext('2d');
  if (!ctx) return undefined;
  const imageData = ctx.createImageData(width, height);
  for (let y = 0; y < height; y += 1) {
    const sourceY = Math.min(grid.height - 1, Math.floor(y / scale));
    for (let x = 0; x < width; x += 1) {
      const sourceX = Math.min(grid.width - 1, Math.floor(x / scale));
      const value = grid.values[sourceY * grid.width + sourceX] ?? 0;
      if (value < 3) continue;
      const [r, g, b] = jet(value / 255);
      const px = (y * width + x) * 4;
      imageData.data[px] = r;
      imageData.data[px + 1] = g;
      imageData.data[px + 2] = b;
      imageData.data[px + 3] = Math.min(220, Math.round(value * 0.86));
    }
  }
  ctx.putImageData(imageData, 0, 0);
  const bitmap = await canvas.convertToBlob().then((blob) => createImageBitmap(blob));
  return { bitmap, width, height };
}

function inflateGrid(data: Uint8Array, packedBits: boolean): { width: number; height: number; values: Uint8Array } | undefined {
  if (data.byteLength < 8) return undefined;
  const view = new DataView(data.buffer, data.byteOffset, data.byteLength);
  const height = view.getUint32(0, true);
  const width = view.getUint32(4, true);
  if (!width || !height || width > 8192 || height > 8192) return undefined;
  let inflated: Uint8Array;
  try {
    inflated = pako.inflate(data.subarray(8));
  } catch {
    return undefined;
  }
  const total = width * height;
  if (!packedBits) return { width, height, values: inflated.subarray(0, total) };
  const values = new Uint8Array(total);
  for (let i = 0; i < total; i += 1) {
    const byte = inflated[i >> 3] ?? 0;
    const bit = 7 - (i & 7);
    values[i] = byte & (1 << bit) ? 255 : 0;
  }
  return { width, height, values };
}

function jet(value: number): [number, number, number] {
  const v = Math.max(0, Math.min(1, value));
  const r = Math.round(255 * Math.max(0, Math.min(1, 1.5 - Math.abs(4 * v - 3))));
  const g = Math.round(255 * Math.max(0, Math.min(1, 1.5 - Math.abs(4 * v - 2))));
  const b = Math.round(255 * Math.max(0, Math.min(1, 1.5 - Math.abs(4 * v - 1))));
  return [r, g, b];
}

function pongOverlay(record: PongRecord, mode: PongOverlayMode): PongOverlayAsset {
  const debug = record.pnp_frame_debug?.[0] ?? {};
  const tablePose = record.table_pose ?? {};
  const frameOutput = record.frame_output ?? {};
  const tableQuad =
    mode === 'hull'
      ? tablePose.quad_img ?? debug.image_plane_table_quad_img
      : mode === 'pnp'
        ? debug.pnp_table_quad_img ?? tablePose.quad_img
        : frameOutput.table_quad_img ?? tablePose.quad_img_global;
  const netQuad = mode === 'pnp' ? debug.pnp_overlay_net_quad_img : frameOutput.net_quad_img ?? debug.image_plane_net_quad_img;
  const ping = record.pingpong_tracking?.ball_positions ?? record.ball_positions ?? [];
  const snooker = record.snooker_tracking?.ball_positions ?? [];
  return {
    frameNumber: record.frameNumber,
    frameIndex: record.frameIndex,
    sportMode: sportMode(record.sport_mode),
    tableQuad,
    midline: tablePose.midline_img ?? debug.image_plane_midline_img,
    netQuad,
    ballPositions: [...ping, ...snooker].map((ball: any) => ({
      u: asNumber(ball.u_img),
      v: asNumber(ball.v_img),
      radius: Math.min(10, Math.max(4, Math.sqrt(asNumber(ball.area_px)) / 3)),
      label: String(ball.label ?? `B${asNumber(ball.track_id, asNumber(ball.object_id, 1))}`),
      confidence: asNumber(ball.confidence),
      insideTable: Boolean(ball.inside_table)
    })),
    score: mode === 'global' ? asNumber(frameOutput.global_iou, asNumber(tablePose.global_iou)) : asNumber(tablePose.pnp_iou, asNumber(tablePose.quad_quality)),
    message: tableQuad?.length ? undefined : 'Pose unavailable for this frame'
  };
}

function buildPong3d(summary: Record<string, any> | undefined, frames: PongRecord[]): Pong3DState {
  const sport = sportMode(summary?.sport_mode ?? frames[0]?.sport_mode);
  const bounces =
    summary?.pingpong_tracking?.ball_trajectory?.bounces?.map((bounce: any) => ({
      frameIdx: asNumber(bounce.frame_idx),
      xMm: asNumber(bounce.table_xyz_mm?.[0]),
      yMm: asNumber(bounce.table_xyz_mm?.[1]),
      zMm: asNumber(bounce.table_xyz_mm?.[2]),
      insideTable: Boolean(bounce.inside_table)
    })) ?? [];
  const balls = frames.flatMap((frame) =>
    (frame.snooker_tracking?.ball_positions ?? []).map((ball: any) => ({
      frameIdx: frame.frameIndex,
      label: String(ball.label ?? ''),
      color: snookerColor(String(ball.label ?? '')),
      xMm: asNumber(ball.table_xyz_mm?.[0]),
      yMm: asNumber(ball.table_xyz_mm?.[1]),
      zMm: asNumber(ball.table_xyz_mm?.[2]),
      insideTable: Boolean(ball.inside_table)
    }))
  );
  return {
    sportMode: sport,
    tableWidthMm: asNumber(summary?.table_width_mm, sport === 'snooker' ? 3569 : 2740),
    tableHeightMm: asNumber(summary?.table_height_mm, sport === 'snooker' ? 1778 : 1525),
    netHeightMm: asNumber(summary?.net_height_mm, 152.5),
    bounces,
    balls
  };
}

function decodeSlamPose(pose: any) {
  const fid = pose.frame_id ?? {};
  return {
    frameIndex: asNumber(pose.frame_index),
    frameNumber: asNumber(fid.frame_number),
    timestampNs: asNumber(fid.timestamp_ns),
    position: vectorToPlain(pose.world_pose?.position),
    euler: vectorToPlain(pose.euler_degrees)
  };
}

function legendForMask(mask: Record<string, any>, index: number): SegmentationLegendItem {
  const [r, g, b] = rgbaForMask(mask, index);
  return {
    objectId: asNumber(mask.object_id, index + 1),
    label: String(mask.label || `Object ${asNumber(mask.object_id, index + 1)}`),
    confidence: asNumber(mask.confidence),
    pixelCount: asNumber(mask.pixel_count),
    color: `rgb(${r}, ${g}, ${b})`
  };
}

function rgbaForMask(mask: Record<string, any>, index: number): [number, number, number, number] {
  const label = String(mask.label ?? '').toLowerCase();
  if (label.includes('person')) return [145, 145, 145, 150];
  if (label.includes('wood')) return [92, 55, 28, 150];
  if (label.includes('black')) return [24, 24, 24, 166];
  if (label.includes('white')) return [248, 248, 248, 150];
  if (label.includes('red')) return [239, 68, 68, 145];
  if (label.includes('green')) return [34, 197, 94, 145];
  if (label.includes('blue')) return [59, 130, 246, 145];
  if (label.includes('brown')) return [150, 91, 42, 150];
  const palette: Array<[number, number, number, number]> = [
    [255, 99, 132, 140],
    [54, 162, 235, 140],
    [255, 206, 86, 140],
    [75, 192, 192, 140],
    [153, 102, 255, 140],
    [255, 159, 64, 140]
  ];
  return palette[index % palette.length];
}

function trackColor(index: number): string {
  const palette = [
    'rgb(255,200,0)',
    'rgb(50,255,50)',
    'rgb(80,80,255)',
    'rgb(200,50,255)',
    'rgb(0,220,255)',
    'rgb(255,100,100)',
    'rgb(200,255,0)',
    'rgb(255,0,200)',
    'rgb(0,180,255)',
    'rgb(255,128,0)'
  ];
  return palette[index % palette.length];
}

function sportMode(value: unknown): 'pingpong' | 'snooker' | 'unknown' {
  const mode = asNumber(value);
  if (mode === 1) return 'pingpong';
  if (mode === 2) return 'snooker';
  return 'unknown';
}

function snookerColor(label: string): string {
  const key = label.toLowerCase();
  if (key.includes('white')) return '#f5f0dc';
  if (key.includes('yellow')) return '#f3d23b';
  if (key.includes('green')) return '#2fa84f';
  if (key.includes('brown')) return '#8b5a2b';
  if (key.includes('blue')) return '#3388ff';
  if (key.includes('pink')) return '#ff8fc5';
  if (key.includes('black')) return '#111111';
  return '#d92626';
}

function isPng(bytes: Uint8Array): boolean {
  return bytes.length > 8 && bytes[0] === 0x89 && bytes[1] === 0x50 && bytes[2] === 0x4e && bytes[3] === 0x47;
}

function respond<T>(request: WorkerRequest, type: string, payload: T, transfer: Transferable[] = []) {
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
