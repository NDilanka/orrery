// Session store (A4 extraction) — the transport lifecycle for the active System: which transport
// is mounted (tauri/ws/replay), its playback/ws-status side channels, and the control()/answer()
// verbs panels call. Previously all of this lived as plain `let`s in +page.svelte (a
// non-reactive `transport` prop-drilled as `control`/`answer` into RunControlBar / QAConsole /
// PlanetariumOverlay→DecisionSheet); +page.svelte now keeps only navigation/view state and reads
// this store for everything transport-shaped, and those panels import the store directly instead
// of receiving it via props. Follows the same store-singleton pattern as runStore/uiStore/
// cosmosStore (a plain class instance with `$state` fields, exported once).

import { browser } from '$app/environment';
import { base } from '$app/paths';
import { runStore } from './run.svelte';
import { logStore } from './log.svelte';
import { activityStore } from './activity.svelte';
import { cosmosStore } from './cosmos.svelte';
import { uiStore } from './ui.svelte';
import {
  createTransport,
  LOOPS,
  type LoopChoice,
  type Transport,
  type WsStatus,
} from '../transport';
import { isPlayback, type PlaybackState, type PlaybackTransport } from '../transport/replay';

// resolve fixture URLs against SvelteKit's base path
function withBase(choice: LoopChoice): LoopChoice {
  return {
    ...choice,
    fixtureUrl: `${base}/${choice.fixtureUrl}`,
    checkpointUrl: choice.checkpointUrl ? `${base}/${choice.checkpointUrl}` : undefined,
  };
}

class SessionStore {
  /** Which transport ACTUALLY mounted for the active System — the single source of truth for
   * the LIVE/REPLAY badge. `null` when no System is mounted (at the Cosmos). */
  transportKind = $state<'tauri' | 'ws' | 'replay' | null>(null);
  playback = $state<PlaybackTransport | null>(null);
  playbackState = $state<PlaybackState>({
    playing: false,
    speed: 1,
    cursor: 0,
    total: 0,
    done: false,
  });
  /** A7 ws freshness badge (only populated when the WebSocket transport is active). */
  wsStatus = $state<WsStatus | null>(null);
  /** One-shot ✦ Create & start intent. The Tuning Console can't start the run itself — the
   * new System's transport isn't mounted yet when the CTA is clicked — so it parks the intent
   * here and RunControlBar consumes it once a real transport is up, firing through its own
   * start path (pending/slow/stalled/error) instead of a fire-and-forget control() whose
   * rejection nobody would see. */
  autostartPending = $state(false);
  /** observe-only when web/no-token (ws transport says so) — disables answer/control. */
  observeOnly = $derived(this.wsStatus?.observeOnly ?? false);

  private transport: Transport | null = null;
  // Monotonic guard against mountLoop's re-entrancy: mountLoop has two awaits, and a fast
  // loop-switch (or unmount) can start a second call before the first resolves. Each call
  // captures its own epoch at entry and rechecks after every await.
  private mountEpoch = 0;

  // Build a LIVE loop choice from a loop's loop.json — for loops that exist on disk (and so
  // are listed by the Cosmos) but are NOT in the static seed LOOPS table (e.g. user-created
  // ones). fixtureUrl is empty because dynamic loops have no replay fixture; the Tauri/LAN
  // transports don't use it. Returns null if the def can't be read.
  private async loopChoiceFromDef(id: string): Promise<LoopChoice | null> {
    const def = await cosmosStore.loadLoopDef(id);
    if (!def) return null;
    const adapter = def.adapter === 'bmad' ? 'bmad' : 'generic';
    const stateDir = (def.stateDir ?? def.state_dir) as string | undefined;
    if (!stateDir) return null;
    return {
      id: String(def.id ?? id),
      name: String(def.name ?? id),
      theme: String(def.theme ?? 'plasma'),
      adapter,
      stateDir,
      logFile: typeof def.logFile === 'string' ? def.logFile : undefined,
      fixtureUrl: '',
    };
  }

  // ── mount a loop's System view via the existing transport/replay ───────────
  async mountLoop(id: string): Promise<void> {
    if (!browser) return;
    // Capture this call's epoch BEFORE the first await; a newer mountLoop/unmountLoop bumps
    // `mountEpoch` and this call must notice on the other side of every await below.
    const epoch = ++this.mountEpoch;
    this.transport?.stop();
    runStore.reset();
    logStore.clear();
    activityStore.clear();
    this.playback = null;
    this.wsStatus = null;
    // Seed loops come from the static LOOPS table (they carry replay fixtures); any other
    // loop on disk is resolved live from its loop.json so created loops are enterable too.
    const choice = LOOPS.find((l) => l.id === id) ?? (await this.loopChoiceFromDef(id));
    if (this.mountEpoch !== epoch) return; // superseded while resolving the loop def — bail
    if (!choice) {
      // Stale-badge fix: don't leave `transport`/`transportKind` pointing at the transport we
      // stopped above when the requested loop can't be resolved.
      this.transport = null;
      this.transportKind = null;
      return;
    }
    // Epoch-guard EVERY transport callback: a transport keeps a live reference to these
    // closures, so a superseded mount (fast loop-switch/unmount) whose start() is still
    // streaming — or a Tauri watcher that fires once more after stop() — must not write the
    // OLD loop's state/status/playback into the stores the NEWER mount now owns. Each closure
    // compares the captured `epoch` against the current `mountEpoch` and drops stale writes.
    const transport = createTransport(withBase(choice), {
      onState: (s) => {
        if (this.mountEpoch === epoch) runStore.set(s);
      },
      onWsStatus: (st) => {
        if (this.mountEpoch === epoch) this.wsStatus = st;
      },
    });
    this.transport = transport;
    this.transportKind = transport.kind;
    if (isPlayback(transport)) {
      this.playback = transport;
      transport.onPlayback((p) => {
        if (this.mountEpoch === epoch) this.playbackState = p;
      });
    } else if (uiStore.mode === 'rewind') {
      // Rewind needs a scrubbable run; a live feed can't scrub → fall back.
      uiStore.setMode('observatory');
    }
    await transport.start();
    // Superseded while start() was in flight: stop OUR transport (defense-in-depth alongside
    // TauriTransport's own `stopped` flag) and leave the newer mount's store state untouched.
    if (this.mountEpoch !== epoch) transport.stop();
  }

  unmountLoop(): void {
    ++this.mountEpoch; // supersede any mountLoop still in flight
    this.transport?.stop();
    this.transport = null;
    this.transportKind = null;
    this.playback = null;
    this.wsStatus = null;
    runStore.reset();
    logStore.clear();
    activityStore.clear();
  }

  async control(action: string): Promise<void> {
    await this.transport?.control(action);
  }

  requestAutostart(): void {
    this.autostartPending = true;
  }

  /** Read-and-clear, so a parked intent can never leak into a later System mount. */
  consumeAutostart(): boolean {
    const v = this.autostartPending;
    this.autostartPending = false;
    return v;
  }

  // A8 — an answer fn the QAConsole/DecisionSheet can call (present on tauri + ws; replay no-ops)
  answer(qid: string, text: string): void | Promise<void> {
    return this.transport?.answer?.(qid, text);
  }
}

export const sessionStore = new SessionStore();
export type { SessionStore };
