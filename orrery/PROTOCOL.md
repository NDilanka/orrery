# Orrery Loop Protocol — canonical contract (v1)

This is the **single source of truth** for the wire shapes shared between the Rust core
(`src-tauri/src/model.rs`, `reducer.rs`) and the Svelte frontend (`src/lib/types.ts`,
`reduce.ts`). Both sides MUST match these names exactly. When in doubt, this file wins.

Field naming: **`camelCase` on the wire** (JSON). Rust structs use `#[serde(rename_all = "camelCase")]`.
TS interfaces use the same names verbatim.

---

## 1. Files a loop emits (per `stateDir`)

| File | Shape | Notes |
|---|---|---|
| `log.jsonl` | append-only, one compact JSON object per line | the event stream. **Field order is non-deterministic** — key on `event`, never position. A writer may leave a trailing partial (unterminated) line; the tailer holds it until the `\n` arrives. |
| `checkpoint.json` | single JSON object | resume state. |
| `activity.json` | single JSON object, **overwritten in place** | LIVENESS heartbeat. The engine re-stamps it every ~12s DURING a long agent step (a 30-min `dev-story` is one `claude` call that emits NO `log.jsonl` line until its gate), so a watcher can tell "actively working" from a hung loop. Shape: `{ ts, phase, story, elapsedSec, dirty, pid }` (`ts` ISO-8601 UTC; `dirty` = changed-file count). Written atomically (temp + rename). State, not event — it never enters `log.jsonl` or its replay cap. |
| `STOP` | text `phase` \| `story` \| `now` | cooperative stop request; the orchestrator deletes it when honored. |
| `answer.json` (stretch) | `{ qid, kind, epic?, a }` | UI→engine answer inbox (inert until the engine reads it). |

External loops may also write a `*-run.out` text transcript; the tailer parses a few markers
from it (see §5) but `log.jsonl` is authoritative.

---

## 2. `log.jsonl` event types

Every object has a string `event`. **Core** events any loop should emit; **BMAD** events are a
superset the `bmad` adapter understands. Unknown events are logged but ignored by the reducer.
All numeric costs are USD. Timestamps are ISO-8601 strings unless noted.

### Core (generic engine — see `.loop/log.jsonl`)
```
{ "event": "iter",  "iter": int, "cost": float, "cum": float, "pass": int, "total": int,
  "best": int, "changed": bool, "stale": int, "plateau": int, "regress": int,
  "action": "stop"|"rollback"|"continue", "reason": string }
{ "event": "stop",  "reason": string, "green": bool, "iter": int, "cum": float, "bestPass": int }
{ "event": "parse_error", "iter": int }
```

### Core (engine v2 additions — emit when present)
```
{ "event": "gate",      "story"?: string, "cum": float, "green": bool, "pass": int, "fail": int,
  "total": int, "baselinePass"?: int, "stages"?: [ { "name": string, "ok": bool, "exit": int } ] }
{ "event": "verdict",   "item": string, "pass": bool, "failingCriteria": [string],
  "evidence"?: string, "nextAction"?: string, "model"?: string }   // verifier subagent
{ "event": "model",     "phase": string, "model": "haiku"|"sonnet"|"opus", "costPerTurn"?: float }
{ "event": "cost-alert","pct": 50|80|100, "cum": float, "ceiling": float }
{ "event": "cache",     "hitRatio": float, "warm": bool }
{ "event": "plateau",   "item"?: string, "k": int }                 // drift/plateau detected
{ "event": "rollback",  "item": string, "toIter": int, "bestPass": int, "strike": int, "strikeBudget": int }
{ "event": "handoff",   "item"?: string, "reason": string, "consecutive"?: int }  // raise the beacon
{ "event": "phase-timeout", "label": string, "timeoutSec": int }
```

