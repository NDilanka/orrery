# Engine capabilities — the research-backed, default-OFF flags

The Orrery engine ships a small set of optional capabilities grounded in the
agent / eval-hardening literature. **Every one is OFF by default.** With all of
them off, the loop behaves exactly like the baseline `loop.ps1` port — same
prompt bytes, same events, same decisions. Turning one on is purely additive:
it emits extra `log.jsonl` events (which older reducers log-but-ignore, per
[`../orrery/PROTOCOL.md`](../orrery/PROTOCOL.md) §2) and changes nothing about
the default path.

Each capability can be enabled two ways:

- a **`loop.json`** `engine.*` key (camelCase on the wire — see PROTOCOL §7), or
- a **CLI flag** on `loop` (the flag overrides the JSON).

The flag surface lives in `engine/orrery_loop/cli.py`; the config keys in
`engine/orrery_loop/config.py`.

---

## 1. Held-out (hidden) test split

**Idea.** Keep a slice of the test suite *hidden* from the agent. It still runs
and still counts toward green, but its output is stripped from every piece of
feedback the agent sees, and its files are hash-locked so the agent can't edit
them. Overall green = *visible* green **AND** *held-out* green. This is the
single strongest defense against an agent overfitting to the exact tests it can
read.

**Grounded in.** Krakovna et al., *Specification Gaming: the flip side of AI
ingenuity* (DeepMind, 2020) — agents satisfy the literal spec, not the intent;
and METR's reward-hacking findings (2024–2025) on models special-casing visible
checks. A held-out suite removes the surface to overfit to.

**Enable.** Per gate stage, mark it held-out and lock its files:

```jsonc
"gate": {
  "stages": [
    { "name": "visible", "command": "pytest -q tests/visible", "passPattern": "(\\d+) passed" },
    { "name": "hidden",  "command": "pytest -q tests/hidden",  "passPattern": "(\\d+) passed",
      "heldOut": true, "lockGlobs": ["tests/hidden/**"] }
  ],
  "lockGlobs": ["**/test_*.py"]
}
```

No dedicated CLI flag — it is a property of the gate stage (`heldOut` /
`held_out`). Implemented in `engine/orrery_loop/verify.py` (`held_out_green`,
`visible_feedback_raw`, `held_out_lock_globs`).

---

## 2. Compact feedback

**Idea.** When the gate is red, feed back only the **first** failing test — its
assertion/expectation and a `file:line` — instead of dumping the whole
multi-thousand-line log. Cheaper per token and empirically steers the fix
better than a log dump. The compact signal goes into the volatile steer, kept
*out* of the cached prompt prefix.

**Grounded in.** SWE-agent (Yang et al., 2024) — the agent–computer interface
matters: concise, well-shaped observations beat raw dumps; and Self-Debug (Chen
et al., 2023) — a model debugs better from a focused error signal than from
noise.

**Enable.**

```jsonc
"feedback": { "compact": true }
```

```bash
loop --compact-feedback ...
```

Implemented in `engine/orrery_loop/feedback.py` (`extract_first_failure`,
`compact_feedback`; dialects: pytest, bun test, vitest, jest, go test).

---

## 3. Lint / type pre-gate

