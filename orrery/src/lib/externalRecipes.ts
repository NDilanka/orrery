// externalRecipes.ts — authoring the two EXTERNAL loop adapters (BMAD + QA).
//
// A generic loop is authored from a blueprint + dials (see blueprints.ts). The
// two external adapters are different animals: the Python engine picks the
// adapter purely by `start.program` — `loop` (generic), `loop-bmad`, or
// `loop-qa` — NOT by `kind`/`adapter` (those are UI/Rust metadata). Each adapter
// reads its OWN top-level config block that is a SIBLING of `engine`:
//   loop-bmad → a top-level `bmad` block   (engine/orrery_loop/bmad/driver.py)
//   loop-qa   → a top-level `qa`   block   (engine/orrery_loop/qa/discover.py)
// Neither block is ever nested under `engine`.
//
// HONESTY CONSTRAINT (mirrors the same rule in blueprints.ts for `engine`):
// every key this module emits under `bmad`/`qa` is one the driver actually
// reads — `_BMAD_KNOWN_KEYS` in bmad/driver.py resp. `_QA_KNOWN_KEYS` in
// qa/discover.py. An unknown key is not an error: the driver prints a stderr
// "unrecognized key" warning and silently ignores it, so emitting one would be
// a lie the user never sees. The companion test (externalRecipes.test.ts) keeps
// a standing mirror of both known-key sets and asserts every composed block —
// at each profile and with every advanced field set — stays inside them.
//
// This module is pure + framework-free (no Svelte, no Rust, no fs) so it can be
// unit-checked in isolation and reused by the live preview, exactly like
// blueprints.ts.

import { isSafeLoopId, type GateStageDef } from './blueprints';

// ─── Public shapes ──────────────────────────────────────────────────────────

export type ExternalAdapter = 'bmad' | 'qa';
export type BmadPhase = 'create' | 'dev' | 'review' | 'smoke' | 'retro' | 'decider';
export type ReviewRetroMode = 'qa' | 'single-pass';
export type SmokeMode = 'iterative' | 'single-pass';

/**
 * The loop.json an external adapter persists. Mirrors the seeded templates
 * (orrery/loops/bmad/loop.json, orrery/loops/webapp-qa/loop.json): `kind` is
 * always 'external'; `adapter` is 'bmad' for the BMAD driver but 'generic' for
 * the QA driver (matching webapp-qa's seed). There is no `engine` block — the
 * adapter-specific `bmad`/`qa` block carries the config instead.
 */
export interface ExternalLoopDef {
  id: string;
  name: string;
  theme: string;
  kind: 'external';
  adapter: 'bmad' | 'generic';
  stateDir: string;
  logFile: string;
  stopFlag: string;
  checkpoint: string;
  start: { program: string; args: string[] };
  bmad?: Record<string, unknown>;
  qa?: Record<string, unknown>;
}

// ─── BMAD ───────────────────────────────────────────────────────────────────

export interface BmadRecipeInput {
  id: string;
  name: string;
  theme?: string;
  /** the repo the sprint runs in — passed as --project-root. */
  targetRepo: string;
  /** the branch new work merges into — passed as --merge-base (default develop). */
  mergeBase: string;
  /** the loop's own state dir (default '.loop'). */
  stateDir?: string;
  models: Record<BmadPhase, string>;
  effort: Record<BmadPhase, string>;
  reviewMode: ReviewRetroMode;
  smokeMode: SmokeMode;
  retroMode: ReviewRetroMode;
  noSmoke: boolean;
  // ── optional toggles (CLI flags) ──
  noMerge?: boolean;
  noRetro?: boolean;
  noVerify?: boolean;
  noPlanGate?: boolean;
  autoRollback?: boolean;
  // ── optional advanced (`bmad` block keys, emitted only when provided) ──
  maxStories?: number;
  createTimeoutMin?: number;
  devTimeoutMin?: number;
  reviewTimeoutMin?: number;
  smokeTimeoutMin?: number;
  retroTimeoutMin?: number;
  deciderTimeoutMin?: number;
  gateStages?: GateStageDef[];
  devServerArgv?: string[];
  // ── quality-gate switches (default ON — emit a block ONLY when turned OFF) ──
  verifyEnabled?: boolean;
  planGateEnabled?: boolean;
  testIntegrityEnabled?: boolean;
}

/**
 * The two one-click BMAD profiles the console offers.
 *  maxPower  — mirrors the seed loops/bmad/loop.json EXACTLY (all-Opus, xhigh,
 *              single-pass gates; a cheap sonnet/low decider).
 *  costAware — mirrors the engine's own DEFAULT_MODELS/DEFAULT_EFFORTS (sonnet
 *              phases, empty dev/effort = "inherit the engine default", haiku
 *              decider; iterative/qa gates).
 */
