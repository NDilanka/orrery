# Loop Engineering on Claude Code — Grounded Synthesis + Buildable Blueprint

> Deep-research synthesis. 25 sources fetched, 116 claims extracted, 25
> adversarially verified (3-vote, 2/3 to kill): **23 confirmed, 2 killed**.
> Confidence and verification votes are noted inline. Sources at the end.

---

## 0. What the verification changed (read this first)

Two assumptions commonly repeated about this topic were **refuted 0–3** (all
three skeptics killed them):

1. **"Dynamic workflows are GA / default-on for Max/Team."** ❌ Refuted.
   They are a **research preview**, not generally available, not confirmed
   default-on. Don't architect an unattended pipeline around them.
2. **"Dynamic workflows auto-persist progress so an interrupted job resumes
   where it stopped."** ❌ Refuted. There is **no built-in state/recovery**. If
   the run dies, the in-flight orchestration is lost. This is the single
   biggest reason to hand-roll the overnight harness.

Honesty flag: **Boris Cherny's exact viral quote could not be verified to a
primary source.** Transcript extraction from the Acquired/YouTube source failed
(0 claims); only *secondary* write-ups (WorkOS, note.com) corroborate the
*themes* — budget shifting from headcount to tokens, deliberate understaffing
to force automation, high AI-written-code share, many PRs/day. Treat the
verbatim "I don't prompt Claude anymore… my job is to write loops" as
**paraphrase-grade, not quote-grade.** The idea is well-attested; the wording
isn't pinned.

---

## (a) What "loop engineering" actually is — and isn't

**Verified, attributable definitions:**

- **Addy Osmani reframes a coding agent as `AI model(s) + harness`**
  (verified 2–1). "Harness engineering" is his term: leverage moved *out of the
  prompt* and *into the scaffolding* — the loop, tools, context assembly,
  verification.
- **Peter Steinberger (@steipete, OpenClaw → OpenAI) publicly framed the same
  shift** (verified 3–0): stop hand-crafting prompts; *design the loop that
  prompts your agents for you.*

**The precise distinction:**

|                | Prompt engineering | Agent harness | Loop engineering |
|----------------|--------------------|---------------|------------------|
| Unit of work   | one model call | model + tools + context for one task | an *outer control loop* that re-invokes the harness until a goal is met |
| You tune       | wording, examples, format | tool set, context contents, system prompt | stop conditions, eval gates, state, cost ceilings, next-step decision |
| Failure fought | bad single output | bad tool use / missing context | runaway spend, drift, false-green, no-progress |

So **prompt engineering ⊂ harness ⊂ loop.** "Loop engineering" is the outermost
layer, and it is mostly *not LLM work* — it is orchestration, accounting, and
verification code. That is the whole point of the meme: the high-value skill
moved from English-into-the-box to **plumbing-around-the-box.**

What it **isn't**: not "run unattended and hope." Every credible practitioner
pairs the loop with a **real verification gate** and a **hard spend bound**. A
loop without those is an expensive way to corrupt a repo — see the verified
$6,000 overnight run.

---

## (b) Canonical loop anatomy — the building blocks

Two nested loops:

- **Inner loop = ReAct** (verified 2–1): *reason → act (tool) → observe →
  repeat* within one agent turn. This is what Claude Code already does inside a
  single `claude -p` invocation.
- **Outer loop = "Ralph"** (ghuntley / snarktank / frankbria): a `while` loop
  re-running a fixed prompt against **fresh context each iteration**, with
  progress carried in *files*, not the model's memory.

The **6 building blocks** of one outer iteration:

```
 1. DISCOVER / SELECT work   ← read TASK.md + state file
 2. ASSEMBLE context         ← spec + progress + failing test output
 3. EXECUTE (agent turn)     ← claude -p; ReAct inside
 4. VERIFY / EVAL  ★         ← orchestrator runs `bun test` — NOT the model's word
 5. PERSIST state            ← progress.md + log.jsonl + git commit
 6. DECIDE next              ← advance / retry / STOP / hand off to human
        └────────── repeat until: goal | max iters | cost ceiling | stagnation
```

