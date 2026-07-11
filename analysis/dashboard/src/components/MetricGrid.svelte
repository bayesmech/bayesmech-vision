<script lang="ts">
  export let metrics: Array<{ label: string; value: string | number }> = [];
</script>

<div class="metric-grid">
  {#each metrics as metric}
    <div class="metric-card">
      <div class="metric-value">{metric.value}</div>
      <div class="metric-label">{metric.label}</div>
    </div>
  {/each}
</div>
