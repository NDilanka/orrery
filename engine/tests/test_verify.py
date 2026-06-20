"""Coverage for the held-out test-split gate hardening + the SAFE mutation audit.

The held-out tests build a realistic ``gate_result`` (the shape ``run_gate`` emits)
with a passing VISIBLE stage and a FAILING HELD-OUT stage and assert:
  * ``held_out_green`` is False (a failing hidden suite blocks green),
  * ``visible_feedback_raw`` does NOT contain the held-out section's output (no leak),
  * ``partition_stages`` splits visible vs held-out correctly,
  * ``held_out_lock_globs`` surfaces the declared globs for the hash-lock.

The mutation tests inject ``run_tests`` callables (no real runner) and assert the
kill/survive accounting, survivor listing, and ``max_mutants`` cap.
"""

from __future__ import annotations

from loop.gate import run_gate
from loop.verify import (
    held_out_green,
    held_out_lock_globs,
    held_out_names,
    mutation_audit,
    partition_stages,
    visible_feedback_raw,
)


def _stage(name, text, code, *, held_out=False, lock_globs=None):
    """A callable-command gate stage (no process spawn), optionally held-out."""
    s = {
        "name": name,
        "command": (lambda t=text, c=code: (t, c)),
        "pass_pattern": r"(\d+)\s+pass",
        "fail_pattern": r"(\d+)\s+fail",
    }
    if held_out:
        s["held_out"] = True
    if lock_globs is not None:
        s["lock_globs"] = lock_globs
    return s


VISIBLE_OUT = "visible suite\n 3 pass\n 0 fail\n"
HIDDEN_OUT = "HIDDEN-SUITE-SECRET assertion failed at edge_case_42\n 0 pass\n 1 fail\n"


# === held-out split ===================================================================


def test_partition_stages_splits_by_flag():
    stages = [
        _stage("test", VISIBLE_OUT, 0),
        _stage("hidden", HIDDEN_OUT, 1, held_out=True),
    ]
    visible, held = partition_stages(stages)
    assert [s["name"] for s in visible] == ["test"]
    assert [s["name"] for s in held] == ["hidden"]
    assert held_out_names(stages) == ["hidden"]


def test_partition_stages_tolerates_empty():
    assert partition_stages(None) == ([], [])
    assert partition_stages([]) == ([], [])


def test_held_out_green_false_when_hidden_fails():
    stages = [
        _stage("test", VISIBLE_OUT, 0),
        _stage("hidden", HIDDEN_OUT, 1, held_out=True),
    ]
    gate = run_gate(stages)
    names = held_out_names(stages)
    # The visible stage passed, but the held-out one failed -> not held-out green.
    assert any(s["name"] == "test" and s["ok"] for s in gate["stages"])
    assert held_out_green(gate, names) is False


def test_held_out_green_true_when_all_hidden_pass():
    stages = [
        _stage("test", VISIBLE_OUT, 0),
        _stage("hidden", "hidden ok\n 2 pass\n 0 fail\n", 0, held_out=True),
    ]
    gate = run_gate(stages)
    assert held_out_green(gate, held_out_names(stages)) is True


def test_held_out_green_vacuous_when_no_hidden():
    gate = run_gate([_stage("test", VISIBLE_OUT, 0)])
    assert held_out_green(gate, []) is True


def test_held_out_green_fails_closed_when_stage_missing():
    # A named held-out stage that never appears in the result must NOT count as green.
    gate = run_gate([_stage("test", VISIBLE_OUT, 0)])
    assert held_out_green(gate, ["hidden"]) is False


def test_visible_feedback_strips_hidden_section():
    stages = [
        _stage("test", VISIBLE_OUT, 0),
        _stage("hidden", HIDDEN_OUT, 1, held_out=True),
    ]
    gate = run_gate(stages)
    feedback = visible_feedback_raw(gate, held_out_names(stages))
    # The hidden suite's output must NOT leak to the agent.
    assert "HIDDEN-SUITE-SECRET" not in feedback
    assert "### stage 'hidden'" not in feedback
    # The visible section is preserved.
    assert "visible suite" in feedback
    assert "### stage 'test' (exit=0)" in feedback


