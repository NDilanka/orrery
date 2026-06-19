<script lang="ts">
  // ORRERY — the PLATFORM shell (A4). A three-altitude zoom orrery:
  //
  //   COSMOS (all loops = star-systems)  ↔  SYSTEM (one run)  ↔  BODY (one item)
  //
  // The Cosmos is the home: every registered loop is a star-system glyph you can
  // glance (Tier-1) and fly into. Entering a system mounts the existing A2/A3
  // Observatory (+ HUD, control, playback, cost strip) scoped to that loop's
  // fixture/stateDir. Drilling into a body opens a single-item dossier. Clear
  // back-nav (Cosmos ← System ← Body) and eased zoom transitions keep the
  // orrery feel. The old loop-picker is subsumed into the Cosmos.
  //
  // Pixi stays client-only (each render component dynamic-imports it under a
  // `browser` guard); this shell only orchestrates view state + the transport.

  import '$lib/render/tokens.css';
  import { onMount } from 'svelte';
  import { browser } from '$app/environment';
  import { base } from '$app/paths';

  import Cosmos from '$lib/render/Cosmos.svelte';
  import Observatory from '$lib/render/Observatory.svelte';
  import Mechanism from '$lib/render/Mechanism.svelte';
  import CostQuotaStrip from '$lib/render/CostQuotaStrip.svelte';
  import Hud from '$lib/panels/Hud.svelte';
  import RunControlBar from '$lib/panels/RunControlBar.svelte';
  import VerdictPanel from '$lib/panels/VerdictPanel.svelte';
  import TransportBar from '$lib/panels/TransportBar.svelte';
  import BodyView from '$lib/panels/BodyView.svelte';

  import { runStore } from '$lib/stores/run.svelte';
  import { cosmosStore } from '$lib/stores/cosmos.svelte';
  import {
    createTransport,
    hasTauri,
    LOOPS,
    type LoopChoice,
    type Transport,
  } from '$lib/transport';
  import { isPlayback, type PlaybackState, type PlaybackTransport } from '$lib/transport/replay';

  type View = 'cosmos' | 'system' | 'body';

  let view = $state<View>('cosmos');
  let activeLoop = $state<string | null>(null);
  let bodyKey = $state<string | null>(null);
  let mode = $state<'live' | 'replay'>('replay');

  let transport: Transport | null = null;
  let playback = $state<PlaybackTransport | null>(null);
  let playbackState = $state<PlaybackState>({
    playing: false,
    speed: 1,
    cursor: 0,
    total: 0,
    done: false,
  });

  const activeLoopName = $derived(
    activeLoop ? LOOPS.find((l) => l.id === activeLoop)?.name ?? activeLoop : null,
  );

  // resolve fixture URLs against SvelteKit's base path
  function withBase(choice: LoopChoice): LoopChoice {
    return {
      ...choice,
      fixtureUrl: `${base}/${choice.fixtureUrl}`,
      checkpointUrl: choice.checkpointUrl ? `${base}/${choice.checkpointUrl}` : undefined,
    };
  }

  // ── mount a loop's System view via the existing transport/replay ───────────
  async function mountLoop(id: string) {
    if (!browser) return;
    transport?.stop();
    runStore.reset();
    playback = null;
    const choice = LOOPS.find((l) => l.id === id);
    if (!choice) return;
    transport = createTransport(withBase(choice), { onState: (s) => runStore.set(s) });
    if (isPlayback(transport)) {
      playback = transport;
      transport.onPlayback((p) => (playbackState = p));
    }
    await transport.start();
  }

  function unmountLoop() {
    transport?.stop();
    transport = null;
    playback = null;
    runStore.reset();
  }

  async function control(action: string) {
    await transport?.control(action);
  }

  // ── navigation (the zoom state machine) ────────────────────────────────────
  async function enterSystem(id: string) {
    activeLoop = id;
    bodyKey = null;
    view = 'system';
    await mountLoop(id);
  }

  function enterBody(key: string | null) {
    const k = key ?? runStore.selectedItem ?? runStore.state.currentItem;
    if (!k) return;
    bodyKey = k;
    runStore.selectItem(k);
    view = 'body';
  }

  function backToSystem() {
    bodyKey = null;
    view = 'system';
  }

  function backToCosmos() {
    view = 'cosmos';
    activeLoop = null;
    bodyKey = null;
    unmountLoop();
    void cosmosStore.load(); // refresh Tier-1 summaries on return
  }

  onMount(() => {
    mode = hasTauri() ? 'live' : 'replay';
    void cosmosStore.load();
    return () => transport?.stop();
  });
