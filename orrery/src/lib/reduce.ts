// Orrery reducer — TS mirror of PROTOCOL.md §4. Same rules as the Rust side:
// pure, keyed, idempotent; cumUsd running-max (never additive); restState
// derived last. Reduces a list of raw events (+ optional checkpoint) into a
// RunState. Handles both generic core events and the BMAD superset (§2).

import type {
  Checkpoint,
  Group,
  Qa,
  RawEvent,
  RunState,
  SixPhase,
  WorkItem,
} from './types';

const COST_RATE_WINDOW = 12; // samples used for ratePerMin

export function initialState(loopId = 'unknown'): RunState {
  return {
    loopId,
    run: {
      status: 'idle',
      restState: null,
      pid: null,
      target: null,
      branch: null,
      mergeBase: 'main',
      cumUsd: 0,
      stage: null,
      stopPending: null,
      resumeCmd: null,
      startedAt: null,
      updatedAt: null,
    },
    groups: {},
    items: {},
    currentItem: null,
    phase: { name: null, label: null, sixPhase: null, model: null },
    cost: { cumUsd: 0, ceilingUsd: null, alertPct: null, series: [], ratePerMin: 0 },
    quota: {
      active: false,
      label: null,
      resetAt: null,
      resumeAt: null,
      resetType: null,
      probe: 0,
      waitSec: 0,
    },
    cache: { hitRatio: 0, warm: false },
    questions: [],
    verdicts: {},
    events: 0,
  };
}

// epic key derived from a story key like "3-4-semantic-search" -> "3"
export function epicOf(storyKey: string | undefined | null): string | null {
  if (!storyKey) return null;
  const m = /^(\d+)\b/.exec(storyKey);
  return m ? m[1] : null;
}

function num(...vals: unknown[]): number | undefined {
  for (const v of vals) if (typeof v === 'number' && !Number.isNaN(v)) return v;
  return undefined;
}

// map a story-status string (from events / sprint-status) to ItemStatus
function normStatus(s: string | undefined | null): WorkItem['status'] | null {
  if (!s) return null;
  const k = s.toLowerCase().trim();
  switch (k) {
    case 'backlog':
      return 'backlog';
    case 'ready':
    case 'ready-for-dev':
      return 'ready';
    case 'in-progress':
    case 'in_progress':
    case 'dev':
      return 'in-progress';
    case 'review':
      return 'review';
    case 'done':
    case 'merged':
      return 'done';
    case 'blocked':
      return 'blocked';
    case 'failed':
    case 'red':
      return 'failed';
    default:
      return null;
  }
}

function ensureGroup(state: RunState, id: string): Group {
  let g = state.groups[id];
  if (!g) {
    const ringIndex = Object.keys(state.groups).length;
    g = { id, status: 'in-progress', retroStatus: null, ringIndex };
    state.groups[id] = g;
  }
  return g;
}

function ensureItem(state: RunState, key: string, groupHint?: string | null): WorkItem {
  let it = state.items[key];
  if (!it) {
    const group = groupHint ?? epicOf(key);
    if (group) ensureGroup(state, group);
    // index within its group, for orbital placement
    const siblings = Object.values(state.items).filter((x) => x.group === group);
    it = {
      key,
      group,
      index: siblings.length,
      status: 'backlog',
      gate: null,
      smoke: null,
      pr: null,
      ghost: null,
      strikes: 0,
      strikeBudget: 3,
      certified: false,
      costAttributed: 0,
      lastEventTs: 0,
    };
    state.items[key] = it;
  } else if (groupHint && !it.group) {
    it.group = groupHint;
    ensureGroup(state, groupHint);
  }
  return it;
}

function pushCostSample(state: RunState, t: number, cum: number) {
  const series = state.cost.series;
  const last = series[series.length - 1];
  // idempotent: skip exact-duplicate sample (same t & cum)
  if (last && last.t === t && last.cum === cum) return;
  series.push({ t, cum });
  // ratePerMin from last N samples
  const window = series.slice(-COST_RATE_WINDOW);
  if (window.length >= 2) {
    const a = window[0];
    const b = window[window.length - 1];
    const dtMin = (b.t - a.t) / 60000;
    state.cost.ratePerMin = dtMin > 0 ? Math.max(0, (b.cum - a.cum) / dtMin) : 0;
  }
}

function bumpCum(state: RunState, ev: RawEvent, t: number) {
  const c = num(ev.cum, ev.cumUsd);
  if (c === undefined) return;
  // running-max, never additive
  const next = Math.max(state.run.cumUsd, c);
  state.run.cumUsd = next;
  state.cost.cumUsd = next;
  pushCostSample(state, t, next);
}

