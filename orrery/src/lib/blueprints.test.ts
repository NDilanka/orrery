// Unit tests for blueprints.ts (wave U3 Task 1 — "honest dials").
//
// The central claim under test: composeEngine/composeLoopDef emit ONLY keys
// engine/loop/config.py actually reads. Before U3 this module also emitted
// `gate.greenWhen` (retired — parsed, never consulted) and whole
// `regression`/`decide`/`qa`/`quota`/`concurrency` blocks the engine's
// `_ENGINE_KNOWN_KEYS` doesn't recognize at all — every one of those would
// print an "unrecognized key" stderr warning today (loop.configkeys.warn_unknown_keys)
// and be silently ignored. `assertEngineIsWarningFree` below is a standing guard
// against that regression.

import { describe, it, expect } from 'vitest';
import {
  BLUEPRINTS,
  BLUEPRINT_ORDER,
  composeEngine,
  composeLoopDef,
  deriveFromDials,
  validateDraft,
  type DialState,
  type EngineConfig,
} from './blueprints';

// Mirrors engine/loop/config.py: `_ENGINE_KNOWN_KEYS` + each block's own
// `_*_KNOWN_KEYS` / dataclass fields (camelCase only — this module never emits
// snake_case, though the engine accepts both).
const KNOWN_ENGINE_KEYS = new Set([
  'task',
  'models',
  'maxTurns',
  'iterTimeoutMin',
  'allowedTools',
  'permissionMode',
  'gate',
  'cost',
  'stop',
  'verify',
  'feedback',
  'memory',
  'metrics',
]);
const KNOWN_MODELS_KEYS = new Set(['discover', 'execute', 'judge', 'hard']);
const KNOWN_GATE_KEYS = new Set(['stages', 'lockGlobs']);
const KNOWN_STAGE_KEYS = new Set(['name', 'command', 'passPattern', 'failPattern', 'heldOut', 'lockGlobs']);
const KNOWN_COST_KEYS = new Set(['ceilingUsd', 'alertPct']);
const KNOWN_STOP_KEYS = new Set([
  'maxIters',
  'stagnationLimit',
  'plateauLimit',
  'regressLimit',
  'gracefulAtPhase',
]);
const KNOWN_VERIFY_KEYS = new Set(['judgeModel', 'contract', 'enabled', 'mutationAudit', 'mutationEvery']);
const KNOWN_FEEDBACK_KEYS = new Set(['compact']);
const KNOWN_MEMORY_KEYS = new Set(['enabled', 'path', 'recallLimit']);
const KNOWN_METRICS_KEYS = new Set(['emit']);

function assertKnownKeys(obj: Record<string, unknown>, known: Set<string>, label: string) {
  for (const k of Object.keys(obj)) {
    expect(known.has(k), `${label}.${k} is not a key engine/loop/config.py reads`).toBe(true);
  }
}

/** Walk a composed EngineConfig and assert every nested key is engine-consumed. */
function assertEngineIsWarningFree(engine: EngineConfig) {
  const e = engine as unknown as Record<string, unknown>;
  assertKnownKeys(e, KNOWN_ENGINE_KEYS, 'engine');
  assertKnownKeys(engine.models as unknown as Record<string, unknown>, KNOWN_MODELS_KEYS, 'engine.models');
  assertKnownKeys(engine.gate as unknown as Record<string, unknown>, KNOWN_GATE_KEYS, 'engine.gate');
  for (const stage of engine.gate.stages) {
    assertKnownKeys(stage as unknown as Record<string, unknown>, KNOWN_STAGE_KEYS, 'engine.gate.stages[]');
  }
  assertKnownKeys(engine.cost as unknown as Record<string, unknown>, KNOWN_COST_KEYS, 'engine.cost');
  assertKnownKeys(engine.stop as unknown as Record<string, unknown>, KNOWN_STOP_KEYS, 'engine.stop');
  assertKnownKeys(engine.verify as unknown as Record<string, unknown>, KNOWN_VERIFY_KEYS, 'engine.verify');
  assertKnownKeys(engine.feedback as unknown as Record<string, unknown>, KNOWN_FEEDBACK_KEYS, 'engine.feedback');
  assertKnownKeys(engine.memory as unknown as Record<string, unknown>, KNOWN_MEMORY_KEYS, 'engine.memory');
  assertKnownKeys(engine.metrics as unknown as Record<string, unknown>, KNOWN_METRICS_KEYS, 'engine.metrics');
}

