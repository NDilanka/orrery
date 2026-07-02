<script lang="ts">
  // AlertBanner (wave U4 Task 3) — dismissible top-of-viewport strip for unattended-run
  // events (stores/alerts.svelte.ts does the edge-detection; this just renders the result).
  // Glyph + plain one-liner, never hue alone. No sound, no OS notification (native
  // notifications need a Tauri plugin — deferred, see the U4 report), no flashing (reduced
  // motion drops even the slide-in entrance).

  import { alertStore, type RunAlert } from '../stores/alerts.svelte';
  import { uiStore } from '../stores/ui.svelte';

  let { onJump }: { onJump: (loopId: string) => void } = $props();

  const GLYPH: Record<RunAlert['kind'], string> = {
    failed: '⚠',
    handoff: '◈',
    quota: '❄',
  };
</script>

{#if alertStore.alerts.length}
  <div class="alerts" role="alert">
    {#each alertStore.alerts as a (a.id)}
      <div class="bar {a.kind}" class:reduced={uiStore.reducedMotion}>
        <span class="glyph" aria-hidden="true">{GLYPH[a.kind]}</span>
        <span class="msg">{a.message}</span>
        {#if a.source === 'cosmos'}
          <button class="jump" onclick={() => onJump(a.loopId)}>enter loop →</button>
        {/if}
        <button class="dismiss" aria-label="dismiss alert" onclick={() => alertStore.dismiss(a.id)}
          >✕</button
        >
      </div>
    {/each}
  </div>
{/if}

<style>
  .alerts {
    /* a REAL flex item (routes/+page.svelte's `.stage` is a column flexbox) — takes its
       natural height at the very top and pushes `.stage-body` (navbar / ModeBar /
       ignite-fab, all position:absolute within it) down below it, rather than floating
       over them. */
    flex: none;
    display: flex;
    flex-direction: column;
  }
  .bar {
    display: flex;
    align-items: center;
    gap: var(--space-3);
    padding: 9px var(--chrome-inset);
    background: color-mix(in srgb, var(--crimson) 14%, var(--void-2));
    border-bottom: 1px solid color-mix(in srgb, var(--crimson) 35%, transparent);
    backdrop-filter: blur(8px);
    animation: slideDown var(--dur-mid) var(--ease-out);
  }
  .bar.reduced {
    animation: none;
  }
  @keyframes slideDown {
    from {
      transform: translateY(-100%);
    }
    to {
      transform: translateY(0);
    }
  }
  .bar.quota {
    background: color-mix(in srgb, var(--frost) 14%, var(--surface-1));
    border-bottom-color: color-mix(in srgb, var(--frost) 35%, transparent);
  }
  .glyph {
    flex: none;
    font-size: 15px;
    color: var(--crimson);
  }
  .bar.quota .glyph {
    color: var(--frost);
  }
  .msg {
    flex: 1;
    min-width: 0;
    font-family: var(--font-grotesk);
    font-size: var(--text-sm);
    font-weight: 600;
    letter-spacing: 0.01em;
    color: var(--starlight);
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .jump {
    flex: none;
    font-family: var(--font-grotesk);
    font-size: var(--text-xs);
    font-weight: 600;
    padding: 5px 12px;
    border-radius: var(--radius-pill);
    border: 1px solid var(--hairline);
    background: var(--void-3);
    color: var(--starlight);
    cursor: pointer;
    transition: border-color var(--dur-fast) var(--ease-standard);
  }
  .jump:hover {
    border-color: var(--brass);
  }
  .dismiss {
    flex: none;
    background: transparent;
    border: 1px solid var(--hairline);
    color: var(--text-dim);
    border-radius: var(--radius-pill);
    width: 22px;
    height: 22px;
    font-size: 10px;
    cursor: pointer;
  }
  .dismiss:hover {
    color: var(--starlight);
    border-color: var(--starlight);
  }

  @media (max-width: 640px) {
    .bar {
      padding: 8px var(--space-2);
      gap: var(--space-2);
    }
    .msg {
      font-size: var(--text-xs);
      white-space: normal;
    }
    .jump {
      padding: 4px 9px;
      font-size: 10px;
    }
  }
</style>
