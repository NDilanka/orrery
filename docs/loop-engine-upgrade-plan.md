# Loop Engine Upgrade Plan — adopting the "loops" playbook

> **Status:** approved plan; implemented phase-by-phase (one change per commit, acceptance test
> per change, pause on any red test).
>
> **Provenance note:** the primary X article (`x.com/ClaudeDevs/article/2074208949205881033`)
> robots-blocked (HTTP 402). Its full text was recovered from the official mirror
> `claude.com/blog/getting-started-with-loops` — claims from it are tagged **[ARTICLE]**.
> Claims from the Agent SDK / `/goal` / `/loop` / worktree docs are **[DOCS]**. Corroborating
> patterns from Anthropic "Building Effective Agents" and MindStudio "Verifiable Stop
> Conditions" are **[C1]/[C2]**. Every technique below is cross-checked across ≥2 sources
> unless flagged single-source.

---

## Context — why this plan exists

The engine (`engine/orrery_loop`, pip pkg `orrery-loop`) is a guarded, self-prompting
"fix-until-green" loop that drives the `claude` CLI as a cold subprocess per iteration, with an
**external test gate's exit code as the sole arbiter of "done"** (`gate.py:196`). The mission was
to study the ClaudeDevs "loops" article, extract transferable mechanisms, and adopt the good ones
*adapted to this engine's philosophy* — not bolt on a foreign architecture.

**Key finding up front:** the engine already implements the article's strongest, best-corroborated
advice — and on the load-bearing primitive (an un-fakeable stop condition) it is **stricter than
the article's own `/goal`**: `/goal` uses a fresh model that *reads the transcript* [DOCS], whereas
this engine reads *real gate exit codes* plus a SHA-256 hash-lock on test files. So this plan is
deliberately small: it closes three real gaps, defers two, and explicitly rejects four ideas that
would fight the engine's design. The three "adopt now" items add **zero always-on token overhead**.

---

## Phase 0 — Current state (grounded, with evidence)

| Concern | How the engine does it | Evidence |
|---|---|---|
| Loop primitive | Sequential `for it in range(1, max_iters+1)`; one iteration = decide→act→verify→terminate | `core.py:391-719` |
| What runs the model | Cold-start subprocess to `claude -p … --max-turns …` per iteration (NOT an SDK; no session reuse) | `claude.py:56-100`, `core.py:427` |
| Stop = "done" | **External gate exit code**, never the actor's word. Green = every stage exited 0 | `gate.py:196-215`, `decide.py:58-59` |
| Anti-fake | Hash-lock on test files → `tampered` stop; test-count floor → `count_dropped` stop; held-out hidden stages ANDed into green | `hashlock.py:102-155`, `decide.py:52-55`, `verify.py:57-156` |
| Verification split | Generic loop: opt-in independent VERIFY judge (diff-only, refute-biased, `max_turns=1`). BMAD: **default-on** independent verify + test-integrity + plan-gate | `core.py:828-871` (`enabled=False` `config.py:181`); `driver.py:1307,1217,1100` (defaults True `273/281/289`) |
| Decision core | Pure `decide()`: tamper > count-drop > green > blocked > cost-ceiling > regress > stagnation > plateau > max-iters | `decide.py:21-88` |
| State persistence | Per-iter git commit + `log.jsonl` + `checkpoint.json` (resume = re-run) + volatile `progress.md`; opt-in cross-run `memory.jsonl` | `logio.py`, `events.py:385-409`, `core.py:1105-1120`, `config.py:176` |
| Error handling | Quota → recoverable (retry SAME iter, no iter consumed); timeout → tree-kill; parse-fail → stop; consecutive-fail → one recover then handoff; BMAD spin-guard + supervisor thrash-guard | `core.py:442-466`, `decide.py:117-157`, `driver.py:966-984`, `supervise.py:129-139` |
| Budget / economics | `max_iters=15`, `max_turns=30`, **cost ceiling $ only** ($3), 50/80/100% alerts; stable-prefix prompt caching | `config.py:33,201,39`, `cost.py`, `prompts.py:1-9` |
| Parallelism / worktrees | **None.** Strictly sequential; BMAD isolates by branch+PR, not worktrees | `core.py:391`, `driver.py:1531`; no worktree/threadpool found |
| Safety rails | Single-flight PID lock (exit 2), cooperative safe-stop/resume, gitignore guards, dry-run, process-tree kill | `lockfile.py`, `checkpoint.py`, `.gitignore:50-56`, `proc.py:150-184` |
| Config | One `loop.json`, namespaced blocks `engine`/`bmad`/`qa`; camel+snake keys; all research caps default-off (byte-parity) | `config.py:244-381`, `engine/README.md:101-105` |

