"""Coverage for ``parse_counts()`` and ``run_gate()``.

Mirrors the intent of ``selftest-gate.ps1``: the same captured real-runner outputs and the
same caller-tunable patterns are asserted against the parser, and the multi-stage
green/red + "last stage with counts wins" logic is exercised through the CALLABLE command
hook (no real process spawn — the analog of the PS scriptblock stages).
"""

from __future__ import annotations

import os
import sys

from loop.gate import parse_counts, run_gate, strip_ansi

# ---- captured sample outputs from real runners (from selftest-gate.ps1) -------------

BUN_GREEN = """bun test v1.3.11 (af24e281)

 3 pass
 0 fail
 12 expect() calls
Ran 3 tests across 1 files. [42.00ms]
"""

BUN_RED = """bun test v1.3.11 (af24e281)

 1 pass
 2 fail
Ran 3 tests across 1 files. [55.00ms]
"""

VITEST = """ FAIL  src/calc.test.ts > handles precedence
Test Files  1 failed (1)
     Tests  2 failed | 5 passed (7)
  Start at  10:00:00
  Duration  812ms
"""

JEST = """Tests:       1 failed, 8 passed, 9 total
Snapshots:   0 total
Time:        2.13 s
Ran all test suites.
"""

PYTEST_GREEN = """============================= test session starts =============================
collected 5 items

test_calc.py .....                                                       [100%]

============================== 5 passed in 0.12s ==============================
"""

PYTEST_RED = """============================= test session starts =============================
collected 5 items

test_calc.py ..F..                                                       [100%]

========================= 1 failed, 4 passed in 0.20s =========================
"""

GO_TEST = """=== RUN   TestAdd
--- PASS: TestAdd (0.00s)
=== RUN   TestSub
--- PASS: TestSub (0.00s)
=== RUN   TestMul
--- FAIL: TestMul (0.00s)
FAIL
exit status 1
FAIL    example/calc    0.012s
"""


def _stage(name, text, code, pass_pattern=None, fail_pattern=None):
    """A stage whose command is a callable returning (captured_output, exit_code).

    Python analog of selftest-gate.ps1's ``Stage`` scriptblock — injects captured output
    and an exit code without spawning a real runner.
    """
    return {
        "name": name,
        "command": (lambda t=text, c=code: (t, c)),
        "pass_pattern": pass_pattern,
        "fail_pattern": fail_pattern,
    }


# === single-stage parsing, multiple runner dialects ===================================


def test_bun_green_counts():
    r = parse_counts(BUN_GREEN, r"(\d+)\s+pass", r"(\d+)\s+fail")
    assert r["pass"] == 3
    assert r["fail"] == 0
    assert r["matched"] is True


def test_bun_red_counts():
    r = parse_counts(BUN_RED, r"(\d+)\s+pass", r"(\d+)\s+fail")
    assert r["pass"] == 1
    assert r["fail"] == 2


def test_vitest_anchored_patterns():
    # The "Tests" line must win over the file-level "1 failed"; caller-tuned patterns.
    r = parse_counts(VITEST, r"(\d+)\s+passed", r"Tests\s+(\d+)\s+failed")
    assert r["pass"] == 5
    assert r["fail"] == 2


def test_jest_counts():
    r = parse_counts(JEST, r"(\d+)\s+passed", r"(\d+)\s+failed")
    assert r["pass"] == 8
    assert r["fail"] == 1


def test_pytest_green_counts():
    r = parse_counts(PYTEST_GREEN, r"(\d+)\s+passed", r"(\d+)\s+failed")
    assert r["pass"] == 5
    assert r["fail"] == 0
    # No fail pattern hit, but the pass pattern DID -> still matched.
    assert r["matched"] is True


def test_pytest_red_counts():
    r = parse_counts(PYTEST_RED, r"(\d+)\s+passed", r"(\d+)\s+failed")
    assert r["pass"] == 4
    assert r["fail"] == 1


def test_go_test_line_counting():
    # go test reports per-test "--- PASS:" / "--- FAIL:" lines, not a single count.
    pass_pattern = r"(?m)^--- PASS:"
    fail_pattern = r"(?m)^--- FAIL:"
    import re

    assert len(re.findall(pass_pattern, GO_TEST)) == 2
    assert len(re.findall(fail_pattern, GO_TEST)) == 1


def test_no_match_returns_zero_unmatched():
    r = parse_counts("nothing numeric here", r"(\d+)\s+pass", r"(\d+)\s+fail")
    assert r == {"pass": 0, "fail": 0, "matched": False}


def test_empty_pattern_is_skipped():
    r = parse_counts(BUN_GREEN, "", "")
    assert r["matched"] is False


# === ANSI stripping ===================================================================


def test_ansi_is_stripped_before_parsing():
    colored = "\x1b[32m 3 pass\x1b[0m\n\x1b[31m 0 fail\x1b[0m"
    assert "\x1b" not in strip_ansi(colored)
    r = parse_counts(strip_ansi(colored), r"(\d+)\s+pass", r"(\d+)\s+fail")
    assert r["pass"] == 3
    assert r["fail"] == 0


def test_run_gate_strips_ansi_internally():
    colored = "\x1b[1;32m 7 pass\x1b[0m\n 0 fail"
    g = run_gate([_stage("test", colored, 0, r"(\d+)\s+pass", r"(\d+)\s+fail")])
    assert g["pass"] == 7
    assert g["green"] is True


