// Unit tests for the Pixi/uPlot color bridge (theme.ts). We deliberately do NOT exercise
// initTheme()'s live getComputedStyle() path here — jsdom never resolves CSS custom
// properties (there's no real layout/paint engine behind it), so a "does it read the DOM"
// test would just assert jsdom's behavior, not ours. What's actually load-bearing and worth
// covering: the static fallback table is complete/valid, and the rgb-string parser handles
// every shape the browser can hand back.

import { describe, it, expect } from 'vitest';
import { FALLBACK, parseRgbToHex, parseRgbTriple, theme } from './theme';

const NON_STATUS_KEYS = [
  'void',
  'brass',
  'starlight',
  'ember',
  'cyan',
  'amber',
  'green',
  'crimson',
  'indigo',
  'auditor',
  'ghostBrass',
  'cacheTeal',
  'horizonRose',
  'frost',
  'haiku',
  'sonnet',
  'opus',
  'hairline',
] as const;

const STATUS_KEYS = ['run', 'ok', 'warn', 'err', 'idle'] as const;
const EM_KEYS = ['hi', 'mid', 'low', 'faint'] as const;

function isValid24BitInt(n: unknown): boolean {
  return typeof n === 'number' && Number.isInteger(n) && n >= 0 && n <= 0xffffff;
}

describe('FALLBACK table', () => {
  it('covers every exported color key', () => {
    for (const key of NON_STATUS_KEYS) {
      expect(FALLBACK).toHaveProperty(key);
    }
    for (const key of STATUS_KEYS) {
      expect(FALLBACK.status).toHaveProperty(key);
      expect(FALLBACK.status[key]).toHaveProperty('core');
      expect(FALLBACK.status[key]).toHaveProperty('base');
    }
    for (const key of EM_KEYS) {
      expect(FALLBACK.em).toHaveProperty(key);
    }
  });

  it('every non-status value is a valid 24-bit int', () => {
    for (const key of NON_STATUS_KEYS) {
      expect(isValid24BitInt(FALLBACK[key]), `${key} = ${FALLBACK[key]}`).toBe(true);
    }
  });

  it('every status core/base value is a valid 24-bit int', () => {
    for (const key of STATUS_KEYS) {
      const pair = FALLBACK.status[key];
      expect(isValid24BitInt(pair.core), `${key}.core = ${pair.core}`).toBe(true);
      expect(isValid24BitInt(pair.base), `${key}.base = ${pair.base}`).toBe(true);
    }
  });

  it('every em-tier value is a valid 24-bit int (M4.1 text-emphasis tiers)', () => {
    expect(FALLBACK.em).toBeDefined();
    for (const key of EM_KEYS) {
      const v = FALLBACK.em?.[key];
      expect(isValid24BitInt(v), `em.${key} = ${v}`).toBe(true);
    }
  });

  it('run and ok status resolve identically post-M4 (grayscale; distinguished by glyph, not hue)', () => {
    expect(FALLBACK.status.run.core).toBe(FALLBACK.status.ok.core);
    expect(FALLBACK.status.run.base).toBe(FALLBACK.status.ok.base);
    expect(FALLBACK.status.run.core).toBe(FALLBACK.em?.hi);
  });

  it('theme() returns FALLBACK before initTheme() has resolved anything', () => {
    // no DOM resolution has happened in this test file — theme() must not throw or return
    // a partial/undefined table.
    const t = theme();
    for (const key of NON_STATUS_KEYS) {
      expect(t[key]).toBe(FALLBACK[key]);
    }
  });
});

describe('parseRgbTriple / parseRgbToHex', () => {
  it('parses "rgb(r, g, b)"', () => {
    expect(parseRgbTriple('rgb(70, 224, 255)')).toEqual([70, 224, 255]);
    expect(parseRgbToHex('rgb(70, 224, 255)')).toBe(0x46e0ff);
  });

  it('parses "rgba(r, g, b, a)"', () => {
    expect(parseRgbTriple('rgba(234, 240, 255, 0.08)')).toEqual([234, 240, 255]);
    expect(parseRgbToHex('rgba(234, 240, 255, 0.08)')).toBe(0xeaf0ff);
  });

  it('parses without a space after commas', () => {
    expect(parseRgbTriple('rgb(1,2,3)')).toEqual([1, 2, 3]);
  });

  it('clamps out-of-range / fractional channel values', () => {
    expect(parseRgbTriple('rgb(255.6, -3, 999)')).toEqual([255, 0, 255]);
  });

  it('returns null for an unparseable string', () => {
    expect(parseRgbTriple('oklch(0.7 0.1 220)')).toBeNull();
    expect(parseRgbToHex('transparent')).toBeNull();
  });
});
