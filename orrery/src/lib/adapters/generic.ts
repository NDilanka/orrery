// Generic adapter — built-in engine loops (roman / calc). The reducer already
// understands the core event set; the adapter just declares which events are
// in-scope and supplies the fixture/log file for dev replay.

import type { RawEvent } from '../types';
import type { Adapter } from './index';

const GENERIC_EVENTS = new Set<string>([
  'iter',
  'stop',
  'parse_error',
  'gate',
  'verdict',
  'model',
  'cost-alert',
  'cache',
  'plateau',
  'rollback',
  'handoff',
  'phase-timeout',
  'quota-hit',
  'quota-wait',
  'quota-resume',
]);

export const genericAdapter: Adapter = {
  id: 'generic',
  // pass-through; the reducer maps these directly
  normalize(ev: RawEvent): RawEvent | null {
    return ev;
  },
  handles(event: string): boolean {
    return GENERIC_EVENTS.has(event);
  },
};
