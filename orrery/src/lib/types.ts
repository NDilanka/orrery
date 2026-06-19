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

// The four "not-running" rest palettes (+ null when running). §Design four states.
export type RestState =
  | 'certified-done'
  | 'stopped-ember'
  | 'quota-frost'
  | 'handoff-beacon'
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
  questions: Qa[];
  verdicts: Record<string, Verdict>; // by item key, latest
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
  // quota
  | 'quota-hit'
  | 'quota-wait'
  | 'quota-resume'
  // bmad superset
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

// ─── §6 Tauri command surface — the Delta channel union ─────────────────────
export type Delta =
  | { kind: 'snapshot'; state: RunState }
  | { kind: 'event'; event: RawEvent }
  | { kind: 'state'; state: RunState };

// §7 LoopDefinition (loops/<id>/loop.json) — minimal surface the frontend uses
export interface LoopDef {
  id: string;
  name: string;
  theme?: string;
  kind: 'generic' | 'external';
  stateDir: string;
  adapter: 'generic' | 'bmad' | 'custom';
}
