"""Hermetic integration tests for :func:`orrery_loop.core.run_loop` — the engine WIRING proof.

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

from orrery_loop import core
from orrery_loop.config import (
    CostConfig,
    EngineConfig,
    GateConfig,
    GateStage,
    StopConfig,
    VerifyConfig,
)
from orrery_loop.core import run_loop
from orrery_loop.runners.base import AgentResult, AgentRunner


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


def test_activity_heartbeat_written_during_execute(tmp_path):
    """run_loop wraps each execute call in a Heartbeat, mirroring bmad's ResilientRunner.

    Prove it end-to-end (same style as test_bmad_driver's heartbeat test): a runner that reads
    <stateDir>/activity.json DURING its own run() sees a beat already written, tagged with the
    "execute" phase and an "iter <N>" story label, carrying the camelCase liveness fields.
    """
    repo = _init_repo(tmp_path)
    state = tmp_path / "state"
    gate = _make_gate(repo)
    config = _config(repo, gate, max_iters=5)

    class CapturingRunner(AgentRunner):
        name = "mock-capture"

        def __init__(self, repo: Path, state: Path):
            self.repo = repo
            self.state = state
            self.calls = 0
            self.beat: dict | None = None

        def run(self, *, prompt, model, allowed_tools, permission_mode, max_turns, cwd,
                timeout_sec=0, resume_session=None, output_format="json"):
            self.calls += 1
            if self.beat is None:
                ap = self.state / "activity.json"
                if ap.exists():
                    self.beat = json.loads(ap.read_text(encoding="utf-8"))
            if self.calls >= 2:
                (self.repo / "target.txt").write_text("FIXED\n", encoding="utf-8")
            return AgentResult(raw="{}", text="worked", cost_usd=0.01)

    runner = CapturingRunner(repo, state)
    rc = run_loop(config, runner=runner, state_dir=state, cwd=repo)
    assert rc == 0

    assert runner.beat is not None, "no activity.json beat observed during execute"
    assert runner.beat.get("phase") == "execute"
    assert runner.beat.get("story") == "iter 1"
    for k in ("ts", "elapsedSec", "dirty", "pid"):
        assert k in runner.beat, f"missing {k} in beat {runner.beat}"
    # the file persists after the run (the final exit beat)
    assert (state / "activity.json").exists()


def test_raw_output_persisted_per_iteration(tmp_path):
    """Each iteration's raw agent output is written to .loop/run-<iter>.out (Task 2)."""
    repo = _init_repo(tmp_path)
    state = tmp_path / "state"
    gate = _make_gate(repo)
    config = _config(repo, gate, max_iters=5)
    runner = FixOnIter2Runner(repo)

    rc = run_loop(config, runner=runner, state_dir=state, cwd=repo)
    assert rc == 0
    assert runner.calls == 2

    out1 = state / "run-1.out"
    out2 = state / "run-2.out"
    assert out1.exists() and out1.read_text(encoding="utf-8") == "{}"
    assert out2.exists() and out2.read_text(encoding="utf-8") == "{}"


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


# ---------------------------------------------------------------------------
# Task 5 — checkpoint resume-command fidelity (orrery_loop.core._resume_command)
# ---------------------------------------------------------------------------


def test_resume_command_carries_real_task_cwd_state_dir():
    config = EngineConfig(task="TASK.md")
    cmd = core._resume_command(config, Path("D:/state"), Path("D:/work"))
    assert cmd.startswith("loop ")
    assert "--task TASK.md" in cmd
    assert "--cwd" in cmd and str(Path("D:/work")) in cmd
    assert "--state-dir" in cmd and str(Path("D:/state")) in cmd
    assert "--loop-json" not in cmd  # none configured -> stays off the command


def test_resume_command_includes_loop_json_when_configured():
    config = EngineConfig(task="TASK.md")
    config.loop_json = "D:/cfg/my engine.json"  # a path with a space, needs quoting
    cmd = core._resume_command(config, Path("D:/state"), Path("D:/work"))
    assert '--loop-json "D:/cfg/my engine.json"' in cmd


def test_checkpoint_resume_string_roundtrips_loop_json_end_to_end(tmp_path):
    """A launch with --loop-json must survive into the checkpoint's `resume` string (Task 5) —
    the SAME gap BMAD's _resume_command had, fixed the same way for the generic driver."""
    repo = _init_repo(tmp_path)
    state = tmp_path / "state"
    gate = _make_gate(repo)
    config = _config(repo, gate, max_iters=5)
    config.loop_json = str(tmp_path / "my-engine.json")

    rc = run_loop(config, runner=FixOnIter2Runner(repo), state_dir=state, cwd=repo)
    assert rc == 0

    cp = json.loads((state / "checkpoint.json").read_text(encoding="utf-8"))
    assert "--loop-json" in cp["resume"]
    assert str(tmp_path / "my-engine.json").replace("\\", "/") in cp["resume"].replace("\\", "/")
    assert f"--state-dir {state}" in cp["resume"] or f'--state-dir "{state}"' in cp["resume"]


def test_checkpoint_resume_string_omits_loop_json_when_none_used(tmp_path):
    repo = _init_repo(tmp_path)
    state = tmp_path / "state"
    gate = _make_gate(repo)
    config = _config(repo, gate, max_iters=5)  # config.loop_json defaults to ""

    rc = run_loop(config, runner=FixOnIter2Runner(repo), state_dir=state, cwd=repo)
    assert rc == 0
    cp = json.loads((state / "checkpoint.json").read_text(encoding="utf-8"))
    assert "--loop-json" not in cp["resume"]


# ---------------------------------------------------------------------------
# Task 1a — per-iteration wall-clock timeout threaded into the execute call
# ---------------------------------------------------------------------------


class RecordingTimeoutRunner(AgentRunner):
    """Fixes target.txt on call 2 (like FixOnIter2Runner) but records timeout_sec per call."""

    name = "mock-record-timeout"

    def __init__(self, repo: Path):
        self.repo = repo
        self.calls = 0
        self.timeouts: list[int] = []

    def run(self, *, prompt, model, allowed_tools, permission_mode, max_turns, cwd,
            timeout_sec=0, resume_session=None, output_format="json"):
        self.calls += 1
        self.timeouts.append(timeout_sec)
        if self.calls >= 2:
            (self.repo / "target.txt").write_text("FIXED\n", encoding="utf-8")
        return AgentResult(raw="{}", text="worked", cost_usd=0.01)


def test_iter_timeout_min_default_threads_3600_sec_to_runner(tmp_path):
    repo = _init_repo(tmp_path)
    state = tmp_path / "state"
    gate = _make_gate(repo)
    config = _config(repo, gate, max_iters=5)
    assert config.iter_timeout_min == 60  # the EngineConfig default
    runner = RecordingTimeoutRunner(repo)

    rc = run_loop(config, runner=runner, state_dir=state, cwd=repo)
    assert rc == 0
    assert runner.timeouts == [60 * 60, 60 * 60]


def test_iter_timeout_min_zero_disables_the_cap(tmp_path):
    repo = _init_repo(tmp_path)
    state = tmp_path / "state"
    gate = _make_gate(repo)
    config = _config(repo, gate, max_iters=5)
    config.iter_timeout_min = 0
    runner = RecordingTimeoutRunner(repo)

    rc = run_loop(config, runner=runner, state_dir=state, cwd=repo)
    assert rc == 0
    assert runner.timeouts == [0, 0]


def test_iter_timeout_min_custom_value_threads_through(tmp_path):
    repo = _init_repo(tmp_path)
    state = tmp_path / "state"
    gate = _make_gate(repo)
    config = _config(repo, gate, max_iters=5)
    config.iter_timeout_min = 5
    runner = RecordingTimeoutRunner(repo)

    rc = run_loop(config, runner=runner, state_dir=state, cwd=repo)
    assert rc == 0
    assert runner.timeouts == [300, 300]


def test_iter_timeout_hit_follows_the_existing_phase_timeout_path(tmp_path):
    """A runner that reports timed_out=True follows the SAME existing path a hung-runner
    timeout always did (phase-timeout event, iteration treated as unproductive) — Task 1a only
    threads the real config value in; the timeout-handling semantics are untouched."""
    repo = _init_repo(tmp_path)
    state = tmp_path / "state"
    gate = _make_gate(repo)
    config = _config(repo, gate, max_iters=3)
    config.iter_timeout_min = 1

    class TimingOutRunner(AgentRunner):
        name = "mock-timeout"

        def run(self, *, prompt, model, allowed_tools, permission_mode, max_turns, cwd,
                timeout_sec=0, resume_session=None, output_format="json"):
            return AgentResult(raw="", text="", timed_out=True)

    rc = run_loop(config, runner=TimingOutRunner(), state_dir=state, cwd=repo)
    assert rc == 1
    events = _events(state / "log.jsonl")
    timeouts = [e for e in events if e["event"] == "phase-timeout"]
    assert len(timeouts) == 3
    assert timeouts[0]["timeoutSec"] == 60


# ---------------------------------------------------------------------------
# Task 3 — crash-safe mutation audit (durable on-disk backup + INIT recovery)
# ---------------------------------------------------------------------------


def _mutation_gate_stages():
    """Gate stages for the mutation-audit tests: always green (so every mutant 'survives'
    unless the caller's probe raises), used with a callable command like _make_gate."""
    return [{"name": "test", "command": lambda: ("1 pass 0 fail", 0)}]


def test_mutation_audit_writes_a_durable_backup_then_cleans_it_up(tmp_path):
    repo = _init_repo(tmp_path)
    state = tmp_path / "state"
    state.mkdir()
    target = repo / "target.txt"
    target.write_text("x = 1\nif x == 1:\n    pass\n", encoding="utf-8")

    seen_backup: dict = {}

    def probe_gate():
        # Called from inside run_tests -> the on-disk backup must exist here, holding the
        # ORIGINAL content, while `target` itself has just been overwritten with the mutant.
        backup = core._mutation_backup_path(state, repo, target)
        seen_backup["exists_during"] = backup.exists()
        seen_backup["content_during"] = backup.read_text(encoding="utf-8") if backup.exists() else None
        return ("1 pass 0 fail", 0)  # green -> the mutant "survives"

    stages = [{"name": "test", "command": probe_gate}]
    config = EngineConfig(task="TASK.md")

    score = core._run_mutation_audit(config, stages, repo, 1, lambda e: None, state)

    assert seen_backup["exists_during"] is True
    assert seen_backup["content_during"] == "x = 1\nif x == 1:\n    pass\n"
    assert score is not None
    # cleaned up: no leftover backup, target restored to its original content
    backup = core._mutation_backup_path(state, repo, target)
    assert not backup.exists()
    assert target.read_text(encoding="utf-8") == "x = 1\nif x == 1:\n    pass\n"


def test_mutation_audit_restores_target_even_when_the_gate_raises(tmp_path):
    """Any exception inside run_tests still restores from the ON-DISK backup via `finally` —
    proving the backup (not the in-memory `original` string) is what's used for the restore."""
    repo = _init_repo(tmp_path)
    state = tmp_path / "state"
    state.mkdir()
    target = repo / "target.txt"
    original = "x = 1\nif x == 1:\n    pass\n"
    target.write_text(original, encoding="utf-8")

    def boom_gate():
        raise RuntimeError("simulated gate crash mid-mutation")

    stages = [{"name": "test", "command": boom_gate}]
    config = EngineConfig(task="TASK.md")

    try:
        core._run_mutation_audit(config, stages, repo, 1, lambda e: None, state)
    except RuntimeError:
        pass  # the gate's own exception is allowed to propagate; what matters is the restore

    assert target.read_text(encoding="utf-8") == original
    backup = core._mutation_backup_path(state, repo, target)
    assert not backup.exists()  # the outer finally still cleaned it up


def test_recover_mutation_backups_restores_a_leftover_backup_at_init(tmp_path):
    """Simulates a hard-kill mid-mutation: a backup was written but never cleaned up because
    the process died before its in-process `finally` ran. The NEXT INIT must restore it."""
    repo = _init_repo(tmp_path)
    state = tmp_path / "state"
    state.mkdir()
    progress_path = state / "progress.md"

    target = repo / "target.txt"
    original = "ORIGINAL\n"
    target.write_text(original, encoding="utf-8")

    backup = state / "mutation-backup" / "target.txt"
    backup.parent.mkdir(parents=True)
    backup.write_text(original, encoding="utf-8")
    # simulate the crash: the file was left mutated on disk
    target.write_text("MUTATED-AND-BROKEN\n", encoding="utf-8")

    core._recover_mutation_backups(state, repo, progress_path)

    assert target.read_text(encoding="utf-8") == original
    assert not backup.exists()
    assert "Mutation-audit recovery" in (progress_path.read_text(encoding="utf-8") or "")


def test_recover_mutation_backups_is_a_no_op_when_none_exist(tmp_path):
    repo = _init_repo(tmp_path)
    state = tmp_path / "state"
    state.mkdir()
    progress_path = state / "progress.md"
    core._recover_mutation_backups(state, repo, progress_path)  # must not raise
    assert not progress_path.exists()


def test_run_loop_recovers_leftover_mutation_backup_before_baseline_gate(tmp_path):
    """End-to-end: a leftover backup from a previous hard crash is restored at INIT, BEFORE the
    baseline gate reads the tree — so a stale-mutated target.txt doesn't corrupt the baseline."""
    repo = _init_repo(tmp_path)
    state = tmp_path / "state"
    state.mkdir()

    # target.txt already contains "FIXED" in the real repo (a crash happened AFTER the agent's
    # real fix landed, but mid a later mutation-audit pass that mutated it back to something
    # broken and never got to restore it).
    (repo / "target.txt").write_text("FIXED\n", encoding="utf-8")
    backup = state / "mutation-backup" / "target.txt"
    backup.parent.mkdir(parents=True)
    backup.write_text("FIXED\n", encoding="utf-8")
    (repo / "target.txt").write_text("BROKEN-BY-CRASHED-MUTATION\n", encoding="utf-8")

    gate = _make_gate(repo)
    config = _config(repo, gate, max_iters=5)

    # Already-green baseline (once recovered) -> a clean green stop with NO runner calls.
    class ExplodingRunner(AgentRunner):
        name = "boom"

        def run(self, **kwargs):  # pragma: no cover - must not be called
            raise AssertionError("runner must not run: baseline should already be green")

    rc = run_loop(config, runner=ExplodingRunner(), state_dir=state, cwd=repo)
    assert rc == 0
    assert (repo / "target.txt").read_text(encoding="utf-8") == "FIXED\n"
    assert not backup.exists()
