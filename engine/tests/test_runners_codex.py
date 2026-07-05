"""CodexRunner tests.

NO real ``codex`` is ever spawned: every test monkeypatches ``orrery_loop.runners.codex.proc`` with a
fake whose ``run_with_timeout`` returns a canned :class:`ProcResult`, then asserts the runner
builds the right argv and normalizes Codex's text output correctly.
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from orrery_loop.runners import ClaudeRunner, CodexRunner, get_runner
from orrery_loop.runners.base import AgentRunner


@dataclass
class FakeProcResult:
    """Mirrors orrery_loop.proc.ProcResult for the monkeypatched proc layer."""

    returncode: int = 0
    stdout: str = ""
    stderr: str = ""
    timed_out: bool = False


class FakeProc:
    """Records the argv/kwargs of the last call and returns a canned result."""

    def __init__(self, result: FakeProcResult):
        self.result = result
        self.calls: list[dict] = []

    def run_with_timeout(self, argv, *, cwd=None, timeout_sec=0, env=None):
        self.calls.append(
            {"argv": list(argv), "cwd": cwd, "timeout_sec": timeout_sec, "env": env}
        )
        return self.result


def _patch_proc(monkeypatch, result: FakeProcResult) -> FakeProc:
    fake = FakeProc(result)
    monkeypatch.setattr("orrery_loop.runners.codex.proc", fake)
    return fake


def _run(runner, **over):
    kwargs = dict(
        prompt="do it",
        model="sonnet",
        allowed_tools=["Read", "Edit"],
        permission_mode="acceptEdits",
        max_turns=12,
        cwd="/repo",
    )
    kwargs.update(over)
    return runner.run(**kwargs)


# --- registry --------------------------------------------------------------


def test_get_runner_returns_codex_instance():
    r = get_runner("codex")
    assert isinstance(r, CodexRunner)
    assert isinstance(r, AgentRunner)
    assert r.name == "codex"


def test_get_runner_claude_still_works():
    assert isinstance(get_runner("claude"), ClaudeRunner)


def test_codex_capability_flags_all_false():
    r = get_runner("codex")
    assert r.supports_quota_probe is False
    assert r.supports_sessions is False
    assert r.supports_cache_telemetry is False


# --- argv construction -----------------------------------------------------


def test_run_argv_has_expected_flags(monkeypatch):
    fake = _patch_proc(monkeypatch, FakeProcResult(stdout="done"))
    _run(get_runner("codex"), prompt="PROMPT", model="opus", cwd="/work", timeout_sec=600)
    argv = fake.calls[0]["argv"]
    assert argv[0] == "codex"
    assert argv[1] == "exec"
    assert argv[2] == "PROMPT"
    assert "--full-auto" in argv
    assert argv[argv.index("--model") + 1] == "gpt-4.1"
    # text-mode parse → we do NOT pass --json
    assert "--json" not in argv
    assert fake.calls[0]["timeout_sec"] == 600
    assert fake.calls[0]["cwd"] == "/work"


def test_run_passes_through_unknown_model_string(monkeypatch):
    fake = _patch_proc(monkeypatch, FakeProcResult(stdout="ok"))
    _run(get_runner("codex"), model="o3-mini")
    argv = fake.calls[0]["argv"]
    assert argv[argv.index("--model") + 1] == "o3-mini"


# --- output normalization --------------------------------------------------


def test_run_success_text_and_not_error(monkeypatch):
    _patch_proc(monkeypatch, FakeProcResult(returncode=0, stdout="task complete"))
    res = _run(get_runner("codex"))
    assert res.is_error is False
    assert res.text == "task complete"
    assert res.raw == "task complete"
    assert res.parse_failed is False
    assert res.timed_out is False
    assert res.cost_usd == 0.0


def test_run_nonzero_exit_is_error(monkeypatch):
    _patch_proc(monkeypatch, FakeProcResult(returncode=2, stdout="partial"))
    res = _run(get_runner("codex"))
    assert res.is_error is True


def test_run_error_marker_in_output_is_error(monkeypatch):
    _patch_proc(
        monkeypatch,
        FakeProcResult(returncode=0, stdout='{"error": "something broke"}'),
    )
    res = _run(get_runner("codex"))
    assert res.is_error is True


def test_run_quota_limited_from_429_text(monkeypatch):
    _patch_proc(
        monkeypatch,
        FakeProcResult(returncode=1, stdout="HTTP 429: rate-limit exceeded"),
    )
    res = _run(get_runner("codex"))
    assert res.quota_limited is True
    assert res.is_error is True


def test_run_quota_limited_from_retry_after_header(monkeypatch):
    # Even if the strong-phrase matcher missed it, a Retry-After confirms a throttle.
    _patch_proc(
        monkeypatch,
        FakeProcResult(returncode=1, stdout="request failed\nRetry-After: 30\n"),
    )
    res = _run(get_runner("codex"))
    assert res.quota_limited is True


def test_run_timed_out_propagates_and_is_error(monkeypatch):
    _patch_proc(monkeypatch, FakeProcResult(stdout="", timed_out=True))
    res = _run(get_runner("codex"), timeout_sec=900)
    assert res.timed_out is True
    assert res.is_error is True


# --- cost parsing ----------------------------------------------------------


def test_cost_parsed_from_cost_line(monkeypatch):
    _patch_proc(monkeypatch, FakeProcResult(stdout="Cost: $0.0210 for this turn\n"))
    res = _run(get_runner("codex"))
    assert res.cost_usd == pytest.approx(0.0210)


def test_cost_prefers_last_figure(monkeypatch):
    out = "Cost: $0.0100 turn1\nCost: $0.0250 turn2\n"
    _patch_proc(monkeypatch, FakeProcResult(stdout=out))
    res = _run(get_runner("codex"))
    assert res.cost_usd == pytest.approx(0.0250)


def test_cost_zero_when_absent(monkeypatch):
    _patch_proc(monkeypatch, FakeProcResult(stdout="no cost reported"))
    res = _run(get_runner("codex"))
    assert res.cost_usd == 0.0


# --- model map -------------------------------------------------------------


def test_map_model_tiers_and_passthrough():
    r = get_runner("codex")
    assert r.map_model("haiku") == "gpt-4o-mini"
    assert r.map_model("sonnet") == "gpt-4o"
    assert r.map_model("opus") == "gpt-4.1"
    assert r.map_model("o3-mini") == "o3-mini"
