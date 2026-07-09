// Unit tests for externalRecipes.ts — the "honest external adapters" guard.
//
// Mirrors the honesty test in blueprints.test.ts, but for the two EXTERNAL
// adapter blocks. The central claim: composeBmadLoopDef/composeQaLoopDef emit
// ONLY keys the driver actually reads — `_BMAD_KNOWN_KEYS` in
// engine/orrery_loop/bmad/driver.py and `_QA_KNOWN_KEYS` in
// engine/orrery_loop/qa/discover.py. Anything else earns a stderr
// "unrecognized key" warning and is silently ignored, so a stray emitted key is
// a lie the user never sees. The known-key sets below are a standing mirror of
// those driver-side sets.

import { describe, it, expect } from 'vitest';
import {
  BMAD_PROFILES,
  composeBmadLoopDef,
  composeQaLoopDef,
  validateExternalDraft,
  type BmadRecipeInput,
  type QaRecipeInput,
} from './externalRecipes';

// ── Mirror of _BMAD_KNOWN_KEYS (engine/orrery_loop/bmad/driver.py) ──
const KNOWN_BMAD_KEYS = new Set([
  'models',
  'effort',
  'reviewMode',
  'smokeMode',
  'retroMode',
  'maxStories',
  'gateStages',
  'devServerArgv',
  'createTimeoutMin',
  'devTimeoutMin',
  'reviewTimeoutMin',
  'smokeTimeoutMin',
  'retroTimeoutMin',
  'deciderTimeoutMin',
  'verify',
  'testIntegrity',
  'planGate',
  'mergeBase',
  'noSmoke',
  'noMerge',
  'noRetro',
  'autoRollback',
  'dryRun',
  'epicOnly',
  'story',
  'maxReviewTurns',
  'maxSmokeIters',
  'maxRetroTurns',
  'defaultQuotaWaitMin',
  'maxQuotaWaits',
  'mergeWaitSec',
  'gateFlakyRetries',
  'gateFlakyMaxFail',
  'metricsEmit',
  'gateFailFast',
  'fallbackModel',
  'structuredVerdicts',
  'projectRoot',
]);
const KNOWN_BMAD_MODELS = new Set(['create', 'dev', 'review', 'smoke', 'retro', 'decider']);
const KNOWN_BMAD_EFFORT = KNOWN_BMAD_MODELS;
const KNOWN_VERIFY = new Set(['enabled', 'model', 'effort', 'timeoutMin']);
const KNOWN_TESTINTEGRITY = new Set(['enabled', 'globs', 'haltOnDeletion']);
const KNOWN_PLANGATE = new Set(['enabled']);

// ── Mirror of _QA_KNOWN_KEYS (engine/orrery_loop/qa/discover.py) ──
const KNOWN_QA_KEYS = new Set([
  'projectRoot',
  'manifest',
  'manifestPath',
  'baseUrl',
  'app',
  'specDir',
  'storageState',
  'seedSummary',
  'model',
  'effort',
  'fallbackModel',
  'maxTurns',
  'timeoutSec',
  'costCeilingUsd',
  'epics',
  'headless',
  'caps',
]);

function assertKnownKeys(obj: Record<string, unknown>, known: Set<string>, label: string) {
  for (const k of Object.keys(obj)) {
    expect(known.has(k), `${label}.${k} is not a key the driver reads`).toBe(true);
  }
}

function assertBmadWarningFree(bmad: Record<string, unknown>) {
  assertKnownKeys(bmad, KNOWN_BMAD_KEYS, 'bmad');
  assertKnownKeys(bmad.models as Record<string, unknown>, KNOWN_BMAD_MODELS, 'bmad.models');
  assertKnownKeys(bmad.effort as Record<string, unknown>, KNOWN_BMAD_EFFORT, 'bmad.effort');
  if (bmad.verify) assertKnownKeys(bmad.verify as Record<string, unknown>, KNOWN_VERIFY, 'bmad.verify');
  if (bmad.testIntegrity)
    assertKnownKeys(bmad.testIntegrity as Record<string, unknown>, KNOWN_TESTINTEGRITY, 'bmad.testIntegrity');
  if (bmad.planGate) assertKnownKeys(bmad.planGate as Record<string, unknown>, KNOWN_PLANGATE, 'bmad.planGate');
}

