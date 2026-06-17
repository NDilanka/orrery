# loop — a minimal self-prompting harness (V0)

A hand-rolled "generate → test → fix until green" loop around headless
`claude -p`. Built for Windows 11 / PowerShell / Bun / Claude Max. The design
rationale and the research behind it are in **`loop-engineering.md`**.

## What's here

```
loop.ps1               the orchestrator (owns cost, gate, state, stops)
TASK.md                the goal spec the agent reads each iteration
src/roman.ts           the module under test — DELIBERATELY BROKEN
src/roman.test.ts      the eval gate — human-written, the loop must NOT edit it
.loop/progress.md      the loop's file-based memory (fresh context each iter)
.loop/log.jsonl        per-iteration machine log (created at runtime)
settings.example.json  optional permission-layer test protection
```

## Run it

```powershell
# 1. Validate wiring without spending any quota (runs the gate once):
pwsh -File loop.ps1 -DryRun

# 2. Run the loop (defaults: 15 iters, $3.00 cumulative ceiling):
pwsh -File loop.ps1

# 3. Tune the bounds:
pwsh -File loop.ps1 -MaxIters 10 -CostCeilingUsd 1.50
```

The demo task (Roman numerals, with subtractive cases + a full round-trip)
typically goes green in a handful of iterations. Watch it work, then point
`TASK.md` and the `src/` files at your own module.

## The guardrails (why this is safe to leave running)

| Concern | How loop.ps1 bounds it |
|---|---|
| Runaway spend | sums `total_cost_usd` each iter → hard `-CostCeilingUsd`. (`--max-budget-usd` only bounds ONE call, so we don't rely on it.) |
| Looping forever | `-MaxIters` backstop + stagnation detection |
| False-green tests | SHA-256 of every `*.test.ts` checked each iter; test-count can't shrink |
| Silent drift | pass-count logged every iter in `.loop/log.jsonl`; per-iter git commit = rollback |
| Context rot | **fresh context every iteration** — state lives in `.loop/progress.md` + git, not the model's memory |
| No progress | no-diff for `-StagnationLimit` iters → stop + handoff |

## Stop / handoff conditions

The loop exits (code 0 = success, 1 = handoff) on the FIRST of:

- ✅ `bun test` green → success
- 🛑 cumulative cost ≥ ceiling
- 🛑 `BLOCKED:` written to `.loop/progress.md` by the agent
- 🛑 a test file was modified, or the test count dropped
- 🛑 stagnation (N iters with no change)
- 🛑 max iterations reached without green

## Notes

- The loop passes its tool allow-list as `--allowedTools` CLI flags, so it runs
  unattended with no persistent config changes. If a tool still prompts in your
  setup, copy `settings.example.json` → `.claude/settings.json`, or (in a
  throwaway copy only) add `--dangerously-skip-permissions` to `$cliArgs`.
- `total_cost_usd` is your **Max-5x quota proxy** — it reports equivalent API
  cost, useful as a relative spend gauge even on a subscription.
- The eval gate is the orchestrator running `bun test`, **not** the model's
  self-assessment and **not** `/goal` (whose evaluator is a transcript judge
  that doesn't run tools and can false-green).
