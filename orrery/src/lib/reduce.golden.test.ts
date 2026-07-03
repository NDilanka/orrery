// @ts-nocheck — node-context vitest test (run via `npm run test:unit`); uses node:fs/path,
// so it is intentionally excluded from the browser-oriented svelte-check typecheck.
// Cross-language reducer parity (TS side). Asserts the TypeScript `reduce()` produces
// the SAME committed RunState goldens as the Rust reducer (src-tauri/tests/golden/*.json,
// regenerated + asserted by src-tauri/tests/golden_parity.rs). If TS and Rust drift,
// this fails. Floats compared with a small epsilon (independent serialization paths).
import { describe, it, expect } from 'vitest';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { reduce } from './reduce';
import type { Checkpoint, RawEvent } from './types';

const FX = resolve(__dirname, '../../static/fixtures');
const GOLD = resolve(__dirname, '../../src-tauri/tests/golden');

function lines(name: string): RawEvent[] {
  const text: string = readFileSync(resolve(FX, name), 'utf8');
  return text
    .split('\n')
    .filter((l: string) => l.trim().length > 0)
    .map((l: string) => JSON.parse(l) as RawEvent);
}
function gold(name: string): unknown {
  const text: string = readFileSync(resolve(GOLD, name), 'utf8');
  return JSON.parse(text);
}

// deep diff with float tolerance; returns the list of differing paths ([] == identical)
function diff(a: unknown, b: unknown, path = ''): string[] {
  if (a === b) return [];
  if (typeof a === 'number' && typeof b === 'number' && Math.abs(a - b) < 1e-9) return [];
  if (a && b && typeof a === 'object' && typeof b === 'object') {
    const out: string[] = [];
    const keys = new Set([...Object.keys(a), ...Object.keys(b)]);
    for (const k of keys) {
      out.push(...diff((a as Record<string, unknown>)[k], (b as Record<string, unknown>)[k], path ? `${path}.${k}` : k));
    }
    return out;
  }
  return [`${path}: TS=${JSON.stringify(a)} RUST=${JSON.stringify(b)}`];
}

// [loopId, fixtureFile, goldenFile, checkpointFile?]
const cases: [string, string, string, string?][] = [
  ['demo', 'demo-events.jsonl', 'demo.bmad.json', undefined],
  ['bmad', 'bmad-log.jsonl', 'bmad.bmad.json', 'checkpoint.json'],
  ['roman', 'roman-log.jsonl', 'roman.generic.json', undefined],
  ['calc', 'calc-log.jsonl', 'calc.generic.json', undefined],
  ['multirun', 'multirun-log.jsonl', 'multirun.generic.json', undefined],
  ['collision', 'series-collision-log.jsonl', 'series-collision.generic.json', undefined],
  ['metrics', 'metrics-log.jsonl', 'metrics.generic.json', undefined],
  ['engine-polish', 'engine-polish-log.jsonl', 'engine-polish.bmad.json', undefined],
  ['failed-dark', 'failed-dark-log.jsonl', 'failed-dark.bmad.json', undefined],
];

describe('reducer parity (TS == committed Rust golden)', () => {
  for (const [loopId, fixture, golden, cp] of cases) {
    it(golden, () => {
      const evs = lines(fixture);
      const checkpoint = cp ? (JSON.parse(readFileSync(resolve(FX, cp), 'utf8')) as Checkpoint) : undefined;
      const ts = reduce(evs, { loopId, checkpoint });
      const d = diff(ts, gold(golden));
      expect(d, `TS reduce() drifted from ${golden}:\n${d.join('\n')}`).toEqual([]);
    });
  }
});
