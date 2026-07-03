// Unit tests for RunStore's derived geometry/quota math (audit: zero coverage on run.svelte.ts).
// Uses the exported singleton (the class itself isn't exported as a value — see
// `export type { RunStore }`), reset via its own reset()/set() API between tests, mirroring
// session.test.ts's pattern for driving a runes-based store class from outside a component.
//
// $app/environment is mocked so `browser` reads true — RunStore's quota countdown timer
// (syncQuotaTimer) is a no-op otherwise, which would silently defeat the ticking tests below.

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

vi.mock('$app/environment', () => ({ browser: true }));

import { runStore } from './run.svelte';
import { initialState } from '../reduce';
import type { RunState, WorkItem } from '../types';

function makeItem(key: string, overrides: Partial<WorkItem> = {}): WorkItem {
  return {
    key,
    group: null,
    index: 0,
    status: 'backlog',
    gate: null,
    smoke: null,
    pr: null,
    ghost: null,
    strikes: 0,
    strikeBudget: 3,
    certified: false,
    costAttributed: 0,
    lastEventTs: 0,
    ...overrides,
  };
}

beforeEach(() => {
  runStore.reset();
});

afterEach(() => {
  // guard against a test that forgot to switch back — never let fake timers leak between files
  vi.useRealTimers();
});

describe('RunStore — cost horizon math', () => {
  it('horizonFrac is cumUsd/ceilingUsd, clamped to 2, and horizonVisible flips at 0.5', () => {
    const state: RunState = initialState();
    state.cost.ceilingUsd = 80;
    state.run.cumUsd = 40;
    runStore.set(state);
    expect(runStore.horizonFrac).toBeCloseTo(0.5);
    expect(runStore.horizonVisible).toBe(true);

    const below = initialState();
    below.cost.ceilingUsd = 80;
    below.run.cumUsd = 39;
    runStore.set(below);
    expect(runStore.horizonFrac).toBeCloseTo(39 / 80);
    expect(runStore.horizonVisible).toBe(false);
  });

  it('clamps horizonFrac to a max of 2 when cumUsd blows well past the ceiling', () => {
    const state = initialState();
    state.cost.ceilingUsd = 10;
    state.run.cumUsd = 1000;
    runStore.set(state);
    expect(runStore.horizonFrac).toBe(2);
  });

  it('an explicit ceilingUsd of 0 (not unset) yields frac 0, not the DEFAULT_CEILING fallback', () => {
    const state = initialState();
    state.cost.ceilingUsd = 0;
    state.run.cumUsd = 40;
    runStore.set(state);
    expect(runStore.ceilingUsd).toBe(0);
    expect(runStore.horizonFrac).toBe(0);
  });

  it('an unset (null) ceilingUsd falls back to DEFAULT_CEILING (80)', () => {
    const state = initialState();
    state.cost.ceilingUsd = null;
    state.run.cumUsd = 40;
    runStore.set(state);
    expect(runStore.ceilingUsd).toBe(80);
    expect(runStore.horizonFrac).toBeCloseTo(0.5);
  });

  it('usdRemaining is headroom clamped to 0, never negative once cumUsd exceeds the ceiling', () => {
    const state = initialState();
    state.cost.ceilingUsd = 50;
    state.run.cumUsd = 70;
    runStore.set(state);
    expect(runStore.usdRemaining).toBe(0);

    const under = initialState();
    under.cost.ceilingUsd = 50;
    under.run.cumUsd = 20;
    runStore.set(under);
    expect(runStore.usdRemaining).toBe(30);
  });

  it('minsToCeiling is usdRemaining/ratePerMin when the rate is positive', () => {
    const state = initialState();
    state.cost.ceilingUsd = 50;
    state.run.cumUsd = 20; // usdRemaining = 30
    state.cost.ratePerMin = 3;
    runStore.set(state);
    expect(runStore.minsToCeiling).toBeCloseTo(10);
  });

  it('minsToCeiling is null when ratePerMin is zero or negative (idle/warming)', () => {
    const zero = initialState();
    zero.cost.ratePerMin = 0;
    runStore.set(zero);
    expect(runStore.minsToCeiling).toBeNull();

    const negative = initialState();
    negative.cost.ratePerMin = -1;
    runStore.set(negative);
    expect(runStore.minsToCeiling).toBeNull();
  });
});

