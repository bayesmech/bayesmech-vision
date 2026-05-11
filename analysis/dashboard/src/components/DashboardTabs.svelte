<script lang="ts">
  import type { PanelId } from '$lib/types';

  export let active: PanelId = 'segmentation';
  export let onSelect: (panel: PanelId) => void;

  const tabs: Array<{ id: PanelId; key: string; label: string; description: string }> = [
    { id: 'segmentation', key: 'SEG', label: 'Segmentation', description: 'Semantic masks and object legends aligned to the selected frame.' },
    { id: 'motioncap', key: 'MOT', label: 'Motion Capture', description: 'RAFT tracks and segmentation trajectories.' },
    { id: 'sport', key: 'SPT', label: 'Sport Understanding', description: 'Surface pose estimation and 3D table trajectory state.' },
    { id: 'sensors', key: 'SNS', label: 'Sensor Data', description: 'IMU, geometry, GPS route, coverage, and SLAM path.' },
    { id: 'localization', key: 'LOC', label: 'Localization + Mapping', description: 'IDOSLAM poses, SIFT matches, road projection, and attitude.' }
  ];
</script>

<div class="tabs" role="tablist" aria-label="Dashboard panels">
  {#each tabs as tab}
    <button
      type="button"
      role="tab"
      aria-selected={active === tab.id}
      aria-controls={`panel-${tab.id}`}
      class:active={active === tab.id}
      on:click={() => onSelect(tab.id)}
    >
      <span class="shortcut">{tab.key}</span>
      <span class="copy">
        <span class="label">{tab.label}</span>
        <span class="description">{tab.description}</span>
      </span>
    </button>
  {/each}
</div>

<style>
  .tabs {
    position: sticky;
    top: 5.25rem;
    display: flex;
    flex-direction: column;
    gap: 0.75rem;
  }

  button {
    display: flex;
    width: 100%;
    gap: 0.85rem;
    padding: 1rem;
    border: 1px solid rgba(0, 255, 136, 0.35);
    border-radius: 0;
    background: rgba(0, 255, 136, 0.08);
    color: var(--text);
    cursor: pointer;
    text-align: left;
    transition: border 0.2s, background 0.2s, transform 0.2s;
  }

  button:hover {
    border-color: var(--accent);
    background: rgba(0, 255, 136, 0.12);
    transform: translateX(2px);
  }

  button.active {
    border-color: var(--accent);
    background: var(--accent);
    color: #001b0f;
    box-shadow: inset 0 0 0 1px rgba(0, 27, 15, 0.2);
  }

  .shortcut {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    flex: 0 0 2rem;
    width: 2rem;
    height: 2rem;
    border: 1px solid rgba(0, 255, 136, 0.35);
    background: rgba(0, 255, 136, 0.08);
    color: var(--accent);
    font-size: 0.7rem;
    font-weight: 700;
    letter-spacing: 0.08em;
  }

  button.active .shortcut {
    border-color: rgba(0, 27, 15, 0.3);
    background: rgba(0, 27, 15, 0.08);
    color: #001b0f;
  }

  .copy {
    display: flex;
    flex-direction: column;
    gap: 0.25rem;
    min-width: 0;
  }

  .label {
    font-size: 0.86rem;
    font-weight: 600;
    letter-spacing: 0.04em;
    text-transform: uppercase;
  }

  .description {
    color: var(--text-dim);
    font-size: 0.75rem;
    line-height: 1.5;
  }

  button.active .description {
    color: rgba(0, 27, 15, 0.72);
  }

  @media (max-width: 1024px) {
    .tabs {
      position: static;
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
    }
  }

  @media (max-width: 720px) {
    button:hover {
      transform: none;
    }
  }
</style>