★ **Block 4 is the one the hype skips and the one that makes or breaks you.**

---

## (c) Native dynamic workflows vs. a hand-rolled harness

**Verified native primitives** (against code.claude.com/docs + release notes):

| Primitive | Verified fact | Flag / version |
|---|---|---|
| Headless mode | runs non-interactively, scriptable | `claude -p "<prompt>"` ✅ 3–0 |
| Machine output | JSON includes `total_cost_usd`, `session_id` | `--output-format json` ✅ 3–0 |
| Streaming | line-delimited events | `--output-format stream-json` ✅ 3–0 |
| Turn cap | bounds turns **within one invocation** | `--max-turns N` ✅ 3–0 |
| Spend cap | bounds cost of **one invocation only** | `--max-budget-usd` ✅ 3–0 ⚠️ |
| Session state | resume/continue across invocations | resume by `session_id` ✅ 3–0 |
| Subagents | Markdown + YAML, **isolated context each**, can recurse | ✅ 3–0 |
| `/goal` | native self-prompting loop command | **v2.1.139** ✅ 3–0 |
| Dynamic workflows | Claude orchestrates **dozens–hundreds of subagents**; **substantially more tokens** | **v2.1.154**, research preview ✅ 3–0 |

**Comparison for Windows 11 / 8 GB / Max 5x / overnight:**

| | Native dynamic workflows | Hand-rolled `claude -p` loop |
|---|---|---|
| Token/quota cost | **substantially higher** (3–0) | you control fan-out; cheapest path |
| State / recovery | **none built-in** (refuted 0–3) | you own it — files + git, survives crash |
| Cost accounting | opaque inside the workflow | you sum `total_cost_usd` → hard ceiling |
| Determinism | model decides control flow | *your code* decides control flow |
| RAM footprint | many concurrent agents | one process at a time |
| Best at | broad fan-out *inside one bounded task* | long, cheap, recoverable, quota-bounded grind |

**Recommendation:** **hand-roll the outer loop.** On Max 5x with 8 GB, a
fan-out-heavy preview with no recovery and higher burn is the wrong overnight
engine. Use **subagents** (cheap, isolated context) *inside* the loop for
fan-out; reach for dynamic workflows only as an occasional, bounded, *attended*
breadth tool — never the unattended substrate.

