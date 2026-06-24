<script lang="ts">
  // Replay transport control — play / pause / speed (1× 4× 16×) / scrub. Lets you
  // WATCH a run unfold and rewind it. The scrub slider is the "rewind" affordance:
  // dragging re-reduces the event prefix up to time T (idempotent, cheap). Only
  // shown in dev replay mode (the live Tauri/ws transports have no scrub).
  //
  // In REWIND mode (uiStore.rewind / the `rewind` prop) the bar grows into a full
  // timeline: notable events — verdicts (seal/refute), strikes/rollbacks, quota
  // nights, merges, stops — are pinned as clickable ticks you can jump to, framed
  // in the Plasma-cyan time-shimmer (plan §3).

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
    class="tbtn play"
    aria-label={state.playing ? 'pause' : 'play'}
    onclick={() => transport?.toggle()}
  >
    {#if state.playing}❚❚{:else}▶{/if}
  </button>

  <button class="tbtn" aria-label="restart" title="scrub to start" onclick={() => transport?.restart()}>
    ⏮
  </button>

  <div class="axis">
    <input
      class="scrub"
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

  <div class="speeds">
    {#each SPEEDS as sp (sp)}
      <button
        class="tbtn sp {state.speed === sp ? 'active' : ''}"
        onclick={() => transport?.setSpeed(sp)}
      >
        {sp}×
      </button>
    {/each}
  </div>
</div>

<style>
  .transport {
    /* positioned by the parent .bottom-dock (sits just above the cost strip) */
    display: flex;
    align-items: center;
    gap: var(--space-2);
    padding: 7px 11px;
    width: min(620px, 80vw);
    background: var(--panel);
    border: 1px solid var(--panel-edge);
    border-radius: var(--radius-pill);
    backdrop-filter: blur(8px);
    z-index: 12;
  }
  /* Rewind mode: grow + frame in the cyan time-shimmer */
  .transport.rewind {
    width: min(820px, 92vw);
    border-color: color-mix(in srgb, var(--plasma-cyan) 45%, transparent);
    box-shadow: 0 0 22px color-mix(in srgb, var(--plasma-cyan) 18%, transparent);
  }
  .rwlabel {
    font-size: var(--text-2xs);
    letter-spacing: 0.16em;
    color: var(--plasma-cyan);
    padding-right: var(--space-1);
  }
  .tbtn {
    font-family: var(--font-grotesk);
    font-size: var(--text-sm);
    font-weight: 600;
    min-width: 30px;
    padding: 5px 9px;
    border-radius: var(--radius-pill);
    border: 1px solid var(--hairline);
    background: var(--void-3);
    color: var(--starlight);
    cursor: pointer;
    transition: border-color var(--dur-fast) var(--ease-standard),
      background var(--dur-fast) var(--ease-standard);
  }
  .tbtn:hover {
    border-color: color-mix(in srgb, var(--brass) 45%, transparent);
  }
  .tbtn.play {
    color: var(--amber);
    border-color: color-mix(in srgb, var(--amber) 40%, transparent);
  }
  .tbtn.sp {
    min-width: 0;
    font-size: var(--text-xs);
    padding: var(--space-1) var(--space-2);
    color: var(--text-dim);
  }
  .tbtn.sp.active {
    color: var(--plasma-cyan);
    border-color: color-mix(in srgb, var(--plasma-cyan) 45%, transparent);
    background: color-mix(in srgb, var(--plasma-cyan) 8%, transparent);
  }
  .speeds {
    display: flex;
    gap: var(--space-1);
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
  .transport.rewind .scrub {
    accent-color: var(--plasma-cyan);
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
  .pin.verdict-pass::after { background: var(--plasma-green); }
  .pin.verdict-fail::after { background: var(--crimson); }
  .pin.rollback::after { background: var(--plasma-cyan); }
  .pin.quota::after { background: var(--frost); }
  .pin.handoff::after { background: var(--crimson); }
  .pin.pr::after { background: var(--brass); }
  .pin.stop::after { background: var(--ember); }
  .pos {
    font-size: 10.5px;
    color: var(--text-meta);
    min-width: 48px;
    text-align: right;
  }
</style>