export const BMAD_PROFILES: Record<
  'maxPower' | 'costAware',
  {
    models: Record<BmadPhase, string>;
    effort: Record<BmadPhase, string>;
    reviewMode: ReviewRetroMode;
    smokeMode: SmokeMode;
    retroMode: ReviewRetroMode;
  }
> = {
  maxPower: {
    models: {
      create: 'claude-opus-4-8[1m]',
      dev: 'claude-opus-4-8[1m]',
      review: 'claude-opus-4-8[1m]',
      smoke: 'claude-opus-4-8[1m]',
      retro: 'claude-opus-4-8[1m]',
      decider: 'sonnet',
    },
    effort: {
      create: 'xhigh',
      dev: 'xhigh',
      review: 'xhigh',
      smoke: 'xhigh',
      retro: 'xhigh',
      decider: 'low',
    },
    reviewMode: 'single-pass',
    smokeMode: 'single-pass',
    retroMode: 'single-pass',
  },
  costAware: {
    models: { create: 'sonnet', dev: '', review: 'sonnet', smoke: 'sonnet', retro: 'sonnet', decider: 'haiku' },
    effort: { create: '', dev: '', review: '', smoke: '', retro: '', decider: '' },
    reviewMode: 'qa',
    smokeMode: 'iterative',
    retroMode: 'qa',
  },
};

/**
 * Assemble the BMAD loop.json. Adapter is chosen by start.program='loop-bmad';
 * config lives in the top-level `bmad` block (a sibling of, never nested under,
 * anything). Only keys the driver reads are emitted (honesty constraint).
 */
export function composeBmadLoopDef(input: BmadRecipeInput): ExternalLoopDef {
  const stateDir = input.stateDir?.trim() || '.loop';

  const args: string[] = [
    '--project-root',
    input.targetRepo.trim(),
    '--state-dir',
    stateDir,
    '--merge-base',
    input.mergeBase.trim() || 'develop',
    '--loop-json',
    'loop.json',
  ];
  if (input.noSmoke) args.push('--no-smoke');
  if (input.noMerge) args.push('--no-merge');
  if (input.noRetro) args.push('--no-retro');
  if (input.noVerify) args.push('--no-verify');
  if (input.noPlanGate) args.push('--no-plan-gate');
  if (input.autoRollback) args.push('--auto-rollback');

  const bmad: Record<string, unknown> = {
    models: { ...input.models },
    effort: { ...input.effort },
    reviewMode: input.reviewMode,
    smokeMode: input.smokeMode,
    retroMode: input.retroMode,
  };
  // advanced keys — emit ONLY when the user supplied one
  if (input.maxStories !== undefined) bmad.maxStories = input.maxStories;
  if (input.gateStages !== undefined) bmad.gateStages = input.gateStages;
  if (input.devServerArgv !== undefined) bmad.devServerArgv = input.devServerArgv;
  if (input.createTimeoutMin !== undefined) bmad.createTimeoutMin = input.createTimeoutMin;
  if (input.devTimeoutMin !== undefined) bmad.devTimeoutMin = input.devTimeoutMin;
  if (input.reviewTimeoutMin !== undefined) bmad.reviewTimeoutMin = input.reviewTimeoutMin;
  if (input.smokeTimeoutMin !== undefined) bmad.smokeTimeoutMin = input.smokeTimeoutMin;
  if (input.retroTimeoutMin !== undefined) bmad.retroTimeoutMin = input.retroTimeoutMin;
  if (input.deciderTimeoutMin !== undefined) bmad.deciderTimeoutMin = input.deciderTimeoutMin;
  // quality gates are ON in the engine by default — only speak up to turn one OFF
  if (input.verifyEnabled === false) bmad.verify = { enabled: false };
  if (input.planGateEnabled === false) bmad.planGate = { enabled: false };
  if (input.testIntegrityEnabled === false) bmad.testIntegrity = { enabled: false };

  return {
    id: input.id,
    name: input.name,
    theme: input.theme ?? 'plasma',
    kind: 'external',
    adapter: 'bmad',
    stateDir,
    logFile: 'log.jsonl',
    stopFlag: `${stateDir}/STOP`,
    checkpoint: `${stateDir}/checkpoint.json`,
    start: { program: 'loop-bmad', args },
    bmad,
  };
}

// ─── QA ─────────────────────────────────────────────────────────────────────

