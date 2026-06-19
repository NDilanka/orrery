// Real-app transport. Opens a Channel via the `watch_run` command and feeds the
// resulting Deltas (snapshot | event | state) into the store via reduce.ts.
// Control methods invoke the Rust control surface (§6). Only used when the app
// runs inside Tauri (window.__TAURI__ present); the dev replay path is separate.

import type { Delta, RawEvent, RunState } from '../types';
import { apply, finalize, initialState } from '../reduce';
import { normalizeAll } from '../adapters';
import type { Transport, TransportOpts } from './index';

// Absolute path to the loops/ registry. A5 (loop library) will source this from
// config / list_loops; until then the control commands resolve it from here.
const DEFAULT_LOOPS_DIR = 'D:/dev/loop/orrery/loops';

export interface TauriConfig {
  stateDir: string;
  adapter: string;
  loopId: string;
  loopsDir?: string;
}

export class TauriTransport implements Transport {
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
      channel,
    });
    // Channel has no explicit close in the surface; track for symmetry.
    this.unlisten = () => {
      channel.onmessage = () => {};
    };
  }

  private onDelta(delta: Delta) {
    switch (delta.kind) {
      case 'snapshot':
      case 'state':
        this.state = delta.state;
        break;
      case 'event': {
        const [ev] = normalizeAll(this.cfg.adapter, [delta.event as RawEvent]);
        if (ev) {
          apply(this.state, ev, Date.now());
          finalize(this.state);
        }
        break;
      }
    }
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
