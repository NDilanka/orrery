// Unit tests for ReplayTransport (audit: zero coverage). `global.fetch` is mocked to serve a
// small inline JSONL fixture (hand-built to match the RawEvent/reducer shapes documented in
// types.ts + reduce.ts — 'start' / 'story-start' / 'dev-gate' / 'verdict', the same event
// vocabulary the real fixtures under static/fixtures/ use) via the 'generic' adapter, which is a
// pure pass-through (see adapters/generic.ts) so the events reach the reducer unmodified.
//
// Covers: start()'s initial emitted state, scrub/seek determinism against an independent
// reduce() of the same prefix, pause/play reflected in the onPlayback callback, and that
// changing speed mid-playback never skips an event (every cursor value 1..N is observed).

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { ReplayTransport } from './replay';
import { reduce } from '../reduce';
import { normalizeAll } from '../adapters';
import type { RawEvent, RunState } from '../types';
import type { PlaybackState } from './replay';

const FIXTURE_LINES: RawEvent[] = [
  { event: 'start', target: '1-1-foo', branch: 'feat/foo', baselinePass: 10 },
  { event: 'story-start', story: '1-1-foo', status: 'in-progress', epic: '1', index: 0 },
  {
    event: 'dev-gate',
    story: '1-1-foo',
    epic: '1',
    cum: 0.5,
    green: true,
    pass: 10,
    fail: 0,
    total: 10,
    baselinePass: 10,
    status: 'review',
    codegenOk: true,
    lintOk: true,
    testOk: true,
  },
  { event: 'verdict', item: '1-1-foo', pass: true, evidence: 'all green' },
  { event: 'story-start', story: '1-2-bar', status: 'in-progress', epic: '1', index: 1 },
  {
    event: 'dev-gate',
    story: '1-2-bar',
    epic: '1',
    cum: 1.2,
    green: true,
    pass: 5,
    fail: 0,
    total: 5,
    baselinePass: 5,
    status: 'review',
  },
];
const FIXTURE_TEXT = FIXTURE_LINES.map((l) => JSON.stringify(l)).join('\n');

/** An independent reduction of the fixture's first `n` events — the ground truth ReplayTransport
 *  must match when scrubbed to cursor `n` (mirrors what `seek`/emit do internally). */
function expectedStateAt(n: number, loopId = 'test-loop'): RunState {
  const events = normalizeAll('generic', FIXTURE_LINES.slice(0, n));
  return reduce(events, { loopId });
}

function makeTransport(onState = vi.fn<(s: RunState) => void>()) {
  const transport = new ReplayTransport(
    { fixtureUrl: '/fixtures/test.jsonl', adapter: 'generic', loopId: 'test-loop', rateMs: 100 },
    { onState },
  );
  return { transport, onState };
}

