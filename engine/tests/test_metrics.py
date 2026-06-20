"""Coverage for ``compute_metrics`` — run-quality metrics from the event stream.

Three trajectories (built from the real :mod:`loop.events` builders where possible):
  * green on iter 1 -> first_try_green True, iters_to_green 1,
  * green on iter 3 with one rollback -> first_try_green False, iters_to_green 3,
    rollbacks 1, regression_rate = 1/3,
  * never green (handoff) -> final_green False, iters_to_green None.

Also exercises purity / tolerance of missing fields.
"""

from __future__ import annotations

from loop.events import (
    handoff_event,
    iter_event,
    rollback_event,
    stop_event,
)
from loop.metrics import compute_metrics


def _iter(idx, *, cum, action="continue", pass_=0, total=0):
    """A PROTOCOL §2 iter event with the fields metrics reads."""
    return iter_event(
        iter=idx,
        cost=cum,
        cum=cum,
        pass_=pass_,
        total=total,
        best=pass_,
        changed=True,
        stale=0,
        plateau=0,
        regress=0,
        action=action,
        reason="",
    )


# === green on iteration 1 =============================================================


def test_green_on_iter_1():
    events = [
        _iter(1, cum=0.50, action="stop", pass_=3, total=3),
        stop_event(reason="green", green=True, iter=1, cum=0.50, best_pass=3),
    ]
    m = compute_metrics(events)
    assert m["first_try_green"] is True
    assert m["iters_to_green"] == 1
    assert m["cost_to_green"] == 0.50
    assert m["final_green"] is True
    assert m["rollbacks"] == 0
    assert m["regression_rate"] == 0.0
    assert m["total_iters"] == 1
    assert m["total_cost"] == 0.50


# === green on iteration 3, one rollback ===============================================


def test_green_on_iter_3_with_rollback():
    events = [
        _iter(1, cum=0.40, pass_=1, total=3),
        _iter(2, cum=0.95, action="rollback", pass_=0, total=3),
        rollback_event(item="task", to_iter=1, best_pass=1, strike=1, strike_budget=3),
        _iter(3, cum=1.60, action="stop", pass_=3, total=3),
        stop_event(reason="green", green=True, iter=3, cum=1.60, best_pass=3),
    ]
    m = compute_metrics(events)
    assert m["first_try_green"] is False
    assert m["iters_to_green"] == 3
    assert m["cost_to_green"] == 1.60
    assert m["final_green"] is True
    # The iter line's action AND the standalone rollback event both count.
    assert m["rollbacks"] == 2
    assert m["total_iters"] == 3
    assert m["regression_rate"] == 2 / 3
    assert m["total_cost"] == 1.60


def test_single_rollback_regression_rate():
    # Only the standalone rollback event (iter action is 'continue') -> one rollback.
    events = [
        _iter(1, cum=0.40),
        _iter(2, cum=0.95),
        rollback_event(item="task", to_iter=1, best_pass=1, strike=1, strike_budget=3),
        _iter(3, cum=1.60, action="stop", pass_=3, total=3),
        stop_event(reason="green", green=True, iter=3, cum=1.60, best_pass=3),
    ]
    m = compute_metrics(events)
    assert m["rollbacks"] == 1
    assert m["regression_rate"] == 1 / 3


# === never green (handoff) ============================================================


def test_never_green_handoff():
    events = [
        _iter(1, cum=0.40, pass_=1, total=3),
        _iter(2, cum=0.90, pass_=2, total=3),
        _iter(3, cum=1.30, action="stop", pass_=2, total=3),
        handoff_event(item="task", reason="stuck", consecutive=3),
        stop_event(reason="handoff", green=False, iter=3, cum=1.30, best_pass=2),
    ]
    m = compute_metrics(events)
    assert m["final_green"] is False
    assert m["first_try_green"] is False
    assert m["iters_to_green"] is None
    assert m["cost_to_green"] is None
    assert m["total_iters"] == 3
    assert m["total_cost"] == 1.30


# === green detected from a gate event when stop is terse ==============================


def test_green_from_gate_event_when_stop_iterless():
    events = [
        _iter(1, cum=0.40, pass_=1, total=3),
        _iter(2, cum=0.80, pass_=3, total=3),
        {"event": "gate", "cum": 0.80, "green": True, "pass": 3, "fail": 0, "total": 3},
        # stop without an iter index -> borrows the last iter (2) and its cum.
        {"event": "stop", "reason": "green", "green": True},
    ]
    m = compute_metrics(events)
    assert m["iters_to_green"] == 2
    assert m["cost_to_green"] == 0.80
    assert m["final_green"] is True


# === purity / tolerance ===============================================================


def test_empty_and_none_are_safe():
    base = {
        "first_try_green": False,
        "iters_to_green": None,
        "cost_to_green": None,
        "rollbacks": 0,
        "regression_rate": 0.0,
        "final_green": False,
        "total_iters": 0,
        "total_cost": 0.0,
    }
    assert compute_metrics([]) == base
    assert compute_metrics(None) == base


def test_tolerates_missing_fields_and_junk():
    events = [
        {"event": "iter"},  # no iter/cum/action
        {"event": "rollback"},
        "not a dict",
        {"event": "stop", "green": True, "iter": 1, "cum": None},
    ]
    m = compute_metrics(events)
    assert m["total_iters"] == 1
    assert m["rollbacks"] == 1
    assert m["final_green"] is True
    # iter index 1 came from the stop event itself.
    assert m["iters_to_green"] == 1


def test_total_cost_is_running_max():
    events = [
        _iter(1, cum=2.00),
        _iter(2, cum=1.00),  # cum should never decrease, but max guards it
        _iter(3, cum=1.50, action="stop"),
    ]
    m = compute_metrics(events)
    assert m["total_cost"] == 2.00