const PHASE_SIX: Record<string, SixPhase> = {
  'create-story': 'discover',
  'dev-story': 'execute',
  'dev-gate': 'verify',
  'code-review': 'verify',
  'browser-smoke': 'verify',
  smoke: 'verify',
  'pr-created': 'persist',
  'pr-merged': 'persist',
  pr: 'persist',
  stop: 'decide',
  next: 'decide',
};

function sixPhaseOf(label: string | undefined): SixPhase | null {
  if (!label) return null;
  const k = label.toLowerCase();
  for (const key of Object.keys(PHASE_SIX)) {
    if (k.startsWith(key)) return PHASE_SIX[key];
  }
  return null;
}

/**
 * Apply a single raw event. Pure-ish: mutates a working copy the caller owns.
 * `t` is ms-since-epoch the caller stamps (tests use line index × 1000).
 */
export function apply(state: RunState, ev: RawEvent, t: number): RunState {
  state.events += 1;
  state.run.updatedAt = state.run.updatedAt; // unchanged unless event carries it

  switch (ev.event) {
    // ─── core engine ─────────────────────────────────────────────────────
    case 'iter': {
      state.run.status = 'running';
      bumpCum(state, ev, t);
      // generic loop = a single item ("the goal")
      const key = state.run.target ?? state.loopId;
      const it = ensureItem(state, key);
      const pass = num(ev.pass);
      const fail = num(ev.fail);
      const total = num(ev.total);
      if (total !== undefined) {
        it.gate = {
          green: ev.green === true || fail === 0,
          pass: pass ?? 0,
          fail: fail ?? 0,
          total,
          baselinePass: num(ev.best, it.gate?.baselinePass) ?? 0,
        };
      }
      if (typeof ev.regress === 'number' && ev.regress > 0) it.strikes = ev.regress;
      it.status = it.gate?.green ? 'review' : 'in-progress';
      it.lastEventTs = t;
      state.currentItem = key;
      break;
    }
    case 'parse_error': {
      // tolerated; just liveness
      break;
    }
    case 'gate': {
      bumpCum(state, ev, t);
      const key = ev.story ?? state.currentItem ?? state.run.target ?? state.loopId;
      const it = ensureItem(state, key);
      it.gate = {
        green: ev.green === true,
        pass: num(ev.pass) ?? 0,
        fail: num(ev.fail) ?? 0,
        total: num(ev.total) ?? 0,
        baselinePass: num(ev.baselinePass) ?? 0,
        stages: ev.stages,
      };
      it.status = ev.green ? 'review' : 'in-progress';
      it.lastEventTs = t;
      state.phase.label = 'dev-gate';
      state.phase.sixPhase = 'verify';
      break;
    }
    case 'verdict': {
      const key = ev.item ?? state.currentItem ?? state.loopId;
      const it = ensureItem(state, key);
      state.verdicts[key] = {
        pass: ev.pass === true,
        failingCriteria: ev.failingCriteria ?? [],
        evidence: ev.evidence,
        nextAction: ev.nextAction,
        model: typeof ev.model === 'string' ? ev.model : undefined,
      };
      // §4.6: certified flips true on verdict{pass:true}
      it.certified = ev.pass === true;
      if (it.certified) it.status = 'done';
      // terraform the ghost: mark criteria met/failed
      if (it.ghost) {
        const failing = new Set(ev.failingCriteria ?? []);
        for (const c of it.ghost.criteria) c.met = !failing.has(c.text);
      }
      it.lastEventTs = t;
      break;
    }
    case 'model': {
      const m = ev.model;
      if (m === 'haiku' || m === 'sonnet' || m === 'opus') state.phase.model = m;
      if (typeof ev.phase === 'string') {
        state.phase.name = ev.phase;
        state.phase.label = ev.phase;
        state.phase.sixPhase = sixPhaseOf(ev.phase) ?? state.phase.sixPhase;
      }
      break;
    }
    case 'cost-alert': {
      bumpCum(state, ev, t);
      if (typeof ev.pct === 'number') state.cost.alertPct = ev.pct;
      if (typeof ev.ceiling === 'number') state.cost.ceilingUsd = ev.ceiling;
      break;
    }
    case 'cache': {
      if (typeof ev.hitRatio === 'number') state.cache.hitRatio = ev.hitRatio;
      if (typeof ev.warm === 'boolean') state.cache.warm = ev.warm;
      break;
    }
    case 'plateau': {
      const key = ev.item ?? state.currentItem;
      if (key) {
        const it = ensureItem(state, key);
        it.lastEventTs = t;
      }
      break;
    }
    case 'rollback': {
      const key = ev.item ?? state.currentItem ?? state.loopId;
      const it = ensureItem(state, key);
      it.strikes = num(ev.strike, it.strikes) ?? it.strikes;
      it.strikeBudget = num(ev.strikeBudget, it.strikeBudget) ?? it.strikeBudget;
      if (it.gate) it.gate.baselinePass = num(ev.bestPass, it.gate.baselinePass) ?? it.gate.baselinePass;
      it.status = 'in-progress';
      it.certified = false;
      it.lastEventTs = t;
      break;
    }
    case 'handoff': {
      state.run.status = 'handoff';
      break;
    }
    case 'phase-timeout': {
      if (typeof ev.label === 'string') {
        state.phase.label = ev.label;
        state.phase.sixPhase = sixPhaseOf(ev.label) ?? state.phase.sixPhase;
      }
      break;
    }

    // ─── quota ───────────────────────────────────────────────────────────
    case 'quota-hit': {
      bumpCum(state, ev, t);
      state.quota.active = true;
      state.quota.label = ev.label ?? state.quota.label;
      state.quota.resetAt = ev.resetAt ?? null;
      state.run.status = 'quota-wait';
      break;
    }
    case 'quota-wait': {
      bumpCum(state, ev, t);
      state.quota.active = true;
      state.quota.label = ev.label ?? state.quota.label;
      state.quota.waitSec = num(ev.waitSec) ?? state.quota.waitSec;
      state.quota.resumeAt = ev.resumeAt ?? state.quota.resumeAt;
      state.quota.probe = num(ev.probe) ?? state.quota.probe;
      if (ev.resetType === 'five_hour' || ev.resetType === 'weekly')
        state.quota.resetType = ev.resetType;
      else if (typeof ev.waitSec === 'number')
        // heuristic: > ~3h wait reads as a weekly (polar) night
        state.quota.resetType = ev.waitSec > 3 * 3600 ? 'weekly' : 'five_hour';
      state.run.status = 'quota-wait';
      break;
    }
    case 'quota-resume': {
      state.quota.active = false;
      state.quota.probe = num(ev.probe) ?? state.quota.probe;
      state.quota.waitSec = 0;
      state.quota.resumeAt = null;
      state.run.stopPending = null; // night is over; the mechanism re-engages
      state.run.status = 'running';
      break;
    }

    // ─── BMAD superset ─────────────────────────────────────────────────────
    case 'start': {
      // A new run begins. Per-run running-max → reset the high-water mark so the
      // final cumUsd reflects the *current* run (matches checkpoint.cumUsd). The
      // bmad log can concatenate runs whose cum restarts. Mirrors the Rust reducer
      // on_start (PROTOCOL §4.2). The cost series is NOT cleared (multi-run sawtooth).
      state.run.cumUsd = 0;
      state.cost.cumUsd = 0;
      state.quota.active = false;
      state.run.restState = null;
      state.run.stopPending = null; // a fresh run re-engages the mechanism
      state.run.status = 'running';
      if (typeof ev.target === 'string') {
        state.run.target = ev.target;
        const it = ensureItem(state, ev.target);
        it.gate = it.gate ?? {
          green: false,
          pass: 0,
          fail: 0,
          total: 0,
          baselinePass: num(ev.baselinePass) ?? 0,
        };
        if (it.gate && typeof ev.baselinePass === 'number')
          it.gate.baselinePass = ev.baselinePass;
        if (it.status === 'backlog') it.status = 'in-progress';
        it.lastEventTs = t;
        state.currentItem = ev.target;
      }
      if (typeof ev.branch === 'string') state.run.branch = ev.branch;
      break;
    }
    case 'story-start': {
      const key = ev.story;
      if (typeof key === 'string') {
        const group = ev.epic ?? epicOf(key);
        const it = ensureItem(state, key, group);
        const st = normStatus(ev.status);
        if (st) it.status = st;
        it.lastEventTs = t;
        state.run.target = key;
        state.currentItem = key;
        state.phase.label = 'create-story';
        state.phase.sixPhase = 'discover';
      }
      break;
    }
    case 'dev-gate': {
      bumpCum(state, ev, t);
      const key = ev.story ?? state.currentItem ?? state.loopId;
      const group = ev.epic ?? epicOf(key);
      const it = ensureItem(state, key, group);
      const stages = [];
      if (typeof ev.codegenOk === 'boolean')
        stages.push({ name: 'codegen', ok: ev.codegenOk, exit: ev.codegenOk ? 0 : 1 });
      if (typeof ev.lintOk === 'boolean')
        stages.push({ name: 'lint', ok: ev.lintOk, exit: ev.lintOk ? 0 : 1 });
      if (typeof ev.testOk === 'boolean')
        stages.push({ name: 'test', ok: ev.testOk, exit: ev.testOk ? 0 : 1 });
      it.gate = {
        green: ev.green === true,
        pass: num(ev.pass) ?? 0,
        fail: num(ev.fail) ?? 0,
        total: num(ev.total) ?? 0,
        baselinePass: num(ev.baselinePass) ?? 0,
        stages: stages.length ? stages : undefined,
      };
      const st = normStatus(ev.status);
      it.status = st ?? (ev.green ? 'review' : 'in-progress');
      it.lastEventTs = t;
      state.currentItem = key;
      state.phase.label = 'dev-gate';
      state.phase.sixPhase = 'verify';
      break;
    }
    case 'review-question': {
      upsertQa(state, 'review', num(ev.turn) ?? 0, ev.epic, (qa) => {
        qa.q = ev.q ?? qa.q;
      });
      state.phase.label = 'code-review';
      state.phase.sixPhase = 'verify';
      break;
    }
    case 'review-answer': {
      upsertQa(state, 'review', num(ev.turn) ?? 0, ev.epic, (qa) => {
        qa.a = ev.a ?? qa.a;
        qa.answeredBy = 'agent';
      });
      break;
    }
    case 'review-complete': {
      upsertQa(state, 'review', num(ev.turn) ?? 0, ev.epic, (qa) => {
        qa.summary = ev.summary ?? qa.summary;
      });
      break;
    }
    case 'retro-start': {
      const epic = ev.epic;
      if (epic) {
        const g = ensureGroup(state, epic);
        g.retroStatus = 'pending';
      }
      break;
    }
    case 'retro-question': {
      upsertQa(state, 'retro', num(ev.turn) ?? 0, ev.epic, (qa) => {
        qa.q = ev.q ?? qa.q;
      });
      break;
    }
    case 'retro-answer': {
      upsertQa(state, 'retro', num(ev.turn) ?? 0, ev.epic, (qa) => {
        qa.a = ev.a ?? qa.a;
        qa.answeredBy = 'agent';
      });
      break;
    }
    case 'retro-complete': {
      upsertQa(state, 'retro', num(ev.turn) ?? 0, ev.epic, (qa) => {
        qa.summary = ev.summary ?? qa.summary;
      });
      if (ev.epic) {
        const g = ensureGroup(state, ev.epic);
        g.retroStatus = 'done';
      }
      break;
    }
    case 'smoke-server': {
      state.phase.label = 'browser-smoke';
      state.phase.sixPhase = 'verify';
      break;
    }
    case 'smoke-iter': {
      const key = state.currentItem ?? state.run.target ?? state.loopId;
      const it = ensureItem(state, key);
      it.smoke = {
        iter: num(ev.iter) ?? 0,
        passed: ev.passed === true,
        verdict: typeof ev.verdict === 'string' ? ev.verdict : '',
        timedOut: ev.timedOut,
      };
      it.lastEventTs = t;
      state.phase.label = 'browser-smoke';
      state.phase.sixPhase = 'verify';
      break;
    }
    case 'pr-created': {
      const key = ev.story ?? state.currentItem ?? state.run.target ?? state.loopId;
      const it = ensureItem(state, key);
      it.pr = {
        url: typeof ev.url === 'string' ? ev.url : '',
        base: typeof ev.base === 'string' ? ev.base : 'develop',
        merged: false,
      };
      it.lastEventTs = t;
      state.phase.label = 'pr-created';
      state.phase.sixPhase = 'persist';
      break;
    }
    case 'pr-merged': {
      const key = ev.story ?? state.currentItem ?? state.run.target ?? state.loopId;
      const it = ensureItem(state, key);
      it.pr = {
        url: it.pr?.url ?? (typeof ev.pr === 'string' ? ev.pr : ''),
        base: typeof ev.base === 'string' ? ev.base : it.pr?.base ?? 'develop',
        merged: true,
      };
      it.status = 'done';
      it.lastEventTs = t;
      state.phase.label = 'pr-merged';
      state.phase.sixPhase = 'persist';
      break;
    }
    case 'cooperative-stop': {
      bumpCum(state, ev, t);
      state.run.status = 'stopped';
      if (ev.mode === 'phase' || ev.mode === 'story' || ev.mode === 'now')
        state.run.stopPending = ev.mode;
      if (typeof ev.stage === 'string') state.run.stage = ev.stage;
      if (typeof ev.branch === 'string') state.run.branch = ev.branch;
      break;
    }
    case 'stop': {
      bumpCum(state, ev, t);
      // generic stop carries bestPass + green; bmad stop carries ok
      const key = state.currentItem ?? state.run.target ?? state.loopId;
      if (state.items[key]) {
        const it = state.items[key];
        if (ev.green === true && it.gate) {
          it.gate.green = true;
          it.status = 'done';
        }
        if (typeof ev.bestPass === 'number' && it.gate)
          it.gate.baselinePass = Math.max(it.gate.baselinePass, ev.bestPass);
      }
      state.run.status = ev.ok === false ? 'error' : 'stopped';
      state.phase.label = 'stop';
      state.phase.sixPhase = 'decide';
      break;
    }

    default:
      // unknown event: counted (above), otherwise ignored
      break;
  }

  state.run.updatedAt = new Date(t).toISOString();
  return state;
}

