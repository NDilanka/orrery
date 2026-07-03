"""Coverage for ``loop.qa.discover`` — the QA discovery orchestration.

Pure ``story_gate`` math, plus a full ``run()`` with the agent invocation injected: a fake
invoker parses the findings path out of the prompt (testing the real contract — "write to
this absolute path") and writes a verdict file, exactly as the real agent would. No browser,
no ``claude`` spawned.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

from conftest import REPO_ROOT

from loop import lockfile
from loop.qa.discover import (
    InvokeResult,
    QaConfig,
    default_invoke,
    findings_to_events,
    run,
    story_gate,
)
from loop.runners.base import AgentResult


def test_story_gate_counts_only_observable():
    g = story_gate(
        [{"status": "pass"}, {"status": "fail"}, {"status": "not-observable"}, {"status": "partial"}]
    )
    assert g == {
        "pass": 1,
        "fail": 2,
        "total": 3,
        "green": False,
        "failingCriteria": ["?", "?"],
    }
    # All observable ACs met -> green; not-observable drops out of the denominator.
    g2 = story_gate([{"status": "pass", "ac": "AC1"}, {"status": "not-observable", "ac": "AC2"}])
    assert g2["green"] and g2["total"] == 1 and g2["pass"] == 1
    # No observable ACs at all -> not green (nothing was actually verified).
    assert story_gate([{"status": "not-observable"}])["green"] is False


def test_findings_to_events_shapes():
    findings = {
        "epic": 2,
        "verdicts": [
            {"story": "2.1", "ac": "AC1", "status": "pass", "evidence": "ok"},
            {"story": "2.1", "ac": "AC2", "status": "fail", "evidence": "missing send button"},
        ],
    }
    events = findings_to_events(2, findings, cum=1.25, iter_index=1)
    kinds = [e["event"] for e in events]
    assert kinds.count("verdict") == 1 and "gate" in kinds and "iter" in kinds
    verdict = next(e for e in events if e["event"] == "verdict")
    assert verdict["item"] == "2.1" and verdict["pass"] is False
    assert verdict["failingCriteria"] == ["AC2"]
    gate = next(e for e in events if e["event"] == "gate")
    assert gate["pass"] == 1 and gate["fail"] == 1 and gate["total"] == 2 and gate["green"] is False


def _manifest() -> dict:
    return {
        "app": "x",
        "epics": [
            {
                "epic": 2,
                "storyCount": 1,
                "acCount": 2,
                "stories": [
                    {
                        "id": "2.1",
                        "title": "Capture",
                        "status": "done",
                        "file": "f",
                        "acCount": 2,
                        "criteria": [{"id": "AC1", "title": "a"}, {"id": "AC2", "title": "b"}],
                        "acMarkdown": "**AC1 — a**\n**AC2 — b**",
                    }
                ],
            }
        ],
    }


def test_run_full_pass_with_fake_agent(tmp_path):
    mpath = tmp_path / "ac-manifest.json"
    mpath.write_text(json.dumps(_manifest()), encoding="utf-8")
    state = tmp_path / "state"
    cfg = QaConfig(project_root=str(tmp_path), manifest_path=str(mpath), app="webapp")

    events: list[dict] = []

    def fake_invoke(prompt: str, *, timeout_sec: int) -> InvokeResult:
        # The agent is told to write the findings JSON to an absolute path in backticks.
        m = re.search(r"absolute path\s*`([^`]+)`", prompt)
        assert m, "prompt must name the findings path"
        fp = Path(m.group(1))
        fp.parent.mkdir(parents=True, exist_ok=True)
        fp.write_text(
            json.dumps(
                {
                    "epic": 2,
                    "summary": "capture works; send button missing on mobile",
                    "specFile": "e2e/functional/epic-2.func.e2e.ts",
                    "verdicts": [
                        {"story": "2.1", "ac": "AC1", "status": "pass", "evidence": "textarea present",
                         "severity": "none"},
                        {"story": "2.1", "ac": "AC2", "status": "fail", "evidence": "no send button",
                         "severity": "high", "repro": "open /capture on mobile"},
                    ],
                }
            ),
            encoding="utf-8",
        )
        return InvokeResult(raw="{}", text="done", cost_usd=0.5)

    rc = run(cfg, state_dir=str(state), invoke=fake_invoke, emit=events.append)
    assert rc == 0

    kinds = [e["event"] for e in events]
    for required in ("engine-start", "start", "model", "verdict", "gate", "iter", "stop"):
        assert required in kinds, f"missing {required}"

    # Aggregated artifacts written.
    assert (state / "findings.json").exists()
    report = (state / "report.md").read_text(encoding="utf-8")
    assert "Epic 2" in report and "send button" in report

    # Cost accumulated into the stop event; epic not green (one failing AC).
    stop = next(e for e in events if e["event"] == "stop")
    assert stop["cum"] == 0.5 and stop["green"] is False
    verdict = next(e for e in events if e["event"] == "verdict")
    assert verdict["item"] == "2.1" and verdict["pass"] is False


def test_run_blocks_epic_when_agent_writes_nothing(tmp_path):
    mpath = tmp_path / "ac-manifest.json"
    mpath.write_text(json.dumps(_manifest()), encoding="utf-8")
    state = tmp_path / "state"
    cfg = QaConfig(project_root=str(tmp_path), manifest_path=str(mpath), app="webapp")

    events: list[dict] = []

    def silent_invoke(prompt: str, *, timeout_sec: int) -> InvokeResult:
        return InvokeResult(raw="", text="", cost_usd=0.0, is_error=True, timed_out=True)

    run(cfg, state_dir=str(state), invoke=silent_invoke, emit=events.append)
    # Every AC is recorded blocked rather than silently passing.
    acs = [e for e in events if e["event"] == "qa-ac"]
    assert acs and all(e["status"] == "blocked" for e in acs)
    verdict = next(e for e in events if e["event"] == "verdict")
    assert verdict["pass"] is False


def test_default_invoke_routes_through_claude_and_resilient_runner(monkeypatch):
    """Task 1: the DEFAULT QA invoke path now goes through ClaudeRunner (real argv) wrapped in
    ResilientRunner (quota survival / raw capture / token telemetry), NOT a hand-rolled
    subprocess block. It forwards QA's model/effort/mcp routing and maps AgentResult ->
    InvokeResult, and the shared runner emits per-call token telemetry for free."""
    calls: dict = {}

    class FakeBase:
        name = "fake"
        supports_quota_probe = False
        supports_sessions = False
        supports_cache_telemetry = False

        def run(self, **kwargs):
            calls.update(kwargs)
            return AgentResult(
                raw="RAW", text="hi", cost_usd=0.4, usage={"input_tokens": 5, "output_tokens": 2}
            )

    monkeypatch.setattr("loop.qa.discover.ClaudeRunner", lambda: FakeBase())
    cfg = QaConfig(
        project_root="/p", manifest_path="m", model="opus", effort="high", max_turns=99
    )
    events: list[dict] = []
    inv = default_invoke(
        cfg, "/tmp/mcp.json", emit=events.append, phase="qa-epic-2", story="epic-2"
    )
    res = inv("PROMPT", timeout_sec=123)

    assert isinstance(res, InvokeResult)
    assert res.raw == "RAW" and res.text == "hi" and res.cost_usd == 0.4
    assert res.is_error is False and res.timed_out is False
    # QA's config routing (model/effort/max_turns) + the per-run mcp config are forwarded.
    assert calls["model"] == "opus" and calls["effort"] == "high" and calls["max_turns"] == 99
    assert calls["prompt"] == "PROMPT" and calls["timeout_sec"] == 123
    assert calls["mcp_config"] == "/tmp/mcp.json" and calls["strict_mcp_config"] is True
    assert list(calls["allowed_tools"]) == list(cfg.allowed_tools)
    # token telemetry rode along, tagged with the epic phase/story.
    tu = [e for e in events if e.get("event") == "token-usage"]
    assert tu and tu[0]["phase"] == "qa-epic-2" and tu[0]["story"] == "epic-2"


def test_run_takes_the_shared_lock_and_refuses_when_live(tmp_path, monkeypatch):
    """Task 4: loop.qa.discover.run() now acquires the SAME shared lock as loop/loop-bmad, so
    it can't race a generic/BMAD run (or another QA run) against the same state dir."""
    mpath = tmp_path / "ac-manifest.json"
    mpath.write_text(json.dumps(_manifest()), encoding="utf-8")
    state = tmp_path / "state"
    state.mkdir(parents=True, exist_ok=True)
    cfg = QaConfig(project_root=str(tmp_path), manifest_path=str(mpath), app="webapp")

    other_pid = os.getpid() + 1
    (state / lockfile.LOCK_NAME).write_text(str(other_pid), encoding="utf-8")
    monkeypatch.setattr(lockfile, "pid_alive", lambda pid: True)

    def boom_invoke(prompt: str, *, timeout_sec: int):  # pragma: no cover - must never run
        raise AssertionError("invoke must not be called when the lock is refused")

    rc = run(cfg, state_dir=str(state), invoke=boom_invoke, emit=lambda e: None)
    assert rc == 2
    # the live lock is untouched
    assert (state / lockfile.LOCK_NAME).read_text(encoding="utf-8").strip() == str(other_pid)