describe('RunStore — quota countdown (quotaSecondsLeft)', () => {
  it('is null when quota is inactive', () => {
    const state = initialState();
    state.quota.active = false;
    runStore.set(state);
    expect(runStore.quotaSecondsLeft).toBeNull();
  });

  // `nowMs` (the reactive clock quotaSecondsLeft reads) is seeded once at store construction
  // time from the REAL Date.now() and is only ever refreshed by the 30s interval tick (see
  // syncQuotaTimer). So right after set()-ing an active quota, nowMs is still that stale
  // construction-time value, not the fake clock — every test here advances one full 30s tick
  // first to let the interval prime nowMs onto the fake timeline before asserting.
  it('resumeAt in the future yields a positive seconds-remaining count', () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date('2026-01-01T00:00:00Z'));
    const state = initialState();
    state.quota.active = true;
    state.quota.resumeAt = new Date(Date.now() + 150_000).toISOString();
    runStore.set(state);
    vi.advanceTimersByTime(30_000); // primes nowMs onto the fake timeline
    expect(runStore.quotaSecondsLeft).toBe(120);
  });

  it('a resumeAt already in the past clamps to 0, never negative', () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date('2026-01-01T00:00:00Z'));
    const state = initialState();
    state.quota.active = true;
    state.quota.resumeAt = new Date(Date.now() - 60_000).toISOString();
    runStore.set(state);
    vi.advanceTimersByTime(30_000); // primes nowMs onto the fake timeline
    expect(runStore.quotaSecondsLeft).toBe(0);
  });

  it('falls back to waitSec when resumeAt is absent', () => {
    const state = initialState();
    state.quota.active = true;
    state.quota.resumeAt = null;
    state.quota.waitSec = 45;
    runStore.set(state);
    expect(runStore.quotaSecondsLeft).toBe(45);
  });

  it('ticks down over time without a new set() call, and its interval stops once quota goes inactive', () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date('2026-01-01T00:00:00Z'));
    const state = initialState();
    state.quota.active = true;
    state.quota.resumeAt = new Date(Date.now() + 150_000).toISOString();
    runStore.set(state);
    vi.advanceTimersByTime(30_000); // first tick primes nowMs onto the fake timeline

    const first = runStore.quotaSecondsLeft;
    expect(first).toBe(120);

    // advance another 30s tick without ever calling set() again — this is the freeze-bug guard:
    // the countdown must move on its own, driven purely by the interval.
    vi.advanceTimersByTime(30_000);
    const second = runStore.quotaSecondsLeft;
    expect(second).toBe(90);
    expect(second as number).toBeLessThan(first as number);

    // now let quota go inactive; the interval must stop (no leaked timer, no further recompute).
    const inactive = initialState();
    inactive.quota.active = false;
    runStore.set(inactive);
    expect(runStore.quotaSecondsLeft).toBeNull();
    expect(vi.getTimerCount()).toBe(0);

    // advancing further must not resurrect a countdown or throw from a stray callback
    vi.advanceTimersByTime(60_000);
    expect(runStore.quotaSecondsLeft).toBeNull();
    expect(vi.getTimerCount()).toBe(0);
  });
});

describe('RunStore — current item resolution', () => {
  it('resolves currentItem to the full WorkItem when the key exists', () => {
    const state = initialState();
    state.items['1-1-foo'] = makeItem('1-1-foo', { status: 'in-progress' });
    state.currentItem = '1-1-foo';
    runStore.set(state);
    expect(runStore.current).toEqual(state.items['1-1-foo']);
  });

  it('resolves to null when currentItem points at a key with no matching item', () => {
    const state = initialState();
    state.currentItem = 'does-not-exist';
    runStore.set(state);
    expect(runStore.current).toBeNull();
  });

  it('resolves to null when currentItem itself is null', () => {
    const state = initialState();
    state.currentItem = null;
    runStore.set(state);
    expect(runStore.current).toBeNull();
  });
});