function upsertQa(
  state: RunState,
  kind: Qa['kind'],
  turn: number,
  epic: string | undefined,
  mut: (qa: Qa) => void,
) {
  const id = `${kind}:${turn}:${epic ?? ''}`;
  let qa = state.questions.find((x) => x.id === id);
  if (!qa) {
    qa = { id, kind, turn, q: '', a: null, summary: null, answeredBy: null, epic };
    state.questions.push(qa);
  }
  mut(qa);
}

// §4.5 — derive restState last.
function deriveRestState(state: RunState): void {
  const items = Object.values(state.items);
  const allDoneMerged =
    items.length > 0 &&
    items.every((it) => it.status === 'done' && (it.pr ? it.pr.merged : true));

  if (state.run.status === 'handoff') {
    state.run.restState = 'handoff-beacon';
  } else if (state.quota.active) {
    state.run.restState = 'quota-frost';
  } else if (allDoneMerged && state.run.status !== 'running') {
    // a clean finish is a certified seal, not a banked ember — even when the run
    // ends with a `stop{ok:true}` (refines PROTOCOL §4.5 ordering: a completed
    // run reads "done", reserving the ember for a stop that left work unfinished).
    state.run.restState = 'certified-done';
  } else if (state.run.status === 'stopped' || state.run.status === 'error') {
    state.run.restState = 'stopped-ember';
  } else {
    state.run.restState = null;
  }
}

