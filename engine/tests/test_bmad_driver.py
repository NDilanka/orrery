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

from loop import lockfile
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
            pr_calls.setdefault("git", []).append(list(args))

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


def test_driver_writes_activity_heartbeat_during_a_phase(tmp_path, monkeypatch):
    # Every agent call is wrapped in a liveness Heartbeat that writes <stateDir>/activity.json, so a
    # watcher can tell a long silent phase (a dev-story emits no log line until its gate) from a
    # hung loop. Prove it end-to-end: a runner that reads activity.json DURING its own run() sees a
    # beat already written, tagged with the current phase + the camelCase liveness fields.
    root = _init_project(tmp_path, ONE_READY)
    state = tmp_path / "state"
    pr_calls: dict = {}
    _patch_externals(monkeypatch, pr_calls=pr_calls)

    seen: dict = {}

    class CapturingRunner(MockRunner):
        def run(self, **kwargs):
            if "beat" not in seen:
                ap = state / "activity.json"
                if ap.exists():
                    seen["beat"] = json.loads(ap.read_text(encoding="utf-8"))
            return super().run(**kwargs)

    runner = CapturingRunner(
        [
            AgentResult(raw="", text="dev complete; status: review", cost_usd=1.0),
            AgentResult(raw="", text="REVIEW_COMPLETE: clean.", cost_usd=0.2),
            AgentResult(raw="", text="SMOKE_PASS: ok.", cost_usd=0.5),
        ]
    )
    rc = driver.run(_config(root, no_merge=True), runner=runner, state_dir=str(state))
    assert rc == 0

    beat = seen.get("beat")
    assert beat is not None, "no activity.json beat was written before/during the first agent call"
    # the first agent call is dev-story (ONE_READY skips create-story); the beat is tagged with it
    assert beat.get("phase") == "dev-story", beat
    # camelCase liveness fields are all present
    for k in ("ts", "elapsedSec", "dirty", "pid"):
        assert k in beat, f"missing {k} in beat {beat}"
    # the file persists after the run (the final exit beat)
    assert (state / "activity.json").exists()


def test_driver_persists_raw_output_per_phase_story(tmp_path, monkeypatch):
    """ResilientRunner.run writes each call's raw stdout to run-<phase>-<story>.out.

    AgentResult.raw was previously thrown away entirely, so a failed/halted phase left no
    artifact. Assert the file lands next to activity.json, named by the phase + story
    set_context tagged the call with, and contains that call's raw text verbatim.
    """
    root = _init_project(tmp_path, ONE_READY)
    state = tmp_path / "state"
    pr_calls: dict = {}
    _patch_externals(monkeypatch, pr_calls=pr_calls)

    runner = MockRunner(
        [
            AgentResult(raw='{"phase":"dev"}', text="dev complete; status: review", cost_usd=1.0),
            AgentResult(raw='{"phase":"review"}', text="REVIEW_COMPLETE: clean.", cost_usd=0.2),
            AgentResult(raw='{"phase":"smoke"}', text="SMOKE_PASS: verified AC1, AC2.", cost_usd=0.5),
        ]
    )
    cfg = _config(root, no_merge=True)
    rc = driver.run(cfg, runner=runner, state_dir=str(state))
    assert rc == 0

    dev_out = state / "run-dev-story-2-1-capture.out"
    assert dev_out.exists()
    assert dev_out.read_text(encoding="utf-8") == '{"phase":"dev"}'

    review_out = state / "run-code-review-2-1-capture.out"
    assert review_out.exists()
    assert review_out.read_text(encoding="utf-8") == '{"phase":"review"}'

    smoke_out = state / "run-browser-smoke-2-1-capture.out"
    assert smoke_out.exists()
    assert smoke_out.read_text(encoding="utf-8") == '{"phase":"smoke"}'


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


def test_resume_reuses_existing_pr_when_create_says_already_exists(tmp_path, monkeypatch):
    # A prior run opened the PR then died before merge landed. On the resume tail, `gh pr
    # create` errors ("a pull request already exists"); the driver must reuse the open PR
    # (pr.pr_url) and proceed to merge instead of stalling at PR-create.
    root = _init_project(tmp_path, ONE_DONE)
    state = tmp_path / "state"
    pr_calls: dict = {}
    _patch_externals(monkeypatch, pr_calls=pr_calls)

    def boom_create(*, branch, base, title, body, cwd):
        pr_calls.setdefault("create", []).append({"branch": branch})
        raise pr.PrError(
            "gh pr create failed (exit 1): a pull request for branch already exists"
        )

    def existing_url(*, branch, cwd):
        pr_calls.setdefault("url", []).append({"branch": branch})
        return f"https://example.test/pr/{branch}/11"

    monkeypatch.setattr(pr, "create_pr", boom_create)
    monkeypatch.setattr(pr, "pr_url", existing_url)

    seen = {"n": 0}

    def predicate_factory(**kw):
        def predicate(story):
            seen["n"] += 1
            return seen["n"] == 1

        return predicate

    monkeypatch.setattr(recovery, "unmerged_done_predicate", predicate_factory)

    runner = MockRunner([AgentResult(raw="", text="SMOKE_PASS: verified.", cost_usd=0.5)])
    cfg = _config(root)  # merge ON
    rc = driver.run(cfg, runner=runner, state_dir=str(state))
    assert rc == 0

    kinds = [e["event"] for e in _events(state)]
    assert "pr-created" in kinds  # emitted with the REUSED PR url
    assert "pr-merged" in kinds  # proceeded to merge instead of stalling at create
    assert pr_calls.get("url")  # the existing-PR fallback was exercised
    assert pr_calls.get("merge")


