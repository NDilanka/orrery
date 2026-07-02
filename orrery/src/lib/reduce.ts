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

const COST_RATE_WINDOW = 8; // samples used for ratePerMin (kept in sync with reducer.rs RATE_WINDOW)

// The synthetic key a generic loop folds all its iterations into (matches reducer.rs "iter").
const GENERIC_ITEM_KEY = 'iter';

// Reducer-private bookkeeping that is NOT on the wire (mirrors reducer.rs struct fields). Stored
// as a non-enumerable property so it never appears in JSON / golden comparisons. Persists across
// incremental apply() calls (the live transport holds one RunState and feeds events one at a time).
interface Bookkeeping {
  // Generic-loop cost scoping (PROTOCOL §4.2): a stop ends a run; the next cum-bearing event
  // rebases the per-run running-max. Mirrors reducer.rs `run_ended`.
  runEnded: boolean;
}
function bk(state: RunState): Bookkeeping {
  let b = (state as unknown as { __bk?: Bookkeeping }).__bk;
  if (!b) {
    b = { runEnded: false };
    Object.defineProperty(state, '__bk', { value: b, enumerable: false, writable: true });
  }
  return b;
}

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
    metrics: null,
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
    // ringIndex = the epic number when numeric, else 0 (matches reducer.rs ensure_group).
    const ringIndex = /^\d+$/.test(id) ? parseInt(id, 10) : 0;
    g = { id, status: 'in-progress', retroStatus: null, ringIndex };
    state.groups[id] = g;
  }
  return g;
}

