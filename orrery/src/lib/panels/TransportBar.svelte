<script lang="ts">
  // Replay transport control — play / pause / speed (1× 4× 16×) / scrub. Lets you
  // WATCH a run unfold and rewind it. The scrub slider is the "rewind" affordance:
  // dragging re-reduces the event prefix up to time T (idempotent, cheap). Only
  // shown in dev replay mode (the live Tauri transport has no scrub).

  import type { PlaybackState, PlaybackTransport } from '../transport/replay';

  let {
    transport,
    state,
  }: {
    transport: PlaybackTransport | null;
    state: PlaybackState;
  } = $props();

  const SPEEDS = [1, 4, 16];

  function onScrub(e: Event) {
    const v = Number((e.target as HTMLInputElement).value);
    transport?.seek(v);
  }
</script>

<div class="transport">
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

  <input
    class="scrub"
    type="range"
    min="0"
    max={Math.max(1, state.total)}
    value={state.cursor}
    step="1"
    aria-label="timeline scrub"
    oninput={onScrub}
  />

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
    position: absolute;
    bottom: 130px;
    left: 50%;
    transform: translateX(-50%);
    display: flex;
    align-items: center;
    gap: 8px;
    padding: 7px 11px;
    width: min(620px, 80vw);
    background: var(--panel);
    border: 1px solid var(--panel-edge);
    border-radius: var(--radius-pill);
    backdrop-filter: blur(8px);
    z-index: 12;
  }
  .tbtn {
    font-family: var(--font-grotesk);
    font-size: 12px;
    font-weight: 600;
    min-width: 30px;
    padding: 5px 9px;
    border-radius: var(--radius-pill);
    border: 1px solid var(--hairline);
    background: var(--void-3);
    color: var(--starlight);
    cursor: pointer;
    transition: border-color 0.15s, background 0.15s;
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
    font-size: 11px;
    padding: 4px 8px;
    color: var(--text-dim);
  }
  .tbtn.sp.active {
    color: var(--plasma-cyan);
    border-color: color-mix(in srgb, var(--plasma-cyan) 45%, transparent);
    background: color-mix(in srgb, var(--plasma-cyan) 8%, transparent);
  }
  .speeds {
    display: flex;
    gap: 4px;
  }
  .scrub {
    flex: 1;
    accent-color: var(--brass);
    cursor: pointer;
    height: 4px;
  }
  .pos {
    font-size: 10.5px;
    color: var(--text-faint);
    min-width: 48px;
    text-align: right;
  }
</style>
