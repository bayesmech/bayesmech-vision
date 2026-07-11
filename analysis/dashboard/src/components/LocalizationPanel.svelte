<script lang="ts">
  import { afterUpdate, onMount } from 'svelte';
  import type { DecodedFrameAsset, LocalizationAsset, SegmentationAsset, SlamPose } from '$lib/types';
  import DataUnavailable from './DataUnavailable.svelte';
  import Panel from './Panel.svelte';

  export let state: 'idle' | 'loading' | 'ready' | 'empty' | 'error' = 'idle';
  export let asset: LocalizationAsset | undefined;
  export let segmentation: SegmentationAsset | undefined;
  export let frame: DecodedFrameAsset | undefined;
  export let error = '';

  type Point2 = { x: number; y: number };
  type Point3 = { x: number; y: number; z: number };
  type ImageRect = { x: number; y: number; width: number; height: number };
  type MaskRaster = { width: number; height: number; mask: Uint8Array };
  type RoadProjectionData = {
    road: MaskRaster;
    bike?: MaskRaster;
    projector?: GroundProjector;
    leftImage: Point2[];
    rightImage: Point2[];
    midImage: Point2[];
    roadGround: Point2[];
    leftGround: Point2[];
    rightGround: Point2[];
    pitchDeg: number;
    cameraHeightM: number;
  };
  type CameraAttitudeData = {
    pitchDeg: number;
    yawDeg: number;
    rollDeg: number;
    cameraRight: Point3;
    cameraUp: Point3;
    cameraBack: Point3;
    worldRight: Point3;
    worldUp: Point3;
    worldForward: Point3;
  };

  const TRACK_WIDTH = 640;
  const TRACK_HEIGHT = 360;
  const VIDEO_WIDTH = 640;
  const VIDEO_HEIGHT = 360;
  const ROAD_LABELS = new Set(['road', 'pavement', 'bike']);

  let rawOverview: HTMLCanvasElement;
  let refinedOverview: HTMLCanvasElement;
  let sift: HTMLCanvasElement;
  let roadImage: HTMLCanvasElement;
  let ground: HTMLCanvasElement;
  let attitude: HTMLCanvasElement;
  let selectedImagePoint: Point2 | undefined;
  let selectedGroundPoint: Point2 | undefined;
  let selectedFrameNumber: number | undefined;

  $: if (frame?.frameNumber !== selectedFrameNumber) {
    selectedFrameNumber = frame?.frameNumber;
    selectedImagePoint = undefined;
    selectedGroundPoint = undefined;
  }

  onMount(drawAll);
  afterUpdate(drawAll);

  function drawAll() {
    const currentRaw = currentPoseForFrame(asset?.rawPoses ?? [], frame);
    const currentRefined = currentPoseForFrame(asset?.refinedPoses ?? [], frame);
    drawSlamMap(rawOverview, asset?.rawPoses ?? [], currentRaw, 'No pre-optimization SLAM poses');
    drawSlamMap(refinedOverview, asset?.refinedPoses ?? [], currentRefined, 'No post-optimization SLAM poses');
    drawSiftOverlay(sift, asset, frame);
    const projection = buildRoadProjectionData(frame, segmentation, asset);
    drawRoadMask(roadImage, frame, projection, selectedImagePoint);
    drawGroundProjection(ground, projection, selectedGroundPoint);
    drawCameraAttitude(attitude, buildCameraAttitudeData(frame));
  }

  class GroundProjector {
    private readonly fx: number;
    private readonly fy: number;
    private readonly cx: number;
    private readonly cy: number;
    private readonly cameraHeightM: number;
    private readonly camToGround: number[][];

    constructor(frame: DecodedFrameAsset, imageWidth: number, imageHeight: number, pitchDeg: number, cameraHeightM: number) {
      const intr = frame.cameraIntrinsics;
      if (!intr) throw new Error('Missing camera intrinsics');
      const srcWidth = intr.imageWidth || frame.rgbWidth || imageWidth;
      const srcHeight = intr.imageHeight || frame.rgbHeight || imageHeight;
      const scaleX = imageWidth / Math.max(srcWidth, 1);
      const scaleY = imageHeight / Math.max(srcHeight, 1);
      this.fx = intr.fx * scaleX;
      this.fy = intr.fy * scaleY;
      this.cx = intr.cx * scaleX;
      this.cy = intr.cy * scaleY;
      this.cameraHeightM = cameraHeightM;

      const pitch = (pitchDeg * Math.PI) / 180;
      const c = Math.cos(pitch);
      const s = Math.sin(pitch);
      const r0 = [
        [1, 0, 0],
        [0, 0, 1],
        [0, -1, 0]
      ];
      const rx = [
        [1, 0, 0],
        [0, c, s],
        [0, -s, c]
      ];
      this.camToGround = multiply3(rx, r0);
    }

    imageToGround(u: number, v: number): Point2 | undefined {
      const ray = [(u - this.cx) / this.fx, (v - this.cy) / this.fy, 1];
      const groundRay = multiplyVec3(this.camToGround, ray);
      if (groundRay[2] >= -1e-6) return undefined;
      const scale = -this.cameraHeightM / groundRay[2];
      return { x: scale * groundRay[0], y: scale * groundRay[1] };
    }
  }

  function prep(canvas: HTMLCanvasElement | undefined, width: number, height: number) {
    if (!canvas) return undefined;
    canvas.width = width;
    canvas.height = height;
    const ctx = canvas.getContext('2d');
    if (!ctx) return undefined;
    drawCanvasBackground(ctx, width, height);
    return ctx;
  }

  function drawCanvasBackground(ctx: CanvasRenderingContext2D, width: number, height: number) {
    ctx.fillStyle = '#030303';
    ctx.fillRect(0, 0, width, height);
    ctx.strokeStyle = '#161616';
    ctx.lineWidth = 1;
    for (let x = 0; x <= width; x += 60) {
      ctx.beginPath();
      ctx.moveTo(x, 0);
      ctx.lineTo(x, height);
      ctx.stroke();
    }
    for (let y = 0; y <= height; y += 60) {
      ctx.beginPath();
      ctx.moveTo(0, y);
      ctx.lineTo(width, y);
      ctx.stroke();
    }
  }

  function drawSlamMap(canvas: HTMLCanvasElement | undefined, poses: SlamPose[], currentPose: SlamPose | undefined, emptyLabel: string) {
    const ctx = prep(canvas, TRACK_WIDTH, TRACK_HEIGHT);
    if (!ctx) return;
    const points3 = poses.map((pose) => pose.position).filter(Boolean) as Point3[];
    if (points3.length < 2) {
      empty(ctx, emptyLabel);
      return;
    }
    const project = createPcaProjector(points3);
    const trajectory = points3.map((point) => project(point));
    const current = currentPose?.position ? project(currentPose.position) : undefined;
    const fit = fitPoints(current ? [...trajectory, current] : trajectory, TRACK_WIDTH, TRACK_HEIGHT, 38);
    drawPolyline(ctx, trajectory, fit, '#ffffff', 2.4);
    if (current) drawDot(ctx, fit(current), '#ffd400', 6.5);
  }

  function drawSiftOverlay(canvas: HTMLCanvasElement | undefined, asset: LocalizationAsset | undefined, frame: DecodedFrameAsset | undefined) {
    const ctx = prep(canvas, VIDEO_WIDTH, VIDEO_HEIGHT);
    if (!ctx) return;
    const rect = drawFrame(ctx, frame);
    if (!rect || !frame?.rgbBitmap) {
      empty(ctx, 'No RGB frame data');
      return;
    }
    const pair = selectPair(asset?.pairDebug ?? [], frame.frameIndex);
    if (!pair) {
      empty(ctx, 'No SIFT pair debug');
      return;
    }

    const scaleX = rect.width / Math.max(frame.rgbBitmap.width, 1);
    const scaleY = rect.height / Math.max(frame.rgbBitmap.height, 1);
    const correspondences = pair.correspondences;
    const stride = Math.max(1, Math.ceil(correspondences.length / 900));
    ctx.lineWidth = 1;
    for (let i = 0; i < correspondences.length; i += stride) {
      const corr = correspondences[i];
      const road = corr.onRoad || Boolean(corr.side);
      const sx = rect.x + corr.sourceX * scaleX;
      const sy = rect.y + corr.sourceY * scaleY;
      const tx = rect.x + corr.targetX * scaleX;
      const ty = rect.y + corr.targetY * scaleY;
      ctx.strokeStyle = road ? 'rgba(255,64,64,0.55)' : 'rgba(255,255,255,0.22)';
      ctx.beginPath();
      ctx.moveTo(sx, sy);
      ctx.lineTo(tx, ty);
      ctx.stroke();
    }
    for (let i = 0; i < correspondences.length; i += stride) {
      const corr = correspondences[i];
      const road = corr.onRoad || Boolean(corr.side);
      drawDot(ctx, { x: rect.x + corr.sourceX * scaleX, y: rect.y + corr.sourceY * scaleY }, road ? '#ff3838' : '#ffffff', road ? 2.8 : 2);
    }
    ctx.fillStyle = 'rgba(245,245,245,0.9)';
    ctx.font = '13px monospace';
    ctx.textAlign = 'left';
    ctx.fillText(`${pair.status || 'pair'}  ${correspondences.length} matches`, 14, 24);
  }

  function drawRoadMask(canvas: HTMLCanvasElement | undefined, frame: DecodedFrameAsset | undefined, projection: RoadProjectionData | undefined, selected?: Point2) {
    const ctx = prep(canvas, VIDEO_WIDTH, VIDEO_HEIGHT);
    if (!ctx) return;
    const rect = drawFrame(ctx, frame);
    if (!rect) {
      empty(ctx, 'No RGB frame data');
      return;
    }
    if (!projection) {
      empty(ctx, segmentation ? 'No road mask in current segmentation' : 'Waiting for segmentation masks');
      return;
    }
    drawMaskOverlay(ctx, rect, projection.road, [47, 136, 255, 95]);
    drawImagePolyline(ctx, rect, projection.leftImage, projection.road, '#ff5bd5', 3);
    drawImagePolyline(ctx, rect, projection.rightImage, projection.road, '#46d884', 3);
    drawImagePolyline(ctx, rect, projection.midImage, projection.road, 'rgba(255,255,255,0.75)', 2);
    drawImagePointList(ctx, rect, projection.leftImage, projection.road, '#ff5bd5', 2.5);
    drawImagePointList(ctx, rect, projection.rightImage, projection.road, '#46d884', 2.5);
    if (selected) {
      drawDot(
        ctx,
        {
          x: rect.x + (selected.x / projection.road.width) * rect.width,
          y: rect.y + (selected.y / projection.road.height) * rect.height
        },
        '#ffd400',
        7
      );
    }
  }

  function drawGroundProjection(canvas: HTMLCanvasElement | undefined, projection: RoadProjectionData | undefined, selected?: Point2) {
    const ctx = prep(canvas, VIDEO_WIDTH, VIDEO_HEIGHT);
    if (!ctx) return;
    if (!projection?.projector) {
      empty(ctx, 'No ground projection data');
      return;
    }
    const allPoints = [...projection.roadGround, ...projection.leftGround, ...projection.rightGround, ...(selected ? [selected] : [])];
    if (!allPoints.length) {
      empty(ctx, 'No projected road points');
      return;
    }
    const fit = fitPoints(allPoints, VIDEO_WIDTH, VIDEO_HEIGHT, 38);
    ctx.fillStyle = 'rgba(47,136,255,0.58)';
    for (const point of projection.roadGround) {
      const p = fit(point);
      ctx.fillRect(p.x - 1.2, p.y - 1.2, 2.4, 2.4);
    }
    drawPolyline(ctx, projection.leftGround, fit, '#ff5bd5', 2.5);
    drawPolyline(ctx, projection.rightGround, fit, '#46d884', 2.5);
    if (selected) drawDot(ctx, fit(selected), '#ffd400', 7);
    ctx.fillStyle = '#707070';
    ctx.font = '11px monospace';
    ctx.textAlign = 'left';
    ctx.fillText(`pitch ${projection.pitchDeg.toFixed(1)} deg  height ${projection.cameraHeightM.toFixed(2)} m`, 14, VIDEO_HEIGHT - 16);
  }

  function drawFrame(ctx: CanvasRenderingContext2D, frame: DecodedFrameAsset | undefined): ImageRect | undefined {
    if (!frame?.rgbBitmap) return undefined;
    const rect = imageRectFor(VIDEO_WIDTH, VIDEO_HEIGHT, frame.rgbBitmap.width, frame.rgbBitmap.height);
    ctx.drawImage(frame.rgbBitmap, rect.x, rect.y, rect.width, rect.height);
    return rect;
  }

  function buildRoadProjectionData(
    frame: DecodedFrameAsset | undefined,
    segmentation: SegmentationAsset | undefined,
    asset: LocalizationAsset | undefined
  ): RoadProjectionData | undefined {
    if (!frame) return undefined;
    const road = combineMasks(segmentation, ROAD_LABELS);
    if (!road) return undefined;
    const bike = firstMask(segmentation, 'bike');
    const pitchDeg = asset?.planeWidthSummary?.pitchDeg ?? 18;
    const cameraHeightM = asset?.planeWidthSummary?.cameraHeightM ?? 1.45;
    let projector: GroundProjector | undefined;
    if (frame.cameraIntrinsics) {
      try {
        projector = new GroundProjector(frame, road.width, road.height, pitchDeg, cameraHeightM);
      } catch {
        projector = undefined;
      }
    }
    const anchorX = estimateBikeCenterX(bike, road.width / 2);
    const edges = extractRoadEdges(road, bike, anchorX);
    const roadGround: Point2[] = [];
    const leftGround: Point2[] = [];
    const rightGround: Point2[] = [];
    if (projector) {
      const step = Math.max(6, Math.floor(Math.sqrt((road.width * road.height) / 3000)));
      for (let y = 0; y < road.height; y += step) {
        const row = y * road.width;
        for (let x = 0; x < road.width; x += step) {
          if (!road.mask[row + x]) continue;
          const p = projector.imageToGround(x, y);
          if (p && p.y >= 0 && p.y <= 45 && Math.abs(p.x) <= 25) roadGround.push(p);
        }
      }
      for (const point of edges.left) {
        const projected = projector.imageToGround(point.x, point.y);
        if (projected) leftGround.push(projected);
      }
      for (const point of edges.right) {
        const projected = projector.imageToGround(point.x, point.y);
        if (projected) rightGround.push(projected);
      }
    }
    return {
      road,
      bike,
      projector,
      leftImage: edges.left,
      rightImage: edges.right,
      midImage: edges.mid,
      roadGround,
      leftGround,
      rightGround,
      pitchDeg,
      cameraHeightM
    };
  }

  function handleRoadClick(event: MouseEvent) {
    const projection = buildRoadProjectionData(frame, segmentation, asset);
    if (!projection?.projector || !roadImage || !frame?.rgbBitmap) return;
    const bounds = roadImage.getBoundingClientRect();
    const x = (event.clientX - bounds.left) * (VIDEO_WIDTH / bounds.width);
    const y = (event.clientY - bounds.top) * (VIDEO_HEIGHT / bounds.height);
    const rect = imageRectFor(VIDEO_WIDTH, VIDEO_HEIGHT, frame.rgbBitmap.width, frame.rgbBitmap.height);
    if (x < rect.x || y < rect.y || x > rect.x + rect.width || y > rect.y + rect.height) return;
    const u = ((x - rect.x) / rect.width) * projection.road.width;
    const v = ((y - rect.y) / rect.height) * projection.road.height;
    selectedImagePoint = { x: u, y: v };
    selectedGroundPoint = projection.projector.imageToGround(u, v);
    drawAll();
  }

  function combineMasks(segmentation: SegmentationAsset | undefined, labels: Set<string>): MaskRaster | undefined {
    const masks = (segmentation?.masks ?? []).filter((mask) => labels.has(mask.label.toLowerCase()));
    if (!masks.length) return undefined;
    const first = masks[0];
    const out = new Uint8Array(first.width * first.height);
    for (const mask of masks) {
      if (mask.width !== first.width || mask.height !== first.height) continue;
      for (let i = 0; i < out.length; i += 1) {
        if (mask.values[i]) out[i] = 1;
      }
    }
    return { width: first.width, height: first.height, mask: out };
  }

  function firstMask(segmentation: SegmentationAsset | undefined, label: string): MaskRaster | undefined {
    const mask = segmentation?.masks?.find((row) => row.label.toLowerCase() === label);
    if (!mask) return undefined;
    const out = new Uint8Array(mask.values.length);
    for (let i = 0; i < out.length; i += 1) out[i] = mask.values[i] ? 1 : 0;
    return { width: mask.width, height: mask.height, mask: out };
  }

  function estimateBikeCenterX(bike: MaskRaster | undefined, fallback: number): number {
    if (!bike) return fallback;
    let sum = 0;
    let count = 0;
    for (let y = 0; y < bike.height; y += 1) {
      const row = y * bike.width;
      for (let x = 0; x < bike.width; x += 1) {
        if (!bike.mask[row + x]) continue;
        sum += x;
        count += 1;
      }
    }
    return count ? sum / count : fallback;
  }

  function extractRoadEdges(road: MaskRaster, bike: MaskRaster | undefined, anchorX: number) {
    const left: Point2[] = [];
    const right: Point2[] = [];
    const mid: Point2[] = [];
    const minSegmentPx = Math.max(8, Math.floor(road.width * 0.02));
    const minTotalWidthPx = Math.max(60, Math.floor(road.width * 0.16));
    let prevLeft: number | undefined;
    let prevRight: number | undefined;
    for (let y = road.height - 24; y >= Math.floor(0.16 * road.height); y -= 6) {
      const segments = segmentsForRow(road.mask, road.width, y, minSegmentPx);
      if (!segments.length) continue;
      const outerLeft = Math.min(...segments.map((segment) => segment[0]));
      const outerRight = Math.max(...segments.map((segment) => segment[1]));
      const chosen = chooseSegment([[outerLeft, outerRight]], anchorX);
      if (!chosen || chosen[1] - chosen[0] < minTotalWidthPx) continue;
      const [l, r] = chosen;
      if (prevLeft !== undefined && Math.abs(l - prevLeft) > 160) continue;
      if (prevRight !== undefined && Math.abs(r - prevRight) > 160) continue;
      const leftBlocked = maskValue(bike, l, y) || maskValue(bike, l - 1, y) || maskValue(bike, l + 1, y);
      const rightBlocked = maskValue(bike, r, y) || maskValue(bike, r - 1, y) || maskValue(bike, r + 1, y);
      if (!leftBlocked) {
        left.push({ x: l, y });
        prevLeft = l;
      }
      if (!rightBlocked) {
        right.push({ x: r, y });
        prevRight = r;
      }
      if (!leftBlocked && !rightBlocked) mid.push({ x: (l + r) / 2, y });
    }
    return { left, right, mid };
  }

  function segmentsForRow(mask: Uint8Array, width: number, y: number, minWidthPx: number): [number, number][] {
    const segments: [number, number][] = [];
    let start = -1;
    const row = y * width;
    for (let x = 0; x < width; x += 1) {
      if (mask[row + x]) {
        if (start < 0) start = x;
      } else if (start >= 0) {
        if (x - 1 - start >= minWidthPx) segments.push([start, x - 1]);
        start = -1;
      }
    }
    if (start >= 0 && width - 1 - start >= minWidthPx) segments.push([start, width - 1]);
    return segments;
  }

  function chooseSegment(segments: [number, number][], anchorX: number): [number, number] | undefined {
    const containing = segments.filter(([left, right]) => left <= anchorX && anchorX <= right);
    const choices = containing.length ? containing : segments;
    return choices.reduce((best, segment) => (segment[1] - segment[0] > best[1] - best[0] ? segment : best), choices[0]);
  }

  function maskValue(mask: MaskRaster | undefined, x: number, y: number): number {
    if (!mask || x < 0 || y < 0 || x >= mask.width || y >= mask.height) return 0;
    return mask.mask[y * mask.width + x];
  }

  function currentPoseForFrame(poses: SlamPose[], frame: DecodedFrameAsset | undefined): SlamPose | undefined {
    if (!poses.length) return undefined;
    if (frame) {
      const byFrameNumber = poses.find((pose) => pose.frameNumber === frame.frameNumber);
      if (byFrameNumber) return byFrameNumber;
      return poses.find((pose) => pose.frameIndex >= frame.frameIndex) ?? poses.at(-1);
    }
    return poses.at(-1);
  }

  function selectPair(pairs: NonNullable<LocalizationAsset['pairDebug']>, frameIndex: number) {
    return pairs.reduce((best, pair) => (Math.abs(pair.frameIndex - frameIndex) < Math.abs(best.frameIndex - frameIndex) ? pair : best), pairs[0]);
  }

  function createPcaProjector(points: Point3[]): (p: Point3) => Point2 {
    if (points.length < 2) return (p) => ({ x: p.x, y: p.z });
    const mean = points.reduce((acc, p) => ({ x: acc.x + p.x, y: acc.y + p.y, z: acc.z + p.z }), { x: 0, y: 0, z: 0 });
    mean.x /= points.length;
    mean.y /= points.length;
    mean.z /= points.length;
    const cov = [
      [0, 0, 0],
      [0, 0, 0],
      [0, 0, 0]
    ];
    for (const p of points) {
      const x = p.x - mean.x;
      const y = p.y - mean.y;
      const z = p.z - mean.z;
      cov[0][0] += x * x;
      cov[0][1] += x * y;
      cov[0][2] += x * z;
      cov[1][0] += y * x;
      cov[1][1] += y * y;
      cov[1][2] += y * z;
      cov[2][0] += z * x;
      cov[2][1] += z * y;
      cov[2][2] += z * z;
    }
    const inv = 1 / points.length;
    for (let r = 0; r < 3; r += 1) for (let c = 0; c < 3; c += 1) cov[r][c] *= inv;
    const axis1 = dominantEigenVector(cov, { x: 1, y: 0.3, z: 0.1 });
    const lambda1 = dot3(axis1, covarianceMultiply(cov, axis1));
    const deflated = cov.map((row, r) =>
      row.map((value, c) => {
        const ar = r === 0 ? axis1.x : r === 1 ? axis1.y : axis1.z;
        const ac = c === 0 ? axis1.x : c === 1 ? axis1.y : axis1.z;
        return value - lambda1 * ar * ac;
      })
    );
    const axis2 = dominantEigenVector(deflated, { x: -axis1.y, y: axis1.x, z: 0.2 });
    return (p) => {
      const centered = { x: p.x - mean.x, y: p.y - mean.y, z: p.z - mean.z };
      return { x: dot3(centered, axis1), y: dot3(centered, axis2) };
    };
  }

  function fitPoints(points: Point2[], width: number, height: number, margin = 36): (p: Point2) => Point2 {
    if (!points.length) return (p) => ({ x: p.x, y: p.y });
    const minX = Math.min(...points.map((p) => p.x));
    const maxX = Math.max(...points.map((p) => p.x));
    const minY = Math.min(...points.map((p) => p.y));
    const maxY = Math.max(...points.map((p) => p.y));
    const spanX = Math.max(maxX - minX, 1e-6);
    const spanY = Math.max(maxY - minY, 1e-6);
    const scale = Math.min((width - margin * 2) / spanX, (height - margin * 2) / spanY);
    const usedW = spanX * scale;
    const usedH = spanY * scale;
    const ox = margin + (width - margin * 2 - usedW) / 2;
    const oy = margin + (height - margin * 2 - usedH) / 2;
    return (p) => ({ x: ox + (p.x - minX) * scale, y: height - (oy + (p.y - minY) * scale) });
  }

  function drawMaskOverlay(ctx: CanvasRenderingContext2D, rect: ImageRect, mask: MaskRaster, color: [number, number, number, number]) {
    const overlay = document.createElement('canvas');
    overlay.width = mask.width;
    overlay.height = mask.height;
    const overlayCtx = overlay.getContext('2d');
    if (!overlayCtx) return;
    const imageData = overlayCtx.createImageData(mask.width, mask.height);
    for (let i = 0; i < mask.mask.length; i += 1) {
      if (!mask.mask[i]) continue;
      const offset = i * 4;
      imageData.data[offset] = color[0];
      imageData.data[offset + 1] = color[1];
      imageData.data[offset + 2] = color[2];
      imageData.data[offset + 3] = color[3];
    }
    overlayCtx.putImageData(imageData, 0, 0);
    ctx.drawImage(overlay, rect.x, rect.y, rect.width, rect.height);
  }

  function drawImagePointList(ctx: CanvasRenderingContext2D, rect: ImageRect, points: Point2[], source: MaskRaster, color: string, radius: number) {
    for (const p of points) {
      drawDot(ctx, { x: rect.x + (p.x / source.width) * rect.width, y: rect.y + (p.y / source.height) * rect.height }, color, radius);
    }
  }

  function drawImagePolyline(ctx: CanvasRenderingContext2D, rect: ImageRect, points: Point2[], source: MaskRaster, color: string, width: number) {
    if (points.length < 2) return;
    ctx.strokeStyle = color;
    ctx.lineWidth = width;
    ctx.beginPath();
    points.forEach((p, index) => {
      const x = rect.x + (p.x / source.width) * rect.width;
      const y = rect.y + (p.y / source.height) * rect.height;
      if (index === 0) ctx.moveTo(x, y);
      else ctx.lineTo(x, y);
    });
    ctx.stroke();
  }

  function drawPolyline(ctx: CanvasRenderingContext2D, points: Point2[], map: (p: Point2) => Point2, color: string, width: number) {
    if (points.length < 2) return;
    ctx.strokeStyle = color;
    ctx.lineWidth = width;
    ctx.beginPath();
    points.forEach((point, index) => {
      const p = map(point);
      if (index === 0) ctx.moveTo(p.x, p.y);
      else ctx.lineTo(p.x, p.y);
    });
    ctx.stroke();
  }

  function drawDot(ctx: CanvasRenderingContext2D, p: Point2, color: string, radius: number) {
    ctx.fillStyle = color;
    ctx.beginPath();
    ctx.arc(p.x, p.y, radius, 0, Math.PI * 2);
    ctx.fill();
  }

  function drawCameraAttitude(canvas: HTMLCanvasElement | undefined, data: CameraAttitudeData | undefined) {
    const ctx = prep(canvas, VIDEO_WIDTH, VIDEO_HEIGHT);
    if (!ctx) return;
    drawAttitudeGround(ctx);
    if (!data) {
      empty(ctx, 'Waiting for camera pose data');
      return;
    }
    drawCameraWireframe(ctx, data);
    ctx.fillStyle = '#f5f5f5';
    ctx.font = '15px monospace';
    ctx.textAlign = 'left';
    ctx.textBaseline = 'top';
    ctx.fillText(`pitch ${data.pitchDeg.toFixed(1)} deg`, 16, 16);
    ctx.fillText(`yaw   ${data.yawDeg.toFixed(1)} deg`, 16, 38);
    ctx.fillText(`roll  ${data.rollDeg.toFixed(1)} deg`, 16, 60);
  }

  function buildCameraAttitudeData(frame: DecodedFrameAsset | undefined): CameraAttitudeData | undefined {
    const pose = frame?.pose;
    if (!pose?.rotation) return undefined;
    const worldUp = { x: 0, y: 1, z: 0 };
    const bearing = frame?.gps?.bearing;
    const bearingRad = Number.isFinite(bearing) ? (bearing! * Math.PI) / 180 : 0;
    const worldForward = normalize3({ x: Math.sin(bearingRad), y: 0, z: -Math.cos(bearingRad) });
    const worldRight = normalize3(cross3(worldUp, worldForward));
    const cameraRight = normalize3(rotateByQuat({ x: 1, y: 0, z: 0 }, pose.rotation));
    const cameraUp = normalize3(rotateByQuat({ x: 0, y: 1, z: 0 }, pose.rotation));
    const cameraBack = normalize3(rotateByQuat({ x: 0, y: 0, z: 1 }, pose.rotation));
    const cameraForward = scale3(cameraBack, -1);
    const forwardComponent = dot3(cameraForward, worldForward);
    const rightComponent = dot3(cameraForward, worldRight);
    const upComponent = dot3(cameraForward, worldUp);
    return {
      pitchDeg: (Math.atan2(upComponent, Math.hypot(forwardComponent, rightComponent)) * 180) / Math.PI,
      yawDeg: (Math.atan2(rightComponent, forwardComponent) * 180) / Math.PI,
      rollDeg: (Math.atan2(dot3(cameraRight, worldUp), dot3(cameraUp, worldUp)) * 180) / Math.PI,
      cameraRight,
      cameraUp,
      cameraBack,
      worldRight,
      worldUp,
      worldForward
    };
  }

  function drawAttitudeGround(ctx: CanvasRenderingContext2D) {
    const y = -0.52;
    for (let x = -2.4; x <= 2.4; x += 0.4) drawAttitudeLine(ctx, { x, y, z: -0.35 }, { x, y, z: 2.7 }, 'rgba(47,136,255,0.22)', 1);
    for (let z = -0.2; z <= 2.7; z += 0.4) drawAttitudeLine(ctx, { x: -2.4, y, z }, { x: 2.4, y, z }, 'rgba(47,136,255,0.22)', 1);
    drawAttitudeLine(ctx, { x: 0, y, z: -0.2 }, { x: 0, y, z: 2.75 }, 'rgba(255,255,255,0.28)', 1.2);
  }

  function drawCameraWireframe(ctx: CanvasRenderingContext2D, data: CameraAttitudeData) {
    const nearZ = -0.45;
    const farZ = -1.2;
    const nearW = 0.28;
    const nearH = 0.18;
    const farW = 0.9;
    const farH = 0.58;
    const corners = [
      { x: -nearW, y: -nearH, z: nearZ },
      { x: nearW, y: -nearH, z: nearZ },
      { x: nearW, y: nearH, z: nearZ },
      { x: -nearW, y: nearH, z: nearZ },
      { x: -farW, y: -farH, z: farZ },
      { x: farW, y: -farH, z: farZ },
      { x: farW, y: farH, z: farZ },
      { x: -farW, y: farH, z: farZ }
    ].map((point) => cameraPointToMotionWorld(point, data));
    for (const [a, b] of [
      [0, 1],
      [1, 2],
      [2, 3],
      [3, 0],
      [4, 5],
      [5, 6],
      [6, 7],
      [7, 4],
      [0, 4],
      [1, 5],
      [2, 6],
      [3, 7]
    ]) {
      drawAttitudeLine(ctx, corners[a], corners[b], '#ffffff', 2);
    }
    drawAttitudeLine(ctx, { x: 0, y: -0.52, z: 0 }, { x: 0, y: 0.85, z: 0 }, '#46d884', 2);
  }

  function cameraPointToMotionWorld(point: Point3, data: CameraAttitudeData): Point3 {
    const world = add3(add3(scale3(data.cameraRight, point.x), scale3(data.cameraUp, point.y)), scale3(data.cameraBack, point.z));
    return {
      x: dot3(world, data.worldRight),
      y: dot3(world, data.worldUp),
      z: dot3(world, data.worldForward)
    };
  }

  function drawAttitudeLine(ctx: CanvasRenderingContext2D, a: Point3, b: Point3, color: string, width = 1.5) {
    const pa = projectAttitudePoint(a);
    const pb = projectAttitudePoint(b);
    ctx.strokeStyle = color;
    ctx.lineWidth = width;
    ctx.beginPath();
    ctx.moveTo(pa.x, pa.y);
    ctx.lineTo(pb.x, pb.y);
    ctx.stroke();
  }

  function projectAttitudePoint(point: Point3): Point2 {
    const scale = 170;
    return {
      x: VIDEO_WIDTH * 0.5 + point.x * scale + point.z * scale * 0.36,
      y: VIDEO_HEIGHT * 0.58 - point.y * scale + point.z * scale * 0.16
    };
  }

  function imageRectFor(canvasWidth: number, canvasHeight: number, imageWidth: number, imageHeight: number): ImageRect {
    const scale = Math.min(canvasWidth / Math.max(imageWidth, 1), canvasHeight / Math.max(imageHeight, 1));
    const width = imageWidth * scale;
    const height = imageHeight * scale;
    return { x: (canvasWidth - width) / 2, y: (canvasHeight - height) / 2, width, height };
  }

  function multiply3(a: number[][], b: number[][]): number[][] {
    return a.map((row) => b[0].map((_, col) => row[0] * b[0][col] + row[1] * b[1][col] + row[2] * b[2][col]));
  }

  function multiplyVec3(m: number[][], v: number[]): number[] {
    return [m[0][0] * v[0] + m[0][1] * v[1] + m[0][2] * v[2], m[1][0] * v[0] + m[1][1] * v[1] + m[1][2] * v[2], m[2][0] * v[0] + m[2][1] * v[1] + m[2][2] * v[2]];
  }

  function dot3(a: Point3, b: Point3): number {
    return a.x * b.x + a.y * b.y + a.z * b.z;
  }

  function normalize3(v: Point3): Point3 {
    const len = Math.hypot(v.x, v.y, v.z);
    return len < 1e-9 ? { x: 1, y: 0, z: 0 } : { x: v.x / len, y: v.y / len, z: v.z / len };
  }

  function add3(a: Point3, b: Point3): Point3 {
    return { x: a.x + b.x, y: a.y + b.y, z: a.z + b.z };
  }

  function scale3(v: Point3, scale: number): Point3 {
    return { x: v.x * scale, y: v.y * scale, z: v.z * scale };
  }

  function cross3(a: Point3, b: Point3): Point3 {
    return { x: a.y * b.z - a.z * b.y, y: a.z * b.x - a.x * b.z, z: a.x * b.y - a.y * b.x };
  }

  function covarianceMultiply(cov: number[][], v: Point3): Point3 {
    return {
      x: cov[0][0] * v.x + cov[0][1] * v.y + cov[0][2] * v.z,
      y: cov[1][0] * v.x + cov[1][1] * v.y + cov[1][2] * v.z,
      z: cov[2][0] * v.x + cov[2][1] * v.y + cov[2][2] * v.z
    };
  }

  function dominantEigenVector(cov: number[][], seed: Point3): Point3 {
    let v = normalize3(seed);
    for (let i = 0; i < 24; i += 1) v = normalize3(covarianceMultiply(cov, v));
    return v;
  }

  function rotateByQuat(v: Point3, q: NonNullable<DecodedFrameAsset['pose']>['rotation']): Point3 {
    if (!q) return v;
    const len = Math.hypot(q.x, q.y, q.z, q.w) || 1;
    const qx = q.x / len;
    const qy = q.y / len;
    const qz = q.z / len;
    const qw = q.w / len;
    const tx = 2 * (qy * v.z - qz * v.y);
    const ty = 2 * (qz * v.x - qx * v.z);
    const tz = 2 * (qx * v.y - qy * v.x);
    return {
      x: v.x + qw * tx + qy * tz - qz * ty,
      y: v.y + qw * ty + qz * tx - qx * tz,
      z: v.z + qw * tz + qx * ty - qy * tx
    };
  }

  function empty(ctx: CanvasRenderingContext2D, label: string) {
    ctx.fillStyle = '#707070';
    ctx.font = '13px monospace';
    ctx.textAlign = 'center';
    ctx.textBaseline = 'middle';
    ctx.fillText(label, ctx.canvas.width / 2, ctx.canvas.height / 2);
  }
