# Contributing to Orrery

Thanks for your interest! Orrery is an autonomous coding-**loop** engine plus a live
**orbital visualizer**. This guide covers the layout, the one rule you must not break,
and how to set up each component.

Orrery is a best-effort personal project: issues and PRs are read and appreciated, but
there are no response-time guarantees. See [ROADMAP.md](ROADMAP.md) for where it's headed,
and [Discussions](https://github.com/NDilanka/orrery/discussions) for questions and ideas.

## Repository layout

```
engine/        # the loop engine — a pip-installable Python package (the port is complete)
orrery/        # the visualizer — a Tauri v2 + Svelte 5 + PixiJS desktop/web app
examples/      # self-contained example loops (e.g. examples/hello) the engine can drive
legacy/        # the original PowerShell engine + demo tasks (legacy/demo/), kept for reference
docs/          # docs: capabilities.md and the design notes in loop-engineering.md
```

For the engine's design rationale, see [`docs/loop-engineering.md`](docs/loop-engineering.md).

## The golden rule: the wire protocol is canonical

`orrery/PROTOCOL.md` is the **single source of truth** for the event/state shapes shared
across three implementations:

- the **engine** emits `.loop/log.jsonl` events + `checkpoint.json`,
- the **Rust** reducer (`orrery/src-tauri/src/reducer.rs`) consumes them,
- the **TypeScript** reducer (`orrery/src/lib/reduce.ts`) mirrors the Rust one.

If you change an event shape, you change it in **all four places in one PR**:
`PROTOCOL.md`, the engine emitter, `reducer.rs`, and `reduce.ts`. New event types are
**additive** (unknown events are logged-but-ignored), so prefer adding over mutating.
Engine output must stay byte-compatible with `PROTOCOL.md` — there are golden tests that
enforce this.

## Dev setup

### Engine (Python)

```bash
# from the repo root
python -m venv .venv && . .venv/Scripts/activate   # or `source .venv/bin/activate`
pip install -e "./engine[dev]"
pytest engine/tests
ruff check engine
```

### Orrery app (Tauri + Svelte)

Requires Node 18+ and a Rust toolchain.

```bash
cd orrery
npm install
npm run tauri dev      # dev window with HMR
npm run check          # svelte-check
cargo test --manifest-path src-tauri/Cargo.toml
```

## Workflow

1. Branch from the default branch (`feat/...`, `fix/...`); never commit to it directly.
2. Make focused changes; match the surrounding style.
3. **Run the relevant tests before opening a PR** (`pytest` for the engine, `cargo test`
   + `npm run check` for the app).
4. Keep PRs scoped; describe *what* changed and *why*.

## Code style

- **Python**: `ruff` (lint + format). Type hints on public functions. Keep the pure
  decision/event logic free of I/O so it stays unit-testable.
- **Rust**: `cargo fmt` + `cargo clippy` clean.
- **Svelte/TS**: `svelte-check` clean; Svelte 5 runes.

## Reporting bugs / proposing features

Open an issue with a minimal reproduction (for the engine, the smallest `TASK.md` + gate
command that shows it). For protocol or visualization changes, reference the relevant
`PROTOCOL.md` section.
