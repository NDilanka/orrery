"""Tests for ``model_for_phase`` (mirrors the verify selftest §4) and the ``loop.json``
engine-block loader (loads the real seeded hello/loop.json + asserts defaults fill in)."""

from __future__ import annotations

from pathlib import Path

from loop.config import EngineConfig, from_loop_json, model_for_phase

# The seeded, self-contained Python loop the visualizer ships (pytest gate).
HELLO = Path("orrery/loops/hello/loop.json")


# =====================================================================
# model_for_phase
# =====================================================================


def test_model_defaults_when_no_map():
    assert model_for_phase("discover", None) == "haiku"
    assert model_for_phase("execute", None) == "sonnet"
    assert model_for_phase("judge", None) == "haiku"
    assert model_for_phase("hard", None) == "opus"


def test_model_user_override_wins_partial_keeps_defaults():
    m = {"execute": "opus", "judge": "sonnet"}
    assert model_for_phase("execute", m) == "opus"
    assert model_for_phase("judge", m) == "sonnet"
    # unspecified phase falls back to the default
    assert model_for_phase("discover", m) == "haiku"


def test_model_phase_case_insensitive():
    assert model_for_phase("EXECUTE", None) == "sonnet"


def test_model_unknown_phase_safe_middle_tier():
    assert model_for_phase("frobnicate", None) == "sonnet"


# =====================================================================
# from_loop_json — the real seeded hello/loop.json
# =====================================================================


def test_load_hello_loop_json():
    cfg = from_loop_json(HELLO)
    assert cfg.task == "TASK.md"
    assert cfg.models == {
        "discover": "haiku", "execute": "sonnet", "judge": "haiku", "hard": "opus",
    }
    assert cfg.max_turns == 20
    assert cfg.allowed_tools == [
        "Read", "Edit", "Write", "Bash(pytest)", "Bash(pytest:*)",
    ]
    assert cfg.permission_mode == "acceptEdits"

    # gate
    assert len(cfg.gate.stages) == 1
    stage = cfg.gate.stages[0]
    assert stage.name == "test"
    assert stage.command == "pytest -q"
    assert stage.pass_pattern == r"(\d+) passed"
    assert stage.fail_pattern == r"(\d+) failed"
    assert cfg.gate.green_when == "exit==0"
    assert cfg.gate.lock_globs == ["**/test_*.py"]

    # cost
    assert cfg.cost.ceiling_usd == 0.5
    assert cfg.cost.alert_pct == [50, 80, 100]

    # stop
    assert cfg.stop.max_iters == 8
    assert cfg.stop.stagnation_limit == 2
    assert cfg.stop.plateau_limit == 3
    assert cfg.stop.regress_limit == 3
    assert cfg.stop.graceful_at_phase is True

    # verify
    assert cfg.verify.judge_model == "haiku"
    assert cfg.verify.contract == []


def test_model_for_via_config():
    cfg = from_loop_json(HELLO)
    assert cfg.model_for("execute") == "sonnet"
    assert cfg.model_for("frobnicate") == "sonnet"


# =====================================================================
# defaults fill in when keys omitted
# =====================================================================


def test_empty_engine_uses_loop_ps1_defaults():
    cfg = from_loop_json({"engine": {}})
    assert cfg.task == "TASK.md"
    assert cfg.models == {}
    assert cfg.max_turns == 30
    assert cfg.permission_mode == "acceptEdits"
    assert cfg.allowed_tools == [
        "Read", "Edit", "Write", "Bash(bun test)", "Bash(bun test:*)",
    ]
    assert cfg.cost.ceiling_usd == 3.00
    assert cfg.cost.alert_pct == [50, 80, 100]
    assert cfg.stop.max_iters == 15
    assert cfg.stop.stagnation_limit == 2
    assert cfg.stop.plateau_limit == 3
    assert cfg.stop.regress_limit == 3
    assert cfg.stop.graceful_at_phase is True
    assert cfg.verify.judge_model == "haiku"
    assert cfg.verify.contract == []
    # default gate stage is the single bun-test stage from loopcore.ps1
    assert len(cfg.gate.stages) == 1
    assert cfg.gate.stages[0].command == "bun test"
    assert cfg.gate.green_when == "exit==0"
    assert cfg.gate.lock_globs == ["*.test.ts"]


def test_accepts_engine_block_directly():
    # passing just the engine sub-dict (no "engine" wrapper) also works
    cfg = from_loop_json({"task": "OTHER.md", "maxTurns": 7})
    assert cfg.task == "OTHER.md"
    assert cfg.max_turns == 7
    # untouched fields still default
    assert cfg.stop.max_iters == 15


def test_snake_case_keys_accepted():
    cfg = from_loop_json(
        {
            "engine": {
                "max_turns": 42,
                "allowed_tools": ["Read"],
                "permission_mode": "plan",
                "cost": {"ceiling_usd": 9.0, "alert_pct": [25]},
                "stop": {"max_iters": 4, "graceful_at_phase": False},
                "gate": {
                    "green_when": "exit==0",
                    "lock_globs": ["a"],
                    "stages": [{"name": "t", "command": "c", "pass_pattern": "p"}],
                },
                "verify": {"judge_model": "opus", "contract": ["x"]},
            }
        }
    )
    assert cfg.max_turns == 42
    assert cfg.allowed_tools == ["Read"]
    assert cfg.permission_mode == "plan"
    assert cfg.cost.ceiling_usd == 9.0
    assert cfg.cost.alert_pct == [25]
    assert cfg.stop.max_iters == 4
    assert cfg.stop.graceful_at_phase is False
    assert cfg.gate.lock_globs == ["a"]
    assert cfg.gate.stages[0].pass_pattern == "p"
    assert cfg.verify.judge_model == "opus"
    assert cfg.verify.contract == ["x"]


def test_isinstance_engine_config():
    assert isinstance(from_loop_json(HELLO), EngineConfig)