### Core (engine v3 additions — emit when present)
Additive only; all behind default-off engine flags. **Unknown events are logged-but-ignored
by the reducer**, so an older reducer skips these and a default-config run never emits them —
fully backward-compatible.
```
{ "event": "metrics", "firstTryGreen": bool, "itersToGreen": int|null, "costToGreen": float|null,
  "rollbacks": int, "regressionRate": float, "totalIters": int, "totalCost": float, "finalGreen": bool }
// emitted ONCE at stop when engine.metrics.emit is on; a run-quality fold of the event stream
// (loop.metrics.compute_metrics). first-try-green + iters/cost-to-green replace pass@k for loops.
// Idempotent (last write wins) — safe even if an engine emits it repeatedly (e.g. every iter)
// instead of once at stop: each event is a full recomputed snapshot, never a partial merge.

{ "event": "token-usage", "phase": string, "story"?: string, "model": string, "input": int,
  "output": int, "cacheRead": int, "cacheCreation": int, "hitRatio": float, "warm": bool,
  "costUsd": float, "cumInput": int, "cumOutput": int, "cumCacheRead": int, "cumCacheCreation": int }
// per-call token + cache telemetry (ResilientRunner._record_usage) — ~22% of a real BMAD log.
// NOT gated by an engine flag like `metrics`: it fires whenever the underlying agent result
// carries usage telemetry (real `claude` CLI calls do; mock/text-format runners emit nothing).
// Tokens are the real Max-**subscription** meter (USD is not); `cum*` are cumulative TOKEN
// counts for the run, NOT dollars. The reducer feeds only `hitRatio`/`warm` into `cache` — the
// SAME two fields the `cache` event feeds, last write wins. `costUsd` is a per-call DELTA (like
// `iter.cost`, not `iter.cum`), and `cum*` are token counts, not USD, so neither fits `cumUsd`'s
// running-max contract or the `cost.series` (built from cumulative USD samples for ratePerMin) —
// both are intentionally left unwired to avoid corrupting that logic.
```

### Quota (engine v2 — see BMAD)
```
{ "event": "quota-hit",    "label": string, "cum": float, "resetAt": iso|null }
{ "event": "quota-wait",   "label": string, "cum": float, "waitSec": int, "resumeAt": iso,
  "probe": int, "resetType"?: "five_hour"|"weekly" }
{ "event": "quota-resume", "label": string, "probe": int }
```

### BMAD adapter (the engine writes `.loop/log.jsonl`; `bmad-log.jsonl` is a recorded fixture)
```
{ "event":"engine-start",     "mergeBase"?: string }   // heartbeat: FIRST line, before preflight → reducer sets status 'running' within ~1s of spawn
{ "event":"start",            "target": string, "branch": string, "baselinePass": int }
{ "event":"story-start",      "story": string, "status": string, "epic"?: string, "index"?: int }
{ "event":"dev-gate",         "story": string, "cum": float, "green": bool, "pass": int, "fail": int,
  "total": int, "baselinePass": int, "status": string, "codegenOk": bool, "lintOk": bool, "testOk": bool }
{ "event":"gate-retry",       "attempt": int, "retries": int, "fail": int, "maxFail": int }  // flaky-SHAPED red gate (codegen+lint green, ≤maxFail failing tests) being re-run before it's trusted
{ "event":"review-question",  "turn": int, "q": string, "story"?: string }
{ "event":"review-answer",    "turn": int, "a": string }
{ "event":"review-complete",  "turn": int, "summary": string }
{ "event":"retro-start"|"retro-question"|"retro-answer"|"retro-complete", "epic": string, "turn"?: int, ... }
{ "event":"smoke-server",     "url": string, "rootCode": int }
{ "event":"smoke-iter",       "iter": int, "passed": bool, "verdict": string, "timedOut"?: bool }
{ "event":"pr-created",       "story": string, "branch": string, "base": string, "url": string }
{ "event":"pr-merged",        "story": string, "base": string, "pr": string }
{ "event":"cooperative-stop", "scope": string, "mode": string, "stage": string, "story": string|null,
  "branch": string, "cum": float }
{ "event":"stop",             "ok": bool, "reason": string, "cum": float }
```

