// blueprints.ts — the brains behind the Tuning Console (plan §4).
//
// You don't fill a form; you calibrate an instrument. A loop is authored from:
//   BLUEPRINT  — a preset star-chart with smart defaults for the full engine
//                config (PROTOCOL §7 engine block).
//   3 DIALS    — three coordinated forces, each a bundle that sets several
//                engine params at once (Ambition⟷Thrift · Patience⟷Fussiness ·
//                Autonomy⟷Company).
//   DESTINATION— the human-authored heart: acceptance criteria (the definition
//                of done) and ordered gate stages (the test gate). Can't be a preset.
//   DRAWERS    — advanced overrides; any change is an Amber override-dot.
//
// This module is pure + framework-free so it can be unit-checked and reused by
// the live preview. `composeLoopDef()` turns all of the above into the exact
// camelCase loop.json the Rust `create_loop` persists and `list_loops` reads.

import type { Model } from './types';

// ─── Engine config shape (PROTOCOL §7 engine block) ─────────────────────────
// Kept structural & permissive: this is the object that lands under loop.json's
// `engine` key. The Rust side treats it as opaque Value; the console owns it.

export interface GateStageDef {
  name: string;
  command: string;
  passPattern?: string;
  failPattern?: string;
}

export interface EngineConfig {
  task: string;
  models: { discover: Model; execute: Model; judge: Model; hard: Model };
  maxTurns: number;
  allowedTools: string[];
  permissionMode: 'acceptEdits' | 'plan' | 'default' | 'bypassPermissions';
  gate: {
    stages: GateStageDef[];
    greenWhen: string;
    lockGlobs: string[];
  };
  cost: { ceilingUsd: number; alertPct: number[] };
  stop: {
    maxIters: number;
    stagnationLimit: number;
    plateauLimit: number;
    regressLimit: number;
    gracefulAtPhase: boolean;
  };
  regression: { floor: number; autoRollback: boolean; strikeBudget: number };
  verify: { judgeModel: Model; contract: string[]; strictness: 'lenient' | 'normal' | 'strict' };
  decide: { plateauK: number; consecutiveFail: number; totalFail: number; recoverOnce: boolean };
  quota: { enabled: boolean; maxWaits: number; defaultWaitMin: number; manualContinue: boolean };
  qa: { maxTurns: number; deciderModel: Model; humanInLoop: boolean };
  concurrency: { guard: boolean };
}

export interface LoopDefDraft {
  id: string;
  name: string;
  theme: string;
  kind: 'generic';
  adapter: 'generic';
  stateDir: string;
  logFile: string;
  stopFlag?: string;
  checkpoint?: string;
  start: { program: string; args: string[] };
  engine: EngineConfig;
}

// ─── Blueprints (preset star-charts) ────────────────────────────────────────

export type BlueprintId = 'grind' | 'sprint' | 'explore' | 'custom';

export interface Blueprint {
  id: BlueprintId;
  name: string;
  glyph: string;
  theme: string;
  tagline: string;
  // where the three dials sit by default (0..1 along each axis)
  dials: DialState;
  // the engine knobs the dials DON'T own (everything else §2). Partial — anything
  // omitted falls back to a sane default in composeEngine.
  base: Partial<EngineConfig>;
  // default destination (the user is expected to edit it — this is a seed)
  destination: { acceptanceCriteria: string[]; gateStages: GateStageDef[] };
}

// ─── The three dials ────────────────────────────────────────────────────────
// Each dial is a 0..1 position. 0 = the left pole, 1 = the right pole. Each
// drives a coordinated BUNDLE of engine params (plan §4). `deriveFromDials`
// resolves the three positions into the concrete engine fields they own.

export interface DialState {
  ambition: number; // 0 = Thrift (cheap/Haiku/low ceiling) … 1 = Ambition (Opus/high ceiling)
  patience: number; // 0 = Fussy (strict, tiny budgets) … 1 = Patient (lenient, big budgets)
  autonomy: number; // 0 = Company (human-in-loop) … 1 = Autonomy (overnight-auto)
}

// the engine fields the dials are authoritative for
interface DialDerived {
  models: EngineConfig['models'];
  cost: EngineConfig['cost'];
  stop: EngineConfig['stop'];
  regression: EngineConfig['regression'];
  verify: EngineConfig['verify'];
  decide: EngineConfig['decide'];
  qa: EngineConfig['qa'];
  permissionMode: EngineConfig['permissionMode'];
}

