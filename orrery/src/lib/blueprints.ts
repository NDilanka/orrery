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
//
// HONESTY CONSTRAINT (wave U3 Task 1): every field this module emits under
// `engine` is a key `engine/orrery_loop/config.py` (`EngineConfig` + its per-block
// parsers) actually reads. A2 retired `gate.greenWhen` (parsed, never
// consulted) and made an unrecognized key print a stderr warning — so this
// module used to hand the engine a `regression`/`decide`/`qa`/`concurrency`/
// `quota` shaped config it silently ignored in full. Those blocks are gone;
// every dial now drives a REAL bundle (see `deriveFromDials`).

import type { Model } from './types';

// ─── Engine config shape (PROTOCOL §7 engine block, engine/orrery_loop/config.py) ──
// Kept structural & permissive: this is the object that lands under loop.json's
// `engine` key. The Rust side treats it as opaque Value; the console owns it.
// Every top-level key here (and every nested key) is one `_ENGINE_KNOWN_KEYS` /
// per-block `_*_KNOWN_KEYS` actually resolves — nothing else is emitted.

export interface GateStageDef {
  name: string;
  command: string;
  passPattern?: string;
  failPattern?: string;
  // advanced, per-stage (engine/orrery_loop/config.py GateStage) — not surfaced in the
  // console's Definition-of-Done rows yet; kept so a hand-authored value round-trips.
  heldOut?: boolean;
  lockGlobs?: string[];
}

