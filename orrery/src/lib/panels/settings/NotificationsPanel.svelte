<script lang="ts">
  // NotificationsPanel — thin wrapper over GenericPanel (category 'notifications').
  import GenericPanel from './GenericPanel.svelte';
  let { query = '', matched }: { query?: string; matched?: Set<string> } = $props();
</script>

<GenericPanel category="notifications" {query} {matched} />