// ── builders ──
function bmadInput(
  profile: keyof typeof BMAD_PROFILES,
  extra: Partial<BmadRecipeInput> = {},
): BmadRecipeInput {
  const p = BMAD_PROFILES[profile];
  return {
    id: 'bmad-x',
    name: 'BMAD X',
    targetRepo: 'C:/dev/app',
    mergeBase: 'develop',
    noSmoke: false,
    models: p.models,
    effort: p.effort,
    reviewMode: p.reviewMode,
    smokeMode: p.smokeMode,
    retroMode: p.retroMode,
    ...extra,
  };
}

const ADVANCED_BMAD: Partial<BmadRecipeInput> = {
  maxStories: 5,
  gateStages: [{ name: 'test', command: 'bun test', passPattern: '(\\d+)\\s+pass' }],
  devServerArgv: ['bun', 'run', 'dev'],
  createTimeoutMin: 30,
  devTimeoutMin: 45,
  reviewTimeoutMin: 20,
  smokeTimeoutMin: 15,
  retroTimeoutMin: 10,
  deciderTimeoutMin: 5,
  verifyEnabled: false,
  planGateEnabled: false,
  testIntegrityEnabled: false,
  noMerge: true,
  noRetro: true,
  noVerify: true,
  noPlanGate: true,
  autoRollback: true,
};

function qaInput(extra: Partial<QaRecipeInput> = {}): QaRecipeInput {
  return {
    id: 'qa-x',
    name: 'QA X',
    targetRepo: 'C:/dev/webapp',
    manifest: 'ac-manifest.json',
    ...extra,
  };
}

const ADVANCED_QA: Partial<QaRecipeInput> = {
  baseUrl: 'http://localhost:4000',
  app: 'todo',
  storageState: 'C:/dev/webapp/.auth/state.json',
  seedSummary: '2 lists, 3 todos.',
  specDir: 'e2e/functional',
  model: 'sonnet',
  effort: 'high',
  fallbackModel: 'opus',
  maxTurns: 120,
  timeoutSec: 1800,
  costCeilingUsd: 30,
  epics: [1, 2, 3],
  headless: true,
};

// ── honesty: every emitted key is one the driver consumes ──
describe('composeBmadLoopDef — every emitted bmad key is one the driver reads', () => {
  for (const profile of ['maxPower', 'costAware'] as const) {
    it(`profile "${profile}" (baseline) is warning-free`, () => {
      const def = composeBmadLoopDef(bmadInput(profile));
      expect(def.bmad).toBeDefined();
      assertBmadWarningFree(def.bmad!);
    });
  }

  it('is warning-free with every advanced field set (gateStages, timeouts, toggles off)', () => {
    const def = composeBmadLoopDef(bmadInput('maxPower', ADVANCED_BMAD));
    assertBmadWarningFree(def.bmad!);
    // the OFF switches emit their block…
    expect(def.bmad!.verify).toEqual({ enabled: false });
    expect(def.bmad!.planGate).toEqual({ enabled: false });
    expect(def.bmad!.testIntegrity).toEqual({ enabled: false });
    // …and the advanced keys round-trip
    expect(def.bmad!.maxStories).toBe(5);
    expect(def.bmad!.createTimeoutMin).toBe(30);
    expect(def.bmad!.gateStages).toEqual(ADVANCED_BMAD.gateStages);
  });

  it('emits NO verify/planGate/testIntegrity block while those gates stay ON (default)', () => {
    const def = composeBmadLoopDef(
      bmadInput('maxPower', { verifyEnabled: true, planGateEnabled: true, testIntegrityEnabled: true }),
    );
    expect('verify' in def.bmad!).toBe(false);
    expect('planGate' in def.bmad!).toBe(false);
    expect('testIntegrity' in def.bmad!).toBe(false);
  });
});