**Idea.** Put a fast lint/type stage *before* the test stage in the gate. Exit
code is truth and **green = every stage exited 0**, so a lint/type failure keeps
the loop red until it's fixed — and with compact feedback on, that stage
surfaces its own precise error (not a downstream test's) because the compactor
slices the *first failing stage's* section first.

**Grounded in.** SWE-agent (Yang et al., 2024) — give the agent the cheapest,
most localized signal first; static checks catch a class of errors before tests
even run.

**Enable.** This is just gate composition — add an ordered stage. A no-count
pre-stage (lint/type emits no pass/fail counts) does **not** zero the real
totals; counts come from the last stage that reports any.

```jsonc
"gate": {
  "stages": [
    { "name": "lint",  "command": "ruff check ." },
    { "name": "types", "command": "mypy src" },
    { "name": "test",  "command": "pytest -q", "passPattern": "(\\d+) passed", "failPattern": "(\\d+) failed" }
  ]
}
```

Implemented in `engine/orrery_loop/gate.py` (multi-stage `run_gate`; exit-code-is-truth).

---

## 4. Cross-run lessons memory

**Idea.** A small append-only store of lessons learned across runs. Durable repo
facts (`semantic`) and run-specific lessons (`episodic`) are recalled into the
**stable** prompt prefix (cache-friendly), green-gated so only useful outcomes
persist, and bounded so it never grows without limit. Off → a null store →
byte-identical prompt.

**Grounded in.** Reflexion (Shinn et al., 2023) — verbal self-reflection stored
and reused across attempts; ExpeL (Zhao et al., 2024) — an agent that
*extracts* reusable insights from past trials; CoALA (Sumers et al., 2024) — the
episodic vs. semantic memory distinction; and ACE (2025) — append-don't-rewrite
to avoid context collapse.

**Enable.**

```jsonc
"memory": { "enabled": true, "path": ".loop/memory.jsonl", "recallLimit": 5 }
```

```bash
loop --memory                 # enable, default path <state-dir>/memory.jsonl
loop --memory path/to.jsonl   # enable with an explicit path
```

Implemented in `engine/orrery_loop/memory/store.py` (`FileMemoryStore`,
`NullMemoryStore`, `Lesson`).

---

## 5. Mutation audit

**Idea.** An advisory probe of how *strong* the suite is. Coverage says a line
ran; mutation score says whether a test would *notice* if that line were wrong.
When the gate is green, rewrite the implementation's source string with simple
operator/literal swaps and ask whether the suite still passes — a surviving
mutant is a weak spot. It's **advisory only**: it never gates a run, and the
file is restored in a `finally` (never left mutated, even on exception).

**Grounded in.** Just et al., *Are Mutants a Valid Substitute for Real Faults in
Software Testing?* (FSE, 2014) — mutation detection correlates with real
fault-detection far better than coverage does.

**Enable.**

```jsonc
"verify": { "mutationAudit": true, "mutationEvery": 1 }
```

```bash
loop --mutation-audit ...
```

`mutationEvery` throttles it (run only every Nth green iteration; `0`/`1` = every
green iter). Emits an additive `mutation` event with the score. Implemented in
`engine/orrery_loop/verify.py` (`mutation_audit`).

> Related (separate flag): the **anti-false-green verifier** — a second,
> independent cheap-tier judge that sees the gate's **real test result** (the
> pass/fail/total counts plus a bounded, held-out-stripped tail of the gate
> output), the diff, and a frozen acceptance-criteria contract, and tries to
> *refute* "done" against that evidence. Enable with `"verify": { "enabled":
> true }` / `loop --verify`. Grounded in the same spec-gaming concern (Krakovna
> et al., 2020) plus "demand evidence, not claims"; a tool-running gate plus an
> independent judge that reads the gate's evidence beats a single
> self-assessment.

---

## 6. Run-quality metrics

**Idea.** Emit one run-quality `metrics` event at stop, folded purely from the
event stream. For an iterative loop, `pass@k` is misleading (the loop is one
learning trajectory, not k independent draws). Instead report **first-try-green**
plus the *cost* of getting there (iterations-to-green, dollars-to-green) and the
**regression rate** (how often it had to roll back) — efficiency and stability,
which `pass@k` throws away.

**Grounded in.** The `pass@k` convention from Codex/HumanEval (Chen et al.,
2021) and the critique that it doesn't fit iterative repair loops; framed around
trajectory cost the way agent evals (e.g. SWE-bench-style task suites) report
resolved-rate-at-cost.

**Enable.**

```jsonc
"metrics": { "emit": true }
```

