// Orrery Loop Protocol — TS mirror of PROTOCOL.md (v1).
// THE CONTRACT. These shapes must match the Rust core (src-tauri/src/model.rs)
// verbatim. camelCase on the wire. When in doubt, PROTOCOL.md wins.

// ─── §3 Universal RunState enums ───────────────────────────────────────────
export type RunStatus =
  | 'idle'
  | 'running'
  | 'stopping'
  | 'stopped'
  | 'quota-wait'
  | 'handoff'
  | 'error';

export type SixPhase =
  | 'discover'
  | 'assemble'
  | 'execute'
  | 'verify'
  | 'persist'
  | 'decide';

export type ItemStatus =
  | 'backlog'
  | 'ready'
  | 'in-progress'
  | 'review'
  | 'done'
  | 'blocked'
  | 'failed';

// The five "not-running" rest palettes (+ null when running). §Design four states (+failed-dark).
export type RestState =
  | 'certified-done'
  | 'stopped-ember'
  | 'quota-frost'
  | 'handoff-beacon'
  | 'failed-dark'
  | null;

export type Model = 'haiku' | 'sonnet' | 'opus';

// ─── §3 Reduced client model ────────────────────────────────────────────────
export interface GateStage {
  name: string;
  ok: boolean;
  exit: number;
}

export interface Gate {
  green: boolean;
  pass: number;
  fail: number;
  total: number;
  baselinePass: number;
  stages?: GateStage[];
}

export interface Smoke {
  iter: number;
  passed: boolean;
  verdict: string;
  timedOut?: boolean;
}

export interface Pr {
  url: string;
  base: string;
  merged: boolean;
}

export interface GhostCriterion {
  text: string;
  met: boolean;
}

export interface Ghost {
  criteria: GhostCriterion[];
}

export interface Group {
  id: string;
  status: 'backlog' | 'in-progress' | 'done';
  retroStatus: 'optional' | 'done' | 'pending' | null;
  ringIndex: number;
}

export interface WorkItem {
  key: string;
  group: string | null;
  index: number;
  status: ItemStatus;
  gate: Gate | null;
  smoke: Smoke | null;
  pr: Pr | null;
  ghost?: Ghost | null; // frozen AC contract
  strikes: number;
  strikeBudget: number;
  certified: boolean; // verifier sealed it (vs claimed-green)
  costAttributed: number;
  lastEventTs: number; // for sprint-status vs live-event conflict resolution
}

export interface Qa {
  id: string;
  kind: 'review' | 'retro';
  turn: number;
  q: string;
  a: string | null;
  summary: string | null;
  answeredBy: 'agent' | 'ui' | null;
  epic?: string;
}

export interface Verdict {
  pass: boolean;
  failingCriteria: string[];
  evidence?: string;
  nextAction?: string;
  model?: string;
}

export interface CostSample {
  t: number;
  cum: number;
}

export interface RunMeta {
  status: RunStatus;
  restState: RestState; // which "not-running" palette, if any
  pid: number | null;
  target: string | null; // current work item / goal
  branch: string | null;
  mergeBase: string;
  cumUsd: number; // RUNNING-MAX of every cum/cumUsd seen (never additive)
  stage: string | null; // from checkpoint
  stopPending: 'phase' | 'story' | 'now' | null;
  resumeCmd: string | null;
  startedAt: string | null;
  /** ts of the last applied log event (independent of updatedAt, which is checkpoint-only). */
  lastEventAt: string | null;
  updatedAt: string | null;
}

export interface PhaseState {
  name: string | null;
  label: string | null;
  sixPhase: SixPhase | null;
  model: Model | null;
}

export interface CostState {
  cumUsd: number;
  ceilingUsd: number | null;
  alertPct: number | null; // last threshold crossed
  series: CostSample[];
  ratePerMin: number;
}

export interface QuotaState {
  active: boolean;
  label: string | null;
  resetAt: string | null;
  resumeAt: string | null;
  resetType: 'five_hour' | 'weekly' | null;
  probe: number;
  waitSec: number;
}

export interface CacheState {
  hitRatio: number;
  warm: boolean;
}

// §2 engine-v3 `metrics` event — a run-quality fold of the event stream
// (loop.metrics.compute_metrics). camelCase on the wire; null until a metrics
// event is seen. first-try-green + iters/cost-to-green replace pass@k for loops.
export interface Metrics {
  firstTryGreen: boolean;
  itersToGreen: number | null;
  costToGreen: number | null;
  rollbacks: number;
  regressionRate: number;
  totalIters: number;
  totalCost: number;
  finalGreen: boolean;
}

