// Liveness heartbeat store (Svelte 5 runes). Holds the latest `activity.json` beat the engine
// overwrites every ~12s DURING a long agent step (e.g. a 30-min dev-story that emits no log line
// until its gate). The LogPanel reads this to render a real "is it actually working right now?"
// indicator — freshness, current phase, elapsed time, files-changed — instead of the old
// entries.length>0 dot that pulsed forever once any event had arrived.
//
// Fed by whichever transport is mounted (Tauri Channel / LAN ws → a `Delta.activity`). The replay
// transport never sets it, so dev/replay simply shows no live beat. This is purely a liveness
// surface and never drives the reduced RunState.

import type { Activity } from '../types';

/** Beats within this many ms read as "live" (≈2 missed 12s heartbeats); older = "idle". */
export const FRESH_MS = 30_000;

export interface Liveness {
  /** 'live' = a fresh beat (working now) · 'idle' = running but quiet · 'off' = nothing to show */
  state: 'live' | 'idle' | 'off';
  /** ms since the beat was written (from its `ts`, else arrival time) */
  ageMs: number;
  /** seconds in the current step: the beat's value + local time since it arrived (ticks up) */
  elapsedSec: number;
}

/**
 * Pure liveness derivation — extracted from the LogPanel so it is unit-testable. A beat only reads
 * "live"/"idle" while the run is actually running (so a stale activity.json from a finished run
 * never shows as working); freshness prefers the beat's own `ts` (engine write time) so an old
 * file reads stale immediately on mount, falling back to arrival time when `ts` is absent.
 */
export function computeLiveness(
  a: Activity | null,
  running: boolean,
  now: number,
  receivedAt: number,
  freshMs: number = FRESH_MS,
): Liveness {
  if (!a) return { state: 'off', ageMs: Infinity, elapsedSec: 0 };
  let ageMs = Number.POSITIVE_INFINITY;
  if (a.ts) {
    const t = Date.parse(a.ts);
    if (!Number.isNaN(t)) ageMs = Math.max(0, now - t);
  }
  if (!Number.isFinite(ageMs)) ageMs = Math.max(0, now - receivedAt);
  const elapsedSec = a.elapsedSec + Math.max(0, (now - receivedAt) / 1000);
  const state: Liveness['state'] = !running ? 'off' : ageMs < freshMs ? 'live' : 'idle';
  return { state, ageMs, elapsedSec };
}

class ActivityStore {
  /** The most recent beat, or null when there is none (file absent / loop idle). */
  current = $state<Activity | null>(null);
  /** ms-epoch when the last beat ARRIVED — the freshness fallback when a beat carries no `ts`. */
  receivedAt = $state<number>(0);

  set(a: Activity | null): void {
    this.current = a;
    this.receivedAt = a ? Date.now() : 0;
  }

  clear(): void {
    this.current = null;
    this.receivedAt = 0;
  }
}

export const activityStore = new ActivityStore();
export type { ActivityStore };
