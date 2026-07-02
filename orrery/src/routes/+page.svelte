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
  import '$lib/render/primitives.css';
  import '$lib/fonts'; // self-hosted Space Grotesk + JetBrains Mono (offline-safe)
  import { onMount } from 'svelte';
  import { browser } from '$app/environment';

  import Cosmos from '$lib/render/Cosmos.svelte';
  import Observatory from '$lib/render/Observatory.svelte';
  import CostQuotaStrip from '$lib/render/CostQuotaStrip.svelte';
  import Hud from '$lib/panels/Hud.svelte';
  import MetricsPanel from '$lib/panels/MetricsPanel.svelte';
  import RunControlBar from '$lib/panels/RunControlBar.svelte';
  import VerdictPanel from '$lib/panels/VerdictPanel.svelte';
  import TransportBar from '$lib/panels/TransportBar.svelte';
  import BodyView from '$lib/panels/BodyView.svelte';
  import TuningConsole from '$lib/panels/TuningConsole.svelte';
  import ModeBar from '$lib/panels/ModeBar.svelte';
  import StaleBadge from '$lib/panels/StaleBadge.svelte';
  import QAConsole from '$lib/panels/QAConsole.svelte';
  import PlanetariumOverlay from '$lib/panels/PlanetariumOverlay.svelte';
  import LogPanel from '$lib/panels/LogPanel.svelte';
  import HelpOverlay from '$lib/panels/HelpOverlay.svelte';
  import ShareButton from '$lib/panels/ShareButton.svelte';
  import AlertBanner from '$lib/panels/AlertBanner.svelte';

  import { runStore } from '$lib/stores/run.svelte';
  import { cosmosStore } from '$lib/stores/cosmos.svelte';
  import { uiStore } from '$lib/stores/ui.svelte';
  import { sessionStore } from '$lib/stores/session.svelte';
  import { alertStore } from '$lib/stores/alerts.svelte';
  import { hasTauri, hasWsServer, LOOPS } from '$lib/transport';

  type View = 'cosmos' | 'system' | 'body';

  let view = $state<View>('cosmos');
  let activeLoop = $state<string | null>(null);
  let bodyKey = $state<string | null>(null);
  let showHelp = $state(false); // the keyboard-shortcut legend overlay
  let mode = $state<'live' | 'replay'>('replay');
  // The kind of the transport that ACTUALLY mounted for the active System (single source of
  // truth for the badge, tracked by sessionStore) — vs `mode`, which is only the environment's
  // capability at the Cosmos.
  const isLiveTransport = $derived(
    sessionStore.transportKind === 'tauri' || sessionStore.transportKind === 'ws',
  );
  // A live loop that has never emitted an event: guide the user to Start instead of showing
  // a silent, pre-populated $0.00/IDLE instrument that reads as "broken".
  const liveAndEmpty = $derived(isLiveTransport && runStore.state.events === 0);
  const badgeLabel = $derived(
    sessionStore.transportKind === 'tauri'
      ? 'LIVE · desktop'
      : sessionStore.transportKind === 'ws'
        ? 'LIVE · LAN'
        : sessionStore.transportKind === 'replay'
          ? 'REPLAY · fixture'
          : mode,
  );

  const activeLoopName = $derived(
    activeLoop ? LOOPS.find((l) => l.id === activeLoop)?.name ?? activeLoop : null,
  );

  // ── navigation (the zoom state machine) ────────────────────────────────────
  async function enterSystem(id: string) {
    activeLoop = id;
    bodyKey = null;
    view = 'system';
    await sessionStore.mountLoop(id);
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
    sessionStore.unmountLoop();
    void cosmosStore.load(); // refresh Tier-1 summaries on return
  }

  // ── app-wide keyboard shortcuts (guarded vs text entry + open dialogs) ──
  //   ?  toggle the shortcut legend · Esc  close help / leave a Body
  //   i  start · b  brake(phase) · r  resume   (only inside a System/Body)
  function onKeydown(e: KeyboardEvent) {
    const t = e.target as HTMLElement | null;
    if (
      t &&
      (t.isContentEditable || t.tagName === 'INPUT' || t.tagName === 'TEXTAREA' || t.tagName === 'SELECT')
    )
      return;
    if (cosmosStore.console) return; // the Tuning Console owns its own keys while open
    if (e.key === '?') {
      showHelp = !showHelp;
      e.preventDefault();
      return;
    }
    if (e.key === 'Escape') {
      if (showHelp) showHelp = false;
      else if (view === 'body') backToSystem();
      else return;
      e.preventDefault();
      return;
    }
    if (view === 'cosmos') return; // run controls only make sense inside a run
    const k = e.key.toLowerCase();
    if (k === 'i') void sessionStore.control('start');
    else if (k === 'b') void sessionStore.control('stop:phase');
    else if (k === 'r') void sessionStore.control('resume');
    else return;
    e.preventDefault();
  }

  // ── Cosmos auto-refresh ─────────────────────────────────────────────────
  // load() used to run only on mount + backToCosmos, so a roster left open
  // overnight (a real desktop app, not a one-shot preview) went stale — a loop
  // could finish, crash, or need a human and the Cosmos would never say so.
  // Poll gently while actually AT the Cosmos and the source is live (never
  // replay/dev fixtures, which don't change); skip while the Tuning Console is
  // open (an editing user shouldn't have the roster churn under them) and
  // never overlap an in-flight load.
  const COSMOS_POLL_MS = 5000;
  function cosmosShouldPoll(): boolean {
    return (
      browser && view === 'cosmos' && cosmosStore.source === 'tauri' && !cosmosStore.console
    );
  }
  function refreshCosmosIfDue() {
    if (cosmosShouldPoll() && !cosmosStore.loading) void cosmosStore.load();
  }
  $effect(() => {
    if (!cosmosShouldPoll()) return;
    const id = setInterval(refreshCosmosIfDue, COSMOS_POLL_MS);
    return () => clearInterval(id);
  });
  // also catch up the instant the window regains focus (e.g. after being away)
  $effect(() => {
    if (!browser) return;
    window.addEventListener('focus', refreshCosmosIfDue);
    return () => window.removeEventListener('focus', refreshCosmosIfDue);
  });

  // ── Task 3 (wave U4): unattended-run alerts ─────────────────────────────
  // Edge-detect the mounted System's restState transitions (System-sourced: no jump
  // action needed, you're already there). NOT fed while the transport is 'replay' —
  // stores/alerts.svelte.ts's module comment explains why (a human is actively
  // watching/scrubbing a fixture; there's no "while I wasn't looking" to alert about,
  // and scrubbing back and forth across a boundary would otherwise spam fire/clear).
  $effect(() => {
    if (!activeLoop || sessionStore.transportKind === 'replay' || sessionStore.transportKind === null)
      return;
    alertStore.observe(activeLoop, runStore.state.run.restState, 'system', runStore.state.quota.resumeAt);
  });
  // Cosmos-sourced: catch a transition on a loop you're NOT currently inside, while
  // sitting at the roster. Only meaningful when the Cosmos is polling a live backend
  // (cosmosShouldPoll() above) — a static dev fixture never changes after its first
  // load, so it establishes its baseline once and never fires.
  $effect(() => {
    if (cosmosStore.source !== 'tauri') return;
    for (const l of cosmosStore.loops) alertStore.observe(l.id, l.restState, 'cosmos');
  });

  onMount(() => {
    mode = hasTauri() || hasWsServer() ? 'live' : 'replay';
    const teardownUi = uiStore.init(); // viewport + reduced-motion + phone default
    void cosmosStore.load();
    window.addEventListener('keydown', onKeydown);
    return () => {
      teardownUi();
      sessionStore.unmountLoop();
      window.removeEventListener('keydown', onKeydown);
    };
  });