// §2 engine-v3 `verify` event — adversarial verify-before-merge verdict per story.
// verdict ∈ pass|refute|skipped|inconclusive; a `refute` blocks the merge. reason null when absent.
export interface Verify {
  verdict: string;
  reason: string | null;
  cum: number;
}

// §2 engine-v3 `test-integrity` event — git tamper check on pre-existing test files per story.
// `deleted` (a tamper → ok:false) and `modified` (edited in place, scrutinized) are file lists.
export interface TestIntegrity {
  deleted: string[];
  modified: string[];
  ok: boolean;
  cum: number;
}

// §2 engine-v3 `plan-check` event — plan-gate verdict before dev-story per story.
// verdict ∈ ok|blocked|inconclusive; `blocked` halts the story. reason null when absent.
export interface PlanCheck {
  ok: boolean;
  verdict: string;
  reason: string | null;
  cum: number;
}

// §2 engine-v3 BMAD flavor of the `metrics` event — a zero-token run-quality summary at stop.
// Distinct field set from generic `Metrics`; the reducer discriminates on `storiesCompleted`.
export interface BmadMetrics {
  storiesCompleted: number;
  storiesHalted: number;
  devGates: number;
  reviews: number;
  smokeIters: number;
  prsCreated: number;
  prsMerged: number;
  retros: number;
  planChecks: number;
  verifies: number;
  gateReds: number;
  flakyRetries: number;
  quotaWaits: number;
  inputTokens: number;
  outputTokens: number;
  cacheReadTokens: number;
  cacheCreationTokens: number;
  hitRatio: number;
  cumUsd: number;
  durationSec: number;
}

export interface RunState {
  loopId: string;
  run: RunMeta;
  groups: Record<string, Group>; // epics (rings)
  items: Record<string, WorkItem>; // stories / iterations-as-one-item
  currentItem: string | null;
  phase: PhaseState;
  cost: CostState;
  quota: QuotaState;
  cache: CacheState;
  metrics: Metrics | null; // run-quality summary (§2 engine-v3 `metrics` event); null until seen
  questions: Qa[];
  verdicts: Record<string, Verdict>; // by item key, latest
  // §2 engine-v3 additive maps/summary — OMITTED (not present) until their event fires, so a state
  // with no such events serializes byte-identically to an older reducer's (keeps goldens stable).
  verifies?: Record<string, Verify>; // adversarial verify verdict, by story
  testIntegrity?: Record<string, TestIntegrity>; // pre-existing-test tamper check, by story
  planChecks?: Record<string, PlanCheck>; // plan-gate verdict, by story
  bmadMetrics?: BmadMetrics; // BMAD-flavored run-quality summary
  events: number; // count
}

// ─── §2 Raw events on log.jsonl ─────────────────────────────────────────────
// Every object has a string `event`. Core events any loop emits; BMAD events
// are a superset. Unknown events are kept (counted) but ignored by the reducer.
// We keep this permissive (index signature) because field order is
// non-deterministic and adapters/the reducer key on `event`, never position.

export type EventName =
  // core
  | 'iter'
  | 'stop'
  | 'parse_error'
  // core v2
  | 'gate'
  | 'verdict'
  | 'model'
  | 'cost-alert'
  | 'cache'
  | 'plateau'
  | 'rollback'
  | 'handoff'
  | 'phase-timeout'
  // core v3
  | 'metrics'
  | 'token-usage'
  // quota
  | 'quota-hit'
  | 'quota-wait'
  | 'quota-resume'
  // bmad superset
  | 'engine-start'
  | 'start'
  | 'story-start'
  | 'dev-gate'
  | 'review-question'
  | 'review-answer'
  | 'review-complete'
  | 'retro-start'
  | 'retro-question'
  | 'retro-answer'
  | 'retro-complete'
  | 'smoke-server'
  | 'smoke-iter'
  | 'pr-created'
  | 'pr-merged'
  | 'cooperative-stop'
  // string-keyed escape hatch for anything else
  | (string & {});