beforeEach(() => {
  vi.stubGlobal(
    'fetch',
    vi.fn(async (url: string) => ({
      ok: true,
      status: 200,
      text: async () => {
        if (String(url).includes('checkpoint')) return '{}';
        return FIXTURE_TEXT;
      },
    })),
  );
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

describe('ReplayTransport.start', () => {
  it('emits an initial reduced state (empty prefix) synchronously as part of start()', async () => {
    vi.useFakeTimers();
    const { transport, onState } = makeTransport();
    const startP = transport.start();
    await vi.advanceTimersByTimeAsync(0);
    await startP;

    expect(onState).toHaveBeenCalled();
    const first = onState.mock.calls[0][0] as RunState;
    expect(first).toEqual(expectedStateAt(0));
    transport.stop();
  });
});

describe('ReplayTransport.seek — scrub determinism', () => {
  it('seeking to cursor N reproduces an independent reduce() of the first N events', async () => {
    vi.useFakeTimers();
    const { transport, onState } = makeTransport();
    const startP = transport.start();
    await vi.advanceTimersByTimeAsync(0);
    await startP;
    transport.pause(); // hold the cursor still so seek() is the only driver

    for (const n of [1, 3, 6, 0, 4]) {
      onState.mockClear();
      transport.seek(n);
      expect(onState).toHaveBeenCalledTimes(1);
      expect(onState.mock.calls[0][0]).toEqual(expectedStateAt(n));
    }
    transport.stop();
  });

  it('seeking is idempotent — seeking to the same cursor twice yields the same state', async () => {
    vi.useFakeTimers();
    const { transport, onState } = makeTransport();
    const startP = transport.start();
    await vi.advanceTimersByTimeAsync(0);
    await startP;
    transport.pause();

    transport.seek(4);
    const a = onState.mock.calls.at(-1)?.[0];
    transport.seek(4);
    const b = onState.mock.calls.at(-1)?.[0];
    expect(a).toEqual(b);
    expect(a).toEqual(expectedStateAt(4));
    transport.stop();
  });

  it('clamps seek targets to [0, total]', async () => {
    vi.useFakeTimers();
    const { transport, onState } = makeTransport();
    const startP = transport.start();
    await vi.advanceTimersByTimeAsync(0);
    await startP;
    transport.pause();

    onState.mockClear();
    transport.seek(999);
    expect(onState.mock.calls[0][0]).toEqual(expectedStateAt(FIXTURE_LINES.length));

    onState.mockClear();
    transport.seek(-5);
    expect(onState.mock.calls[0][0]).toEqual(expectedStateAt(0));
    transport.stop();
  });
});

describe('ReplayTransport.play/pause — playback state transitions', () => {
  it('reflects playing:true after start() (auto-play) and playing:false after pause()', async () => {
    vi.useFakeTimers();
    const { transport } = makeTransport();
    const states: PlaybackState[] = [];
    transport.onPlayback((s) => states.push(s));

    const startP = transport.start();
    await vi.advanceTimersByTimeAsync(0);
    await startP;
    expect(states.at(-1)?.playing).toBe(true);

    transport.pause();
    expect(states.at(-1)?.playing).toBe(false);

    transport.play();
    expect(states.at(-1)?.playing).toBe(true);

    transport.toggle();
    expect(states.at(-1)?.playing).toBe(false);
    transport.toggle();
    expect(states.at(-1)?.playing).toBe(true);

    transport.stop();
  });

  it('reaches done:true with playing:false once the cursor exhausts all events', async () => {
    vi.useFakeTimers();
    const { transport } = makeTransport();
    const states: PlaybackState[] = [];
    transport.onPlayback((s) => states.push(s));

    const startP = transport.start();
    await vi.advanceTimersByTimeAsync(0);
    await startP;
    await vi.advanceTimersByTimeAsync(100 * (FIXTURE_LINES.length + 2));

    const last = states.at(-1)!;
    expect(last.done).toBe(true);
    expect(last.playing).toBe(false);
    expect(last.cursor).toBe(FIXTURE_LINES.length);
    transport.stop();
  });
});

describe('ReplayTransport.setSpeed — no skipped events', () => {
  it('changing speed mid-playback still visits every cursor value from 1..total in order', async () => {
    vi.useFakeTimers();
    const { transport } = makeTransport();
    const cursors: number[] = [];
    transport.onPlayback((s) => cursors.push(s.cursor));

    const startP = transport.start();
    await vi.advanceTimersByTimeAsync(0);
    await startP;

    // let a couple events land at 1x, then speed up — the schedule loop always increments the
    // cursor by exactly 1 per tick regardless of delay, so no event should ever be skipped.
    await vi.advanceTimersByTimeAsync(250);
    transport.setSpeed(4);
    await vi.advanceTimersByTimeAsync(250);
    transport.setSpeed(16);
    await vi.advanceTimersByTimeAsync(1000);

    const seen = cursors.filter((c) => c > 0);
    const expectedSeq = Array.from({ length: FIXTURE_LINES.length }, (_, i) => i + 1);
    // every cursor 1..total must appear, in non-decreasing, gap-free order (dedupe consecutive
    // repeats from non-cursor-changing playback emissions, e.g. the final done:true emit).
    const dedup = seen.filter((c, i) => i === 0 || c !== seen[i - 1]);
    expect(dedup).toEqual(expectedSeq);
    transport.stop();
  });
});

describe('ReplayTransport.markers', () => {
  it('pins a verdict marker at cursor index+1', async () => {
    vi.useFakeTimers();
    const { transport } = makeTransport();
    const startP = transport.start();
    await vi.advanceTimersByTimeAsync(0);
    await startP;
    transport.pause();

    const marks = transport.markers();
    expect(marks).toContainEqual({ index: 4, kind: 'verdict-pass', label: 'sealed · 1-1-foo' });
    transport.stop();
  });
});
