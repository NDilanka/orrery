// DEV replay transport. Fetches a fixture .jsonl (served from static/fixtures/),
// splits lines, runs them through the adapter + reducer, and feeds the store at
// a configurable rate: all-at-once for a snapshot, or e.g. 1 line / 120ms to
// animate the run. Never touches a live stateDir. No Tauri required.

import type { Checkpoint, RawEvent, RunState } from '../types';
import { reduce } from '../reduce';
import { normalizeAll } from '../adapters';
import type { Transport, TransportOpts } from './index';

function parseJsonl(text: string): RawEvent[] {
  const out: RawEvent[] = [];
  for (const raw of text.split(/\r?\n/)) {
    const line = raw.trim();
    if (!line) continue;
    try {
      out.push(JSON.parse(line) as RawEvent);
    } catch {
      // tolerate a trailing partial / malformed line (the tailer holds it live)
    }
  }
  return out;
}

export interface ReplayConfig {
  fixtureUrl: string; // e.g. /fixtures/bmad-log.jsonl
  checkpointUrl?: string; // optional /fixtures/checkpoint.json
  adapter: string; // 'generic' | 'bmad'
  loopId: string;
  /** ms between lines; 0 (or omitted) = snapshot all at once */
  rateMs?: number;
}

export class ReplayTransport implements Transport {
  private timer: ReturnType<typeof setTimeout> | null = null;
  private events: RawEvent[] = [];
  private checkpoint: Checkpoint | undefined;
  private cursor = 0;
  private cfg: ReplayConfig;
  private onState: (s: RunState) => void;

  constructor(cfg: ReplayConfig, opts: TransportOpts) {
    this.cfg = cfg;
    this.onState = opts.onState;
  }

  async start(): Promise<void> {
    const text = await fetchText(this.cfg.fixtureUrl);
    this.events = normalizeAll(this.cfg.adapter, parseJsonl(text));
    if (this.cfg.checkpointUrl) {
      try {
        const cpText = await fetchText(this.cfg.checkpointUrl);
        this.checkpoint = JSON.parse(cpText) as Checkpoint;
      } catch {
        this.checkpoint = undefined;
      }
    }
    this.cursor = 0;

    const rate = this.cfg.rateMs ?? 0;
    if (rate <= 0) {
      // snapshot: reduce everything immediately
      this.cursor = this.events.length;
      this.emit();
      return;
    }
    // animate: reveal one more line per tick (re-reduce the prefix — idempotent)
    this.cursor = 0;
    this.emit(); // empty/initial frame
    this.tick(rate);
  }

  private tick(rate: number) {
    this.timer = setTimeout(() => {
      this.cursor = Math.min(this.cursor + 1, this.events.length);
      this.emit();
      if (this.cursor < this.events.length) this.tick(rate);
    }, rate);
  }

  private emit() {
    const slice = this.events.slice(0, this.cursor);
    // checkpoint is only authoritative once the whole log has played
    const cp = this.cursor >= this.events.length ? this.checkpoint : undefined;
    const state = reduce(slice, { checkpoint: cp, loopId: this.cfg.loopId });
    this.onState(state);
  }

  stop(): void {
    if (this.timer) {
      clearTimeout(this.timer);
      this.timer = null;
    }
  }

  // ── control methods (no-op in replay; resolve gracefully) ────────────────
  async control(action: string): Promise<void> {
    // eslint-disable-next-line no-console
    console.info(`[replay] control "${action}" is a no-op in dev replay.`);
  }
}

async function fetchText(url: string): Promise<string> {
  const res = await fetch(url);
  if (!res.ok) throw new Error(`replay: failed to fetch ${url} (${res.status})`);
  return res.text();
}
