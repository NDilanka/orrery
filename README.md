# Orrery

**An autonomous AI coding-loop engine — and a live orbital visualizer to watch it run.**

[![CI](https://github.com/NDilanka/orrery/actions/workflows/ci.yml/badge.svg)](https://github.com/NDilanka/orrery/actions/workflows/ci.yml)

Orrery drives a coding agent (`claude`, `aider`, or `codex`) in a **fix-until-green**
loop: it picks up a task, lets the agent edit code, then runs a **real external test
gate** and uses the exit code — not the model's self-assessment — as the only truth.
It survives quota limits and crashes, commits per iteration so it can roll back, and
stops on green or hands off cleanly. The companion app (`orrery/`) renders that loop as
a living star-system you can watch and steer.

> The design rationale and the research behind it are in
> **[`loop-engineering.md`](loop-engineering.md)** — read that for the *why*. This README
> is the *how*.

**Status: alpha** (pre-1.0). APIs, the wire protocol, and engine internals may change.
See [SECURITY.md](SECURITY.md) before running an agent unattended against a real repo.

---

## How it works

A loop is **mechanism, not luck** — the control flow lives in *your* code, not the
model's. Each outer iteration runs six blocks (fresh context every time; state lives in
files + git, not the model's memory):

```
 1. DISCOVER  read the task (TASK.md) + prior state
 2. ASSEMBLE  build the prompt: spec + frozen acceptance criteria + last gate feedback
 3. EXECUTE   one agent turn (claude/aider/codex) edits the code
 4. VERIFY ★  the ORCHESTRATOR runs the gate (e.g. pytest) — exit code is truth
 5. PERSIST   log.jsonl + checkpoint.json + a git commit (per-iteration rollback)
 6. DECIDE    continue / roll back / stop / hand off
      └── repeat until: green | max iters | cost ceiling | stagnation | tamper
```

**★ Block 4 is the whole ballgame.** The gate is an *external* command the orchestrator
runs and reads the exit code of — never the model's word. This is what stops the classic
failure where an agent declares victory on tests it never really passed (or quietly
deletes the failing one). Orrery hardens it further: test files are **hash-locked** and
the test count can't drop without triggering a handoff.

**Recovery-first.** State is durable by construction — every productive iteration is a
git commit, every event is appended to `log.jsonl`, and a `checkpoint.json` records where
to resume. If the run dies (or hits a quota wall), resume = re-run. Why this matters, and
why hand-rolling the outer loop beats the native "just run it unattended" path, is laid
out in [`loop-engineering.md`](loop-engineering.md).

---

## Quickstart

**Requirements:** Python ≥ 3.10, an agent CLI on your `PATH` (`claude` by default; or
`aider` / `codex`), and a gate command your project already has (`pytest`, `bun test`, …).

```bash
# 1. install the engine (pip or uv)
pip install -e ./engine          # or:  uv pip install -e ./engine
#    add the dev extras for ruff + pytest:  pip install -e "./engine[dev]"

# 2. run the bundled example (a tiny Python project with a deliberate bug).
#    Dry-run first — runs the gate ONCE, spends nothing, calls no agent:
cd examples/hello
loop --loop-json loop.json --cwd . --state-dir .loop --dry-run
#    -> Baseline: 0/2 pass  green=False   (RED on purpose — that's the bug to fix)

# 3. let an agent fix it for real (spends quota; needs `claude` on PATH):
loop --loop-json loop.json --cwd . --state-dir .loop --runner claude
```

> The gate runs in the directory passed via `--cwd`, so you can launch `loop` from
> anywhere. Full walkthrough: [`examples/hello/README.md`](examples/hello/README.md).

**Watch it in the visualizer:** point Orrery at the `log.jsonl` the run produced (e.g.
`examples/hello/.loop/log.jsonl`) — see [The visualizer](#the-visualizer) below.

---

## The engine

A pip-installable Python package in [`engine/`](engine/). Three console scripts:

| Command | What it does |
|---|---|
| `loop`      | the generic fix-until-green loop (one task, one gate) |
| `loop-bmad` | the BMAD multi-story epic pipeline (a queue-driven applied loop) |
| `loop-stop` | cooperative safe-stop controller (request a clean stop at the next checkpoint) |

**Pluggable backends.** `--runner claude` (default), `--runner aider`, `--runner codex`.

**Always-on guardrails** (no flags needed): an external exit-code gate, a cumulative cost
ceiling, max-iters + stagnation/plateau/regress stops, test-file hash-lock + count-floor,
per-iteration git commits (rollback), quota-survival (wait-and-resume across resets), and
a concurrency lock.

**Research-backed capabilities** — all **OFF by default**, additive when on, full parity
when off. Each has a `loop.json` `engine.*` key *and* a CLI flag. Details + citations +
config snippets: **[`docs/capabilities.md`](docs/capabilities.md)**.

| Capability | One-line value | Enable | Grounded in |
|---|---|---|---|
| **Held-out test split** | a hidden suite the agent can't read → can't overfit to | gate stage `heldOut: true` | Krakovna et al. *Specification Gaming* (2020); METR reward-hacking (2024–25) |
| **Compact feedback** | feed back only the first failing test, not the whole log | `--compact-feedback` / `feedback.compact` | SWE-agent (Yang 2024); Self-Debug (Chen 2023) |
| **Lint/type pre-gate** | a fast static stage fails the gate before tests run | add an ordered gate stage | SWE-agent (Yang 2024) |
| **Cross-run lessons memory** | recall lessons from past runs into the cached prefix | `--memory` / `memory.enabled` | Reflexion (Shinn 2023); ExpeL (Zhao 2024); CoALA (Sumers 2024); ACE (2025) |
| **Mutation audit** | probe whether the suite would *notice* a wrong line | `--mutation-audit` / `verify.mutationAudit` | Just et al. (FSE 2014) |
| **Run-quality metrics** | first-try-green + iters/cost-to-green instead of pass@k | `--emit-metrics` / `metrics.emit` | pass@k critique (Chen 2021) |
| **Anti-false-green verifier** | a second, independent judge tries to *refute* "done" | `--verify` / `verify.enabled` | Krakovna et al. (2020) |

The original PowerShell scripts (`legacy/loop.ps1` / `legacy/loopcore.ps1`) are the
**reference implementation** (Windows / PowerShell), kept under [`legacy/`](legacy/) for
provenance + to regenerate the parity goldens; the Python package is a faithful port.
`engine/tests` holds the golden parity suite (375 tests).

---

## The visualizer

[`orrery/`](orrery/) is a GPU-accelerated **orrery** (clockwork solar-system) that turns
any loop into a living star-system you can watch from desktop, browser, or phone. It
**tails** the `log.jsonl` a loop emits and **reduces** it (per [`orrery/PROTOCOL.md`](orrery/PROTOCOL.md))
into a `RunState`: cost horizon, the six-phase cadence, per-item gate state, quota-night,
rest-states, and a verifier seal. The BMAD sprint loop is one seeded built-in — Orrery is
a general platform; you can author your own loops in its Tuning Console.

**Stack:** Tauri v2 (Rust core) + SvelteKit / Svelte 5 (runes) + PixiJS v8 + uPlot.

**Quickest — double-click to run:** on Windows, double-click **`run-orrery.bat`** at the
repo root (macOS/Linux: `bash run-orrery.sh`). It installs deps on first run, compiles, and
opens the desktop app — where you can author loops in the Tuning Console and start/stop
them. (First launch compiles the Rust core, so it takes a few minutes; needs Node 18+ and a
[Rust toolchain](https://rustup.rs).) Or run it manually:

```bash
cd orrery
npm install
npm run tauri dev      # desktop window (HMR) — drives loops LIVE (spawns + tails the engine)
npm run tauri build    # release bundle/installer
```

---

## Project layout

```
engine/                 the loop engine — a pip-installable Python package
  loop/                 config, gate, decide, runners (claude/aider/codex), capabilities
  tests/                golden parity suite
orrery/                 the visualizer — Tauri v2 + Svelte 5 + PixiJS app
  PROTOCOL.md           the canonical wire contract (events, RunState, commands)
  src-tauri/            Rust core: model, reducer, tailer, watcher, control
  src/lib/              transport, stores, adapters, Pixi render, panels
examples/hello/         a runnable, cross-platform example (pytest gate) ← start here
src/                    small TypeScript demo tasks (roman / calc) the engine can drive
docs/capabilities.md    the research-backed, default-off capabilities reference
loop-engineering.md     the design essay — the WHY behind all of this
legacy/                 the original PowerShell engine (reference impl; goldens source)
```

---

## Contributing & policies

- **[CONTRIBUTING.md](CONTRIBUTING.md)** — layout, dev setup, and the one rule (the wire
  protocol is canonical).
- **[orrery/PROTOCOL.md](orrery/PROTOCOL.md)** — the single source of truth for event /
  state shapes shared across the engine, the Rust reducer, and the TS reducer.
- **[SECURITY.md](SECURITY.md)** — the threat model for running an agent against your repo.
- **[CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md)** · **[LICENSE](LICENSE)** (MIT).

> CI runs on every push and pull request; the badge above tracks the default branch.
