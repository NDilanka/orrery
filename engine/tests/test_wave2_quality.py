"""Wave-2 quality features for the BMAD driver — five additive/parity-safe gates.

Covers, in isolation and end-to-end:

- FEATURE 1: adversarial verify-before-merge (REFUTE blocks the PR; PASS proceeds; skipped when
  no baseline; inconclusive/no-verdict/errored fail-open; modified test files flow into the prompt).
- FEATURE 2: test-integrity via git (a DELETED pre-existing test halts; halt_on_deletion off warns
  and proceeds; a MODIFIED test is not a halt but is surfaced).
- FEATURE 3: plan-gate before dev (BLOCKED halts; PLAN_OK / garbage proceed).
- FEATURE 4: run-quality `metrics` event emitted at stop with sane aggregates.
- FEATURE 5: gate fail-fast threading (a red lint skips the test stage, no flaky retries).

Plus config parsing (camel+snake, defaults, unknown-key quiet) + `_resume_command` round-trip +
the `--no-verify` / `--no-plan-gate` CLI flags.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

from loop import cli
from loop.bmad import driver, pr
from loop.bmad import phases as phases_mod
from loop.bmad.sprint import Story
from loop.runners.base import AgentResult, AgentRunner


# ---------------------------------------------------------------------------
# doubles / helpers
# ---------------------------------------------------------------------------


class RecordingRunner(AgentRunner):
    """Records every run() kwargs; returns queued results (default: text 'ok')."""

    name = "recording"
    supports_sessions = True

    def __init__(self, results=None):
        self._results = list(results or [])
        self.calls: list[dict] = []

    def run(self, **kwargs) -> AgentResult:  # type: ignore[override]
        self.calls.append(dict(kwargs))
        return self._results.pop(0) if self._results else AgentResult(raw="", text="ok")


class MockRunner(AgentRunner):
    """Queued results; advances the story file's Status on the dev-story prompt (like the real
    agent). Mirrors the double in test_bmad_driver.py."""

    name = "mock"
    supports_sessions = True

    def __init__(self, results=None):
        self._results = list(results or [])
        self.calls: list[dict] = []

    def run(self, **kwargs) -> AgentResult:  # type: ignore[override]
        self.calls.append(dict(kwargs))
        prompt = str(kwargs.get("prompt", ""))
        if "bmad-dev-story" in prompt:
            cwd = kwargs.get("cwd")
            if cwd:
                art = Path(cwd) / "_bmad-output" / "implementation-artifacts"
                for f in art.glob("*.md"):
                    t = f.read_text(encoding="utf-8")
                    n = t.replace("Status: ready-for-dev", "Status: review")
                    if n != t:
                        f.write_text(n, encoding="utf-8")
        return self._results.pop(0) if self._results else AgentResult(raw="", text="ok")


class FakeServer:
    instances: list["FakeServer"] = []

    def __init__(self, *a, **k):
        FakeServer.instances.append(self)

    def start(self):
        return "http://localhost:4137"

    def stop(self):
        pass


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True)


def _head(repo: Path) -> str:
    r = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True)
    return r.stdout.strip()


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


def _init_project(tmp_path: Path, sprint: str, *, story_key="2-1-capture", with_baseline=False):
    """Temp git repo with the BMAD artifacts + a story file, optionally carrying a real
    baseline_commit line (so FEATURE 1/2 have a diff to inspect). Returns (root, baseline_sha)."""
    root = tmp_path / "project"
    art = root / "_bmad-output" / "implementation-artifacts"
    art.mkdir(parents=True)
    (art / "sprint-status.yaml").write_text(sprint, encoding="utf-8")
    story = art / f"{story_key}.md"
    story.write_text(
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
    base = _head(root)
    if with_baseline:
        story.write_text(story.read_text(encoding="utf-8") + f"\nbaseline_commit: {base}\n", encoding="utf-8")
        _git(root, "add", "-A")
        _git(root, "commit", "-q", "-m", "record baseline")
    _git(root, "branch", "develop")
    return root, base


def _patch_externals(monkeypatch, pr_calls: dict, *, merge=True):
    FakeServer.instances.clear()
    monkeypatch.setattr(phases_mod, "DevServer", FakeServer)
    monkeypatch.setattr(pr, "create_pr", lambda **k: (pr_calls.setdefault("create", []).append(k), "https://example.test/pr")[1])
    monkeypatch.setattr(pr, "merge_pr", lambda **k: (pr_calls.setdefault("merge", []).append(k), "merged")[1])
    monkeypatch.setattr(pr, "pr_state", lambda **k: "MERGED")
    real_git = driver.gitutil._git

    def guard_git(args, cwd):
        if args and args[0] in ("push", "pull"):
            pr_calls.setdefault("git", []).append(list(args))

            class _R:
                returncode = 0
                stdout = ""
                stderr = ""

            return _R()
        return real_git(args, cwd)

    monkeypatch.setattr(driver.gitutil, "_git", guard_git)


def _config(root: Path, base: str, **overrides) -> driver.BmadConfig:
    cfg = driver.BmadConfig(project_root=str(root), merge_base="develop", gate_stages=_gate_stages())
    for k, v in overrides.items():
        setattr(cfg, k, v)
    return cfg


def _events(state: Path) -> list[dict]:
    log = state / "log.jsonl"
    if not log.exists():
        return []
    return [json.loads(ln) for ln in log.read_text(encoding="utf-8").splitlines() if ln.strip()]


def _story(key="2-1-capture", status="done"):
    return Story(key=key, status=status, raw_status=status, epic="2", index=0)


ONE_READY = "development_status:\n  epic-2: in-progress\n  2-1-capture: ready-for-dev\n"


# ---------------------------------------------------------------------------
# FEATURE 1 — adversarial verify-before-merge
# ---------------------------------------------------------------------------


def _resilient(base, events):
    return driver.ResilientRunner(base, emit=events.append, sleep=lambda s: None)


def test_verify_refute_blocks_pr_and_halts_end_to_end(tmp_path, monkeypatch):
    root, base = _init_project(tmp_path, ONE_READY, with_baseline=True)
    state = tmp_path / "state"
    pr_calls: dict = {}
    _patch_externals(monkeypatch, pr_calls)
    runner = MockRunner([
        AgentResult(raw="", text="PLAN_OK", cost_usd=0.0),
        AgentResult(raw="", text="dev complete; status: review", cost_usd=1.0),
        AgentResult(raw="", text="REVIEW_COMPLETE: clean.", cost_usd=0.2),
        AgentResult(raw="", text="SMOKE_PASS: verified.", cost_usd=0.3),
        AgentResult(raw="", text="VERDICT: REFUTE — AC2 (save a note) is not implemented", cost_usd=0.1),
    ])
    rc = driver.run(_config(root, base, no_merge=True), runner=runner, state_dir=str(state))
    assert rc == 1
    evts = _events(state)
    kinds = [e["event"] for e in evts]
    vf = [e for e in evts if e["event"] == "verify"][-1]
    assert vf["verdict"] == "refute"
    assert "AC2" in vf["reason"]
    # the PR was BLOCKED — verify runs before push/PR
    assert "pr-created" not in kinds
    assert "create" not in pr_calls
    stop = [e for e in evts if e["event"] == "stop"][-1]
    assert stop["ok"] is False
    assert "REFUTED" in stop["reason"]


def test_verify_pass_proceeds_to_pr_end_to_end(tmp_path, monkeypatch):
    root, base = _init_project(tmp_path, ONE_READY, with_baseline=True)
    state = tmp_path / "state"
    pr_calls: dict = {}
    _patch_externals(monkeypatch, pr_calls)
    runner = MockRunner([
        AgentResult(raw="", text="PLAN_OK", cost_usd=0.0),
        AgentResult(raw="", text="dev complete; status: review", cost_usd=1.0),
        AgentResult(raw="", text="REVIEW_COMPLETE: clean.", cost_usd=0.2),
        AgentResult(raw="", text="SMOKE_PASS: verified.", cost_usd=0.3),
        AgentResult(raw="", text="Everything checks out.\nVERDICT: PASS", cost_usd=0.1),
    ])
    rc = driver.run(_config(root, base, no_merge=True), runner=runner, state_dir=str(state))
    assert rc == 0
    evts = _events(state)
    assert [e for e in evts if e["event"] == "verify"][-1]["verdict"] == "pass"
    assert "pr-created" in [e["event"] for e in evts]
    assert pr_calls.get("create")
    # 5 agent calls: plan + dev + review + smoke + verify (baseline present -> verify DID call)
    assert len(runner.calls) == 5


def test_verify_skipped_when_no_baseline_makes_no_call():
    events: list[dict] = []
    base = RecordingRunner()
    resilient = _resilient(base, events)
    # story_text has NO baseline_commit; _run_verify reads baseline from the (absent) file on disk
    exit_code, cost = driver._run_verify(
        driver.BmadConfig(project_root="/p"), resilient, _story(status="review"),
        "## Acceptance Criteria\n1. x\n", repo=Path("/p"), project_root=Path("/p"),
        modified_tests=[], emit=events.append, cwd="/p", cum=0.0, branch="feat/x", resume_cmd="R",
    )
    assert exit_code is None and cost == 0.0
    assert base.calls == []  # no runner call when there's nothing to diff
    assert [e for e in events if e["event"] == "verify"][0]["verdict"] == "skipped"


def test_verify_inconclusive_on_no_verdict_proceeds(tmp_path):
    root, base = _init_project(tmp_path, ONE_READY, story_key="2-1-capture", with_baseline=True)
    (root / "x.txt").write_text("change\n", encoding="utf-8")  # make HEAD differ from baseline
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "wip")
    events: list[dict] = []
    runner = RecordingRunner([AgentResult(raw="", text="I could not reach a conclusion.")])
    resilient = _resilient(runner, events)
    text = (root / "_bmad-output" / "implementation-artifacts" / "2-1-capture.md").read_text(encoding="utf-8")
    exit_code, _cost = driver._run_verify(
        driver.BmadConfig(project_root=str(root)), resilient, _story(), text,
        repo=root, project_root=root, modified_tests=[], emit=events.append,
        cwd=str(root), cum=0.0, branch="feat/x", resume_cmd="R",
    )
    assert exit_code is None  # fail-open
    assert len(runner.calls) == 1  # it DID make a verify call (baseline present)
    assert [e for e in events if e["event"] == "verify"][0]["verdict"] == "inconclusive"


def test_verify_inconclusive_on_errored_call(tmp_path):
    root, base = _init_project(tmp_path, ONE_READY, with_baseline=True)
    (root / "x.txt").write_text("c\n", encoding="utf-8")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "wip")
    events: list[dict] = []
    runner = RecordingRunner([AgentResult(raw="", text="", is_error=True)])
    resilient = _resilient(runner, events)
    text = (root / "_bmad-output" / "implementation-artifacts" / "2-1-capture.md").read_text(encoding="utf-8")
    exit_code, _c = driver._run_verify(
        driver.BmadConfig(project_root=str(root)), resilient, _story(), text,
        repo=root, project_root=root, modified_tests=[], emit=events.append,
        cwd=str(root), cum=0.0, branch="feat/x", resume_cmd="R",
    )
    assert exit_code is None
    assert [e for e in events if e["event"] == "verify"][0]["verdict"] == "inconclusive"


def test_verify_prompt_includes_modified_test_files(tmp_path):
    root, base = _init_project(tmp_path, ONE_READY, with_baseline=True)
    (root / "x.txt").write_text("c\n", encoding="utf-8")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "wip")
    events: list[dict] = []
    runner = RecordingRunner([AgentResult(raw="", text="VERDICT: PASS")])
    resilient = _resilient(runner, events)
    text = (root / "_bmad-output" / "implementation-artifacts" / "2-1-capture.md").read_text(encoding="utf-8")
    driver._run_verify(
        driver.BmadConfig(project_root=str(root)), resilient, _story(), text,
        repo=root, project_root=root, modified_tests=["src/foo.test.ts"], emit=events.append,
        cwd=str(root), cum=0.0, branch="feat/x", resume_cmd="R",
    )
    prompt = runner.calls[0]["prompt"]
    assert "MODIFIED these PRE-EXISTING test files" in prompt
    assert "src/foo.test.ts" in prompt
    # the verifier runs single-turn, refute-biased, with read-only tools
    assert runner.calls[0]["max_turns"] == 1
    assert runner.calls[0]["permission_mode"] == "plan"
    assert runner.calls[0]["model"] == "haiku"


def test_verify_uses_last_verdict_line(tmp_path):
    root, base = _init_project(tmp_path, ONE_READY, with_baseline=True)
    (root / "x.txt").write_text("c\n", encoding="utf-8")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "wip")
    events: list[dict] = []
    # A stray earlier "VERDICT: PASS" in the reasoning must NOT win over the final REFUTE line.
    runner = RecordingRunner([AgentResult(raw="", text="At first VERDICT: PASS seemed right, but:\nVERDICT: REFUTE — AC1 fails")])
    resilient = _resilient(runner, events)
    text = (root / "_bmad-output" / "implementation-artifacts" / "2-1-capture.md").read_text(encoding="utf-8")
    exit_code, _c = driver._run_verify(
        driver.BmadConfig(project_root=str(root)), resilient, _story(), text,
        repo=root, project_root=root, modified_tests=[], emit=events.append,
        cwd=str(root), cum=0.0, branch="feat/x", resume_cmd="R",
    )
    assert exit_code == 1
    assert [e for e in events if e["event"] == "verify"][0]["verdict"] == "refute"


# ---------------------------------------------------------------------------
# FEATURE 2 — test-integrity via git
# ---------------------------------------------------------------------------


def _mk_repo_with_tests(tmp_path, story_key="2-1"):
    root = tmp_path / "proj"
    art = root / "_bmad-output" / "implementation-artifacts"
    art.mkdir(parents=True)
    (art / "sprint-status.yaml").write_text(f"development_status:\n  epic-2: in-progress\n  {story_key}: done\n", encoding="utf-8")
    src = root / "src"
    src.mkdir()
    (src / "foo.test.ts").write_text("test('a', () => {})\n", encoding="utf-8")
    (src / "bar.spec.js").write_text("it('b', () => {})\n", encoding="utf-8")
    (src / "app.ts").write_text("export const x = 1\n", encoding="utf-8")
    _git(root, "init")
    _git(root, "config", "user.email", "t@t.t")
    _git(root, "config", "user.name", "t")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "base")
    base = _head(root)
    (art / f"{story_key}.md").write_text(
        f"# S\n\nStatus: done\n\n## Acceptance Criteria\n1. x\n\nbaseline_commit: {base}\n",
        encoding="utf-8",
    )
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "story")
    return root, base


def test_test_integrity_deletion_halts(tmp_path):
    root, base = _mk_repo_with_tests(tmp_path)
    (root / "src" / "foo.test.ts").unlink()
    (root / "src" / "bar.spec.js").write_text("it('b', () => { /* weakened */ })\n", encoding="utf-8")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "tamper")
    events: list[dict] = []
    cfg = driver.BmadConfig(project_root=str(root))
    exit_code, modified = driver._run_test_integrity(
        cfg, _story("2-1"), repo=root, project_root=root, emit=events.append, cum=0.0, resume_cmd="R",
    )
    assert exit_code == 1  # deletion halts by default
    ti = [e for e in events if e["event"] == "test-integrity"][0]
    assert ti["deleted"] == ["src/foo.test.ts"]
    assert ti["modified"] == ["src/bar.spec.js"]
    assert ti["ok"] is False
    stop = [e for e in events if e["event"] == "stop"][-1]
    assert "DELETED pre-existing test" in stop["reason"]


def test_test_integrity_deletion_warns_when_halt_disabled(tmp_path):
    root, base = _mk_repo_with_tests(tmp_path)
    (root / "src" / "foo.test.ts").unlink()
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "delete test")
    events: list[dict] = []
    cfg = driver.BmadConfig(project_root=str(root), test_integrity_halt_on_deletion=False)
    exit_code, modified = driver._run_test_integrity(
        cfg, _story("2-1"), repo=root, project_root=root, emit=events.append, cum=0.0, resume_cmd="R",
    )
    assert exit_code is None  # warn-and-proceed
    ti = [e for e in events if e["event"] == "test-integrity"][0]
    assert ti["deleted"] == ["src/foo.test.ts"]
    assert ti["ok"] is False
    # no terminal stop was emitted (proceeds)
    assert not [e for e in events if e["event"] == "stop"]


def test_test_integrity_modified_only_is_not_a_halt(tmp_path):
    root, base = _mk_repo_with_tests(tmp_path)
    (root / "src" / "bar.spec.js").write_text("it('b', () => { /* edited */ })\n", encoding="utf-8")
    (root / "src" / "app.ts").write_text("export const x = 2\n", encoding="utf-8")  # non-test edit
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "edit")
    events: list[dict] = []
    cfg = driver.BmadConfig(project_root=str(root))
    exit_code, modified = driver._run_test_integrity(
        cfg, _story("2-1"), repo=root, project_root=root, emit=events.append, cum=0.0, resume_cmd="R",
    )
    assert exit_code is None
    assert modified == ["src/bar.spec.js"]  # app.ts is not a test file -> excluded
    ti = [e for e in events if e["event"] == "test-integrity"][0]
    assert ti["ok"] is True
    assert ti["deleted"] == []


def test_test_integrity_no_baseline_skips_silently(tmp_path):
    root, base = _init_project(tmp_path, ONE_READY, with_baseline=False)
    events: list[dict] = []
    cfg = driver.BmadConfig(project_root=str(root))
    exit_code, modified = driver._run_test_integrity(
        cfg, _story("2-1-capture"), repo=root, project_root=root, emit=events.append, cum=0.0, resume_cmd="R",
    )
    assert exit_code is None and modified == []
    assert events == []  # no baseline -> emit nothing


def test_test_globs_match_excludes_node_modules():
    globs = ["**/*.test.*", "**/*.spec.*"]
    assert driver._test_globs_match("src/foo.test.ts", globs) is True
    assert driver._test_globs_match("a/b/c.spec.jsx", globs) is True
    assert driver._test_globs_match("foo.test.ts", globs) is True  # top-level
    assert driver._test_globs_match("src/app.ts", globs) is False
    assert driver._test_globs_match("node_modules/x/y.test.ts", globs) is False


# ---------------------------------------------------------------------------
# FEATURE 3 — plan-gate before dev
# ---------------------------------------------------------------------------


def test_plan_gate_blocked_halts():
    events: list[dict] = []
    runner = RecordingRunner([AgentResult(raw="", text="BLOCKED: AC1 and AC2 conflict; ambiguous")])
    resilient = _resilient(runner, events)
    cfg = driver.BmadConfig(project_root="/p")
    exit_code, _c = driver._run_plan_gate(
        cfg, resilient, _story(status="ready-for-dev"),
        "## Acceptance Criteria\n1. a\n\n## Tasks\n- t\n",
        emit=events.append, cwd="/p", cum=0.0, resume_cmd="R",
    )
    assert exit_code == 1
    pc = [e for e in events if e["event"] == "plan-check"][0]
    assert pc["verdict"] == "blocked"
    assert "conflict" in pc["reason"]
    stop = [e for e in events if e["event"] == "stop"][-1]
    assert stop["ok"] is False and "plan-gate BLOCKED" in stop["reason"]


def test_plan_gate_ok_proceeds():
    events: list[dict] = []
    runner = RecordingRunner([AgentResult(raw="", text="PLAN_OK")])
    resilient = _resilient(runner, events)
    exit_code, _c = driver._run_plan_gate(
        driver.BmadConfig(project_root="/p"), resilient, _story(status="ready-for-dev"),
        "## Acceptance Criteria\n1. a\n\n## Tasks\n- t\n",
        emit=events.append, cwd="/p", cum=0.0, resume_cmd="R",
    )
    assert exit_code is None
    assert [e for e in events if e["event"] == "plan-check"][0]["verdict"] == "ok"
    # single-turn, decider tier, plan (read-only) mode
    assert runner.calls[0]["max_turns"] == 1
    assert runner.calls[0]["model"] == "haiku"


def test_plan_gate_garbage_fails_open():
    events: list[dict] = []
    runner = RecordingRunner([AgentResult(raw="", text="Sure, this looks reasonable to me!")])
    resilient = _resilient(runner, events)
    exit_code, _c = driver._run_plan_gate(
        driver.BmadConfig(project_root="/p"), resilient, _story(status="ready-for-dev"),
        "## Acceptance Criteria\n1. a\n\n## Tasks\n- t\n",
        emit=events.append, cwd="/p", cum=0.0, resume_cmd="R",
    )
    assert exit_code is None  # unparseable -> proceed
    assert [e for e in events if e["event"] == "plan-check"][0]["verdict"] == "inconclusive"


def test_plan_gate_blocked_halts_end_to_end(tmp_path, monkeypatch):
    root, base = _init_project(tmp_path, ONE_READY)
    state = tmp_path / "state"
    pr_calls: dict = {}
    _patch_externals(monkeypatch, pr_calls)
    # plan-gate is the FIRST agent call and BLOCKS -> dev-story never runs, no PR.
    runner = MockRunner([AgentResult(raw="", text="BLOCKED: the story is really three stories")])
    rc = driver.run(_config(root, base, no_merge=True), runner=runner, state_dir=str(state))
    assert rc == 1
    kinds = [e["event"] for e in _events(state)]
    assert "plan-check" in kinds
    assert "dev-gate" not in kinds  # blocked before dev
    assert "pr-created" not in kinds
    assert len(runner.calls) == 1  # only the plan-gate ran


# ---------------------------------------------------------------------------
# FEATURE 4 — run-quality metrics
# ---------------------------------------------------------------------------


def test_metrics_event_emitted_before_stop_with_sane_aggregates(tmp_path, monkeypatch):
    root, base = _init_project(tmp_path, ONE_READY, with_baseline=True)
    state = tmp_path / "state"
    pr_calls: dict = {}
    _patch_externals(monkeypatch, pr_calls)
    runner = MockRunner([
        AgentResult(raw="", text="PLAN_OK", cost_usd=0.0),
        AgentResult(raw="", text="dev complete; status: review", cost_usd=1.0),
        AgentResult(raw="", text="REVIEW_COMPLETE: clean.", cost_usd=0.2),
        AgentResult(raw="", text="SMOKE_PASS: verified.", cost_usd=0.3),
        AgentResult(raw="", text="VERDICT: PASS", cost_usd=0.1),
    ])
    # --no-merge -> a clean terminal stop (ok=True) after the PR opens (a merged multi-story run
    # would re-select the same story on the next scan since the mock never flips sprint-status).
    rc = driver.run(_config(root, base, no_merge=True), runner=runner, state_dir=str(state))
    assert rc == 0
    evts = _events(state)
    kinds = [e["event"] for e in evts]
    # the metrics event lands immediately BEFORE the terminal bmad stop
    assert "metrics" in kinds
    assert kinds.index("metrics") == kinds.index("stop") - 1
    m = [e for e in evts if e["event"] == "metrics"][-1]
    assert m["storiesHalted"] == 0  # clean --no-merge stop
    assert m["planChecks"] == 1
    assert m["verifies"] == 1
    assert m["prsCreated"] == 1
    assert m["prsMerged"] == 0  # --no-merge
    assert m["devGates"] == 1
    assert m["durationSec"] >= 0.0


def test_metrics_event_on_a_halt_marks_stories_halted(tmp_path, monkeypatch):
    root, base = _init_project(tmp_path, ONE_READY, with_baseline=True)
    state = tmp_path / "state"
    pr_calls: dict = {}
    _patch_externals(monkeypatch, pr_calls)
    runner = MockRunner([
        AgentResult(raw="", text="PLAN_OK", cost_usd=0.0),
        AgentResult(raw="", text="dev complete; status: review", cost_usd=1.0),
        AgentResult(raw="", text="REVIEW_COMPLETE: clean.", cost_usd=0.2),
        AgentResult(raw="", text="SMOKE_PASS: verified.", cost_usd=0.3),
        AgentResult(raw="", text="VERDICT: REFUTE — AC1 not met", cost_usd=0.1),
    ])
    rc = driver.run(_config(root, base, no_merge=True), runner=runner, state_dir=str(state))
    assert rc == 1
    m = [e for e in _events(state) if e["event"] == "metrics"][-1]
    assert m["storiesHalted"] == 1
    assert m["storiesCompleted"] == 0


def test_metrics_disabled_emits_no_metrics_event(tmp_path, monkeypatch):
    root, base = _init_project(tmp_path, ONE_READY)
    state = tmp_path / "state"
    pr_calls: dict = {}
    _patch_externals(monkeypatch, pr_calls)
    runner = MockRunner([AgentResult(raw="", text="BLOCKED: stop here", cost_usd=0.0)])
    rc = driver.run(_config(root, base, no_merge=True, metrics_emit=False), runner=runner, state_dir=str(state))
    assert rc == 1
    assert "metrics" not in [e["event"] for e in _events(state)]


# ---------------------------------------------------------------------------
# FEATURE 5 — gate fail-fast threading
# ---------------------------------------------------------------------------


def test_gate_fail_fast_skips_test_stage_and_no_flaky_retry(tmp_path):
    test_ran = {"v": False}

    def test_cmd():
        test_ran["v"] = True
        return ("10 pass 0 fail", 0)

    stages = [
        {"name": "codegen", "command": lambda: ("", 0)},
        {"name": "lint", "command": lambda: ("lint error", 1)},
        {"name": "test", "command": test_cmd, "pass_pattern": r"(\d+)\s+pass", "fail_pattern": r"(\d+)\s+fail"},
    ]
    cfg = driver.BmadConfig(project_root=str(tmp_path), gate_stages=stages, gate_fail_fast=True)
    emitted: list[dict] = []
    g = driver._run_gate(cfg, tmp_path, emitted.append)
    assert g["green"] is False
    assert test_ran["v"] is False  # fail_fast short-circuited BEFORE the (build-heavy) test stage
    # a skipped test stage means gate-level fail==0 -> NOT flaky-shaped -> no wasted retry
    assert emitted == []
    test_stage = [s for s in g["stages"] if s["name"] == "test"][0]
    assert test_stage.get("skipped") is True


def test_gate_fail_fast_off_still_runs_test_stage(tmp_path):
    test_ran = {"v": False}

    def test_cmd():
        test_ran["v"] = True
        return ("10 pass 0 fail", 0)

    stages = [
        {"name": "codegen", "command": lambda: ("", 0)},
        {"name": "lint", "command": lambda: ("lint error", 1)},
        {"name": "test", "command": test_cmd, "pass_pattern": r"(\d+)\s+pass", "fail_pattern": r"(\d+)\s+fail"},
    ]
    cfg = driver.BmadConfig(project_root=str(tmp_path), gate_stages=stages, gate_fail_fast=False, gate_flaky_retries=0)
    driver._run_gate(cfg, tmp_path, [].append)
    assert test_ran["v"] is True  # default: every stage runs


# ---------------------------------------------------------------------------
# config parsing (camel + snake + defaults + unknown-key quiet) + resume round-trip
# ---------------------------------------------------------------------------


def test_wave2_config_defaults():
    cfg = driver.BmadConfig(project_root="/p")
    assert cfg.verify_enabled is True
    assert cfg.verify_model == "haiku"
    assert cfg.verify_effort == "low"
    assert cfg.verify_timeout_min == 10
    assert cfg.test_integrity_enabled is True
    assert cfg.test_integrity_globs == ["**/*.test.*", "**/*.spec.*"]
    assert cfg.test_integrity_halt_on_deletion is True
    assert cfg.plan_gate_enabled is True
    assert cfg.metrics_emit is True
    assert cfg.gate_fail_fast is False


def test_wave2_config_from_loop_json_camel_case(capsys):
    cfg = driver.BmadConfig.from_loop_json({
        "bmad": {
            "projectRoot": "/p",
            "verify": {"enabled": False, "model": "sonnet", "effort": "high", "timeoutMin": 5},
            "testIntegrity": {"enabled": False, "globs": ["**/*.t.ts"], "haltOnDeletion": False},
            "planGate": {"enabled": False},
            "metricsEmit": False,
            "gateFailFast": True,
        }
    })
    assert cfg.verify_enabled is False
    assert cfg.verify_model == "sonnet"
    assert cfg.verify_effort == "high"
    assert cfg.verify_timeout_min == 5
    assert cfg.test_integrity_enabled is False
    assert cfg.test_integrity_globs == ["**/*.t.ts"]
    assert cfg.test_integrity_halt_on_deletion is False
    assert cfg.plan_gate_enabled is False
    assert cfg.metrics_emit is False
    assert cfg.gate_fail_fast is True
    # every new key is KNOWN -> no unrecognized-key warning on stderr
    assert capsys.readouterr().err == ""


def test_wave2_config_from_loop_json_snake_case(capsys):
    cfg = driver.BmadConfig.from_loop_json({
        "bmad": {
            "project_root": "/p",
            "verify": {"timeout_min": 3},
            "test_integrity": {"halt_on_deletion": False},
            "plan_gate": {"enabled": False},
            "metrics_emit": False,
            "gate_fail_fast": True,
        }
    })
    assert cfg.verify_timeout_min == 3
    assert cfg.test_integrity_halt_on_deletion is False
    assert cfg.plan_gate_enabled is False
    assert cfg.metrics_emit is False
    assert cfg.gate_fail_fast is True
    assert capsys.readouterr().err == ""


def test_wave2_resume_command_round_trips_disabler_flags():
    cfg = driver.BmadConfig(project_root="/p", verify_enabled=False, plan_gate_enabled=False)
    cmd = driver._resume_command(cfg, "/state")
    assert "--no-verify" in cmd
    assert "--no-plan-gate" in cmd
    # defaults (both ON) stay OFF the resume command
    default = driver._resume_command(driver.BmadConfig(project_root="/p"), "/state")
    assert "--no-verify" not in default
    assert "--no-plan-gate" not in default


def test_from_args_maps_disabler_flags():
    on = driver.BmadConfig.from_args(SimpleNamespace(project_root="/p"))
    assert on.verify_enabled is True and on.plan_gate_enabled is True
    off = driver.BmadConfig.from_args(
        SimpleNamespace(project_root="/p", no_verify=True, no_plan_gate=True)
    )
    assert off.verify_enabled is False and off.plan_gate_enabled is False


# ---------------------------------------------------------------------------
# CLI flags --no-verify / --no-plan-gate
# ---------------------------------------------------------------------------


def test_cli_no_verify_no_plan_gate_flags(tmp_path, monkeypatch):
    captured: dict = {}

    def fake_run(config, *, runner, state_dir, cwd=None):
        captured["config"] = config
        return 0

    monkeypatch.setattr(driver, "run", fake_run)
    monkeypatch.setattr(cli, "get_runner", lambda name: object())
    rc = cli.main_bmad(["--project-root", str(tmp_path), "--no-verify", "--no-plan-gate"])
    assert rc == 0
    assert captured["config"].verify_enabled is False
    assert captured["config"].plan_gate_enabled is False


def test_cli_defaults_keep_wave2_gates_on(tmp_path, monkeypatch):
    captured: dict = {}
    monkeypatch.setattr(driver, "run", lambda config, **k: (captured.__setitem__("config", config), 0)[1])
    monkeypatch.setattr(cli, "get_runner", lambda name: object())
    rc = cli.main_bmad(["--project-root", str(tmp_path)])
    assert rc == 0
    assert captured["config"].verify_enabled is True
    assert captured["config"].plan_gate_enabled is True
