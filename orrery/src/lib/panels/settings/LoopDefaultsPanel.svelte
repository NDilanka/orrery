<script lang="ts">
  // LoopDefaultsPanel — thin wrapper over GenericPanel (category 'loopDefaults').
  import GenericPanel from './GenericPanel.svelte';
  let { query = '', matched }: { query?: string; matched?: Set<string> } = $props();
</script>

<GenericPanel category="loopDefaults" {query} {matched} />
