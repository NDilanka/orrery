"""AiderRunner tests.

NO real ``aider`` is ever spawned: every test monkeypatches ``orrery_loop.runners.aider.proc`` with a
fake whose ``run_with_timeout`` returns a canned :class:`ProcResult`, then asserts the runner
builds the right argv and normalizes aider's text output correctly.
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from orrery_loop.runners import AiderRunner, ClaudeRunner, get_runner
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
    monkeypatch.setattr("orrery_loop.runners.aider.proc", fake)
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


def test_get_runner_returns_aider_instance():
    r = get_runner("aider")
    assert isinstance(r, AiderRunner)
    assert isinstance(r, AgentRunner)
    assert r.name == "aider"


def test_get_runner_claude_still_works():
    assert isinstance(get_runner("claude"), ClaudeRunner)


def test_aider_capability_flags_all_false():
    r = get_runner("aider")
    assert r.supports_quota_probe is False
    assert r.supports_sessions is False
    assert r.supports_cache_telemetry is False


# --- argv construction -----------------------------------------------------


def test_run_argv_has_expected_flags(monkeypatch):
    fake = _patch_proc(monkeypatch, FakeProcResult(stdout="done"))
    _run(get_runner("aider"), prompt="PROMPT", model="opus", cwd="/work", timeout_sec=600)
    argv = fake.calls[0]["argv"]
    assert argv[0] == "aider"
    assert argv[argv.index("--message") + 1] == "PROMPT"
    # the loop owns git → aider must not auto-commit, and must run non-interactively
    assert "--no-auto-commit" in argv
    assert "--yes" in argv
    assert argv[argv.index("--model") + 1] == "claude-3-opus-latest"
    # timeout + cwd forwarded to proc
    assert fake.calls[0]["timeout_sec"] == 600
    assert fake.calls[0]["cwd"] == "/work"


def test_run_passes_through_unknown_model_string(monkeypatch):
    fake = _patch_proc(monkeypatch, FakeProcResult(stdout="ok"))
    _run(get_runner("aider"), model="openai/gpt-4o")
    argv = fake.calls[0]["argv"]
    assert argv[argv.index("--model") + 1] == "openai/gpt-4o"


# --- output normalization --------------------------------------------------


def test_run_success_text_and_not_error(monkeypatch):
    _patch_proc(monkeypatch, FakeProcResult(returncode=0, stdout="all changes applied"))
    res = _run(get_runner("aider"))
    assert res.is_error is False
    assert res.text == "all changes applied"
    assert res.raw == "all changes applied"
    assert res.parse_failed is False
    assert res.timed_out is False
    assert res.cost_usd == 0.0


def test_run_nonzero_exit_is_error(monkeypatch):
    _patch_proc(monkeypatch, FakeProcResult(returncode=1, stdout="partial"))
    res = _run(get_runner("aider"))
    assert res.is_error is True


def test_run_error_marker_in_output_is_error(monkeypatch):
    _patch_proc(
        monkeypatch,
        FakeProcResult(returncode=0, stdout="Traceback (most recent call last):\n ..."),
    )
    res = _run(get_runner("aider"))
    assert res.is_error is True


def test_run_quota_limited_from_429_text(monkeypatch):
    _patch_proc(
        monkeypatch,
        FakeProcResult(returncode=1, stdout="litellm.RateLimitError: 429 too many requests"),
    )
    res = _run(get_runner("aider"))
    assert res.quota_limited is True
    assert res.is_error is True


def test_run_timed_out_propagates_and_is_error(monkeypatch):
    _patch_proc(monkeypatch, FakeProcResult(stdout="", timed_out=True))
    res = _run(get_runner("aider"), timeout_sec=900)
    assert res.timed_out is True
    assert res.is_error is True


# --- cost parsing ----------------------------------------------------------


def test_cost_parsed_from_session_line(monkeypatch):
    out = (
        "Applied edit.\n"
        "Tokens: 1.2k sent, 350 received. "
        "Cost: $0.0123 message, $0.0456 session.\n"
    )
    _patch_proc(monkeypatch, FakeProcResult(stdout=out))
    res = _run(get_runner("aider"))
    # rule: prefer the cumulative "$X session" figure
    assert res.cost_usd == pytest.approx(0.0456)


def test_cost_prefers_last_session_figure(monkeypatch):
    out = (
        "Cost: $0.0100 message, $0.0100 session.\n"
        "Cost: $0.0200 message, $0.0300 session.\n"
    )
    _patch_proc(monkeypatch, FakeProcResult(stdout=out))
    res = _run(get_runner("aider"))
    assert res.cost_usd == pytest.approx(0.0300)


def test_cost_falls_back_to_message_cost_when_no_session(monkeypatch):
    _patch_proc(monkeypatch, FakeProcResult(stdout="Cost: $0.0077 for this run\n"))
    res = _run(get_runner("aider"))
    assert res.cost_usd == pytest.approx(0.0077)


def test_cost_zero_when_absent(monkeypatch):
    _patch_proc(monkeypatch, FakeProcResult(stdout="no cost reported here"))
    res = _run(get_runner("aider"))
    assert res.cost_usd == 0.0


# --- model map -------------------------------------------------------------


def test_map_model_tiers_and_passthrough():
    r = get_runner("aider")
    assert r.map_model("haiku") == "claude-3-5-haiku-latest"
    assert r.map_model("sonnet") == "claude-3-5-sonnet-latest"
    assert r.map_model("opus") == "claude-3-opus-latest"
    assert r.map_model("some/custom-id") == "some/custom-id"
