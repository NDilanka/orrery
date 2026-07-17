"""Tests for ``model_for_phase`` (mirrors the verify selftest §4) and the ``loop.json``
engine-block loader (loads the real seeded hello/loop.json + asserts defaults fill in)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from conftest import REPO_ROOT

from orrery_loop.config import EngineConfig, from_loop_json, model_for_phase

# The seeded, self-contained Python loop the visualizer ships (pytest gate). Resolved off the
# repo root (from the test file's location) so this passes from any CWD, not just the repo root.
HELLO = REPO_ROOT / "orrery/loops/hello/loop.json"


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
    assert not hasattr(cfg.gate, "green_when")  # removed (Task 5) — gate green is exit==0, always
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
    assert cfg.iter_timeout_min == 60
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
    assert cfg.stop.token_ceiling == 0  # A2: token ceiling is opt-in (disabled by default)
    assert cfg.verify.judge_model == "haiku"
    assert cfg.verify.contract == []
    # default gate stage is the single bun-test stage from loopcore.ps1
    assert len(cfg.gate.stages) == 1
    assert cfg.gate.stages[0].command == "bun test"
    assert not hasattr(cfg.gate, "green_when")  # removed (Task 5) — gate green is exit==0, always
    assert cfg.gate.lock_globs == ["*.test.ts"]
    assert cfg.gate.fail_fast is False
    assert cfg.gate.lock_infra is False  # A1: infra-lock is strictly opt-in


def test_lock_infra_parsed_both_spellings():
    """A1: ``gate.lockInfra`` / ``lock_infra`` toggles the curated infra-file hash-lock."""
    assert from_loop_json({"engine": {"gate": {"lockInfra": True}}}).gate.lock_infra is True
    assert from_loop_json({"engine": {"gate": {"lock_infra": True}}}).gate.lock_infra is True
    # absent -> default off (parity)
    assert from_loop_json({"engine": {"gate": {}}}).gate.lock_infra is False


def test_token_ceiling_parsed_both_spellings():
    """A2: ``stop.tokenCeiling`` / ``token_ceiling`` sets the cumulative token budget."""
    assert from_loop_json({"engine": {"stop": {"tokenCeiling": 50000}}}).stop.token_ceiling == 50000
    assert from_loop_json({"engine": {"stop": {"token_ceiling": 42}}}).stop.token_ceiling == 42
    # absent -> default 0 (disabled)
    assert from_loop_json({"engine": {"stop": {}}}).stop.token_ceiling == 0


# --- lenient numeric coercion (config load stays loud, not fatal) ----------


def test_bad_ceiling_usd_falls_back_to_default_and_warns(capsys):
    # A non-numeric ceilingUsd used to crash load with ValueError. Now it warns + keeps default.
    cfg = from_loop_json({"engine": {"cost": {"ceilingUsd": "lots"}}})
    assert cfg.cost.ceiling_usd == 3.00  # default preserved
    err = capsys.readouterr().err
    assert "ceilingUsd" in err and "engine.cost" in err


def test_bad_max_iters_falls_back_to_default_and_warns(capsys):
    cfg = from_loop_json({"engine": {"stop": {"maxIters": "seven"}}})
    assert cfg.stop.max_iters == 15  # default preserved
    err = capsys.readouterr().err
    assert "maxIters" in err and "engine.stop" in err


def test_bad_token_ceiling_falls_back_to_default_and_warns(capsys):
    cfg = from_loop_json({"engine": {"stop": {"tokenCeiling": "big"}}})
    assert cfg.stop.token_ceiling == 0
    assert "tokenCeiling" in capsys.readouterr().err


def test_valid_numeric_strings_still_coerce():
    # A numeric STRING is still accepted (float("3.5") / int("9")), no warning path.
    assert from_loop_json({"engine": {"cost": {"ceilingUsd": "3.5"}}}).cost.ceiling_usd == 3.5
    assert from_loop_json({"engine": {"stop": {"maxIters": "9"}}}).stop.max_iters == 9


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


# =====================================================================
# iter_timeout_min (Task 1a — wall-clock cap on the generic loop's execute call)
# =====================================================================


def test_iter_timeout_min_default_is_60():
    assert EngineConfig().iter_timeout_min == 60


def test_iter_timeout_min_camel_case():
    cfg = from_loop_json({"engine": {"iterTimeoutMin": 45}})
    assert cfg.iter_timeout_min == 45


def test_iter_timeout_min_snake_case():
    cfg = from_loop_json({"engine": {"iter_timeout_min": 15}})
    assert cfg.iter_timeout_min == 15


def test_iter_timeout_min_zero_disables():
    cfg = from_loop_json({"engine": {"iterTimeoutMin": 0}})
    assert cfg.iter_timeout_min == 0


def test_loop_json_field_default_empty():
    assert EngineConfig().loop_json == ""


# =====================================================================
# Task 3/5 — unknown-key warnings + the retired gate.greenWhen field
# =====================================================================


def test_green_when_removed_from_gate_config():
    """gate.greenWhen was parsed but never consulted (real green = every stage exit 0,
    orrery_loop.gate.run_gate) — the field is gone; GateConfig has no such attribute."""
    from orrery_loop.config import GateConfig

    cfg = GateConfig()
    assert not hasattr(cfg, "green_when")


def test_green_when_in_json_warns_retired_not_unrecognized(capsys):
    from_loop_json({"engine": {"gate": {"greenWhen": "exit==0"}}})
    err = capsys.readouterr().err
    assert "retired" in err
    assert "greenWhen" in err
    assert "unrecognized" not in err


def test_unknown_top_level_engine_key_warns(capsys):
    from_loop_json({"engine": {"maxTurnz": 5}})
    err = capsys.readouterr().err
    assert "maxTurnz" in err
    assert "unrecognized" in err


def test_known_engine_keys_produce_no_warning(capsys):
    from_loop_json(HELLO)
    assert capsys.readouterr().err == ""


def test_misspelled_stop_key_warns_not_silently_dropped(capsys):
    """A2 safety: a typo'd ``stop.tokenCeiling`` must be LOUD — otherwise the budget backstop
    silently stays disabled (0) and the run is unbounded when the user believed they capped it."""
    cfg = from_loop_json({"engine": {"stop": {"tokenCeilng": 1500000}}})
    err = capsys.readouterr().err
    assert "tokenCeilng" in err
    assert "unrecognized" in err
    assert cfg.stop.token_ceiling == 0  # the typo did NOT set the ceiling


def test_valid_stop_keys_produce_no_warning(capsys):
    """Every real ``stop.*`` spelling (incl. tokenCeiling/token_ceiling) is known -> silent."""
    from_loop_json({"engine": {"stop": {"maxIters": 5, "tokenCeiling": 10, "token_ceiling": 10}}})
    assert capsys.readouterr().err == ""


@pytest.mark.parametrize(
    "block, valid, bogus",
    [
        ("cost", {"ceilingUsd": 1.0, "alertPct": [50]}, "ceilingUds"),
        ("verify", {"enabled": True, "judgeModel": "haiku", "mutationEvery": 1}, "judgemodel"),
        ("feedback", {"compact": True}, "compakt"),
        ("memory", {"enabled": True, "recallLimit": 3}, "recalLimit"),
        ("metrics", {"emit": True}, "emmit"),
    ],
)
def test_every_engine_subblock_validates_its_keys(capsys, block, valid, bogus):
    """No sub-block silently swallows a typo: a valid key set is silent, a bogus key warns and
    names both the block and the offending key (parity across gate/stop/cost/verify/
    feedback/memory/metrics)."""
    from_loop_json({"engine": {block: valid}})
    assert capsys.readouterr().err == "", f"valid {block} keys should not warn"

    from_loop_json({"engine": {block: {**valid, bogus: 1}}})
    err = capsys.readouterr().err
    assert f"engine.{block}" in err and bogus in err and "unrecognized" in err


# =====================================================================
# Task 4 — an external regression-style loop.json (single-file, no adapter block) still parses
# =====================================================================


def _external_regression_loop_json(loop_dir: Path) -> Path:
    """Replicates the shape of an external-repo regression seed: intra-loop paths RELATIVE,
    `--cwd` (the external repo the gate/git run against) and `engine.task` ABSOLUTE."""
    task = loop_dir / "TASK.md"
    task.write_text("fix until green", encoding="utf-8")
    path = loop_dir / "loop.json"
    path.write_text(
        json.dumps(
            {
                "id": "webapp-regression",
                "kind": "external",
                "adapter": "generic",
                "stateDir": ".loop",
                "stopFlag": ".loop/STOP",
                "checkpoint": ".loop/checkpoint.json",
                "start": {
                    "program": "loop",
                    "args": [
                        "--cwd", str(loop_dir / "your-webapp").replace("\\", "/"),
                        "--state-dir", ".loop",
                        "--loop-json", "loop.json",
                    ],
                },
                "engine": {
                    "task": str(task).replace("\\", "/"),
                    "gate": {
                        "stages": [
                            {
                                "name": "e2e",
                                "command": "npx playwright test",
                                "passPattern": "(\\d+) passed",
                                "failPattern": "(\\d+) failed",
                            }
                        ]
                    },
                    "cost": {"ceilingUsd": 2.0},
                },
            }
        ),
        encoding="utf-8",
    )
    return path


def test_load_external_regression_seed(tmp_path):
    cfg = from_loop_json(_external_regression_loop_json(tmp_path))
    assert cfg.task.endswith("TASK.md")
    assert cfg.gate.stages[0].name == "e2e"
    assert cfg.cost.ceiling_usd == 2.0
    assert not hasattr(cfg.gate, "green_when")


def test_seed_external_regression_intra_loop_paths_are_relative(tmp_path):
    """A4 Task 3: stateDir/stopFlag/checkpoint + the --state-dir/--loop-json args are RELATIVE.
    `--cwd` (the external repo the gate/git run against) and `engine.task` both stay
    ABSOLUTE on purpose: core.py resolves `config.task` as `work / config.task` first (`work` =
    `--cwd` = the external repo, NOT this loop's dir), so a relative task path would be looked up
    in the WRONG repo first (and only fall through to the loop dir if no same-named file exists
    there) -- a silent footgun if the external repo ever grows its own TASK.md. Absolute keeps
    it unambiguous."""
    data = json.loads(_external_regression_loop_json(tmp_path).read_text(encoding="utf-8"))
    assert data["stateDir"] == ".loop"
    assert data["stopFlag"] == ".loop/STOP"
    assert data["checkpoint"] == ".loop/checkpoint.json"
    args = data["start"]["args"]
    assert args[args.index("--state-dir") + 1] == ".loop"
    assert args[args.index("--loop-json") + 1] == "loop.json"
    assert Path(args[args.index("--cwd") + 1]).is_absolute()
    # `engine.task` stays ABSOLUTE on purpose (see docstring).
    task = data["engine"]["task"]
    assert Path(task).is_absolute()
    assert task.endswith("TASK.md")
