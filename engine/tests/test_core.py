"""Hermetic integration tests for :func:`loop.core.run_loop` — the engine WIRING proof.

NO real ``claude`` and NO real model: a ``MockRunner`` edits a target file in a real temp git
repo, and the gate is a CALLABLE stage that reads that file. This proves the already-built pure
modules are correctly wired into the live loop:

  green path : MockRunner fixes the file on iter 2 -> gate flips green -> a ``stop`` event with
               ``green=True`` is logged AFTER the ``iter`` events, ``checkpoint.json`` exists,
               and ``run_loop`` returns 0.
  handoff    : a MockRunner that never fixes it -> max-iters -> ``stop`` ``green=False`` -> 1.

git IS used (this is the only place git runs), but only inside the test's OWN temp repo.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from loop.config import (
    CostConfig,
    EngineConfig,
    GateConfig,
    GateStage,
    StopConfig,
    VerifyConfig,
)
from loop.core import run_loop
from loop.runners.base import AgentResult, AgentRunner


# --- helpers ---------------------------------------------------------------


def _git(args, cwd):
    return subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, text=True)


def _init_repo(tmp_path: Path) -> Path:
    """Create a real git repo with a target file whose content the gate reads."""
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "target.txt").write_text("BROKEN\n", encoding="utf-8")
    (repo / "TASK.md").write_text(
        "# Task\n\n## Acceptance Criteria\n- target.txt must contain FIXED\n",
        encoding="utf-8",
    )
    _git(["init"], repo)
    _git(["config", "user.email", "t@t.t"], repo)
    _git(["config", "user.name", "t"], repo)
    _git(["add", "-A"], repo)
    _git(["commit", "-q", "-m", "baseline"], repo)
    return repo


def _make_gate(repo: Path):
    """A CALLABLE gate stage: green only once target.txt contains FIXED.

    Returns ``(output, exit)`` so it never spawns a process — the gate is pure local I/O. The
    output carries ``N pass`` / ``N fail`` so the default count patterns parse a total.
    """

    def stage():
        body = (repo / "target.txt").read_text(encoding="utf-8")
        if "FIXED" in body:
            return ("1 pass 0 fail", 0)
        return ("0 pass 1 fail", 1)

    return stage


def _config(repo: Path, gate_callable, max_iters: int = 5) -> EngineConfig:
    """An EngineConfig with the callable gate stage and no locked-file globs to trip tamper."""
    return EngineConfig(
        task="TASK.md",
        gate=GateConfig(
            stages=[GateStage(name="test", command=gate_callable)],
            lock_globs=["*.locked"],  # nothing matches -> empty baseline map, no tamper
        ),
        cost=CostConfig(ceiling_usd=100.0),
        stop=StopConfig(max_iters=max_iters, stagnation_limit=99, plateau_limit=99, regress_limit=99),
        verify=VerifyConfig(),
    )


def _events(log_path: Path) -> list[dict]:
    raw = log_path.read_text(encoding="utf-8")
    return [json.loads(ln) for ln in raw.splitlines() if ln.strip()]


# --- runners ---------------------------------------------------------------


class FixOnIter2Runner(AgentRunner):
    """Edits target.txt to contain FIXED on its 2nd call, so the gate flips green on iter 2."""

    name = "mock-fix"

    def __init__(self, repo: Path):
        self.repo = repo
        self.calls = 0

    def run(self, *, prompt, model, allowed_tools, permission_mode, max_turns, cwd,
            timeout_sec=0, resume_session=None, output_format="json"):
        self.calls += 1
        if self.calls >= 2:
            (self.repo / "target.txt").write_text("FIXED\n", encoding="utf-8")
        return AgentResult(raw="{}", text="worked", cost_usd=0.01)


class NeverFixesRunner(AgentRunner):
    """Touches an unrelated file every turn (tree changes) but never makes the gate green."""

    name = "mock-stuck"

    def __init__(self, repo: Path):
        self.repo = repo
        self.calls = 0

    def run(self, *, prompt, model, allowed_tools, permission_mode, max_turns, cwd,
            timeout_sec=0, resume_session=None, output_format="json"):
        self.calls += 1
        (self.repo / f"scratch_{self.calls}.txt").write_text("noise", encoding="utf-8")
        return AgentResult(raw="{}", text="tried", cost_usd=0.01)


# --- tests -----------------------------------------------------------------


def test_green_path_stop_event_after_iters_and_checkpoint(tmp_path):
    repo = _init_repo(tmp_path)
    state = tmp_path / "state"
    gate = _make_gate(repo)
    config = _config(repo, gate, max_iters=5)
    runner = FixOnIter2Runner(repo)

    rc = run_loop(config, runner=runner, state_dir=state, cwd=repo)

    assert rc == 0
    log = state / "log.jsonl"
    assert log.exists()
    events = _events(log)

    # there is a stop event with green=True
    stops = [e for e in events if e["event"] == "stop"]
    assert len(stops) == 1
    assert stops[0]["green"] is True

    # iter events precede the stop event
    iter_idxs = [i for i, e in enumerate(events) if e["event"] == "iter"]
    stop_idx = next(i for i, e in enumerate(events) if e["event"] == "stop")
    assert iter_idxs, "expected at least one iter event"
    assert max(iter_idxs) < stop_idx

    # checkpoint exists
    assert (state / "checkpoint.json").exists()
    # the loop took exactly two execute turns (fixed on the 2nd)
    assert runner.calls == 2


def test_handoff_path_max_iters_stop_not_green_returns_1(tmp_path):
    repo = _init_repo(tmp_path)
    state = tmp_path / "state"
    gate = _make_gate(repo)
    config = _config(repo, gate, max_iters=3)
    runner = NeverFixesRunner(repo)

    rc = run_loop(config, runner=runner, state_dir=state, cwd=repo)

    assert rc == 1
    events = _events(state / "log.jsonl")
    stops = [e for e in events if e["event"] == "stop"]
    assert len(stops) == 1
    assert stops[0]["green"] is False
    assert "max iterations" in stops[0]["reason"]
    assert (state / "checkpoint.json").exists()
    assert runner.calls == 3


def test_dry_run_does_not_call_runner(tmp_path):
    repo = _init_repo(tmp_path)
    state = tmp_path / "state"
    gate = _make_gate(repo)
    config = _config(repo, gate)

    class ExplodingRunner(AgentRunner):
        name = "boom"

        def run(self, **kwargs):  # pragma: no cover - must never be called
            raise AssertionError("runner called during dry-run")

    rc = run_loop(config, runner=ExplodingRunner(), state_dir=state, cwd=repo, dry_run=True)
    assert rc == 0
    # dry-run writes no events
    assert not (state / "log.jsonl").exists() or _events(state / "log.jsonl") == []


def test_concurrency_lock_refuses_second_run(tmp_path):
    repo = _init_repo(tmp_path)
    state = tmp_path / "state"
    state.mkdir()
    # plant a live lock owned by a DIFFERENT, still-alive process (a real child we control).
    import sys

    holder = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        (state / "lock").write_text(str(holder.pid), encoding="utf-8")
        gate = _make_gate(repo)
        config = _config(repo, gate)
        rc = run_loop(config, runner=FixOnIter2Runner(repo), state_dir=state, cwd=repo)
        assert rc == 2
    finally:
        holder.terminate()
        holder.wait()