function lerp(a: number, b: number, t: number): number {
  return a + (b - a) * t;
}
function lerpInt(a: number, b: number, t: number): number {
  return Math.round(lerp(a, b, t));
}
function round2(n: number): number {
  return Math.round(n * 100) / 100;
}

/** Pick a model tier from a heat scalar (0 cold → 1 hot). */
function modelFromHeat(t: number): Model {
  if (t < 0.34) return 'haiku';
  if (t < 0.72) return 'sonnet';
  return 'opus';
}

/**
 * Resolve the 3 dials into the concrete engine fields they coordinate.
 * This is the single source of truth for "what does this dial DO" — the live
 * preview reads the same derivation, so the preview never lies.
 */
export function deriveFromDials(d: DialState): DialDerived {
  const { ambition, patience, autonomy } = d;

  // AMBITION ⟷ THRIFT — model tiering + cost ceiling + max-iters as one bundle.
  // High ambition = hotter models, taller ceiling, more iterations.
  const models: EngineConfig['models'] = {
    discover: 'haiku', // discovery stays cheap regardless
    judge: 'haiku', // the judge is deliberately cheap (fresh-context verifier)
    execute: modelFromHeat(lerp(0.3, 0.85, ambition)), // sonnet → opus as ambition climbs
    hard: modelFromHeat(lerp(0.6, 1.0, ambition)), // sonnet → opus for the hard fallback
  };
  const ceilingUsd = round2(lerp(1.0, 25.0, ambition)); // $1 thrift → $25 ambition
  const maxIters = lerpInt(6, 40, ambition);

  // PATIENCE ⟷ FUSSINESS — plateau K + regression strike budget + consecutive-fail
  // + verifier strictness. Fussy (low) = strict verifier, tiny budgets, quick to
  // give up; Patient (high) = lenient, generous budgets, long plateau tolerance.
  const strictness: EngineConfig['verify']['strictness'] =
    patience < 0.34 ? 'strict' : patience > 0.7 ? 'lenient' : 'normal';
  const plateauK = lerpInt(2, 8, patience);
  const strikeBudget = lerpInt(1, 6, patience);
  const consecutiveFail = lerpInt(2, 6, patience);
  const totalFail = lerpInt(4, 14, patience);
  const stagnationLimit = lerpInt(1, 4, patience);

  // AUTONOMY ⟷ COMPANY — permission/interaction. Company (low) = human-in-loop
  // Q&A, accept-edits stays guarded, stop-sensitive. Autonomy (high) = overnight
  // unattended, decider answers its own questions, fewer stop teeth.
  const humanInLoop = autonomy < 0.45;
  const permissionMode: EngineConfig['permissionMode'] =
    autonomy > 0.8 ? 'bypassPermissions' : 'acceptEdits';
  const qaMaxTurns = lerpInt(2, 8, 1 - Math.abs(autonomy - 0.5) * 2 + 0.5); // most Q&A in the middle
  const recoverOnce = autonomy > 0.5;
  const gracefulAtPhase = autonomy < 0.7; // company stops at phase teeth; high-autonomy runs longer

  return {
    models,
    cost: { ceilingUsd, alertPct: [50, 80, 100] },
    stop: {
      maxIters,
      stagnationLimit,
      plateauLimit: plateauK,
      regressLimit: strikeBudget,
      gracefulAtPhase,
    },
    regression: { floor: 0, autoRollback: true, strikeBudget },
    verify: { judgeModel: 'haiku', contract: [], strictness },
    decide: { plateauK, consecutiveFail, totalFail, recoverOnce },
    qa: { maxTurns: Math.max(2, qaMaxTurns), deciderModel: 'haiku', humanInLoop },
    permissionMode,
  };
}

// ─── Blueprint definitions (smart defaults for the full engine) ─────────────

const COMMON_TOOLS = ['Read', 'Edit', 'Write', 'Bash(bun test)', 'Bash(bun test:*)'];