def test_merge_local_cleanup_fails_but_remote_merged_is_success(tmp_path, monkeypatch):
    # `gh pr merge --squash --delete-branch` exits non-zero because its LOCAL post-merge cleanup
    # (checkout base / delete branch) tripped on a dirty tree — but the squash merge ALREADY
    # landed on the remote. The driver must confirm pr_state == MERGED and CONTINUE (pull
    # merge_base + emit pr-merged), NOT halt: halting here is what re-opened + re-merged story 5-1
    # on every resume (PRs #17 and #18 both merged) and would have kept spawning duplicate PRs.
    root = _init_project(tmp_path, ONE_DONE)
    state = tmp_path / "state"
    pr_calls: dict = {}
    _patch_externals(monkeypatch, pr_calls=pr_calls)
    seen = {"n": 0}

    def predicate_factory(**kw):
        def predicate(story):
            seen["n"] += 1
            return seen["n"] == 1

        return predicate

    monkeypatch.setattr(recovery, "unmerged_done_predicate", predicate_factory)

    def cleanup_fails(*, branch, base, cwd):
        pr_calls.setdefault("merge", []).append({"branch": branch})
        raise pr.PrError(
            "gh pr merge feat/story-2-1-capture --squash --delete-branch failed (exit 1): "
            "failed to run git: error: Your local changes to the following files would be "
            "overwritten by checkout:\n\tconvex/_generated/api.d.ts\nAborting"
        )

    monkeypatch.setattr(pr, "merge_pr", cleanup_fails)
    # _patch_externals' fake_state already returns "MERGED" — i.e. the remote merge landed.

    runner = MockRunner([AgentResult(raw="", text="SMOKE_PASS: verified.", cost_usd=0.5)])
    rc = driver.run(_config(root), runner=runner, state_dir=str(state))
    assert rc == 0  # the remote-merged PR is success, not a halt

    evts = _events(state)
    assert "pr-merged" in [e["event"] for e in evts]  # treated as a completed merge
    assert pr_calls.get("merge")  # merge WAS attempted (and raised on local cleanup)
    # local merge_base was advanced so the next scan no longer sees the story as unmerged
    assert ["pull", "origin", "develop"] in pr_calls.get("git", [])
    # NO failure stop event was emitted
    assert not [e for e in evts if e["event"] == "stop" and e.get("ok") is False]


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
    # a live lock owned by a DIFFERENT (live) process -> refuse with 2. The shared lockfile
    # (loop.lockfile) uses ONE filename ("lock") for every driver now — "bmad-lock" is retired.
    other_pid = __import__("os").getpid() + 1
    (state / "lock").write_text(str(other_pid), encoding="utf-8")
    monkeypatch.setattr(lockfile, "pid_alive", lambda pid: True)
    _patch_externals(monkeypatch, pr_calls={})
    rc = driver.run(_config(root), runner=MockRunner([]), state_dir=str(state))
    assert rc == 2


def test_concurrency_guard_ignores_stale_bmad_lock_file(tmp_path, monkeypatch):
    """A leftover 'bmad-lock' from before the lockfile unification is simply ignored (not read):
    a live-looking pid in it must NOT refuse the run, since only 'lock' is ever consulted now."""
    root = _init_project(tmp_path, ONE_READY)
    state = tmp_path / "state"
    state.mkdir(parents=True, exist_ok=True)
    other_pid = __import__("os").getpid() + 1
    (state / "bmad-lock").write_text(str(other_pid), encoding="utf-8")
    monkeypatch.setattr(lockfile, "pid_alive", lambda pid: True)
    pr_calls: dict = {}
    _patch_externals(monkeypatch, pr_calls=pr_calls)
    rc = driver.run(_config(root, no_merge=True), runner=MockRunner([]), state_dir=str(state))
    # NOT refused (2) — the stale bmad-lock is never consulted by the shared lockfile guard.
    assert rc != 2
    # the stale bmad-lock file is left untouched (never read, never migrated)...
    assert (state / "bmad-lock").read_text(encoding="utf-8").strip() == str(other_pid)
    # ...and the real "lock" file was acquired-then-released (gone once the run finished).
    assert not (state / "lock").exists()


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


# ---------------------------------------------------------------------------
# cost-aware model tiers + per-phase token/cache telemetry
# ---------------------------------------------------------------------------


