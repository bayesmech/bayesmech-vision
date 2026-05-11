<script lang="ts">
  import { Pause, Play, SkipBack, SkipForward } from 'lucide-svelte';
  import type { ProgressState } from '$lib/types';

  export let playing = false;
  export let mode: 'file' | 'live' = 'file';
  export let status = 'idle';
  export let frameCount = 0;
  export let displayIndex = 0;
  export let scrubIndex = 0;
  export let fps = 30;
  export let progress: ProgressState;
  export let onPlay: () => void;
  export let onPause: () => void;
  export let onSkip: (delta: number) => void;
  export let onBeginScrub: () => void;
  export let onPreviewScrub: (index: number) => void;
  export let onCommitScrub: (index: number) => void;

  let localValue = 0;
  $: localValue = scrubIndex;
  $: time = fps > 0 ? displayIndex / fps : 0;

  function commit() {
    onCommitScrub(Number(localValue));
  }

  function formatTime(seconds: number) {
    const safe = Math.max(0, seconds);
    const minutes = Math.floor(safe / 60);
    const secs = Math.floor(safe % 60);
    return `${minutes}:${secs.toString().padStart(2, '0')}`;
  }
</script>

<div class="playback-bar">
  {#if mode === 'live'}
    <span class:paused={!playing} class="live-badge">LIVE</span>
  {:else}
    <button type="button" class="play-icon" on:click={() => onSkip(-1)} aria-label="Skip back">
      <SkipBack size={17} />
    </button>
  {/if}

  <button type="button" class="play-icon main" on:click={playing ? onPause : onPlay} aria-label={playing ? 'Pause' : 'Play'} disabled={!frameCount && mode === 'file'}>
    {#if playing}
      <Pause size={18} />
    {:else}
      <Play size={18} />
    {/if}
  </button>

  {#if mode === 'file'}
    <button type="button" class="play-icon" on:click={() => onSkip(1)} aria-label="Skip forward">
      <SkipForward size={17} />
    </button>
    <div class="position-text">{displayIndex + 1}/{Math.max(frameCount, 1)} <span>{formatTime(time)}</span></div>
    <input
      type="range"
      min="0"
      max={Math.max(0, frameCount - 1)}
      bind:value={localValue}
      on:pointerdown={onBeginScrub}
      on:input={() => onPreviewScrub(Number(localValue))}
      on:change={commit}
      aria-label="Playback frame"
    />
    <div class="fps-text">{fps.toFixed(1)} FPS</div>
  {:else}
    <div class="position-text">{playing ? 'STREAMING' : 'PAUSED'}</div>
  {/if}

  <div class="status-text">{status}</div>

  {#if progress.visible}
    <div class="inline-progress">
      <span>{progress.label}</span>
      <div><i style={`width:${Math.min(100, Math.max(3, (progress.loaded / progress.total) * 100))}%`}></i></div>
    </div>
  {/if}
</div>

<style>
  .playback-bar {
    display: flex;
    align-items: center;
    gap: 0.75rem;
    min-height: 3.25rem;
    padding: 0.625rem 1rem;
    margin-bottom: 1.5rem;
    background: var(--bg-card);
    border: 1px solid var(--border);
  }

  .play-icon {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    width: 2rem;
    height: 2rem;
    border: 0;
    border-radius: 0;
    background: transparent;
    color: var(--text);
    cursor: pointer;
  }

  .play-icon:hover {
    color: var(--accent);
    background: rgba(0, 255, 136, 0.08);
  }

  .play-icon:disabled {
    color: var(--text-dim);
    cursor: not-allowed;
    opacity: 0.5;
  }

  .main {
    border: 1px solid rgba(0, 255, 136, 0.35);
  }

  .position-text {
    min-width: 7rem;
    color: rgba(224, 224, 224, 0.72);
    font-size: 0.82rem;
    font-variant-numeric: tabular-nums;
    text-align: center;
    white-space: nowrap;
  }

  .position-text span,
  .fps-text,
  .status-text {
    color: var(--text-dim);
    font-size: 0.72rem;
    letter-spacing: 0.06em;
    text-transform: uppercase;
    white-space: nowrap;
  }

  input[type='range'] {
    flex: 1 1 auto;
    accent-color: var(--accent);
    min-width: 80px;
  }

  .live-badge {
    padding: 0.15rem 0.45rem;
    background: #e03;
    color: #fff;
    font-size: 0.7rem;
    font-weight: 700;
    letter-spacing: 0.05em;
  }

  .live-badge.paused {
    background: #555;
  }

  .inline-progress {
    display: grid;
    grid-template-columns: auto minmax(72px, 130px);
    gap: 0.5rem;
    align-items: center;
    color: var(--accent);
    font-size: 0.65rem;
    font-weight: 700;
    letter-spacing: 0.06em;
    text-transform: uppercase;
  }

  .inline-progress div {
    height: 4px;
    background: rgba(255, 255, 255, 0.07);
  }

  .inline-progress i {
    display: block;
    height: 100%;
    background: var(--accent);
  }

  @media (max-width: 720px) {
    .playback-bar {
      flex-wrap: wrap;
    }
  }
</style>
