"""Hermetic tests for the BMAD multi-story DRIVER (``loop.bmad.driver``).

Everything external is injected or stubbed so the orchestration runs with NO network and NO
real ``claude``:

- a temp git repo is the "project" (with ``_bmad-output/implementation-artifacts/
  sprint-status.yaml`` + a story ``.md``);
- a ``MockRunner`` returns canned ``AgentResult``s and records every ``run()`` call;
- ``loop.bmad.pr.create_pr`` / ``merge_pr`` are monkeypatched (asserting NO real ``gh``);
- the dev server is a ``FakeServer`` injected by monkeypatching ``phases.DevServer``;
- the gate stages are CALLABLE hooks (no ``bun``);
- the recovery git predicate is stubbed where a ``done``-but-unmerged story is exercised.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from loop.bmad import driver, pr, recovery
from loop.bmad import phases as phases_mod
from loop.runners.base import AgentResult, AgentRunner


# ---------------------------------------------------------------------------
# fixtures / doubles
# ---------------------------------------------------------------------------


class MockRunner(AgentRunner):
    """Returns queued AgentResults in order; records each run()'s kwargs. Supports sessions."""

    name = "mock"
    supports_sessions = True

    def __init__(self, results=None):
        self._results = list(results or [])
        self.calls: list[dict] = []

    def run(self, **kwargs) -> AgentResult:  # type: ignore[override]
        self.calls.append(dict(kwargs))
        if self._results:
            return self._results.pop(0)
        return AgentResult(raw="", text="ok", cost_usd=0.0)


class FakeServer:
    """Fake server_ctl injected in place of phases.DevServer."""

    instances: list["FakeServer"] = []

    def __init__(self, *args, **kwargs):
        self.started = 0
        self.stopped = 0
        FakeServer.instances.append(self)

    def start(self):
        self.started += 1
        return "http://localhost:4137"

    def stop(self):
        self.stopped += 1


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True)


def _init_project(tmp_path: Path, sprint_yaml: str, *, story_key: str = "2-1-capture") -> Path:
    """A temp git repo with the BMAD artifacts dir + a sprint-status + one story file."""
    root = tmp_path / "project"
    artifacts = root / "_bmad-output" / "implementation-artifacts"
    artifacts.mkdir(parents=True)
    (artifacts / "sprint-status.yaml").write_text(sprint_yaml, encoding="utf-8")
    (artifacts / f"{story_key}.md").write_text(
        f"# Story {story_key}\n\nStatus: ready-for-dev\n\n"
        "## Acceptance Criteria\n1. A capture box appears.\n2. It saves a note.\n\n"
        "## Tasks\n- wire it\n",
        encoding="utf-8",
    )
    _git(root, "init")
    _git(root, "config", "user.email", "t@t.t")
    _git(root, "config", "user.name", "t")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "base")
    _git(root, "branch", "develop")
    return root


def _green_gate():
    """A callable gate hook -> green, used as every config gate stage's command."""
    return ("10 pass 0 fail", 0)


def _gate_stages():
    return [
        {"name": "codegen", "command": lambda: ("", 0)},
        {"name": "lint", "command": lambda: ("", 0)},
        {
            "name": "test",
            "command": _green_gate,
            "pass_pattern": r"(\d+)\s+pass",
            "fail_pattern": r"(\d+)\s+fail",
        },
    ]


def _patch_externals(monkeypatch, *, pr_calls: dict):
    """Monkeypatch the dev server + the gh PR/merge wrappers; record gh usage in pr_calls."""
    FakeServer.instances.clear()
    monkeypatch.setattr(phases_mod, "DevServer", FakeServer)

    def fake_create(*, branch, base, title, body, cwd):
        pr_calls.setdefault("create", []).append({"branch": branch, "base": base})
        return f"https://example.test/pr/{branch}"

    def fake_merge(*, branch, base, cwd):
        pr_calls.setdefault("merge", []).append({"branch": branch, "base": base})
        return "merged"

    def fake_state(*, branch, cwd):
        pr_calls.setdefault("state", []).append({"branch": branch})
        return "MERGED"

    monkeypatch.setattr(pr, "create_pr", fake_create)
    monkeypatch.setattr(pr, "merge_pr", fake_merge)
    monkeypatch.setattr(pr, "pr_state", fake_state)
    # Neutralize the real `git push` so the temp repo (no remote) doesn't error/stall.
    from loop.bmad import driver as drv

    real_git = drv.gitutil._git

    def guard_git(args, cwd):
        # Neutralize network git (push/pull) so the temp repo (no remote) doesn't error/stall.
        if args and args[0] in ("push", "pull"):
            class _R:
                returncode = 0
                stdout = ""
                stderr = ""

            return _R()
        return real_git(args, cwd)

    monkeypatch.setattr(drv.gitutil, "_git", guard_git)