def test_default_models_are_cost_aware_tiers():
    """The BMAD defaults route cheap phases off the (expensive, inherited) Opus tier.

    Only ``dev`` inherits the user's default (``""`` -> the runner omits ``--model``); the
    deciders run on ``haiku`` and the mechanical phases on ``sonnet`` — the fix for the
    'one story per 5-hour window' burn.
    """
    cfg = driver.BmadConfig(project_root="/p")
    assert cfg.model_for("decider") == "haiku"
    assert cfg.model_for("create") == "sonnet"
    assert cfg.model_for("review") == "sonnet"
    assert cfg.model_for("smoke") == "sonnet"
    assert cfg.model_for("retro") == "sonnet"
    assert cfg.model_for("dev") == ""  # inherit (the one phase to spend Opus on)


def test_resilient_runner_emits_token_usage_tagged_by_phase():
    """Every productive run emits a ``token-usage`` event from the result's ``usage`` block.

    Tokens are the Max-plan meter (USD is not), and the data is already in claude's JSON — so
    this surfaces WHICH phase + model ate the window with zero extra token cost.
    """
    events: list[dict] = []
    base = MockRunner(
        [
            AgentResult(
                raw="",
                text="ok",
                cost_usd=0.5,
                usage={
                    "input_tokens": 1000,
                    "output_tokens": 200,
                    "cache_read_input_tokens": 9000,
                    "cache_creation_input_tokens": 500,
                },
            )
        ]
    )
    resilient = driver.ResilientRunner(base, emit=events.append, sleep=lambda s: None)
    resilient.set_context("dev-story", "3-4")
    resilient.run(
        prompt="x", model="opus", allowed_tools=[], permission_mode="acceptEdits",
        max_turns=0, cwd=None,
    )
    tu = [e for e in events if e["event"] == "token-usage"]
    assert len(tu) == 1
    e = tu[0]
    assert e["phase"] == "dev-story"
    assert e["story"] == "3-4"
    assert e["model"] == "opus"
    assert e["input"] == 1000
    assert e["output"] == 200
    assert e["cacheRead"] == 9000
    assert e["cacheCreation"] == 500
    assert e["hitRatio"] == 0.9  # 9000 / (9000 + 1000)
    assert e["warm"] is True
    # cumulative counters carry the running token draw (the real meter)
    assert e["cumInput"] == 1000
    assert e["cumOutput"] == 200
    assert e["cumCacheRead"] == 9000


def test_resilient_runner_accumulates_tokens_and_inherit_label():
    """Cumulative token counters sum across calls; an empty model tier shows as '(inherit)'."""
    events: list[dict] = []
    base = MockRunner(
        [
            AgentResult(raw="", text="a", usage={"input_tokens": 100, "cache_read_input_tokens": 0}),
            AgentResult(raw="", text="b", usage={"input_tokens": 50, "cache_read_input_tokens": 900}),
        ]
    )
    resilient = driver.ResilientRunner(base, emit=events.append, sleep=lambda s: None)
    for _ in range(2):
        resilient.run(
            prompt="x", model="", allowed_tools=[], permission_mode="acceptEdits",
            max_turns=0, cwd=None,
        )
    tu = [e for e in events if e["event"] == "token-usage"]
    assert len(tu) == 2
    assert tu[0]["model"] == "(inherit)"  # "" -> surfaced as inherit
    assert tu[0]["warm"] is False  # no cache reads on the first call
    assert tu[1]["warm"] is True
    assert tu[1]["cumInput"] == 150  # 100 + 50
    assert tu[1]["cumCacheRead"] == 900
    # a decider/non-story call omits the story key
    assert "story" not in tu[0]


def test_resilient_runner_no_usage_emits_no_token_event():
    """A result with no usage telemetry (mock/text-format) emits nothing — keeps logs clean."""
    events: list[dict] = []
    base = MockRunner([AgentResult(raw="", text="done", cost_usd=1.0)])  # usage=None
    resilient = driver.ResilientRunner(base, emit=events.append, sleep=lambda s: None)
    resilient.run(
        prompt="x", model="sonnet", allowed_tools=[], permission_mode="acceptEdits",
        max_turns=0, cwd=None,
    )
    assert [e for e in events if e["event"] == "token-usage"] == []


# ---------------------------------------------------------------------------
# single-pass review / smoke modes (collapse the cold-start fan-out)
# ---------------------------------------------------------------------------


def test_phase_modes_roundtrip_loop_json_and_resume():
    cfg = driver.BmadConfig.from_loop_json(
        {"bmad": {
            "projectRoot": "/p", "reviewMode": "single-pass",
            "smokeMode": "single-pass", "retroMode": "single-pass",
        }}
    )
    assert cfg.review_mode == "single-pass"
    assert cfg.smoke_mode == "single-pass"
    assert cfg.retro_mode == "single-pass"
    # non-default modes ride the resume command so a Reignite preserves them
    cmd = driver._resume_command(cfg, "/state")
    assert "--review-mode single-pass" in cmd
    assert "--smoke-mode single-pass" in cmd
    assert "--retro-mode single-pass" in cmd
    # defaults stay OFF the resume command (and are the parity defaults)
    default = driver.BmadConfig(project_root="/p")
    assert default.review_mode == "qa"
    assert default.smoke_mode == "iterative"
    assert default.retro_mode == "qa"
    cmd2 = driver._resume_command(default, "/state")
    assert "--review-mode" not in cmd2
    assert "--smoke-mode" not in cmd2
    assert "--retro-mode" not in cmd2


