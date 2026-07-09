<script lang="ts">
  // SettingsNav — the left rail of the six categories. Arrow-key navigable (roving tabindex),
  // shows per-category match counts + dims empty categories while a search is active.
  import type { Settings } from '../../settings/schema';

  let {
    active,
    onSelect,
    counts,
  }: {
    active: keyof Settings;
    onSelect: (cat: keyof Settings) => void;
    counts?: Record<string, number>;
  } = $props();

  const CATEGORIES: { cat: keyof Settings; label: string; icon: string }[] = [
    { cat: 'general', label: 'General', icon: '⚙' },
    { cat: 'appearance', label: 'Appearance', icon: '◐' },
    { cat: 'loopDefaults', label: 'Loops & Defaults', icon: '✦' },
    { cat: 'notifications', label: 'Notifications', icon: '◈' },
    { cat: 'ai', label: 'AI / Models', icon: '◇' },
    { cat: 'diagnostics', label: 'About & Diagnostics', icon: 'ⓘ' },
  ];

  const searching = $derived(!!counts);
  let btns = $state<HTMLButtonElement[]>([]);

  function onKey(e: KeyboardEvent, i: number) {
    if (e.key === 'ArrowDown') {
      e.preventDefault();
      btns[(i + 1) % CATEGORIES.length]?.focus();
    } else if (e.key === 'ArrowUp') {
      e.preventDefault();
      btns[(i - 1 + CATEGORIES.length) % CATEGORIES.length]?.focus();
    } else if (e.key === 'Enter' || e.key === ' ') {
      e.preventDefault();
      onSelect(CATEGORIES[i].cat);
    }
  }
</script>

<nav class="nav" aria-label="Settings categories">
  {#each CATEGORIES as c, i (c.cat)}
    {@const count = counts?.[c.cat] ?? 0}
    <button
      bind:this={btns[i]}
      type="button"
      class="navitem"
      class:active={active === c.cat}
      class:dimmed={searching && count === 0}
      aria-current={active === c.cat ? 'page' : undefined}
      tabindex={active === c.cat ? 0 : -1}
      onclick={() => onSelect(c.cat)}
      onkeydown={(e) => onKey(e, i)}
    >
      <span class="icon" aria-hidden="true">{c.icon}</span>
      <span class="lbl">{c.label}</span>
      {#if searching && count > 0}<span class="count num">{count}</span>{/if}
    </button>
  {/each}
</nav>

<style>
  .nav {
    display: flex;
    flex-direction: column;
    gap: 2px;
    overflow-y: auto;
    min-width: 0;
  }
  .navitem {
    display: flex;
    align-items: center;
    gap: var(--space-2);
    width: 100%;
    padding: var(--space-2) var(--space-3);
    border: none;
    border-left: 2px solid transparent;
    border-radius: var(--radius-sm);
    background: transparent;
    font-family: var(--font-grotesk);
    font-size: var(--text-sm);
    color: var(--em-low);
    text-align: left;
    cursor: pointer;
    transition:
      background-color var(--dur-feedback) var(--ease-standard),
      color var(--dur-feedback) var(--ease-standard),
      border-color var(--dur-feedback) var(--ease-standard);
  }
  .navitem:hover {
    background: var(--surface-hover);
    color: var(--em-mid);
  }
  .navitem.active {
    background: var(--surface-hover);
    border-left-color: var(--em-hi);
    color: var(--em-hi);
  }
  .navitem.dimmed {
    opacity: 0.4;
  }
  .icon {
    flex: none;
    width: 16px;
    text-align: center;
    color: var(--em-faint);
  }
  .navitem.active .icon {
    color: var(--em-mid);
  }
  .lbl {
    flex: 1;
    min-width: 0;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .count {
    flex: none;
    font-size: var(--text-2xs);
    color: var(--em-faint);
    padding: 0 5px;
    border: 1px solid var(--hairline);
    border-radius: var(--radius-pill);
  }
</style>
