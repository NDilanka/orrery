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

### Quota (engine v2 — see BMAD)
```
{ "event": "quota-hit",    "label": string, "cum": float, "resetAt": iso|null }
{ "event": "quota-wait",   "label": string, "cum": float, "waitSec": int, "resumeAt": iso,
  "probe": int, "resetType"?: "five_hour"|"weekly" }
{ "event": "quota-resume", "label": string, "probe": int }
```

### BMAD adapter (see `.loop/bmad-log.jsonl`)
```
{ "event":"start",            "target": string, "branch": string, "baselinePass": int }
{ "event":"story-start",      "story": string, "status": string, "epic"?: string, "index"?: int }
{ "event":"dev-gate",         "story": string, "cum": float, "green": bool, "pass": int, "fail": int,
  "total": int, "baselinePass": int, "status": string, "codegenOk": bool, "lintOk": bool, "testOk": bool }
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
type RestState = 'certified-done'|'stopped-ember'|'quota-frost'|'handoff-beacon'|null; // §Design four states

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
    startedAt: string|null;
    updatedAt: string|null;
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
5. **restState** is derived last: `stop{ok:true}`/cooperative-stop→`stopped-ember`; quota active→`quota-frost`;
   `handoff`→`handoff-beacon`; all items done & merged→`certified-done`; else `null` (running).
6. **certified** flips true on a `verdict{pass:true}` for that item; a `dev-gate`/`gate green` only sets *claimed* green.

---

## 5. Transcript markers (`*-run.out`, secondary)
`=== PHASE <name> (cum $X) ===` · `<label>: cost=$X cum=$Y is_error=B` · `Gate (dev): ... (P pass / F fail) green=B`
· `[REVIEW Q1] ...` / `[ORCHESTRATOR A1] ...` / `[REVIEW COMPLETE] ...` · `[SMOKE iter N] ...` · `[STOPPED] graceful stop honored at: <stage>`.
Phase-name → `sixPhase`: create-story→discover · dev-story→execute · dev-gate/code-review→verify · browser-smoke→verify · pr-*→persist · stop/next→decide.

---

## 6. Tauri command surface (Rust `control.rs`)

```
// A0/A1 — observe
load_run(stateDir: string, adapter: string) -> RunState        // one-shot reduce of existing files
watch_run(stateDir: string, adapter: string, channel: Channel<Delta>) -> ()  // emit snapshot then deltas
// Delta = { kind: 'snapshot', state: RunState } | { kind: 'event', event: RawEvent } | { kind: 'state', state: RunState }
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

Seeded loops: `bmad` (external → bmad-loop.ps1, adapter bmad), `roman` & `calc` (generic → loop.ps1).