def test_loop_json_path_roundtrips_into_resume_command():
    """A Reignite must re-point at --loop-json to restore per-phase models/effort (no CLI flag)."""
    from types import SimpleNamespace

    cfg = driver.BmadConfig.from_args(
        SimpleNamespace(project_root="/p", loop_json="D:/cfg/bmad engine.json")
    )
    assert cfg.loop_json == "D:/cfg/bmad engine.json"
    cmd = driver._resume_command(cfg, "/state")
    assert '--loop-json "D:/cfg/bmad engine.json"' in cmd  # quoted: path contains a space
    # a run with no --loop-json keeps the flag OFF the resume command (parity default)
    assert "--loop-json" not in driver._resume_command(driver.BmadConfig(project_root="/p"), "/state")


def test_run_retro_single_pass_one_process():
    """single-pass retro: ONE warm facilitator process, no QUESTION/decider Q&A round-trips."""
    events: list[dict] = []
    runner = MockRunner(
        [AgentResult(raw="", text="RETRO_COMPLETE: solid epic; carry TDD forward.", cost_usd=0.5)]
    )
    cfg = driver.BmadConfig(project_root="/p", retro_mode="single-pass")
    ok, cost = driver._run_retro(cfg, runner, "2", emit=events.append, cwd="/repo")
    assert ok is True
    assert cost == 0.5
    assert len(runner.calls) == 1
    assert runner.calls[0]["max_turns"] == 0
    kinds = [e["event"] for e in events]
    assert kinds == ["retro-complete"]
    assert "retro-question" not in kinds
    # the decider stance is folded into the facilitator's own prompt (no separate decider)
    assert "DECIDE every point yourself" in runner.calls[0]["prompt"]


def test_run_retro_single_pass_timeout_halts_and_threads_timeout_sec():
    events: list[dict] = []
    runner = MockRunner([AgentResult(raw="", text="", is_error=True, timed_out=True)])
    cfg = driver.BmadConfig(project_root="/p", retro_mode="single-pass", retro_timeout_min=15)
    ok, cost = driver._run_retro(cfg, runner, "2", emit=events.append, cwd="/repo")
    assert ok is False
    assert runner.calls[0]["timeout_sec"] == 900
    kinds = [e["event"] for e in events]
    assert "phase-timeout" in kinds
    assert "retro-complete" not in kinds


def test_run_retro_qa_mode_timeout_halts_and_threads_timeout_sec():
    """Task 1b: a timed-out turn in the QA-mode retro Q&A loop takes the same non-ok exit as
    is_error/parse_failed — it must not treat the (empty, killed) turn as RETRO_COMPLETE."""
    events: list[dict] = []
    runner = MockRunner([AgentResult(raw="", text="", is_error=True, timed_out=True)])
    cfg = driver.BmadConfig(project_root="/p", retro_mode="qa", retro_timeout_min=20)
    ok, cost = driver._run_retro(cfg, runner, "3", emit=events.append, cwd="/repo")
    assert ok is False
    assert runner.calls[0]["timeout_sec"] == 1200
    kinds = [e["event"] for e in events]
    assert "phase-timeout" in kinds
    assert "retro-complete" not in kinds


def test_run_retro_timeout_min_zero_disables_the_cap():
    events: list[dict] = []
    runner = MockRunner(
        [AgentResult(raw="", text="RETRO_COMPLETE: fine.", cost_usd=0.1)]
    )
    cfg = driver.BmadConfig(project_root="/p", retro_mode="single-pass", retro_timeout_min=0)
    ok, cost = driver._run_retro(cfg, runner, "2", emit=events.append, cwd="/repo")
    assert ok is True
    assert runner.calls[0]["timeout_sec"] == 0


def test_merge_wait_config_roundtrip():
    cfg = driver.BmadConfig.from_loop_json({"bmad": {"projectRoot": "/p", "mergeWaitSec": 120}})
    assert cfg.merge_wait_sec == 120
    assert "--merge-wait-sec 120" in driver._resume_command(cfg, "/state")
    # default 0 stays off the resume command (parity)
    assert driver.BmadConfig(project_root="/p").merge_wait_sec == 0
    assert "--merge-wait-sec" not in driver._resume_command(driver.BmadConfig(project_root="/p"), "/state")