export const BLUEPRINTS: Record<BlueprintId, Blueprint> = {
  grind: {
    id: 'grind',
    name: 'Grind',
    glyph: '◧',
    theme: 'ember',
    tagline: 'fix-until-green · hash-locked · cheap · aggressive rollback',
    dials: { ambition: 0.28, patience: 0.3, autonomy: 0.72 },
    base: {
      allowedTools: COMMON_TOOLS,
      gate: {
        stages: [
          { name: 'test', command: 'bun test', passPattern: '(\\d+)\\s+pass', failPattern: '(\\d+)\\s+fail' },
        ],
        greenWhen: 'exit==0',
        lockGlobs: ['**/*.test.ts'],
      },
      quota: { enabled: true, maxWaits: 6, defaultWaitMin: 20, manualContinue: false },
      concurrency: { guard: true },
    },
    destination: {
      acceptanceCriteria: ['All tests pass', 'No regressions from the baseline'],
      gateStages: [
        { name: 'test', command: 'bun test', passPattern: '(\\d+)\\s+pass', failPattern: '(\\d+)\\s+fail' },
      ],
    },
  },
  sprint: {
    id: 'sprint',
    name: 'Sprint',
    glyph: '◫',
    theme: 'gold',
    tagline: 'work-queue + PRs + retros — the BMAD pattern',
    dials: { ambition: 0.6, patience: 0.55, autonomy: 0.55 },
    base: {
      allowedTools: [...COMMON_TOOLS, 'Bash(bun lint)', 'Bash(git:*)'],
      gate: {
        stages: [
          { name: 'codegen', command: 'bun run build', passPattern: '', failPattern: 'error' },
          { name: 'lint', command: 'bun lint', passPattern: '', failPattern: '(\\d+)\\s+error' },
          { name: 'test', command: 'bun test', passPattern: '(\\d+)\\s+pass', failPattern: '(\\d+)\\s+fail' },
        ],
        greenWhen: 'exit==0',
        lockGlobs: ['**/*.test.ts', '**/*.spec.ts'],
      },
      quota: { enabled: true, maxWaits: 8, defaultWaitMin: 30, manualContinue: false },
      concurrency: { guard: true },
    },
    destination: {
      acceptanceCriteria: ['Story acceptance criteria met', 'Tests green', 'PR opened to develop'],
      gateStages: [
        { name: 'codegen', command: 'bun run build', passPattern: '', failPattern: 'error' },
        { name: 'lint', command: 'bun lint', passPattern: '', failPattern: '(\\d+)\\s+error' },
        { name: 'test', command: 'bun test', passPattern: '(\\d+)\\s+pass', failPattern: '(\\d+)\\s+fail' },
        { name: 'audit', command: '', passPattern: '', failPattern: '' },
      ],
    },
  },
  explore: {
    id: 'explore',
    name: 'Explore',
    glyph: '◍',
    theme: 'cyan',
    tagline: 'attended breadth · human-in-loop · careful',
    dials: { ambition: 0.78, patience: 0.7, autonomy: 0.2 },
    base: {
      allowedTools: COMMON_TOOLS,
      gate: {
        stages: [
          { name: 'test', command: 'bun test', passPattern: '(\\d+)\\s+pass', failPattern: '(\\d+)\\s+fail' },
        ],
        greenWhen: 'exit==0',
        lockGlobs: [],
      },
      quota: { enabled: true, maxWaits: 3, defaultWaitMin: 15, manualContinue: true },
      concurrency: { guard: true },
    },
    destination: {
      acceptanceCriteria: ['The approach is sound and reviewed', 'Tests cover the new behavior'],
      gateStages: [
        { name: 'test', command: 'bun test', passPattern: '(\\d+)\\s+pass', failPattern: '(\\d+)\\s+fail' },
        { name: 'audit', command: '', passPattern: '', failPattern: '' },
      ],
    },
  },
  custom: {
    id: 'custom',
    name: 'Custom',
    glyph: '◎',
    theme: 'plasma',
    tagline: 'a blank instrument — calibrate everything yourself',
    dials: { ambition: 0.5, patience: 0.5, autonomy: 0.5 },
    base: {
      allowedTools: COMMON_TOOLS,
      gate: {
        stages: [
          { name: 'test', command: 'bun test', passPattern: '(\\d+)\\s+pass', failPattern: '(\\d+)\\s+fail' },
        ],
        greenWhen: 'exit==0',
        lockGlobs: ['**/*.test.ts'],
      },
      quota: { enabled: true, maxWaits: 5, defaultWaitMin: 20, manualContinue: false },
      concurrency: { guard: true },
    },
    destination: {
      acceptanceCriteria: ['Tests green'],
      gateStages: [
        { name: 'test', command: 'bun test', passPattern: '(\\d+)\\s+pass', failPattern: '(\\d+)\\s+fail' },
      ],
    },
  },
};

export const BLUEPRINT_ORDER: BlueprintId[] = ['grind', 'sprint', 'explore', 'custom'];

// ─── Compose the full engine config from blueprint + dials ──────────────────