---

## Phase 1 — Techniques extracted from the sources

Concrete mechanisms only, best-corroborated first:

1. **Un-fakeable stop condition** — completion decided by evidence, not the actor's self-report.
   `/goal` wraps a Stop-hook: after each turn a *separate fast model* judges the condition against
   the transcript; "yes" ends, "no" returns with guidance [ARTICLE][DOCS]. C2: a stop condition must
   be *binary, interpretation-free, and reference concrete measurable state* [C2]. C1: agents need
   "ground truth from the environment … to assess progress" [C1]. **Corroborated ×4.**
2. **Maker/checker split** — a *separate* pass evaluates, because "an agent evaluating its own work
   has a strong prior that the work is good" [C2]; Anthropic's evaluator-optimizer: "one LLM
   generates … another provides evaluation and feedback in a loop" [C1]; article: "use a second
   agent for code reviews" [ARTICLE]. **Corroborated ×3.**
3. **Turn/budget caps + token economics** — `max_turns` (tool-use round trips) and `max_budget_usd`;
   "setting a budget is a good default for production agents" [DOCS]; "always include a maximum
   iteration count, even with a strong primary stop condition" [C2]; `effort` trades tokens for
   depth; `/usage` breaks down spend [ARTICLE]. **Corroborated ×4.**
4. **On-disk memory / "memory spine"** — "the model forgets, the repo doesn't"; state that "lives
   outside the single conversation and holds what's done and what is next" [ARTICLE]; CLAUDE.md is
   "re-injected on every request" and survives compaction [DOCS]. **Concept corroborated ×2; the
   name is single-source.**
5. **Worktree isolation for parallel agents** — each agent its own git worktree so "edits in one
   session never touch files in another"; `isolation: worktree` subagent frontmatter [DOCS]. Addy's
   caveat: "YOU are the ceiling — your review bandwidth decides how many you can run" [ARTICLE].
   **Worktree mechanism single-source (docs); write-isolation principle corroborated.**
6. **Error handling** — a denied/failed tool returns a rejection that the agent adapts to (different
   approach) rather than blindly repeating [DOCS]; cap retries and "stop on success or when N is
   reached"; don't treat "no error" as "correct" [C2]. **Corroborated ×2; explicit
   recoverable-vs-hard-blocker taxonomy is thin in the sources.**
7. **Lifecycle + hooks** — receive prompt → evaluate/respond → execute tools → repeat → return; hooks
   fire at `PreToolUse`/`PostToolUse`/`Stop`/`PreCompact` and "run in your application process, not
   inside the agent's context window" [DOCS]. **Single-source (docs).**
8. **Failure debts** [ARTICLE, single-source but useful vocabulary]: *verification debt*
   ("unattended loops make mistakes unattended"), *comprehension debt*, *cognitive surrender*.
   Fix = separate verifier + human review; C2 corroborates "loops collapse at the exit point."

---

## Phase 2 — Gap analysis (honest; partials are partials)

| Technique | Engine does it? | Evidence | Gap |
|---|---|---|---|
| Un-fakeable, evidence-based stop | **YES — best-in-class** | gate exit code (`gate.py:196`), hash-lock, count-floor, held-out | *Stronger* than `/goal`. **One real hole:** hash-lock covers test *files* only; test **infra/config** (conftest.py, pytest.ini, package.json test script, vitest config) is unlocked → actor can neuter collection and still exit 0 |
| Maker/checker split | **PARTIAL (generic) / YES (BMAD)** | generic verify opt-in + diff-only (`core.py:828`, `config.py:151`); BMAD default-on independent (`driver.py:1307`) | Generic verifier sees only the diff — no gate/test evidence; and it's off by default |
| Turn/budget caps + economics | **PARTIAL** | iters/turns/`$` ceiling (`config.py`), alerts (`cost.py`) | **No token ceiling** — only dollars, which are meaningless on a Max subscription (`events.py:615-617` says so). Token telemetry already parsed (`core.py:484`) but never bounds the run |
| On-disk memory spine | **PARTIAL** | git + `checkpoint.json` + `progress.md`; lessons `memory.jsonl` **off** (`config.py:176`) | Cross-run "what's done/next" = resume-by-re-run (recomputes baseline); lessons opt-in |
| Worktree isolation / parallel | **NO** | sequential (`core.py:391`), branch-only isolation | No parallelism; but this fights the single-guarded-loop design |
| Error: recoverable vs hard-blocker, no spin-retry | **YES — strong** | quota-retry (`core.py:442`), consec-fail→recover→handoff (`decide.py:117`), spin/thrash guards | Adequate. Minor: max-iters-without-green stops the same as a clean green stop (no distinct "exhausted, needs human" beacon) |
| Lifecycle + hooks | **PARTIAL (by design)** | cold subprocess; experimental `sessionGate` Stop-hook off (`core.py:737`) | Engine orchestrates from outside on purpose; not a gap to force |
| Skills / codified standards | **N/A to engine** | drives `claude` CLI; skills live in target repo `.claude/` | Out of engine scope |