</script>

<svelte:head>
  <title>Orrery — {view === 'cosmos' ? 'cosmos' : activeLoop ?? view}</title>
  <link rel="preconnect" href="https://fonts.googleapis.com" />
  <link
    href="https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500&display=swap"
    rel="stylesheet"
  />
</svelte:head>

<main class="stage">
  {#if browser}
    <!-- ── COSMOS (default home) ───────────────────────────────────────────── -->
    <div class="layer cosmos-layer {view === 'cosmos' ? 'in' : 'out-far'}">
      {#if view === 'cosmos'}
        <Cosmos onEnter={enterSystem} />
      {/if}
    </div>

    <!-- ── SYSTEM (the existing Observatory, scoped to the active loop) ─────── -->
    {#if view === 'system' || view === 'body'}
      <div class="layer system-layer {view === 'system' ? 'in' : 'out-near'}">
        <Observatory />
        <Mechanism />
        <CostQuotaStrip />
        <Hud />
        <VerdictPanel />
        {#if playback}
          <TransportBar transport={playback} state={playbackState} />
        {/if}
        <RunControlBar {control} />
      </div>
    {/if}

    <!-- ── BODY (one work-item dossier) ────────────────────────────────────── -->
    {#if view === 'body' && bodyKey}
      <div class="layer body-layer in">
        <BodyView itemKey={bodyKey} />
      </div>
    {/if}

    <!-- ── nav shell: brand + breadcrumbs + actions ────────────────────────── -->
    <nav class="navbar">
      <div class="crumbs">
        <button
          class="crumb {view === 'cosmos' ? 'active' : ''}"
          onclick={backToCosmos}
          disabled={view === 'cosmos'}
        >
          ✦ COSMOS
        </button>
        {#if view !== 'cosmos'}
          <span class="sep">←</span>
          <button
            class="crumb {view === 'system' ? 'active' : ''}"
            onclick={backToSystem}
            disabled={view === 'system'}
          >
            {activeLoop ?? 'system'}
          </button>
        {/if}
        {#if view === 'body' && bodyKey}
          <span class="sep">←</span>
          <span class="crumb active body">{bodyKey}</span>
        {/if}
      </div>

      <div class="navactions">
        {#if view === 'system'}
          {@const sel = runStore.selectedItem ?? runStore.state.currentItem}
          {#if sel}
            <button class="nbtn body" onclick={() => enterBody(sel)}>fly into body →</button>
          {/if}
        {/if}
        <span class="mode mono">{mode}</span>
      </div>
    </nav>

    <!-- ✦ ignite-new-loop affordance (only at the Cosmos altitude) -->
    {#if view === 'cosmos'}
      <button class="ignite-fab" onclick={() => cosmosStore.igniteNew()}>
        ✦ ignite new loop
      </button>
    {/if}

    <!-- A5 stub: Tuning Console placeholder -->
    {#if cosmosStore.ignitePlaceholder}
      <div
        class="modal-scrim"
        role="presentation"
        onclick={() => cosmosStore.dismissIgnite()}
        onkeydown={(e) => e.key === 'Escape' && cosmosStore.dismissIgnite()}
      >
        <div
          class="modal"
          role="dialog"
          aria-modal="true"
          aria-label="Tuning Console placeholder"
          tabindex="-1"
          onclick={(e) => e.stopPropagation()}
          onkeydown={(e) => e.stopPropagation()}
        >
          <div class="mtitle mono">✦ SET ORBITAL PARAMETERS</div>
          <p class="mbody">
            The Tuning Console — blueprint + three dials + a destination — is coming in
            <strong>A5</strong>. Creating a loop here will <em>ignite a new star</em> into the
            Cosmos.
          </p>
          <button class="nbtn" onclick={() => cosmosStore.dismissIgnite()}>got it</button>
        </div>
      </div>
    {/if}
  {/if}
</main>

<style>
  .stage {
    position: fixed;
    inset: 0;
    overflow: hidden;
    background: var(--void);
  }

  /* zoom layers — eased scale/opacity for the orrery "fly in / out" feel */
  .layer {
    position: absolute;
    inset: 0;
    transition: opacity 0.5s ease, transform 0.5s cubic-bezier(0.22, 1, 0.36, 1);
    transform-origin: center center;
  }
  .layer.in {
    opacity: 1;
    transform: scale(1);
    pointer-events: auto;
  }
  /* cosmos recedes (zoom OUT past it) when you dive into a system */
  .layer.out-far {
    opacity: 0;
    transform: scale(2.4);
    pointer-events: none;
  }
  /* system recedes slightly behind the body card */
  .layer.out-near {
    opacity: 0.25;
    transform: scale(0.96);
    pointer-events: none;
    filter: blur(1px);
  }
  .body-layer {
    z-index: 8;
  }

  @media (prefers-reduced-motion: reduce) {
    .layer {
      transition: opacity 0.25s linear;
      transform: none !important;
    }
  }

  /* nav shell */
  .navbar {
    position: absolute;
    top: 18px;
    right: 18px;
    display: flex;
    align-items: center;
    gap: 14px;
    padding: 9px 14px;
    background: var(--panel);
    border: 1px solid var(--panel-edge);
    border-radius: var(--radius-pill);
    backdrop-filter: blur(8px);
    z-index: 20;
  }
  .crumbs {
    display: flex;
    align-items: center;
    gap: 8px;
  }
  .crumb {
    font-family: var(--font-mono);
    font-size: 11px;
    letter-spacing: 0.1em;
    text-transform: uppercase;
    padding: 4px 9px;
    border-radius: var(--radius-pill);
    border: 1px solid transparent;
    background: transparent;
    color: var(--text-dim);
    cursor: pointer;
    transition: color 0.18s, border-color 0.18s;
  }
  .crumb:hover:not(:disabled) {
    color: var(--starlight);
    border-color: var(--hairline);
  }
  .crumb.active {
    color: var(--brass);
  }
  .crumb.body {
    color: var(--plasma-cyan);
    cursor: default;
  }
  .crumb:disabled {
    cursor: default;
  }
  .sep {
    color: var(--text-faint);
    font-size: 11px;
  }
  .navactions {
    display: flex;
    align-items: center;
    gap: 10px;
  }
  .nbtn {
    font-family: var(--font-grotesk);
    font-size: 11px;
    font-weight: 600;
    letter-spacing: 0.04em;
    padding: 6px 13px;
    border-radius: var(--radius-pill);
    border: 1px solid var(--hairline);
    background: var(--void-3);
    color: var(--starlight);
    cursor: pointer;
    transition: border-color 0.18s, transform 0.1s;
  }
  .nbtn:hover {
    border-color: var(--brass);
    transform: translateY(-1px);
  }
  .nbtn.body {
    color: var(--plasma-cyan);
    border-color: color-mix(in srgb, var(--plasma-cyan) 35%, transparent);
  }
  .mode {
    font-size: 9px;
    padding: 2px 7px;
    border-radius: var(--radius-pill);
    background: var(--void-3);
    color: var(--text-dim);
    letter-spacing: 0.1em;
    text-transform: uppercase;
  }

  /* ignite affordance */
  .ignite-fab {
    position: absolute;
    top: 18px;
    left: 18px;
    font-family: var(--font-grotesk);
    font-size: 12px;
    font-weight: 600;
    letter-spacing: 0.06em;
    padding: 9px 16px;
    border-radius: var(--radius-pill);
    border: 1px solid color-mix(in srgb, var(--amber) 45%, transparent);
    background: color-mix(in srgb, var(--amber) 9%, transparent);
    color: var(--amber);
    cursor: pointer;
    backdrop-filter: blur(8px);
    z-index: 20;
    transition: transform 0.12s, background 0.18s;
  }
  .ignite-fab:hover {
    transform: translateY(-1px);
    background: color-mix(in srgb, var(--amber) 16%, transparent);
  }

  /* A5 stub modal */
  .modal-scrim {
    position: absolute;
    inset: 0;
    display: flex;
    align-items: center;
    justify-content: center;
    background: rgba(7, 9, 18, 0.6);
    backdrop-filter: blur(3px);
    z-index: 30;
  }
  .modal {
    width: min(440px, 90vw);
    padding: 24px 26px;
    background: var(--panel);
    border: 1px solid var(--panel-edge);
    border-radius: var(--radius);
    display: flex;
    flex-direction: column;
    gap: 14px;
  }
  .mtitle {
    font-size: 13px;
    letter-spacing: 0.18em;
    color: var(--brass);
  }
  .mbody {
    margin: 0;
    font-size: 13px;
    line-height: 1.55;
    color: var(--text-dim);
  }
  .mbody strong {
    color: var(--starlight);
  }
  .modal .nbtn {
    align-self: flex-start;
  }
</style>