/** Build the complete EngineConfig a blueprint+dials imply (no drawer overrides yet). */
export function composeEngine(
  blueprint: Blueprint,
  dials: DialState,
  destination: { acceptanceCriteria: string[]; gateStages: GateStageDef[] },
  task: string,
): EngineConfig {
  const derived = deriveFromDials(dials);
  return {
    task,
    models: derived.models,
    // maxTurns is the per-phase Claude turn cap (not dial-driven — the iteration
    // budget the dials own lives in stop.maxIters).
    maxTurns: blueprint.base.maxTurns ?? 30,
    allowedTools: blueprint.base.allowedTools ?? COMMON_TOOLS,
    permissionMode: derived.permissionMode,
    gate: {
      stages: destination.gateStages.length
        ? destination.gateStages
        : blueprint.base.gate?.stages ?? [],
      greenWhen: blueprint.base.gate?.greenWhen ?? 'exit==0',
      lockGlobs: blueprint.base.gate?.lockGlobs ?? [],
    },
    cost: derived.cost,
    stop: derived.stop,
    regression: derived.regression,
    verify: { ...derived.verify, contract: destination.acceptanceCriteria },
    decide: derived.decide,
    quota: blueprint.base.quota ?? {
      enabled: true,
      maxWaits: 5,
      defaultWaitMin: 20,
      manualContinue: false,
    },
    qa: derived.qa,
    concurrency: blueprint.base.concurrency ?? { guard: true },
  };
}

// ─── The full loop.json the console produces ────────────────────────────────

export interface ConsoleInput {
  id: string;
  name: string;
  blueprint: Blueprint;
  dials: DialState;
  destination: { acceptanceCriteria: string[]; gateStages: GateStageDef[] };
  stateDir: string;
  task: string;
  // drawer overrides: a partial EngineConfig deep-merged over the composed one.
  engineOverrides?: Partial<EngineConfig>;
}

/**
 * Assemble the exact loop.json (camelCase) that `create_loop` persists.
 * `engine` is the composed config with any drawer overrides merged on top.
 */
export function composeLoopDef(input: ConsoleInput): LoopDefDraft {
  const composed = composeEngine(input.blueprint, input.dials, input.destination, input.task);
  const engine = mergeEngine(composed, input.engineOverrides);
  const stopFlag = `${input.stateDir.replace(/\/$/, '')}/STOP`;
  const checkpoint = `${input.stateDir.replace(/\/$/, '')}/checkpoint.json`;
  return {
    id: input.id,
    name: input.name,
    theme: input.blueprint.theme,
    kind: 'generic',
    adapter: 'generic',
    stateDir: input.stateDir,
    logFile: 'log.jsonl',
    stopFlag,
    checkpoint,
    // Mirror the seeded generic loops exactly (orrery/loops/hello/loop.json): the
    // Python engine's `loop` console entrypoint, not the retired loop.ps1 script.
    // Args are relative — the Rust side resolves them against the loop's own dir
    // (loops/<id>/), same as stateDir/stopFlag/checkpoint above, so `.` (cwd) and
    // `loop.json` land in the right place regardless of the app's own cwd.
    start: {
      program: 'loop',
      args: ['--loop-json', 'loop.json', '--cwd', '.', '--state-dir', input.stateDir],
    },
    engine,
  };
}

/** Shallow-by-section deep merge of drawer overrides over a composed engine. */
function mergeEngine(base: EngineConfig, over?: Partial<EngineConfig>): EngineConfig {
  if (!over) return base;
  const out = { ...base } as unknown as Record<string, unknown>;
  for (const key of Object.keys(over) as (keyof EngineConfig)[]) {
    const ov = over[key];
    const bv = base[key];
    if (ov && typeof ov === 'object' && !Array.isArray(ov) && bv && typeof bv === 'object') {
      // merge one level (sections like cost / stop / gate / models)
      out[key] = { ...(bv as object), ...(ov as object) };
    } else if (ov !== undefined) {
      out[key] = ov as unknown;
    }
  }
  return out as unknown as EngineConfig;
}

// ─── Live-preview projection (where the horizon lands, est. $, audit, strikes)─

export interface PreviewSummary {
  ceilingUsd: number;
  estUsd: number; // a back-of-envelope spend estimate from iters × model heat
  horizonAtPct: number; // where the est spend sits on the cost horizon (0..1+)
  auditOn: boolean;
  strikeBudget: number;
  maxIters: number;
  executeModel: Model;
  humanInLoop: boolean;
  plateauK: number;
  stageCount: number;
  acCount: number;
}

