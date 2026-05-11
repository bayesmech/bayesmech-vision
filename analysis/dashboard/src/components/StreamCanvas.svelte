<script lang="ts">
  import { afterUpdate, onMount } from 'svelte';
  import type { DecodedFrameAsset, MotionOverlayAsset, SegmentationAsset } from '$lib/types';

  export let frame: DecodedFrameAsset | undefined;
  export let segmentation: SegmentationAsset | undefined = undefined;
  export let motion: MotionOverlayAsset | undefined = undefined;
  export let label = 'RGB stream';
  export let showBaseFrame = true;
  export let maxDrawWidth = 0;

  let canvas: HTMLCanvasElement;

  function draw() {
    if (!canvas) return;
    const ctx = canvas.getContext('2d');
    if (!ctx) return;
    const source = showBaseFrame ? frame?.rgbBitmap : undefined;
    const sourceWidth = source?.width ?? segmentation?.bitmap?.width ?? motion?.heatmapBitmap?.width ?? frame?.rgbWidth ?? 1280;
    const sourceHeight = source?.height ?? segmentation?.bitmap?.height ?? motion?.heatmapBitmap?.height ?? frame?.rgbHeight ?? 720;
    const scale = maxDrawWidth > 0 && sourceWidth > maxDrawWidth ? maxDrawWidth / sourceWidth : 1;
    const width = Math.max(1, Math.round(sourceWidth * scale));
    const height = Math.max(1, Math.round(sourceHeight * scale));
    canvas.width = width;
    canvas.height = height;
    ctx.clearRect(0, 0, width, height);
    ctx.fillStyle = '#000';
    ctx.fillRect(0, 0, width, height);
    if (source) ctx.drawImage(source, 0, 0, width, height);
    if (segmentation?.bitmap) {
      ctx.globalAlpha = 0.88;
      ctx.drawImage(segmentation.bitmap, 0, 0, width, height);
      ctx.globalAlpha = 1;
    }
    if (motion?.heatmapBitmap) {
      ctx.globalAlpha = 0.55;
      ctx.drawImage(motion.heatmapBitmap, 0, 0, width, height);
      ctx.globalAlpha = 1;
    }
  }

  onMount(draw);
  afterUpdate(draw);
</script>

<div class="viewer-frame" aria-label={label}>
  {#if frame?.rgbBitmap || segmentation?.bitmap || motion?.heatmapBitmap}
    <canvas bind:this={canvas}></canvas>
  {:else}
    <div class="empty-state">No RGB frame loaded</div>
  {/if}
</div>

<style>
  canvas {
    width: 100%;
    height: 100%;
    object-fit: contain;
    display: block;
  }
</style>
