<script lang="ts">
  // GeneralPanel — thin wrapper over GenericPanel (category 'general').
  import GenericPanel from './GenericPanel.svelte';
  let { query = '', matched }: { query?: string; matched?: Set<string> } = $props();
</script>

<GenericPanel category="general" {query} {matched} />