</script>

{#if state === 'error'}
  <DataUnavailable message="Localization failed" detail={error} />
{:else if state === 'loading' || state === 'idle'}
  <DataUnavailable message={state === 'loading' ? 'Loading IDOSLAM artifact' : 'Localization idle'} />
{:else if state === 'empty'}
  <DataUnavailable message="No localization artifact" />
{:else}
  <div class="slam-overview-row">
    <Panel title="Pre-Optimization SLAM"><canvas bind:this={rawOverview}></canvas></Panel>
    <Panel title="Post-Optimization SLAM"><canvas bind:this={refinedOverview}></canvas></Panel>
  </div>
  <div class="localization-grid two-column">
    <Panel title="SIFT Correspondences"><canvas bind:this={sift}></canvas></Panel>
    <Panel title="Camera Ground Pose"><canvas bind:this={attitude}></canvas></Panel>
  </div>
  <div class="localization-grid two-column">
    <Panel title="Road Mask And Edges"><canvas class="clickable" bind:this={roadImage} on:click={handleRoadClick}></canvas></Panel>
    <Panel title="Ground Plane Projection"><canvas bind:this={ground}></canvas></Panel>
  </div>
{/if}

<style>
  .slam-overview-row,
  .localization-grid {
    display: grid;
    grid-template-columns: repeat(2, minmax(0, 1fr));
    gap: 1rem;
    margin-bottom: 1rem;
    align-items: start;
  }

  .slam-overview-row :global(.panel),
  .localization-grid :global(.panel) {
    min-width: 0;
  }

  canvas {
    width: 100%;
    height: auto;
    display: block;
    background: #030303;
    border: 1px solid var(--border);
  }

  .clickable {
    cursor: crosshair;
  }

  @media (max-width: 1024px) {
    .slam-overview-row,
    .two-column {
      grid-template-columns: 1fr;
    }
  }
</style>