</script>

<svelte:head>
  <title>Orrery — {view === 'cosmos' ? 'cosmos' : activeLoop ?? view}</title>
  <!-- fonts are self-hosted via $lib/fonts (no runtime CDN; works offline in Tauri) -->
</svelte:head>

<main class="stage">
  {#if browser}
    <!-- ── unattended-run alerts (Task 3): a REAL flex row, not an absolute overlay —
         everything below (`.stage-body`) is its own positioning context, so the banner
         pushes the navbar / ModeBar / ignite-fab down instead of covering them. Top of
         System + Cosmos, not Body. -->
    {#if view !== 'body'}
      <AlertBanner onJump={(id) => void enterSystem(id)} />
    {/if}

    <div class="stage-body">
    <!-- ── COSMOS (default home) ───────────────────────────────────────────── -->
    <div class="layer cosmos-layer {view === 'cosmos' ? 'in' : 'out-far'}">
      {#if view === 'cosmos'}
        <Cosmos onEnter={enterSystem} />
      {/if}
    </div>

    <!-- ── SYSTEM (the existing Observatory, scoped to the active loop) ─────── -->
    {#if view === 'system' || view === 'body'}
      <div class="layer system-layer {view === 'system' ? 'in' : 'out-near'}">
        <!-- wave U2 Task 1: a CSS grid dock replaces the old stack of independently
             absolutely-positioned panels (each hand-tracking a sibling's pixel
             height via calc()). The canvas is a real grid cell ("center") that
             genuinely resizes as the rails collapse/expand ("breathes"); chrome is
             placed by the grid, not by z-indexed floats — each panel keeps only
             its own internal styling (bg/border/padding). `.bare` collapses the
             rails/bottom-dock/cost-strip to zero size in Ambient (Planetarium)
             mode AND whenever the Body drawer is open (view==='body'), so the
             canvas gets the full dock either way. Observatory itself is ALWAYS
             mounted here (never remounted on the ambient toggle) — it owns its
             own Tier-1 rendering via uiStore.tierOne. -->
        <div class="system-grid" class:bare={uiStore.ambient || view !== 'system'}>
          {#if view === 'system'}
            <!-- top bar: freshness badge (left) · mode toggle (center) · the
                 breadcrumb/live-replay badge cluster (navbar, below) owns the right -->
            <div class="g-topbar">
              <div class="tb-left">
                <StaleBadge status={sessionStore.wsStatus} transportKind={sessionStore.transportKind} />
              </div>
              <div class="tb-center"><ModeBar canRewind={!!sessionStore.playback} /></div>
              <div class="tb-right" aria-hidden="true"></div>
            </div>
          {/if}

          {#if view === 'system' && !uiStore.ambient}
            <!-- left rail: HUD (top) + the live log (flexes to fill, internal scroll) -->
            <div class="g-hud"><Hud /></div>
            <div class="g-log"><LogPanel /></div>
          {/if}

          <div class="g-center">
            <!-- the canvas always renders; it consults uiStore for the tier/mode -->
            <Observatory />

            <!-- Accessibility: the orbital canvas is aria-hidden + click-only, so mirror the
                 Cosmos legend pattern as a keyboard/screen-reader list of work items. Visually
                 hidden until focused (skip-link style), so it never crowds the instrument. -->
            {#if view === 'system' && runStore.orbits.length}
              <nav class="items-a11y" aria-label="work items">
                <ul>
                  {#each runStore.orbits as o (o.key)}
                    <li>
                      <button onclick={() => { runStore.selectItem(o.key); enterBody(o.key); }}
                        >{o.key} — {o.status}</button
                      >
                    </li>
                  {/each}
                </ul>
              </nav>
            {/if}
          </div>

          {#if view === 'system' && !uiStore.ambient}
            <!-- right rail: report card, verdict and pending decisions, stacked +
                 scrollable — appear/disappear on their own, the rail just flexes -->
            <div class="g-right">
              <MetricsPanel />
              <VerdictPanel />
              <QAConsole />
            </div>
            <!-- bottom dock: run controls (+ the transport scrubber in replay) -->
            <div class="g-bottom">
              {#if liveAndEmpty}
                <div class="empty-hint" role="status">
                  <p class="eh-title">This loop hasn't run yet</p>
                  <p class="eh-sub">
                    Press <strong>✦ Start</strong> below to start it — events stream in here live.
                  </p>
                </div>
              {/if}
              <RunControlBar />
              {#if sessionStore.playback}
                <TransportBar
                  transport={sessionStore.playback}
                  state={sessionStore.playbackState}
                  rewind={uiStore.rewind}
                />
              {/if}
            </div>
            <div class="g-strip"><CostQuotaStrip /></div>
          {/if}
        </div>

        <!-- PLANETARIUM: ambient Tier-1; threshold text + a loud decision affordance
             (answer a blocking question without leaving ambient). A full-viewport
             overlay independent of the dock above (unchanged by wave U2). -->
        {#if view === 'system' && uiStore.ambient}
          <PlanetariumOverlay />
        {/if}
      </div>
    {/if}

    <!-- ── BODY (one work-item dossier) ────────────────────────────────────── -->
    {#if view === 'body' && bodyKey}
      <div class="layer body-layer in">
        <BodyView itemKey={bodyKey} onBack={backToSystem} />
      </div>
    {/if}

    <!-- ── nav shell: brand + breadcrumbs + actions ────────────────────────── -->
    <nav class="navbar pill" aria-label="primary">
      <div class="crumbs">
        <button
          class="crumb {view === 'cosmos' ? 'active' : ''}"
          onclick={backToCosmos}
          disabled={view === 'cosmos'}
        >
          <span aria-hidden="true">✦</span> COSMOS
        </button>
        {#if view !== 'cosmos'}
          <span class="sep" aria-hidden="true">›</span>
          <button
            class="crumb {view === 'system' ? 'active' : ''}"
            onclick={backToSystem}
            disabled={view === 'system'}
          >
            {activeLoop ?? 'system'}
          </button>
        {/if}
        {#if view === 'body' && bodyKey}
          <span class="sep" aria-hidden="true">›</span>
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
        <!-- Share to phone (Task 1): desktop/tauri-source affordance — hidden for the
             actual phone/web client served BY the LAN server (hasWsServer()), which has no
             Tauri invoke bridge to start its own server and would just be confusing there.
             Left visible in plain `vite dev` (no Tauri) via shareStore's dev-preview fallback
             so the popover/QR remain screenshot-able without a packaged app. -->
        {#if view === 'cosmos' && !hasWsServer()}
          <ShareButton />
        {/if}
        <span
          class="mode mono"
          class:islive={view !== 'cosmos' && isLiveTransport}
          class:isreplay={view !== 'cosmos' && sessionStore.transportKind === 'replay'}
          title={view !== 'cosmos' && sessionStore.transportKind === 'replay'
            ? 'Watching a recorded fixture — controls (Start/Resume) are inert here.'
            : isLiveTransport
              ? 'Driving the real loop — Start/Resume spawn the engine.'
              : ''}>{badgeLabel}</span
        >
      </div>
    </nav>

    <!-- ✦ new-loop (Cosmos altitude only). The per-loop ✎ edit affordance
         now lives in the Cosmos roster — the single home for enter + edit. -->
    {#if view === 'cosmos'}
      <button class="ignite-fab pill" onclick={() => cosmosStore.igniteNew()}>
        <span aria-hidden="true">✦</span> new loop
      </button>
    {/if}

    <!-- keyboard-shortcut legend (toggled with ?) -->
    {#if showHelp}
      <HelpOverlay onClose={() => (showHelp = false)} />
    {/if}

    <!-- A5: the Tuning Console (create / edit a loop) -->
    {#if cosmosStore.console}
      <TuningConsole
        mode={cosmosStore.console.mode}
        editId={cosmosStore.console.editId}
        onClose={() => cosmosStore.dismissIgnite()}
        onCreated={(id, ctx) => {
          void cosmosStore.load();
          // Follow through ONLY for a NEW loop that was actually persisted: fly into its System,
          // where the run is started with ✦ Start (disambiguating "create a loop" from "start a
          // run"). A SAVE (edit) or a dev-mode no-op create stays at the Cosmos rather than
          // dead-mounting a transport-less System.
          if (id && ctx.mode === 'create' && ctx.persisted) void enterSystem(id);
        }}
      />
    {/if}
    </div>
  {/if}
</main>

<style>
  .stage {
    position: fixed;
    inset: 0;
    overflow: hidden;
    background: var(--void);
    display: flex;
    flex-direction: column;
  }

  /* Task 3: everything except the (optional) alert banner lives in here. A real flex
     sibling below the banner rather than a shared position:fixed/absolute stack — the
     banner takes its natural height and this claims the rest, so it becomes the
     containing block for the navbar / ignite-fab / zoom layers (all position:absolute)
     WITHOUT any of them needing to know the banner exists. */
  .stage-body {
    position: relative;
    flex: 1;
    min-height: 0;
  }

  /* zoom layers — eased scale/opacity for the orrery "fly in / out" feel */
  .layer {
    position: absolute;
    inset: 0;
    transition: opacity var(--dur-zoom) var(--ease-standard),
      transform var(--dur-zoom) var(--ease-out);
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
    z-index: var(--z-layer-body);
  }

  @media (prefers-reduced-motion: reduce) {
    .layer {
      transition: opacity 0.25s linear;
      transform: none !important;
    }
  }

  /* nav shell — .pill (primitives.css) supplies the shared position/shape/blur/z-index */
  .navbar {
    right: 18px;
    display: flex;
    align-items: center;
    gap: 14px;
    padding: 9px 14px;
    background: var(--panel);
    border: 1px solid var(--panel-edge);
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
    border: 1px solid transparent;
    cursor: default;
  }
  /* honest live/replay badge: green when driving a real loop, amber when watching a fixture */
  .mode.islive {
    color: var(--plasma-green);
    border-color: color-mix(in srgb, var(--plasma-green) 40%, transparent);
    background: color-mix(in srgb, var(--plasma-green) 8%, transparent);
  }
  .mode.isreplay {
    color: var(--amber);
    border-color: color-mix(in srgb, var(--amber) 40%, transparent);
    background: color-mix(in srgb, var(--amber) 8%, transparent);
  }

  /* accessible work-item list: visually hidden until a child is focused (skip-link pattern),
     giving keyboard + screen-reader users a way to reach bodies the aria-hidden canvas can't. */
  .items-a11y {
    position: absolute;
    top: 0;
    left: 0;
    width: 1px;
    height: 1px;
    overflow: hidden;
    clip: rect(0 0 0 0);
    white-space: nowrap;
    z-index: var(--z-a11y);
  }
  .items-a11y:focus-within {
    width: auto;
    height: auto;
    clip: auto;
    white-space: normal;
    margin: 14px;
    padding: 10px 12px;
    background: var(--panel);
    border: 1px solid var(--panel-edge);
    border-radius: var(--radius);
    backdrop-filter: blur(8px);
    max-height: 60vh;
    overflow-y: auto;
  }
  .items-a11y ul {
    list-style: none;
    margin: 0;
    padding: 0;
    display: flex;
    flex-direction: column;
    gap: 4px;
  }
  .items-a11y button {
    font-family: var(--font-mono);
    font-size: 11px;
    background: var(--void-3);
    color: var(--starlight);
    border: 1px solid var(--hairline);
    border-radius: 6px;
    padding: 4px 9px;
    cursor: pointer;
    text-align: left;
    width: 100%;
  }
  .items-a11y button:focus-visible {
    outline: 2px solid var(--plasma-cyan);
    outline-offset: 1px;
  }

  /* ══ wave U2 Task 1: the System dock — a CSS grid host ══════════════════════
     Replaces the old stack of independently absolutely-positioned panels (each
     hand-tracking a sibling's pixel height via calc() — MetricsPanel hardcoding
     the Mechanism's 190px, VerdictPanel stacking off a --metrics-block constant,
     five files hand-tracking --strip-h). Named areas; each cell is a plain
     unstyled box, so a panel's own CSS carries only its internal look (bg/
     border/padding) — the grid owns placement + spacing (the token scale).

     Rows: topbar (auto) / hud (auto) + log (1fr, so LogPanel fills the rail and
     scrolls internally) / bottom-dock (auto) / cost strip (auto, --strip-h tall).
     "center" and "right" span the hud+log row band so the canvas gets the full
     rail height even though the left rail is itself split into two rows.
     Columns: left rail / center (canvas, 1fr — this is what "breathes") / right
     rail. `.bare` (Ambient mode, or the Body drawer open) collapses both rails
     to zero width so the canvas gets the whole dock; `gap: 0` there too so an
     empty rail leaves no dead gutter. */
  .system-grid {
    position: absolute;
    inset: 0;
    display: grid;
    grid-template-columns: minmax(272px, 320px) 1fr minmax(272px, 320px);
    grid-template-rows: auto auto 1fr auto auto;
    grid-template-areas:
      'topbar topbar topbar'
      'hud    center right'
      'log    center right'
      'bottom bottom bottom'
      'strip  strip  strip';
    gap: var(--space-3);
  }
  .system-grid.bare {
    grid-template-columns: 0 1fr 0;
    gap: 0;
  }

  .g-topbar {
    grid-area: topbar;
    display: grid;
    grid-template-columns: 1fr auto 1fr;
    align-items: start;
    padding: var(--space-3) var(--chrome-inset) 0;
  }
  .tb-left {
    justify-self: start;
  }
  .tb-center {
    justify-self: center;
  }
  .tb-right {
    /* an empty reserved column — clears the floating breadcrumb/badge pill
       (navbar, top-right, z-index 20) so the centered ModeBar never creeps
       under it on medium widths. */
    justify-self: end;
    min-width: 168px;
  }

  .g-hud {
    grid-area: hud;
    min-width: 0;
    padding: var(--space-3) 0 0 var(--chrome-inset);
  }
  .g-log {
    grid-area: log;
    min-width: 0;
    min-height: 0;
    display: flex;
    padding: var(--space-2) 0 var(--space-3) var(--chrome-inset);
  }
  .g-center {
    grid-area: center;
    position: relative;
    min-width: 0;
    min-height: 0;
  }
  .g-right {
    grid-area: right;
    min-width: 0;
    min-height: 0;
    overflow-y: auto;
    display: flex;
    flex-direction: column;
    gap: var(--space-3);
    padding: var(--space-3) var(--chrome-inset) var(--space-3) 0;
  }
  .g-bottom {
    grid-area: bottom;
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: var(--space-3);
    padding: 0 var(--chrome-inset) var(--space-3);
  }
  .g-strip {
    grid-area: strip;
  }

  /* phone (wave U2 Task 4): single column, reordered so the canvas + controls
     lead and the log/right-rail cards follow — the dock scrolls as a page
     instead of splitting into rails. Heights are self-contained (vh-based, not
     tracking any sibling's pixel size) so nothing here re-creates the old
     cross-file magic-number breakpoints. */
  @media (max-width: 640px) {
    .system-grid,
    .system-grid.bare {
      grid-template-columns: 1fr;
      grid-template-rows: auto auto minmax(220px, 42vh) auto auto auto auto;
      grid-template-areas:
        'topbar'
        'hud'
        'center'
        'bottom'
        'log'
        'right'
        'strip';
      gap: var(--space-2);
      overflow-y: auto;
    }
    .g-topbar {
      display: flex;
      flex-wrap: wrap;
      justify-content: center;
      gap: var(--space-2);
      /* clears the floating navbar pill, which wraps to two rows (~80px deep
         incl. its 18px top inset) on a 390px width */
      padding: 88px var(--space-2) 0;
    }
    .tb-right {
      display: none;
    }
    .g-hud,
    .g-log,
    .g-right {
      padding-left: var(--space-2);
      padding-right: var(--space-2);
    }
    /* content-driven rows on phone: the desktop rail cells use min-height:0 +
       overflow so a 1fr track can bound them, but inside the phone's auto rows
       that same combination lets the track squish to ~0 (min-content of an
       overflow box is 0). Let content set the row height; the GRID scrolls. */
    .g-log {
      display: block;
      min-height: auto;
    }
    .g-right {
      min-height: auto;
      overflow-y: visible;
    }
    .g-bottom {
      padding: 0 var(--space-2) var(--space-2);
    }
  }
  /* CostQuotaStrip hidden on a short phone viewport so the dock never has to
     fight it for room — the strip is a nicety, the controls above it aren't. */
  @media (max-width: 640px) and (max-height: 700px) {
    .g-strip {
      display: none;
    }
  }

  /* empty-state hint for a freshly-entered live loop (sits atop the control bar) */
  .empty-hint {
    text-align: center;
    pointer-events: none;
    max-width: 420px;
  }
  .eh-title {
    font-family: var(--font-grotesk);
    font-size: 15px;
    font-weight: 600;
    color: var(--starlight);
    margin: 0 0 4px;
  }
  .eh-sub {
    font-family: var(--font-mono);
    font-size: 11.5px;
    color: var(--text-dim);
    margin: 0;
  }
  .eh-sub strong {
    color: var(--amber);
    font-weight: 600;
  }

  /* ignite affordance — .pill (primitives.css) supplies the shared position/shape/blur/z-index */
  .ignite-fab {
    left: 18px;
    font-family: var(--font-grotesk);
    font-size: 12px;
    font-weight: 600;
    letter-spacing: 0.06em;
    padding: 9px 16px;
    border: 1px solid color-mix(in srgb, var(--amber) 45%, transparent);
    background: color-mix(in srgb, var(--amber) 9%, transparent);
    color: var(--amber);
    cursor: pointer;
    transition: transform 0.12s, background 0.18s;
  }
  .ignite-fab:hover {
    transform: translateY(-1px);
    background: color-mix(in srgb, var(--amber) 16%, transparent);
  }

</style>