### `checkpoint.json`
```
{ "updatedAt": iso, "stage": string, "story": string|null, "branch": string,
  "mergeBase": string, "cumUsd": float, "resume": string }   // resume = a shell command string
```

---

## 3. Universal `RunState` (the reduced client model)

Produced by the reducer from a loop's events + checkpoint + (BMAD) sprint-status.yaml. Both Rust
and TS define this shape identically.

```ts
type RunStatus = 'idle'|'running'|'stopping'|'stopped'|'quota-wait'|'handoff'|'error';
type SixPhase  = 'discover'|'assemble'|'execute'|'verify'|'persist'|'decide';
type ItemStatus= 'backlog'|'ready'|'in-progress'|'review'|'done'|'blocked'|'failed';
type RestState = 'certified-done'|'stopped-ember'|'quota-frost'|'handoff-beacon'|'failed-dark'|null; // §Design five states

interface RunState {
  loopId: string;
  run: {
    status: RunStatus;
    restState: RestState;            // which "not-running" palette, if any
    pid: number|null;
    target: string|null;             // current work item / goal
    branch: string|null;
    mergeBase: string;
    cumUsd: number;                  // RUNNING-MAX of every cum/cumUsd seen (never additive)
    stage: string|null;              // from checkpoint
    stopPending: 'phase'|'story'|'now'|null;
    resumeCmd: string|null;
    startedAt: string|null;          // ISO ts of the most recent run-starting event (bmad: `start`/
                                      // `engine-start`; generic: the first cum-bearing event after a
                                      // run_ended rebase, or the very first one — §4.2 boundary)
    lastEventAt: string|null;        // ISO ts of the last applied log event (every event, unconditionally)
    updatedAt: string|null;          // from checkpoint.json only, NOT synthesized per-event
  };
  groups: Record<string, Group>;     // epics (rings). generic loops may have 0-1 group.
  items: Record<string, WorkItem>;   // stories / iterations-as-one-item
  currentItem: string|null;
  phase: { name: string|null; label: string|null; sixPhase: SixPhase|null; model: 'haiku'|'sonnet'|'opus'|null };
  cost: { cumUsd: number; ceilingUsd: number|null; alertPct: number|null;   // last threshold crossed
          series: { t: number; cum: number }[]; ratePerMin: number };
  quota: { active: boolean; label: string|null; resetAt: string|null; resumeAt: string|null;
           resetType: 'five_hour'|'weekly'|null; probe: number; waitSec: number };
  cache: { hitRatio: number; warm: boolean };
  questions: Qa[];
  verdicts: Record<string, Verdict>; // by item key, latest
  events: number;                    // count (raw events kept in a side buffer/drawer, not here)
}

interface Group   { id: string; status: 'backlog'|'in-progress'|'done'; retroStatus: 'optional'|'done'|'pending'|null; ringIndex: number; }
interface WorkItem{
  key: string; group: string|null; index: number; status: ItemStatus;
  gate: { green: boolean; pass: number; fail: number; total: number; baselinePass: number;
          stages?: { name: string; ok: boolean; exit: number }[] } | null;
  smoke: { iter: number; passed: boolean; verdict: string; timedOut?: boolean } | null;
  pr: { url: string; base: string; merged: boolean } | null;
  ghost?: { criteria: { text: string; met: boolean }[] } | null; // frozen AC contract
  strikes: number; strikeBudget: number;
  certified: boolean;               // verifier sealed it (vs claimed-green)
  costAttributed: number;
  lastEventTs: number;              // for sprint-status vs live-event conflict resolution
}
interface Qa     { id: string; kind: 'review'|'retro'; turn: number; q: string; a: string|null;
                   summary: string|null; answeredBy: 'agent'|'ui'|null; epic?: string; }
interface Verdict{ pass: boolean; failingCriteria: string[]; evidence?: string; nextAction?: string; model?: string; }
```

---

## 4. Reducer rules (MUST hold on both sides)

