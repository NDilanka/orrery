"""ClaudeRunner + registry tests.

NO real ``claude`` is ever spawned: every test monkeypatches ``loop.runners.claude.proc``
with a fake whose ``run_with_timeout`` returns a canned :class:`ProcResult`, then asserts the
runner builds the right argv and parses the result correctly.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import pytest

from loop import quota
from loop.runners import ClaudeRunner, get_runner
from loop.runners.base import AgentRunner


@dataclass
class FakeProcResult:
    """Mirrors loop.proc.ProcResult for the monkeypatched proc layer."""

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
    monkeypatch.setattr("loop.runners.claude.proc", fake)
    return fake


# --- registry --------------------------------------------------------------


def test_get_runner_returns_claude_instance():
    r = get_runner("claude")
    assert isinstance(r, ClaudeRunner)
    assert isinstance(r, AgentRunner)
    assert r.name == "claude"


def test_get_runner_fresh_instance_each_call():
    assert get_runner("claude") is not get_runner("claude")


def test_get_runner_unknown_raises():
    with pytest.raises(ValueError):
        get_runner("nope")


def test_claude_capability_flags_all_true():
    r = get_runner("claude")
    assert r.supports_quota_probe is True
    assert r.supports_sessions is True
    assert r.supports_cache_telemetry is True


# --- run(): JSON parsing ---------------------------------------------------


def test_run_parses_cost_text_is_error_session_usage(monkeypatch):
    canned = {
        "type": "result",
        "is_error": False,
        "result": "all green",
        "total_cost_usd": 0.1234,
        "session_id": "sess-abc",
        "usage": {"input_tokens": 100, "cache_read_input_tokens": 300},
    }
    _patch_proc(monkeypatch, FakeProcResult(stdout=json.dumps(canned)))

    r = get_runner("claude")
    res = r.run(
        prompt="do it",
        model="claude-sonnet-4",
        allowed_tools=["Read", "Edit"],
        permission_mode="acceptEdits",
        max_turns=12,
        cwd="/repo",
    )
    assert res.is_error is False
    assert res.text == "all green"
    assert res.cost_usd == pytest.approx(0.1234)
    assert res.session_id == "sess-abc"
    assert res.usage == {"input_tokens": 100, "cache_read_input_tokens": 300}
    assert res.timed_out is False
    assert res.raw == json.dumps(canned)


def test_run_is_error_true_from_result(monkeypatch):
    canned = {"is_error": True, "result": "boom", "total_cost_usd": 0.0}
    _patch_proc(monkeypatch, FakeProcResult(stdout=json.dumps(canned)))
    res = get_runner("claude").run(
        prompt="x", model="m", allowed_tools=[], permission_mode="plan",
        max_turns=1, cwd=".",
    )
    assert res.is_error is True
    assert res.text == "boom"


def test_run_non_json_stdout_is_error(monkeypatch):
    _patch_proc(monkeypatch, FakeProcResult(stdout="not json at all"))
    res = get_runner("claude").run(
        prompt="x", model="m", allowed_tools=[], permission_mode="plan",
        max_turns=1, cwd=".",
    )
    assert res.is_error is True
    assert res.timed_out is False
    assert res.raw == "not json at all"
    assert res.cost_usd == 0.0


def test_run_timed_out_propagates_and_is_error(monkeypatch):
    _patch_proc(monkeypatch, FakeProcResult(stdout="", timed_out=True))
    res = get_runner("claude").run(
        prompt="x", model="m", allowed_tools=["Read"], permission_mode="plan",
        max_turns=1, cwd=".", timeout_sec=900,
    )
    assert res.timed_out is True
    assert res.is_error is True


def test_run_quota_limited_flag_set_on_error_with_limit_text(monkeypatch):
    canned = {"is_error": True, "result": "API error: 429 too many requests"}
    _patch_proc(monkeypatch, FakeProcResult(stdout=json.dumps(canned)))
    res = get_runner("claude").run(
        prompt="x", model="m", allowed_tools=[], permission_mode="plan",
        max_turns=1, cwd=".",
    )
    assert res.is_error is True
    assert res.quota_limited is True


# --- run(): argv construction ----------------------------------------------


def test_run_argv_has_expected_flags(monkeypatch):
    fake = _patch_proc(monkeypatch, FakeProcResult(stdout=json.dumps({"is_error": False})))
    get_runner("claude").run(
        prompt="PROMPT",
        model="claude-opus-4",
        allowed_tools=["Read", "Edit", "Bash"],
        permission_mode="acceptEdits",
        max_turns=20,
        cwd="/work",
        timeout_sec=600,
    )
    argv = fake.calls[0]["argv"]
    assert argv[0] == "claude"
    assert argv[1] == "-p"
    assert argv[2] == "PROMPT"
    # exact flag/value pairs
    assert argv[argv.index("--output-format") + 1] == "json"
    assert argv[argv.index("--max-turns") + 1] == "20"
    assert argv[argv.index("--model") + 1] == "claude-opus-4"
    assert argv[argv.index("--permission-mode") + 1] == "acceptEdits"
    # --allowedTools is a single flag followed by the tool list spread as positional args
    at = argv.index("--allowedTools")
    assert argv[at + 1 : at + 4] == ["Read", "Edit", "Bash"]
    assert "--resume" not in argv
    # the timeout is forwarded to proc
    assert fake.calls[0]["timeout_sec"] == 600
    assert fake.calls[0]["cwd"] == "/work"


def test_run_argv_includes_resume_when_session_given(monkeypatch):
    fake = _patch_proc(monkeypatch, FakeProcResult(stdout=json.dumps({"is_error": False})))
    get_runner("claude").run(
        prompt="P",
        model="m",
        allowed_tools=["Read"],
        permission_mode="plan",
        max_turns=1,
        cwd=".",
        resume_session="sess-123",
    )
    argv = fake.calls[0]["argv"]
    assert "--resume" in argv
    assert argv[argv.index("--resume") + 1] == "sess-123"


def test_run_output_format_override_flows_to_argv(monkeypatch):
    fake = _patch_proc(monkeypatch, FakeProcResult(stdout=json.dumps({"is_error": False})))
    get_runner("claude").run(
        prompt="P", model="m", allowed_tools=[], permission_mode="plan",
        max_turns=1, cwd=".", output_format="text",
    )
    argv = fake.calls[0]["argv"]
    assert argv[argv.index("--output-format") + 1] == "text"


# --- probe_quota() ---------------------------------------------------------


def _epoch(dt: datetime) -> int:
    return int(dt.replace(tzinfo=timezone.utc).timestamp())


def _rli(status: str, resets_at: int | None, rli_type: str | None) -> str:
    parts = [f'"status": "{status}"']
    if resets_at is not None:
        parts.append(f'"resetsAt": {resets_at}')
    if rli_type:
        parts.append(f'"rateLimitType": "{rli_type}"')
    return '{ "rate_limit_info": { ' + ", ".join(parts) + " } }"


def test_probe_quota_rejected_fragment_is_limited_with_reset_type(monkeypatch):
    reset_at = datetime(2026, 6, 20, 12, 0, 0) + timedelta(minutes=45)
    e = _epoch(reset_at)
    frag = _rli("rejected", e, "five_hour")
    _patch_proc(monkeypatch, FakeProcResult(stdout=frag))

    status = get_runner("claude").probe_quota()
    assert status.limited is True
    assert status.reset_type == "five_hour"
    # reset_at matches the pure resolver's round-trip
    assert status.reset_at == quota.resolve_quota_status(frag).reset_at


def test_probe_quota_allowed_fragment_not_limited(monkeypatch):
    frag = _rli("allowed", None, "five_hour")
    _patch_proc(monkeypatch, FakeProcResult(stdout=frag))
    status = get_runner("claude").probe_quota()
    assert status.limited is False
    assert status.reset_at is None
    assert status.reset_type is None


def test_probe_quota_reads_combined_stdout_and_stderr(monkeypatch):
    # The limit signal arrives on STDERR — probe must combine both streams.
    reset_at = datetime(2026, 6, 20, 12, 0, 0) + timedelta(minutes=30)
    frag = _rli("rejected", _epoch(reset_at), "weekly")
    _patch_proc(monkeypatch, FakeProcResult(stdout="", stderr=frag))
    status = get_runner("claude").probe_quota()
    assert status.limited is True
    assert status.reset_type == "weekly"


def test_probe_quota_argv_is_cheap_stream_json(monkeypatch):
    fake = _patch_proc(monkeypatch, FakeProcResult(stdout=_rli("allowed", None, None)))
    get_runner("claude").probe_quota()
    argv = fake.calls[0]["argv"]
    assert argv[:3] == ["claude", "-p", "ok"]
    assert argv[argv.index("--output-format") + 1] == "stream-json"
    assert "--verbose" in argv
    assert argv[argv.index("--max-turns") + 1] == "1"