def test_merge_wait_sec_polls_queued_merge_then_continues(tmp_path, monkeypatch):
    """A QUEUED merge (branch protection) is polled until MERGED instead of halting immediately."""
    root = _init_project(tmp_path, ONE_READY)
    state = tmp_path / "state"
    pr_calls: dict = {}
    _patch_externals(monkeypatch, pr_calls=pr_calls)
    monkeypatch.setattr(driver.time, "sleep", lambda s: None)  # no real sleep
    seq = iter(["QUEUED", "QUEUED", "MERGED"])  # lands on the 3rd poll
    monkeypatch.setattr(pr, "pr_state", lambda *, branch, cwd: next(seq, "MERGED"))
    runner = MockRunner([
        AgentResult(raw="", text="dev complete; status: review", cost_usd=1.0),
        AgentResult(raw="", text="REVIEW_COMPLETE: ok.", cost_usd=0.2),
        AgentResult(raw="", text="SMOKE_PASS: ok.", cost_usd=0.3),
    ])
    driver.run(_config(root, no_merge=False, merge_wait_sec=120), runner=runner, state_dir=str(state))
    kinds = [e["event"] for e in _events(state)]
    assert "pr-merged" in kinds  # polled to MERGED rather than the immediate "did not complete" halt


def test_after_story_stop_honored_right_after_merge(tmp_path, monkeypatch):
    """A stop requested mid-story is honored at the new post-merge boundary (story completed)."""
    root = _init_project(tmp_path, ONE_READY)
    state = tmp_path / "state"
    state.mkdir(parents=True, exist_ok=True)
    pr_calls: dict = {}
    _patch_externals(monkeypatch, pr_calls=pr_calls)

    # Request a stop DURING the merge (so the top-of-loop between-stories check hadn't seen it yet).
    def merge_then_request_stop(*, branch, base, cwd):
        (state / "STOP").write_text("story", encoding="utf-8")
        pr_calls.setdefault("merge", []).append({"branch": branch})
        return "merged"

    monkeypatch.setattr(pr, "merge_pr", merge_then_request_stop)
    runner = MockRunner([
        AgentResult(raw="", text="dev complete; status: review", cost_usd=1.0),
        AgentResult(raw="", text="REVIEW_COMPLETE: ok.", cost_usd=0.2),
        AgentResult(raw="", text="SMOKE_PASS: ok.", cost_usd=0.3),
    ])
    rc = driver.run(_config(root, no_merge=False), runner=runner, state_dir=str(state))
    assert rc == 0
    kinds = [e["event"] for e in _events(state)]
    assert "pr-merged" in kinds  # the story DID complete + merge first
    coop = [e for e in _events(state) if e["event"] == "cooperative-stop"]
    assert coop and coop[-1]["stage"].startswith("story-merged")


def test_spin_guard_halts_on_no_progress_reselection(tmp_path, monkeypatch):
    """A story re-selected at the same status (a phase didn't advance it) halts, never spins."""
    root = _init_project(tmp_path, ONE_READY)  # 2-1-capture stays ready-for-dev (mock never writes)
    state = tmp_path / "state"
    pr_calls: dict = {}
    _patch_externals(monkeypatch, pr_calls=pr_calls)
    runner = MockRunner([
        AgentResult(raw="", text="dev complete; status: review", cost_usd=1.0),
        AgentResult(raw="", text="REVIEW_COMPLETE: ok.", cost_usd=0.2),
        AgentResult(raw="", text="SMOKE_PASS: ok.", cost_usd=0.3),
    ])
    # story 1 "merges" (mock), returns None -> iteration 2 re-selects the SAME ready story -> halt.
    rc = driver.run(_config(root, no_merge=False), runner=runner, state_dir=str(state))
    assert rc == 1
    stop = [e for e in _events(state) if e["event"] == "stop"][-1]
    assert stop["ok"] is False
    assert "WITHOUT advancing" in stop["reason"]
    assert "2-1-capture" in stop["reason"]


def test_effort_config_from_loop_json_and_defaults():
    cfg = driver.BmadConfig.from_loop_json(
        {"bmad": {"projectRoot": "/p", "effort": {"dev": "xhigh", "review": "xhigh", "decider": "low"}}}
    )
    assert cfg.effort_for("dev") == "xhigh"
    assert cfg.effort_for("review") == "xhigh"
    assert cfg.effort_for("decider") == "low"
    assert cfg.effort_for("smoke") == ""  # unset -> inherit
    # engine default is all-inherit (byte-parity: no --effort emitted)
    assert driver.BmadConfig(project_root="/p").effort_for("dev") == ""