def _config(root: Path, **overrides) -> driver.BmadConfig:
    cfg = driver.BmadConfig(project_root=str(root), gate_stages=_gate_stages())
    for k, v in overrides.items():
        setattr(cfg, k, v)
    return cfg


def _events(state: Path) -> list[dict]:
    log = state / "log.jsonl"
    if not log.exists():
        return []
    return [json.loads(ln) for ln in log.read_text(encoding="utf-8").splitlines() if ln.strip()]


# ---------------------------------------------------------------------------
# 1. one ready story end-to-end with --no-merge
# ---------------------------------------------------------------------------

ONE_READY = (
    "development_status:\n"
    "  epic-2: in-progress\n"
    "  2-1-capture: ready-for-dev\n"
)


def test_one_ready_story_end_to_end_no_merge(tmp_path, monkeypatch):
    root = _init_project(tmp_path, ONE_READY)
    state = tmp_path / "state"
    pr_calls: dict = {}
    _patch_externals(monkeypatch, pr_calls=pr_calls)

    runner = MockRunner(
        [
            AgentResult(raw="", text="dev complete; status: review", cost_usd=1.0),  # dev-story
            AgentResult(raw="", text="REVIEW_COMPLETE: clean.", cost_usd=0.2),  # code-review
            AgentResult(raw="", text="SMOKE_PASS: verified AC1, AC2.", cost_usd=0.5),  # smoke
        ]
    )
    cfg = _config(root, no_merge=True)
    rc = driver.run(cfg, runner=runner, state_dir=str(state))
    assert rc == 0

    evts = _events(state)
    kinds = [e["event"] for e in evts]
    # the required integration sequence
    for needed in ("start", "story-start", "dev-gate", "review-complete", "smoke-server", "smoke-iter", "pr-created", "stop"):
        assert needed in kinds, f"missing {needed} in {kinds}"
    # terminal bmad stop carries ok + the --no-merge reason
    stop = [e for e in evts if e["event"] == "stop"][-1]
    assert stop["ok"] is True
    assert "not merged" in stop["reason"]
    # PR was created via the (monkeypatched) wrapper; merge was NOT called
    assert pr_calls.get("create") and pr_calls["create"][0]["branch"] == "feat/story-2-1-capture"
    assert "merge" not in pr_calls
    # a between-stories checkpoint.json was written
    assert (state / "checkpoint.json").exists()
    cp = json.loads((state / "checkpoint.json").read_text(encoding="utf-8"))
    assert cp["mergeBase"] == "develop"
    # dev server started + stopped (no real process)
    assert FakeServer.instances and FakeServer.instances[0].stopped == 1


# ---------------------------------------------------------------------------
# 2. selection / recovery
# ---------------------------------------------------------------------------

ONE_DONE = (
    "development_status:\n"
    "  epic-2: in-progress\n"
    "  2-1-capture: done\n"
)


def test_done_but_unmerged_resumes_at_smoke_and_merge(tmp_path, monkeypatch):
    root = _init_project(tmp_path, ONE_DONE)
    state = tmp_path / "state"
    pr_calls: dict = {}
    _patch_externals(monkeypatch, pr_calls=pr_calls)
    # Force the recovery predicate True ONCE (then False) — modelling the branch being
    # deleted on merge so the resumed story is no longer "done-but-unmerged" on re-scan.
    seen = {"n": 0}

    def predicate_factory(**kw):
        def predicate(story):
            seen["n"] += 1
            return seen["n"] == 1

        return predicate

    monkeypatch.setattr(recovery, "unmerged_done_predicate", predicate_factory)

    # Only smoke runs on the resume tail (no create/dev/review runner calls).
    runner = MockRunner(
        [AgentResult(raw="", text="SMOKE_PASS: verified.", cost_usd=0.5)]
    )
    cfg = _config(root)  # merge ON
    rc = driver.run(cfg, runner=runner, state_dir=str(state))
    assert rc == 0

    evts = _events(state)
    kinds = [e["event"] for e in evts]
    # resume tail: smoke + PR + merge, but NOT dev-gate / review-complete
    assert "smoke-iter" in kinds
    assert "pr-created" in kinds
    assert "pr-merged" in kinds
    assert "dev-gate" not in kinds
    assert "review-complete" not in kinds
    # exactly one runner call (smoke) — create/dev/review were skipped
    assert len(runner.calls) == 1
    assert pr_calls.get("merge")


