"""Regression tests for three event-emission PARITY fixes vs ``loop.ps1`` / PROTOCOL §2.

Each test drives :func:`orrery_loop.core.run_loop` hermetically (a real temp git repo + a callable
gate + a MockRunner — NO real claude, model, sleep, or network), the same harness style as
``tests/test_core.py``. They lock in:

- FIX 1: with verify ENABLED, a ``model{phase:"judge"}`` event at baseline and a
  ``verdict{pass:true}`` event appear; with verify DISABLED (default) neither does.
- FIX 2: a runner whose result is ``parse_failed=True`` (non-empty raw) emits a
  ``parse_error`` event and stops nonzero (loop.ps1:662); a parseable result does not.
- FIX 3: an unrecoverable quota limit emits the terminal ``stop`` and NO ``handoff`` event
  from the quota path (loop.ps1:660-663 raises no beacon there).
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
    def stage():
        body = (repo / "target.txt").read_text(encoding="utf-8")
        if "FIXED" in body:
            return ("1 pass 0 fail", 0)
        return ("0 pass 1 fail", 1)

    return stage


def _config(repo: Path, gate_callable, *, max_iters=5, verify=None) -> EngineConfig:
    return EngineConfig(
        task="TASK.md",
        gate=GateConfig(
            stages=[GateStage(name="test", command=gate_callable)],
            lock_globs=["*.locked"],  # nothing matches -> empty baseline map, no tamper
        ),
        cost=CostConfig(ceiling_usd=100.0),
        stop=StopConfig(max_iters=max_iters, stagnation_limit=99, plateau_limit=99, regress_limit=99),
        verify=verify or VerifyConfig(),
    )


def _events(log_path: Path) -> list[dict]:
    raw = log_path.read_text(encoding="utf-8")
    return [json.loads(ln) for ln in raw.splitlines() if ln.strip()]


# --- FIX 1: verify/verdict path is reachable when enabled ------------------


class FixWithJudgeRunner(AgentRunner):
    """Fixes target.txt on its 2nd EXECUTE call; answers judge calls with passing verdict JSON.

    The VERIFY pass calls ``run`` with ``max_turns==1`` + ``permission_mode=="plan"`` — we use
    that to distinguish a judge turn from an execute turn.
    """

    name = "mock-fix-judge"

    def __init__(self, repo: Path):
        self.repo = repo
        self.exec_calls = 0
        self.judge_calls = 0

    def run(self, *, prompt, model, allowed_tools, permission_mode, max_turns, cwd,
            timeout_sec=0, resume_session=None, output_format="json"):
        if max_turns == 1 and permission_mode == "plan":
            self.judge_calls += 1
            verdict_json = json.dumps(
                {"pass": True, "failingCriteria": [], "evidence": "diff matches AC",
                 "nextAction": "none"}
            )
            return AgentResult(raw=verdict_json, text=verdict_json, cost_usd=0.0)
        self.exec_calls += 1
        if self.exec_calls >= 2:
            (self.repo / "target.txt").write_text("FIXED\n", encoding="utf-8")
        return AgentResult(raw="{}", text="worked", cost_usd=0.01)


def test_verify_enabled_emits_judge_model_and_verdict(tmp_path):
    repo = _init_repo(tmp_path)
    state = tmp_path / "state"
    gate = _make_gate(repo)
    config = _config(repo, gate, verify=VerifyConfig(enabled=True))
    runner = FixWithJudgeRunner(repo)

    rc = run_loop(config, runner=runner, state_dir=state, cwd=repo)
    assert rc == 0

    events = _events(state / "log.jsonl")
    judge_models = [e for e in events if e["event"] == "model" and e.get("phase") == "judge"]
    verdicts = [e for e in events if e["event"] == "verdict"]
    assert len(judge_models) == 1, "judge model event must fire at baseline when verify enabled"
    assert verdicts, "a verdict event must be emitted when verify enabled + gate green"
    assert verdicts[-1]["pass"] is True
    assert runner.judge_calls >= 1


def test_verify_disabled_emits_no_judge_model_or_verdict(tmp_path):
    repo = _init_repo(tmp_path)
    state = tmp_path / "state"
    gate = _make_gate(repo)
    config = _config(repo, gate, verify=VerifyConfig())  # default: disabled
    runner = FixWithJudgeRunner(repo)

    rc = run_loop(config, runner=runner, state_dir=state, cwd=repo)
    assert rc == 0

    events = _events(state / "log.jsonl")
    assert not [e for e in events if e["event"] == "model" and e.get("phase") == "judge"]
    assert not [e for e in events if e["event"] == "verdict"]
    assert runner.judge_calls == 0


# --- FIX 2: parse_error fires on parse_failed (non-empty raw) --------------


class ParseFailedRunner(AgentRunner):
    """Returns a NON-EMPTY garbage result flagged parse_failed (the ClaudeRunner contract)."""

    name = "mock-parsefail"

    def run(self, *, prompt, model, allowed_tools, permission_mode, max_turns, cwd,
            timeout_sec=0, resume_session=None, output_format="json"):
        return AgentResult(raw="not json at all", is_error=True, parse_failed=True)


def test_parse_failed_nonempty_raw_emits_parse_error_and_stops(tmp_path):
    repo = _init_repo(tmp_path)
    state = tmp_path / "state"
    gate = _make_gate(repo)
    config = _config(repo, gate, max_iters=5)

    rc = run_loop(config, runner=ParseFailedRunner(), state_dir=state, cwd=repo)
    assert rc == 1

    events = _events(state / "log.jsonl")
    pe = [e for e in events if e["event"] == "parse_error"]
    assert len(pe) == 1
    assert pe[0]["iter"] == 1
    stops = [e for e in events if e["event"] == "stop"]
    assert len(stops) == 1 and stops[0]["green"] is False


def test_parseable_result_emits_no_parse_error(tmp_path):
    repo = _init_repo(tmp_path)
    state = tmp_path / "state"
    gate = _make_gate(repo)
    config = _config(repo, gate, max_iters=3)

    class GoodRunner(AgentRunner):
        name = "mock-good"

        def __init__(self, repo):
            self.repo = repo
            self.calls = 0

        def run(self, *, prompt, model, allowed_tools, permission_mode, max_turns, cwd,
                timeout_sec=0, resume_session=None, output_format="json"):
            self.calls += 1
            if self.calls >= 2:
                (self.repo / "target.txt").write_text("FIXED\n", encoding="utf-8")
            return AgentResult(raw="{}", text="ok", cost_usd=0.0)

    rc = run_loop(config, runner=GoodRunner(repo), state_dir=state, cwd=repo)
    assert rc == 0
    events = _events(state / "log.jsonl")
    assert not [e for e in events if e["event"] == "parse_error"]


# --- FIX 3: unrecoverable quota emits stop, NO handoff from the quota path --


class QuotaLimitedRunner(AgentRunner):
    """Always returns a quota-limited result, so the loop enters quota-survival every iter."""

    name = "mock-quota"

    def run(self, *, prompt, model, allowed_tools, permission_mode, max_turns, cwd,
            timeout_sec=0, resume_session=None, output_format="json"):
        return AgentResult(raw="{}", is_error=True, quota_limited=True)


def test_unrecoverable_quota_emits_stop_and_no_handoff(tmp_path, monkeypatch):
    repo = _init_repo(tmp_path)
    state = tmp_path / "state"
    gate = _make_gate(repo)
    config = _config(repo, gate, max_iters=5)

    # survive() returns False -> unrecoverable quota -> terminate.
    monkeypatch.setattr(core, "survive", lambda *a, **k: False)

    rc = run_loop(config, runner=QuotaLimitedRunner(), state_dir=state, cwd=repo)
    assert rc == 1

    events = _events(state / "log.jsonl")
    stops = [e for e in events if e["event"] == "stop"]
    assert len(stops) == 1
    assert stops[0]["green"] is False
    assert "quota limit" in stops[0]["reason"]
    # PARITY: loop.ps1 raises NO handoff in the quota path.
    assert not [e for e in events if e["event"] == "handoff"]