export interface QaRecipeInput {
  id: string;
  name: string;
  theme?: string;
  /** the webapp repo under test — passed as --project-root. */
  targetRepo: string;
  /** the acceptance-criteria manifest — passed as --manifest. */
  manifest: string;
  /** the loop's own state dir (default '.loop'). */
  stateDir?: string;
  /** the running app's URL (default 'http://localhost:3000'). */
  baseUrl?: string;
  /** the app's human name in prompts (default 'app'). */
  app?: string;
  storageState?: string;
  seedSummary?: string;
  // ── optional advanced (`qa` block keys, emitted only when provided) ──
  specDir?: string;
  model?: string;
  effort?: string;
  fallbackModel?: string;
  maxTurns?: number;
  timeoutSec?: number;
  costCeilingUsd?: number;
  epics?: number[];
  headless?: boolean;
}

/**
 * Assemble the QA loop.json. Adapter is chosen by start.program='loop-qa';
 * config lives in the top-level `qa` block. NOTE: loop-qa does NOT accept
 * `--cwd` (it derives cwd from --project-root) — passing one makes it reject
 * the invocation, so it is deliberately absent from args. `adapter` is
 * 'generic' to match the webapp-qa seed template.
 */
export function composeQaLoopDef(input: QaRecipeInput): ExternalLoopDef {
  const stateDir = input.stateDir?.trim() || '.loop';

  const args: string[] = [
    '--project-root',
    input.targetRepo.trim(),
    '--manifest',
    input.manifest.trim(),
    '--state-dir',
    stateDir,
    '--loop-json',
    'loop.json',
  ];

  const qa: Record<string, unknown> = {
    app: input.app?.trim() || 'app',
    baseUrl: input.baseUrl?.trim() || 'http://localhost:3000',
  };
  if (input.storageState && input.storageState.trim()) qa.storageState = input.storageState.trim();
  if (input.seedSummary && input.seedSummary.trim()) qa.seedSummary = input.seedSummary;
  // advanced keys — emit ONLY when the user supplied one
  if (input.specDir !== undefined) qa.specDir = input.specDir;
  if (input.model !== undefined) qa.model = input.model;
  if (input.effort !== undefined) qa.effort = input.effort;
  if (input.fallbackModel !== undefined) qa.fallbackModel = input.fallbackModel;
  if (input.maxTurns !== undefined) qa.maxTurns = input.maxTurns;
  if (input.timeoutSec !== undefined) qa.timeoutSec = input.timeoutSec;
  if (input.costCeilingUsd !== undefined) qa.costCeilingUsd = input.costCeilingUsd;
  if (input.epics !== undefined) qa.epics = input.epics;
  if (input.headless !== undefined) qa.headless = input.headless;

  return {
    id: input.id,
    name: input.name,
    theme: input.theme ?? 'aurora',
    kind: 'external',
    adapter: 'generic',
    stateDir,
    logFile: 'log.jsonl',
    stopFlag: `${stateDir}/STOP`,
    checkpoint: `${stateDir}/checkpoint.json`,
    start: { program: 'loop-qa', args },
    qa,
  };
}

// ─── Validation ─────────────────────────────────────────────────────────────

// A repo path must be absolute so the driver resolves it independent of cwd:
// a Windows drive path (C:\… or C:/…) OR a POSIX root (/…).
const ABSOLUTE_PATH = /^([A-Za-z]:[\\/]|\/)/;

/**
 * Validate an external-loop draft the way the Rust side + driver would: safe/
 * unique id, a name, an absolute target repo, and the adapter's required
 * pointer (BMAD needs a merge base; QA needs a manifest).
 */
export function validateExternalDraft(
  input: { id: string; name: string; targetRepo: string; mergeBase?: string; manifest?: string },
  adapter: ExternalAdapter,
  existingIds: string[],
): { ok: boolean; errors: string[] } {
  const errors: string[] = [];

  if (!input.id.trim()) errors.push('Give the loop an id.');
  else if (!isSafeLoopId(input.id))
    errors.push('Id must be letters, digits, “-” or “_” (1–64 chars).');
  else if (existingIds.includes(input.id)) errors.push(`A loop “${input.id}” already exists.`);

  if (!input.name.trim()) errors.push('Give the loop a name.');

  if (!input.targetRepo.trim()) errors.push('Point the loop at a target repo.');
  else if (!ABSOLUTE_PATH.test(input.targetRepo.trim()))
    errors.push('Repo path must be an absolute path.');

  if (adapter === 'bmad') {
    if (!(input.mergeBase ?? '').trim())
      errors.push('Set a merge base (the branch new work merges into).');
  } else if (adapter === 'qa') {
    if (!(input.manifest ?? '').trim())
      errors.push('Point the QA loop at an acceptance-criteria manifest.');
  }

  return { ok: errors.length === 0, errors };
}
