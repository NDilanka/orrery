"""Branch coverage for ``decide()`` and ``update_consecutive_fail()``.

Mirrors the intent of ``selftest.ps1`` (the PowerShell decision self-test) plus the extra
branches the port must hold (verifier_refuted suppression, regression->handoff at limit,
and the consecutive-fail reset/recover/handoff transitions).
"""

from __future__ import annotations

from orrery_loop.decide import decide, floor_breach, update_consecutive_fail


def D(**over):
    """Sensible defaults; each scenario overrides only what it tests (mirrors selftest.ps1)."""
    p = dict(
        green=False, tampered=False, count_dropped=False, blocked=False,
        pass_=5, best_pass=5, changed=True,
        regress_count=0, regress_limit=3, plateau=0, plateau_limit=3,
        stale=0, stagnation_limit=2, cum=0.0, ceiling=3.0, iter=2, max_iters=15,
    )
    p.update(over)
    return decide(**p)


# --- priority order (mirrors selftest.ps1) ---


def test_green_wins():
    d = D(green=True)
    assert d.action == "stop"
    assert d.green is True
    assert d.reason == "all tests green at iter 2"


def test_tamper_beats_green():
    d = D(green=True, tampered=True)
    assert d.action == "stop"
    assert d.green is False
    assert d.reason.startswith("test")


def test_count_drop_beats_green():
    d = D(green=True, count_dropped=True)
    assert d.action == "stop"
    assert d.green is False
    assert "count dropped" in d.reason


def test_verifier_refuted_suppresses_green():
    # gate green but verifier refuted -> NOT a stop-green; falls through to continue.
    d = D(green=True, verifier_refuted=True)
    assert d.action == "continue"
    assert d.green is False
    assert d.reason == "advance"


def test_blocked_handoff():
    d = D(blocked=True)
    assert d.action == "stop"
    assert d.reason == "agent reported BLOCKED"


def test_cost_ceiling_beats_regress():
    d = D(cum=3.0, pass_=1, best_pass=5)
    assert d.action == "stop"
    assert d.reason.startswith("cost")
    assert d.reason == "cost ceiling $3.0 reached"


def test_token_ceiling_stops_when_reached():
    # A2: cumulative tokens at/over a positive ceiling stops (not-green), beside the cost ceiling.
    d = D(cum_tokens=1000, token_ceiling=1000, pass_=1, best_pass=5)
    assert d.action == "stop"
    assert d.green is False
    assert d.reason == "token ceiling 1000 reached"


def test_token_ceiling_disabled_by_default():
    # A2 parity: ceiling 0 (default) never fires no matter how many tokens accrued.
    d = D(cum_tokens=10_000_000, token_ceiling=0)
    assert d.reason == "advance" and d.action == "continue"


def test_token_ceiling_below_threshold_does_not_stop():
    d = D(cum_tokens=999, token_ceiling=1000)
    assert d.action == "continue" and d.reason == "advance"


def test_regression_rollback():
    d = D(pass_=3, best_pass=5)
    assert d.action == "rollback"
    assert d.reason == "regression (3 < best 5) — rolling back to best"


def test_regression_handoff_at_limit():
    d = D(pass_=3, best_pass=5, regress_count=2, regress_limit=3)
    assert d.action == "stop"
    assert d.reason == "repeated regressions (3/3) — handoff"


def test_stagnation_no_change():
    d = D(changed=False, stale=2, stagnation_limit=2)
    assert d.action == "stop"
    assert d.reason == "stagnation: 2 iters with no change"


def test_no_stop_below_stale_limit():
    d = D(changed=False, stale=1, stagnation_limit=2)
    assert d.action == "continue"


def test_plateau_churn_no_gain():
    d = D(changed=True, pass_=5, best_pass=5, plateau=3, plateau_limit=3)
    assert d.action == "stop"
    assert d.reason == "plateau: 3 iters changed with no net progress"


def test_below_plateau_continues():
    d = D(changed=True, pass_=5, best_pass=5, plateau=1)
    assert d.action == "continue"


def test_max_iters_stop():
    d = D(iter=15, max_iters=15)
    assert d.action == "stop"
    assert d.reason == "max iterations (15) reached without green"


def test_normal_progress_continues():
    d = D(pass_=6, best_pass=5)
    assert d.action == "continue"
    assert d.reason == "advance"


def test_priority_tamper_beats_count_drop():
    # both set -> tamper reason wins (it is checked first).
    d = D(tampered=True, count_dropped=True)
    assert d.reason == "test files were modified (tamper)"


# --- update_consecutive_fail transitions ---


def test_fail_green_resets_streak():
    s = update_consecutive_fail(green=True, made_progress=False, count=2, recovered=True)
    assert s.count == 0
    assert s.recovered is False
    assert s.recover is False
    assert s.handoff is False
    assert s.reason == ""


def test_fail_progress_resets_streak():
    s = update_consecutive_fail(green=False, made_progress=True, count=2, recovered=False)
    assert s.count == 0
    assert s.recovered is False


def test_fail_increments_below_limit():
    s = update_consecutive_fail(green=False, made_progress=False, count=1, limit=3)
    assert s.count == 2
    assert s.recover is False
    assert s.handoff is False
    assert s.reason == ""


def test_fail_recover_once_at_limit():
    s = update_consecutive_fail(green=False, made_progress=False, count=2, recovered=False, limit=3)
    assert s.count == 3
    assert s.recover is True
    assert s.handoff is False
    assert s.recovered is True
    assert s.reason == "consecutive no-progress failures (3/3) — recover-once"


def test_fail_handoff_after_recover_spent():
    s = update_consecutive_fail(green=False, made_progress=False, count=3, recovered=True, limit=3)
    assert s.count == 4
    assert s.recover is False
    assert s.handoff is True
    assert s.reason == "consecutive no-progress failures (4) after recover — handoff"


def test_recover_and_handoff_mutually_exclusive():
    s = update_consecutive_fail(green=False, made_progress=False, count=2, recovered=False, limit=3)
    assert not (s.recover and s.handoff)


# ---------------------------------------------------------------------------
# floor_breach — the shared regression-floor decision (Task 2, orrery_loop.bmad.driver's
# dev-gate / post-review / post-smoke boundaries all call this ONE pure function now).
# ---------------------------------------------------------------------------


def test_floor_breach_true_when_pass_drops_below_floor():
    assert floor_breach(8, 10) is True


def test_floor_breach_false_when_pass_meets_or_exceeds_floor():
    assert floor_breach(10, 10) is False
    assert floor_breach(11, 10) is False


def test_floor_breach_zero_floor_never_breaches_at_zero_or_above():
    assert floor_breach(0, 0) is False
