<script lang="ts">
  // GenericPanel — the workhorse: renders every scalar setting in a category as a SettingRow,
  // grouped under a .panel-hd with a "reset section" link. When searching, it filters to the
  // matched key set and threads the query down so rows filter + highlight. Zero per-panel code:
  // the six category panels are all thin wrappers over this.
  import { settingsStore } from '../../stores/settings.svelte';
  import { settingsForCategory, type Settings } from '../../settings/schema';
  import SettingRow from './SettingRow.svelte';

  let {
    category,
    query = '',
    matched,
  }: { category: keyof Settings; query?: string; matched?: Set<string> } = $props();

  const LABELS: Record<keyof Settings, string> = {
    version: '',
    general: 'General',
    appearance: 'Appearance',
    loopDefaults: 'Loops & Defaults',
    notifications: 'Notifications',
    ai: 'AI / Models',
    diagnostics: 'About & Diagnostics',
  };

  const metas = $derived(
    settingsForCategory(category).filter((m) => !matched || matched.has(m.key)),
  );
</script>

<section class="panel">
  <div class="panel-hd">
    <span>{LABELS[category]}</span>
    <button
      type="button"
      class="reset-section"
      onclick={() => settingsStore.resetSection(category)}
    >
      Reset section
    </button>
  </div>

  {#if metas.length === 0}
    <p class="empty">No matching settings.</p>
  {:else}
    <div class="rows">
      {#each metas as m (m.key)}
        <SettingRow meta={m} {query} />
      {/each}
    </div>
  {/if}
</section>

<style>
  .panel {
    /* the .panel primitive supplies surface/border/shadow; only layout is local */
    min-width: 0;
  }
  .reset-section {
    background: none;
    border: none;
    padding: 0;
    font-family: var(--font-grotesk);
    font-size: var(--text-2xs);
    font-weight: 500;
    letter-spacing: 0.02em;
    text-transform: none;
    color: var(--em-low);
    cursor: pointer;
    transition: color var(--dur-feedback) var(--ease-standard);
  }
  .reset-section:hover {
    color: var(--em-hi);
  }
  .empty {
    margin: 0;
    font-size: var(--text-xs);
    color: var(--em-faint);
  }
</style>