```bash
loop --emit-metrics ...
```

Emits a single `metrics` event at stop with `firstTryGreen`, `itersToGreen`,
`costToGreen`, `rollbacks`, `regressionRate`, `totalIters`, `totalCost`,
`finalGreen` (PROTOCOL §2). Implemented in `engine/orrery_loop/metrics.py`
(`compute_metrics`).

---

## 7. Test-infrastructure lock

**Idea.** The always-on hash-lock freezes the *test files* (`gate.lockGlobs`,
default `*.test.ts`) so the agent can't weaken assertions. But a suite can also
be neutered from *outside* the test files — skip collection in `conftest.py`,
change a `vitest.config`/`jest.config`, point the runner at an empty dir. Turning
`lockInfra` on extends the same tamper detector over a curated set of **pure
test-infrastructure files**, so editing them mid-run trips `tampered` (a forced
not-green stop) exactly like editing a locked test file. The curated set
(`INFRA_LOCK_GLOBS` in `config.py`): `conftest.py`, `pytest.ini`, `tox.ini`,
`bunfig.toml`, `jest.config.*`, `vitest.config.*`, `playwright.config.*`,
`cypress.config.*`, `karma.conf.*`, `.mocharc.*`. It deliberately **excludes**
dual-purpose files (`pyproject.toml`, `package.json`, `setup.cfg`) that also
hold real dependencies/scripts — locking those would false-trip on a legitimate
edit. A project that keeps its test config in one of those should add that
specific file to `gate.lockGlobs` by hand.

**Grounded in.** Same spec-gaming concern as the held-out split — Krakovna et
al., *Specification Gaming* (DeepMind, 2020) and METR's reward-hacking findings
(2024–2025): if a check can be satisfied by editing the harness instead of the
code, an unattended agent eventually will. Freezing the harness removes that
surface.

**Enable.**

```jsonc
"gate": { "lockInfra": true, "lockGlobs": ["**/test_*.py"] }
```

No dedicated CLI flag — it is a property of the `gate` block (like `heldOut`).
Implemented in `engine/orrery_loop/core.py` (`_lock_glob_set`) over the existing
`engine/orrery_loop/hashlock.py` tamper detector.

---

## 8. Token-budget ceiling

**Idea.** The always-on `cost.ceilingUsd` bounds a run in **dollars** — but on a
flat-rate / subscription plan the CLI's dollar figure is ~meaningless, so a
dollar cap can't actually stop a runaway loop there. `stop.tokenCeiling` adds a
cumulative **token** budget (input + output + cache tokens, summed across
iterations). The moment cumulative tokens reach the ceiling the loop stops
not-green — the token-denominated twin of the cost ceiling, sitting right beside
it in the decision order (integrity → success → **budget** → drift → caps).
`0` (default) disables it, so a run with no ceiling behaves exactly as before.

**Grounded in.** The Claude Agent SDK's `max_budget_usd` guidance — *"setting a
budget is a good default for production agents"* — generalized to the unit that
actually binds a subscription run. (The engine already logs per-call token
telemetry in `token-usage` events; this turns that signal into a stop.)

**Enable.**

```jsonc
"stop": { "tokenCeiling": 2000000 }
```

No dedicated CLI flag (it is a `stop.*` knob, like `maxIters`). Token totals are
read tolerantly from each run's `usage` block via
`engine/orrery_loop/cache.py` (`total_tokens`); the stop itself is decided in
`engine/orrery_loop/decide.py` (`token ceiling <N> reached`).

---

## Parity guarantee

With **no** flags set and **no** non-default gate stages, the loop emits exactly
the baseline event set and makes the same decisions as the PowerShell reference
`loop.ps1`. Every capability above is additive: it only ever *adds* events or
*adds* a gate conjunct — none of them change the default trajectory. This is
enforced by the golden parity tests under `engine/tests/`.
