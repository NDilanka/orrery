// Unit tests for the liveness derivation (computeLiveness) + the activity store's set/clear.
// The derivation is pure (clock + arrival time injected), so no fake timers are needed.

import { describe, it, expect } from 'vitest';
import { computeLiveness, FRESH_MS, activityStore } from './activity.svelte';
import type { Activity } from '../types';

const beat = (over: Partial<Activity> = {}): Activity => ({
  ts: '2026-06-24T17:15:00Z',
  phase: 'dev-story',
  story: '5-2',
  elapsedSec: 100,
  dirty: 3,
  pid: 7240,
  ...over,
});

const T0 = Date.parse('2026-06-24T17:15:00Z'); // the beat's ts

describe('computeLiveness', () => {
  it('is "off" when there is no beat', () => {
    expect(computeLiveness(null, true, T0, T0).state).toBe('off');
  });

  it('is "off" when the run is not running, even with a fresh beat', () => {
    expect(computeLiveness(beat(), false, T0 + 1000, T0 + 1000).state).toBe('off');
  });

  it('is "live" for a recent beat while running', () => {
    const l = computeLiveness(beat(), true, T0 + 5_000, T0 + 5_000);
    expect(l.state).toBe('live');
    expect(l.ageMs).toBe(5_000);
  });

  it('is "idle" once the beat ages past FRESH_MS while still running', () => {
    const l = computeLiveness(beat(), true, T0 + FRESH_MS + 1, T0);
    expect(l.state).toBe('idle');
    expect(l.ageMs).toBeGreaterThan(FRESH_MS);
  });

  it('derives freshness from `ts`, not arrival time (stale file reads stale on mount)', () => {
    // The beat was written long ago (ts=T0) but only just ARRIVED (receivedAt=now). It must still
    // read stale, so a finished run's leftover activity.json never shows as "live" on mount.
    const now = T0 + 10 * 60_000; // 10 min after the beat was written
    const l = computeLiveness(beat(), true, now, now /* just arrived */);
    expect(l.state).toBe('idle');
    expect(l.ageMs).toBe(10 * 60_000);
  });

  it('falls back to arrival time when the beat carries no `ts`', () => {
    const now = 1_000_000;
    const l = computeLiveness(beat({ ts: undefined }), true, now, now - 4_000);
    expect(l.ageMs).toBe(4_000);
    expect(l.state).toBe('live');
  });

  it('elapsed ticks up locally from the beat value as time passes', () => {
    const l = computeLiveness(beat({ elapsedSec: 100 }), true, T0 + 7_000, T0 + 2_000);
    // 100 (engine) + (7000-2000)/1000 local = 105s
    expect(l.elapsedSec).toBe(105);
  });
});

describe('activityStore', () => {
  it('set stamps receivedAt; clear resets to null/0', () => {
    activityStore.set(beat());
    expect(activityStore.current?.phase).toBe('dev-story');
    expect(activityStore.receivedAt).toBeGreaterThan(0);
    activityStore.set(null);
    expect(activityStore.current).toBeNull();
    expect(activityStore.receivedAt).toBe(0);
    activityStore.clear();
    expect(activityStore.current).toBeNull();
  });
});
