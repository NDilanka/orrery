// Unit tests for timefmt.ts (audit: zero coverage) — fmtClock/fmtDuration/fmtRelative plus the
// SANE_EPOCH_MS guard that treats synthetic pre-2000 fixture timestamps (line-index×1000, which
// reads as 1970-01-01 + a few seconds) as "no meaningful time" rather than rendering a bogus
// multi-decade duration.
//
// fmtClock renders in LOCAL time (getHours/getMinutes), so expectations are derived from an
// actual `Date` rather than hardcoded HH:MM strings — this keeps the test TZ-independent.

import { describe, it, expect } from 'vitest';
import { fmtClock, fmtDuration, fmtRelative } from './timefmt';

function hhmm(d: Date): string {
  return `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`;
}

describe('fmtClock', () => {
  it('formats a sane ISO timestamp as local HH:MM', () => {
    const iso = '2026-07-03T14:05:00Z';
    expect(fmtClock(iso)).toBe(hhmm(new Date(iso)));
  });

  it('pads single-digit hours/minutes', () => {
    const iso = '2026-01-01T00:05:00Z';
    expect(fmtClock(iso)).toBe(hhmm(new Date(iso)));
  });

  it('returns null for absent input', () => {
    expect(fmtClock(null)).toBeNull();
    expect(fmtClock(undefined)).toBeNull();
    expect(fmtClock('')).toBeNull();
  });

  it('returns null for an unparseable string', () => {
    expect(fmtClock('not-a-date')).toBeNull();
  });

  it('returns null for a synthetic pre-2000 timestamp (SANE_EPOCH_MS guard)', () => {
    expect(fmtClock('1970-01-01T00:00:03.000Z')).toBeNull();
  });

  it('accepts a timestamp exactly at the 2000-01-01 sane-epoch boundary', () => {
    const iso = '2000-01-01T00:00:00.000Z';
    expect(fmtClock(iso)).toBe(hhmm(new Date(iso)));
  });
});

describe('fmtDuration', () => {
  it('renders 0ms as "0m"', () => {
    expect(fmtDuration(0)).toBe('0m');
  });

  it('clamps negative durations to "0m" rather than going negative', () => {
    expect(fmtDuration(-60_000)).toBe('0m');
    expect(fmtDuration(-1)).toBe('0m');
  });

  it('rounds a sub-minute duration to the nearest minute', () => {
    expect(fmtDuration(10_000)).toBe('0m'); // 10s rounds down to 0m
    expect(fmtDuration(45_000)).toBe('1m'); // 45s rounds up to 1m
  });

  it('renders under an hour as "Ym" with no hours segment', () => {
    expect(fmtDuration(59 * 60_000)).toBe('59m');
  });

  it('renders exactly one hour as "1h 0m"', () => {
    expect(fmtDuration(60 * 60_000)).toBe('1h 0m');
  });

  it('renders hours and minutes together past the hour boundary', () => {
    expect(fmtDuration(61 * 60_000)).toBe('1h 1m');
    expect(fmtDuration(150 * 60_000)).toBe('2h 30m');
  });
});

describe('fmtRelative', () => {
  const now = Date.parse('2026-07-03T12:00:00Z');

  it('returns null for absent input', () => {
    expect(fmtRelative(null, now)).toBeNull();
    expect(fmtRelative(undefined, now)).toBeNull();
  });

  it('returns null for a synthetic pre-2000 timestamp (SANE_EPOCH_MS guard)', () => {
    expect(fmtRelative('1970-01-01T00:00:03.000Z', now)).toBeNull();
  });

  it('renders "just now" under 45 seconds', () => {
    expect(fmtRelative(new Date(now - 10_000).toISOString(), now)).toBe('just now');
    expect(fmtRelative(new Date(now - 44_000).toISOString(), now)).toBe('just now');
  });

  it('crosses into "Xm ago" at the 45s boundary', () => {
    expect(fmtRelative(new Date(now - 45_000).toISOString(), now)).toBe('1m ago');
    expect(fmtRelative(new Date(now - 5 * 60_000).toISOString(), now)).toBe('5m ago');
  });

  it('crosses into "Xh ago" once minutes round up to 60', () => {
    expect(fmtRelative(new Date(now - 59 * 60_000 - 45_000).toISOString(), now)).toBe('1h ago');
    expect(fmtRelative(new Date(now - 3 * 60 * 60_000).toISOString(), now)).toBe('3h ago');
  });

  it('crosses into "Xd ago" once hours round up to 24', () => {
    expect(fmtRelative(new Date(now - 23.5 * 60 * 60_000).toISOString(), now)).toBe('1d ago');
    expect(fmtRelative(new Date(now - 5 * 24 * 60 * 60_000).toISOString(), now)).toBe('5d ago');
  });

  it('defaults `now` to the real clock when omitted', () => {
    const iso = new Date(Date.now() - 10_000).toISOString();
    expect(fmtRelative(iso)).toBe('just now');
  });
});
