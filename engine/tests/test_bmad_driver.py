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