export interface EngineConfig {
  task: string;
  models: { discover: Model; execute: Model; judge: Model; hard: Model };
  maxTurns: number;
  iterTimeoutMin: number;
  allowedTools: string[];
  permissionMode: 'acceptEdits' | 'plan' | 'default' | 'bypassPermissions';
  gate: {
    stages: GateStageDef[];
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
  verify: {
    judgeModel: Model;
    contract: string[];
    // the switch: only when true does the engine emit the judge event + run the
    // anti-false-green VERIFY pass (engine/orrery_loop/config.py VerifyConfig docstring).
    enabled: boolean;
    mutationAudit: boolean;
    mutationEvery: number;
  };
  feedback: { compact: boolean };
  memory: { enabled: boolean; path: string | null; recallLimit: number };
  metrics: { emit: boolean };
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
// resolves the three positions into the concrete engine fields they own —
// every field named below is a real `EngineConfig` field (see the honesty
// constraint at the top of this file).

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
  // only the two verify fields the PATIENCE dial owns; `enabled`/`contract`/`judgeModel`
  // are owned by the destination (the AC list) + a fixed cheap judge, not a dial.
  verify: { mutationAudit: boolean; mutationEvery: number };
  feedback: EngineConfig['feedback'];
  permissionMode: EngineConfig['permissionMode'];
  maxTurns: number;
  iterTimeoutMin: number;
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
 * preview reads the same derivation, so the preview never lies. Every field
 * produced here is a real, engine-consumed `EngineConfig` field (see the
 * dial→field mapping table in the wave U3 report).
 */
export function deriveFromDials(d: DialState): DialDerived {
  const { ambition, patience, autonomy } = d;

  // AMBITION ⟷ THRIFT — model tiering + cost ceiling + iteration budget, one bundle.
  // High ambition = hotter models, taller ceiling, more iterations.
  const models: EngineConfig['models'] = {
    discover: 'haiku', // discovery stays cheap regardless
    judge: 'haiku', // the judge is deliberately cheap (fresh-context verifier)
    execute: modelFromHeat(lerp(0.3, 0.85, ambition)), // sonnet → opus as ambition climbs
    hard: modelFromHeat(lerp(0.6, 1.0, ambition)), // sonnet → opus for the hard fallback
  };
  const ceilingUsd = round2(lerp(1.0, 25.0, ambition)); // $1 thrift → $25 ambition
  const maxIters = lerpInt(6, 40, ambition);

  // PATIENCE ⟷ FUSSINESS — the real stop.* resilience knobs (how many stagnant /
  // plateaued / regressed iterations the loop tolerates before giving up) plus the
  // verify mutation-audit rigor. The engine has no configurable pass/fail RULE (gate
  // green is always "every stage exits 0" — PROTOCOL §7), so "verifier strictness" is
  // honestly the mutation-audit knob: how hard a claimed-green iteration gets
  // double-checked, not a different green definition.
  const stagnationLimit = lerpInt(1, 4, patience);
  const plateauLimit = lerpInt(2, 8, patience);
  const regressLimit = lerpInt(1, 6, patience);
  const mutationAudit = patience < 0.7; // the lenient tail turns the extra audit off
  const mutationEvery = patience < 0.34 ? 1 : patience < 0.7 ? 3 : 0; // fussy = audit every green

  // AUTONOMY ⟷ COMPANY — how unattended the run is: permission mode, the per-phase
  // turn budget + per-iteration wall-clock cap (more slack when nobody's watching),
  // whether a STOP lands at the next phase boundary, and feedback verbosity (a
  // compacted single-failure steer suits an unattended overnight run; the full raw
  // gate dump suits a human reading along).
  const permissionMode: EngineConfig['permissionMode'] =
    autonomy > 0.8 ? 'bypassPermissions' : 'acceptEdits';
  const maxTurns = lerpInt(15, 50, autonomy);
  const iterTimeoutMin = lerpInt(20, 120, autonomy);
  const gracefulAtPhase = autonomy < 0.7; // company stops at phase teeth; high-autonomy runs longer
  const compact = autonomy > 0.6;

  return {
    models,
    cost: { ceilingUsd, alertPct: [50, 80, 100] },
    stop: { maxIters, stagnationLimit, plateauLimit, regressLimit, gracefulAtPhase },
    verify: { mutationAudit, mutationEvery },
    feedback: { compact },
    permissionMode,
    maxTurns,
    iterTimeoutMin,
  };
}

// ─── Guardrail presets (one-click dial bundles) ─────────────────────────────
// Named intents — Careful / Balanced / Fast / Overnight — that set all three
// dials at once. A preset is JUST a DialState, so it flows through
// deriveFromDials → composeEngine exactly like a hand-set dial: nothing new is
// emitted into the engine (the honesty constraint holds). The UI offers these
// as "pick an intent, not three sliders"; nudging any dial snaps back to
// `null` (→ Custom).

export type PresetName = 'careful' | 'balanced' | 'fast' | 'overnight';

export const PRESET_ORDER: PresetName[] = ['careful', 'balanced', 'fast', 'overnight'];

export const PRESETS: Record<PresetName, DialState> = {
  // strict + cheap + attended
  careful: { ambition: 0.22, patience: 0.18, autonomy: 0.28 },
  // the recommended middle
  balanced: { ambition: 0.45, patience: 0.45, autonomy: 0.55 },
  // ambitious + lenient + hands-off
  fast: { ambition: 0.85, patience: 0.8, autonomy: 0.85 },
  // long + unattended (moderate models, very autonomous)
  overnight: { ambition: 0.6, patience: 0.55, autonomy: 0.92 },
};

/**
 * Snap a dial position to the nearest preset, or `null` once it's been nudged
 * off every preset (the UI shows that as "Custom"). Distance is the summed L1
 * gap across the three dials; the tight default threshold means a deliberate
 * drag of even one dial leaves the preset, while `presetFromDials(PRESETS[x])`
 * round-trips to `x`.
 */
export function presetFromDials(d: DialState, threshold = 0.06): PresetName | null {
  let best: PresetName | null = null;
  let bestDist = Infinity;
  for (const name of PRESET_ORDER) {
    const p = PRESETS[name];
    const dist =
      Math.abs(p.ambition - d.ambition) +
      Math.abs(p.patience - d.patience) +
      Math.abs(p.autonomy - d.autonomy);
    if (dist < bestDist) {
      bestDist = dist;
      best = name;
    }
  }
  return bestDist <= threshold ? best : null;
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
        lockGlobs: ['**/*.test.ts'],
      },
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
    tagline: 'multi-stage gate — build, then lint, then test',
    dials: { ambition: 0.6, patience: 0.55, autonomy: 0.55 },
    base: {
      allowedTools: [...COMMON_TOOLS, 'Bash(bun lint)', 'Bash(git:*)'],
      gate: {
        stages: [
          { name: 'codegen', command: 'bun run build', passPattern: '', failPattern: 'error' },
          { name: 'lint', command: 'bun lint', passPattern: '', failPattern: '(\\d+)\\s+error' },
          { name: 'test', command: 'bun test', passPattern: '(\\d+)\\s+pass', failPattern: '(\\d+)\\s+fail' },
        ],
        lockGlobs: ['**/*.test.ts', '**/*.spec.ts'],
      },
    },
    destination: {
      acceptanceCriteria: ['The build succeeds', 'Lint is clean', 'All tests pass'],
      gateStages: [
        { name: 'codegen', command: 'bun run build', passPattern: '', failPattern: 'error' },
        { name: 'lint', command: 'bun lint', passPattern: '', failPattern: '(\\d+)\\s+error' },
        { name: 'test', command: 'bun test', passPattern: '(\\d+)\\s+pass', failPattern: '(\\d+)\\s+fail' },
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
        lockGlobs: [],
      },
    },
    destination: {
      acceptanceCriteria: ['The approach is sound and reviewed', 'Tests cover the new behavior'],
      gateStages: [
        { name: 'test', command: 'bun test', passPattern: '(\\d+)\\s+pass', failPattern: '(\\d+)\\s+fail' },
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
        lockGlobs: ['**/*.test.ts'],
      },
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
  // FOOT-GUN CATCH: `verify.enabled` is the switch that arms the AC-driven
  // anti-false-green pass (engine/orrery_loop/config.py VerifyConfig docstring — a
  // non-empty `contract` alone does nothing without it). Typing acceptance
  // criteria into the console and having them silently do nothing would be
  // exactly the kind of foot-gun this wave exists to catch, so `enabled` tracks
  // "has the user described at least one criterion" automatically.
  const hasCriteria = destination.acceptanceCriteria.some((c) => c.trim().length > 0);
  return {
    task,
    models: derived.models,
    // per-phase Claude turn cap + per-iteration wall-clock cap — both AUTONOMY-owned
    // (see deriveFromDials): the iteration BUDGET the AMBITION dial owns is stop.maxIters.
    maxTurns: derived.maxTurns,
    iterTimeoutMin: derived.iterTimeoutMin,
    allowedTools: blueprint.base.allowedTools ?? COMMON_TOOLS,
    permissionMode: derived.permissionMode,
    gate: {
      stages: destination.gateStages.length
        ? destination.gateStages
        : blueprint.base.gate?.stages ?? [],
      lockGlobs: blueprint.base.gate?.lockGlobs ?? [],
    },
    cost: derived.cost,
    stop: derived.stop,
    verify: {
      judgeModel: 'haiku',
      contract: destination.acceptanceCriteria,
      enabled: hasCriteria,
      mutationAudit: derived.verify.mutationAudit,
      mutationEvery: derived.verify.mutationEvery,
    },
    feedback: derived.feedback,
    // memory/metrics are OFF by default (matches the engine's own defaults exactly —
    // MemoryConfig.enabled=False, MetricsConfig.emit=False); the Diagnostics drawer is
    // the only way to turn them on, so a fresh loop's config is 1:1 with "say nothing".
    memory: { enabled: false, path: null, recallLimit: 5 },
    metrics: { emit: false },
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
  // the working dir the generic loop runs its gate/git/agent in; empty = its
  // own loops/<id>/ folder (the '.' the Rust side resolves against the loop dir).
  cwd?: string;
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
      args: ['--loop-json', 'loop.json', '--cwd', input.cwd?.trim() || '.', '--state-dir', input.stateDir],
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
  auditOn: boolean; // verify.enabled — is the AC-driven anti-false-green pass armed
  regressLimit: number; // stop.regressLimit — the real regression-tolerance field
  maxIters: number;
  executeModel: Model;
  plateauLimit: number; // stop.plateauLimit — the real plateau-tolerance field
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
 * the ceiling — "at this thrift, the horizon sits here, ~$3" (plan §4). Every field
 * is sourced straight from a real `EngineConfig` field — see the module-top honesty
 * constraint.
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
    auditOn: engine.verify.enabled,
    regressLimit: engine.stop.regressLimit,
    maxIters,
    executeModel: engine.models.execute,
    plateauLimit: engine.stop.plateauLimit,
    stageCount: engine.gate.stages.length,
    acCount: engine.verify.contract.filter((c) => c.trim().length > 0).length,
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

// ─── Numeric bounds (engine-consumed fields the console lets you type a raw
// number into via drawer overrides — engine/orrery_loop/config.py just casts to
// int/float and trusts it, no clamping on that side). Split into two groups
// by inspecting how core.py actually consumes each field:
//   POSITIVE  — a zero/negative/NaN value degenerates the run rather than
//               disabling anything: max_iters=0 → `range(1, 1)` runs zero
//               iterations, max_turns=0 permits no turns, ceiling_usd<=0
//               trips the cost gate before iteration 1's spend is even
//               counted. These have no documented "0 = off" meaning.
//   NONNEGATIVE — 0 is a real, engine-honored value: iter_timeout_min=0 is
//               the DOCUMENTED disable (engine/orrery_loop/core.py: "0 = unbounded" —
//               also the console's own "(0 = unbounded)" field hint); the
//               stop.*Limit / memory.recallLimit counters are thresholds
//               that are simply strictest at 0 (stop on the very first
//               stagnant/plateaued/regressed iteration, or recall nothing).
const POSITIVE_FIELDS = ['ceilingUsd', 'maxIters', 'maxTurns'] as const;
const NONNEGATIVE_FIELDS = [
  'iterTimeoutMin',
  'stagnationLimit',
  'plateauLimit',
  'regressLimit',
  'recallLimit',
] as const;

export interface NumericBoundsInput {
  ceilingUsd: number;
  maxIters: number;
  maxTurns: number;
  iterTimeoutMin: number;
  stagnationLimit: number;
  plateauLimit: number;
  regressLimit: number;
  recallLimit: number;
}

const NUMERIC_FIELD_LABEL: Record<keyof NumericBoundsInput, string> = {
  ceilingUsd: 'cost ceiling',
  maxIters: 'max iterations',
  maxTurns: 'max turns/phase',
  iterTimeoutMin: 'iteration timeout',
  stagnationLimit: 'stagnation limit',
  plateauLimit: 'plateau limit',
  regressLimit: 'regress limit',
  recallLimit: 'recall limit',
};

/**
 * Reject non-finite (NaN from an emptied number input, Infinity) and
 * out-of-bounds values for the engine-numeric fields present in `input`.
 * Partial — only the keys supplied are checked, so callers that don't yet
 * have an engine draft (e.g. the id/name/destination-only validateDraft
 * callers) aren't forced to fabricate numbers.
 */
export function validateNumericBounds(input: Partial<NumericBoundsInput>): string[] {
  const errors: string[] = [];
  for (const f of POSITIVE_FIELDS) {
    if (!(f in input)) continue;
    const v = input[f] as number;
    if (!Number.isFinite(v) || v <= 0) {
      errors.push(`${NUMERIC_FIELD_LABEL[f]} must be a number greater than 0.`);
    }
  }
  for (const f of NONNEGATIVE_FIELDS) {
    if (!(f in input)) continue;
    const v = input[f] as number;
    if (!Number.isFinite(v) || v < 0) {
      errors.push(`${NUMERIC_FIELD_LABEL[f]} must be a number 0 or greater.`);
    }
  }
  return errors;
}

/** Validate a console draft mirrors the Rust-side guards (id safety + AC/gate)
 *  plus the engine-numeric bounds above when a draft engine is supplied. */
export function validateDraft(
  input: {
    id: string;
    name: string;
    acceptanceCriteria: string[];
    gateStages: GateStageDef[];
    numeric?: Partial<NumericBoundsInput>;
  },
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
  if (input.numeric) errors.push(...validateNumericBounds(input.numeric));
  return { ok: errors.length === 0, errors };
}
