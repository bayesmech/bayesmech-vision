<script lang="ts">
  import type { DecodedFrameAsset, Pong3DState, PongOverlayAsset, PongOverlayMode } from '$lib/types';
  import DataUnavailable from './DataUnavailable.svelte';
  import Panel from './Panel.svelte';
  import StreamCanvas from './StreamCanvas.svelte';
  import ThreeTableView from './ThreeTableView.svelte';

  export let frame: DecodedFrameAsset | undefined;
  export let state: 'idle' | 'loading' | 'ready' | 'empty' | 'error' = 'idle';
  export let mode: PongOverlayMode = 'global';
  export let asset: PongOverlayAsset | undefined;
  export let state3d: Pong3DState | undefined;
  export let poseCorrections = true;
  export let error = '';
  export let onMode: (mode: PongOverlayMode) => void;
  export let onTogglePoseCorrections: () => void;

  const placeholder3d: Pong3DState = {
    sportMode: 'unknown',
    tableWidthMm: 2740,
    tableHeightMm: 1525,
    netHeightMm: 152.5,
    bounces: [],
    balls: []
  };

  function points(values: number[]) {
    const pairs: string[] = [];
    for (let i = 0; i + 1 < values.length; i += 2) pairs.push(`${values[i]},${values[i + 1]}`);
    return pairs.join(' ');
  }
</script>

<div class="panel-grid-equal">
  <Panel title="Surface Pose Estimation">
    <div slot="actions" class="segmented sport-mode" role="tablist" aria-label="Sport overlay mode">
      <button type="button" class:active={mode === 'hull'} on:click={() => onMode('hull')}>Hull Generation</button>
      <button type="button" class:active={mode === 'pnp'} on:click={() => onMode('pnp')}>PnP Estimates</button>
      <button type="button" class:active={mode === 'global'} on:click={() => onMode('global')}>Global Pose</button>
    </div>
    {#if state === 'error'}
      <DataUnavailable message="Sport understanding failed" detail={error} />
    {:else if state === 'loading' || state === 'idle'}
      <DataUnavailable message={state === 'loading' ? 'Loading sport artifact' : 'Sport understanding idle'} />
    {:else if state === 'empty'}
      <DataUnavailable message="No sport understanding artifact" />
    {:else}
      <div class="sport-viewer">
        <StreamCanvas {frame} label="Sport understanding frame" />
        <svg viewBox={`0 0 ${frame?.rgbWidth ?? 1280} ${frame?.rgbHeight ?? 720}`} preserveAspectRatio="xMidYMid meet" aria-hidden="true">
          {#if asset?.tableQuad}
            <polygon points={points(asset.tableQuad)} fill="none" stroke="#fff" stroke-width="3" vector-effect="non-scaling-stroke" />
          {/if}
          {#if asset?.netQuad}
            <polygon points={points(asset.netQuad)} fill="none" stroke="#ff35ff" stroke-width="2" vector-effect="non-scaling-stroke" />
          {/if}
          {#if asset?.midline}
            <line x1={asset.midline[0]} y1={asset.midline[1]} x2={asset.midline[2]} y2={asset.midline[3]} stroke="#ff3030" stroke-width="2" vector-effect="non-scaling-stroke" />
          {/if}
          {#each asset?.ballPositions ?? [] as ball}
            <circle cx={ball.u} cy={ball.v} r={ball.radius} fill="#ffd84d" stroke="#07120b" stroke-width="2" vector-effect="non-scaling-stroke" />
          {/each}
        </svg>
        {#if asset?.message}<div class="overlay-message">{asset.message}</div>{/if}
      </div>
      <footer>
        <span>Frame {asset?.frameNumber ?? frame?.frameNumber ?? 0}</span>
        <span>{asset?.sportMode ?? 'unknown'}</span>
        <span>Score {((asset?.score ?? 0) * 100).toFixed(1)}%</span>
      </footer>
    {/if}
  </Panel>

  <Panel title="3D Trajectory Understanding">
    <div slot="actions" class="three-actions">
      <button type="button" aria-pressed={poseCorrections} class:active={poseCorrections} on:click={onTogglePoseCorrections}>Pose Corrections</button>
      <span>{state3d?.sportMode ?? 'No table'}</span>
    </div>
    <ThreeTableView state3d={state3d ?? placeholder3d} frameIndex={frame?.frameIndex ?? 0} {poseCorrections} />
  </Panel>
</div>

<style>
  .sport-mode {
    grid-template-columns: repeat(3, 1fr);
    width: min(100%, 520px);
  }

  .sport-viewer {
    position: relative;
    min-height: 360px;
  }

  .sport-viewer :global(.viewer-frame) {
    min-height: 360px;
  }

  svg {
    position: absolute;
    inset: 0;
    width: 100%;
    height: 100%;
    pointer-events: none;
  }

  .overlay-message {
    position: absolute;
    bottom: 1rem;
    left: 50%;
    transform: translateX(-50%);
    padding: 0.45rem 0.75rem;
    border: 1px solid rgba(255, 255, 255, 0.16);
    background: rgba(0, 0, 0, 0.72);
    color: var(--text-dim);
    font-size: 0.72rem;
    letter-spacing: 0.06em;
    text-transform: uppercase;
  }

  footer {
    display: flex;
    justify-content: space-between;
    gap: 1rem;
    margin-top: 0.75rem;
    color: var(--text-dim);
    font-size: 0.72rem;
    letter-spacing: 0.06em;
    text-transform: uppercase;
  }

  .three-actions {
    display: flex;
    align-items: center;
    gap: 0.75rem;
  }

  .three-actions button {
    min-height: 2rem;
    border: 1px solid rgba(255, 255, 255, 0.18);
    background: #050706;
    color: var(--text-dim);
    cursor: pointer;
    font-size: 0.72rem;
    letter-spacing: 0.06em;
    text-transform: uppercase;
  }

  .three-actions button.active {
    border-color: rgba(0, 255, 136, 0.52);
    background: rgba(0, 255, 136, 0.12);
    color: var(--text);
  }

  .three-actions span {
    color: var(--text-dim);
    font-size: 0.72rem;
    text-transform: uppercase;
  }
</style>