def test_visible_feedback_strips_hidden_even_when_first():
    # Held-out stage ordered FIRST — slicing must still drop exactly its section.
    stages = [
        _stage("hidden", HIDDEN_OUT, 1, held_out=True),
        _stage("test", VISIBLE_OUT, 0),
    ]
    gate = run_gate(stages)
    feedback = visible_feedback_raw(gate, held_out_names(stages))
    assert "HIDDEN-SUITE-SECRET" not in feedback
    assert "visible suite" in feedback


def test_visible_feedback_noop_without_hidden():
    gate = run_gate([_stage("test", VISIBLE_OUT, 0)])
    assert visible_feedback_raw(gate, []) == gate["raw"]


def test_held_out_lock_globs_returns_declared():
    stages = [
        _stage("test", VISIBLE_OUT, 0, lock_globs=["**/*.test.ts"]),
        _stage("hidden", HIDDEN_OUT, 1, held_out=True, lock_globs=["**/hidden/*.test.ts"]),
        _stage("hidden2", HIDDEN_OUT, 1, held_out=True, lock_globs="**/secret_*.py"),
    ]
    globs = held_out_lock_globs(stages)
    # Only the held-out stages' globs, de-duplicated, order-stable. The visible
    # stage's glob is NOT included here (the caller already locks visible files).
    assert globs == ["**/hidden/*.test.ts", "**/secret_*.py"]


def test_held_out_lock_globs_empty_when_none():
    assert held_out_lock_globs([_stage("test", VISIBLE_OUT, 0)]) == []


# === mutation audit ===================================================================

# A tiny program with comparison, boolean and arithmetic operators to mutate.
SAMPLE_SOURCE = """
def classify(x):
    if x == 0:
        return True
    if x > 0 and x < 100:
        return x + 1
    return False
"""


def test_mutation_audit_all_killed_score_one():
    # A perfect suite notices every fault -> every mutant fails the suite.
    result = mutation_audit(SAMPLE_SOURCE, run_tests=lambda mutated: False, max_mutants=8)
    assert result["mutants"] > 0
    assert result["killed"] == result["mutants"]
    assert result["survived"] == 0
    assert result["score"] == 1.0
    assert result["survivors"] == []


def test_mutation_audit_true_false_survivor_listed():
    # A blind spot: the suite passes whenever a True<->False swap is applied, so those
    # mutants SURVIVE and score drops below 1.0. The injected runner is "blind" exactly
    # on a flipped boolean literal and kills every other mutant.
    def blind_on_bool(mutated: str) -> bool:
        flipped_true = mutated.count("True") != SAMPLE_SOURCE.count("True")
        flipped_false = mutated.count("False") != SAMPLE_SOURCE.count("False")
        return flipped_true or flipped_false

    result = mutation_audit(SAMPLE_SOURCE, run_tests=blind_on_bool, max_mutants=12)
    assert result["score"] < 1.0
    assert result["survived"] >= 1
    # At least one survivor label mentions a True/False swap.
    assert any("True" in s or "False" in s for s in result["survivors"])


def test_mutation_audit_respects_max_mutants():
    result = mutation_audit(SAMPLE_SOURCE, run_tests=lambda m: False, max_mutants=3)
    assert result["mutants"] <= 3
    assert result["killed"] <= 3


def test_mutation_audit_no_mutants_score_zero():
    # Source with nothing to mutate -> 0 mutants, score 0.0, no division error.
    result = mutation_audit("x = 5\n", run_tests=lambda m: True, max_mutants=8)
    assert result["mutants"] == 0
    assert result["score"] == 0.0
    assert result["survivors"] == []


def test_mutation_audit_deterministic():
    a = mutation_audit(SAMPLE_SOURCE, run_tests=lambda m: False, max_mutants=8)
    b = mutation_audit(SAMPLE_SOURCE, run_tests=lambda m: False, max_mutants=8)
    assert a == b