describe('composeEngine — every emitted key is one the engine actually consumes', () => {
  for (const id of BLUEPRINT_ORDER) {
    it(`blueprint "${id}" is warning-free at every dial extreme`, () => {
      const bp = BLUEPRINTS[id];
      const extremes: DialState[] = [
        { ambition: 0, patience: 0, autonomy: 0 },
        { ambition: 1, patience: 1, autonomy: 1 },
        bp.dials,
      ];
      for (const dials of extremes) {
        assertEngineIsWarningFree(composeEngine(bp, dials, bp.destination, 'TASK.md'));
      }
    });
  }

  it('a drawer-override-merged loop.json is still warning-free', () => {
    const bp = BLUEPRINTS.grind;
    const def = composeLoopDef({
      id: 'x',
      name: 'x',
      blueprint: bp,
      dials: bp.dials,
      destination: bp.destination,
      stateDir: '.loop',
      task: 'TASK.md',
      engineOverrides: {
        cost: { ceilingUsd: 9, alertPct: [90] },
        feedback: { compact: true },
        memory: { enabled: true, path: null, recallLimit: 3 },
        metrics: { emit: true },
      },
    });
    assertEngineIsWarningFree(def.engine);
  });
});

describe('verify.enabled — the AC-driven anti-false-green pass (foot-gun catch)', () => {
  it('is OFF when no acceptance criteria are written', () => {
    const bp = BLUEPRINTS.custom;
    const engine = composeEngine(
      bp,
      bp.dials,
      { acceptanceCriteria: ['', '   '], gateStages: bp.destination.gateStages },
      'TASK.md',
    );
    expect(engine.verify.enabled).toBe(false);
  });

  it('turns ON automatically once a criterion is typed — contract alone would otherwise silently do nothing', () => {
    const bp = BLUEPRINTS.custom;
    const engine = composeEngine(
      bp,
      bp.dials,
      { acceptanceCriteria: ['', 'the button turns green'], gateStages: bp.destination.gateStages },
      'TASK.md',
    );
    expect(engine.verify.enabled).toBe(true);
    expect(engine.verify.contract).toEqual(['', 'the button turns green']);
  });
});

describe('deriveFromDials — each dial moves a real, monotonic, engine-consumed bundle', () => {
  it('ambition: thrift end is cheap+short+cool, ambition end is pricier+longer+hot', () => {
    const thrift = deriveFromDials({ ambition: 0, patience: 0.5, autonomy: 0.5 });
    const ambition = deriveFromDials({ ambition: 1, patience: 0.5, autonomy: 0.5 });
    expect(thrift.cost.ceilingUsd).toBeLessThan(ambition.cost.ceilingUsd);
    expect(thrift.stop.maxIters).toBeLessThan(ambition.stop.maxIters);
    expect(thrift.models.execute).toBe('haiku');
    expect(ambition.models.execute).toBe('opus');
  });

  it('patience: the fussy end audits every green, the patient end turns the audit off', () => {
    const fussy = deriveFromDials({ ambition: 0.5, patience: 0, autonomy: 0.5 });
    const patient = deriveFromDials({ ambition: 0.5, patience: 1, autonomy: 0.5 });
    expect(fussy.verify.mutationAudit).toBe(true);
    expect(fussy.verify.mutationEvery).toBe(1);
    expect(patient.verify.mutationAudit).toBe(false);
    expect(fussy.stop.plateauLimit).toBeLessThan(patient.stop.plateauLimit);
    expect(fussy.stop.regressLimit).toBeLessThan(patient.stop.regressLimit);
    expect(fussy.stop.stagnationLimit).toBeLessThan(patient.stop.stagnationLimit);
  });

  it('autonomy: company end is tight+guarded+verbose, autonomy end is loose+unattended+compact', () => {
    const company = deriveFromDials({ ambition: 0.5, patience: 0.5, autonomy: 0 });
    const autonomy = deriveFromDials({ ambition: 0.5, patience: 0.5, autonomy: 1 });
    expect(company.iterTimeoutMin).toBeLessThan(autonomy.iterTimeoutMin);
    expect(company.maxTurns).toBeLessThan(autonomy.maxTurns);
    expect(company.feedback.compact).toBe(false);
    expect(autonomy.feedback.compact).toBe(true);
    expect(company.permissionMode).toBe('acceptEdits');
    expect(autonomy.permissionMode).toBe('bypassPermissions');
  });
});

describe('composeLoopDef', () => {
  it('produces the exact top-level shape create_loop expects, engine included', () => {
    const bp = BLUEPRINTS.sprint;
    const def = composeLoopDef({
      id: 'my-loop',
      name: 'My loop',
      blueprint: bp,
      dials: bp.dials,
      destination: bp.destination,
      stateDir: '.loop',
      task: 'TASK.md',
    });
    expect(def.id).toBe('my-loop');
    expect(def.kind).toBe('generic');
    expect(def.adapter).toBe('generic');
    expect(def.start.program).toBe('loop');
    assertEngineIsWarningFree(def.engine);
  });
});

describe('validateDraft', () => {
  it('still requires an id, a name, one gate stage and one acceptance criterion', () => {
    const bad = validateDraft({ id: '', name: '', acceptanceCriteria: [], gateStages: [] }, []);
    expect(bad.ok).toBe(false);
    expect(bad.errors.length).toBeGreaterThan(0);

    const good = validateDraft(
      {
        id: 'my-loop',
        name: 'My loop',
        acceptanceCriteria: ['tests pass'],
        gateStages: [{ name: 'test', command: 'bun test' }],
      },
      ['existing-loop'],
    );
    expect(good.ok).toBe(true);
  });
});
