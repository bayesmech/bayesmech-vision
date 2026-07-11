<script lang="ts">
  import { Upload, X } from 'lucide-svelte';
  import type { RecordingRow } from '$lib/types';

  export let open = false;
  export let recordings: RecordingRow[] = [];
  export let loading = false;
  export let activeName = '';
  export let onClose: () => void;
  export let onSelect: (recording: RecordingRow) => void;
  export let onLive: () => void;
  export let onUpload: (file: File) => void;

  let fileInput: HTMLInputElement;

  function pickFile() {
    fileInput?.click();
  }

  function fileChanged(event: Event) {
    const input = event.currentTarget as HTMLInputElement;
    const file = input.files?.[0];
    if (file) onUpload(file);
    input.value = '';
  }
</script>

{#if open}
  <div class="modal-backdrop" role="presentation" on:click={onClose} on:keydown={(event) => event.key === 'Escape' && onClose()}>
    <div class="recording-modal" role="dialog" aria-modal="true" aria-label="Load recording" tabindex="-1" on:click|stopPropagation on:keydown|stopPropagation>
      <header>
        <h2>Load Recording</h2>
        <button type="button" on:click={onClose} aria-label="Close">
          <X size={16} />
        </button>
      </header>
      <div class="modal-actions">
        <button type="button" class="button-outline" on:click={pickFile}>
          <Upload size={14} />
          Upload .vis.pb
        </button>
        <input bind:this={fileInput} type="file" accept=".pb,.vis.pb,application/x-protobuf" on:change={fileChanged} />
      </div>
      <div class="rows">
        <button type="button" class="recording-row live-row" on:click={onLive}>
          <div>
            <div class="recording-title">Live Stream</div>
            <div class="recording-meta">Subscribe to live dashboard frames</div>
          </div>
          <div class="live-dot"></div>
        </button>
        {#if loading}
          <div class="empty-state">Loading recordings</div>
        {:else if recordings.length === 0}
          <div class="empty-state">No recordings found</div>
        {:else}
          {#each recordings as recording}
            <button type="button" class:active={recording.name === activeName} class="recording-row" on:click={() => onSelect(recording)}>
              <div>
                <div class="recording-title">{recording.title || recording.name}</div>
                <div class="recording-meta">
                  {recording.name}
                  {#if recording.size_mb}
                    - {recording.size_mb} MB
                  {/if}
                </div>
              </div>
              <div class="badges">
                {#if recording.has_motioncap}<span class="motion">MOTION</span>{/if}
                {#if recording.has_pongtown}<span class="pong">SPORT</span>{/if}
                {#if recording.has_segmentation}<span class="seg">SEG</span>{/if}
                {#if recording.has_idoslam}<span class="slam">SLAM</span>{/if}
              </div>
            </button>
          {/each}
        {/if}
      </div>
    </div>
  </div>
{/if}

<style>
  .modal-backdrop {
    position: fixed;
    inset: 0;
    z-index: 9999;
    display: flex;
    align-items: center;
    justify-content: center;
    padding: 1rem;
    background: rgba(0, 0, 0, 0.85);
  }

  .recording-modal {
    display: flex;
    flex-direction: column;
    width: 100%;
    max-width: 560px;
    max-height: 70vh;
    background: #0a0a0a;
    border: 1px solid #1a1a1a;
    overflow: hidden;
  }

  header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 1rem 1.25rem;
    border-bottom: 1px solid #1a1a1a;
  }

  h2 {
    margin: 0;
    color: var(--accent);
    font-size: 0.8rem;
    font-weight: 600;
    letter-spacing: 0.1em;
    text-transform: uppercase;
  }

  header button {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    border: 0;
    background: transparent;
    color: var(--text-dim);
    cursor: pointer;
  }

  .modal-actions {
    padding: 0.75rem 1.25rem;
    border-bottom: 1px solid #1a1a1a;
  }

  input[type='file'] {
    display: none;
  }

  .rows {
    overflow: auto;
    min-height: 0;
  }

  .recording-row {
    display: grid;
    grid-template-columns: minmax(0, 1fr) auto;
    gap: 1rem;
    width: 100%;
    padding: 0.75rem 1.25rem;
    border: 0;
    border-bottom: 1px solid #111;
    background: transparent;
    color: var(--text);
    cursor: pointer;
    text-align: left;
  }

  .recording-row:hover,
  .recording-row.active {
    background: #111;
  }

  .live-row {
    background: rgba(238, 0, 51, 0.08);
  }

  .live-row:hover {
    background: rgba(238, 0, 51, 0.12);
  }

  .live-dot {
    align-self: center;
    width: 8px;
    height: 8px;
    background: #e03;
    box-shadow: 0 0 6px rgba(238, 0, 51, 0.6);
  }

  .recording-title {
    overflow: hidden;
    font-size: 0.82rem;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .recording-meta {
    margin-top: 0.25rem;
    color: #505050;
    font-size: 0.68rem;
    overflow-wrap: anywhere;
  }

  .badges {
    display: flex;
    align-items: center;
    justify-content: flex-end;
    gap: 0.35rem;
    flex-wrap: wrap;
  }

  .badges span {
    border: 1px solid currentColor;
    padding: 0.15rem 0.4rem;
    font-size: 0.58rem;
    font-weight: 600;
  }

  .motion {
    color: #ffaa00;
  }

  .pong {
    color: #ff35ff;
  }

  .seg {
    color: #00ff88;
  }

  .slam {
    color: #69a8ff;
  }
</style>