// Mirrors reducer.rs `item_mut`: a plain create (group null, index 0) — group/epic assignment is
// done explicitly by the specific handlers (start / story-start / dev-gate), never auto-derived
// here. A `groupHint` is an explicit assignment from such a handler.
function ensureItem(state: RunState, key: string, groupHint?: string | null): WorkItem {
  let it = state.items[key];
  if (!it) {
    it = {
      key,
      group: groupHint ?? null,
      index: 0,
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
    if (groupHint) ensureGroup(state, groupHint);
    state.items[key] = it;
  } else if (groupHint && !it.group) {
    it.group = groupHint;
    ensureGroup(state, groupHint);
  }
  return it;
}

function pushCostSample(state: RunState, t: number, cum: number) {
  const series = state.cost.series;
  // BUG-FIX #2 (same-timestamp collision): identify a slot by (t, cum), scanning the WHOLE
  // series — not just the last sample. Two genuinely-distinct events sharing an identical `t`
  // (same ms) carry different `cum`, so both are kept instead of the second clobbering the
  // first. A true re-apply re-produces the same (t, cum) pairs → upsert in place (idempotent).
  const existing = series.find((s) => s.t === t && s.cum === cum);
  if (existing) {
    existing.cum = cum;
  } else {
    series.push({ t, cum });
  }
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
  // BUG-FIX #1 (generic cost scoping): the running-max is scoped to the CURRENT run. A stop sets
  // runEnded; the first cum after it rebases the running-max to that run's fresh high-water,
  // instead of carrying a prior run's high-water forward. bmad's `start`-reset still applies.
  let next: number;
  if (bk(state).runEnded) {
    next = c;
    bk(state).runEnded = false;
  } else {
    next = Math.max(state.run.cumUsd, c); // running-max, never additive
  }
  state.run.cumUsd = next;
  state.cost.cumUsd = next;
  // series tracks the raw per-event cum (the sawtooth), not the running-max.
  pushCostSample(state, t, c);
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

  switch (ev.event) {
    // ─── core engine ─────────────────────────────────────────────────────
    case 'iter': {
      bumpCum(state, ev, t);
      state.run.status = 'running';
      state.phase.sixPhase = 'execute';
      // Generic loops fold all iterations into one synthetic item (key matches reducer.rs).
      const pass = num(ev.pass) ?? 0;
      const total = num(ev.total) ?? 0;
      const green = pass > 0 && total > 0 && pass >= total;
      const best = num(ev.best) ?? 0;
      const regress = num(ev.regress) ?? 0;
      const it = ensureItem(state, GENERIC_ITEM_KEY);
      it.index = 0;
      it.status = green ? 'done' : 'in-progress';
      it.strikes = regress;
      it.costAttributed = state.run.cumUsd;
      it.gate = {
        green,
        pass,
        fail: Math.max(0, num(ev.fail) ?? total - pass),
        total,
        baselinePass: best,
      };
      it.lastEventTs = t;
      state.currentItem = GENERIC_ITEM_KEY;
      break;
    }
    case 'parse_error': {
      // tolerated; just liveness
      break;
    }
    case 'gate': {
      bumpCum(state, ev, t);
      state.phase.sixPhase = 'verify';
      const key = ev.story ?? GENERIC_ITEM_KEY;
      const it = ensureItem(state, key);
      it.gate = {
        green: ev.green === true,
        pass: num(ev.pass) ?? 0,
        fail: num(ev.fail) ?? 0,
        total: num(ev.total) ?? 0,
        baselinePass: num(ev.baselinePass) ?? 0,
        stages: ev.stages,
      };
      it.costAttributed = state.run.cumUsd;
      it.lastEventTs = t;
      state.currentItem = key;
      break;
    }
    case 'verdict': {
      // Rust requires an `item` key (no fallback). Mirror that.
      if (typeof ev.item !== 'string') break;
      const key = ev.item;
      const it = ensureItem(state, key);
      const pass = ev.pass === true;
      state.verdicts[key] = {
        pass,
        failingCriteria: ev.failingCriteria ?? [],
        evidence: ev.evidence,
        nextAction: ev.nextAction,
        model: typeof ev.model === 'string' ? ev.model : undefined,
      };
      // §4.6: certified flips true on verdict{pass:true}. (Lifecycle status comes from other
      // events — Rust does not set status here.)
      if (pass) it.certified = true;
      it.lastEventTs = t;
      break;
    }
    case 'model': {
      // mirrors reducer.rs on_model: sets phase.name + sixPhase + model, never phase.label.
      if (typeof ev.phase === 'string') {
        state.phase.name = ev.phase;
        state.phase.sixPhase = sixPhaseOf(ev.phase);
      }
      const m = ev.model;
      if (m === 'haiku' || m === 'sonnet' || m === 'opus') state.phase.model = m;
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
    case 'token-usage': {
      // Engine-v3 per-call token + cache telemetry (~22% of a real BMAD log). Feeds the SAME
      // two fields the documented `cache` event feeds, last write wins — mirrors 'cache' above
      // exactly (mirrors reducer.rs on_token_usage). `costUsd` is a per-call DELTA (not a
      // running cum) and the `cum*` fields are cumulative TOKEN counts, not USD, so neither fits
      // bumpCum's running-max-of-cum contract or cost.series (which assumes cumulative USD
      // samples for ratePerMin); wiring either in would corrupt that logic. Intentionally left
      // unwired.
      if (typeof ev.hitRatio === 'number') state.cache.hitRatio = ev.hitRatio;
      if (typeof ev.warm === 'boolean') state.cache.warm = ev.warm;
      break;
    }
    case 'plateau': {
      // Rust requires an `item` and sets its strike count to `k`.
      if (typeof ev.item === 'string') {
        const it = ensureItem(state, ev.item);
        it.strikes = num(ev.k) ?? 0;
      }
      break;
    }
    case 'rollback': {
      // Rust requires an `item`; sets only strikes + strikeBudget (defaults 0 when absent).
      if (typeof ev.item === 'string') {
        const it = ensureItem(state, ev.item);
        it.strikes = num(ev.strike) ?? 0;
        it.strikeBudget = num(ev.strikeBudget) ?? 0;
      }
      break;
    }
    case 'handoff': {
      state.run.status = 'handoff';
      break;
    }
    case 'phase-timeout': {
      // Rust sets only phase.label (not sixPhase).
      if (typeof ev.label === 'string') state.phase.label = ev.label;
      break;
    }

    // ─── core v3: run-quality summary ──────────────────────────────────────
    case 'metrics': {
      // A run-quality fold of the event stream, emitted once at stop. Idempotent
      // (last one wins); itersToGreen/costToGreen are null when never green.
      // Mirrors reducer.rs on_metrics — same field-by-field defaults so goldens agree.
      state.metrics = {
        firstTryGreen: ev.firstTryGreen === true,
        itersToGreen: num(ev.itersToGreen) ?? null,
        costToGreen: num(ev.costToGreen) ?? null,
        rollbacks: num(ev.rollbacks) ?? 0,
        regressionRate: num(ev.regressionRate) ?? 0,
        totalIters: num(ev.totalIters) ?? 0,
        totalCost: num(ev.totalCost) ?? 0,
        finalGreen: ev.finalGreen === true,
      };
      break;
    }

    // ─── quota ───────────────────────────────────────────────────────────
    case 'quota-hit': {
      bumpCum(state, ev, t);
      state.quota.active = true;
      state.run.status = 'quota-wait';
      // Rust overwrites label/resetAt straight from the event (null when absent).
      state.quota.label = typeof ev.label === 'string' ? ev.label : null;
      state.quota.resetAt = typeof ev.resetAt === 'string' ? ev.resetAt : null;
      break;
    }
    case 'quota-wait': {
      bumpCum(state, ev, t);
      state.quota.active = true;
      state.run.status = 'quota-wait';
      state.quota.label = typeof ev.label === 'string' ? ev.label : null;
      state.quota.resumeAt = typeof ev.resumeAt === 'string' ? ev.resumeAt : null;
      state.quota.waitSec = num(ev.waitSec) ?? 0;
      state.quota.probe = num(ev.probe) ?? 0;
      // resetType ONLY from the explicit field; keep prior on absence (no waitSec heuristic —
      // reducer.rs has none, and a heuristic would drift the goldens).
      if (ev.resetType === 'five_hour' || ev.resetType === 'weekly')
        state.quota.resetType = ev.resetType;
      break;
    }
    case 'quota-resume': {
      state.quota.active = false;
      state.quota.probe = num(ev.probe) ?? state.quota.probe;
      // night is over: clear the wait/resume countdown and re-engage (mirrors reducer.rs).
      state.quota.waitSec = 0;
      state.quota.resumeAt = null;
      state.run.stopPending = null;
      state.run.status = 'running';
      break;
    }

    // ─── BMAD superset ─────────────────────────────────────────────────────
    case 'engine-start': {
      // Heartbeat emitted before the slow preflight so the UI shows life immediately. Just mark
      // running; the per-run cost reset + target wiring happen on the subsequent `start`.
      // Mirrors reducer.rs on_engine_start.
      state.run.status = 'running';
      break;
    }
    case 'start': {
      // A new run begins. Per-run running-max → reset the high-water mark so the final cumUsd
      // reflects the *current* run (matches checkpoint.cumUsd). Mirrors reducer.rs on_start
      // (PROTOCOL §4.2). The cost series is NOT cleared (multi-run sawtooth). Kept minimal to
      // match Rust: no gate/status/lastEventTs side-effects here.
      state.run.cumUsd = 0;
      state.cost.cumUsd = 0;
      bk(state).runEnded = false; // explicit start supersedes any pending stop-rebase
      state.run.status = 'running';
      state.quota.active = false;
      state.run.restState = null;
      if (typeof ev.target === 'string') {
        state.run.target = ev.target;
        state.currentItem = ev.target;
        const epic = epicOf(ev.target);
        const it = ensureItem(state, ev.target);
        if (epic) {
          ensureGroup(state, epic);
          if (it.group == null) it.group = epic;
        }
        if (typeof ev.baselinePass === 'number' && it.gate)
          it.gate.baselinePass = ev.baselinePass;
      }
      if (typeof ev.branch === 'string') state.run.branch = ev.branch;
      break;
    }
    case 'story-start': {
      const key = ev.story;
      if (typeof key === 'string') {
        // explicit epic field, else derive from key (mirrors reducer.rs on_story_start).
        const epic = ev.epic ?? epicOf(key);
        if (epic) ensureGroup(state, epic);
        const it = ensureItem(state, key);
        it.lastEventTs = t;
        it.index = num(ev.index) ?? 0;
        it.group = epic ?? null;
        // story-start moves it into flight; don't downgrade a finished item.
        const st = normStatus(ev.status) ?? 'in-progress';
        if (it.status !== 'done') it.status = st;
        state.currentItem = key;
        state.run.target = key;
      }
      break;
    }
    case 'dev-gate': {
      bumpCum(state, ev, t);
      state.phase.sixPhase = 'verify';
      // Rust returns early when `story` is absent; mirror that (all dev-gates carry a story).
      if (typeof ev.story !== 'string') break;
      const key = ev.story;
      const group = epicOf(key);
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
      it.costAttributed = state.run.cumUsd;
      if (it.group == null) it.group = group;
      // dev-gate green only *claims* green; status from the event drives lifecycle, but never
      // downgrade an item already Done (mirrors reducer.rs on_dev_gate).
      const st = normStatus(ev.status);
      if (st && it.status !== 'done') it.status = st;
      it.lastEventTs = t;
      state.currentItem = key;
      break;
    }
    case 'review-question': {
      // review QA is keyed without an epic (Rust passes None); no phase side-effects.
      upsertQa(state, 'review', num(ev.turn) ?? 0, undefined, (qa) => {
        qa.q = typeof ev.q === 'string' ? ev.q : qa.q;
      });
      break;
    }
    case 'review-answer': {
      upsertQa(state, 'review', num(ev.turn) ?? 0, undefined, (qa) => {
        qa.a = typeof ev.a === 'string' ? ev.a : qa.a;
        qa.answeredBy = 'agent';
      });
      break;
    }
    case 'review-complete': {
      // A concluded review is NOT an open decision. The completion event's `turn` is
      // one past the last Q&A turn, so upserting here minted a phantom unanswered
      // question (empty `q`, `a == null`) that the Decision Chamber then rendered
      // forever as "(awaiting question text…)". The carried `summary` is not rendered
      // anywhere, so we drop it entirely. (mirrors reducer.rs on_review_complete)
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
        qa.q = typeof ev.q === 'string' ? ev.q : qa.q;
      });
      break;
    }
    case 'retro-answer': {
      upsertQa(state, 'retro', num(ev.turn) ?? 0, ev.epic, (qa) => {
        qa.a = typeof ev.a === 'string' ? ev.a : qa.a;
        qa.answeredBy = 'agent';
      });
      break;
    }
    case 'retro-complete': {
      // Mark the epic's retro done. Do NOT upsert a QA here: the completion `turn` is
      // past the last retro question, so it would mint a phantom unanswered card (same
      // bug as review-complete above). The carried `summary` is unused. (mirrors
      // reducer.rs on_retro_complete)
      if (typeof ev.epic === 'string') {
        const g = state.groups[ev.epic];
        if (g) g.retroStatus = 'done';
      }
      break;
    }
    case 'smoke-server': {
      // informational; ignored by the reducer (mirrors reducer.rs).
      break;
    }
    case 'smoke-iter': {
      state.phase.sixPhase = 'verify';
      // attach to the current item if one is known (Rust does not fall back to loopId here).
      const key = state.currentItem;
      if (key != null) {
        const it = ensureItem(state, key);
        it.smoke = {
          iter: num(ev.iter) ?? 0,
          passed: ev.passed === true,
          verdict: typeof ev.verdict === 'string' ? ev.verdict : '',
          timedOut: ev.timedOut,
        };
        it.lastEventTs = t;
      }
      break;
    }
    case 'pr-created': {
      state.phase.sixPhase = 'persist';
      const key = ev.story ?? state.currentItem;
      if (key == null) break; // Rust does nothing without a story or current item
      const it = ensureItem(state, key);
      it.pr = {
        url: typeof ev.url === 'string' ? ev.url : '',
        base: typeof ev.base === 'string' ? ev.base : 'develop',
        merged: it.pr?.merged ?? false,
      };
      it.lastEventTs = t;
      break;
    }
    case 'pr-merged': {
      state.phase.sixPhase = 'persist';
      const key = ev.story ?? state.currentItem;
      if (key == null) break;
      const it = ensureItem(state, key);
      const prId = typeof ev.pr === 'string' ? ev.pr : '';
      if (it.pr) {
        it.pr.merged = true;
        // keep the pr-created URL; the merge event's `pr` only fills it when none was set.
        if (it.pr.url === '' && prId !== '') it.pr.url = prId;
        if (typeof ev.base === 'string') it.pr.base = ev.base;
      } else {
        it.pr = {
          url: prId,
          base: typeof ev.base === 'string' ? ev.base : 'develop',
          merged: true,
        };
      }
      it.status = 'done';
      it.lastEventTs = t;
      break;
    }
    case 'cooperative-stop': {
      bumpCum(state, ev, t);
      // cooperative-stop also ends the current run (BUG-FIX #1 cost scoping).
      bk(state).runEnded = true;
      state.run.status = 'stopped';
      state.phase.sixPhase = 'decide';
      if (typeof ev.stage === 'string') state.run.stage = ev.stage;
      if (typeof ev.branch === 'string') state.run.branch = ev.branch;
      // story may be null between stories — only set target for a real story (mirrors Rust).
      if (typeof ev.story === 'string') state.run.target = ev.story;
      break;
    }
    case 'stop': {
      bumpCum(state, ev, t);
      // a stop ends the current run (BUG-FIX #1): the next cum-bearing event rebases the
      // per-run running-max. Mirrors reducer.rs on_stop.
      bk(state).runEnded = true;
      // BMAD `stop` carries {ok}; generic `stop` carries {green}. `ok:false` is BMAD's failure
      // signal (a halted/blocked session — gate red, regression, merge failure, ...): it maps to
      // status 'error'. `ok` absent (generic loops) or `ok:true` keeps today's 'stopped'.
      // Because the reducer is sequential, this is safe even mid-log: the very next event of a
      // healthy run is 'engine-start'/'start' for the NEXT session, which resets status to
      // 'running' before a human ever sees it. Only a log that truly ENDS on `stop{ok:false}`
      // surfaces 'error' (→ restState 'failed-dark'). `cooperative-stop` is a distinct event
      // that never carries `ok`, so a user-requested brake is never reclassified as an error.
      state.run.status = ev.ok === false ? 'error' : 'stopped';
      state.phase.sixPhase = 'decide';
      state.phase.label = 'stop';
      // A green generic stop seals the synthetic "iter" item.
      if (ev.green === true) {
        const it = state.items[GENERIC_ITEM_KEY];
        if (it) {
          it.status = 'done';
          if (it.gate) it.gate.green = true;
        }
      }
      break;
    }

    default:
      // unknown event: counted (above), otherwise ignored
      break;
  }

  // NB: run.updatedAt is intentionally NOT synthesized per-event (matches reducer.rs, which
  // only sets it from checkpoint.json). The synthetic line-index timestamp is not meaningful.
  return state;
}