1. **Pure, keyed, idempotent.** `apply(state, event) -> state`. Re-applying the full log twice yields
   identical state. Items keyed by `key`, groups by `id`, QA by `kind+turn+(epic||'')`.
2. **`cumUsd` is running-max**, never additive: `cumUsd = max(cumUsd, event.cum ?? event.cumUsd ?? 0)`.
   **Reset the running-max (and `cost.cumUsd`) to 0 on each `start` event** — the bmad log can
   concatenate multiple runs whose `cum` restarts, and the displayed cumUsd must reflect the *current*
   run so it matches `checkpoint.cumUsd` (e.g. the bmad fixture → 26.75, the last run's high-water, not
   the whole-file 75.23). The cost `series` is NOT cleared on `start` (it keeps the multi-run sawtooth).
   `start` also clears `quota.active` and `restState`.
3. **Cost series** appends a sample `{t, cum}` whenever a `cum`-bearing event arrives; `ratePerMin`
   is derived from the last ~N samples. `t` is ms since epoch (caller stamps; in tests use the line index ×1000).
4. **Status authority:** sprint-status.yaml is authoritative for items NOT currently in flight; a live
   event for an item overrides yaml if `item.lastEventTs` is newer. The current item's status comes from events.
5. **restState** is derived last, in this precedence (highest first): `status:'error'`→`failed-dark`;
   `handoff`→`handoff-beacon`; quota active→`quota-frost`; all items done & merged (and not running)→
   `certified-done`; `stop{ok:true}`/cooperative-stop (status `'stopped'`)→`stopped-ember`; else `null` (running).
   `failed-dark` outranks every other rest state — a crashed run needs attention most — so it is checked
   FIRST, even before `handoff`/`quota`/`certified-done`.
6. **certified** flips true on a `verdict{pass:true}` for that item; a `dev-gate`/`gate green` only sets *claimed* green.
7. **`RunStatus.error`** comes from a BMAD `stop{ok:false}` (a halted/blocked session — gate red, regression,
   merge failure, spin-guard, ...). `stop.ok` absent (generic loops, which carry `green` instead) or
   `ok:true` keeps the existing `'stopped'` status. Because the reducer is sequential, this is safe even
   mid-log: the very next event of a healthy multi-session run is `engine-start`/`start`, which resets
   status to `'running'` before a human ever observes the transient `'error'`. Only a log that truly ENDS
   on `stop{ok:false}` surfaces `'error'` in the final `RunState` (→ `restState: 'failed-dark'`).
   `cooperative-stop` is a distinct event that never carries `ok` — a user-requested brake (STOP file) is
   never reclassified as an error.
