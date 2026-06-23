// Live event log (A?) — a small, capped ring of the RAW protocol events for a textual "is it
// actually doing anything?" feed, complementing the orbital viz (which is glance-first and can sit
// visually still through a long silent phase). Fed by whichever transport is mounted:
//   - Tauri: the watcher emits a `Delta.Event` per new log.jsonl line → onDelta pushes here.
//   - Replay: each replayed fixture event is pushed here as it fires.
// The reduced RunState (run.svelte) stays the source of truth for the instrument; this is purely
// a human-readable activity tail and never drives state.

import type { RawEvent } from '../types';

export interface LogEntry {
  seq: number;
  event: string;
  /** one-line salient summary (story / target / stage / question / phase …) */
  detail: string;
  raw: RawEvent;
}

const CAP = 300;

function detailOf(r: RawEvent): string {
  const pick = (v: unknown) => (typeof v === 'string' && v.trim() ? v.trim() : null);
  const anyr = r as Record<string, unknown>;
  return (
    pick(anyr.story) ??
    pick(anyr.target) ??
    pick(anyr.stage) ??
    pick(anyr.label) ??
    pick(anyr.phase) ??
    pick(anyr.verdict) ??
    (typeof anyr.q === 'string' ? `“${(anyr.q as string).slice(0, 80)}…”` : null) ??
    (typeof anyr.cum === 'number' ? `$${(anyr.cum as number).toFixed(2)}` : null) ??
    ''
  );
}

class LogStore {
  entries = $state<LogEntry[]>([]);
  private seq = 0;

  push(ev: RawEvent): void {
    if (!ev || typeof ev !== 'object') return;
    const event = typeof ev.event === 'string' ? ev.event : '?';
    this.entries.push({ seq: this.seq++, event, detail: detailOf(ev), raw: ev });
    if (this.entries.length > CAP) this.entries.splice(0, this.entries.length - CAP);
  }

  /** Replace the whole log with `events` — used by the replay transport, which is prefix/scrub
   *  based (the visible log must always match the current cursor) rather than append-only. */
  setAll(events: RawEvent[]): void {
    const slice = events.length > CAP ? events.slice(events.length - CAP) : events;
    this.entries = slice.map((ev, i) => ({
      seq: i,
      event: ev && typeof ev.event === 'string' ? ev.event : '?',
      detail: detailOf(ev),
      raw: ev,
    }));
    this.seq = slice.length;
  }

  clear(): void {
    this.entries = [];
    this.seq = 0;
  }
}

export const logStore = new LogStore();
