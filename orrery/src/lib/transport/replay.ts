// DEV replay transport. Fetches a fixture .jsonl (served from static/fixtures/),
// splits lines, runs them through the adapter + reducer, and feeds the store as
// an ANIMATED unfolding: events are revealed over wall-clock time so you can
// WATCH a run evolve. Exposes a small transport surface (play / pause / speed /
// scrub) consumed by the TransportBar panel. Never touches a live stateDir.
//
// Scrub / rewind: the cursor is just an index into the (immutable) event list;
// because reduce() is pure + idempotent, jumping to any time T is a re-reduce of
// the prefix events[0..cursorForTime(T)] — no replay-from-scratch needed.

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
  /** ms between lines at 1x speed; 0 (or omitted) = snapshot all at once */
  rateMs?: number;
}

/** Observable playback state for a transport control surface. */
export interface PlaybackState {
  playing: boolean;
  speed: number; // 1 | 4 | 16
  cursor: number; // events revealed so far
  total: number; // total events
  done: boolean; // cursor === total
}

/** A transport that can be scrubbed/played (the dev replay). */
export interface PlaybackTransport extends Transport {
  onPlayback(cb: (s: PlaybackState) => void): void;
  play(): void;
  pause(): void;
  toggle(): void;
  setSpeed(speed: number): void;
  /** scrub to an absolute cursor (0..total); re-reduces the prefix. */
  seek(cursor: number): void;
  restart(): void;
}

export function isPlayback(t: Transport | null): t is PlaybackTransport {
  return !!t && typeof (t as PlaybackTransport).onPlayback === 'function';
}

export class ReplayTransport implements PlaybackTransport {
  private timer: ReturnType<typeof setTimeout> | null = null;
  private events: RawEvent[] = [];
  private checkpoint: Checkpoint | undefined;
  private cursor = 0;
  private cfg: ReplayConfig;
  private onState: (s: RunState) => void;
  private playbackCb: ((s: PlaybackState) => void) | null = null;
  private speed = 1;
  private playing = false;
  private baseRate: number;
  private started = false;

  constructor(cfg: ReplayConfig, opts: TransportOpts) {
    this.cfg = cfg;
    this.onState = opts.onState;
    // a sensible default cadence so even a "snapshot" loop animates on screen
    this.baseRate = cfg.rateMs && cfg.rateMs > 0 ? cfg.rateMs : 220;
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
    this.started = true;
    this.emit(); // empty/initial frame
    this.emitPlayback();
    // auto-play the unfolding
    this.play();
  }

  // ── playback controls ─────────────────────────────────────────────────────
  onPlayback(cb: (s: PlaybackState) => void): void {
    this.playbackCb = cb;
    if (this.started) this.emitPlayback();
  }

  play(): void {
    if (this.playing) return;
    if (this.cursor >= this.events.length) this.cursor = 0; // replay from start
    this.playing = true;
    this.schedule();
    this.emitPlayback();
  }

  pause(): void {
    this.playing = false;
    this.clearTimer();
    this.emitPlayback();
  }

  toggle(): void {
    this.playing ? this.pause() : this.play();
  }

  setSpeed(speed: number): void {
    this.speed = speed > 0 ? speed : 1;
    if (this.playing) {
      this.clearTimer();
      this.schedule();
    }
    this.emitPlayback();
  }

  seek(cursor: number): void {
    this.cursor = Math.max(0, Math.min(cursor, this.events.length));
    this.emit();
    this.emitPlayback();
    if (this.playing) {
      this.clearTimer();
      this.schedule();
    }
  }

  restart(): void {
    this.cursor = 0;
    this.emit();
    this.emitPlayback();
    this.play();
  }

  private schedule(): void {
    if (!this.playing) return;
    if (this.cursor >= this.events.length) {
      this.playing = false;
      this.emitPlayback();
      return;
    }
    const delay = Math.max(16, this.baseRate / this.speed);
    this.timer = setTimeout(() => {
      this.cursor = Math.min(this.cursor + 1, this.events.length);
      this.emit();
      this.emitPlayback();
      if (this.cursor < this.events.length && this.playing) this.schedule();
      else {
        this.playing = false;
        this.emitPlayback();
      }
    }, delay);
  }

  private emit() {
    const slice = this.events.slice(0, this.cursor);
    // checkpoint is only authoritative once the whole log has played
    const cp = this.cursor >= this.events.length ? this.checkpoint : undefined;
    const state = reduce(slice, { checkpoint: cp, loopId: this.cfg.loopId });
    this.onState(state);
  }

  private emitPlayback() {
    this.playbackCb?.({
      playing: this.playing,
      speed: this.speed,
      cursor: this.cursor,
      total: this.events.length,
      done: this.cursor >= this.events.length,
    });
  }

  private clearTimer() {
    if (this.timer) {
      clearTimeout(this.timer);
      this.timer = null;
    }
  }

  stop(): void {
    this.playing = false;
    this.clearTimer();
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
