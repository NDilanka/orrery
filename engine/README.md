# orrery-loop (the engine)

The **loop engine** for [Orrery](https://github.com/NDilanka/orrery) — a pip-installable,
cross-platform Python package that drives a coding agent in a **fix-until-green** loop with
an **external test gate as truth**. A faithful port of the original PowerShell engine
(`../loop.ps1` / `../loopcore.ps1`), verified byte-compatible with the wire protocol in
[`../orrery/PROTOCOL.md`](../orrery/PROTOCOL.md) via a golden corpus.

## Install

```bash
pip install -e ./engine            # or:  uv pip install -e ./engine
pip install -e "./engine[dev]"     # + pytest, ruff
```

Requires Python ≥ 3.10, an agent CLI on `PATH` (`claude` by default; `aider` / `codex`
also supported), and a gate command your project already has (`pytest`, `bun test`, …).

## Console scripts

| Command | What it does |
|---|---|
| `loop`      | the generic fix-until-green loop (one task, one gate) |
| `loop-bmad` | the BMAD multi-story epic pipeline (queue-driven applied loop; needs `--project-root`) |
| `loop-stop` | cooperative safe-stop controller (request a clean stop at the next checkpoint) |

```bash
# from the repo root — fix the bundled example (dry-run spends nothing):
loop --loop-json examples/hello/loop.json --cwd examples/hello --state-dir examples/hello/.loop --dry-run
# for real (spends quota; needs an agent CLI on PATH):
loop --loop-json examples/hello/loop.json --cwd examples/hello --state-dir examples/hello/.loop --runner claude
```

## What's in the box

**Always-on guardrails** (no flags): external exit-code gate, cumulative cost ceiling,
max-iters + stagnation/plateau/regress stops, test-file hash-lock + count-floor,
per-iteration git commits (rollback to best), quota survival (wait-and-resume across
resets), cross-platform process-tree kill, and a concurrency lock.

**Pluggable backends:** `--runner claude` (default) · `aider` · `codex`.

**Research-backed capabilities** — all **OFF by default**, additive when on, full parity
when off; each has a `loop.json` `engine.*` key and a CLI flag. See
[`../docs/capabilities.md`](../docs/capabilities.md): held-out/hidden test split, compact
first-failure feedback, lint/type pre-gate, cross-run lessons memory, mutation strength
audit, run-quality metrics, and an anti-false-green verifier.

## Module map

```
loop/
  core.py        generic fix-until-green loop (the driver)
  cli.py         loop / loop-bmad / loop-stop entrypoints
  decide.py      pure decision core (port of Get-LoopDecision)
  events.py      single source of truth for log.jsonl event shapes
  gate.py        multi-stage external gate (exit code is truth)
  quota.py       quota parsing + wait-and-resume survival
  hashlock.py    per-file test hash-lock (anti-tamper)
  cost.py cache.py verdict.py checkpoint.py config.py   pure helpers
  proc.py        cross-platform spawn + process-tree kill (replaces taskkill)
  logio.py       log.jsonl / checkpoint / answer-inbox / STOP-flag I/O
  prompts.py     stable-prefix-first prompt assembly (cache-friendly)
  gitutil.py     git helpers (commit / reset / status)
  runners/       AgentRunner ABC + claude / aider / codex backends
  bmad/          applied BMAD driver: sprint scan, phases, deciders, recovery
  memory/ verify.py feedback.py metrics.py   the default-off capabilities
```

## Tests

```bash
python -m pytest engine/tests -q     # full suite (pure logic + golden byte-parity)
ruff check engine
```

Golden event fixtures are generated from the authoritative PowerShell source via
`tests/gen_golden.ps1` (PowerShell 7) and assert the Python builders stay byte-compatible
with `PROTOCOL.md`.