ALL_DONE = (
    "development_status:\n"
    "  epic-2: done\n"
    "  2-1-capture: done\n"
    "  epic-2-retrospective: done\n"
)


def test_all_merged_terminal_bmad_stop_ok(tmp_path, monkeypatch):
    root = _init_project(tmp_path, ALL_DONE)
    state = tmp_path / "state"
    pr_calls: dict = {}
    _patch_externals(monkeypatch, pr_calls=pr_calls)
    # predicate False -> a 'done' story is NOT actionable; retro already done -> nothing to do.
    monkeypatch.setattr(
        recovery, "unmerged_done_predicate", lambda **kw: (lambda story: False)
    )
    runner = MockRunner([])
    rc = driver.run(_config(root), runner=runner, state_dir=str(state))
    assert rc == 0
    stop = [e for e in _events(state) if e["event"] == "stop"][-1]
    assert stop["ok"] is True
    assert "backlog complete" in stop["reason"]
    # nothing ran
    assert len(runner.calls) == 0
    assert pr_calls == {}


# ---------------------------------------------------------------------------
# 3. STOP flag honored at a between-stories boundary
# ---------------------------------------------------------------------------


def test_stop_flag_honored_at_between_stories_boundary(tmp_path, monkeypatch):
    root = _init_project(tmp_path, ONE_READY)
    state = tmp_path / "state"
    state.mkdir(parents=True, exist_ok=True)
    pr_calls: dict = {}
    _patch_externals(monkeypatch, pr_calls=pr_calls)
    # pre-write a STOP flag -> honored at the FIRST between-stories boundary (before any phase).
    (state / "STOP").write_text("phase", encoding="utf-8")

    runner = MockRunner([])
    rc = driver.run(_config(root), runner=runner, state_dir=str(state))
    assert rc == 0

    evts = _events(state)
    kinds = [e["event"] for e in evts]
    assert "cooperative-stop" in kinds
    coop = [e for e in evts if e["event"] == "cooperative-stop"][0]
    assert coop["scope"] == "story"
    # checkpoint written, STOP flag consumed, no runner work, no PR
    assert (state / "checkpoint.json").exists()
    assert not (state / "STOP").exists()
    assert len(runner.calls) == 0
    assert pr_calls == {}


# ---------------------------------------------------------------------------
# 4. concurrency guard
# ---------------------------------------------------------------------------


def test_concurrency_guard_refuses_when_lock_live(tmp_path, monkeypatch):
    root = _init_project(tmp_path, ONE_READY)
    state = tmp_path / "state"
    state.mkdir(parents=True, exist_ok=True)
    # a live lock owned by a DIFFERENT (live) process -> refuse with 2.
    other_pid = __import__("os").getpid() + 1
    (state / "bmad-lock").write_text(str(other_pid), encoding="utf-8")
    monkeypatch.setattr(driver, "_pid_alive", lambda pid: True)
    _patch_externals(monkeypatch, pr_calls={})
    rc = driver.run(_config(root), runner=MockRunner([]), state_dir=str(state))
    assert rc == 2


# ---------------------------------------------------------------------------
# 5. quota: ResilientRunner survives a one-shot quota_limited result
# ---------------------------------------------------------------------------


class QuotaOnceRunner(AgentRunner):
    """Returns quota_limited once, then the real result. Probes 'available' (survive clears)."""

    name = "quota-once"
    supports_quota_probe = True
    supports_sessions = True

    def __init__(self, real: AgentResult):
        self._real = real
        self._limited_emitted = False
        self.calls = 0
        self.probes = 0

    def run(self, **kwargs) -> AgentResult:  # type: ignore[override]
        self.calls += 1
        if not self._limited_emitted:
            self._limited_emitted = True
            return AgentResult(raw="", text="", is_error=True, quota_limited=True)
        return self._real

    def probe_quota(self):
        from loop.runners.base import QuotaStatus

        self.probes += 1
        return QuotaStatus(limited=False)


