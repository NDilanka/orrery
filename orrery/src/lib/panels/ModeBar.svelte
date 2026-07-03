<script lang="ts">
  // ModeBar (A7) — the System-view mode toggle: Observatory · Ambient · Rewind.
  //   Observatory = full interactive instrument (all tiers).
  //   Ambient (internally 'planetarium') = full-screen Tier-1 (the overnight / second-screen view).
  //   Rewind      = scrub the run with the cyan time-shimmer (replay only).
  // Reflects uiStore.mode. Rewind is only offered when a scrubbable (playback)
  // transport is present — the live ws/Tauri feeds have no timeline to scrub.

  import { uiStore, type ViewMode } from '../stores/ui.svelte';

  let { canRewind = false }: { canRewind?: boolean } = $props();

  const modes = $derived<{ id: ViewMode; label: string; glyph: string; on: boolean }[]>([
    { id: 'observatory', label: 'Observatory', glyph: '✦', on: true },
    { id: 'planetarium', label: 'Ambient', glyph: '◐', on: true },
    { id: 'rewind', label: 'Rewind', glyph: '⟲', on: canRewind },
  ]);

  function pick(m: ViewMode, on: boolean) {
    if (!on) return;
    uiStore.setMode(m);
  }
</script>

<div class="seg modebar" role="group" aria-label="view mode">
  {#each modes as m (m.id)}
    <button
      class="seg-item mbtn {m.id} {uiStore.mode === m.id ? 'selected' : ''}"
      disabled={!m.on}
      title={m.on ? m.label : `${m.label} (needs a scrubbable run)`}
      aria-label={m.label}
      aria-pressed={uiStore.mode === m.id}
      onclick={() => pick(m.id, m.on)}
    >
      <span class="glyph">{m.glyph}</span>
      <span class="lbl">{m.label}</span>
    </button>
  {/each}
</div>

<style>
  .modebar {
    /* wave U2 Task 1: placed by the System dock's top bar (grid-area: topbar,
       centered column) instead of floating over the canvas at a hand-picked
       top/left offset — this is internal styling only now.
       M4.5: track shape/border/background/padding/gap now come from the shared
       `.seg` primitive (primitives.css) — only the glass backdrop is ModeBar-specific. */
    backdrop-filter: blur(8px);
  }
  .mbtn {
    /* M4.5: base shape/color/padding/font/transition now come from `.seg-item`
       (primitives.css) — this only adds the icon+label row layout `.seg-item`
       doesn't define, plus ModeBar's own letter-spacing. */
    display: inline-flex;
    align-items: center;
    gap: var(--space-2);
    letter-spacing: 0.04em;
  }
  .mbtn .glyph {
    font-size: var(--text-sm);
  }
  .mbtn:hover:not(:disabled):not(.selected) {
    color: var(--em-hi);
  }
  .mbtn:disabled {
    /* M4.5: dimmest text tier, not an opacity trick — no chromatic pixels here (modes
       are not alerts), so hierarchy is carried by lightness alone. */
    color: var(--em-faint);
    cursor: not-allowed;
  }
  /* tablet/narrow: collapse to glyphs — the top bar's own layout (+page.svelte
     .g-topbar) reserves room so this never runs under the floating navbar pill. */
  @media (max-width: 860px) {
    .mbtn .lbl {
      display: none;
    }
    .mbtn {
      padding: 8px 12px;
    }
  }
</style>
