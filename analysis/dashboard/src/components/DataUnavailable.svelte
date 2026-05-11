<script lang="ts">
  export let message = 'Data unavailable';
  export let detail = '';
</script>

<div class="empty-state">
  <div>{message}</div>
  {#if detail}
    <small>{detail}</small>
  {/if}
</div>
