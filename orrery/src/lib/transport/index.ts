// Transport selector. Three paths, one store callback + one reduce path:
//   - Tauri (window.__TAURI__ present)          → tauri.ts  (Channel + invoke)
//   - SPA served by the Rust LAN server over     → ws.ts     (WebSocket + POST)
//     http(s) in a browser with a /ws endpoint
//   - else (dev, `vite dev`)                     → replay.ts (fixture .jsonl)
// All three feed a RunState to the same store callback and share reduce.ts.

import type { RunState } from '../types';
import { getLoopsDir } from '../paths';
import { ReplayTransport, type ReplayConfig } from './replay';
import { TauriTransport, type TauriConfig } from './tauri';
import { WsTransport, type WsConfig } from './ws';

export interface Transport {
  /** Which transport actually mounted — the single source of truth for the LIVE/REPLAY badge. */
  readonly kind: 'tauri' | 'ws' | 'replay';
  start(): Promise<void>;
  stop(): void;
  control(action: string): Promise<void>;
  /** A8 — answer a pending review/retro question (optional; replay no-ops). */
  answer?(qid: string, text: string): Promise<void>;
}

export interface TransportOpts {
  onState: (s: RunState) => void;
}

export function hasTauri(): boolean {
  // Tauri v2 only injects `window.__TAURI__` when `withGlobalTauri` is enabled
  // (it isn't, by default). But `__TAURI_INTERNALS__` — the IPC bridge that
  // `invoke` rides — is ALWAYS present inside a Tauri webview. Detect on that so
  // the desktop app picks the live transport; otherwise it silently falls back
  // to dev replay, where every control verb (start/stop/reignite) is a no-op.
  if (typeof window === 'undefined') return false;
  return '__TAURI_INTERNALS__' in window || '__TAURI__' in window;
}

/**
 * Are we a plain browser served by the LAN server (vs Tauri / `vite dev`)?
 *
 * The Rust `lan.rs` serves the static SPA and exposes `/ws`. We detect that
 * environment heuristically: not Tauri, an http(s) origin, and EITHER an
 * explicit `?ws`/`?token` deep-link param OR a build-time flag the LAN serve
 * injects. `vite dev` (the dev replay path) is on http too, so to avoid
 * hijacking local development we require an explicit signal:
 *   - `?token=` / `?ws=1` in the page URL (the QR deep-link always carries one), or
 *   - a global `window.__ORRERY_WS__` the served index.html can set.
 */
export function hasWsServer(): boolean {
  if (typeof window === 'undefined') return false;
  if (hasTauri()) return false;
  const proto = window.location.protocol;
  if (proto !== 'http:' && proto !== 'https:') return false;
  if ('__ORRERY_WS__' in window && (window as Record<string, unknown>).__ORRERY_WS__) {
    return true;
  }
  const q = new URLSearchParams(window.location.search);
  if (q.has('token') || q.get('ws') === '1') return true;
  const hash = new URLSearchParams(window.location.hash.replace(/^#/, ''));
  return hash.has('token') || hash.get('ws') === '1';
}

/** A loop the picker can load: real (Tauri) config + dev replay config. */
export interface LoopChoice {
  id: string;
  name: string;
  theme: string;
  adapter: 'generic' | 'bmad';
  stateDir: string; // for Tauri watch_run
  logFile?: string; // log filename within stateDir (defaults per adapter when omitted)
  fixtureUrl: string; // for dev replay
  checkpointUrl?: string;
  rateMs?: number;
}

export interface CreateTransportOpts extends TransportOpts {
  /** ws freshness-badge callback (only invoked by the WebSocket transport). */
  onWsStatus?: (s: import('./ws').WsStatus) => void;
}

export function createTransport(choice: LoopChoice, opts: CreateTransportOpts): Transport {
  if (hasTauri()) {
    const cfg: TauriConfig = {
      stateDir: choice.stateDir,
      adapter: choice.adapter,
      loopId: choice.id,
      logFile: choice.logFile,
    };
    return new TauriTransport(cfg, opts);
  }
  if (hasWsServer()) {
    const cfg: WsConfig = {
      loopId: choice.id,
      adapter: choice.adapter,
      onStatus: opts.onWsStatus,
    };
    return new WsTransport(cfg, opts);
  }
  const cfg: ReplayConfig = {
    fixtureUrl: choice.fixtureUrl,
    checkpointUrl: choice.checkpointUrl,
    adapter: choice.adapter,
    loopId: choice.id,
    rateMs: choice.rateMs,
  };
  return new ReplayTransport(cfg, opts);
}

// The seeded loops (PROTOCOL §7) + the A3 synthetic showcase. Fixture URLs
// resolve under static/.
export const LOOPS: LoopChoice[] = [
  {
    // A3 synthetic stream: claimed-green→audit→seal, a refute, rollback+strike,
    // cooperative-stop→ember→reignite, and a weekly quota-frost. Designed to be
    // scrubbed in the TransportBar so every signature moment is visible.
    id: 'demo',
    name: 'A3 showcase — auditor · rollback · rest-states',
    theme: 'plasma',
    adapter: 'bmad',
    stateDir: '.loop',
    fixtureUrl: 'fixtures/demo-events.jsonl',
    rateMs: 700,
  },
  {
    id: 'bmad',
    name: 'BMAD — sample sprint (replay)',
    theme: 'plasma',
    adapter: 'bmad',
    // Live (Tauri/LAN): watch the real loop's state dir + log written by `loop-bmad`
    // (orrery/loops/bmad/loop.json). Absolute so the watcher + the spawn agree regardless
    // of the app's cwd. The fixtureUrl below is ONLY used by the pure `vite dev` replay.
    // A getter (not a literal) so the loops dir is read AT USE TIME — after boot's
    // resolveLoopsDir() may have applied the settings.general.loopsDir override — rather than
    // captured at module import when getLoopsDir() would still be the build-time default.
    get stateDir() {
      return `${getLoopsDir()}/bmad/.loop`;
    },
    logFile: 'log.jsonl',
    fixtureUrl: 'fixtures/bmad-log.jsonl',
    checkpointUrl: 'fixtures/checkpoint.json',
    rateMs: 90,
  },
  {
    id: 'roman',
    name: 'Roman numerals — fix until green',
    theme: 'ember',
    adapter: 'generic',
    stateDir: '.loop',
    fixtureUrl: 'fixtures/roman-log.jsonl',
    rateMs: 600,
  },
  {
    id: 'calc',
    name: 'Calculator — fix until green',
    theme: 'ember',
    adapter: 'generic',
    stateDir: '.loop',
    fixtureUrl: 'fixtures/calc-log.jsonl',
    rateMs: 600,
  },
  {
    // exercises the 'failed-dark' rest state: a run that ends on stop{ok:false}
    // (a halted epic retro) rather than a cooperative brake — the crimson
    // cracked-disc silhouette, never confusable with a banked ember.
    id: 'failed-dark',
    name: 'Failed dark — a genuinely crashed retro',
    theme: 'ember',
    adapter: 'bmad',
    stateDir: '.loop',
    fixtureUrl: 'fixtures/failed-dark-log.jsonl',
    rateMs: 500,
  },
];

export { ReplayTransport, TauriTransport, WsTransport };
export type { WsConfig, WsStatus } from './ws';
