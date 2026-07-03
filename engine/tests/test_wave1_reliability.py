"""Wave-1 reliability hardening — focused tests for the five surgical fixes.

These close hang/burn holes in the overnight `claude -p` outer loop. NEW file (existing test
files are owned by another worker); each fix gets a targeted test:

- FIX 1: decider timeout threading (decider funcs + BmadConfig + CLI + resume round-trip).
- FIX 2: quota probe finite timeout (a hung probe is inconclusive -> "still limited").
- FIX 3: probe-on-any-error (an errored-but-not-text-flagged result probes once, then survives).
- FIX 4: session-resume after quota (retry with --resume; one fresh fallback; never loops).
- FIX 5: dev-story completion check (green gate is necessary, not sufficient — the story FILE's
  status must reach review|done, else HALT).
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from loop.bmad import driver
from loop.bmad import phases as phases_mod
from loop.bmad.decider import retro_decider, review_decider
from loop.bmad.driver import BmadConfig, ResilientRunner
from loop.bmad import pr
from loop.runners.base import AgentResult, AgentRunner, QuotaStatus


# ---------------------------------------------------------------------------
# doubles
# ---------------------------------------------------------------------------


class RecordingRunner(AgentRunner):
    """Records every run() kwargs; returns queued results (or a default success)."""

    name = "recording"
    supports_sessions = True

    def __init__(self, results=None):
        self._results = list(results or [])
        self.calls: list[dict] = []

    def run(self, **kwargs) -> AgentResult:  # type: ignore[override]
        self.calls.append(dict(kwargs))
        if self._results:
            return self._results.pop(0)
        return AgentResult(raw="", text="ok", cost_usd=0.0)


# ---------------------------------------------------------------------------
# FIX 1 — decider timeout
# ---------------------------------------------------------------------------


def test_review_decider_forwards_timeout_sec():
    r = RecordingRunner([AgentResult(raw="", text="PATCH it.")])
    ans = review_decider(r, question="Fix X?", story_scope="2-1", timeout_sec=600)
    assert ans == "PATCH it."
    assert r.calls[0]["timeout_sec"] == 600


def test_retro_decider_forwards_timeout_sec():
    r = RecordingRunner([AgentResult(raw="", text="Carry forward TDD.")])
    ans = retro_decider(r, question="What went well?", epic_scope="2", timeout_sec=300)
    assert ans == "Carry forward TDD."
    assert r.calls[0]["timeout_sec"] == 300


def test_decider_timeout_defaults_to_zero_when_unset():
    # Default is unbounded (0) so non-driver callers / existing tests are unaffected.
    r = RecordingRunner([AgentResult(raw="", text="ok")])
    review_decider(r, question="q", story_scope="s")
    assert r.calls[0]["timeout_sec"] == 0


def test_bmadconfig_decider_timeout_default_is_finite():
    # FINITE by default (hang-protection is the point).
    assert BmadConfig(project_root="/p").decider_timeout_min == 10


def test_decider_timeout_from_loop_json_both_spellings():
    snake = BmadConfig.from_loop_json({"bmad": {"project_root": "/p", "decider_timeout_min": 3}})
    camel = BmadConfig.from_loop_json({"bmad": {"project_root": "/p", "deciderTimeoutMin": 7}})
    assert snake.decider_timeout_min == 3
    assert camel.decider_timeout_min == 7


def test_decider_timeout_round_trips_in_resume_command(tmp_path):
    cfg = BmadConfig(project_root=str(tmp_path / "proj"), decider_timeout_min=5)
    cmd = driver._resume_command(cfg, tmp_path / "state")
    assert "--decider-timeout-min 5" in cmd
    # default value is NOT emitted (keeps the command readable)
    cfg_default = BmadConfig(project_root=str(tmp_path / "proj"))
    assert "--decider-timeout-min" not in driver._resume_command(cfg_default, tmp_path / "state")


def test_driver_threads_finite_decider_timeout_into_retro(monkeypatch):
    # The retro Q&A loop must hand retro_decider a FINITE timeout so a wedged decider can't hang
    # the phase. Drive one QUESTION turn and assert the decider's own run() carried the timeout.
    from loop.bmad import driver as drv

    cfg = BmadConfig(project_root="/p", decider_timeout_min=4, max_retro_turns=3)
    # facilitator emits a QUESTION on turn 1, then RETRO_COMPLETE on turn 2; the decider answers
    # in between (its own run() is the 3rd recorded call, tagged with the haiku decider model).
    runner = RecordingRunner(
        [
            AgentResult(raw="", text="QUESTION: [P2] keep the gate?", session_id="s1"),
            AgentResult(raw="", text="the decider answer"),  # decider's run()
            AgentResult(raw="", text="RETRO_COMPLETE: solid epic."),
        ]
    )
    events: list[dict] = []
    ok, _cost = drv._run_retro(cfg, runner, "2", emit=events.append, cwd="/p")
    assert ok
    # find the decider call (model == the decider tier 'haiku')
    decider_calls = [c for c in runner.calls if c.get("model") == cfg.model_for("decider")]
    assert decider_calls, "decider run() not observed"
    assert decider_calls[0]["timeout_sec"] == 4 * 60


def test_code_review_decider_partial_forwards_timeout():
    # The driver binds the decider timeout via functools.partial before handing it to
    # phases.code_review (which calls decider(...) without a timeout). Prove the bound partial
    # forwards the finite timeout to the decider's run().
    from functools import partial

    runner = RecordingRunner(
        [
            AgentResult(raw="", text="QUESTION: [P1] bug?", session_id="rev1"),
            AgentResult(raw="", text="the decision"),  # decider run()
            AgentResult(raw="", text="REVIEW_COMPLETE: done."),
        ]
    )
    bound = partial(review_decider, timeout_sec=900)
    res = phases_mod.code_review(
        runner,
        bound,
        "2-1",
        emit=lambda e: None,
        cwd="/p",
        gate_fn=lambda: {"green": True, "pass": 5},
        max_turns=5,
        model="sonnet",
    )
    assert res.ok
    decider_calls = [c for c in runner.calls if c.get("model") == "haiku"]
    assert decider_calls and decider_calls[0]["timeout_sec"] == 900


# ---------------------------------------------------------------------------
# FIX 2 — quota probe finite timeout
# ---------------------------------------------------------------------------


def test_probe_quota_uses_finite_timeout(monkeypatch):
    from loop.runners import claude as claude_mod

    seen: dict = {}

    class _R:
        returncode = 0
        stdout = ""
        stderr = ""
        timed_out = False

    def fake_run(argv, *, cwd, timeout_sec):
        seen["timeout_sec"] = timeout_sec
        return _R()

    monkeypatch.setattr(claude_mod.proc, "run_with_timeout", fake_run)
    claude_mod.ClaudeRunner().probe_quota()
    assert seen["timeout_sec"] == 120  # finite, not 0/unbounded


def test_probe_quota_timeout_reports_still_limited(monkeypatch):
    from loop.runners import claude as claude_mod

    class _R:
        returncode = -1
        stdout = ""
        stderr = ""
        timed_out = True

    monkeypatch.setattr(
        claude_mod.proc, "run_with_timeout", lambda argv, *, cwd, timeout_sec: _R()
    )
    status = claude_mod.ClaudeRunner().probe_quota()
    # A hung probe is inconclusive -> treated as STILL limited so survive() sleeps + re-probes.
    assert status.limited is True
    assert status.reset_at is None


def test_probe_quota_clean_response_not_limited(monkeypatch):
    from loop.runners import claude as claude_mod

    class _R:
        returncode = 0
        stdout = '{"ok": true}'
        stderr = ""
        timed_out = False

    monkeypatch.setattr(
        claude_mod.proc, "run_with_timeout", lambda argv, *, cwd, timeout_sec: _R()
    )
    assert claude_mod.ClaudeRunner().probe_quota().limited is False


# ---------------------------------------------------------------------------
# FIX 3 — probe on ANY failed phase (not just text-flagged quota)
# ---------------------------------------------------------------------------


class ProbeDrivenRunner(AgentRunner):
    """Errors (NOT text-flagged quota) until `error_calls` is exhausted; probe limited N times."""

    name = "probe-driven"
    supports_quota_probe = True
    supports_sessions = True

    def __init__(self, *, error_calls: int, probe_limited: int, real: AgentResult):
        self._error_calls = error_calls
        self._probe_limited = probe_limited
        self._real = real
        self.calls = 0
        self.probes = 0

    def run(self, **kwargs) -> AgentResult:  # type: ignore[override]
        self.calls += 1
        if self.calls <= self._error_calls:
            # errored, but NOT quota_limited by TEXT — the FIX 3 probe must catch it.
            return AgentResult(raw="boom", is_error=True, quota_limited=False)
        return self._real

    def probe_quota(self) -> QuotaStatus:
        self.probes += 1
        limited = self.probes <= self._probe_limited
        return QuotaStatus(limited=limited, reset_at=None, reset_type=None)


def test_error_not_text_flagged_triggers_probe_and_survives():
    base = ProbeDrivenRunner(
        error_calls=1, probe_limited=1, real=AgentResult(raw="", text="done", cost_usd=1.0)
    )
    events: list[dict] = []
    r = ResilientRunner(base, emit=events.append, sleep=lambda s: None)
    res = r.run(prompt="x", model="sonnet", allowed_tools=[], permission_mode="acceptEdits",
                max_turns=0, cwd=None)
    assert res.text == "done"
    kinds = [e["event"] for e in events]
    assert "quota-hit" in kinds and "quota-resume" in kinds
    assert base.probes >= 1  # the error triggered an independent probe


def test_success_never_triggers_a_probe():
    base = ProbeDrivenRunner(
        error_calls=0, probe_limited=0, real=AgentResult(raw="", text="ok", cost_usd=0.0)
    )
    r = ResilientRunner(base, emit=lambda e: None, sleep=lambda s: None)
    r.run(prompt="x", model="sonnet", allowed_tools=[], permission_mode="acceptEdits",
          max_turns=0, cwd=None)
    assert base.probes == 0  # no probe on a clean success


def test_error_with_clean_probe_returns_error_unchanged():
    # An errored result whose probe says CLEAN takes the existing error path (returned as-is).
    base = ProbeDrivenRunner(
        error_calls=99, probe_limited=0, real=AgentResult(raw="", text="never")
    )
    r = ResilientRunner(base, emit=lambda e: None, sleep=lambda s: None)
    res = r.run(prompt="x", model="sonnet", allowed_tools=[], permission_mode="acceptEdits",
                max_turns=0, cwd=None)
    assert res.is_error and base.calls == 1 and base.probes == 1


# ---------------------------------------------------------------------------
# FIX 4 — session-resume after quota (kills the double-charge burn)
# ---------------------------------------------------------------------------


class ResumeAwareRunner(AgentRunner):
    """Scriptable per-call results; records resume_session seen on each call."""

    name = "resume-aware"
    supports_quota_probe = True
    supports_sessions = True

    def __init__(self, results):
        self._results = list(results)
        self.calls: list[dict] = []

    def run(self, **kwargs) -> AgentResult:  # type: ignore[override]
        self.calls.append(dict(kwargs))
        return self._results.pop(0)

    def probe_quota(self) -> QuotaStatus:
        return QuotaStatus(limited=False, reset_at=None, reset_type=None)


def test_quota_hit_with_session_resumes_on_retry():
    base = ResumeAwareRunner(
        [
            AgentResult(raw="", is_error=True, quota_limited=True, session_id="sess-1"),
            AgentResult(raw="", text="continued", cost_usd=1.0),
        ]
    )
    r = ResilientRunner(base, emit=lambda e: None, sleep=lambda s: None)
    res = r.run(prompt="p", model="opus", allowed_tools=[], permission_mode="acceptEdits",
                max_turns=0, cwd=None)
    assert res.text == "continued"
    assert len(base.calls) == 2
    # first attempt had no resume; the post-survive retry RESUMES the carried session
    assert base.calls[0].get("resume_session") is None
    assert base.calls[1].get("resume_session") == "sess-1"


def test_resumed_attempt_nonquota_error_falls_back_to_one_fresh_attempt():
    base = ResumeAwareRunner(
        [
            AgentResult(raw="", is_error=True, quota_limited=True, session_id="sess-9"),
            AgentResult(raw="", is_error=True, quota_limited=False),  # resumed attempt errors
            AgentResult(raw="", text="fresh-ok", cost_usd=1.0),  # ONE fresh (non-resume) attempt
        ]
    )
    r = ResilientRunner(base, emit=lambda e: None, sleep=lambda s: None)
    res = r.run(prompt="p", model="opus", allowed_tools=[], permission_mode="acceptEdits",
                max_turns=0, cwd=None)
    assert res.text == "fresh-ok"
    assert len(base.calls) == 3  # never loops beyond one fresh fallback
    assert base.calls[1].get("resume_session") == "sess-9"
    assert base.calls[2].get("resume_session") is None  # the fresh fallback drops --resume


def test_quota_without_session_retries_same_call():
    # No session id -> the retry is a plain re-run (parity with the pre-FIX-4 behavior).
    base = ResumeAwareRunner(
        [
            AgentResult(raw="", is_error=True, quota_limited=True, session_id=None),
            AgentResult(raw="", text="rerun-ok"),
        ]
    )
    r = ResilientRunner(base, emit=lambda e: None, sleep=lambda s: None)
    res = r.run(prompt="p", model="opus", allowed_tools=[], permission_mode="acceptEdits",
                max_turns=0, cwd=None)
    assert res.text == "rerun-ok"
    assert base.calls[1].get("resume_session") is None


# ---------------------------------------------------------------------------
# FIX 5 — dev-story completion check (story FILE status must reach review|done)
# ---------------------------------------------------------------------------


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True)


def _init_project(tmp_path: Path, *, status: str = "ready-for-dev", key: str = "2-1-cap") -> Path:
    root = tmp_path / "project"
    artifacts = root / "_bmad-output" / "implementation-artifacts"
    artifacts.mkdir(parents=True)
    (artifacts / "sprint-status.yaml").write_text(
        f"development_status:\n  epic-2: in-progress\n  {key}: ready-for-dev\n", encoding="utf-8"
    )
    (artifacts / f"{key}.md").write_text(
        f"# Story {key}\n\nStatus: {status}\n\n"
        "## Acceptance Criteria\n1. It works.\n\n## Tasks\n- do it\n",
        encoding="utf-8",
    )
    _git(root, "init")
    _git(root, "config", "user.email", "t@t.t")
    _git(root, "config", "user.name", "t")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "base")
    _git(root, "branch", "develop")
    return root


def _gate_stages():
    return [
        {"name": "codegen", "command": lambda: ("", 0)},
        {"name": "lint", "command": lambda: ("", 0)},
        {
            "name": "test",
            "command": lambda: ("10 pass 0 fail", 0),
            "pass_pattern": r"(\d+)\s+pass",
            "fail_pattern": r"(\d+)\s+fail",
        },
    ]


class _FakeServer:
    def __init__(self, *a, **k):
        pass

    def start(self):
        return "http://localhost:4137"

    def stop(self):
        pass


def _patch_externals(monkeypatch):
    monkeypatch.setattr(phases_mod, "DevServer", _FakeServer)
    monkeypatch.setattr(pr, "create_pr", lambda **k: "https://example.test/pr")
    monkeypatch.setattr(pr, "merge_pr", lambda **k: "merged")
    monkeypatch.setattr(pr, "pr_state", lambda **k: "MERGED")
    from loop.bmad import driver as drv

    real_git = drv.gitutil._git

    def guard_git(args, cwd):
        if args and args[0] in ("push", "pull"):
            class _R:
                returncode = 0
                stdout = ""
                stderr = ""

            return _R()
        return real_git(args, cwd)

    monkeypatch.setattr(drv.gitutil, "_git", guard_git)


def _events(state: Path) -> list[dict]:
    log = state / "log.jsonl"
    if not log.exists():
        return []
    return [json.loads(ln) for ln in log.read_text(encoding="utf-8").splitlines() if ln.strip()]


class _StaticRunner(AgentRunner):
    name = "static"
    supports_sessions = True

    def __init__(self, results):
        self._results = list(results)

    def run(self, **kwargs) -> AgentResult:  # type: ignore[override]
        return self._results.pop(0) if self._results else AgentResult(raw="", text="ok")


def test_dev_story_green_gate_but_status_not_advanced_halts(tmp_path, monkeypatch):
    # Green gate is necessary but NOT sufficient: the story FILE still says ready-for-dev (the
    # agent HALTed mid-story), so the pipeline must HALT rather than slip into code-review.
    root = _init_project(tmp_path, status="ready-for-dev")
    state = tmp_path / "state"
    _patch_externals(monkeypatch)
    runner = _StaticRunner([AgentResult(raw="", text="dev complete", cost_usd=1.0)])
    cfg = driver.BmadConfig(project_root=str(root), gate_stages=_gate_stages(), no_merge=True)
    rc = driver.run(cfg, runner=runner, state_dir=str(state))
    assert rc == 1
    stops = [e for e in _events(state) if e["event"] == "stop"]
    assert stops and stops[-1]["ok"] is False
    assert "did not complete" in stops[-1]["reason"]
    # it must NOT have proceeded to code-review / PR
    kinds = [e["event"] for e in _events(state)]
    assert "review-complete" not in kinds
    assert "pr-created" not in kinds


class _AdvancingRunner(AgentRunner):
    """Simulates the real dev-story agent: on the dev-story call it advances the story FILE's
    Status to 'review' (as the prompt instructs), so the FIX 5 completion check passes."""

    name = "advancing"
    supports_sessions = True

    def __init__(self, story_md: Path, results):
        self._story_md = story_md
        self._results = list(results)
        self._advanced = False

    def run(self, **kwargs) -> AgentResult:  # type: ignore[override]
        if not self._advanced:
            self._advanced = True
            self._story_md.write_text(
                self._story_md.read_text(encoding="utf-8").replace(
                    "Status: ready-for-dev", "Status: review"
                ),
                encoding="utf-8",
            )
        return self._results.pop(0) if self._results else AgentResult(raw="", text="ok")


def test_dev_story_status_advanced_proceeds_through_pipeline(tmp_path, monkeypatch):
    # When the agent advances the story FILE to 'review', the completion check passes and the
    # pipeline proceeds (dev -> review -> smoke -> PR), stopping cleanly under --no-merge.
    root = _init_project(tmp_path, status="ready-for-dev")
    state = tmp_path / "state"
    _patch_externals(monkeypatch)
    story_md = root / "_bmad-output" / "implementation-artifacts" / "2-1-cap.md"
    runner = _AdvancingRunner(
        story_md,
        [
            AgentResult(raw="", text="dev complete; status: review", cost_usd=1.0),
            AgentResult(raw="", text="REVIEW_COMPLETE: clean.", cost_usd=0.2),
            AgentResult(raw="", text="SMOKE_PASS: verified.", cost_usd=0.3),
        ],
    )
    cfg = driver.BmadConfig(project_root=str(root), gate_stages=_gate_stages(), no_merge=True)
    rc = driver.run(cfg, runner=runner, state_dir=str(state))
    assert rc == 0
    kinds = [e["event"] for e in _events(state)]
    assert "review-complete" in kinds
    assert "pr-created" in kinds
    stops = [e for e in _events(state) if e["event"] == "stop"]
    assert stops and stops[-1]["ok"] is True
