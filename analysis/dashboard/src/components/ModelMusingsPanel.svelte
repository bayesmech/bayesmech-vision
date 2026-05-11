<script lang="ts">
  import type { ModelMusingsData } from '$lib/types';
  import DataUnavailable from './DataUnavailable.svelte';

  export let data: ModelMusingsData;
  export let recordingName = '';

  function markdown(value: string) {
    const escaped = escapeHtml(value || '');
    const lines = escaped.split('\n');
    const out: string[] = [];
    let inCode = false;
    let paragraph: string[] = [];
    const flush = () => {
      if (!paragraph.length) return;
      out.push(`<p>${paragraph.join('<br>')}</p>`);
      paragraph = [];
    };
    for (const line of lines) {
      if (line.trim().startsWith('```')) {
        flush();
        if (inCode) out.push('</code></pre>');
        else out.push('<pre><code>');
        inCode = !inCode;
        continue;
      }
      if (inCode) {
        out.push(`${line}\n`);
        continue;
      }
      if (/^#{1,4}\s/.test(line)) {
        flush();
        const level = Math.min(4, line.match(/^#+/)?.[0].length ?? 2);
        out.push(`<h${level}>${line.replace(/^#+\s*/, '')}</h${level}>`);
      } else if (line.trim() === '') {
        flush();
      } else {
        paragraph.push(line.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>').replace(/`([^`]+)`/g, '<code>$1</code>'));
      }
    }
    flush();
    return out.join('');
  }

  function escapeHtml(value: string) {
    return value.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
  }
</script>

<div class="model-panel">
  <div class="panel-header">
    <div>
      <h2 class="panel-title">Model Musings</h2>
      {#if recordingName}<div class="recording-name">{recordingName}</div>{/if}
    </div>
  </div>
  <div class="scroll-area messages">
    {#if data.status === 'loading'}
      <DataUnavailable message="Loading generated analysis" />
    {:else if data.status === 'error'}
      <DataUnavailable message="Model analysis failed" detail={data.error ?? ''} />
    {:else if data.status === 'empty'}
      <DataUnavailable message="No Model Musings artifact" />
    {:else}
      {#if data.title || data.summaryText}
        <article class="message model">
          <div class="role">Model</div>
          <div class="markdown">
            {@html markdown((data.title ? `## ${data.title}\n\n` : '') + (data.summaryText ?? ''))}
          </div>
        </article>
      {/if}
      {#if data.parameters.length}
        <article class="message model">
          <div class="role">Parameters</div>
          <div class="table-wrap">
            <table>
              <thead><tr><th>Name</th><th>Value</th><th>Unit</th></tr></thead>
              <tbody>
                {#each data.parameters as parameter}
                  <tr><td>{parameter.name}</td><td>{parameter.value}</td><td>{parameter.unit}</td></tr>
                {/each}
              </tbody>
            </table>
          </div>
        </article>
      {/if}
      {#each data.turns as turn}
        <article class:user={turn.role === 'user'} class="message">
          <div class="role">{turn.role || 'turn'}</div>
          <div class="markdown">{@html markdown(turn.text)}</div>
        </article>
      {/each}
    {/if}
  </div>
</div>

<style>
  .model-panel {
    display: flex;
    flex-direction: column;
    min-height: 0;
    flex: 1 1 0;
  }

  .recording-name {
    max-width: 50vw;
    margin-top: 0.25rem;
    overflow: hidden;
    color: var(--text-dim);
    font-size: 0.68rem;
    letter-spacing: 0.04em;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .messages {
    flex: 1 1 0;
    padding-right: 0.35rem;
  }

  .message {
    margin-bottom: 1rem;
    padding: 0.15rem 0 0.1rem 0.85rem;
    border-left: 2px solid rgba(0, 255, 136, 0.36);
  }

  .message.user {
    border-left-color: rgba(255, 255, 255, 0.32);
  }

  .role {
    margin-bottom: 0.4rem;
    color: var(--text-dim);
    font-size: 0.66rem;
    font-weight: 700;
    letter-spacing: 0.08em;
    text-transform: uppercase;
  }

  :global(.markdown) {
    font-size: 0.8rem;
    line-height: 1.55;
  }

  :global(.markdown h1),
  :global(.markdown h2),
  :global(.markdown h3),
  :global(.markdown h4) {
    margin: 0.75rem 0 0.5rem;
    color: var(--text);
    font-size: 0.86rem;
    font-weight: 700;
    letter-spacing: 0.03em;
    text-transform: uppercase;
  }

  :global(.markdown p) {
    margin: 0 0 0.75rem;
  }

  :global(.markdown code) {
    border: 1px solid rgba(255, 255, 255, 0.08);
    background: rgba(255, 255, 255, 0.06);
    padding: 0.05rem 0.25rem;
  }

  :global(.markdown pre) {
    overflow: auto;
    max-height: 180px;
    border: 1px solid var(--border);
    background: #030303;
    padding: 0.75rem;
    font-size: 0.76rem;
    line-height: 1.55;
  }

  .table-wrap {
    overflow: auto;
    border: 1px solid var(--border);
  }

  table {
    width: 100%;
    border-collapse: collapse;
    font-size: 0.76rem;
  }

  th {
    background: rgba(0, 255, 136, 0.08);
    color: var(--accent);
    font-size: 0.68rem;
    letter-spacing: 0.06em;
    text-transform: uppercase;
  }

  th,
  td {
    padding: 0.55rem 0.65rem;
    border-bottom: 1px solid var(--border);
    text-align: left;
  }
</style>
