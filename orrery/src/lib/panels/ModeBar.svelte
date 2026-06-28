<script lang="ts">
  // ModeBar (A7) — the System-view mode toggle: Observatory · Planetarium · Rewind.
  //   Observatory = full interactive instrument (all tiers).
  //   Planetarium = full-screen ambient Tier-1 (the overnight / second-screen view).
  //   Rewind      = scrub the run with the cyan time-shimmer (replay only).
  // Reflects uiStore.mode. Rewind is only offered when a scrubbable (playback)
  // transport is present — the live ws/Tauri feeds have no timeline to scrub.

  import { uiStore, type ViewMode } from '../stores/ui.svelte';

  let { canRewind = false }: { canRewind?: boolean } = $props();

  const modes = $derived<{ id: ViewMode; label: string; glyph: string; on: boolean }[]>([
    { id: 'observatory', label: 'Observatory', glyph: '✦', on: true },
    { id: 'planetarium', label: 'Planetarium', glyph: '◐', on: true },
    { id: 'rewind', label: 'Rewind', glyph: '⟲', on: canRewind },
  ]);

  function pick(m: ViewMode, on: boolean) {
    if (!on) return;
    uiStore.setMode(m);
  }
</script>

<div class="modebar" role="group" aria-label="view mode">
  {#each modes as m (m.id)}
    <button
      class="mbtn {m.id} {uiStore.mode === m.id ? 'active' : ''}"
      disabled={!m.on}
      title={m.on ? m.label : `${m.label} (needs a scrubbable run)`}
      onclick={() => pick(m.id, m.on)}
    >
      <span class="glyph">{m.glyph}</span>
      <span class="lbl">{m.label}</span>
    </button>
  {/each}
</div>

<style>
  .modebar {
    position: absolute;
    top: var(--chrome-inset);
    left: 50%;
    transform: translateX(-50%);
    display: flex;
    gap: var(--space-1);
    padding: 5px 6px;
    background: var(--panel);
    border: 1px solid var(--panel-edge);
    border-radius: var(--radius-pill);
    backdrop-filter: blur(8px);
    z-index: 18;
  }
  .mbtn {
    display: inline-flex;
    align-items: center;
    gap: var(--space-2);
    font-family: var(--font-grotesk);
    font-size: var(--text-xs);
    font-weight: 600;
    letter-spacing: 0.04em;
    padding: 6px 13px;
    border-radius: var(--radius-pill);
    border: 1px solid transparent;
    background: transparent;
    color: var(--text-dim);
    cursor: pointer;
    transition: color var(--dur-fast) var(--ease-standard),
      background var(--dur-fast) var(--ease-standard),
      border-color var(--dur-fast) var(--ease-standard);
  }
  .mbtn .glyph {
    font-size: var(--text-sm);
  }
  .mbtn:hover:not(:disabled):not(.active) {
    color: var(--starlight);
  }
  .mbtn.active {
    background: var(--void-3);
    color: var(--brass);
    border-color: var(--hairline);
  }
  .mbtn.planetarium.active {
    color: var(--frost);
  }
  .mbtn.rewind.active {
    color: var(--plasma-cyan);
  }
  .mbtn:disabled {
    opacity: 0.32;
    cursor: default;
  }
  /* tablet/narrow: collapse to glyphs AND drop below the top navbar so the
     centred mode toggle and the right-anchored navbar never overlap. */
  @media (max-width: 860px) {
    .modebar {
      top: 64px;
    }
    .mbtn .lbl {
      display: none;
    }
    .mbtn {
      padding: 8px 12px;
    }
  }
</style>