describe('composeQaLoopDef — every emitted qa key is one the driver reads', () => {
  it('baseline (defaults only) is warning-free', () => {
    const def = composeQaLoopDef(qaInput());
    expect(def.qa).toBeDefined();
    assertKnownKeys(def.qa!, KNOWN_QA_KEYS, 'qa');
    expect(def.qa!.app).toBe('app');
    expect(def.qa!.baseUrl).toBe('http://localhost:3000');
  });

  it('is warning-free with every advanced field set', () => {
    const def = composeQaLoopDef(qaInput(ADVANCED_QA));
    assertKnownKeys(def.qa!, KNOWN_QA_KEYS, 'qa');
    expect(def.qa!.epics).toEqual([1, 2, 3]);
    expect(def.qa!.headless).toBe(true);
    expect(def.qa!.storageState).toBe('C:/dev/webapp/.auth/state.json');
  });

  it('omits storageState/seedSummary when empty', () => {
    const def = composeQaLoopDef(qaInput({ storageState: '   ', seedSummary: '' }));
    expect('storageState' in def.qa!).toBe(false);
    expect('seedSummary' in def.qa!).toBe(false);
  });
});

// ── shape ──
describe('composeBmadLoopDef — shape', () => {
  it('is an external/bmad loop launched by loop-bmad', () => {
    const def = composeBmadLoopDef(bmadInput('maxPower'));
    expect(def.kind).toBe('external');
    expect(def.adapter).toBe('bmad');
    expect(def.start.program).toBe('loop-bmad');
    expect(def.start.args).toContain('--project-root');
    expect(def.start.args).toContain('--merge-base');
    expect(def.start.args).toContain('--loop-json');
    expect(def.start.args).toContain('--state-dir');
    expect(def.theme).toBe('plasma');
    expect(def.stopFlag).toBe('.loop/STOP');
    expect(def.checkpoint).toBe('.loop/checkpoint.json');
    expect(def.logFile).toBe('log.jsonl');
  });

  it('--no-smoke is present iff noSmoke is set', () => {
    expect(composeBmadLoopDef(bmadInput('maxPower', { noSmoke: true })).start.args).toContain('--no-smoke');
    expect(composeBmadLoopDef(bmadInput('maxPower', { noSmoke: false })).start.args).not.toContain('--no-smoke');
  });

  it('falls back to merge-base "develop" when blank', () => {
    const def = composeBmadLoopDef(bmadInput('maxPower', { mergeBase: '   ' }));
    const i = def.start.args.indexOf('--merge-base');
    expect(def.start.args[i + 1]).toBe('develop');
  });
});

describe('composeQaLoopDef — shape', () => {
  it('is an external/generic loop launched by loop-qa, WITHOUT --cwd', () => {
    const def = composeQaLoopDef(qaInput());
    expect(def.kind).toBe('external');
    expect(def.adapter).toBe('generic');
    expect(def.start.program).toBe('loop-qa');
    expect(def.start.args).toContain('--project-root');
    expect(def.start.args).toContain('--manifest');
    expect(def.start.args).not.toContain('--cwd'); // loop-qa rejects --cwd
    expect(def.theme).toBe('aurora');
    expect(def.stopFlag).toBe('.loop/STOP');
    expect(def.checkpoint).toBe('.loop/checkpoint.json');
  });
});