def test_review_and_smoke_single_pass_end_to_end(tmp_path, monkeypatch):
    """single-pass review + smoke run the story with NO Q&A fan-out and NO smoke re-spawn."""
    root = _init_project(tmp_path, ONE_READY)
    state = tmp_path / "state"
    pr_calls: dict = {}
    _patch_externals(monkeypatch, pr_calls=pr_calls)
    runner = MockRunner(
        [
            AgentResult(raw="", text="dev complete; status: review", cost_usd=1.0),
            AgentResult(raw="", text="REVIEW_COMPLETE: decided + applied; green.", cost_usd=0.4),
            AgentResult(raw="", text="SMOKE_PASS: verified ACs.", cost_usd=0.3),
        ]
    )
    cfg = _config(root, no_merge=True, review_mode="single-pass", smoke_mode="single-pass")
    rc = driver.run(cfg, runner=runner, state_dir=str(state))
    assert rc == 0
    kinds = [e["event"] for e in _events(state)]
    # single-pass review: completes WITHOUT any QUESTION/answer round-trip
    assert "review-complete" in kinds
    assert "review-question" not in kinds
    assert "review-answer" not in kinds
    assert "pr-created" in kinds
    # exactly THREE agent processes for the whole story: dev + review(1) + smoke(1).
    # The default Q&A + iterative path would spawn extra decider/re-spawn processes.
    assert len(runner.calls) == 3


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
    # Simulate the bmad-retrospective skill: when invoked it flips the flag to 'done' on disk
    # (its Step 11) and emits RETRO_COMPLETE — the real contract the driver depends on.
    status_path = root / "_bmad-output" / "implementation-artifacts" / "sprint-status.yaml"

    class RetroRunner(AgentRunner):
        name = "retro-mock"
        supports_sessions = True

        def __init__(self):
            self.calls: list[dict] = []

        def run(self, **kwargs):
            self.calls.append(dict(kwargs))
            status_path.write_text(
                status_path.read_text(encoding="utf-8").replace(
                    "epic-2-retrospective: optional", "epic-2-retrospective: done"
                ),
                encoding="utf-8",
            )
            return AgentResult(raw="", text="RETRO_COMPLETE: good epic.", cost_usd=0.4)

    rc = driver.run(_config(root), runner=RetroRunner(), state_dir=str(state))
    assert rc == 0
    kinds = [e["event"] for e in _events(state)]
    assert "retro-start" in kinds
    assert "retro-complete" in kinds
    # fired exactly once — the flipped flag stops it re-triggering on the next scan
    assert sum(1 for k in kinds if k == "retro-start") == 1
    # the retro commit is PUSHED to the merge base (bmad-loop.ps1 parity; not left local)
    assert ["push", "origin", "develop"] in pr_calls.get("git", [])
    # after the retro the loop re-scans; nothing else actionable -> backlog complete (ok)
    stop = [e for e in _events(state) if e["event"] == "stop"][-1]
    assert stop["ok"] is True


def test_retro_halts_if_skill_did_not_mark_done(tmp_path, monkeypatch):
    # The retro reports complete (RETRO_COMPLETE) but the skill failed to flip the flag. The
    # driver must HALT with an actionable message rather than silently re-running the same retro
    # every iteration up to --max-stories.
    root = _init_project(tmp_path, EPIC_DONE_RETRO_PENDING)
    state = tmp_path / "state"
    pr_calls: dict = {}
    _patch_externals(monkeypatch, pr_calls=pr_calls)
    monkeypatch.setattr(
        recovery, "unmerged_done_predicate", lambda **kw: (lambda story: False)
    )
    runner = MockRunner(
        [AgentResult(raw="", text="RETRO_COMPLETE: claimed, but flag not written", cost_usd=0.1)]
    )
    rc = driver.run(_config(root, max_stories=3), runner=runner, state_dir=str(state))
    assert rc == 1
    kinds = [e["event"] for e in _events(state)]
    # ran the retro exactly once, then halted (did NOT spin to the max-stories backstop)
    assert sum(1 for k in kinds if k == "retro-start") == 1
    stop = [e for e in _events(state) if e["event"] == "stop"][-1]
    assert stop["ok"] is False
    assert "still 'optional'" in stop["reason"]


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
    # decider now defaults to the cheap haiku tier (was "" = silently inherit Opus, which
    # overrode decider.py's own haiku default — the cost bug this fix closes).
    assert cfg.model_for("decider") == "haiku"


def test_bmadconfig_requires_project_root():
    with pytest.raises(TypeError):
        driver.BmadConfig()  # type: ignore[call-arg]


# ---------------------------------------------------------------------------
# Task 1b — per-phase wall-clock timeout fields
# ---------------------------------------------------------------------------


def test_bmadconfig_phase_timeout_defaults():
    cfg = driver.BmadConfig(project_root="/p")
    assert cfg.create_timeout_min == 30
    assert cfg.dev_timeout_min == 120
    assert cfg.review_timeout_min == 60
    assert cfg.retro_timeout_min == 30
    assert cfg.smoke_timeout_min == 12  # pre-existing field, unchanged


def test_bmadconfig_phase_timeouts_from_loop_json_camel_case():
    cfg = driver.BmadConfig.from_loop_json(
        {
            "bmad": {
                "projectRoot": "/p",
                "createTimeoutMin": 5,
                "devTimeoutMin": 90,
                "reviewTimeoutMin": 45,
                "retroTimeoutMin": 10,
            }
        }
    )
    assert cfg.create_timeout_min == 5
    assert cfg.dev_timeout_min == 90
    assert cfg.review_timeout_min == 45
    assert cfg.retro_timeout_min == 10