function upsertQa(
  state: RunState,
  kind: Qa['kind'],
  turn: number,
  epic: string | undefined,
  mut: (qa: Qa) => void,
) {
  // id format must match reducer.rs upsert_qa: `${kind}-${turn}-${epic||''}`.
  const id = `${kind}-${turn}-${epic ?? ''}`;
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

  // Ordering: failed-dark → handoff → quota → certified-done(&& not running) → stopped → null.
  if (state.run.status === 'error') {
    // a crashed run (stop{ok:false}) needs attention most — outranks every other rest state,
    // checked before handoff/quota/certified-done.
    state.run.restState = 'failed-dark';
  } else if (state.run.status === 'handoff') {
    state.run.restState = 'handoff-beacon';
  } else if (state.quota.active) {
    state.run.restState = 'quota-frost';
  } else if (allDoneMerged && state.run.status !== 'running') {
    // a clean finish is a certified seal, not a banked ember — even when the run
    // ends with a `stop{ok:true}` (refines PROTOCOL §4.5 ordering: a completed
    // run reads "done", reserving the ember for a stop that left work unfinished).
    state.run.restState = 'certified-done';
  } else if (state.run.status === 'stopped') {
    state.run.restState = 'stopped-ember';
  } else {
    state.run.restState = null;
  }
}

