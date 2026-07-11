<script lang="ts">
  import { RefreshCw, X } from 'lucide-svelte';
  import type { EndpointCheck } from '$lib/types';

  export let open = false;
  export let checks: EndpointCheck[] = [];
  export let loading = false;
  export let onClose: () => void;
  export let onRefresh: () => void;
</script>

{#if open}
  <div class="health-backdrop" role="presentation" on:click={onClose}>
    <div class="health-dialog" role="dialog" aria-modal="true" aria-label="Endpoint health" tabindex="-1" on:click|stopPropagation on:keydown|stopPropagation>
      <header>
        <div>
          <h2>Endpoint Health</h2>
          <p>HTTP data path, optional analysis endpoints, and active recording checks.</p>
        </div>
        <button type="button" on:click={onClose} aria-label="Close"><X size={16} /></button>
      </header>
      <div class="actions">
        <button type="button" class="button-outline" on:click={onRefresh} disabled={loading}>
          <RefreshCw size={14} />
          Refresh
        </button>
      </div>
      <div class="endpoint-list">
        {#each checks as check}
          <div class="endpoint-row">
            <div class:ok={check.status === 'ok'} class:failed={check.status === 'failed'} class="state">
              <span></span>
              {check.status === 'ok' ? 'OK' : 'FAILED'}
            </div>
            <div class="endpoint-detail">
              <div class="endpoint-title">
                <span>{check.name}</span>
                <i>{check.kind}</i>
                {#if check.latencyMs !== undefined}<b>{check.latencyMs} ms</b>{/if}
              </div>
              <div class="url">{check.url}</div>
              {#if check.detail}<div class="detail">{check.detail}</div>{/if}
            </div>
          </div>
        {/each}
      </div>
    </div>
  </div>
{/if}

<style>
  .health-backdrop {
    position: fixed;
    inset: 0;
    z-index: 2000;
    display: flex;
    align-items: flex-start;
    justify-content: flex-end;
    padding: 4.5rem 2rem 2rem;
    background: rgba(0, 0, 0, 0.55);
  }

  .health-dialog {
    display: flex;
    flex-direction: column;
    width: min(760px, calc(100vw - 2rem));
    max-height: min(760px, calc(100vh - 6rem));
    background: #050505;
    border: 1px solid var(--border);
    overflow: hidden;
  }

  header {
    display: flex;
    align-items: flex-start;
    justify-content: space-between;
    gap: 1rem;
    padding: 1rem;
    border-bottom: 1px solid var(--border);
  }

  h2 {
    margin: 0 0 0.25rem;
    color: var(--accent);
    font-size: 0.9rem;
    letter-spacing: 0.08em;
    text-transform: uppercase;
  }

  p {
    margin: 0;
    color: var(--text-dim);
    font-size: 0.75rem;
  }

  header button {
    width: 2rem;
    height: 2rem;
    border: 1px solid var(--border);
    background: transparent;
    color: var(--text-dim);
    cursor: pointer;
  }

  .actions {
    padding: 0.75rem 1rem;
    border-bottom: 1px solid var(--border);
  }

  .endpoint-list {
    overflow: auto;
    padding: 0 1rem;
  }

  .endpoint-row {
    display: grid;
    grid-template-columns: 5.2rem minmax(0, 1fr);
    gap: 0.75rem;
    padding: 0.75rem 0;
    border-bottom: 1px solid var(--border);
  }

  .state {
    display: flex;
    align-items: center;
    gap: 0.45rem;
    color: var(--error);
    font-size: 0.7rem;
    letter-spacing: 0.06em;
    text-transform: uppercase;
  }

  .state.ok {
    color: var(--success);
  }

  .state span {
    width: 0.45rem;
    height: 0.45rem;
    background: currentColor;
  }

  .endpoint-title {
    display: flex;
    align-items: center;
    gap: 0.5rem;
    flex-wrap: wrap;
    font-size: 0.8rem;
  }

  .endpoint-title i,
  .endpoint-title b {
    border: 1px solid var(--border);
    padding: 0.1rem 0.3rem;
    color: var(--text-dim);
    font-size: 0.62rem;
    font-style: normal;
    font-weight: 500;
    letter-spacing: 0.05em;
    text-transform: uppercase;
  }

  .url,
  .detail {
    margin-top: 0.25rem;
    color: var(--text-dim);
    font-size: 0.72rem;
    line-height: 1.35;
    overflow-wrap: anywhere;
  }
</style>