const MODEL_COST_PER_ITER: Record<Model, number> = {
  haiku: 0.04,
  sonnet: 0.22,
  opus: 0.95,
};

/**
 * A cheap, deterministic projection used by the live preview AND "Preview night".
 * estUsd ≈ a fraction of maxIters × the execute model's per-iter heat, clamped to
 * the ceiling — "at this thrift, the horizon sits here, ~$3" (plan §4).
 */
export function projectPreview(engine: EngineConfig): PreviewSummary {
  const perIter = MODEL_COST_PER_ITER[engine.models.execute] ?? 0.22;
  const maxIters = engine.stop.maxIters;
  // assume a realistic run uses ~55% of the iteration budget before going green
  const estUsd = round2(Math.min(engine.cost.ceilingUsd, maxIters * 0.55 * perIter));
  const horizonAtPct = engine.cost.ceilingUsd > 0 ? estUsd / engine.cost.ceilingUsd : 0;
  return {
    ceilingUsd: engine.cost.ceilingUsd,
    estUsd,
    horizonAtPct,
    auditOn: engine.verify.strictness !== 'lenient' || engine.verify.contract.length > 0,
    strikeBudget: engine.regression.strikeBudget,
    maxIters,
    executeModel: engine.models.execute,
    humanInLoop: engine.qa.humanInLoop,
    plateauK: engine.decide.plateauK,
    stageCount: engine.gate.stages.length,
    acCount: engine.verify.contract.length,
  };
}

/**
 * "Preview night" — a simulated overnight run that fast-forwards the dials'
 * spend trajectory to show WHERE the cost horizon lands before spending real
 * quota (plan §4). Returns a sawtooth-ish cumulative series + the landing point.
 */
export interface NightPreview {
  series: { iter: number; cum: number }[];
  landsAtUsd: number;
  landsAtPct: number;
  ceilingUsd: number;
  hitsCeiling: boolean;
  greenAtIter: number | null;
}

export function previewNight(engine: EngineConfig): NightPreview {
  const perIter = MODEL_COST_PER_ITER[engine.models.execute] ?? 0.22;
  const ceiling = engine.cost.ceilingUsd;
  const maxIters = engine.stop.maxIters;
  // expect to go green around 55% of the budget (a healthy run), unless ceiling bites
  const greenAt = Math.max(2, Math.round(maxIters * 0.55));
  const series: { iter: number; cum: number }[] = [];
  let cum = 0;
  let green: number | null = null;
  let hits = false;
  for (let i = 1; i <= maxIters; i++) {
    // each iter costs the per-iter heat with a little discovery/judge tax
    cum = round2(cum + perIter + 0.03);
    if (cum >= ceiling) {
      cum = ceiling;
      hits = true;
      series.push({ iter: i, cum });
      break;
    }
    series.push({ iter: i, cum });
    if (i >= greenAt && green === null) {
      green = i;
      break; // a healthy run stops at green
    }
  }
  return {
    series,
    landsAtUsd: cum,
    landsAtPct: ceiling > 0 ? cum / ceiling : 0,
    ceilingUsd: ceiling,
    hitsCeiling: hits,
    greenAtIter: green,
  };
}

// ─── Validation (used in dev where create_loop no-ops) ──────────────────────

export function isSafeLoopId(id: string): boolean {
  return /^[A-Za-z0-9_-]{1,64}$/.test(id);
}

export interface ValidationResult {
  ok: boolean;
  errors: string[];
}

/** Validate a console draft mirrors the Rust-side guards (id safety + AC/gate). */
export function validateDraft(
  input: { id: string; name: string; acceptanceCriteria: string[]; gateStages: GateStageDef[] },
  existingIds: string[],
): ValidationResult {
  const errors: string[] = [];
  if (!input.id.trim()) errors.push('Give the loop an id.');
  else if (!isSafeLoopId(input.id))
    errors.push('Id must be letters, digits, “-” or “_” (1–64 chars).');
  else if (existingIds.includes(input.id)) errors.push(`A loop “${input.id}” already exists.`);
  if (!input.name.trim()) errors.push('Give the loop a name.');
  if (input.gateStages.filter((s) => s.command.trim() || s.name.trim()).length === 0)
    errors.push('Add at least one gate stage (the test gate).');
  if (input.acceptanceCriteria.filter((a) => a.trim()).length === 0)
    errors.push('Describe at least one acceptance criterion.');
  return { ok: errors.length === 0, errors };
}
