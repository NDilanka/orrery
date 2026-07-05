"""Wave-4 new-CLI-capability adoption — all knobs default-off / experimental.

Covers, without ever spawning a real ``claude``:

- TASK A: ``--fallback-model`` argv wiring on ClaudeRunner (present when set, omitted when empty),
  aider/codex accept-and-ignore it, the three config surfaces parse ``fallbackModel``, and the
  ResilientRunner injects it into wrapped calls only when configured.
- TASK B: ``--json-schema`` argv + ``structured_output`` parsing into ``AgentResult.structured``;
  the structured-verdict resolver; and the BMAD verify / plan-gate preferring a valid structured
  verdict while falling back to the free-text parse (fail-open polarity preserved).
- TASK C: the experimental in-session gate — config parse (camel+snake, default off), the
  stop-hook settings-file content, the /goal prompt prefix, ``--settings`` argv, and an OFF-mode
  byte-identical argv+prompt parity regression.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from orrery_loop import core
from orrery_loop.bmad import driver
from orrery_loop.bmad.sprint import Story
from orrery_loop.config import CostConfig, EngineConfig, GateConfig, GateStage, StopConfig, from_loop_json
from orrery_loop.core import run_loop
from orrery_loop.qa.discover import QaConfig
from orrery_loop.resilient import ResilientRunner
from orrery_loop.runners import get_runner
from orrery_loop.runners.base import AgentResult, AgentRunner


# ---------------------------------------------------------------------------
# doubles
# ---------------------------------------------------------------------------


@dataclass
class FakeProcResult:
    returncode: int = 0
    stdout: str = ""
    stderr: str = ""
    timed_out: bool = False


class FakeProc:
    def __init__(self, result: FakeProcResult):
        self.result = result
        self.calls: list[dict] = []

    def run_with_timeout(self, argv, *, cwd=None, timeout_sec=0, env=None):
        self.calls.append({"argv": list(argv), "cwd": cwd, "timeout_sec": timeout_sec})
        return self.result


def _patch_proc(monkeypatch, result: FakeProcResult) -> FakeProc:
    fake = FakeProc(result)
    monkeypatch.setattr("orrery_loop.runners.claude.proc", fake)
    return fake


class RecordingRunner(AgentRunner):
    """Records every run() kwargs; returns a canned success result."""

    name = "recording"
    supports_quota_probe = False
    supports_sessions = False

    def __init__(self, result: AgentResult | None = None):
        self._result = result or AgentResult(raw="{}", text="ok")
        self.calls: list[dict] = []

    def run(self, **kwargs) -> AgentResult:  # type: ignore[override]
        self.calls.append(dict(kwargs))
        return self._result


class FakeResilient:
    """Stand-in for the ResilientRunner the BMAD verify/plan-gate call (records kwargs)."""

    def __init__(self, result: AgentResult):
        self._result = result
        self.calls: list[dict] = []

    def set_context(self, *a, **k):
        pass

    def run(self, **kwargs) -> AgentResult:
        self.calls.append(dict(kwargs))
        return self._result


def _story(key: str = "1-1") -> Story:
    return Story(key=key, status="ready", raw_status="ready-for-dev", epic="1", index=0)


_STORY_TEXT = "## Acceptance Criteria\n- AC1: it works\n\n## Tasks\n- do the thing\n"


# ===========================================================================
# TASK A — --fallback-model
# ===========================================================================


def test_claude_argv_includes_fallback_model_when_set(monkeypatch):
    fake = _patch_proc(monkeypatch, FakeProcResult(stdout=json.dumps({"is_error": False})))
    get_runner("claude").run(
        prompt="P", model="m", allowed_tools=["Read"], permission_mode="plan",
        max_turns=1, cwd=".", fallback_model="sonnet,haiku",
    )
    argv = fake.calls[0]["argv"]
    assert argv[argv.index("--fallback-model") + 1] == "sonnet,haiku"


def test_claude_argv_omits_fallback_model_when_empty(monkeypatch):
    fake = _patch_proc(monkeypatch, FakeProcResult(stdout=json.dumps({"is_error": False})))
    get_runner("claude").run(
        prompt="P", model="m", allowed_tools=["Read"], permission_mode="plan",
        max_turns=1, cwd=".",
    )
    assert "--fallback-model" not in fake.calls[0]["argv"]


def test_aider_and_codex_accept_and_ignore_fallback_model(monkeypatch):
    for name in ("aider", "codex"):
        fake = FakeProc(FakeProcResult(stdout="done", returncode=0))
        monkeypatch.setattr(f"orrery_loop.runners.{name}.proc", fake)
        # Must not raise; the flag never appears in the (non-claude) argv.
        res = get_runner(name).run(
            prompt="P", model="sonnet", allowed_tools=[], permission_mode="plan",
            max_turns=1, cwd=".", fallback_model="sonnet,haiku",
            json_schema='{"x":1}', settings="s.json",
        )
        assert "--fallback-model" not in fake.calls[0]["argv"]
        assert res.is_error is False


def test_engine_config_parses_fallback_model_both_spellings():
    assert from_loop_json({"engine": {"fallbackModel": "sonnet,haiku"}}).fallback_model == "sonnet,haiku"
    assert from_loop_json({"engine": {"fallback_model": "opus"}}).fallback_model == "opus"
    assert EngineConfig().fallback_model == ""  # default


def test_bmad_config_parses_fallback_model_both_spellings():
    c1 = driver.BmadConfig.from_loop_json({"bmad": {"project_root": "r", "fallbackModel": "sonnet,haiku"}})
    assert c1.fallback_model == "sonnet,haiku"
    c2 = driver.BmadConfig.from_loop_json({"bmad": {"project_root": "r", "fallback_model": "opus"}})
    assert c2.fallback_model == "opus"
    assert driver.BmadConfig(project_root="r").fallback_model == ""


def test_qa_config_parses_fallback_model():
    q = QaConfig.from_loop_json({"qa": {"projectRoot": "r", "manifest": "m.json", "fallbackModel": "sonnet,haiku"}})
    assert q.fallback_model == "sonnet,haiku"
    assert QaConfig(project_root="r", manifest_path="m.json").fallback_model == ""


def test_resilient_injects_fallback_model_only_when_set():
    base_on = RecordingRunner()
    ResilientRunner(base_on, emit=lambda e: None, fallback_model="sonnet,haiku").run(
        prompt="p", model="m", allowed_tools=[], permission_mode="plan", max_turns=1, cwd=None
    )
    assert base_on.calls[0]["fallback_model"] == "sonnet,haiku"

    base_off = RecordingRunner()
    ResilientRunner(base_off, emit=lambda e: None).run(
        prompt="p", model="m", allowed_tools=[], permission_mode="plan", max_turns=1, cwd=None
    )
    assert "fallback_model" not in base_off.calls[0]  # parity: nothing injected when unset


# ===========================================================================
# TASK B — --json-schema / structured verdicts
# ===========================================================================


def test_claude_argv_includes_json_schema_and_parses_structured(monkeypatch):
    schema = '{"type":"object","properties":{"verdict":{"enum":["PASS","REFUTE"]}}}'
    canned = {"is_error": False, "result": "text body", "structured_output": {"verdict": "PASS"}}
    fake = _patch_proc(monkeypatch, FakeProcResult(stdout=json.dumps(canned)))
    res = get_runner("claude").run(
        prompt="P", model="m", allowed_tools=[], permission_mode="plan",
        max_turns=1, cwd=".", json_schema=schema,
    )
    argv = fake.calls[0]["argv"]
    assert argv[argv.index("--json-schema") + 1] == schema
    assert res.structured == {"verdict": "PASS"}


def test_claude_structured_none_when_absent_or_nonobject(monkeypatch):
    _patch_proc(monkeypatch, FakeProcResult(stdout=json.dumps({"is_error": False, "result": "x"})))
    assert get_runner("claude").run(
        prompt="P", model="m", allowed_tools=[], permission_mode="plan", max_turns=1, cwd="."
    ).structured is None
    _patch_proc(monkeypatch, FakeProcResult(
        stdout=json.dumps({"is_error": False, "structured_output": ["not", "a", "dict"]})
    ))
    assert get_runner("claude").run(
        prompt="P", model="m", allowed_tools=[], permission_mode="plan", max_turns=1, cwd="."
    ).structured is None


def test_structured_verdict_resolver():
    ok = AgentResult(raw="", structured={"verdict": "refute", "reason": "AC1 unmet"})
    assert driver._structured_verdict(ok, {"PASS", "REFUTE"}) == ("REFUTE", "AC1 unmet")
    # verdict not in the allowed set -> None (fall back to text)
    assert driver._structured_verdict(
        AgentResult(raw="", structured={"verdict": "MAYBE"}), {"PASS", "REFUTE"}
    ) is None
    # no structured field -> None
    assert driver._structured_verdict(AgentResult(raw=""), {"PASS", "REFUTE"}) is None


def test_bmad_config_parses_structured_verdicts():
    c = driver.BmadConfig.from_loop_json({"bmad": {"project_root": "r", "structuredVerdicts": True}})
    assert c.structured_verdicts is True
    assert driver.BmadConfig(project_root="r").structured_verdicts is False


def test_plan_gate_structured_blocked_halts():
    config = driver.BmadConfig(project_root=".", structured_verdicts=True)
    res = AgentResult(raw="", text="", structured={"verdict": "BLOCKED", "reason": "ambiguous ACs"})
    fake = FakeResilient(res)
    events: list[dict] = []
    exit_, _ = driver._run_plan_gate(
        config, fake, _story(), _STORY_TEXT,
        emit=events.append, cwd=".", cum=0.0, resume_cmd="RESUME",
    )
    assert exit_ == 1  # BLOCKED halts
    assert fake.calls[0]["json_schema"] == driver._PLAN_GATE_SCHEMA
    pc = [e for e in events if e.get("event") == "plan-check"][0]
    assert pc["verdict"] == "blocked" and "ambiguous ACs" in pc["reason"]


def test_plan_gate_structured_ok_proceeds():
    config = driver.BmadConfig(project_root=".", structured_verdicts=True)
    fake = FakeResilient(AgentResult(raw="", text="", structured={"verdict": "PLAN_OK"}))
    events: list[dict] = []
    exit_, _ = driver._run_plan_gate(
        config, fake, _story(), _STORY_TEXT, emit=events.append, cwd=".", cum=0.0, resume_cmd="R"
    )
    assert exit_ is None  # proceeds
    assert [e for e in events if e.get("event") == "plan-check"][0]["verdict"] == "ok"


def test_plan_gate_falls_back_to_text_when_structured_absent():
    # structured ON but the result carries no structured field -> use the free-text BLOCKED parse.
    config = driver.BmadConfig(project_root=".", structured_verdicts=True)
    fake = FakeResilient(AgentResult(raw="", text="BLOCKED: too big for one story"))
    events: list[dict] = []
    exit_, _ = driver._run_plan_gate(
        config, fake, _story(), _STORY_TEXT, emit=events.append, cwd=".", cum=0.0, resume_cmd="R"
    )
    assert exit_ == 1  # text BLOCKED still halts (fail-open polarity preserved)


def test_plan_gate_off_sends_no_json_schema():
    config = driver.BmadConfig(project_root=".", structured_verdicts=False)
    fake = FakeResilient(AgentResult(raw="", text="PLAN_OK"))
    driver._run_plan_gate(
        config, fake, _story(), _STORY_TEXT, emit=lambda e: None, cwd=".", cum=0.0, resume_cmd="R"
    )
    assert "json_schema" not in fake.calls[0]  # OFF -> zero argv change


def test_verify_structured_refute_halts_and_sends_schema(monkeypatch):
    config = driver.BmadConfig(project_root=".", structured_verdicts=True)
    # Stub the git/story helpers so _run_verify needs no real repo.
    monkeypatch.setattr(driver, "_story_text", lambda root, key: "")
    monkeypatch.setattr(driver, "story_meta", lambda text: {"baseline": "deadbeef"})
    monkeypatch.setattr(driver, "story_acs", lambda text: "- AC1")
    monkeypatch.setattr(driver, "_story_diff", lambda repo, base: "diff body")
    fake = FakeResilient(AgentResult(raw="", text="", structured={"verdict": "REFUTE", "reason": "AC1 violated"}))
    events: list[dict] = []
    exit_, _ = driver._run_verify(
        config, fake, _story(), _STORY_TEXT,
        repo=Path("."), project_root=Path("."), modified_tests=[],
        emit=events.append, cwd=".", cum=0.0, branch="feat/x", resume_cmd="R",
    )
    assert exit_ == 1
    assert fake.calls[0]["json_schema"] == driver._VERIFY_SCHEMA
    assert [e for e in events if e.get("event") == "verify"][0]["verdict"] == "refute"


def test_verify_falls_back_to_text_when_structured_absent(monkeypatch):
    config = driver.BmadConfig(project_root=".", structured_verdicts=True)
    monkeypatch.setattr(driver, "_story_text", lambda root, key: "")
    monkeypatch.setattr(driver, "story_meta", lambda text: {"baseline": "deadbeef"})
    monkeypatch.setattr(driver, "story_acs", lambda text: "- AC1")
    monkeypatch.setattr(driver, "_story_diff", lambda repo, base: "diff body")
    fake = FakeResilient(AgentResult(raw="", text="VERDICT: REFUTE — AC1 broken"))
    events: list[dict] = []
    exit_, _ = driver._run_verify(
        config, fake, _story(), _STORY_TEXT,
        repo=Path("."), project_root=Path("."), modified_tests=[],
        emit=events.append, cwd=".", cum=0.0, branch="feat/x", resume_cmd="R",
    )
    assert exit_ == 1  # text VERDICT: REFUTE still halts


# ===========================================================================
# TASK C — experimental in-session gate
# ===========================================================================


def test_session_gate_config_parse_camel_snake_default_off():
    assert EngineConfig().session_gate == "off"  # default
    assert from_loop_json({"engine": {"sessionGate": "stop-hook"}}).session_gate == "stop-hook"
    assert from_loop_json({"engine": {"session_gate": "goal"}}).session_gate == "goal"


def test_session_gate_settings_json_content():
    s = core._session_gate_settings_json("bun run test")
    hook = s["hooks"]["Stop"][0]["hooks"][0]
    assert hook["type"] == "command"
    # A failing gate is coerced to the blocking exit-2; the gate command is embedded verbatim.
    assert hook["command"] == "bun run test || exit 2"


def test_session_gate_setup_modes(tmp_path):
    state = tmp_path
    off = EngineConfig(session_gate="off")
    assert core._session_gate_setup(off, state, "bun run test") == ("", "")
    # A callable (non-string) gate command has no shell form -> stays off.
    assert core._session_gate_setup(EngineConfig(session_gate="stop-hook"), state, "") == ("", "")

    hook_cfg = EngineConfig(session_gate="stop-hook")
    settings_path, goal_prefix = core._session_gate_setup(hook_cfg, state, "bun run test")
    assert settings_path == str(state / "session-gate-settings.json")
    assert goal_prefix == ""
    written = json.loads(Path(settings_path).read_text(encoding="utf-8"))
    assert written["hooks"]["Stop"][0]["hooks"][0]["command"] == "bun run test || exit 2"

    goal_cfg = EngineConfig(session_gate="goal")
    settings_path, goal_prefix = core._session_gate_setup(goal_cfg, state, "bun run test")
    assert settings_path == ""
    assert goal_prefix.startswith("/goal the command `bun run test` exits 0")


def test_claude_argv_includes_settings_when_set(monkeypatch):
    fake = _patch_proc(monkeypatch, FakeProcResult(stdout=json.dumps({"is_error": False})))
    get_runner("claude").run(
        prompt="P", model="m", allowed_tools=["Read"], permission_mode="plan",
        max_turns=1, cwd=".", settings="/state/session-gate-settings.json",
    )
    argv = fake.calls[0]["argv"]
    assert argv[argv.index("--settings") + 1] == "/state/session-gate-settings.json"


# --- run_loop wiring (monkeypatched gate; no real shell, no real claude) ----


def _core_config(session_gate: str = "off", fallback_model: str = "") -> EngineConfig:
    return EngineConfig(
        task="TASK.md",
        gate=GateConfig(stages=[GateStage(name="test", command="bun run test")], lock_globs=["*.locked"]),
        cost=CostConfig(ceiling_usd=100.0),
        stop=StopConfig(max_iters=3, stagnation_limit=99, plateau_limit=99, regress_limit=99),
        session_gate=session_gate,
        fallback_model=fallback_model,
    )


def _run_once(tmp_path, monkeypatch, config) -> RecordingRunner:
    """Drive run_loop for exactly one execute: baseline gate red, then green after the call."""
    work = tmp_path / "work"
    work.mkdir()
    (work / "TASK.md").write_text("# T\n## Acceptance Criteria\n- x\n", encoding="utf-8")
    state = tmp_path / "state"

    n = {"v": 0}

    def fake_run_gate(stages, cwd, *, fail_fast=False):
        n["v"] += 1
        green = n["v"] >= 2  # baseline red, then green on the first in-loop gate
        return {
            "green": green, "pass": 1 if green else 0, "fail": 0 if green else 1,
            "total": 1, "stages": [{"name": "test", "ok": green}], "raw": "",
        }

    monkeypatch.setattr(core, "run_gate", fake_run_gate)
    runner = RecordingRunner()
    rc = run_loop(config, runner=runner, state_dir=state, cwd=work)
    assert rc == 0
    assert runner.calls, "the execute runner must have been called once"
    return runner


def test_core_off_mode_byte_identical_call(tmp_path, monkeypatch):
    runner = _run_once(tmp_path, monkeypatch, _core_config(session_gate="off"))
    call = runner.calls[0]
    assert "settings" not in call  # no --settings kwarg
    assert "fallback_model" not in call  # no fallback threaded
    assert not call["prompt"].startswith("/goal")  # prompt unchanged


def test_core_goal_mode_prepends_goal_line(tmp_path, monkeypatch):
    runner = _run_once(tmp_path, monkeypatch, _core_config(session_gate="goal"))
    call = runner.calls[0]
    assert call["prompt"].startswith("/goal the command `bun run test` exits 0")
    assert "settings" not in call  # goal mode uses no --settings


def test_core_stop_hook_mode_passes_settings(tmp_path, monkeypatch):
    runner = _run_once(tmp_path, monkeypatch, _core_config(session_gate="stop-hook"))
    call = runner.calls[0]
    assert call["settings"].endswith("session-gate-settings.json")
    assert not call["prompt"].startswith("/goal")  # stop-hook leaves the prompt untouched
    written = json.loads(Path(call["settings"]).read_text(encoding="utf-8"))
    assert written["hooks"]["Stop"][0]["hooks"][0]["command"] == "bun run test || exit 2"


def test_core_threads_fallback_model(tmp_path, monkeypatch):
    runner = _run_once(tmp_path, monkeypatch, _core_config(fallback_model="sonnet,haiku"))
    assert runner.calls[0]["fallback_model"] == "sonnet,haiku"
