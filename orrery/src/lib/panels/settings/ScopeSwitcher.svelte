<script lang="ts">
  // ScopeSwitcher — a .seg toggling settingsStore.scope between User and Workspace. For v1
  // the Workspace scope shows a lightweight note pointing at a loop's Tuning Console for
  // per-loop overrides (we don't duplicate that surface here).
  import { settingsStore } from '../../stores/settings.svelte';

  const SCOPES = [
    { value: 'user', label: 'User' },
    { value: 'workspace', label: 'Workspace' },
  ] as const;

  // Roving-tabindex arrow-key nav (mirrors Segmented.svelte): a radiogroup with only a roving
  // tabindex but no arrow handler is a keyboard dead-end (WAI-ARIA radiogroup pattern).
  let btns = $state<HTMLButtonElement[]>([]);

  function move(i: number, dir: 1 | -1) {
    const n = (i + dir + SCOPES.length) % SCOPES.length;
    settingsStore.scope = SCOPES[n].value;
    btns[n]?.focus();
  }
  function onKey(e: KeyboardEvent, i: number) {
    if (e.key === 'ArrowRight' || e.key === 'ArrowDown') {
      e.preventDefault();
      move(i, 1);
    } else if (e.key === 'ArrowLeft' || e.key === 'ArrowUp') {
      e.preventDefault();
      move(i, -1);
    }
  }
</script>

<div class="scopewrap">
  <div class="seg" role="radiogroup" aria-label="Settings scope">
    {#each SCOPES as s, i (s.value)}
      <button
        bind:this={btns[i]}
        type="button"
        class="seg-item"
        class:selected={settingsStore.scope === s.value}
        role="radio"
        aria-checked={settingsStore.scope === s.value}
        tabindex={settingsStore.scope === s.value ? 0 : -1}
        onclick={() => (settingsStore.scope = s.value)}
        onkeydown={(e) => onKey(e, i)}
      >
        {s.label}
      </button>
    {/each}
  </div>
  {#if settingsStore.scope === 'workspace'}
    <p class="note" role="note">
      Editing defaults for new loops; open a loop's Tuning Console to override it.
    </p>
  {/if}
</div>

<style>
  .scopewrap {
    position: relative;
    flex: none;
  }
  .note {
    position: absolute;
    top: calc(100% + 6px);
    left: 0;
    z-index: 1;
    width: 220px;
    margin: 0;
    padding: var(--space-2) var(--space-3);
    border: 1px solid var(--hairline);
    border-radius: var(--radius-sm);
    background: var(--surface-raised);
    box-shadow: 0 6px 16px rgba(0, 0, 0, 0.4);
    font-size: var(--text-2xs);
    color: var(--em-low);
    line-height: 1.45;
  }
</style>