// ── profile round-trip: maxPower deep-equals the seed values ──
describe('BMAD_PROFILES.maxPower — mirrors the seed loops/bmad/loop.json exactly', () => {
  it('composes the seed models/effort/modes verbatim', () => {
    const def = composeBmadLoopDef(bmadInput('maxPower'));
    expect(def.bmad!.models).toEqual({
      create: 'claude-opus-4-8[1m]',
      dev: 'claude-opus-4-8[1m]',
      review: 'claude-opus-4-8[1m]',
      smoke: 'claude-opus-4-8[1m]',
      retro: 'claude-opus-4-8[1m]',
      decider: 'sonnet',
    });
    expect(def.bmad!.effort).toEqual({
      create: 'xhigh',
      dev: 'xhigh',
      review: 'xhigh',
      smoke: 'xhigh',
      retro: 'xhigh',
      decider: 'low',
    });
    expect(def.bmad!.reviewMode).toBe('single-pass');
    expect(def.bmad!.smokeMode).toBe('single-pass');
    expect(def.bmad!.retroMode).toBe('single-pass');
  });

  it('costAware mirrors the engine defaults (sonnet phases, empty effort, haiku decider)', () => {
    const def = composeBmadLoopDef(bmadInput('costAware'));
    expect(def.bmad!.models).toEqual({
      create: 'sonnet',
      dev: '',
      review: 'sonnet',
      smoke: 'sonnet',
      retro: 'sonnet',
      decider: 'haiku',
    });
    expect(def.bmad!.effort).toEqual({ create: '', dev: '', review: '', smoke: '', retro: '', decider: '' });
    expect(def.bmad!.reviewMode).toBe('qa');
    expect(def.bmad!.smokeMode).toBe('iterative');
    expect(def.bmad!.retroMode).toBe('qa');
  });
});

// ── validation ──
describe('validateExternalDraft', () => {
  it('flags a missing id, name and target repo', () => {
    const r = validateExternalDraft(
      { id: '', name: '', targetRepo: '', mergeBase: 'develop' },
      'bmad',
      [],
    );
    expect(r.ok).toBe(false);
    expect(r.errors.length).toBeGreaterThanOrEqual(3);
  });

  it('rejects an unsafe or duplicate id', () => {
    expect(
      validateExternalDraft({ id: 'bad id!', name: 'n', targetRepo: 'C:/x', mergeBase: 'develop' }, 'bmad', []).ok,
    ).toBe(false);
    expect(
      validateExternalDraft({ id: 'dupe', name: 'n', targetRepo: 'C:/x', mergeBase: 'develop' }, 'bmad', ['dupe']).ok,
    ).toBe(false);
  });

  it('rejects a relative repo path with an "absolute" error', () => {
    const r = validateExternalDraft(
      { id: 'a', name: 'n', targetRepo: './x', mergeBase: 'develop' },
      'bmad',
      [],
    );
    expect(r.ok).toBe(false);
    expect(r.errors.some((e) => /absolute/i.test(e))).toBe(true);
  });

  it('accepts both a Windows drive path and a POSIX root path', () => {
    expect(
      validateExternalDraft({ id: 'a', name: 'n', targetRepo: 'C:/dev/app', mergeBase: 'develop' }, 'bmad', []).ok,
    ).toBe(true);
    expect(
      validateExternalDraft({ id: 'a', name: 'n', targetRepo: '/home/x', mergeBase: 'develop' }, 'bmad', []).ok,
    ).toBe(true);
    // backslash Windows path too
    expect(
      validateExternalDraft({ id: 'a', name: 'n', targetRepo: 'C:\\dev\\app', mergeBase: 'develop' }, 'bmad', []).ok,
    ).toBe(true);
  });

  it('bmad requires a merge base', () => {
    const r = validateExternalDraft({ id: 'a', name: 'n', targetRepo: 'C:/x', mergeBase: '' }, 'bmad', []);
    expect(r.ok).toBe(false);
    expect(r.errors.some((e) => /merge base/i.test(e))).toBe(true);
  });

  it('qa requires a manifest', () => {
    const r = validateExternalDraft({ id: 'a', name: 'n', targetRepo: 'C:/x', manifest: '' }, 'qa', []);
    expect(r.ok).toBe(false);
    expect(r.errors.some((e) => /manifest/i.test(e))).toBe(true);
    // …and passes once one is supplied
    expect(
      validateExternalDraft({ id: 'a', name: 'n', targetRepo: 'C:/x', manifest: 'ac.json' }, 'qa', []).ok,
    ).toBe(true);
  });
});