function applyCheckpoint(state: RunState, cp: Checkpoint): void {
  // Mirrors reducer.rs apply_checkpoint: running-max cumUsd; authoritative for stage/branch/
  // mergeBase/resume/updatedAt; a non-null `story` sets run.target.
  if (cp.updatedAt) state.run.updatedAt = cp.updatedAt;
  if (cp.stage) state.run.stage = cp.stage;
  if (typeof cp.story === 'string') state.run.target = cp.story;
  if (cp.branch) state.run.branch = cp.branch;
  if (cp.mergeBase) state.run.mergeBase = cp.mergeBase;
  if (typeof cp.cumUsd === 'number' && cp.cumUsd > state.run.cumUsd) {
    state.run.cumUsd = cp.cumUsd;
    state.cost.cumUsd = cp.cumUsd;
  }
  if (cp.resume) state.run.resumeCmd = cp.resume;
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
  // `t` is line-index×1000 (PROTOCOL §3), unless the event carries a test-only `_t` override
  // (used by the series-collision fixture to force two events onto the same millisecond). Real
  // logs never carry `_t`; both reducers honor it identically so the goldens stay in lockstep.
  events.forEach((ev, i) => {
    const t = typeof ev._t === 'number' ? ev._t : base + i * 1000;
    apply(state, ev, t);
  });
  if (opts.checkpoint) applyCheckpoint(state, opts.checkpoint);
  deriveRestState(state);
  // NB: run.startedAt is intentionally NOT synthesized from the synthetic baseTs (matches
  // reducer.rs reduce_events, which leaves it null). It carries no meaning under index×1000.
  return state;
}

// Re-run derivation after an incremental apply (used by the live transport).
export function finalize(state: RunState): RunState {
  deriveRestState(state);
  return state;
}

export { deriveRestState, applyCheckpoint };
