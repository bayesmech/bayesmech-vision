<script lang="ts">
  import type { DecodedFrameAsset, SegmentationAsset } from '$lib/types';
  import DataUnavailable from './DataUnavailable.svelte';
  import Panel from './Panel.svelte';
  import StreamCanvas from './StreamCanvas.svelte';

  export let frame: DecodedFrameAsset | undefined;
  export let state: 'idle' | 'loading' | 'ready' | 'empty' | 'error' = 'idle';
  export let asset: SegmentationAsset | undefined;
  export let error = '';
</script>

<div class="panel-grid-2">
  <Panel title="Segmentation Viewer">
    {#if state === 'error'}
      <DataUnavailable message="Segmentation failed" detail={error} />
    {:else if state === 'idle' || state === 'loading'}
      <DataUnavailable message={state === 'loading' ? 'Loading segmentation masks' : 'Open a recording'} />
    {:else if !asset?.bitmap}
      <DataUnavailable message="No objects detected" />
    {:else}
      <StreamCanvas {frame} segmentation={asset} showBaseFrame={false} label="Segmentation masks" />
    {/if}
  </Panel>

  <Panel title="Legend">
    {#if asset?.legend?.length}
      <div class="legend-list">
        {#each asset.legend as item}
          <div class="legend-row">
            <span class="swatch" style={`background:${item.color}; border-color:${item.color}`}></span>
            <div>
              <div class="legend-label">{item.label}</div>
              <div class="legend-meta">ID {item.objectId} - {(item.confidence * 100).toFixed(1)}% - {item.pixelCount} px</div>
            </div>
          </div>
        {/each}
      </div>
    {:else}
      <DataUnavailable message="No objects detected" />
    {/if}
  </Panel>
</div>

<style>
  .legend-list {
    display: flex;
    flex-direction: column;
    gap: 0.5rem;
    overflow: auto;
    max-height: 420px;
  }

  .legend-row {
    display: flex;
    align-items: flex-start;
    gap: 0.5rem;
    padding: 0.4rem 0;
    border-bottom: 1px solid rgba(255, 255, 255, 0.05);
  }

  .swatch {
    flex: 0 0 12px;
    width: 12px;
    height: 12px;
    margin-top: 2px;
    border: 1px solid currentColor;
  }

  .legend-label {
    font-size: 0.78rem;
    line-height: 1.35;
    overflow-wrap: anywhere;
  }

  .legend-meta {
    margin-top: 0.2rem;
    color: var(--text-dim);
    font-size: 0.72rem;
  }
</style>
