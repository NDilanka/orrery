// BMAD adapter — the external bmad-loop.ps1 superset. Epics → rings, stories →
// planets, PR → dock, review/retro → oracle. The reducer already understands
// the BMAD superset; the adapter normalises a couple of conveniences:
//   - ensures every story-bearing event carries an `epic` (derived from key)
//   - is the place domain flair selection would live for the renderer.

import type { RawEvent } from '../types';
import { epicOf } from '../reduce';
import type { Adapter } from './index';

export const bmadAdapter: Adapter = {
  id: 'bmad',
  normalize(ev: RawEvent): RawEvent | null {
    // Backfill epic from a story/target key so rings group correctly even when
    // the writer omitted `epic` (it is non-deterministic in the real log).
    const key = ev.story ?? ev.target ?? ev.item;
    if (typeof key === 'string' && ev.epic == null) {
      const epic = epicOf(key);
      if (epic) return { ...ev, epic };
    }
    return ev;
  },
};