export interface RawEvent {
  event: EventName;
  // common fields seen across the protocol (all optional — keyed by `event`)
  iter?: number;
  cost?: number;
  cum?: number;
  cumUsd?: number;
  pass?: number | boolean; // int count (iter/gate) OR boolean (verdict)
  fail?: number;
  total?: number;
  best?: number;
  bestPass?: number;
  baselinePass?: number;
  changed?: boolean;
  green?: boolean;
  ok?: boolean;
  stale?: number;
  plateau?: number;
  regress?: number;
  action?: string;
  reason?: string;
  // gate / verdict
  story?: string;
  item?: string;
  stages?: GateStage[];
  failingCriteria?: string[];
  evidence?: string;
  nextAction?: string;
  // model / phase
  phase?: string;
  model?: string;
  costPerTurn?: number;
  label?: string;
  timeoutSec?: number;
  // cost / cache
  pct?: number;
  ceiling?: number;
  hitRatio?: number;
  warm?: boolean;
  // token-usage (engine v3) — per-call token + cache telemetry; only hitRatio/warm feed the
  // reducer (same fields as `cache`). costUsd is a per-call DELTA (not a running cum), so it is
  // intentionally NOT fed into cost.cumUsd/series; cum* below are cumulative TOKEN counts, not
  // USD, so they don't fit `cum`/`cumUsd` either. Carried here for completeness / future UI use.
  input?: number;
  output?: number;
  cacheRead?: number;
  cacheCreation?: number;
  costUsd?: number;
  cumInput?: number;
  cumOutput?: number;
  cumCacheRead?: number;
  cumCacheCreation?: number;
  // metrics (engine v3 — generic flavor)
  firstTryGreen?: boolean;
  itersToGreen?: number | null;
  costToGreen?: number | null;
  rollbacks?: number;
  regressionRate?: number;
  totalIters?: number;
  totalCost?: number;
  finalGreen?: boolean;
  // metrics (engine v3 — BMAD flavor; discriminated by `storiesCompleted` being present)
  storiesCompleted?: number;
  storiesHalted?: number;
  devGates?: number;
  reviews?: number;
  smokeIters?: number;
  prsCreated?: number;
  prsMerged?: number;
  retros?: number;
  planChecks?: number;
  verifies?: number;
  gateReds?: number;
  flakyRetries?: number;
  quotaWaits?: number;
  inputTokens?: number;
  outputTokens?: number;
  cacheReadTokens?: number;
  cacheCreationTokens?: number;
  durationSec?: number;
  // verify / test-integrity / plan-check (engine v3) — all carry `story` + `cum` (above/below).
  // `verdict`, `ok`, `reason` are already declared above (shared with gate/smoke/verdict/stop).
  deleted?: string[];
  modified?: string[];
  // plateau / rollback / handoff
  k?: number;
  toIter?: number;
  strike?: number;
  strikeBudget?: number;
  consecutive?: number;
  // quota
  resetAt?: string | null;
  waitSec?: number;
  resumeAt?: string;
  probe?: number;
  resetType?: 'five_hour' | 'weekly';
  // bmad
  target?: string;
  branch?: string;
  status?: string;
  epic?: string;
  index?: number;
  codegenOk?: boolean;
  lintOk?: boolean;
  testOk?: boolean;
  turn?: number;
  q?: string;
  a?: string;
  summary?: string;
  url?: string;
  rootCode?: number;
  passed?: boolean;
  verdict?: string;
  timedOut?: boolean;
  base?: string;
  pr?: string;
  scope?: string;
  mode?: string;
  stage?: string;
  // anything else (non-deterministic field order; unknown keys tolerated)
  [key: string]: unknown;
}

// checkpoint.json (§2)
export interface Checkpoint {
  updatedAt: string;
  stage: string;
  story: string | null;
  branch: string;
  mergeBase: string;
  cumUsd: number;
  resume: string;
  meta?: Record<string, unknown>;
}

// ─── Liveness heartbeat (`<stateDir>/activity.json`, PROTOCOL §1) ────────────
// A single beat the engine OVERWRITES every few seconds during a long agent step, so the UI can
// tell "actively working" from a hung loop between the coarse phase-boundary events in log.jsonl.
export interface Activity {
  /** ISO-8601 (UTC, `Z`) write time — freshness is `Date.now() - Date.parse(ts)`. */
  ts?: string;
  phase?: string;
  story?: string;
  /** seconds the current agent step has been running */
  elapsedSec: number;
  /** changed-file count in the repo work tree — a live "actually producing work" signal */
  dirty: number;
  pid?: number;
}

// ─── §6 Tauri command surface — the Delta channel union ─────────────────────
export type Delta =
  | { kind: 'snapshot'; state: RunState }
  | { kind: 'event'; event: RawEvent }
  | { kind: 'state'; state: RunState }
  | { kind: 'activity'; activity: Activity | null };

// §7 LoopDefinition (loops/<id>/loop.json) — minimal surface the frontend uses
export interface LoopDef {
  id: string;
  name: string;
  theme?: string;
  kind: 'generic' | 'external';
  stateDir: string;
  adapter: 'generic' | 'bmad' | 'custom';
}