**Two design-shaping caveats (both verified):**
- ⚠️ **`--max-budget-usd` bounds ONE invocation, not the loop** (GitHub #57719).
  The orchestrator must sum cumulative cost itself.
- ⚠️ **`/goal`'s evaluator is a transcript judge that does not run tools — it
  can false-green.** The eval gate must be your orchestrator running the real
  tests and reading the exit code.

---

## (d) Minimal loop (this repo's V0)

Pick a real module with a real, human-written test file. The agent fixes the
implementation; it must not touch the tests.

```
PowerShell orchestrator (lightest on Win/8GB — no extra runtime)
   │  owns: cumulative cost, stop conditions, eval gate, state, handoff
   ▼
claude -p --output-format json --max-turns 30
          --allowedTools Read Edit Write "Bash(bun test)" "Bash(bun test:*)"
          --permission-mode acceptEdits
   │  (Bun = test runner for the module under test)
   ▼
.loop/  ← state in files, fresh context each iteration
   progress.md  (narrative scratchpad)   log.jsonl (per-iter machine log)
   git commits  (rollback per iteration)
```

**Design decision — fresh context, not session-resume.** Re-run with clean
context each iteration; carry state in `progress.md`. Costs a re-read per iter
but **eliminates context rot** — the right trade for a fix-until-green grind.

The five required pieces, as implemented in `loop.ps1`:

| Piece | Mechanism |
|---|---|
| Orchestrator | PowerShell `for` loop wrapping `claude -p` |
| Stop conditions | green ✓ \| MaxIters \| cumulative CostCeiling \| stagnation \| `BLOCKED` |
| Eval gate | orchestrator runs `bun test`, reads exit code — independent of model and `/goal` |
| State | `.loop/progress.md` + `.loop/log.jsonl` + git commit per productive iter |
| Cost ceiling | sum `total_cost_usd` (since `--max-budget` bounds one call); the quota proxy |
| Human handoff | cost ceiling \| max iters w/o green \| stagnation \| test tampering \| `BLOCKED:` |

---

## (e) Build sequence — minimal loop → general harness (hardest parts ⚠️)

1. **V0** — the loop in this repo, on one module. Green-or-handoff end to end. ← *you are here*
2. **V1 — ⚠️ EVALUATION hardening (hardest #1).** Lock test files (hash check),
   assert test count never drops (catches "delete the failing test"). The gate
   is the whole ballgame.
3. **V2 — ⚠️ STATE (hardest #2).** Give `progress.md` a strict schema
   (done / in-progress / blocked / next). Add pass-count trend analysis to
   catch silent drift.
4. **V3 — ⚠️ RECOVERY (hardest #3).** Make the loop crash-restartable: on
   launch, read `log.jsonl` + last commit and resume. Native dynamic workflows
   don't give you this (refuted) — own it. Git commits = rollback.
5. **V4** — observability + spend dashboard; alert at 50/80/100% of ceiling;
   wire `--output-format stream-json` for live progress.
6. **V5** — fan-out via subagents (isolated context) once V1–V3 are solid.
7. **V6** — generalize: TASK.md becomes a queue; the loop discovers next work
   (block 1) instead of one fixed goal. This is the "write loops, not prompts"
   machine.

### Failure modes → concrete bounds

| Failure | Detection | Bound |
|---|---|---|
| Runaway spend | cumulative `total_cost_usd` sum | hard CostCeiling (not `--max-budget`, per-call only) |
| Looping on broken goal | iter count, no green | MaxIters + stagnation counter |
| Context rot | — | fresh context per iter + compact `progress.md` |
| False-green tests | test-file hash + count delta | tamper → immediate handoff |
| Silent drift | pass-count trend in `log.jsonl` | alert if green-count decreases |
| No-progress spin | empty `git status` 2× | stagnation → handoff |

---

## Sources

Primary:
- Osmani, "Agent Harness Engineering" — https://addyosmani.com/blog/agent-harness-engineering/
- Steinberger (@steipete) — https://x.com/steipete/status/2063697162748260627
- Claude Code docs — https://code.claude.com/docs/en/headless · /cli-reference · /sub-agents · /whats-new
- Release notes — https://docs.anthropic.com/en/release-notes/claude-code
- Dynamic workflows — https://claude.com/blog/introducing-dynamic-workflows-in-claude-code
- `--max-budget` scope (per-call) — https://github.com/anthropics/claude-code/issues/57719
- Ralph harnesses — https://github.com/snarktank/ralph · https://github.com/frankbria/ralph-claude-code · https://github.com/AnandChowdhary/continuous-claude
- Auto mode — https://www.anthropic.com/engineering/claude-code-auto-mode

Secondary / supporting:
- Osmani, "Loop Engineering" — https://addyosmani.com/blog/loop-engineering/
- Ralph pattern writeup — https://ghuntley.com/ralph/
- Cherny interview takeaways (theme corroboration only) — https://workos.com/blog/boris-cherny-claude-code-acquired-interview-takeaways
- $6,000 overnight run — https://www.makeuseof.com/someone-left-claude-code-running-overnight-and-it-cost-6000/
- stream-json patterns — https://backgroundclaude.com/blog/stream-json
- cost control in agentic systems — https://www.alpsagility.com/cost-control-agentic-systems

**Refuted (do not rely on):** dynamic-workflows auto-persist/resume (0–3);
dynamic-workflows GA / default-on for Max/Team as of 2026-05-28 (0–3).
**Unverified:** Cherny's verbatim Acquired quote.