def test_run_acquires_and_releases_the_lock_on_a_clean_run(tmp_path):
    mpath = tmp_path / "ac-manifest.json"
    mpath.write_text(json.dumps(_manifest()), encoding="utf-8")
    state = tmp_path / "state"
    cfg = QaConfig(project_root=str(tmp_path), manifest_path=str(mpath), app="webapp")

    def silent_invoke(prompt: str, *, timeout_sec: int) -> InvokeResult:
        return InvokeResult(raw="", text="", cost_usd=0.0, is_error=True, timed_out=True)

    rc = run(cfg, state_dir=str(state), invoke=silent_invoke, emit=lambda e: None)
    assert rc == 0
    # the lock is released once the run finishes (no lingering lockfile)
    assert not (state / lockfile.LOCK_NAME).exists()


# ---------------------------------------------------------------------------
# Task 4 — the folded webapp-qa/loop.json single-file seed (+ the deprecated but still-working
# qa-engine.json) both parse cleanly through QaConfig.from_loop_json.
# ---------------------------------------------------------------------------


def test_seed_webapp_qa_loop_json_parses_with_namespaced_block(capsys):
    data = json.loads(
        (REPO_ROOT / "orrery/loops/webapp-qa/loop.json").read_text(encoding="utf-8")
    )
    cfg = QaConfig.from_loop_json(data, project_root="/p")
    assert cfg.app == "your-webapp"
    assert cfg.effort == "high"
    assert cfg.cost_ceiling_usd == 30
    assert cfg.headless is True
    # orrery-side top-level keys (id/name/start/stateDir/...) are outside the "qa" block -> no
    # unknown-key noise (only the namespaced block is ever inspected).
    assert capsys.readouterr().err == ""