def test_resilient_runner_survives_quota_then_completes(tmp_path):
    sleeps: list[float] = []
    events: list[dict] = []
    base = QuotaOnceRunner(AgentResult(raw="", text="done; status: review", cost_usd=1.0))
    resilient = driver.ResilientRunner(
        base, emit=events.append, sleep=sleeps.append
    )
    res = resilient.run(
        prompt="x", model="sonnet", allowed_tools=[], permission_mode="acceptEdits",
        max_turns=0, cwd=None,
    )
    # the quota-limited call was retried and the real result came through
    assert res.text == "done; status: review"
    assert base.calls == 2
    # survive emitted the quota-hit (probe cleared on first probe -> no sleep)
    kinds = [e["event"] for e in events]
    assert "quota-hit" in kinds
    assert "quota-resume" in kinds


def test_quota_limited_phase_completes_in_driver(tmp_path, monkeypatch):
    """End-to-end: a dev-story run that is quota_limited once still completes the phase."""
    root = _init_project(tmp_path, ONE_READY)
    state = tmp_path / "state"
    pr_calls: dict = {}
    _patch_externals(monkeypatch, pr_calls=pr_calls)

    # dev-story: quota_limited first, then green; then review + smoke succeed.
    class DriverQuotaRunner(AgentRunner):
        name = "dq"
        supports_quota_probe = True
        supports_sessions = True

        def __init__(self):
            self.calls = 0
            self._queue = [
                AgentResult(raw="", text="dev complete; status: review", cost_usd=1.0),
                AgentResult(raw="", text="REVIEW_COMPLETE: ok.", cost_usd=0.2),
                AgentResult(raw="", text="SMOKE_PASS: ok.", cost_usd=0.3),
            ]
            self._did_limit = False

        def run(self, **kwargs):
            self.calls += 1
            if not self._did_limit:
                self._did_limit = True
                return AgentResult(raw="", text="", is_error=True, quota_limited=True)
            return self._queue.pop(0)

        def probe_quota(self):
            from loop.runners.base import QuotaStatus

            return QuotaStatus(limited=False)

    runner = DriverQuotaRunner()
    cfg = _config(root, no_merge=True)
    rc = driver.run(cfg, runner=runner, state_dir=str(state))
    assert rc == 0
    kinds = [e["event"] for e in _events(state)]
    # the quota was survived (events present) and the pipeline still reached PR + stop
    assert "quota-hit" in kinds
    assert "pr-created" in kinds
    assert "stop" in kinds


# ---------------------------------------------------------------------------
# 6. dry-run (driver-level) — sprint scan + gate, returns 0, no runner
# ---------------------------------------------------------------------------


class ExplodingRunner(AgentRunner):
    name = "boom"

    def run(self, **kwargs):  # pragma: no cover
        raise AssertionError("dry-run must not call the runner")


def test_dry_run_returns_0_and_calls_no_runner(tmp_path):
    root = _init_project(tmp_path, ONE_READY)
    state = tmp_path / "state"
    cfg = _config(root, dry_run=True)
    rc = driver.run(cfg, runner=ExplodingRunner(), state_dir=str(state))
    assert rc == 0
    # dry-run emits nothing to the log
    assert not (state / "log.jsonl").exists()


# ---------------------------------------------------------------------------
# 7. recovery.is_unmerged_done — real git semantics
# ---------------------------------------------------------------------------


def test_is_unmerged_done_true_for_clean_descendant(tmp_path):
    root = _init_project(tmp_path, ONE_DONE)
    # create feat/story-2-1-capture off develop with an extra commit (clean descendant).
    _git(root, "checkout", "develop")
    _git(root, "checkout", "-b", "feat/story-2-1-capture")
    (root / "extra.txt").write_text("x\n", encoding="utf-8")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "wip")
    _git(root, "checkout", "develop")

    from loop.bmad.sprint import Story

    story = Story(key="2-1-capture", status="done", raw_status="done", epic="2", index=0)
    assert recovery.is_unmerged_done(story, repo=root, merge_base="develop") is True


def test_is_unmerged_done_false_when_no_branch(tmp_path):
    root = _init_project(tmp_path, ONE_DONE)
    from loop.bmad.sprint import Story

    story = Story(key="2-1-capture", status="done", raw_status="done", epic="2", index=0)
    assert recovery.is_unmerged_done(story, repo=root, merge_base="develop") is False


def test_is_unmerged_done_false_for_non_done(tmp_path):
    root = _init_project(tmp_path, ONE_READY)
    from loop.bmad.sprint import Story

    story = Story(key="2-1-capture", status="ready", raw_status="ready-for-dev", epic="2", index=0)
    assert recovery.is_unmerged_done(story, repo=root, merge_base="develop") is False


