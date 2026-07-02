# orrery-loop (the engine)

The **loop engine** for [Orrery](https://github.com/NDilanka/orrery) — a pip-installable,
cross-platform Python package that drives a coding agent in a **fix-until-green** loop with
an **external test gate as truth**. A faithful port of the original PowerShell engine
(`../legacy/loop.ps1` / `../legacy/loopcore.ps1`), verified byte-compatible with the wire
protocol in [`../orrery/PROTOCOL.md`](../orrery/PROTOCOL.md) via a golden corpus.

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
| `loop-qa`   | AC-driven QA discovery pass (headless-browser judge + spec author; needs `--project-root`/`--manifest`) |
| `loop-stop` | cooperative safe-stop controller (request a clean stop at the next checkpoint) |
| `loop-supervise` | restart-on-failure wrapper for any of the above (thrash-guarded; see below) |

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
resets), cross-platform process-tree kill, and a concurrency lock (`loop.lockfile` — ONE
`lock` file shared by `loop` / `loop-bmad` / `loop-qa`, so any two of them racing the same
state dir correctly serialize instead of silently double-running).

**Pluggable backends:** `--runner claude` (default) · `aider` · `codex`.

### Reliability for unattended runs (Wave A1 — "don't hang, don't lose work")

- **Wall-clock timeouts.** Every agent-spawning phase has a per-call timeout so a hung runner
  process can never block an overnight run forever; on expiry the process TREE is killed
  (`loop.proc.kill_tree`) and the iteration/phase follows its normal failure path (a
  `phase-timeout` log event, then retried/halted like any other unproductive attempt).
  - Generic loop: `engine.iterTimeoutMin` (`loop.json`) / `--iter-timeout-min` (CLI). Default
    **60**, `0` disables.
  - BMAD: `bmad.createTimeoutMin` / `--create-timeout-min` (default **30**),
    `bmad.devTimeoutMin` / `--dev-timeout-min` (default **120**),
    `bmad.reviewTimeoutMin` / `--review-timeout-min` (default **60**),
    `bmad.retroTimeoutMin` / `--retro-timeout-min` (default **30**), and the pre-existing
    `bmad.smokeTimeoutMin` / `--smoke-timeout-min` (default 12). All `0` disables that phase's
    cap. A run launched with `--loop-json` carries these (and every other `bmad.*` tuning knob)
    into the checkpoint's `resume` string, so a Reignite/resume restores the full config instead
    of silently reverting to defaults.
- **No orphaned children.** `loop.proc.run_with_timeout` kills the whole child process tree on
  ANY exception path out of `communicate()` — not just a timeout — including a
  `KeyboardInterrupt`. Every CLI entrypoint (`loop`, `loop-bmad`, `loop-qa`) catches a
  `KeyboardInterrupt` around its driver call and exits `130` cleanly instead of a raw traceback
  (the lock is still released via the existing `finally`; `proc.py` has already torn down any
  child tree by then).
- **Crash-safe mutation audit.** Before the advisory mutation-strength probe (`--mutation-audit`)
  mutates a source file, it writes a durable backup to `<state-dir>/mutation-backup/<relative
  path>` and restores from THAT FILE (not just an in-memory string) after every mutant — so a
  hard kill (SIGKILL / power loss) mid-mutation can't leave the user's real source file
  corrupted. At the next `loop` INIT, a leftover backup (proof of a prior hard crash) is
  restored automatically, BEFORE the baseline gate reads the tree, and the recovery is noted in
  `progress.md` and on stdout.
- **`loop-supervise`** — a built-in restart-on-failure wrapper (replaces the old
  `supervise.ps1`), generalized to wrap any command:

  ```bash
  loop-supervise --state-dir .loop --max-restarts 5 --window-min 90 -- \
      loop-bmad --project-root . --state-dir .loop --loop-json bmad-engine.json
  ```

  Restarts the wrapped command on a nonzero exit unless a `STOP` file or a `STOP-SUPERVISOR`
  sentinel exists in `--state-dir`, or the thrash guard trips (more than `--max-restarts`
  restarts within the rolling `--window-min` window — the crash-loop signature). A clean exit
  (`0`) ends supervision immediately. Each restart appends a `supervisor-restart` event to
  `log.jsonl` and a human line to `supervisor.log`.

**Research-backed capabilities** — all **OFF by default**, additive when on, full parity
when off; each has a `loop.json` `engine.*` key and a CLI flag. See
[`../docs/capabilities.md`](../docs/capabilities.md): held-out/hidden test split, compact
first-failure feedback, lint/type pre-gate, cross-run lessons memory, mutation strength
audit, run-quality metrics, and an anti-false-green verifier.

## Module map

```
loop/
  core.py        generic fix-until-green loop (the driver)
  cli.py         loop / loop-bmad / loop-qa / loop-stop / loop-supervise entrypoints
  decide.py      pure decision core (port of Get-LoopDecision)
  events.py      single source of truth for log.jsonl event shapes
  gate.py        multi-stage external gate (exit code is truth)
  quota.py       quota parsing + wait-and-resume survival
  hashlock.py    per-file test hash-lock (anti-tamper)
  lockfile.py    shared single-flight concurrency lock (loop / loop-bmad / loop-qa)
  supervise.py   restart-on-failure wrapper (loop-supervise; replaces supervise.ps1)
  cost.py cache.py verdict.py checkpoint.py config.py   pure helpers
  proc.py        cross-platform spawn + process-tree kill (replaces taskkill)
  logio.py       log.jsonl / checkpoint / answer-inbox / STOP-flag I/O
  prompts.py     stable-prefix-first prompt assembly (cache-friendly)
  gitutil.py     git helpers (commit / reset / status)
  runners/       AgentRunner ABC + claude / aider / codex backends
  bmad/          applied BMAD driver: sprint scan, phases, deciders, recovery
  qa/            AC-driven QA discovery driver (loop-qa)
  memory/ verify.py feedback.py metrics.py   the default-off capabilities
```

## Tests

```bash
python -m pytest engine/tests -q     # full suite (pure logic + golden byte-parity)
ruff check engine
```

Golden event fixtures are generated from the authoritative PowerShell source via
`../legacy/gen_golden.ps1` (PowerShell 7) and assert the Python builders stay
byte-compatible with `PROTOCOL.md`. The committed corpus lives at
`tests/fixtures/golden_events.jsonl`; the test asserts against it and does not run the
generator.
