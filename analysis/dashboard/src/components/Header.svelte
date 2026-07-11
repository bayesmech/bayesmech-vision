<script lang="ts">
  import { Activity, Database, RefreshCw } from 'lucide-svelte';
  import type { ProgressState } from '$lib/types';

  export let aggregate: 'READY' | 'BUFFERING' | 'LIVE' | 'DEGRADED' | 'OFFLINE' = 'OFFLINE';
  export let loading = false;
  export let progress: ProgressState;
  export let onLoad: () => void;
  export let onHealth: () => void;
  export let onRefresh: () => void;
</script>

<header class="app-header">
  <div class="header-inner">
    <div class="brand">
      <img src="/bayesmech-logo.png" alt="BayesMech" />
      <div class="brand-title">Vision Console</div>
    </div>
    <div class="header-actions">
      <button class="load-button" type="button" on:click={onLoad}>
        <Database size={15} />
        Load
      </button>
      <button
        class:offline={aggregate === 'OFFLINE'}
        class:degraded={aggregate === 'DEGRADED'}
        class="status-button"
        type="button"
        on:click={onHealth}
        aria-label="Open endpoint health"
      >
        <span class="state-dot"></span>
        {aggregate}
      </button>
      <button class="icon-button" type="button" on:click={onRefresh} aria-label="Refresh health" disabled={loading}>
        {#if loading}
          <Activity size={15} />
        {:else}
          <RefreshCw size={15} />
        {/if}
      </button>
    </div>
  </div>
  {#if progress.visible}
    <div class="global-progress">
      <div class="progress-copy">
        <span>{progress.label}</span>
        {#if progress.detail}<span class="dim">{progress.detail}</span>{/if}
      </div>
      <div class="progress-track">
        <div style={`width:${Math.min(100, Math.max(3, (progress.loaded / progress.total) * 100))}%`}></div>
      </div>
    </div>
  {/if}
</header>

<style>
  .icon-button {
    width: 2rem;
    height: 2rem;
    border: 1px solid var(--border);
    background: transparent;
    color: var(--text-dim);
    cursor: pointer;
  }

  .icon-button:hover {
    border-color: var(--accent);
    color: var(--accent);
  }

  .icon-button:disabled {
    cursor: wait;
    opacity: 0.6;
  }

  .global-progress {
    width: min(1600px, 100%);
    margin: 0.6rem auto 0;
  }

  .progress-copy {
    display: flex;
    justify-content: space-between;
    gap: 1rem;
    margin-bottom: 0.35rem;
    font-size: 0.68rem;
    font-weight: 700;
    letter-spacing: 0.06em;
    text-transform: uppercase;
  }

  .progress-track {
    height: 4px;
    background: rgba(255, 255, 255, 0.06);
    overflow: hidden;
  }

  .progress-track div {
    height: 100%;
    background: var(--accent);
    box-shadow: 0 0 12px rgba(0, 255, 136, 0.45);
    transition: width 160ms linear;
  }
</style>
