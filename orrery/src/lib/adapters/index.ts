// Adapter registry — picks/normalises per-loop event mappings and supplies the
// dev fixture path. The orrery renders the universal RunState; adapters light
// domain flair and select which log a replay loads.

import type { RawEvent } from '../types';
import { genericAdapter } from './generic';
import { bmadAdapter } from './bmad';

export interface Adapter {
  id: 'generic' | 'bmad' | 'custom';
  /** normalise a raw event (e.g. backfill epic); return null to drop it. */
  normalize(ev: RawEvent): RawEvent | null;
  /** whether this adapter recognises an event name (else reducer ignores it). */
  handles(event: string): boolean;
}

export type AdapterId = Adapter['id'];

const REGISTRY: Record<string, Adapter> = {
  generic: genericAdapter,
  bmad: bmadAdapter,
};

export function getAdapter(id: string): Adapter {
  return REGISTRY[id] ?? genericAdapter;
}

/** Run a raw event list through an adapter's normaliser. */
export function normalizeAll(adapterId: string, events: RawEvent[]): RawEvent[] {
  const a = getAdapter(adapterId);
  const out: RawEvent[] = [];
  for (const ev of events) {
    const n = a.normalize(ev);
    if (n) out.push(n);
  }
  return out;
}

export { genericAdapter, bmadAdapter };