def test_bmadconfig_phase_timeouts_from_loop_json_snake_case():
    cfg = driver.BmadConfig.from_loop_json(
        {"bmad": {"project_root": "/p", "create_timeout_min": 1, "dev_timeout_min": 2}}
    )
    assert cfg.create_timeout_min == 1
    assert cfg.dev_timeout_min == 2


def test_bmadconfig_phase_timeout_from_args_zero_disables_not_default():
    """`is not None` (not `or`) — an explicit 0 on the CLI must not be coerced to the default."""
    from types import SimpleNamespace

    cfg = driver.BmadConfig.from_args(
        SimpleNamespace(project_root="/p", dev_timeout_min=0, create_timeout_min=None)
    )
    assert cfg.dev_timeout_min == 0  # explicit 0 preserved
    assert cfg.create_timeout_min == 30  # absent -> default


def test_resume_command_roundtrips_phase_timeout_flags():
    cfg = driver.BmadConfig(
        project_root="/p", create_timeout_min=5, dev_timeout_min=90,
        review_timeout_min=45, retro_timeout_min=10,
    )
    cmd = driver._resume_command(cfg, "/state")
    assert "--create-timeout-min 5" in cmd
    assert "--dev-timeout-min 90" in cmd
    assert "--review-timeout-min 45" in cmd
    assert "--retro-timeout-min 10" in cmd
    # defaults stay OFF the resume command
    default_cmd = driver._resume_command(driver.BmadConfig(project_root="/p"), "/state")
    assert "--create-timeout-min" not in default_cmd
    assert "--dev-timeout-min" not in default_cmd
    assert "--review-timeout-min" not in default_cmd
    assert "--retro-timeout-min" not in default_cmd


def test_process_story_threads_phase_timeouts_into_dev_and_review_calls(tmp_path, monkeypatch):
    """End-to-end proof the driver actually passes config.*_timeout_min (in SECONDS) down to
    the dev-story and code-review runner.run calls."""
    root = _init_project(tmp_path, ONE_READY)
    state = tmp_path / "state"
    pr_calls: dict = {}
    _patch_externals(monkeypatch, pr_calls=pr_calls)

    runner = MockRunner(
        [
            AgentResult(raw="", text="dev complete; status: review", cost_usd=1.0),
            AgentResult(raw="", text="REVIEW_COMPLETE: clean.", cost_usd=0.2),
            AgentResult(raw="", text="SMOKE_PASS: verified AC1, AC2.", cost_usd=0.5),
        ]
    )
    cfg = _config(
        root, no_merge=True, dev_timeout_min=45, review_timeout_min=20,
    )
    rc = driver.run(cfg, runner=runner, state_dir=str(state))
    assert rc == 0
    assert runner.calls[0]["timeout_sec"] == 45 * 60  # dev-story
    assert runner.calls[1]["timeout_sec"] == 20 * 60  # code-review


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


# ---------------------------------------------------------------------------
# Flaky-test tolerance: a single flaky `test` failure must NOT terminally stop
# the loop. `_run_gate` re-runs a flaky-SHAPED red gate; a deterministic failure
# (codegen/lint red, or many failures) still fails fast. (regression guard for the
# "post-code-review gate red: ... test=1" terminal stops seen on real runs.)
# ---------------------------------------------------------------------------


def _fake_gate(green, fail, *, codegen_ok=True, lint_ok=True, test_ok=None):
    """Build a run_gate-style result dict with per-stage ok flags."""
    if test_ok is None:
        test_ok = fail == 0
    return {
        "green": green,
        "pass": 1098,
        "fail": fail,
        "total": 1098 + fail,
        "stages": [
            {"name": "codegen", "ok": codegen_ok, "exit": 0 if codegen_ok else 1},
            {"name": "lint", "ok": lint_ok, "exit": 0 if lint_ok else 1},
            {"name": "test", "ok": test_ok, "exit": 0 if test_ok else 1},
        ],
        "raw": "",
    }


def test_is_flaky_shape_classification():
    f = driver._is_flaky_shape
    # the recurring signature: codegen+lint green, 1 failing test
    assert f(_fake_gate(False, 1), 2) is True
    assert f(_fake_gate(False, 2), 2) is True
    # NOT flaky: too many failures (a real regression)
    assert f(_fake_gate(False, 3), 2) is False
    # NOT flaky: a deterministic codegen / lint failure
    assert f(_fake_gate(False, 1, codegen_ok=False), 2) is False
    assert f(_fake_gate(False, 1, lint_ok=False), 2) is False
    # NOT flaky: red with no failing-test count (fail==0) — not the test signature
    assert f(_fake_gate(False, 0, test_ok=False), 2) is False
    # a green gate is never "flaky red"
    assert f(_fake_gate(True, 0), 2) is False


