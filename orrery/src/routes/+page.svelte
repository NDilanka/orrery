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
  import CommandPalette from '$lib/panels/CommandPalette.svelte';
  import SettingsOverlay from '$lib/panels/settings/SettingsOverlay.svelte';
  import Toast from '$lib/panels/Toast.svelte';

  import { runStore } from '$lib/stores/run.svelte';
  import { cosmosStore } from '$lib/stores/cosmos.svelte';
  import { uiStore } from '$lib/stores/ui.svelte';
  import { sessionStore } from '$lib/stores/session.svelte';
  import { alertStore } from '$lib/stores/alerts.svelte';
  import { settingsStore } from '$lib/stores/settings.svelte';
  import { hasTauri, hasWsServer, LOOPS } from '$lib/transport';
  import { resolveLoopsDir } from '$lib/paths';

  type View = 'cosmos' | 'system' | 'body';

  let view = $state<View>('cosmos');
  let activeLoop = $state<string | null>(null);
  let bodyKey = $state<string | null>(null);
  let showHelp = $state(false); // the keyboard-shortcut legend overlay
  let showPalette = $state(false); // M3.1 command palette (Ctrl/Cmd+K)
  let showSettings = $state(false); // app settings modal (Ctrl/Cmd+,)
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

  // Platform-aware shortcut chip: "⌘K" reads wrong on Windows/Linux — only the whole
  // stage (this component's markup) is client-only ({#if browser} below), so `navigator`
  // is always safe to read here without an SSR guard beyond `browser` itself.
  const isMac = browser && /Mac|iPhone|iPad|iPod/i.test(navigator.platform || navigator.userAgent || '');
  const kbdChip = isMac ? '⌘K' : 'Ctrl K';

  // ── M4.3 full-bleed canvas: safeInsets for Observatory ─────────────────────
  // Observatory now renders on a stage-level layer BEHIND `.system-grid` (the scene fills
  // the viewport edge-to-edge instead of being boxed into a grid cell). `.g-center` stays
  // in the grid as an EMPTY probe — it's still laid out exactly where the canvas used to
  // live (rails/dock/strip collapse it precisely as before), so its box IS the
  // "unobstructed" region. We measure that box's distance from the full-bleed layer's
  // edges (gridEl, whose rect === the layer's rect — both position:absolute inset:0) and
  // hand it to Observatory as `safeInsets`, so the system centers/fit-scales inside the
  // same region a boxed canvas used to occupy, without actually being boxed.
  let gridEl = $state<HTMLDivElement | null>(null);
  let centerProbeEl = $state<HTMLDivElement | null>(null);
  let safeInsets = $state({ top: 0, right: 0, bottom: 0, left: 0 });

  function measureSafeInsets() {
    if (!gridEl || !centerProbeEl) return;
    const g = gridEl.getBoundingClientRect();
    const c = centerProbeEl.getBoundingClientRect();
    safeInsets = {
      top: Math.max(0, c.top - g.top),
      right: Math.max(0, g.right - c.right),
      bottom: Math.max(0, g.bottom - c.bottom),
      left: Math.max(0, c.left - g.left),
    };
  }

  // Re-measure whenever the probe or its grid host resize — rail collapse/expand, the
  // ambient toggle, the Body drawer's `.bare` mode, the phone breakpoint, and a plain
  // window resize all show up as one or the other's box changing size, so a single
  // ResizeObserver pair covers every case without polling on every reactive tick. The
  // effect itself re-runs whenever the elements mount/unmount (entering/leaving the
  // System/Body altitude), tearing the observer down and resetting to zero insets.
  $effect(() => {
    if (!browser || !gridEl || !centerProbeEl) {
      safeInsets = { top: 0, right: 0, bottom: 0, left: 0 };
      return;
    }
    const ro = new ResizeObserver(measureSafeInsets);
    ro.observe(gridEl);
    ro.observe(centerProbeEl);
    measureSafeInsets(); // synchronous first read — don't wait a frame for the initial box
    return () => ro.disconnect();
  });

  // ── navigation (the zoom state machine) ────────────────────────────────────
  async function enterSystem(id: string) {
    activeLoop = id;
    bodyKey = null;
    view = 'system';
    // settings: open a system straight into the ambient Planetarium when asked.
    // setMode marks the choice "user-picked", which also keeps the phone
    // auto-default from fighting it later — the setting IS the user's pick.
    if (settingsStore.data.general.startInAmbient) uiStore.setMode('planetarium');
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
    // The settings modal owns all keys while open (its focusTrap handles Tab; here we only
    // handle its close chords) so no app shortcut leaks to the shell beneath it.
    if (showSettings) {
      if (e.key === 'Escape' || ((e.ctrlKey || e.metaKey) && e.key === ',')) {
        showSettings = false;
        e.preventDefault();
      }
      return;
    }
    // Ctrl/Cmd+K — the command palette. Simplest correct behavior (M3.1): ignored while
    // another modal (Help) or the palette itself is already open, rather than trying to
    // stack/z-fight above them.
    if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'k') {
      if (showHelp || showPalette || showSettings) return;
      showPalette = true;
      e.preventDefault();
      return;
    }
    // Ctrl/Cmd+, — the settings modal (the macOS convention). Ignored while another modal is up.
    if ((e.ctrlKey || e.metaKey) && e.key === ',') {
      if (showHelp || showPalette) return;
      showSettings = true;
      e.preventDefault();
      return;
    }
    if (e.key === '?') {
      showHelp = !showHelp;
      e.preventDefault();
      return;
    }
    if (e.key === 'Escape') {
      if (showPalette) showPalette = false;
      else if (showHelp) showHelp = false;
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
    const id = setInterval(refreshCosmosIfDue, settingsStore.data.general.cosmosPollMs || COSMOS_POLL_MS);
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
  // settings gate: the master toggle maps to allowed=[] (not an early return) so the
  // observe() clear/baseline bookkeeping still runs — switching alerts back on later
  // edge-detects fresh instead of firing on states that predate the toggle.
  const alertAllowed = $derived(
    settingsStore.data.notifications.unattendedAlerts ? settingsStore.data.notifications.alertOn : [],
  );
  $effect(() => {
    if (!activeLoop || sessionStore.transportKind === 'replay' || sessionStore.transportKind === null)
      return;
    alertStore.observe(
      activeLoop,
      runStore.state.run.restState,
      'system',
      runStore.state.quota.resumeAt,
      alertAllowed,
    );
  });
  // Cosmos-sourced: catch a transition on a loop you're NOT currently inside, while
  // sitting at the roster. Only meaningful when the Cosmos is polling a live backend
  // (cosmosShouldPoll() above) — a static dev fixture never changes after its first
  // load, so it establishes its baseline once and never fires.
  $effect(() => {
    if (cosmosStore.source !== 'tauri') return;
    for (const l of cosmosStore.loops) alertStore.observe(l.id, l.restState, 'cosmos', undefined, alertAllowed);
  });

  onMount(() => {
    mode = hasTauri() || hasWsServer() ? 'live' : 'replay';
    const teardownUi = uiStore.init(); // viewport + reduced-motion + phone default
    // App settings: load persisted prefs, start the config-file watcher, apply the theme.
    let teardownSettings: (() => void) | undefined;
    void settingsStore.init().then((fn) => {
      teardownSettings = fn;
    });
    // Resolve the runtime loops dir (settings override) BEFORE the first roster
    // load — otherwise the roster races the resolver and lists the build-time
    // default dir. resolveLoopsDir() is idempotent and a cheap no-op in dev.
    void resolveLoopsDir().then(() => cosmosStore.load());
    window.addEventListener('keydown', onKeydown);
    return () => {
      teardownUi();
      teardownSettings?.();
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
        <!-- M2.3 grain (CSS part): static, full-bleed over the Cosmos canvas only — this
             layer IS the canvas at this altitude, no rails to avoid. -->
        {#if settingsStore.data.appearance.grain}
          <div class="grain" aria-hidden="true"></div>
        {/if}
      {/if}
    </div>

    <!-- ── SYSTEM (the existing Observatory, scoped to the active loop) ─────── -->
    {#if view === 'system' || view === 'body'}
      <div class="layer system-layer {view === 'system' ? 'in' : 'out-near'}">
        <!-- ══ M4.3 full-bleed canvas ═══════════════════════════════════════════════
             Observatory moves OFF the grid entirely onto a stage-level layer — behind
             `.system-grid` in both DOM order and z-index — so the scene fills the whole
             viewport edge-to-edge instead of being boxed into the old `.g-center` grid
             cell. Observatory is ALWAYS mounted here (never remounted on the ambient
             toggle, same lifetime guarantee the old `.g-center` gave it) and reads
             `safeInsets` (measured off the now-empty `.g-center` probe below) to center/
             fit-scale the system inside the unobstructed region instead of the whole
             viewport. -->
        <div class="canvas-stage">
          <Observatory {safeInsets} />
        </div>
        <!-- M2.3 grain: now viewport-sized (stage level, below the chrome) instead of
             bounded to the old canvas grid cell. -->
        {#if settingsStore.data.appearance.grain}
          <div class="grain" aria-hidden="true"></div>
        {/if}

        <!-- wave U2 Task 1 (M4.3: topbar row removed — merged into the one top rail
             below) — a CSS grid dock places the floating chrome (rails/dock/strip) over
             the full-bleed canvas above; every cell is transparent, so only each panel's
             own background reads as a surface. `.bare` collapses the rails/bottom-dock/
             cost-strip to zero size in Ambient (Planetarium) mode AND whenever the Body
             drawer is open (view==='body') — the empty probe cell then spans the whole
             grid, so safeInsets correctly collapses to ~0 in both cases too. -->
        <div class="system-grid" class:bare={uiStore.ambient || view !== 'system'} bind:this={gridEl}>
          {#if view === 'system' && !uiStore.ambient}
            <!-- left rail: HUD (top) + the live log (flexes to fill, internal scroll) -->
            <div class="g-hud"><Hud /></div>
            <div class="g-log"><LogPanel /></div>
          {/if}

          <!-- the empty probe cell: laid out exactly where the canvas used to live (rails/
               dock/strip collapse it identically to before), so its box IS the safe region
               — see the safeInsets effect above. Carries nothing visual of its own
               (pointer-events:none) so clicks/hover reach the canvas behind it; the a11y
               nav re-enables pointer-events on itself. -->
          <div class="g-center" bind:this={centerProbeEl}>
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
            <!-- bottom dock (M4.3): RunControlBar + TransportBar merged into ONE
                 full-width bar — this container supplies the hairline-top/background/
                 padding; the panels inside stay transparent segments. -->
            <div class="g-bottom">
              {#if liveAndEmpty}
                <!-- plan's empty-state pattern (§M3.2): dim glyph + one-line explanation +
                     the action, named inline since the actual button (RunControlBar) is the
                     very next element down — no separate CTA needed here. -->
                <div class="empty-hint" role="status">
                  <span class="eh-glyph" aria-hidden="true">✦</span>
                  <p class="eh-line">
                    This loop hasn't run yet — press <strong>Start</strong> below to begin, events
                    stream in here live.
                  </p>
                </div>
              {/if}
              <div class="dock">
                <div class="dock-controls"><RunControlBar /></div>
                {#if sessionStore.playback}
                  <div class="dock-scrub">
                    <TransportBar
                      transport={sessionStore.playback}
                      state={sessionStore.playbackState}
                      rewind={uiStore.rewind}
                    />
                  </div>
                {/if}
              </div>
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

    <!-- ══ M4.3 the one top rail ═══════════════════════════════════════════════
         Replaces three previously-competing floating clusters (the breadcrumb/badge
         navbar pill, the System dock's own top-bar row) with a single rail on one
         baseline: left = breadcrumb · center = ModeBar (System only) · right =
         live/replay badge + staleness + share + ⌘K — one family of ghost pills.
         Positioned within `.stage-body` exactly like the old navbar did, so
         AlertBanner still pushes it down instead of overlapping it. -->
    <nav class="toprail" class:has-fab={view === 'cosmos'} aria-label="primary">
      <div class="tr-left">
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
      </div>

      <div class="tr-center">
        {#if view === 'system'}
          <ModeBar canRewind={!!sessionStore.playback} />
        {/if}
      </div>

      <div class="tr-right">
        {#if view === 'system'}
          {@const sel = runStore.selectedItem ?? runStore.state.currentItem}
          {#if sel}
            <button class="nbtn body" onclick={() => enterBody(sel)}>fly into body →</button>
          {/if}
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
        {#if view === 'system'}
          <StaleBadge status={sessionStore.wsStatus} transportKind={sessionStore.transportKind} />
        {/if}
        <!-- Share to phone (Task 1): desktop/tauri-source affordance — hidden for the
             actual phone/web client served BY the LAN server (hasWsServer()), which has no
             Tauri invoke bridge to start its own server and would just be confusing there.
             Left visible in plain `vite dev` (no Tauri) via shareStore's dev-preview fallback
             so the popover/QR remain screenshot-able without a packaged app. -->
        {#if view === 'cosmos' && !hasWsServer()}
          <ShareButton />
        {/if}
        <!-- M3.1: command-palette affordance — a discoverable hint chip alongside the shortcut
             legend, not just a hidden keybinding. -->
        <button
          class="kbdhint mono"
          title="Command palette"
          aria-label="Open command palette"
          onclick={() => (showPalette = true)}>{kbdChip}</button
        >
        <!-- app settings (Ctrl/Cmd+,) — a calm ghost gear beside the palette hint. -->
        <button
          class="nbtn settings-gear"
          title="Settings"
          aria-label="Open settings"
          onclick={() => (showSettings = true)}
        >
          <span aria-hidden="true">⚙</span>
        </button>
      </div>
    </nav>

    <!-- ✦ new-loop (Cosmos altitude only) — the primary CTA there, so it stays its own
         top-left affordance rather than folding into the rail above (`.has-fab` on the
         rail reserves clearance so the two never overlap). The per-loop ✎ edit
         affordance now lives in the Cosmos roster — the single home for enter + edit.
         M4.2 anatomy: `.btn-primary` (solid light — the monochrome inversion is the
         strongest CTA) at `-lg` size; `.pill` keeps its shared position/z-index. -->
    {#if view === 'cosmos'}
      <button class="ignite-fab pill btn btn-primary btn-lg" onclick={() => cosmosStore.igniteNew()}>
        <span aria-hidden="true">✦</span> new loop
      </button>
    {/if}

    <!-- keyboard-shortcut legend (toggled with ?) -->
    {#if showHelp}
      <HelpOverlay onClose={() => (showHelp = false)} {kbdChip} />
    {/if}

    <!-- M3.1: the command palette (Ctrl/Cmd+K) — pure dispatch onto the same nav closures
         and stores every other panel already uses; +page.svelte owns only whether it's
         mounted (mirrors HelpOverlay). -->
    {#if showPalette}
      <CommandPalette
        onClose={() => (showPalette = false)}
        {view}
        {activeLoop}
        {kbdChip}
        onEnterSystem={(id) => void enterSystem(id)}
        onEnterBody={(key) => enterBody(key)}
        onBackToSystem={backToSystem}
        onBackToCosmos={backToCosmos}
        onOpenHelp={() => (showHelp = true)}
        onOpenSettings={() => {
          showPalette = false;
          showSettings = true;
        }}
      />
    {/if}

    <!-- app settings modal (gear / Ctrl/Cmd+,) — mirrors HelpOverlay; +page owns mount. -->
    {#if showSettings}
      <SettingsOverlay onClose={() => (showSettings = false)} />
    {/if}

    <!-- quota-resume toast (settings-gated internally) — one app-wide mount. -->
    <Toast />

    <!-- A5: the Tuning Console (create / edit a loop) -->
    {#if cosmosStore.console}
      <TuningConsole
        mode={cosmosStore.console.mode}
        editId={cosmosStore.console.editId}
        onClose={() => cosmosStore.dismissIgnite()}
        onCreated={async (id, ctx) => {
          void cosmosStore.load();
          // Follow through ONLY for a NEW loop that was actually persisted: fly into its System,
          // where the run is started with ✦ Start (disambiguating "create a loop" from "start a
          // run"). A SAVE (edit) or a dev-mode no-op create stays at the Cosmos rather than
          // dead-mounting a transport-less System.
          if (id && ctx.mode === 'create' && ctx.persisted) {
            // ✦ Create & start: park a one-shot intent BEFORE the System mounts; RunControlBar
            // consumes it once the transport is up and fires its own start path, so a failed
            // auto-start surfaces exactly like a failed hand-clicked ✦ Start (error line, stall
            // timer) instead of rejecting unseen. Plain ✦ Create loop leaves startAfterCreate
            // false — the deliberate create≠start split for the default path is preserved.
            if (ctx.startAfterCreate) sessionStore.requestAutostart();
            await enterSystem(id);
          }
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

  /* ══ M2.3 grain overlay — a static, tiny (128px) grayscale-noise SVG tile (feTurbulence,
     generated once, inlined as a data-URI so it costs one paint and zero network/deps) laid
     over the canvas regions only (Cosmos layer + the System dock's canvas cell). opacity is
     intentionally tiny (0.03) and mix-blend-mode:overlay so it reads as "the scene has grain"
     rather than "there's noise on my screen"; never animated (plan §1 "motion is honest" —
     this is a finish, not an effect), so it needs no reduced-motion guard. */
  .grain {
    position: absolute;
    inset: 0;
    pointer-events: none;
    opacity: 0.03;
    mix-blend-mode: overlay;
    background-repeat: repeat;
    background-size: 128px 128px;
    background-image: url("data:image/svg+xml,%3Csvg%20xmlns%3D'http%3A%2F%2Fwww.w3.org%2F2000%2Fsvg'%20width%3D'128'%20height%3D'128'%3E%20%3Cfilter%20id%3D'n'%3E%20%3CfeTurbulence%20type%3D'fractalNoise'%20baseFrequency%3D'0.9'%20numOctaves%3D'2'%20stitchTiles%3D'stitch'%20result%3D't'%2F%3E%20%3CfeColorMatrix%20in%3D't'%20type%3D'matrix'%20values%3D'0%200%200%200%201%200%200%200%200%201%200%200%200%200%201%200%200%200%201%200'%2F%3E%20%3C%2Ffilter%3E%20%3Crect%20width%3D'100%25'%20height%3D'100%25'%20filter%3D'url(%23n)'%2F%3E%20%3C%2Fsvg%3E");
  }

  @media (prefers-reduced-motion: reduce) {
    :global(:root:not([data-motion='full'])) .layer {
      transition: opacity 0.25s linear;
      transform: none !important;
    }
  }
  /* mirrors the media block above for the user-forced Reduced setting (data-motion) */
  :global(:root[data-motion='reduced']) .layer {
    transition: opacity 0.25s linear;
    transform: none !important;
  }

  /* ══ M4.3 the one top rail ═══════════════════════════════════════════════════
     Replaces the old right-anchored `.navbar` pill + the System dock's separate
     `.g-topbar` row with a single full-width rail on the page gutter — one baseline,
     three flex clusters (left/center/right), each still a "ghost pill" family but no
     longer two independently-floating structures that needed mutual clearance hacks.
     `position:absolute` within `.stage-body` (not `.pill` — that primitive is a small
     anchored chip, not a full-width rail) so AlertBanner still pushes it down exactly
     like the old navbar did. */
  .toprail {
    position: absolute;
    top: var(--page-inset);
    left: var(--page-inset);
    right: var(--page-inset);
    z-index: var(--z-chrome);
    display: grid;
    grid-template-columns: 1fr auto 1fr;
    align-items: start;
    gap: var(--space-3);
    /* empty grid tracks between the pill clusters must not steal clicks/hover from the
       full-bleed canvas now sitting behind the whole rail — each cluster re-enables. */
    pointer-events: none;
  }
  .tr-left,
  .tr-center,
  .tr-right {
    display: flex;
    align-items: center;
    gap: var(--space-2);
    min-width: 0;
    pointer-events: auto;
  }
  .tr-left {
    justify-self: start;
  }
  .tr-center {
    justify-self: center;
  }
  .tr-right {
    justify-self: end;
  }
  /* Cosmos view: the ignite-fab (below) is pinned to the exact same top-left corner as
     `.tr-left` — reserve its width so the "COSMOS" home crumb doesn't render underneath
     it (same clearance-reservation pattern the old `.tb-right` used for the reverse
     case). ~152px covers the restyled `.btn-lg` "✦ new loop" button at its widest. */
  .toprail.has-fab .tr-left {
    padding-left: 152px;
  }
  .crumbs {
    display: flex;
    align-items: center;
    gap: 8px;
  }
  .crumb {
    font-family: var(--font-mono);
    font-size: var(--text-xs);
    letter-spacing: 0.1em;
    text-transform: uppercase;
    padding: 4px 9px;
    border-radius: var(--radius-pill);
    border: 1px solid transparent;
    background: transparent;
    color: var(--em-low);
    cursor: pointer;
    transition: color var(--dur-feedback) var(--ease-standard),
      border-color var(--dur-feedback) var(--ease-standard);
  }
  .crumb:hover:not(:disabled) {
    color: var(--em-hi);
    border-color: var(--border-hairline);
  }
  .crumb.active {
    color: var(--em-hi);
  }
  .crumb.body {
    color: var(--em-hi);
    cursor: default;
  }
  .crumb:disabled {
    cursor: default;
  }
  .sep {
    color: var(--em-faint);
    font-size: var(--text-xs);
  }
  .nbtn {
    font-family: var(--font-grotesk);
    font-size: var(--text-xs);
    font-weight: 600;
    letter-spacing: 0.04em;
    padding: 6px 13px;
    border-radius: var(--radius-pill);
    border: 1px solid var(--border-hairline);
    background: var(--void-3);
    color: var(--em-hi);
    cursor: pointer;
    transition: border-color var(--dur-feedback) var(--ease-standard),
      transform var(--dur-feedback) var(--ease-standard);
  }
  .nbtn:hover {
    border-color: var(--em-mid);
    transform: translateY(-1px);
  }
  .nbtn.body {
    color: var(--em-hi);
    border-color: var(--border-hairline);
  }
  /* M3.1 hint chip — same boxed-mono chip vocabulary as HelpOverlay's .kbd, now pill-
     radius like every other rail chip (M4.3 "one family of ghost pills"). */
  .kbdhint {
    font-size: var(--text-2xs);
    letter-spacing: 0.06em;
    padding: 3px 8px;
    border-radius: var(--radius-pill);
    background: var(--void-3);
    border: 1px solid var(--border-hairline);
    color: var(--em-low);
    cursor: pointer;
    transition: border-color var(--dur-feedback) var(--ease-standard),
      color var(--dur-feedback) var(--ease-standard);
  }
  .kbdhint:hover {
    border-color: var(--em-hi);
    color: var(--em-hi);
  }
  /* Tier C (meta, plan §M4.4): the LIVE/REPLAY source badge — borderless, faint by
     default; LIVE brightens (em-hi, it's the thing you're actively watching) but stays
     monochrome — neither state is a genuine alert (those stay red/amber, M4.1). was
     9px — below the scale's floor; nearest step is --text-2xs (10px, plan §M1.1). */
  .mode {
    font-size: var(--text-2xs);
    padding: 2px 7px;
    border-radius: var(--radius-pill);
    background: transparent;
    color: var(--em-faint);
    letter-spacing: 0.1em;
    text-transform: uppercase;
    border: 1px solid transparent;
    cursor: default;
  }
  .mode.islive {
    color: var(--em-hi);
    border-color: var(--border-hairline);
  }
  .mode.isreplay {
    color: var(--em-low);
  }

  /* phone: the rail has too many pills (crumbs + ModeBar + fly-into-body + badge +
     StaleBadge + kbdhint) for one 3-column grid row at 390px — the grid's tracks would
     force a single item to either overflow past the viewport edge or squeeze its text
     into a wrapped blob. Flex + space-between instead: left cluster stays left, right
     cluster stays right (never merged into one centered blob — Cosmos's own centered
     "N needs you" badge/filter bar, which +page.svelte can't touch, live in that exact
     center band on this view), wrapping to a new line only when a cluster itself runs
     out of room. */
  @media (max-width: 640px) {
    .toprail {
      display: flex;
      flex-wrap: wrap;
      justify-content: space-between;
      align-items: flex-start;
      row-gap: var(--space-2);
      column-gap: var(--space-2);
      left: var(--space-2);
      right: var(--space-2);
      top: var(--space-2);
    }
    .toprail.has-fab .tr-left {
      /* the ignite-fab sits above at top-left — clear it vertically (no room for the
         desktop's horizontal reservation on a narrow phone); the right cluster is
         untouched, so it stays above Cosmos's own centered badge instead of dropping
         into the same band. */
      padding-left: 0;
      padding-top: 52px;
    }
    .tr-left,
    .tr-center,
    .tr-right {
      flex-wrap: wrap;
    }
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
    /* `.g-center` (its parent, M4.3) is pointer-events:none so clicks reach the full-bleed
       canvas behind it — re-enable here so this list stays clickable once focused. */
    pointer-events: auto;
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
    font-size: var(--text-xs);
    background: var(--void-3);
    color: var(--em-hi);
    border: 1px solid var(--border-hairline);
    border-radius: var(--radius-sm);
    padding: 4px 9px;
    cursor: pointer;
    text-align: left;
    width: 100%;
  }
  .items-a11y button:focus-visible {
    outline: 2px solid var(--em-hi);
    outline-offset: 1px;
  }

  /* ══ M4.3 full-bleed canvas layer ═══════════════════════════════════════════
     Observatory's stage-level host — behind `.system-grid` (see z-index below),
     filling the whole System layer edge-to-edge instead of a boxed grid cell. */
  .canvas-stage {
    position: absolute;
    inset: 0;
    z-index: 0;
  }

  /* ══ wave U2 Task 1 / M4.3: the System dock — a CSS grid host ═══════════════
     Replaces the old stack of independently absolutely-positioned panels (each
     hand-tracking a sibling's pixel height via calc() — MetricsPanel hardcoding
     the Mechanism's 190px, VerdictPanel stacking off a --metrics-block constant,
     five files hand-tracking --strip-h). Named areas; each cell is a plain
     unstyled box, so a panel's own CSS carries only its internal look (bg/
     border/padding) — the grid owns placement + spacing (the token scale). Every
     cell stays transparent (no background here) — panels paint their own
     surface; the grid itself must never read as a boxed rectangle over the
     full-bleed canvas now sitting behind it (z-index above `.canvas-stage`/
     `.grain`, both z-index:0/auto).

     Rows: a reserved-but-empty first row ('.') clearing the floating top rail
     (`.toprail`, a stage-level sibling — not part of this grid) / hud (auto) +
     log (1fr, so LogPanel fills the rail and scrolls internally) / bottom-dock
     (auto) / cost strip (auto, --strip-h tall). "center" and "right" span the
     hud+log row band so the canvas gets the full rail height even though the
     left rail is itself split into two rows. Columns: left rail / center (the
     EMPTY safe-area probe, 1fr — this is what "breathes") / right rail. `.bare`
     (Ambient mode, or the Body drawer open) collapses both rails to zero width
     so the probe — and so Observatory's safeInsets — spans the whole dock;
     `gap: 0` there too so an empty rail leaves no dead gutter. */
  .system-grid {
    position: absolute;
    inset: 0;
    z-index: 1;
    display: grid;
    grid-template-columns: minmax(272px, 320px) 1fr minmax(272px, 320px);
    /* ~60px reserves the floating top rail's height (page-inset top offset + one row of
       ghost pills + a little breathing room) so nothing renders underneath it. */
    grid-template-rows: 60px auto 1fr auto auto;
    grid-template-areas:
      '.      .      .'
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

  .g-hud {
    grid-area: hud;
    min-width: 0;
    padding: var(--space-3) 0 0 var(--page-inset);
  }
  .g-log {
    grid-area: log;
    min-width: 0;
    min-height: 0;
    display: flex;
    padding: var(--space-2) 0 var(--space-3) var(--page-inset);
  }
  .g-center {
    grid-area: center;
    position: relative;
    min-width: 0;
    min-height: 0;
    /* the empty safe-area probe (M4.3) — no visual of its own, so it must not steal
       clicks/hover from the full-bleed canvas now rendering behind it. */
    pointer-events: none;
  }
  .g-right {
    grid-area: right;
    min-width: 0;
    min-height: 0;
    overflow-y: auto;
    display: flex;
    flex-direction: column;
    gap: var(--space-3);
    padding: var(--space-3) var(--page-inset) var(--space-3) 0;
  }
  .g-bottom {
    grid-area: bottom;
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: var(--space-2);
    padding: 0 var(--page-inset) var(--page-inset);
  }
  /* M4.3: RunControlBar + TransportBar merged into ONE full-width bar — this container
     supplies the hairline-top/background/padding; the panels inside (once the sibling
     M4.5 sweep lands) render as borderless transparent segments. Controls (fixed width)
     grouped left; the scrubber (when replay offers one) fills the remaining width. */
  .dock {
    width: 100%;
    display: flex;
    align-items: center;
    gap: var(--space-3);
    padding: var(--space-3) var(--space-4);
    background: var(--surface-panel);
    border-top: 1px solid var(--border-hairline);
    border-radius: var(--radius);
  }
  .dock-controls {
    flex: none;
  }
  .dock-scrub {
    flex: 1 1 auto;
    min-width: 0;
  }
  .g-strip {
    grid-area: strip;
    /* M4.3: CostQuotaStrip renders edge-to-edge internally — inset it by the page
       gutter here so it shares the same left/right margin as the rails/dock above. */
    padding: 0 var(--page-inset);
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
      /* the floating top rail wraps to several rows once ModeBar/StaleBadge/badges all
         share it on a narrow width — reserve generously (mirrors BodyView's own 100px
         phone clearance for the lighter body-view rail). */
      grid-template-rows: 132px auto minmax(220px, 42vh) auto auto auto auto;
      grid-template-areas:
        '.'
        'hud'
        'center'
        'bottom'
        'log'
        'right'
        'strip';
      gap: var(--space-2);
      overflow-y: auto;
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

  /* empty-state hint for a freshly-entered live loop (sits atop the control bar) —
     the plan's dim-glyph + one-line pattern (§M3.2), reused here for M1. */
  .empty-hint {
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: var(--space-1);
    text-align: center;
    pointer-events: none;
    max-width: 420px;
  }
  .eh-glyph {
    font-size: var(--text-xl);
    line-height: 1;
    color: var(--em-faint);
  }
  .eh-line {
    font-family: var(--font-mono);
    /* was 11.5px — an exact tie between --text-xs (11) and --text-sm (12); this file's
       other mono meta text (crumbs/nbtn) sits on --text-xs, so this follows that lead */
    font-size: var(--text-xs);
    color: var(--em-mid);
    margin: 0;
  }
  .eh-line strong {
    /* brightness carries the emphasis now, not amber (M4.1: amber is reserved for genuine
       alerts — needs-you/handoff/quota — and this is a neutral how-to hint). */
    color: var(--em-hi);
    font-weight: 600;
  }

  /* ignite affordance (M4.3) — position/shape/z-index only; `.btn-primary.btn-lg`
     (primitives.css) supplies the anatomy: solid light fill, the monochrome inversion is
     the strongest CTA in a hueless palette. `.has-fab` on `.toprail` reserves this
     button's width so it never collides with the "COSMOS" home crumb sharing this
     corner. */
  .ignite-fab {
    left: var(--page-inset);
    letter-spacing: 0.06em;
    transition: transform var(--dur-feedback) var(--ease-standard);
  }
  .ignite-fab:hover {
    transform: translateY(-1px);
  }
  .ignite-fab:active {
    transform: translateY(0);
  }

</style>
