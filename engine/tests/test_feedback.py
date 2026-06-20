"""Coverage for compact failure-feedback extraction (``loop.feedback``).

The SWE-agent / Self-Debug insight under test: from a noisy multi-line runner log, pull only
the FIRST failure — its test/symbol, its assertion message, and a ``file:line`` — bounded to
``max_chars``. We feed REAL-ish captured snippets for bun, vitest, jest, pytest, go test, and
a lint stage, and assert the compact block carries the right signal and nothing more.

``compact_feedback`` is then exercised over a synthetic ``run_gate``-shaped result to prove it
slices the FIRST failing stage's section and stays silent when the gate is green.
"""

from __future__ import annotations

from loop.feedback import compact_feedback, extract_first_failure

# ---- captured-ish runner outputs -----------------------------------------------------

# bun test red: the "(fail)" marker + an expect() block + a stack frame with file:line.
BUN_RED = """bun test v1.3.11 (af24e281)

calc.test.ts:
(fail) add > adds two numbers [1.20ms]
  error: expect(received).toBe(expected)

    Expected: 5
    Received: 4

      at add (src/calc.test.ts:12:17)
      at calc.test.ts:11:3

 0 pass
 1 fail
Ran 1 tests across 1 files. [55.00ms]
"""

# vitest red: "FAIL  file > name", AssertionError, and an "at …(file:line:col)" frame.
VITEST_RED = """ FAIL  src/sum.test.ts > sum > handles precedence
AssertionError: expected 6 to be 7 // Object.is equality

- Expected
+ Received

- 7
+ 6

 ❯ src/sum.test.ts:8:19
      6|   it('handles precedence', () => {
      7|     expect(sum(1, 2, 3)).toBe(7)
         |                          ^
    at Object.<anonymous> (src/sum.test.ts:8:19)

Test Files  1 failed (1)
     Tests  1 failed | 4 passed (5)
"""

# jest red: "✕ test name", "expect(received)…", "Expected/Received", and an "at …" frame.
JEST_RED = """ FAIL  ./multiply.test.js
  multiply
    ✕ multiplies two numbers (3 ms)

  ● multiply › multiplies two numbers

    expect(received).toBe(expected) // Object.is equality

    Expected: 6
    Received: 5

      10 |   it('multiplies two numbers', () => {
    > 11 |     expect(multiply(2, 3)).toBe(6);
         |                            ^
      at Object.<anonymous> (multiply.test.js:11:28)

Tests:       1 failed, 8 passed, 9 total
"""

# pytest red: short-summary "FAILED …::…", the section banner, an "E   assert …" row,
# and a "path:line:" locator line.
PYTEST_RED = """============================= test session starts =============================
collected 5 items

tests/test_calc.py ..F..                                                 [ 60%]

================================== FAILURES ===================================
_______________________________ test_add_two ________________________________

    def test_add_two():
>       assert add(2, 2) == 5
E       assert 4 == 5
E        +  where 4 = add(2, 2)

tests/test_calc.py:12: AssertionError
=========================== short test summary info ===========================
FAILED tests/test_calc.py::test_add_two - assert 4 == 5
========================= 1 failed, 4 passed in 0.20s =========================
"""

# go test red: the "--- FAIL:" marker plus the indented "file_test.go:NN: message" detail.
GO_RED = """=== RUN   TestAdd
--- PASS: TestAdd (0.00s)
=== RUN   TestMul
    calc_test.go:21: Mul(2, 3) = 5; want 6
--- FAIL: TestMul (0.00s)
FAIL
exit status 1
FAIL    example/calc    0.012s
"""

# lint stage (ruff-style) red: generic fallback should surface the precise error line.
LINT_RED = """src/app.py:14:5: F821 undefined name 'foo'
src/app.py:30:1: E305 expected 2 blank lines after class or function definition
Found 2 errors.
"""

# an all-green pytest run — must yield "" (no failure to compact).
PYTEST_GREEN = """============================= test session starts =============================
collected 5 items

tests/test_calc.py .....                                                 [100%]

============================== 5 passed in 0.12s ==============================
"""


# === extract_first_failure: per-dialect signal =======================================


def test_bun_failure_has_name_and_file_line():
    out = extract_first_failure(BUN_RED)
    assert "add" in out  # failing test/symbol
    assert "src/calc.test.ts:12" in out  # file:line present in input
    assert "Expected: 5" in out
    assert len(out) <= 1200


def test_vitest_failure_has_name_and_file_line():
    out = extract_first_failure(VITEST_RED)
    assert "handles precedence" in out
    assert "src/sum.test.ts:8" in out
    assert "AssertionError" in out
    assert len(out) <= 1200