def _patch_run_gate(monkeypatch, results):
    """Stub loop.gate.run_gate to yield `results` in order (repeating the last)."""
    calls = {"n": 0}

    def fake(stages, cwd):
        idx = min(calls["n"], len(results) - 1)
        calls["n"] += 1
        return results[idx]

    monkeypatch.setattr("loop.gate.run_gate", fake)
    return calls


def test_run_gate_retries_flaky_then_passes(tmp_path, monkeypatch):
    cfg = driver.BmadConfig(
        project_root=str(tmp_path), gate_flaky_retries=2, gate_flaky_max_fail=2
    )
    calls = _patch_run_gate(monkeypatch, [_fake_gate(False, 1), _fake_gate(True, 0)])
    emitted = []
    g = driver._run_gate(cfg, tmp_path, emitted.append)
    assert g["green"] is True  # the retry confirmed it was flaky
    assert calls["n"] == 2  # initial + one retry
    assert [e["event"] for e in emitted] == ["gate-retry"]
    assert emitted[0]["attempt"] == 1 and emitted[0]["fail"] == 1


def test_run_gate_does_not_retry_deterministic_failure(tmp_path, monkeypatch):
    cfg = driver.BmadConfig(project_root=str(tmp_path), gate_flaky_retries=2)
    calls = _patch_run_gate(monkeypatch, [_fake_gate(False, 1, lint_ok=False)])
    emitted = []
    g = driver._run_gate(cfg, tmp_path, emitted.append)
    assert g["green"] is False
    assert calls["n"] == 1  # failed fast — no retry
    assert emitted == []


def test_run_gate_gives_up_after_exhausting_retries(tmp_path, monkeypatch):
    cfg = driver.BmadConfig(
        project_root=str(tmp_path), gate_flaky_retries=2, gate_flaky_max_fail=2
    )
    calls = _patch_run_gate(monkeypatch, [_fake_gate(False, 1)])  # always red
    emitted = []
    g = driver._run_gate(cfg, tmp_path, emitted.append)
    assert g["green"] is False  # persistently red => a real failure, reported
    assert calls["n"] == 3  # initial + 2 retries
    assert len(emitted) == 2  # one gate-retry per retry attempt


def test_loop_json_can_tune_flaky_knobs():
    cfg = driver.BmadConfig.from_loop_json(
        {"bmad": {"project_root": "x", "gateFlakyRetries": 5, "gateFlakyMaxFail": 3}}
    )
    assert cfg.gate_flaky_retries == 5
    assert cfg.gate_flaky_max_fail == 3


# ---------------------------------------------------------------------------
# Task 4 — the folded bmad/loop.json single-file seed (+ the deprecated but still-working
# bmad-engine.json) both parse cleanly through BmadConfig.from_loop_json.
# ---------------------------------------------------------------------------


def test_seed_bmad_loop_json_parses_with_namespaced_block(capsys):
    data = json.loads(Path("orrery/loops/bmad/loop.json").read_text(encoding="utf-8"))
    cfg = driver.BmadConfig.from_loop_json({**data, "bmad": {**data["bmad"], "projectRoot": "/p"}})
    assert cfg.project_root == "/p"
    assert cfg.model_for("dev") == "claude-opus-4-8[1m]"
    assert cfg.effort_for("dev") == "xhigh"
    assert cfg.review_mode == "single-pass"
    assert cfg.smoke_mode == "single-pass"
    assert cfg.retro_mode == "single-pass"
    # orrery-side top-level keys (id/name/start/stateDir/...) are outside the "bmad" block ->
    # no unknown-key noise (only the namespaced block is ever inspected).
    assert capsys.readouterr().err == ""


def test_seed_bmad_engine_json_still_parses_deprecated_but_working():
    data = json.loads(Path("orrery/loops/bmad/bmad-engine.json").read_text(encoding="utf-8"))
    data["bmad"]["projectRoot"] = "/p"
    cfg = driver.BmadConfig.from_loop_json(data)
    assert cfg.model_for("dev") == "claude-opus-4-8[1m]"
    assert cfg.review_mode == "single-pass"


def test_seed_bmad_loop_json_intra_loop_paths_are_relative():
    """A4 Task 3: stateDir/stopFlag/checkpoint + the matching --state-dir/--loop-json args are
    RELATIVE (portable across machines/checkouts); only --project-root, which points OUTSIDE the
    loop dir at the external brain2 repo, stays absolute."""
    data = json.loads(Path("orrery/loops/bmad/loop.json").read_text(encoding="utf-8"))
    assert data["stateDir"] == ".loop"
    assert data["stopFlag"] == ".loop/STOP"
    assert data["checkpoint"] == ".loop/checkpoint.json"
    args = data["start"]["args"]
    assert "--state-dir" in args and args[args.index("--state-dir") + 1] == ".loop"
    assert "--loop-json" in args and args[args.index("--loop-json") + 1] == "loop.json"
    # the external project root is a different repo entirely -> must stay absolute.
    assert args[args.index("--project-root") + 1] == "D:/dev/brain2"
