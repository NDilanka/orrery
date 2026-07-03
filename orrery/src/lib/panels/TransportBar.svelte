<script lang="ts">
  // Replay transport control — play / pause / speed (1× 4× 16×) / scrub. Lets you
  // WATCH a run unfold and rewind it. The scrub slider is the "rewind" affordance:
  // dragging re-reduces the event prefix up to time T (idempotent, cheap). Only
  // shown in dev replay mode (the live Tauri/ws transports have no scrub).
  //
  // In REWIND mode (uiStore.rewind / the `rewind` prop) the scrub axis becomes a
  // full timeline: notable events — verdicts (seal/refute), strikes/rollbacks,
  // quota nights, merges, stops — are pinned as clickable ticks you can jump to
  // (grayscale per the M4.5 monochrome sweep — the only chromatic pins are the
  // genuine alerts, verdict-fail/handoff/quota).

  import type { PlaybackState, PlaybackTransport, TimelineMarker } from '../transport/replay';

  let {
    transport,
    state,
    rewind = false,
  }: {
    transport: PlaybackTransport | null;
    state: PlaybackState;
    rewind?: boolean;
  } = $props();

  const SPEEDS = [1, 4, 16];

  // pinned timeline markers (verdicts/strikes/quota…); recomputed when the
  // transport or the total changes (events are immutable once loaded).
  const markers = $derived<TimelineMarker[]>(
    transport && state.total ? transport.markers() : [],
  );

  function onScrub(e: Event) {
    const v = Number((e.target as HTMLInputElement).value);
    transport?.seek(v);
  }
  function jumpTo(m: TimelineMarker) {
    transport?.seek(m.index);
  }
  function pct(i: number): number {
    return Math.max(0, Math.min(100, (i / Math.max(1, state.total)) * 100));
  }
</script>

<div class="transport" class:rewind>
  {#if rewind}
    <span class="rwlabel mono">⟲ REWIND</span>
  {/if}

  <button
    class="btn btn-ghost btn-icon"
    aria-label={state.playing ? 'pause' : 'play'}
    onclick={() => transport?.toggle()}
  >
    {#if state.playing}❚❚{:else}▶{/if}
  </button>

  <button class="btn btn-ghost btn-icon" aria-label="restart" title="scrub to start" onclick={() => transport?.restart()}>
    ⏮
  </button>

  <div class="axis">
    <input
      class="scrub slider"
      type="range"
      min="0"
      max={Math.max(1, state.total)}
      value={state.cursor}
      step="1"
      aria-label="timeline scrub"
      aria-valuetext="event {state.cursor} of {state.total}"
      oninput={onScrub}
    />
    {#if rewind}
      <!-- pinned event ticks (clickable) — only in Rewind mode to keep the bar calm otherwise -->
      <div class="pins">
        {#each markers as m (m.index + m.kind)}
          <button
            class="pin {m.kind}"
            style="left:{pct(m.index)}%"
            title={m.label}
            aria-label={m.label}
            onclick={() => jumpTo(m)}
          ></button>
        {/each}
      </div>
    {/if}
  </div>

  <span class="pos mono">{state.cursor}/{state.total}</span>

  <div class="seg speeds" role="group" aria-label="playback speed">
    {#each SPEEDS as sp (sp)}
      <button
        class="seg-item sp {state.speed === sp ? 'selected' : ''}"
        aria-pressed={state.speed === sp}
        onclick={() => transport?.setSpeed(sp)}
      >
        {sp}×
      </button>
    {/each}
  </div>
</div>

<style>
  .transport {
    /* placed by the System dock's single bottom bar (+page.svelte merges this
       with RunControlBar into one full-width dock — plan §M4.3/M4.5). M4.5:
       no more pill-card chrome of its own (background/border/backdrop-filter,
       clamped width) — the dock container supplies that; this just lays the
       controls out and lets the scrubber (.axis, flex:1) fill the rest. */
    display: flex;
    align-items: center;
    gap: var(--space-2);
    width: 100%;
  }
  .rwlabel {
    font-size: var(--text-2xs);
    letter-spacing: 0.16em;
    color: var(--em-mid);
    padding-right: var(--space-1);
  }
  .axis {
    position: relative;
    flex: 1;
    display: flex;
    align-items: center;
  }
  .scrub {
    flex: 1;
    accent-color: var(--brass);
    cursor: pointer;
    height: 4px;
  }
  .pins {
    position: absolute;
    inset: 0;
    pointer-events: none;
  }
  .pin {
    position: absolute;
    top: 50%;
    width: 9px;
    height: 14px;
    transform: translate(-50%, -50%);
    padding: 0;
    border: none;
    background: transparent;
    cursor: pointer;
    pointer-events: auto;
  }
  /* the visible tick (a vertical bar) */
  .pin::after {
    content: '';
    position: absolute;
    left: 50%;
    top: 0;
    transform: translateX(-50%);
    width: 2px;
    height: 100%;
    border-radius: 1px;
    background: var(--text-faint);
    transition: transform var(--dur-fast) var(--ease-standard);
  }
  .pin:hover::after {
    transform: translateX(-50%) scaleY(1.4);
  }
  /* per-kind marker mapping (M4.5 monochrome sweep), semantically:
       verdict-pass → status-ok    (grayscale — a certified pass; status-ok-core
                                    already resolves to --em-hi post-M4.1)
       verdict-fail → status-err   (the one non-alert-adjacent hue: a refuted /
                                    false-green verdict reads as an error)
       rollback     → status-run   (grayscale — status-run-core = --em-hi too;
                                    distinguished from pass by tick position/tooltip,
                                    never by hue alone)
       quota        → status-warn  (amber — a quota-wait IS a genuine attention
                                    window, not mere status; was --frost, a plain
                                    cool gray that undersold it)
       handoff      → status-warn  (amber — a human-handoff moment reads as
                                    "needs attention", matching the app's NEEDS YOU)
       pr           → --em-mid     (grayscale — was --brass; brass now carries the
                                    "identity/certification" connotation elsewhere
                                    (VerdictPanel's seal), so a plain marker pin
                                    uses an explicit emphasis tier instead)
       stop         → status-idle  (grayscale, dimmest tier — a quiet at-rest marker)
  */
  .pin.verdict-pass::after { background: var(--status-ok-core); }
  .pin.verdict-fail::after { background: var(--status-err-core); }
  .pin.rollback::after { background: var(--status-run-core); }
  .pin.quota::after { background: var(--status-warn-core); }
  .pin.handoff::after { background: var(--status-warn-core); }
  .pin.pr::after { background: var(--em-mid); }
  .pin.stop::after { background: var(--status-idle-core); }
  .pos {
    /* 10.5px was one of the audit's near-duplicate micro-sizes (#1); collapsed onto
       --text-2xs alongside ShareButton's matching .urlfield readout. */
    font-size: var(--text-2xs);
    color: var(--text-meta);
    min-width: 48px;
    text-align: right;
  }
</style>