# ---------------------------------------------------------------------------
# 8. epic retrospective is run when an epic is fully done + retro optional
# ---------------------------------------------------------------------------

EPIC_DONE_RETRO_PENDING = (
    "development_status:\n"
    "  epic-2: done\n"
    "  2-1-capture: done\n"
    "  epic-2-retrospective: optional\n"
)


def test_pending_epic_retro_runs_then_backlog_complete(tmp_path, monkeypatch):
    root = _init_project(tmp_path, EPIC_DONE_RETRO_PENDING)
    state = tmp_path / "state"
    pr_calls: dict = {}
    _patch_externals(monkeypatch, pr_calls=pr_calls)
    # done story is not unmerged -> not actionable; the pending retro fires instead.
    monkeypatch.setattr(
        recovery, "unmerged_done_predicate", lambda **kw: (lambda story: False)
    )
    runner = MockRunner(
        [AgentResult(raw="", text="RETRO_COMPLETE: good epic.", cost_usd=0.4)]
    )
    rc = driver.run(_config(root), runner=runner, state_dir=str(state))
    assert rc == 0
    kinds = [e["event"] for e in _events(state)]
    assert "retro-start" in kinds
    assert "retro-complete" in kinds
    # after the retro the loop re-scans; nothing else actionable -> backlog complete (ok)
    stop = [e for e in _events(state) if e["event"] == "stop"][-1]
    assert stop["ok"] is True


# ---------------------------------------------------------------------------
# 9. config from_loop_json
# ---------------------------------------------------------------------------


def test_bmadconfig_from_loop_json_camel_and_snake(tmp_path):
    cfg = driver.BmadConfig.from_loop_json(
        {
            "bmad": {
                "projectRoot": "/p",
                "mergeBase": "main",
                "maxStories": 7,
                "noMerge": True,
                "models": {"dev": "opus"},
            }
        }
    )
    assert cfg.project_root == "/p"
    assert cfg.merge_base == "main"
    assert cfg.max_stories == 7
    assert cfg.no_merge is True
    assert cfg.model_for("dev") == "opus"
    assert cfg.model_for("decider") == ""  # default = inherit the CC default model


def test_bmadconfig_requires_project_root():
    with pytest.raises(TypeError):
        driver.BmadConfig()  # type: ignore[call-arg]


# ---------------------------------------------------------------------------
# fidelity restorations (PS parity): merge-verify, dev regression, resume flags
# ---------------------------------------------------------------------------


def test_auto_merge_not_completed_halts(tmp_path, monkeypatch):
    """gh reports the PR still OPEN after merge (queued behind branch protection) -> halt."""
    root = _init_project(tmp_path, ONE_READY)
    state = tmp_path / "state"
    pr_calls: dict = {}
    _patch_externals(monkeypatch, pr_calls=pr_calls)
    monkeypatch.setattr(pr, "pr_state", lambda *, branch, cwd: "OPEN")

    runner = MockRunner(
        [
            AgentResult(raw="", text="dev complete; status: review", cost_usd=1.0),
            AgentResult(raw="", text="REVIEW_COMPLETE: clean.", cost_usd=0.2),
            AgentResult(raw="", text="SMOKE_PASS: ok.", cost_usd=0.5),
        ]
    )
    rc = driver.run(_config(root, no_merge=False), runner=runner, state_dir=str(state))
    assert rc == 1
    stop = [e for e in _events(state) if e["event"] == "stop"][-1]
    assert stop["ok"] is False
    assert "did not complete" in stop["reason"]


def test_dev_story_regression_halts(tmp_path, monkeypatch):
    """A drop in passing tests vs the branch baseline halts (report-only by default)."""
    root = _init_project(tmp_path, ONE_READY)
    state = tmp_path / "state"
    pr_calls: dict = {}
    _patch_externals(monkeypatch, pr_calls=pr_calls)

    counter = {"n": 0}

    def shrinking_test():
        counter["n"] += 1
        return ("10 pass 0 fail", 0) if counter["n"] == 1 else ("8 pass 0 fail", 0)

    stages = [
        {"name": "codegen", "command": lambda: ("", 0)},
        {"name": "lint", "command": lambda: ("", 0)},
        {"name": "test", "command": shrinking_test, "pass_pattern": r"(\d+)\s+pass", "fail_pattern": r"(\d+)\s+fail"},
    ]
    runner = MockRunner([AgentResult(raw="", text="dev complete; status: review", cost_usd=1.0)])
    rc = driver.run(_config(root, no_merge=False, gate_stages=stages), runner=runner, state_dir=str(state))
    assert rc == 1
    stop = [e for e in _events(state) if e["event"] == "stop"][-1]
    assert stop["ok"] is False
    assert "regression" in stop["reason"].lower()
    assert "10->8" in stop["reason"]


