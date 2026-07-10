# orrery-loop (the engine)

The **loop engine** for [Orrery](https://github.com/NDilanka/orrery) — a pip-installable,
cross-platform Python package that drives a coding agent in a **fix-until-green** loop with
an **external test gate as truth**. A faithful port of the original PowerShell engine
(`../legacy/loop.ps1` / `../legacy/loopcore.ps1`), verified byte-compatible with the wire
protocol in [`../orrery/PROTOCOL.md`](../orrery/PROTOCOL.md) via a golden corpus.

## Install

```bash
# from the repo root:
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

You don't have to start from the CLI: the Orrery desktop app's Tuning Console authors
`loop`, `loop-bmad`, and `loop-qa` loops from a recipe gallery (✦ new loop) and runs them
with live visualization — this README is the headless/hand-authored path.

If the `loop` script name is shadowed on your `PATH` (another tool ships a `loop` binary, or
the venv's `bin`/`Scripts` isn't first), run the generic loop via its module entry point
instead — it's the same `main()` the console script calls:

```bash
python -m orrery_loop --help     # identical usage/behavior to `loop`
```

```bash
# from the repo root (the example lives at the repo-root examples/hello, not under engine/)
# — fix the bundled example (dry-run spends nothing):
pip install -e ./engine
loop --loop-json examples/hello/loop.json --cwd examples/hello --state-dir examples/hello/.loop --dry-run
# for real (spends quota; needs an agent CLI on PATH):
loop --loop-json examples/hello/loop.json --cwd examples/hello --state-dir examples/hello/.loop --runner claude
```

## What's in the box

**Always-on guardrails** (no flags): external exit-code gate, cumulative cost ceiling
(plus an opt-in `stop.tokenCeiling` for subscription runs where dollars don't bind),
max-iters + stagnation/plateau/regress stops, test-file hash-lock + count-floor,
per-iteration git commits (rollback to best), quota survival (wait-and-resume across
resets), cross-platform process-tree kill, and a concurrency lock (`orrery_loop.lockfile` — ONE
`lock` file shared by `loop` / `loop-bmad` / `loop-qa`, so any two of them racing the same
state dir correctly serialize instead of silently double-running).

**Pluggable backends:** `--runner claude` (default) · `aider` · `codex`.

### Reliability for unattended runs (Wave A1 — "don't hang, don't lose work")

- **Wall-clock timeouts.** Every agent-spawning phase has a per-call timeout so a hung runner
  process can never block an overnight run forever; on expiry the process TREE is killed
  (`orrery_loop.proc.kill_tree`) and the iteration/phase follows its normal failure path (a
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
- **No orphaned children.** `orrery_loop.proc.run_with_timeout` kills the whole child process tree on
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
audit, run-quality metrics, an anti-false-green verifier, and a
test-infrastructure lock (`gate.lockInfra` — extend the hash-lock over
`conftest.py`/`vitest.config`/etc. so the suite can't be neutered from outside
the test files).

### Gate commands and shells

Each gate stage's `command` string (`loop.json` `engine.gate.stages[].command`) runs through
`subprocess.run(..., shell=True)` (`orrery_loop.gate._run_command`) — i.e. **`cmd.exe` on Windows,
`/bin/sh` on POSIX**. Those are different shells: `&&`/`||` chaining, quoting rules, glob
expansion, and builtins (`export`, `set`, `[[ ]]`) are NOT portable between them, and a command
written for one will often fail silently or parse-error on the other. Prefer a single simple
binary invocation per stage (`pytest -q`, `npx playwright test e2e/functional`) over shell
chains; if you truly need OS-specific logic, write it into a small per-OS script (`.ps1` /
`.sh`) and point the gate `command` at that script instead of inlining shell syntax in
`loop.json`.

### Experimental: in-session gate (`engine.sessionGate`, default `off`)

A prototype token optimization for the **generic** loop only (not BMAD, not QA): instead of N
cold `claude -p` cold starts — one per fix-until-green iteration — a single invocation loops
*inside* the session until the gate is green, collapsing the outer restarts. **The external gate
still runs afterward and remains the sole arbiter of green** — this only changes how many cold
starts happen, never whether a run is trusted.

`engine.sessionGate` takes one of three values:

- `off` (default) — no change. The prompt and the `claude` argv are byte-identical to a run
  without the knob.
- `stop-hook` — the loop writes a settings file (`<state-dir>/session-gate-settings.json`) and
  passes it via `--settings`. It installs a **Stop hook** that re-runs the gate's first (`test`)
  stage command as `<gate cmd> || exit 2`. Per the Claude Code hooks contract, a Stop hook that
  exits **code 2** *blocks* turn-end and feeds its stderr back to the agent, so a red gate keeps
  the session working; a green gate exits 0 and lets the turn end. Claude Code **auto-overrides**
  the hook after ~8 consecutive blocks (the documented backstop against an unsatisfiable gate
  looping forever). The gate command is embedded verbatim — it runs through the user's shell, so
  the same `cmd.exe`-vs-`/bin/sh` caveat as ordinary gate stages applies.
- `goal` — the loop prepends a `/goal the command `<gate cmd>` exits 0` line as the execute
  prompt's first line, asking the model to keep going until that condition holds. **Caveat:** the
  installed CLI (2.1.199) does not document `/goal` in `--help` or the CLI reference, so this mode
  is a best-effort prototype form and may be a no-op on some builds.

Both `stop-hook` and `goal` fall back to `off` when the first gate stage's `command` is a Python
callable (a test hook) rather than a shell string, since there is no shell form to install.

## Module map

```
orrery_loop/
  core.py        generic fix-until-green loop (the driver)
  cli.py         loop / loop-bmad / loop-qa / loop-stop / loop-supervise entrypoints
  driver_shell.py shared driver lifecycle: lock -> body -> release; checkpoint + STOP helpers
  decide.py      pure decision cores (Get-LoopDecision port + the shared regression floor)
  events.py      single source of truth for log.jsonl event shapes
  gate.py        multi-stage external gate (exit code is truth)
  quota.py       quota parsing + wait-and-resume survival
  hashlock.py    per-file test hash-lock (anti-tamper)
  lockfile.py    shared single-flight concurrency lock (loop / loop-bmad / loop-qa)
  supervise.py   restart-on-failure wrapper (loop-supervise; replaces supervise.ps1)
  configkeys.py  shared config-key loader: camel/snake resolve + unknown-key warnings
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

## Writing a new loop driver

All three shipped drivers (`loop`, `loop-bmad`, `loop-qa`) run inside the same shell —
`orrery_loop.driver_shell.run_driver` — and a new driver should too. The full contract lives in
`orrery_loop/driver_shell.py`'s module docstring; the short version:

1. **Entry shape.** Parse your config, then hand your orchestration to the shell:

   ```python
   from orrery_loop.driver_shell import run_driver

   def run(config, *, state_dir, ...) -> int:
       def body(state: Path) -> int:
           ...  # your orchestration; return 0 (ok / clean stop) or 1 (halt / handoff)
       return run_driver(state_dir, guard_label="my-driver", body=body)
   ```

   The shell creates the state dir, takes the shared single-flight lock (`<stateDir>/lock` —
   the ONE name every driver uses, so racing drivers serialize), runs `body` with the lock
   held, and releases it on every exit path. A refused lock returns **exit code 2** without
   calling `body`; never return 2 yourself.
2. **State files** (PROTOCOL §1) live in `state_dir`: append events to `log.jsonl` via
   `orrery_loop.logio.append_event` (compact JSON, one per line; reducers ignore unknown events, so
   adapter-specific events are fine — emit the §2 core set where your run maps onto it); write
   `checkpoint.json` ONLY through `orrery_loop.driver_shell.write_checkpoint_now` (one shape,
   consistent `updatedAt`/`cumUsd` rounding, a real `resume` command string).
3. **Cooperative stop.** Poll the `STOP` flag at your own safe boundaries with
   `orrery_loop.driver_shell.read_stop_request(stop_path, scope)`; when it says honor, write a
   checkpoint, emit `cooperative-stop`, delete the flag, and return 0. Nothing kills you
   mid-step — honoring promptly is your job.
4. **Liveness.** Wrap every long blocking agent call in `orrery_loop.heartbeat.Heartbeat`
   (`activity.json`) so a watcher can tell working from hung.
5. **Config.** Read your tuning from a namespaced block of the loop's `loop.json`
   (`{"myadapter": {...}}` — see PROTOCOL §7), resolving keys with `orrery_loop.configkeys.resolve`
   (camelCase AND snake_case both accepted) and warning on unrecognized keys with
   `orrery_loop.configkeys.warn_unknown_keys`.
6. **Keep decisions pure.** Put any go/no-go logic in pure functions with injected inputs
   (see `orrery_loop.decide`) and inject every side effect (runner, gate, emit) so your driver is
   testable with mocks — no network, no real agent.

**Frontend side.** The Orrery UI (Rust `control.rs` + TS `reduce.ts`) reduces `log.jsonl` into
one `RunState` shared by every adapter — it never special-cases a driver by name. Emit
[`PROTOCOL.md`](../orrery/PROTOCOL.md) §2's **core** events (`iter`/`stop`/`gate`/`verdict`/
`model`/`cost-alert`/quota-*/...) wherever your run maps onto them and you get the full UI for
free: cost/rate charts, item status, stop reasons, live durations. Anything adapter-specific you
also emit is **ignored by design** — both reducers skip unknown `event` values without error, so
new adapters never require a lockstep UI change (§2 "Adapter-specific events + artifacts"). The
precedent to copy is `loop-qa`: it maps its per-AC judging onto the core `verdict`/`gate`/`iter`
events for full UI support, and separately emits a driver-specific `qa-ac` event (plus
`findings/epic-<N>.json` / `report.md` artifact files) that the UI doesn't consume yet but a
human or downstream tool can.

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
