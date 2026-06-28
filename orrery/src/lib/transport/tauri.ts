// Real-app transport. Opens a Channel via the `watch_run` command and feeds the
// resulting Deltas (snapshot | event | state) into the store via reduce.ts.
// Control methods invoke the Rust control surface (§6). Only used when the app
// runs inside Tauri (window.__TAURI__ present); the dev replay path is separate.

import type { Delta, RawEvent, RunState } from '../types';
import { initialState } from '../reduce';
import { logStore } from '../stores/log.svelte';
import type { Transport, TransportOpts } from './index';

// Absolute path to the loops/ registry. A5 (loop library) will source this from
// config / list_loops; until then the control commands resolve it from here.
// Absolute so control commands find loops regardless of the app's working directory
// (the launcher runs from orrery/, cargo from orrery/src-tauri — a relative 'orrery/loops'
// resolves against neither). LOCAL override; a packaged build should resolve this via a
// Tauri path API instead. Matches cosmos.svelte.ts DEFAULT_LOOPS_DIR.
const DEFAULT_LOOPS_DIR = 'D:/dev/loop/orrery/loops';

export interface TauriConfig {
  stateDir: string;
  adapter: string;
  loopId: string;
  loopsDir?: string;
  logFile?: string;
}

export class TauriTransport implements Transport {
  readonly kind = 'tauri' as const;
  private cfg: TauriConfig;
  private onState: (s: RunState) => void;
  private state: RunState;
  private unlisten: (() => void) | null = null;

  constructor(cfg: TauriConfig, opts: TransportOpts) {
    this.cfg = cfg;
    this.onState = opts.onState;
    this.state = initialState(cfg.loopId);
  }

  async start(): Promise<void> {
    // Lazy import so the bundle never hard-requires Tauri at build/SSR time.
    const { invoke, Channel } = await import('@tauri-apps/api/core');
    const channel = new Channel<Delta>();
    channel.onmessage = (delta: Delta) => this.onDelta(delta);
    await invoke('watch_run', {
      stateDir: this.cfg.stateDir,
      adapter: this.cfg.adapter,
      logFile: this.cfg.logFile,
      channel,
    });
    // Channel has no explicit close in the surface; track for symmetry.
    this.unlisten = () => {
      channel.onmessage = () => {};
    };
  }

  private onDelta(delta: Delta) {
    if (delta.kind === 'event') {
      // raw event → live LOG feed only; the authoritative reduced RunState arrives via the
      // matching `state` delta (the watcher sends Event then State per log line).
      logStore.push(delta.event as RawEvent);
      return;
    }
    // snapshot | state: the full reduced RunState from the Rust watcher.
    this.state = delta.state;
    // hand a fresh reference so the runes store registers the change
    this.onState({ ...this.state });
  }

  stop(): void {
    this.unlisten?.();
    this.unlisten = null;
  }

  async control(action: string): Promise<void> {
    const { invoke } = await import('@tauri-apps/api/core');
    const loopId = this.cfg.loopId;
    const loopsDir = this.cfg.loopsDir ?? DEFAULT_LOOPS_DIR;
    switch (action) {
      case 'start':
        await invoke('start_loop', { loopId, loopsDir });
        break;
      case 'stop:phase':
        await invoke('stop_loop', { loopId, loopsDir, mode: 'phase' });
        break;
      case 'stop:story':
        await invoke('stop_loop', { loopId, loopsDir, mode: 'story' });
        break;
      case 'stop:now':
        await invoke('stop_loop', { loopId, loopsDir, mode: 'now' });
        break;
      case 'cancel-stop':
        await invoke('cancel_stop', { loopId, loopsDir });
        break;
      case 'resume':
        await invoke('resume_loop', { loopId, loopsDir });
        break;
      default:
        // eslint-disable-next-line no-console
        console.warn(`[tauri] unknown control action "${action}"`);
    }
  }

  // A8 — answer a pending review/retro question. Writes the engine's answer.json
  // inbox via the Rust command (PROTOCOL §6 `answer_question`). Inert until the
  // engine/script reads it; the resulting `review-answer` flows back over the
  // watch Channel and the reducer reflects it.
  async answer(qid: string, text: string): Promise<void> {
    const { invoke } = await import('@tauri-apps/api/core');
    const loopId = this.cfg.loopId;
    const loopsDir = this.cfg.loopsDir ?? DEFAULT_LOOPS_DIR;
    await invoke('answer_question', { loopId, loopsDir, qid, text });
  }
}