8. **`startedAt`/`lastEventAt`** are ISO-8601 strings formatted from an event's `t` (`new Date(t)
   .toISOString()` / chrono `to_rfc3339_opts(Millis, true)` — byte-identical). `lastEventAt` is set
   unconditionally at the top of `apply()`, every event. `startedAt` is set at the SAME "new run"
   boundary as rule 2's `cumUsd` reset: on `start`/`engine-start` for bmad, and — since generic logs
   have no `start` event — on the first `cum`-bearing event after a `run_ended` rebase (or the very
   first `cum`-bearing event of the log, when no prior run ended). It is NOT set on `story-start`
   (a per-story, not per-run, boundary).

---

## 5. Transcript markers (`*-run.out`, secondary)
`=== PHASE <name> (cum $X) ===` · `<label>: cost=$X cum=$Y is_error=B` · `Gate (dev): ... (P pass / F fail) green=B`
· `[REVIEW Q1] ...` / `[ORCHESTRATOR A1] ...` / `[REVIEW COMPLETE] ...` · `[SMOKE iter N] ...` · `[STOPPED] graceful stop honored at: <stage>`.
Phase-name → `sixPhase`: create-story→discover · dev-story→execute · dev-gate/code-review→verify · browser-smoke→verify · pr-*→persist · stop/next→decide.

---

## 6. Tauri command surface (Rust `control.rs`)

```
// A0/A1 — observe. `logFile` overrides the in-stateDir log name (both adapters default to log.jsonl,
// what the engine writes); a relative stateDir is resolved to absolute so watcher + engine agree.
load_run(stateDir: string, adapter: string, logFile?: string) -> RunState      // one-shot reduce of existing files
watch_run(stateDir, adapter, logFile?, channel: Channel<Delta>) -> ()
// Emits a `snapshot`, then per drain: an `event` delta per NEW raw line (feeds the live LOG panel)
// followed by ONE reduced `state` delta. The historical log is replayed once on mount, batched and
// capped at 300 events so a long-running loop doesn't flood the channel. An `activity` delta is
// also emitted whenever <stateDir>/activity.json changes (the ~12s liveness heartbeat) — this is
// what keeps the live-log freshness indicator alive through a silent dev-story; it never feeds the
// reduced RunState. `activity` is null when the file is absent/cleared.
// Delta = { kind: 'snapshot', state } | { kind: 'event', event: RawEvent } | { kind: 'state', state }
//       | { kind: 'activity', activity: { ts?, phase?, story?, elapsedSec, dirty, pid? } | null }
list_loops() -> LoopDef[]                                       // from loops/<id>/loop.json
// A6 — control. All take `loopsDir` (path to loops/) so the Rust side can locate loop.json.
start_loop(loopId, loopsDir, overrides?) -> { pid } | Err('AlreadyRunning'|...)
stop_loop(loopId, loopsDir, mode) -> ()   cancel_stop(loopId, loopsDir) -> ()   resume_loop(loopId, loopsDir) -> ()
guard_status(loopId, loopsDir) -> { running: bool, pid: number|null, stopPending, checkpoint }
answer_question(loopId, loopsDir, qid, text) -> ()   // A8 — writes <stateDir>/answer.json {qid,kind:'review',a:text}
// A7 — LAN reach: serves the built SPA + /ws Delta stream + token-gated POST /api/control
start_lan_server(loopsDir, port?) -> { url, token }   stop_lan_server() -> ()
```

For dev/replay without Tauri, the frontend also accepts a plain array of events fed through `reduce.ts`.

---

## 7. `loop.json` — a LoopDefinition (`loops/<id>/loop.json`)

```jsonc
{
  "id": "roman", "name": "Roman numerals — fix until green", "theme": "ember",
  "kind": "generic",                       // 'generic' (built-in engine) | 'external'
  "stateDir": ".loop",
  "adapter": "generic",                    // 'generic' | 'bmad' | 'custom'
  "start": { "program": "pwsh", "args": ["-File","loop.ps1","-TaskFile","TASK.md"] }, // external/start cmd
  "engine": {                              // present when kind=generic (the 6 blocks as config)
    "task": "TASK.md",
    "models": { "discover": "haiku", "execute": "sonnet", "judge": "haiku", "hard": "opus" },
    "maxTurns": 30, "allowedTools": ["Read","Edit","Write","Bash(bun test)"], "permissionMode": "acceptEdits",
    "gate": { "stages": [ { "name":"test", "command":"bun test", "passPattern":"(\\d+)\\s+pass", "failPattern":"(\\d+)\\s+fail" } ],
              "greenWhen": "exit==0", "lockGlobs": ["**/*.test.ts"] },
    "cost": { "ceilingUsd": 3.0, "alertPct": [50,80,100] },
    "stop": { "maxIters": 15, "stagnationLimit": 2, "plateauLimit": 3, "regressLimit": 3, "gracefulAtPhase": true },
    "verify": { "judgeModel": "haiku", "contract": [] }   // frozen AC criteria
  }
}
```

Seeded loop dirs (`loops/<id>/`): `hello` (generic, self-contained Python engine) and `bmad` (runs the live `loop-bmad` engine against an external project). `roman` & `calc` ship as replay-only fixtures; `static/fixtures/bmad-log.jsonl` is a recorded BMAD run for dev/replay.
