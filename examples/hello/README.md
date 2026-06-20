# `hello` — a runnable fix-until-green example (pytest gate)

A tiny, cross-platform demo of the Orrery loop engine. `src/mathlib.py` ships
with two **deliberate bugs**; the human-written tests in `tests/` fail until the
implementation is correct. The loop's job is to fix `mathlib.py` until
`pytest -q` is green — and **only** `mathlib.py` (the test file is hash-locked).

No `bun` required — the gate is `pytest`, so this runs anywhere Python does.

```
examples/hello/
  src/mathlib.py          the module under test — DELIBERATELY BROKEN
  tests/test_mathlib.py   the eval gate — human-written, the loop must NOT edit it
  pytest.ini             scopes collection to this example's tests
  TASK.md                 the goal + acceptance criteria the agent reads each iter
  loop.json               the engine config (generic kind, generic adapter)
```

## Prerequisites

- Python ≥ 3.10 with the engine installed (`pip install -e ./engine` from the
  repo root — see the root README).
- `pytest` on your `PATH` (it comes with `pip install -e "./engine[dev]"`, or
  `pip install pytest`).
- For a real run: an agent CLI on `PATH` — `claude` (default), `aider`, or
  `codex`.

## Run the dry-run (no quota, no agent)

The dry-run runs the gate **once** and prints the wiring — nothing is spent and
no agent is called. The gate command (`pytest -q`) runs in the **current process
working directory**, so launch the loop **from this example directory**:

```bash
cd examples/hello
loop --loop-json loop.json --cwd . --state-dir .loop --dry-run
```

Expected output (the baseline is **RED** — that's correct; the bug makes the
tests fail, which is exactly what a fix-until-green demo needs):

```
Baseline: 0/2 pass  green=False  task=TASK.md
DryRun OK — gate wired. git=True  bestPass=0  stages=[test] ...
No runner calls, no quota spent.
```

> Tip: if your `loop` is the repo venv's, call it explicitly, e.g.
> `../../.venv/Scripts/loop.exe` (Windows) / `../../.venv/bin/loop` (Unix), and
> make sure `pytest` resolves from the same venv.

## Run for real (spends quota — needs an agent CLI)

```bash
cd examples/hello
loop --loop-json loop.json --cwd . --state-dir .loop --runner claude
```

The loop will: read `TASK.md`, edit `src/mathlib.py`, re-run `pytest -q`, commit
on progress, and stop **green** when both tests pass (usually one iteration).
The cost ceiling is set low (`$0.50`) in `loop.json`. Other backends:
`--runner aider` or `--runner codex`.

Watch it in the visualizer by pointing Orrery at the produced
`examples/hello/.loop/log.jsonl` (see the root README → *The visualizer*).

When you're done, `git restore src/mathlib.py` (or `git checkout`) to reset the
deliberate bug, and delete the generated `.loop/` directory.

## TypeScript variants (`bun` gate)

The repo root also ships two **TypeScript** demos the same engine can drive,
using `bun test` as the gate instead of `pytest`:

- `src/roman.ts` + `src/roman.test.ts` — Roman-numeral conversion (subtractive
  cases + round-trip). Seeded loop id `roman`.
- `src/calc.ts` + `src/calc.test.ts` — a small expression calculator. Seeded
  loop id `calc`.

These need `bun` installed. Their loop definitions live under
`orrery/loops/<id>/loop.json`, and the original PowerShell driver is
`loop.ps1` (`pwsh -File loop.ps1 -DryRun`). The `hello` example above is the
pure-Python, no-extra-runtime equivalent.