def test_resume_command_roundtrips_tuning_flags():
    """The checkpoint resume string carries the real paths + the knobs the user changed (#5)."""
    cfg = driver.BmadConfig(
        project_root="D:/p",
        merge_base="develop",
        max_stories=5,
        max_review_turns=3,
        auto_rollback=True,
        no_merge=True,
    )
    cmd = driver._resume_command(cfg, "D:/state")
    assert cmd.startswith("loop-bmad --project-root D:/p --state-dir D:/state --merge-base develop")
    assert "--no-merge" in cmd
    assert "--auto-rollback" in cmd
    assert "--max-stories 5" in cmd
    assert "--max-review-turns 3" in cmd
    # unchanged knobs stay out of the command
    assert "--max-smoke-iters" not in cmd


# ---------------------------------------------------------------------------
# mid-pipeline resume: a paused story re-enters at the PHASE its Status implies
# (in-progress -> dev, review -> review, done-but-unmerged -> smoke+merge). The
# last is already pinned by test_done_but_unmerged_resumes_at_smoke_and_merge.
# ---------------------------------------------------------------------------


def _set_story_status(root: Path, story_key: str, status: str) -> None:
    """Rewrite a story file's `Status:` line on `develop` (the durable resume signal the
    driver reads after checking out the merge base)."""
    _git(root, "checkout", "develop")
    f = root / "_bmad-output" / "implementation-artifacts" / f"{story_key}.md"
    f.write_text(f.read_text(encoding="utf-8").replace("Status: ready-for-dev", f"Status: {status}"), encoding="utf-8")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", f"set {story_key} -> {status}")


def test_in_progress_story_reenters_dev_not_create(tmp_path, monkeypatch):
    """A paused 'in-progress' story resumes at dev-story — create-story is NOT re-run."""
    sprint = "development_status:\n  epic-2: in-progress\n  2-1-capture: in-progress\n"
    root = _init_project(tmp_path, sprint)
    _set_story_status(root, "2-1-capture", "in-progress")
    state = tmp_path / "state"
    _patch_externals(monkeypatch, pr_calls={})

    runner = MockRunner(
        [
            AgentResult(raw="", text="dev complete; status: review", cost_usd=1.0),  # dev-story
            AgentResult(raw="", text="REVIEW_COMPLETE: clean.", cost_usd=0.2),  # code-review
            AgentResult(raw="", text="SMOKE_PASS: ok.", cost_usd=0.5),  # smoke
        ]
    )
    rc = driver.run(_config(root, no_merge=True), runner=runner, state_dir=str(state))
    assert rc == 0
    kinds = [e["event"] for e in _events(state)]
    assert "dev-gate" in kinds  # dev-story ran -> re-entered at dev
    # the FIRST agent call is dev-story, not create-story (no create on resume)
    first_prompt = runner.calls[0].get("prompt", "")
    assert "bmad-dev-story" in first_prompt
    assert "bmad-create-story" not in first_prompt


def test_review_story_reenters_review_skips_dev(tmp_path, monkeypatch):
    """A paused 'review' story resumes at code-review — dev-story is SKIPPED."""
    sprint = "development_status:\n  epic-2: in-progress\n  2-1-capture: review\n"
    root = _init_project(tmp_path, sprint)
    _set_story_status(root, "2-1-capture", "review")
    state = tmp_path / "state"
    _patch_externals(monkeypatch, pr_calls={})

    runner = MockRunner(
        [
            AgentResult(raw="", text="REVIEW_COMPLETE: clean.", cost_usd=0.2),  # code-review
            AgentResult(raw="", text="SMOKE_PASS: ok.", cost_usd=0.5),  # smoke
        ]
    )
    rc = driver.run(_config(root, no_merge=True), runner=runner, state_dir=str(state))
    assert rc == 0
    kinds = [e["event"] for e in _events(state)]
    assert "review-complete" in kinds
    assert "dev-gate" not in kinds  # dev-story skipped -> re-entered at review
    first_prompt = runner.calls[0].get("prompt", "")
    assert "bmad-code-review" in first_prompt
