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
  /** observe-only when web/no-token (ws transport says so) — disables answer/control. */
  observeOnly = $derived(this.wsStatus?.observeOnly ?? false);

  private transport: Transport | null = null;

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
    this.transport?.stop();
    runStore.reset();
    logStore.clear();
    activityStore.clear();
    this.playback = null;
    this.wsStatus = null;
    // Seed loops come from the static LOOPS table (they carry replay fixtures); any other
    // loop on disk is resolved live from its loop.json so created loops are enterable too.
    const choice = LOOPS.find((l) => l.id === id) ?? (await this.loopChoiceFromDef(id));
    if (!choice) return;
    const transport = createTransport(withBase(choice), {
      onState: (s) => runStore.set(s),
      onWsStatus: (st) => (this.wsStatus = st),
    });
    this.transport = transport;
    this.transportKind = transport.kind;
    if (isPlayback(transport)) {
      this.playback = transport;
      transport.onPlayback((p) => (this.playbackState = p));
    } else if (uiStore.mode === 'rewind') {
      // Rewind needs a scrubbable run; a live feed can't scrub → fall back.
      uiStore.setMode('observatory');
    }
    await transport.start();
  }

  unmountLoop(): void {
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

  // A8 — an answer fn the QAConsole/DecisionSheet can call (present on tauri + ws; replay no-ops)
  answer(qid: string, text: string): void | Promise<void> {
    return this.transport?.answer?.(qid, text);
  }
}

export const sessionStore = new SessionStore();
export type { SessionStore };