# === run_gate end to end via callable stages ==========================================


def test_single_stage_green():
    g = run_gate([_stage("test", BUN_GREEN, 0, r"(\d+)\s+pass", r"(\d+)\s+fail")])
    assert g["green"] is True
    assert g["pass"] == 3
    assert g["fail"] == 0
    assert g["total"] == 3
    assert len(g["stages"]) == 1
    assert g["stages"][0] == {"name": "test", "ok": True, "exit": 0}


def test_single_stage_red_exit_is_truth():
    g = run_gate([_stage("test", BUN_RED, 1, r"(\d+)\s+pass", r"(\d+)\s+fail")])
    assert g["green"] is False
    assert g["pass"] == 1
    assert g["fail"] == 2


def test_multi_stage_all_green_last_counts_win():
    # codegen + lint print no counts; the test stage's counts must win (not zeroed).
    g = run_gate(
        [
            _stage("codegen", "codegen ok\nGenerated 4 files.", 0, r"(\d+)\s+pass", r"(\d+)\s+fail"),
            _stage("lint", "lint: 0 problems", 0, r"(\d+)\s+pass", r"(\d+)\s+fail"),
            _stage("test", BUN_GREEN, 0, r"(\d+)\s+pass", r"(\d+)\s+fail"),
        ]
    )
    assert g["green"] is True
    assert g["pass"] == 3
    assert g["total"] == 3
    assert len(g["stages"]) == 3


def test_multi_stage_pre_stage_fail_makes_red_but_keeps_counts():
    # lint exits 1 -> NOT green, even though the test stage is green and reported counts.
    g = run_gate(
        [
            _stage("codegen", "codegen ok", 0, r"(\d+)\s+pass", r"(\d+)\s+fail"),
            _stage("lint", "lint: 3 problems", 1, r"(\d+)\s+pass", r"(\d+)\s+fail"),
            _stage("test", BUN_GREEN, 0, r"(\d+)\s+pass", r"(\d+)\s+fail"),
        ]
    )
    assert g["green"] is False
    assert g["pass"] == 3
    by_name = {s["name"]: s for s in g["stages"]}
    assert by_name["lint"]["ok"] is False
    assert by_name["test"]["ok"] is True


def test_pre_stage_fail_no_counts_total_zero():
    # codegen fails with no counts; test never reached -> totals stay 0.
    g = run_gate([_stage("codegen", "ERROR: type mismatch", 1, r"(\d+)\s+pass", r"(\d+)\s+fail")])
    assert g["green"] is False
    assert g["pass"] == 0
    assert g["total"] == 0


def test_none_patterns_fall_back_to_defaults():
    # pass_pattern/fail_pattern None -> default (\d+)\s+pass / (\d+)\s+fail.
    g = run_gate([_stage("test", BUN_GREEN, 0, None, None)])
    assert g["pass"] == 3
    assert g["fail"] == 0


def test_count_dropped_pre_stage_does_not_zero_real_counts():
    # A later no-count stage must not overwrite an earlier stage's counts.
    g = run_gate(
        [
            _stage("test", BUN_GREEN, 0, r"(\d+)\s+pass", r"(\d+)\s+fail"),
            _stage("publish", "published artifact", 0, r"(\d+)\s+pass", r"(\d+)\s+fail"),
        ]
    )
    assert g["pass"] == 3
    assert g["total"] == 3


def test_raw_collects_each_stage():
    g = run_gate(
        [
            _stage("lint", "lint clean", 0, r"(\d+)\s+pass", r"(\d+)\s+fail"),
            _stage("test", BUN_GREEN, 0, r"(\d+)\s+pass", r"(\d+)\s+fail"),
        ]
    )
    assert "### stage 'lint' (exit=0)" in g["raw"]
    assert "### stage 'test' (exit=0)" in g["raw"]


# === cwd threading: string-command stages run in the configured working dir ============


def test_string_stage_runs_in_cwd(tmp_path):
    """A STRING-command gate stage honors run_gate's ``cwd``.

    Regression for: ``loop --cwd <dir>`` ran the gate in the wrong directory because run_gate
    spawned the shell with no ``cwd``. The stage prints its own getcwd via the venv python; the
    captured ``raw`` must report the temp dir, NOT the process cwd. Hermetic + cross-platform.
    """
    # ``sys.executable`` is the running interpreter (the venv python under pytest). Use -c so no
    # file needs to exist; -I isolates from site/user config for determinism.
    cmd = f'"{sys.executable}" -I -c "import os;print(os.getcwd())"'
    g = run_gate([{"name": "pwd", "command": cmd}], str(tmp_path))

    assert g["stages"][0]["ok"] is True
    reported = g["raw"].strip().splitlines()[-1].strip()
    # Compare realpaths so a symlinked temp dir (e.g. macOS /var -> /private/var) still matches.
    assert os.path.realpath(reported) == os.path.realpath(str(tmp_path))


def test_string_stage_default_cwd_is_process_dir():
    """``cwd=None`` (the default) preserves today's behavior: the gate runs in the process dir."""
    cmd = f'"{sys.executable}" -I -c "import os;print(os.getcwd())"'
    g = run_gate([{"name": "pwd", "command": cmd}])

    reported = g["raw"].strip().splitlines()[-1].strip()
    assert os.path.realpath(reported) == os.path.realpath(os.getcwd())