def test_seed_qa_engine_json_still_parses_deprecated_but_working():
    data = json.loads(
        (REPO_ROOT / "orrery/loops/webapp-qa/qa-engine.json").read_text(encoding="utf-8")
    )
    cfg = QaConfig.from_loop_json(data, project_root="/p")
    assert cfg.app == "your-webapp"
    assert cfg.effort == "high"


def test_seed_webapp_qa_loop_json_intra_loop_paths_are_relative():
    """A4 Task 3: stateDir/stopFlag/checkpoint + the --state-dir/--manifest/--loop-json args are
    RELATIVE (portable); --project-root (the external app repo) stays absolute, and
    qa.storageState stays absolute too — it's consumed by a Playwright subprocess whose cwd is
    `project_root`, a DIFFERENT directory than this loop's own dir, so a relative value there
    would resolve against the wrong repo."""
    data = json.loads(
        (REPO_ROOT / "orrery/loops/webapp-qa/loop.json").read_text(encoding="utf-8")
    )
    assert data["stateDir"] == ".loop"
    assert data["stopFlag"] == ".loop/STOP"
    assert data["checkpoint"] == ".loop/checkpoint.json"
    args = data["start"]["args"]
    assert args[args.index("--state-dir") + 1] == ".loop"
    assert args[args.index("--manifest") + 1] == "ac-manifest.json"
    assert args[args.index("--loop-json") + 1] == "loop.json"
    assert Path(args[args.index("--project-root") + 1]).is_absolute()
    assert Path(data["qa"]["storageState"]).is_absolute()
    assert data["qa"]["storageState"].endswith("/.auth/storage-state.json")