---

## Phase 3 — Prioritized plan

Ordered by leverage-to-effort. Every "adopt now" item is **additive and default-off/parity-preserving**
(matching the engine's existing philosophy) and adds **no always-on token cost**.

### ADOPT NOW

#### A1 — Lock the test *infrastructure*, not just test files  *(closes the only real un-fakeable-stop hole)*
- **What:** the tamper mechanism already exists and is fully general over `lockGlobs`; the gap is
  that the **default** only locks test files (`*.test.ts`), leaving `conftest.py`, `pytest.ini`,
  `setup.cfg`/`tox.ini`, `pyproject.toml [tool.pytest]`, `package.json` test scripts, and
  vitest/jest config editable — a gutted fixture or a `--collect-only` shortcut passes the gate.
  Add an opt-in convenience `gate.lockInfra: true` that expands to a curated infra glob set for the
  detected stack, plus docs + a locked example. No default behavior change (parity). The curated
  default set is limited to *pure* test-infra files (conftest.py, pytest.ini, tox.ini, jest/vitest/
  playwright/cypress/karma/mocha configs, bunfig.toml); dual-purpose files (pyproject.toml,
  package.json, setup.cfg) are documented as manual `lockGlobs` adds to avoid false positives on
  legitimate dependency edits.
- **Why:** [C2] a stop condition must be un-fakeable and reference concrete state; [ARTICLE] the #1
  failure is the actor declaring "done" when it isn't. This is the engine's own theme, one layer deeper.
- **Files:** `hashlock.py` (glob-expansion helper), `config.py` (`GateConfig.lock_infra` flag +
  infra glob constant), `core.py:104-116` (`_lock_glob_set` appends infra globs), `examples` +
  `engine/README.md` (recommended infra locks), tests under `engine/tests/`.
- **Size:** M. **Risk:** low (opt-in; false-positive tamper only if a user legitimately edits a
  locked infra file, which is the intended signal). **Token/cost:** zero (local SHA-256 only).
- **Acceptance test:** a fixture run where the agent edits `conftest.py` to skip a suite is caught
  as `tampered` (forced non-green stop) with `lockInfra` on; with it off, behavior is byte-identical.

#### A2 — Token-budget ceiling alongside the dollar ceiling  *(the token-conscious win)*
- **What:** add `stop.tokenCeiling` (default `0` = disabled → parity). Accumulate input+output tokens
  from `result.usage` each iteration (same object `get_cache_usage` already reads at `core.py:484`),
  and add a `decide()` branch mirroring the existing cost-ceiling stop.
- **Why:** [DOCS] `max_budget_usd` is "a good default for production agents"; the engine's own
  comment (`events.py:615-617`) says that on a Max subscription the binding constraint is **tokens,
  not dollars** — so the current `$` ceiling can't actually bound a subscription run.
- **Files:** `config.py` (`StopConfig.token_ceiling`), `decide.py:62-63` (new branch, above/beside
  cost ceiling, reason `"token ceiling reached"`), `core.py` (sum `cum_tokens`, thread into
  `decide`), `events.py` (stop reason string), tests mirroring the cost-ceiling test.
- **Size:** M. **Risk:** low (additive; disabled by default). **Token/cost:** zero always-on — it
  only ever *stops sooner*.
- **Acceptance test:** a loop with `tokenCeiling` set and a stub runner returning known `usage` stops
  with reason `"token ceiling reached"` once cumulative tokens exceed it; `tokenCeiling: 0` run is
  byte-identical to today.

#### A3 — Feed the VERIFY judge real evidence (gate summary), not just the diff
- **What:** when the opt-in VERIFY pass runs, include the gate result summary (pass/fail/total + the
  tail of gate output) in the judge prompt alongside the diff.
- **Why:** [C2] "demand evidence, not claims"; [ARTICLE] the second-agent reviewer should judge
  against what was actually demonstrated. Today the verifier reasons from diff plausibility alone
  (`core.py:840-846`) and can't confirm the tests genuinely ran/passed.
- **Files:** `core.py:840-846` (`_run_verify` prompt + pass `g` summary through the call site at
  `core.py:532`), one test asserting the gate summary reaches the judge prompt.
- **Size:** S. **Risk:** low. **Token/cost:** negligible — a few hundred tokens added to a cheap
  (`max_turns=1`, haiku-tier) call that only fires on an already-green iteration; **verify stays
  opt-in**, so there is no always-on cost.

### ADOPT LATER (needs measurement or is lower value)

#### L1 — Mature the experimental warm-session gate (`sessionGate`) for token economics
- **What / why:** [DOCS] context accumulates and stable prefixes are prompt-cached; the generic loop
  cold-starts each iteration and never reuses a session (`core.py:427`), so it re-reads TASK.md +
  progress.md + re-explores every iteration. A warm/`--resume` session or the prototyped `sessionGate`
  could cut per-iteration re-read tokens. **Files:** `core.py:737-781`, `claude.py`. **Size:** L.
  **Risk:** medium (context pollution; a warm actor may "remember" it said done — must keep the
  external gate as the arbiter). **Token/cost:** potential *savings*, but defer until measured on a
  real run; don't change the default until the win is proven.

#### L2 — Document the cross-run memory spine as an opt-in for multi-run campaigns
- **What / why:** [ARTICLE][DOCS] "the model forgets, the repo doesn't." `memory.jsonl` exists but is
  off (`config.py:176`) because recall injects lessons into the prompt every iteration (always-on
  token cost). Keep it **off by default**; add docs on when a multi-run campaign earns it. **Files:**
  `engine/README.md`, example. **Size:** S. **Risk:** none. **Token/cost:** adds recall tokens per
  iteration *only when enabled* — hence stays opt-in for a token-conscious operator.

#### L3 — Distinct "exhausted without green" terminal beacon
- **What / why:** [C2] log when the iteration cap is hit — "the task is impossible or your stop
  condition needs adjustment." Today max-iters-without-green stops the same as a clean green stop.
  Emit a distinct handoff beacon so the UI/human can tell "needs attention" from "done." **Files:**
  `decide.py:84-87`, `events.py`, `core.py` stop path. **Size:** S. **Risk:** low. **Token/cost:** zero.

### EXPLICITLY REJECT (with why it doesn't fit THIS engine)

- **R1 — Git worktree isolation + parallel agents** [ARTICLE single-source + DOCS]. The engine is a
  *single-agent, sequential, external-gate-is-truth* primitive. Parallelism is a foreign architecture,
  and Addy himself says review bandwidth is the ceiling. BMAD already isolates by branch+PR. If
  parallelism is ever wanted it belongs at the **orrery orchestration layer** (spawn N independent
  `loop` processes on separate branches/worktrees), never inside the loop primitive. Adopting it
  would multiply token spend N× against a token-conscious mandate.
- **R2 — In-session Stop/PreToolUse hooks as the primary loop mechanism** [DOCS]. The engine
  deliberately verifies from *outside* the actor's process so the gate can't be influenced by the
  actor's context. Hooks run inside the `claude` session and would couple verification to the actor.
  The opt-in `sessionGate` prototype already offers a Stop-hook path *without* surrendering the
  external gate — that's the right ceiling for this idea.
- **R3 — Replace the gate with a `/goal`-style transcript-reading evaluator** [ARTICLE/DOCS]. Reading
  the transcript is *weaker* than real gate exit codes + hash-lock. Keep the external gate as truth;
  the evaluator's one edge (catching "claimed but not demonstrated") is already covered by
  requiring green plus the opt-in diff-verifier (strengthened by A3). Adopt only the *spirit*
  (evidence-based stop), which A1 and A3 do.
- **R4 — Time-based / proactive scheduled loops inside the engine** [ARTICLE taxonomy]. Scheduling is
  an OS/orrery concern (Task Scheduler, cron, orrery). The engine is a goal-based fix-until-green
  primitive; `loop-supervise` already handles restart. Wrapping it on a schedule is the caller's job.

---

## Self-check
- Every "adopt now" item is grounded in **both** a source (A1→C2/ARTICLE, A2→DOCS+`events.py:615`,
  A3→C2/ARTICLE) **and** a real Phase-2 gap. No proposal is source-only or gap-only.
- Nothing here changes a default or adds always-on token overhead; all three adopt-now items are
  opt-in/parity-preserving, matching the engine's existing "research caps default-off" philosophy.
- No source files were modified during research/planning; only this plan file was written.

---

## Verification (how each change is proven end-to-end)
Run `python -m pytest engine/tests -q` after each change (baseline: 631/632 per `README.md:145`).
Per-item acceptance tests are listed inline above. Additionally, drive a real `examples/hello` loop
(`loop --loop-json examples/hello/loop.json --dry-run` first, then a live short run) to confirm:
A1 catches an infra-file edit; A2 stops on a token ceiling with the stub runner; A3's judge prompt
contains the gate summary. Commit one change per acceptance test; pause if any test goes red.
