"""Cost-alert parity — fires each of 50/80/100 exactly once, ascending, ceiling>0 only."""

from __future__ import annotations

from loop.cost import update_cost_alert


def test_crossing_thresholds_across_calls_fires_each_once():
    # ceiling 10 -> pct = cum * 10.
    fired: tuple[int, ...] = ()

    r1 = update_cost_alert(5.0, 10.0, fired=fired)  # pct 50 -> fire 50
    assert r1.newly == [50]
    assert r1.fired == [50]

    r2 = update_cost_alert(8.0, 10.0, fired=r1.fired)  # pct 80 -> fire 80
    assert r2.newly == [80]
    assert r2.fired == [50, 80]

    r3 = update_cost_alert(10.0, 10.0, fired=r2.fired)  # pct 100 -> fire 100
    assert r3.newly == [100]
    assert r3.fired == [50, 80, 100]

    # Re-crossing an already-fired threshold fires nothing new.
    r4 = update_cost_alert(10.0, 10.0, fired=r3.fired)
    assert r4.newly == []
    assert r4.fired == [50, 80, 100]


def test_ceiling_zero_fires_nothing():
    r = update_cost_alert(999.0, 0.0)
    assert r.newly == []
    assert r.fired == []


def test_negative_ceiling_fires_nothing():
    r = update_cost_alert(999.0, -5.0)
    assert r.newly == []
    assert r.fired == []


def test_jump_past_multiple_thresholds_fires_all_ascending():
    # pct = 100/10 * ... cum 10 / ceiling 10 = 100% in one call from a cold fired-set.
    r = update_cost_alert(10.0, 10.0, fired=())
    assert r.newly == [50, 80, 100]
    assert r.fired == [50, 80, 100]


def test_jump_past_remaining_thresholds_only():
    # 50 already fired; jump straight to 100% -> fire the remaining 80 and 100 ascending.
    r = update_cost_alert(10.0, 10.0, fired=(50,))
    assert r.newly == [80, 100]
    assert r.fired == [50, 80, 100]


def test_below_first_threshold_fires_nothing():
    r = update_cost_alert(4.0, 10.0)  # pct 40
    assert r.newly == []
    assert r.fired == []


def test_custom_thresholds_respected():
    r = update_cost_alert(7.0, 10.0, thresholds=(25, 60, 90))  # pct 70 -> 25, 60
    assert r.newly == [25, 60]
    assert r.fired == [25, 60]
