// Generic adapter — built-in engine loops (roman / calc). The reducer already
// understands the core event set and ignores anything it doesn't recognise
// (see `default: break` in reduce.ts); this adapter is just a pass-through.

import type { RawEvent } from '../types';
import type { Adapter } from './index';

export const genericAdapter: Adapter = {
  id: 'generic',
  // pass-through; the reducer maps these directly
  normalize(ev: RawEvent): RawEvent | null {
    return ev;
  },
};
