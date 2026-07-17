"""quota.survive — the wait-and-resume driver.

Deterministic: injected ``emit`` (records the event sequence), ``sleep`` (records durations,
never sleeps), and ``now_fn`` (a fixed clock). Fake runners drive the probe outcomes. No real
claude, no real sleep, no real clock.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from orrery_loop.quota import QuotaStatus, get_quota_wait_sec, survive

# Fixed clock so wait arithmetic is deterministic.
NOW = datetime(2026, 6, 20, 10, 0, 0)


def _epoch_dt(dt: datetime) -> datetime:
    """Round-trip a wall-clock datetime through the same FromUnixTimeSeconds path the
    resolver uses, so an expected reset_at is timezone-correct on any machine."""
    e = int(dt.replace(tzinfo=timezone.utc).timestamp())
    return datetime.fromtimestamp(e, tz=timezone.utc).astimezone().replace(tzinfo=None)


class FakeRunner:
    """Drives survive() with a scripted list of QuotaStatus probe outcomes."""

    def __init__(self, statuses, *, supports_quota_probe=True):
        self.supports_quota_probe = supports_quota_probe
        self._statuses = list(statuses)
        self.probe_calls = 0

    def probe_quota(self) -> QuotaStatus:
        self.probe_calls += 1
        # Hold the last scripted status once the script is exhausted.
        idx = min(self.probe_calls - 1, len(self._statuses) - 1)
        return self._statuses[idx]


class Recorder:
    """Captures emitted events and slept durations."""

    def __init__(self):
        self.events: list[dict] = []
        self.sleeps: list[int] = []

    def emit(self, ev):
        self.events.append(ev)

    def sleep(self, sec):
        self.sleeps.append(sec)

    def kinds(self):
        return [e["event"] for e in self.events]


def _now():
    return NOW


# --- probing backend: limited, limited, then available ---------------------


def test_survive_hit_wait_wait_resume_returns_true():
    reset_at = _epoch_dt(NOW + timedelta(minutes=45))
    limited = QuotaStatus(limited=True, reset_at=reset_at, reset_type="five_hour")
    available = QuotaStatus(limited=False, reset_at=None, reset_type=None)
    runner = FakeRunner([limited, limited, available])
    rec = Recorder()

    ok = survive(
        runner,
        label="iter 3",
        cum=1.25,
        emit=rec.emit,
        sleep=rec.sleep,
        default_wait_min=30,
        max_waits=30,
        now_fn=_now,
    )

    assert ok is True
    assert rec.kinds() == ["quota-hit", "quota-wait", "quota-wait", "quota-resume"]

    # the hit event carries the detected reset moment
    hit = rec.events[0]
    assert hit["label"] == "iter 3"
    assert hit["cum"] == 1.25

    # both sleeps came from get_quota_wait_sec(reset_at, 30, now=NOW)
    expected_wait = get_quota_wait_sec(reset_at, 30, now=NOW)
    assert rec.sleeps == [expected_wait, expected_wait]
    # the wait events carry that same waitSec and the right resetType
    waits = [e for e in rec.events if e["event"] == "quota-wait"]
    assert all(w["waitSec"] == expected_wait for w in waits)
    assert all(w["resetType"] == "five_hour" for w in waits)
    assert [w["probe"] for w in waits] == [1, 2]
    # resume reports the probe index that cleared
    assert rec.events[-1]["probe"] == 3


def test_survive_immediately_available_hit_then_resume():
    available = QuotaStatus(limited=False, reset_at=None, reset_type=None)
    runner = FakeRunner([available])
    rec = Recorder()
    ok = survive(
        runner, label="L", cum=0.0, emit=rec.emit, sleep=rec.sleep, now_fn=_now
    )
    assert ok is True
    # hit fires on entry even when the very first probe is already clear, then resume.
    assert rec.kinds() == ["quota-hit", "quota-resume"]
    assert rec.sleeps == []


# --- probing backend: never clears within max_waits ------------------------


def test_survive_exhausts_max_waits_returns_false():
    reset_at = _epoch_dt(NOW + timedelta(minutes=10))
    limited = QuotaStatus(limited=True, reset_at=reset_at, reset_type="five_hour")
    runner = FakeRunner([limited])  # always limited
    rec = Recorder()

    ok = survive(
        runner,
        label="stuck",
        cum=0.0,
        emit=rec.emit,
        sleep=rec.sleep,
        max_waits=3,
        now_fn=_now,
    )
    assert ok is False
    # hit + exactly max_waits wait events, no resume
    assert rec.kinds() == ["quota-hit", "quota-wait", "quota-wait", "quota-wait"]
    assert len(rec.sleeps) == 3
    assert "quota-resume" not in rec.kinds()


# --- non-probing backend: single fallback wait -----------------------------


def test_survive_non_probing_single_fallback_wait_returns_true():
    runner = FakeRunner([], supports_quota_probe=False)
    rec = Recorder()
    ok = survive(
        runner,
        label="aider",
        cum=2.0,
        emit=rec.emit,
        sleep=rec.sleep,
        default_wait_min=15,
        now_fn=_now,
    )
    assert ok is True
    # never probes a non-probing backend
    assert runner.probe_calls == 0
    assert rec.kinds() == ["quota-hit", "quota-wait"]
    # one fallback sleep of default_wait_min minutes, no reset known
    assert rec.sleeps == [15 * 60]
    wait = [e for e in rec.events if e["event"] == "quota-wait"][0]
    assert wait["waitSec"] == 15 * 60
    assert wait["resetType"] == ""  # unknown reset type serializes as empty string


# --- non-probing backend: the blind-wait cap (QUOTA fix) -------------------


def test_non_probing_returns_false_once_blind_wait_cap_reached():
    # A persistent rate limit on a non-probing backend must NOT let the caller retry forever.
    # At/over the cap survive() returns False WITHOUT sleeping -> the caller's failure path runs.
    runner = FakeRunner([], supports_quota_probe=False)
    rec = Recorder()
    ok = survive(
        runner,
        label="aider",
        cum=0.0,
        emit=rec.emit,
        sleep=rec.sleep,
        blind_waits=4,
        max_blind_waits=4,
        now_fn=_now,
    )
    assert ok is False
    assert rec.sleeps == []  # no wait once capped
    assert rec.kinds() == []  # no hit/wait events emitted on the capped path


def test_non_probing_waits_up_to_but_not_past_the_cap():
    # Simulate the caller's retry loop: survive() keeps waiting (True) until blind_waits hits
    # the cap, then returns False. A total of max_blind_waits waits, then termination.
    runner = FakeRunner([], supports_quota_probe=False)
    rec = Recorder()
    cap = 4
    results = []
    blind = 0
    for _ in range(cap + 3):  # loop more than the cap to prove it terminates
        ok = survive(
            runner,
            label="aider",
            cum=0.0,
            emit=rec.emit,
            sleep=rec.sleep,
            blind_waits=blind,
            max_blind_waits=cap,
            now_fn=_now,
        )
        results.append(ok)
        if not ok:
            break
        blind += 1
    # exactly `cap` successful blind waits, then a terminating False.
    assert results == [True, True, True, True, False]
    assert len(rec.sleeps) == cap


def test_non_probing_success_resets_the_blind_wait_counter():
    # The caller resets its counter on a successful (non-quota) attempt: a FRESH retry sequence
    # starting at blind_waits=0 waits again rather than being permanently poisoned by an
    # earlier burst that approached the cap.
    runner = FakeRunner([], supports_quota_probe=False)
    rec = Recorder()
    # near the cap -> still waits (3 < 4)
    assert survive(runner, label="a", cum=0.0, emit=rec.emit, sleep=rec.sleep,
                   blind_waits=3, max_blind_waits=4, now_fn=_now) is True
    # ...caller then had a success and reset to 0 -> waits again, cap not tripped.
    assert survive(runner, label="a", cum=0.0, emit=rec.emit, sleep=rec.sleep,
                   blind_waits=0, max_blind_waits=4, now_fn=_now) is True
