// Transport selector. Picks the Tauri transport when running inside the desktop
// app (window.__TAURI__ present), else the dev replay transport that streams a
// fixture .jsonl. Both feed a RunState to the same store callback.

import type { RunState } from '../types';
import { ReplayTransport, type ReplayConfig } from './replay';
import { TauriTransport, type TauriConfig } from './tauri';

export interface Transport {
  start(): Promise<void>;
  stop(): void;
  control(action: string): Promise<void>;
}

export interface TransportOpts {
  onState: (s: RunState) => void;
}

export function hasTauri(): boolean {
  return typeof window !== 'undefined' && '__TAURI__' in window;
}

/** A loop the picker can load: real (Tauri) config + dev replay config. */
export interface LoopChoice {
  id: string;
  name: string;
  theme: string;
  adapter: 'generic' | 'bmad';
  stateDir: string; // for Tauri watch_run
  fixtureUrl: string; // for dev replay
  checkpointUrl?: string;
  rateMs?: number;
}

export function createTransport(choice: LoopChoice, opts: TransportOpts): Transport {
  if (hasTauri()) {
    const cfg: TauriConfig = {
      stateDir: choice.stateDir,
      adapter: choice.adapter,
      loopId: choice.id,
    };
    return new TauriTransport(cfg, opts);
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
    stateDir: 'D:/dev/loop/.loop',
    fixtureUrl: 'fixtures/demo-events.jsonl',
    rateMs: 700,
  },
  {
    id: 'bmad',
    name: 'BMAD — brain2 sprint',
    theme: 'plasma',
    adapter: 'bmad',
    stateDir: 'D:/dev/loop/.loop',
    fixtureUrl: 'fixtures/bmad-log.jsonl',
    checkpointUrl: 'fixtures/checkpoint.json',
    rateMs: 90,
  },
  {
    id: 'roman',
    name: 'Roman numerals — fix until green',
    theme: 'ember',
    adapter: 'generic',
    stateDir: 'D:/dev/loop/.loop',
    fixtureUrl: 'fixtures/roman-log.jsonl',
    rateMs: 600,
  },
  {
    id: 'calc',
    name: 'Calculator — fix until green',
    theme: 'ember',
    adapter: 'generic',
    stateDir: 'D:/dev/loop/.loop',
    fixtureUrl: 'fixtures/calc-log.jsonl',
    rateMs: 600,
  },
];

export { ReplayTransport, TauriTransport };
