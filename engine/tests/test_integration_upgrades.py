"""Hermetic integration tests for the four engine upgrades wired into :func:`loop.core.run_loop`.

Each feature is DEFAULT-OFF: a default-config run is byte-for-byte the same event sequence as
before (the ``parity guard`` test re-asserts the green-path sequence). Every test drives the
real loop with a ``MockRunner`` + a temp git repo + CALLABLE gate stages — NO real claude, no
model, no network, no sleep — the same harness style as ``tests/test_core.py``.

Coverage:
  * memory     — green run records a green-outcome lesson; a later run's prompt recalls it; OFF
                 -> no memory file and no recall block in the prompt.
  * feedback   — compact flag -> progress.md feedback equals ``compact_feedback`` (raw absent);
                 OFF -> the raw gate dump is used.
  * held-out   — a hidden stage that FAILS while the visible stage passes never stops-green, the
                 agent never sees the hidden stage's section, and the gate event reports the TRUE
                 (not-green) result.
  * mutation   — flag on + green gate -> a ``mutation`` log entry carries a score and the target
                 file is unchanged on disk afterward (restore verified).
  * metrics    — ``--emit-metrics`` -> a ``metrics`` event at stop with firstTryGreen/itersToGreen;
                 OFF -> no ``metrics`` event.
  * parity     — a default-config run emits the SAME green-path event sequence as before.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from loop import cli
from loop.config import (
    CostConfig,
    EngineConfig,
    FeedbackConfig,
    GateConfig,
    GateStage,
    MemoryConfig,
    MetricsConfig,
    StopConfig,
    VerifyConfig,
)
from loop.core import run_loop
from loop.feedback import compact_feedback
from loop.runners.base import AgentResult, AgentRunner

# --- helpers (mirror tests/test_core.py) -----------------------------------


def _git(args, cwd):
    return subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, text=True)


def _init_repo(tmp_path: Path) -> Path:
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
    """Callable visible gate stage: green only once target.txt contains FIXED."""

    def stage():
        body = (repo / "target.txt").read_text(encoding="utf-8")
        if "FIXED" in body:
            return ("1 pass 0 fail", 0)
        return ("0 pass 1 fail", 1)

    return stage


def _config(
    repo: Path,
    stages: list[GateStage],
    *,
    max_iters: int = 5,
    feedback: FeedbackConfig | None = None,
    memory: MemoryConfig | None = None,
    metrics: MetricsConfig | None = None,
    verify: VerifyConfig | None = None,
    lock_globs=None,
) -> EngineConfig:
    return EngineConfig(
        task="TASK.md",
        gate=GateConfig(
            stages=stages,
            lock_globs=lock_globs if lock_globs is not None else ["*.locked"],
        ),
        cost=CostConfig(ceiling_usd=100.0),
        stop=StopConfig(max_iters=max_iters, stagnation_limit=99, plateau_limit=99, regress_limit=99),
        verify=verify or VerifyConfig(),
        feedback=feedback or FeedbackConfig(),
        memory=memory or MemoryConfig(),
        metrics=metrics or MetricsConfig(),
    )


def _events(log_path: Path) -> list[dict]:
    raw = log_path.read_text(encoding="utf-8")
    return [json.loads(ln) for ln in raw.splitlines() if ln.strip()]


# --- runners ---------------------------------------------------------------


class FixOnIter2Runner(AgentRunner):
    """Edits target.txt to FIXED on its 2nd call; records every prompt it is handed."""

    name = "mock-fix"

    def __init__(self, repo: Path):
        self.repo = repo
        self.calls = 0
        self.prompts: list[str] = []

    def run(self, *, prompt, model, allowed_tools, permission_mode, max_turns, cwd,
            timeout_sec=0, resume_session=None, output_format="json"):
        self.calls += 1
        self.prompts.append(prompt)
        if self.calls >= 2:
            (self.repo / "target.txt").write_text("FIXED\n", encoding="utf-8")
        return AgentResult(raw="{}", text="worked", cost_usd=0.01)


class NoOpRunner(AgentRunner):
    """Touches a scratch file every turn (tree changes) but never makes the gate green."""

    name = "mock-noop"

    def __init__(self, repo: Path):
        self.repo = repo
        self.calls = 0
        self.prompts: list[str] = []

    def run(self, *, prompt, model, allowed_tools, permission_mode, max_turns, cwd,
            timeout_sec=0, resume_session=None, output_format="json"):
        self.calls += 1
        self.prompts.append(prompt)
        (self.repo / f"scratch_{self.calls}.txt").write_text("noise", encoding="utf-8")
        return AgentResult(raw="{}", text="tried", cost_usd=0.01)


# ===========================================================================
# memory
# ===========================================================================


def test_memory_on_records_green_lesson_and_later_run_recalls_it(tmp_path):
    repo = _init_repo(tmp_path)
    state = tmp_path / "state"
    mem_path = tmp_path / "mem.jsonl"
    cfg = _config(
        repo,
        [GateStage(name="test", command=_make_gate(repo))],
        memory=MemoryConfig(enabled=True, path=str(mem_path)),
    )

    rc = run_loop(cfg, runner=FixOnIter2Runner(repo), state_dir=state, cwd=repo)
    assert rc == 0

    # memory file exists and carries a green-outcome lesson
    assert mem_path.exists()
    lessons = [json.loads(ln) for ln in mem_path.read_text(encoding="utf-8").splitlines() if ln.strip()]
    assert any(le["outcome"] == "green" for le in lessons), "a green-outcome lesson must be recorded"

    # a LATER run's first prompt includes the recalled block (memory carries across runs)
    (repo / "target.txt").write_text("BROKEN\n", encoding="utf-8")
    _git(["add", "-A"], repo)
    _git(["commit", "-q", "-m", "reset"], repo)
    runner2 = FixOnIter2Runner(repo)
    state2 = tmp_path / "state2"
    rc2 = run_loop(cfg, runner=runner2, state_dir=state2, cwd=repo)
    assert rc2 == 0
    assert runner2.prompts, "the runner must have received at least one prompt"
    assert "Lessons from prior runs:" in runner2.prompts[0]


def test_memory_off_no_file_and_no_recall_block(tmp_path):
    repo = _init_repo(tmp_path)
    state = tmp_path / "state"
    cfg = _config(repo, [GateStage(name="test", command=_make_gate(repo))])  # memory OFF

    runner = FixOnIter2Runner(repo)
    rc = run_loop(cfg, runner=runner, state_dir=state, cwd=repo)
    assert rc == 0

    # no memory file is created when memory is off
    assert not (state / "memory.jsonl").exists()
    # and no recall block leaks into the prompt
    assert runner.prompts
    assert all("Lessons from prior runs:" not in p for p in runner.prompts)


# ===========================================================================
# compact feedback
# ===========================================================================


def test_compact_feedback_on_uses_compact_block_not_raw(tmp_path):
    repo = _init_repo(tmp_path)
    state = tmp_path / "state"
    # never fixes -> the gate stays red so feedback is written every iter
    cfg = _config(
        repo,
        [GateStage(name="test", command=_make_gate(repo))],
        max_iters=2,
        feedback=FeedbackConfig(compact=True),
    )

    rc = run_loop(cfg, runner=NoOpRunner(repo), state_dir=state, cwd=repo)
    assert rc == 1  # never green

    from loop.gate import run_gate

    gate_result = run_gate([{"name": "test", "command": _make_gate(repo)}])
    expected = compact_feedback(gate_result)
    assert expected, "the red gate must produce a non-empty compact feedback block"

    progress = (state / "progress.md").read_text(encoding="utf-8")
    assert expected in progress, "compact feedback block must appear in the volatile steer"
    # the RAW multi-line stage dump must NOT be present (the section header is the tell)
    assert "### stage 'test' (exit=" not in progress


def test_compact_feedback_off_uses_raw_dump(tmp_path):
    repo = _init_repo(tmp_path)
    state = tmp_path / "state"
    cfg = _config(
        repo,
        [GateStage(name="test", command=_make_gate(repo))],
        max_iters=2,
    )  # feedback compact OFF

    rc = run_loop(cfg, runner=NoOpRunner(repo), state_dir=state, cwd=repo)
    assert rc == 1

    progress = (state / "progress.md").read_text(encoding="utf-8")
    # today's behavior: the raw gate dump (with the per-stage section header) is the steer
    assert "### stage 'test' (exit=" in progress


# ===========================================================================
# held-out (hidden) test split
# ===========================================================================


def test_held_out_failing_stage_blocks_green_and_is_hidden(tmp_path):
    repo = _init_repo(tmp_path)
    state = tmp_path / "state"

    def hidden_stage():
        # the hidden suite always FAILS; its output carries a secret marker that must not leak
        return ("HIDDEN_SECRET_ASSERTION failed", 1)

    stages = [
        GateStage(name="visible", command=_make_gate(repo)),
        GateStage(name="hidden", command=hidden_stage, held_out=True),
    ]
    cfg = _config(repo, stages, max_iters=3)

    # the runner fixes the VISIBLE gate on iter 2, but the hidden stage still fails
    runner = FixOnIter2Runner(repo)
    rc = run_loop(cfg, runner=runner, state_dir=state, cwd=repo)
    assert rc == 1, "a failing held-out stage must prevent a green stop"

    events = _events(state / "log.jsonl")
    stops = [e for e in events if e["event"] == "stop"]
    assert stops and stops[-1]["green"] is False

    # the gate event reports the TRUE combined (not-green) result
    gates = [e for e in events if e["event"] == "gate"]
    assert gates and all(g["green"] is False for g in gates)

    # the agent NEVER sees the hidden stage section or its secret marker
    progress_path = state / "progress.md"
    progress = progress_path.read_text(encoding="utf-8") if progress_path.exists() else ""
    assert "HIDDEN_SECRET_ASSERTION" not in progress
    assert "### stage 'hidden' (exit=" not in progress
    assert all("HIDDEN_SECRET_ASSERTION" not in p for p in runner.prompts)


def test_held_out_lock_globs_merged_into_tamper_set(tmp_path):
    """A held-out stage's lock_globs are merged into the hash-lock set, so editing the hidden
    test file trips the tamper detector (and the loop stops not-green)."""
    repo = _init_repo(tmp_path)
    state = tmp_path / "state"

    # a hidden test file that the held-out stage declares as locked
    hidden_test = repo / "hidden_suite.locktest"
    hidden_test.write_text("original hidden assertions\n", encoding="utf-8")
    _git(["add", "-A"], repo)
    _git(["commit", "-q", "-m", "hidden"], repo)

    def hidden_stage():
        return ("1 pass 0 fail", 0)  # hidden suite passes

    stages = [
        GateStage(name="visible", command=_make_gate(repo)),
        GateStage(name="hidden", command=hidden_stage, held_out=True, lock_globs=["*.locktest"]),
    ]
    cfg = _config(repo, stages, max_iters=3)

    class TamperRunner(AgentRunner):
        name = "mock-tamper"

        def __init__(self, repo):
            self.repo = repo
            self.calls = 0

        def run(self, **kwargs):
            self.calls += 1
            # green the visible gate AND edit the locked hidden test file -> tamper
            (self.repo / "target.txt").write_text("FIXED\n", encoding="utf-8")
            (self.repo / "hidden_suite.locktest").write_text("WEAKENED\n", encoding="utf-8")
            return AgentResult(raw="{}", text="t", cost_usd=0.01)

    rc = run_loop(cfg, runner=TamperRunner(repo), state_dir=state, cwd=repo)
    assert rc == 1, "editing a held-out locked test file must trip tamper -> not green"

    events = _events(state / "log.jsonl")
    stops = [e for e in events if e["event"] == "stop"]
    assert stops and stops[-1]["green"] is False
    assert "locked test file" in stops[-1]["reason"]


# ===========================================================================
# mutation audit
# ===========================================================================


class FixImplRunner(AgentRunner):
    """Writes a green ``impl.py`` (with mutable operators) on its 2nd call."""

    GREEN_SOURCE = "def ok():\n    return 1 == 1 and True\n"

    name = "mock-fiximpl"

    def __init__(self, repo: Path):
        self.repo = repo
        self.calls = 0
        self.prompts: list[str] = []

    def run(self, *, prompt, model, allowed_tools, permission_mode, max_turns, cwd,
            timeout_sec=0, resume_session=None, output_format="json"):
        self.calls += 1
        self.prompts.append(prompt)
        if self.calls >= 2:
            (self.repo / "impl.py").write_text(self.GREEN_SOURCE, encoding="utf-8")
        return AgentResult(raw="{}", text="worked", cost_usd=0.01)


def test_mutation_audit_logs_score_and_restores_file(tmp_path):
    repo = _init_repo(tmp_path)
    state = tmp_path / "state"

    # impl.py is BOTH the gate's subject and the mutation target: green only once it contains
    # the marker AND ``1 == 1`` holds, so operator mutants (== -> !=) are actually KILLED.
    impl = repo / "impl.py"
    impl.write_text("def ok():\n    return 1 != 1 and True\n", encoding="utf-8")  # red at baseline
    _git(["add", "-A"], repo)
    _git(["commit", "-q", "-m", "impl"], repo)

    def gate_stage():
        body = (repo / "impl.py").read_text(encoding="utf-8")
        # green when the impl evaluates ok() truthy: the marker "1 == 1 and True" present
        if "1 == 1 and True" in body:
            return ("1 pass 0 fail", 0)
        return ("0 pass 1 fail", 1)

    cfg = _config(
        repo,
        [GateStage(name="test", command=gate_stage)],
        max_iters=3,
        verify=VerifyConfig(mutation_audit=True, mutation_every=1),
    )

    runner = FixImplRunner(repo)
    rc = run_loop(cfg, runner=runner, state_dir=state, cwd=repo)
    assert rc == 0

    events = _events(state / "log.jsonl")
    muts = [e for e in events if e["event"] == "mutation"]
    assert muts, "a mutation log entry must be emitted on a green gate with mutation_audit on"
    assert "score" in muts[0]
    assert 0.0 <= muts[0]["score"] <= 1.0
    assert muts[0]["mutants"] >= 1, "the impl has mutable operators -> mutants generated"

    # the target file is byte-identical on disk afterward (restore verified by the finally block)
    assert impl.read_text(encoding="utf-8") == FixImplRunner.GREEN_SOURCE


# ===========================================================================
# metrics
# ===========================================================================


def test_emit_metrics_appends_metrics_event_at_stop(tmp_path):
    repo = _init_repo(tmp_path)
    state = tmp_path / "state"
    cfg = _config(
        repo,
        [GateStage(name="test", command=_make_gate(repo))],
        metrics=MetricsConfig(emit=True),
    )

    rc = run_loop(cfg, runner=FixOnIter2Runner(repo), state_dir=state, cwd=repo)
    assert rc == 0

    events = _events(state / "log.jsonl")
    metrics = [e for e in events if e["event"] == "metrics"]
    # one LIVE metrics event per iteration (2 iters) plus the ONE authoritative event at
    # shutdown -> 3 total. Reducers treat repeated `metrics` events as last-write-wins, so
    # only the FINAL one (the shutdown fold, which sees the stop event too) is authoritative.
    assert len(metrics) == 3
    m = metrics[-1]
    assert "firstTryGreen" in m and "itersToGreen" in m
    assert m["finalGreen"] is True
    # the loop ran two iters and reached green; compute_metrics folds it from the live stream.
    assert m["itersToGreen"] is not None
    assert m["totalIters"] == 2
    assert m["totalCost"] > 0.0
    # the FINAL metrics event is the LAST line (emitted at stop, after the stop event)
    assert events[-1]["event"] == "metrics"


def test_live_metrics_event_emitted_after_each_iteration(tmp_path):
    """A `metrics` event is appended after EVERY iter event (Task 3's live cadence), not only
    at stop -- so a watching UI gets a fresh run-quality read mid-run instead of only at the end.
    """
    repo = _init_repo(tmp_path)
    state = tmp_path / "state"
    cfg = _config(
        repo,
        [GateStage(name="test", command=_make_gate(repo))],
        metrics=MetricsConfig(emit=True),
    )

    rc = run_loop(cfg, runner=FixOnIter2Runner(repo), state_dir=state, cwd=repo)
    assert rc == 0

    events = _events(state / "log.jsonl")
    kinds = [e["event"] for e in events]
    iter_idxs = [i for i, k in enumerate(kinds) if k == "iter"]
    assert len(iter_idxs) == 2

    # a live metrics event immediately follows EACH iter event.
    for iter_i in iter_idxs:
        assert events[iter_i + 1]["event"] == "metrics", (
            f"iter event at {iter_i} not immediately followed by a live metrics event: {kinds}"
        )

    # after iter 1 (not yet green), the live snapshot reflects 1 iteration so far.
    live_after_iter1 = events[iter_idxs[0] + 1]
    assert live_after_iter1["totalIters"] == 1
    assert live_after_iter1["finalGreen"] is False

    # after iter 2 (the green iter), totalIters already reflects it -- but finalGreen is still
    # False here: the authoritative `stop` event hasn't been emitted yet at this point.
    live_after_iter2 = events[iter_idxs[1] + 1]
    assert live_after_iter2["totalIters"] == 2
    assert live_after_iter2["finalGreen"] is False

    # the FINAL metrics event (the shutdown fold, after the stop event) IS authoritative.
    assert events[-1]["event"] == "metrics"
    assert events[-1]["finalGreen"] is True


def test_metrics_event_builder_shape_direct():
    """Direct unit test of the engine-v3 ``metrics_event`` builder (camelCase wire shape)."""
    from loop.events import metrics_event

    ev = metrics_event(
        first_try_green=False,
        iters_to_green=3,
        cost_to_green=0.12,
        rollbacks=1,
        regression_rate=0.25,
        total_iters=4,
        total_cost=0.5,
        final_green=True,
    )
    assert ev == {
        "event": "metrics",
        "firstTryGreen": False,
        "itersToGreen": 3,
        "costToGreen": 0.12,
        "rollbacks": 1,
        "regressionRate": 0.25,
        "totalIters": 4,
        "totalCost": 0.5,
        "finalGreen": True,
    }
    # null-safe optionals when no green was reached
    ev2 = metrics_event(False, None, None, 0, 0.0, 0, 0.0, False)
    assert ev2["itersToGreen"] is None and ev2["costToGreen"] is None


def test_metrics_off_no_metrics_event(tmp_path):
    repo = _init_repo(tmp_path)
    state = tmp_path / "state"
    cfg = _config(repo, [GateStage(name="test", command=_make_gate(repo))])  # metrics OFF

    rc = run_loop(cfg, runner=FixOnIter2Runner(repo), state_dir=state, cwd=repo)
    assert rc == 0

    events = _events(state / "log.jsonl")
    assert not [e for e in events if e["event"] == "metrics"]


def test_cli_flags_flip_the_right_config_fields():
    """Each new CLI flag sets exactly its config field; absent flags leave defaults (off)."""
    # all flags on
    args = cli.argparse.Namespace(
        loop_json=None, task="TASK.md", max_iters=2, iter_timeout_min=None, cost_ceiling=100.0,
        verify=False, compact_feedback=True, memory="m.jsonl", mutation_audit=True,
        emit_metrics=True,
    )
    cfg = cli._build_config(args)
    assert cfg.metrics.emit is True
    assert cfg.feedback.compact is True
    assert cfg.memory.enabled is True and cfg.memory.path == "m.jsonl"
    assert cfg.verify.mutation_audit is True

    # --memory with no PATH -> enabled, default path (None -> <state_dir>/memory.jsonl at runtime)
    args2 = cli.argparse.Namespace(
        loop_json=None, task="TASK.md", max_iters=None, iter_timeout_min=None, cost_ceiling=None,
        verify=False, compact_feedback=False, memory="", mutation_audit=False, emit_metrics=False,
    )
    cfg2 = cli._build_config(args2)
    assert cfg2.memory.enabled is True and cfg2.memory.path is None

    # all flags absent -> everything default-off (parity)
    args3 = cli.argparse.Namespace(
        loop_json=None, task="TASK.md", max_iters=None, iter_timeout_min=None, cost_ceiling=None,
        verify=False, compact_feedback=False, memory=cli._MEMORY_UNSET, mutation_audit=False,
        emit_metrics=False,
    )
    cfg3 = cli._build_config(args3)
    assert cfg3.metrics.emit is False
    assert cfg3.feedback.compact is False
    assert cfg3.memory.enabled is False
    assert cfg3.verify.mutation_audit is False


# ===========================================================================
# parity guard — default-config run == the pre-upgrade green-path sequence
# ===========================================================================


def test_parity_default_config_green_path_event_sequence_unchanged(tmp_path):
    """With ALL flags off, the green-path event sequence is exactly the pre-upgrade one.

    This re-asserts the invariants ``tests/test_core.py`` /
    ``tests/test_core_parity.py`` lock (without modifying them): the green run emits the
    iter events then a single ``stop{green:true}`` and NO new engine-v3 events (metrics,
    mutation) leak in by default.
    """
    repo = _init_repo(tmp_path)
    state = tmp_path / "state"
    cfg = _config(repo, [GateStage(name="test", command=_make_gate(repo))], max_iters=5)

    runner = FixOnIter2Runner(repo)
    rc = run_loop(cfg, runner=runner, state_dir=state, cwd=repo)
    assert rc == 0

    events = _events(state / "log.jsonl")
    kinds = [e["event"] for e in events]

    # exactly one green stop, after the iter events (test_core.py invariant)
    stops = [e for e in events if e["event"] == "stop"]
    assert len(stops) == 1 and stops[0]["green"] is True
    iter_idxs = [i for i, e in enumerate(events) if e["event"] == "iter"]
    stop_idx = kinds.index("stop")
    assert iter_idxs and max(iter_idxs) < stop_idx

    # NONE of the new engine-v3 events appear in a default run (parity preserved)
    assert "metrics" not in kinds
    assert "mutation" not in kinds
    # no judge model / verdict either (verify default off — test_core_parity invariant)
    assert not [e for e in events if e["event"] == "model" and e.get("phase") == "judge"]
    assert not [e for e in events if e["event"] == "verdict"]
