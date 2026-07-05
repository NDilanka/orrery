# Headless Quickstart

This guide is for running the Orrery engine from a terminal without starting the desktop app.
It uses the Python package in `engine/` and the runnable `examples/hello/` loop.

## Prerequisites

- Python 3.10 or newer
- Git
- For dry-runs: `pytest`
- For real runs: an authenticated agent CLI such as `claude`, `aider`, or `codex`

Real runs can spend model quota or API money. Start with the dry-run first.

## Install the Engine

From the repository root:

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e "./engine[dev]"
```

On Windows PowerShell, activate the venv with:

```powershell
.\.venv\Scripts\Activate.ps1
```

When the package is available from PyPI, the standalone install path is:

```bash
python -m pip install orrery-loop
```

The install provides these console commands:

- `loop`
- `loop-bmad`
- `loop-qa`
- `loop-supervise`
- `loop-stop`

## Run a Hello Loop Dry-Run

The `examples/hello/` project is intentionally broken so the gate starts red. A dry-run only
checks the wiring; it does not call an agent and does not spend quota.

```bash
cd examples/hello
loop --loop-json loop.json --cwd . --state-dir .loop --dry-run
```

Expected result:

```text
Baseline: 0/2 pass  green=False
DryRun OK
No runner calls, no quota spent.
```

The state directory is `.loop/` inside `examples/hello/`. During real runs, this directory holds
files such as:

- `log.jsonl`: event stream consumed by Orrery's visualizer
- `checkpoint.json`: resume state
- `progress.md`: human-readable run progress
- `STOP`: cooperative stop request written by `loop-stop`

## Run the Loop for Real

Only run this after the dry-run succeeds and your agent CLI is authenticated.

```bash
cd examples/hello
loop --loop-json loop.json --cwd . --state-dir .loop --runner claude
```

The engine reads `TASK.md`, edits the allowed source file, runs the configured test gate, commits
progress, and stops when the gate is green or a stop condition is reached. The hello loop has a
low cost ceiling in `loop.json`.

To use a different supported runner:

```bash
loop --loop-json loop.json --cwd . --state-dir .loop --runner aider
loop --loop-json loop.json --cwd . --state-dir .loop --runner codex
```

## Stop Safely

Request a cooperative stop from another terminal:

```bash
loop-stop --state-dir examples/hello/.loop
```

The engine honors the stop at the next safe checkpoint. To check or cancel a pending stop:

```bash
loop-stop --state-dir examples/hello/.loop --status
loop-stop --state-dir examples/hello/.loop --cancel
```

## BMAD Headless Pattern

`loop-bmad` drives a multi-story BMAD epic pipeline. It needs a project root and can also read
its settings from a `loop.json` file with a `bmad` block.

```bash
loop-bmad --project-root . --state-dir .loop --loop-json loop.json
```

For long unattended runs, wrap any loop command with `loop-supervise`:

```bash
loop-supervise --state-dir .loop --max-restarts 5 --window-min 90 -- \
  loop-bmad --project-root . --state-dir .loop --loop-json loop.json
```

## Author a Minimal `loop.json`

The smallest useful generic loop defines a task file, a state directory, and a gate command:

```json
{
  "id": "hello",
  "name": "hello loop",
  "kind": "generic",
  "stateDir": ".loop",
  "engine": {
    "task": "TASK.md",
    "gate": {
      "stages": [
        {
          "name": "test",
          "command": "pytest -q",
          "passPattern": "(\\d+) passed",
          "failPattern": "(\\d+) failed",
          "lockGlobs": ["**/test_*.py"]
        }
      ],
      "lockGlobs": ["**/test_*.py"]
    },
    "cost": {
      "ceilingUsd": 0.5,
      "alertPct": [50, 80, 100]
    },
    "stop": {
      "maxIters": 8,
      "stagnationLimit": 2,
      "plateauLimit": 3,
      "regressLimit": 3
    }
  }
}
```

Copy `examples/hello/loop.json` when you want a complete working seed.

## Clean Up

For the bundled hello example, reset the deliberate bug and remove generated state:

```bash
git restore examples/hello/src/mathlib.py
rm -rf examples/hello/.loop
```

On Windows PowerShell:

```powershell
git restore examples\hello\src\mathlib.py
Remove-Item -Recurse -Force examples\hello\.loop
```
