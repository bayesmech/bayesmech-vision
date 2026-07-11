<script lang="ts">
  export let title = '';
  export let className = '';
</script>

<section class={`panel ${className}`}>
  {#if title || $$slots.actions}
    <div class="panel-header">
      {#if title}
        <h2 class="panel-title">{title}</h2>
      {/if}
      {#if $$slots.actions}
        <slot name="actions" />
      {/if}
    </div>
  {/if}
  <slot />
</section>