def test_jest_failure_has_name_and_file_line():
    out = extract_first_failure(JEST_RED)
    assert "multiplies two numbers" in out
    assert "multiply.test.js:11" in out
    assert "Expected: 6" in out
    assert len(out) <= 1200


def test_pytest_failure_has_name_assertion_and_file_line():
    out = extract_first_failure(PYTEST_RED)
    assert "test_add_two" in out  # failing test name
    assert "assert 4 == 5" in out  # assertion message
    assert "tests/test_calc.py:12" in out  # file:line
    assert len(out) <= 1200


def test_go_failure_has_name_and_file_line():
    out = extract_first_failure(GO_RED)
    assert "TestMul" in out
    assert "calc_test.go:21" in out
    assert len(out) <= 1200


def test_lint_failure_surfaces_precise_error_via_generic():
    out = extract_first_failure(LINT_RED)
    assert "F821" in out or "undefined name 'foo'" in out
    assert "src/app.py:14" in out
    assert len(out) <= 1200


# === extract_first_failure: edge behaviour ============================================


def test_all_pass_returns_empty():
    assert extract_first_failure(PYTEST_GREEN) == ""


def test_empty_input_returns_empty():
    assert extract_first_failure("") == ""
    assert extract_first_failure("nothing interesting here\nall good\n") == ""


def test_truncates_to_max_chars():
    # A failure with a huge expect() payload must be clipped to max_chars.
    big = BUN_RED.replace("Received: 4", "Received: " + "x" * 5000)
    out = extract_first_failure(big, max_chars=200)
    assert len(out) <= 200


def test_first_failure_only_not_second():
    # Two pytest failures; only the FIRST test's name should anchor the block.
    two = PYTEST_RED.replace(
        "FAILED tests/test_calc.py::test_add_two - assert 4 == 5",
        "FAILED tests/test_calc.py::test_add_two - assert 4 == 5\n"
        "FAILED tests/test_calc.py::test_sub_two - assert 1 == 0",
    )
    out = extract_first_failure(two)
    assert "test_add_two" in out
    assert "test_sub_two" not in out


def test_ansi_is_stripped_before_extraction():
    colored = "\x1b[31m(fail) add > adds [1ms]\x1b[0m\n\x1b[31m  Expected: 5\x1b[0m"
    out = extract_first_failure(colored)
    assert "\x1b" not in out
    assert "add" in out


# === compact_feedback over a run_gate-shaped result ===================================


def _gate(green, stages, raw):
    """A minimal run_gate-shaped result: {green, stages:[{name,ok,exit}], raw}."""
    return {"green": green, "stages": stages, "raw": raw}


def _section(name, exit_code, body):
    """Reproduce run_gate's per-stage raw block (gate.py line 157)."""
    return f"### stage '{name}' (exit={exit_code})\n{body}"


def test_compact_feedback_green_returns_empty():
    raw = _section("test", 0, PYTEST_GREEN)
    g = _gate(True, [{"name": "test", "ok": True, "exit": 0}], raw)
    assert compact_feedback(g) == ""


def test_compact_feedback_picks_first_failing_stage_section():
    # lint fails first (exit 1); a later test stage also fails. We must compact the LINT
    # section, surfacing its precise error — not the downstream test failure.
    raw = "\n".join(
        [
            _section("lint", 1, LINT_RED),
            _section("test", 1, PYTEST_RED),
        ]
    )
    g = _gate(
        False,
        [
            {"name": "lint", "ok": False, "exit": 1},
            {"name": "test", "ok": False, "exit": 1},
        ],
        raw,
    )
    out = compact_feedback(g)
    assert "src/app.py:14" in out  # from the LINT section
    assert "F821" in out or "undefined name" in out
    assert "test_add_two" not in out  # the downstream test failure must NOT leak in


def test_compact_feedback_skips_passing_pre_stage_to_failing_test():
    # codegen + lint pass; the test stage is the FIRST failing one -> compact its section.
    raw = "\n".join(
        [
            _section("codegen", 0, "Generated 4 files."),
            _section("lint", 0, "All checks passed!"),
            _section("test", 1, GO_RED),
        ]
    )
    g = _gate(
        False,
        [
            {"name": "codegen", "ok": True, "exit": 0},
            {"name": "lint", "ok": True, "exit": 0},
            {"name": "test", "ok": False, "exit": 1},
        ],
        raw,
    )
    out = compact_feedback(g)
    assert "TestMul" in out
    assert "calc_test.go:21" in out


def test_compact_feedback_respects_max_chars():
    raw = _section("test", 1, BUN_RED.replace("Received: 4", "Received: " + "y" * 4000))
    g = _gate(False, [{"name": "test", "ok": False, "exit": 1}], raw)
    out = compact_feedback(g, max_chars=150)
    assert len(out) <= 150
