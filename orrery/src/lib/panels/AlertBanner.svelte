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

  // Cap the visible stack — a multi-loop bad night (several failures + handoffs) shouldn't
  // fill the viewport. The store has no ordering concept of its own (plain insertion order,
  // see alerts.svelte.ts), so severity ordering lives here: failures sort ahead of the amber
  // kinds (handoff/quota), stable otherwise.
  const MAX_VISIBLE = 3;
  const SEVERITY_RANK: Record<RunAlert['kind'], number> = { failed: 0, handoff: 1, quota: 1 };

  let expanded = $state(false);

  let sorted = $derived(
    [...alertStore.alerts].sort((a, b) => SEVERITY_RANK[a.kind] - SEVERITY_RANK[b.kind]),
  );
  let overflow = $derived(sorted.length > MAX_VISIBLE);
  let visible = $derived(expanded ? sorted : sorted.slice(0, MAX_VISIBLE));
  let hidden = $derived(sorted.slice(MAX_VISIBLE));
  // never hue-alone: the summary's amber/red family is a shorthand for "does the hidden
  // tail contain a failure", but the "+N more" text carries the actual information.
  let hiddenHasFailure = $derived(hidden.some((a) => a.kind === 'failed'));
</script>

{#if alertStore.alerts.length}
  <div class="alerts" role="alert">
    {#each visible as a (a.id)}
      <div class="bar {a.kind}" class:reduced={uiStore.reducedMotion}>
        <span class="glyph" aria-hidden="true">{GLYPH[a.kind]}</span>
        <span class="msg">{a.message}</span>
        {#if a.source === 'cosmos'}
          <button class="jump btn btn-ghost btn-sm" onclick={() => onJump(a.loopId)}>enter loop →</button>
        {/if}
        <button
          class="dismiss btn btn-ghost btn-icon"
          aria-label="dismiss alert"
          onclick={() => alertStore.dismiss(a.id)}>✕</button
        >
      </div>
    {/each}
    {#if overflow}
      <button
        type="button"
        class="bar summary"
        class:sev-amber={!hiddenHasFailure}
        class:reduced={uiStore.reducedMotion}
        onclick={() => (expanded = !expanded)}
      >
        <span class="glyph" aria-hidden="true">{hiddenHasFailure ? GLYPH.failed : GLYPH.handoff}</span>
        <span class="msg">{expanded ? 'show less' : `+${hidden.length} more`}</span>
      </button>
    {/if}
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
  /* M4.5: handoff and quota are both "needs-you" alerts (docs/ui-modernization-plan.md §5;
     matches Hud.svelte's status-pill.frost/.beacon reclassification) — only a genuine
     failure stays on the base crimson tint above; these two share the amber family. */
  .bar.quota,
  .bar.handoff {
    background: color-mix(in srgb, var(--amber) 14%, var(--void-2));
    border-bottom-color: color-mix(in srgb, var(--amber) 35%, transparent);
  }
  /* summary row (overflow cap) — a real <button> so it's keyboard-reachable; reset the
     native button chrome back to the plain `.bar` look. `.sev-amber` is a severity
     shorthand (not a real alert kind) for "no failure in the hidden tail"; unset it falls
     through to the base crimson tint above, same family as a failed alert. */
  .bar.summary {
    appearance: none;
    width: 100%;
    margin: 0;
    border: none;
    border-bottom: 1px solid color-mix(in srgb, var(--crimson) 35%, transparent);
    font: inherit;
    text-align: left;
    cursor: pointer;
  }
  .bar.summary.sev-amber {
    background: color-mix(in srgb, var(--amber) 14%, var(--void-2));
    border-bottom-color: color-mix(in srgb, var(--amber) 35%, transparent);
  }
  .bar.summary.sev-amber .glyph {
    color: var(--amber);
  }
  .glyph {
    flex: none;
    font-size: var(--text-lg);
    color: var(--crimson);
  }
  .bar.quota .glyph,
  .bar.handoff .glyph {
    color: var(--amber);
  }
  .msg {
    flex: 1;
    min-width: 0;
    font-family: var(--font-grotesk);
    font-size: var(--text-sm);
    font-weight: 600;
    letter-spacing: 0.01em;
    color: var(--em-hi);
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  /* .btn/.btn-ghost/.btn-sm (primitives.css) supply the shape/border/hover — matches
     LogPanel's "jump to newest" chip, the app's one ghost-button convention. */
  .jump {
    flex: none;
  }
  /* .btn-icon squares the padding for the ✕ glyph (matches TransportBar's icon buttons). */
  .dismiss {
    flex: none;
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
  }
</style>
