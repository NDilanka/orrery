"""Coverage for the opt-in gate fail-fast short-circuit (``run_gate(fail_fast=True)``).

The build-heavy motivation: on a repo where the ``test`` stage is expensive, paying for a full
test run AFTER ``lint`` already failed is pure waste, and the loop runs the gate several times
per story. ``fail_fast`` stops launching stages after the first non-zero exit, while STILL
listing the skipped stages so every downstream consumer's shape stays intact.

Uses the CALLABLE-command hook (no real process): each command records that it ran, so we can
prove a skipped stage's command was never launched.
"""

from __future__ import annotations

from orrery_loop.config import GateConfig, from_loop_json
from orrery_loop.gate import run_gate


def _counting_stage(name: str, exit_code: int, ran: list[str], text: str = ""):
    """A stage whose callable records its own name in ``ran`` when (and only when) launched."""

    def cmd(_n=name, _c=exit_code, _t=text):
        ran.append(_n)
        return (_t, _c)

    return {"name": name, "command": cmd, "pass_pattern": r"(\d+)\s+pass",
            "fail_pattern": r"(\d+)\s+fail"}


# =====================================================================
# (a) fail_fast=False is unchanged — regression parity
# =====================================================================


def test_fail_fast_false_runs_every_stage_after_a_failure():
    ran: list[str] = []
    stages = [
        _counting_stage("codegen", 0, ran),
        _counting_stage("lint", 1, ran),  # fails
        _counting_stage("test", 0, ran, "3 pass 0 fail"),
    ]
    g = run_gate(stages, fail_fast=False)
    # every command ran, exactly as before
    assert ran == ["codegen", "lint", "test"]
    assert g["green"] is False
    # test's counts still surface (last stage with counts wins) — proves nothing was skipped
    assert g["pass"] == 3
    # no skipped markers on any stage
    assert all("skipped" not in s for s in g["stages"])


def test_default_is_fail_fast_off():
    """Calling run_gate with no fail_fast arg is byte-identical to the old behavior."""
    ran: list[str] = []
    stages = [_counting_stage("lint", 1, ran), _counting_stage("test", 0, ran, "5 pass 0 fail")]
    g = run_gate(stages)  # default
    assert ran == ["lint", "test"]  # test still ran
    assert g["pass"] == 5


# =====================================================================
# (b) fail_fast=True short-circuits after the first failing stage
# =====================================================================


def test_fail_fast_true_skips_stages_after_first_failure():
    ran: list[str] = []
    stages = [
        _counting_stage("codegen", 0, ran),
        _counting_stage("lint", 1, ran),  # fails -> stop here
        _counting_stage("test", 0, ran, "3 pass 0 fail"),
    ]
    g = run_gate(stages, fail_fast=True)
    # codegen + lint ran; test's command was NEVER launched
    assert ran == ["codegen", "lint"]
    assert "test" not in ran
    assert g["green"] is False


def test_fail_fast_all_green_runs_everything():
    """fail_fast only skips AFTER a failure — an all-green pipeline runs every stage."""
    ran: list[str] = []
    stages = [
        _counting_stage("codegen", 0, ran),
        _counting_stage("lint", 0, ran),
        _counting_stage("test", 0, ran, "7 pass 0 fail"),
    ]
    g = run_gate(stages, fail_fast=True)
    assert ran == ["codegen", "lint", "test"]
    assert g["green"] is True
    assert g["pass"] == 7


# =====================================================================
# (c) skipped-stage shape + floor/flaky safety
# =====================================================================


def test_skipped_stage_shape():
    ran: list[str] = []
    stages = [
        _counting_stage("lint", 1, ran),  # fails
        _counting_stage("test", 0, ran, "3 pass 0 fail"),
        _counting_stage("typecheck", 0, ran),
    ]
    g = run_gate(stages, fail_fast=True)

    by_name = {s["name"]: s for s in g["stages"]}
    # every stage is still LISTED (shape intact for consumers)
    assert set(by_name) == {"lint", "test", "typecheck"}

    # the stage that ran-and-failed keeps the normal {name, ok, exit} shape
    assert by_name["lint"] == {"name": "lint", "ok": False, "exit": 1}

    # skipped stages carry the documented shape: ok=False, exit=None, skipped=True
    for skipped in ("test", "typecheck"):
        assert by_name[skipped] == {
            "name": skipped, "ok": False, "exit": None, "skipped": True
        }


def test_skipped_stage_adds_no_raw_section():
    """A skipped stage produced no output, so it writes no ``### stage`` header into raw
    (feedback.py parses exit codes out of those headers with an integer regex — a skipped
    stage must never inject a non-integer there)."""
    ran: list[str] = []
    stages = [
        _counting_stage("lint", 1, ran, "lint blew up"),
        _counting_stage("test", 0, ran, "3 pass 0 fail"),
    ]
    g = run_gate(stages, fail_fast=True)
    assert "### stage 'lint' (exit=1)" in g["raw"]
    assert "### stage 'test'" not in g["raw"]  # skipped -> no header


def test_skipped_test_stage_does_not_feed_floor_or_flaky_logic():
    """A skipped ``test`` stage contributes NO counts: gate fail/total stay 0 (zero-safe for the
    core count-drop floor), and BMAD's _is_flaky_shape reads fail==0 -> NOT flaky (no wasted
    retries; the red gate is reported at once because the real failure was ``lint``)."""
    from orrery_loop.bmad.driver import _is_flaky_shape

    ran: list[str] = []
    stages = [
        _counting_stage("codegen", 0, ran),
        _counting_stage("lint", 1, ran),  # the real, deterministic failure
        _counting_stage("test", 0, ran, "3 pass 0 fail"),  # would report counts, but is skipped
    ]
    g = run_gate(stages, fail_fast=True)
    # no counts leaked from the skipped test stage
    assert g["fail"] == 0
    assert g["total"] == 0
    # flaky check: lint red + fail==0 -> not the flaky signature -> fail fast, no retries
    assert _is_flaky_shape(g, max_fail=2) is False


# =====================================================================
# (d) config key resolution — camel + snake, default off
# =====================================================================


def test_gate_config_fail_fast_defaults_off():
    assert GateConfig().fail_fast is False
    assert from_loop_json({"engine": {}}).gate.fail_fast is False


def test_gate_fail_fast_camel_case():
    cfg = from_loop_json({"engine": {"gate": {"failFast": True}}})
    assert cfg.gate.fail_fast is True


def test_gate_fail_fast_snake_case():
    cfg = from_loop_json({"engine": {"gate": {"fail_fast": True}}})
    assert cfg.gate.fail_fast is True


def test_gate_fail_fast_key_is_not_flagged_unknown(capsys):
    from_loop_json({"engine": {"gate": {"failFast": True}}})
    err = capsys.readouterr().err
    assert "failFast" not in err  # a known key -> no unrecognized-key warning
