<script lang="ts">
  import type { DecodedFrameAsset, MotionOverlayAsset, MotionTrack } from '$lib/types';
  import DataUnavailable from './DataUnavailable.svelte';
  import Panel from './Panel.svelte';
  import StreamCanvas from './StreamCanvas.svelte';

  export let frame: DecodedFrameAsset | undefined;
  export let state: 'idle' | 'loading' | 'ready' | 'empty' | 'error' = 'idle';
  export let mode: 'raft' | 'segmentation' = 'raft';
  export let asset: MotionOverlayAsset | undefined;
  export let legendTracks: MotionTrack[] = [];
  export let inactiveTrackCount = 0;
  export let error = '';
  export let onMode: (mode: 'raft' | 'segmentation') => void;

  $: activeSegments = mode === 'raft' ? asset?.raftSegments ?? [] : asset?.segmentationSegments ?? [];
  $: stageWidth = frame?.rgbWidth ?? 1280;
  $: stageHeight = frame?.rgbHeight ?? 720;
</script>

<div class="panel-grid-2">
  <Panel title="Motion Viewer">
    <div slot="actions" class="segmented motion-mode" role="tablist" aria-label="Motion mode">
      <button type="button" class:active={mode === 'raft'} on:click={() => onMode('raft')}>RAFT</button>
      <button type="button" class:active={mode === 'segmentation'} on:click={() => onMode('segmentation')}>Segmentation</button>
    </div>
    {#if state === 'error'}
      <DataUnavailable message="Motion capture failed" detail={error} />
    {:else if state === 'loading' || state === 'idle'}
      <DataUnavailable message={state === 'loading' ? 'Loading motion capture artifact' : 'Motion capture idle'} />
    {:else if state === 'empty'}
      <DataUnavailable message="No motion capture artifact" />
    {:else}
      <div class="motion-viewer" aria-label="Motion capture tracks">
        <StreamCanvas {frame} motion={asset} label="Motion capture RGB and heatmap stream" maxDrawWidth={640} />
        <svg viewBox={`0 0 ${stageWidth} ${stageHeight}`} preserveAspectRatio="xMidYMid meet" role="img" aria-label="Motion capture tracks">
          {#each activeSegments as track}
            {#each track.positions as point, index}
              {#if index > 0}
                <line
                  x1={track.positions[index - 1].cx}
                  y1={track.positions[index - 1].cy}
                  x2={point.cx}
                  y2={point.cy}
                  stroke={track.color}
                  stroke-opacity={0.18 + (index / Math.max(track.positions.length, 1)) * 0.7}
                  stroke-width="2"
                  vector-effect="non-scaling-stroke"
                />
              {/if}
            {/each}
            {#if track.positions.length}
              {@const current = track.positions[track.positions.length - 1]}
              {#if mode === 'raft'}
                {#if current.interpolated}
                  <path d={`M ${current.cx - 5} ${current.cy} H ${current.cx + 5} M ${current.cx} ${current.cy - 5} V ${current.cy + 5}`} stroke={track.color} stroke-width="1" vector-effect="non-scaling-stroke" />
                {:else}
                  <circle cx={current.cx} cy={current.cy} r="6" fill="none" stroke={track.color} stroke-width="2" vector-effect="non-scaling-stroke" />
                {/if}
              {:else}
                {#if current.interpolated}
                  <path d={`M ${current.cx - 5} ${current.cy - 5} L ${current.cx + 5} ${current.cy + 5} M ${current.cx + 5} ${current.cy - 5} L ${current.cx - 5} ${current.cy + 5}`} stroke={track.color} stroke-width="1.5" vector-effect="non-scaling-stroke" />
                {:else}
                  <rect x={current.cx - 5} y={current.cy - 5} width="10" height="10" transform={`rotate(45 ${current.cx} ${current.cy})`} fill="none" stroke={track.color} stroke-width="2" vector-effect="non-scaling-stroke" />
                {/if}
              {/if}
              <text x={current.cx + 8} y={current.cy - 8} fill={track.color}>{mode === 'raft' ? 'T' : 'S'}{track.trackId}</text>
            {/if}
          {/each}
        </svg>
      </div>
      <div class="motion-footer">
        <span>Frame {asset?.frameNumber ?? frame?.frameNumber ?? 0}</span>
        <span>{activeSegments.length} visible tracks</span>
        <span>{asset?.heatmapBitmap ? 'Heatmap' : 'Heatmap pending'}</span>
      </div>
    {/if}
  </Panel>

  <Panel title={mode === 'raft' ? 'RAFT Tracks' : 'Segmentation Tracks'}>
    {#if legendTracks.length}
      <div class="track-list">
        {#each legendTracks as track}
          <div class="track-row">
            <span style={`background:${track.color}; box-shadow:0 0 12px ${track.color}`}></span>
            <b>{track.label || `${mode === 'raft' ? 'T' : 'S'}${track.trackId}`}</b>
            <i>{(track.presenceFraction * 100).toFixed(0)}%</i>
          </div>
        {/each}
      </div>
      {#if inactiveTrackCount}<div class="inactive-note">{inactiveTrackCount} tracks in inactive mode</div>{/if}
    {:else}
      <DataUnavailable message="No tracks detected" />
    {/if}
  </Panel>
</div>

<style>
  .motion-mode {
    grid-template-columns: repeat(2, 1fr);
    width: min(100%, 300px);
  }

  .motion-viewer {
    position: relative;
    width: 100%;
    aspect-ratio: 16 / 9;
    overflow: hidden;
    border: 1px solid var(--border);
    background: #000;
  }

  .motion-viewer :global(.viewer-frame) {
    position: absolute;
    inset: 0;
    width: 100%;
    height: 100%;
    border: 0;
  }

  .motion-viewer :global(canvas) {
    image-rendering: auto;
  }

  svg {
    position: absolute;
    inset: 0;
    width: 100%;
    height: 100%;
    pointer-events: none;
    overflow: hidden;
  }

  text {
    font: 13px Arial, sans-serif;
  }

  .motion-footer {
    display: flex;
    flex-wrap: wrap;
    gap: 0.75rem;
    justify-content: space-between;
    margin-top: 0.6rem;
    color: var(--text-dim);
    font-size: 0.75rem;
    text-transform: uppercase;
  }

  .track-list {
    display: flex;
    flex-direction: column;
    gap: 0.5rem;
  }

  .track-row {
    display: grid;
    grid-template-columns: 2rem minmax(0, 1fr) auto;
    gap: 0.75rem;
    align-items: center;
    padding: 0.5rem 0;
    border-bottom: 1px solid rgba(255, 255, 255, 0.05);
  }

  .track-row span {
    width: 1.8rem;
    height: 0.25rem;
  }

  .track-row b {
    overflow: hidden;
    font-size: 0.8rem;
    font-weight: 600;
    letter-spacing: 0.05em;
    text-overflow: ellipsis;
    text-transform: uppercase;
    white-space: nowrap;
  }

  .track-row i,
  .inactive-note {
    color: var(--text-dim);
    font-size: 0.72rem;
    font-style: normal;
  }
</style>
