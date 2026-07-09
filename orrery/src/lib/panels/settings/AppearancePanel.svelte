<script lang="ts">
  // AppearancePanel — thin wrapper over GenericPanel (category 'appearance').
  import GenericPanel from './GenericPanel.svelte';
  let { query = '', matched }: { query?: string; matched?: Set<string> } = $props();
</script>

<GenericPanel category="appearance" {query} {matched} />