function applyCheckpoint(state: RunState, cp: Checkpoint): void {
  // running-max for cumUsd; authoritative for stage/branch/resume
  state.run.cumUsd = Math.max(state.run.cumUsd, cp.cumUsd ?? 0);
  state.cost.cumUsd = state.run.cumUsd;
  if (cp.stage) state.run.stage = cp.stage;
  if (cp.branch) state.run.branch = cp.branch;
  if (cp.mergeBase) state.run.mergeBase = cp.mergeBase;
  if (cp.resume) state.run.resumeCmd = cp.resume;
  if (cp.updatedAt) state.run.updatedAt = cp.updatedAt;
  if (cp.story && state.items[cp.story]) state.currentItem = cp.story;
}

/**
 * Reduce a full list of raw events (+ optional checkpoint) into a RunState.
 * Idempotent: re-applying the whole log yields identical state (running-max
 * cumUsd, keyed upserts). `t` defaults to (index × 1000) per PROTOCOL §3.
 */
export function reduce(
  events: RawEvent[],
  opts: { checkpoint?: Checkpoint; loopId?: string; baseTs?: number } = {},
): RunState {
  const state = initialState(opts.loopId ?? 'unknown');
  const base = opts.baseTs ?? 0;
  events.forEach((ev, i) => apply(state, ev, base + i * 1000));
  if (opts.checkpoint) applyCheckpoint(state, opts.checkpoint);
  deriveRestState(state);
  if (state.run.startedAt === null && events.length > 0)
    state.run.startedAt = new Date(base).toISOString();
  return state;
}

// Re-run derivation after an incremental apply (used by the live transport).
export function finalize(state: RunState): RunState {
  deriveRestState(state);
  return state;
}

export { deriveRestState, applyCheckpoint };
