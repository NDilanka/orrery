"""Tests for the liveness heartbeat (``orrery_loop.heartbeat``).

No real git, no real wall-clock waits where avoidable: the clock, timestamp and dirty-count are
injected so the payload is deterministic; the thread loop is exercised with a tiny interval.
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path

from orrery_loop.heartbeat import Heartbeat, write_activity


def _read(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def test_write_activity_writes_compact_json_and_no_temp_left(tmp_path):
    p = tmp_path / "activity.json"
    write_activity(p, {"phase": "dev-story", "dirty": 3})
    assert _read(p) == {"phase": "dev-story", "dirty": 3}
    # the temp file used for the atomic replace is gone
    assert not (tmp_path / "activity.json.tmp").exists()


def test_beat_payload_shape_is_camelcase_and_complete(tmp_path):
    p = tmp_path / "activity.json"
    fixed = datetime(2026, 6, 24, 17, 15, 0, tzinfo=timezone.utc)
    hb = Heartbeat(
        p,
        phase="dev-story",
        story="5-2-trust",
        repo="/repo",
        pid=4242,
        dirty_fn=lambda repo: 3,
        clock=lambda: 152.5,  # beat reads 152.5; started_at set to 100.0 below → 52.5s elapsed
        now=lambda: fixed,
    )
    hb._started_at = 100.0  # baseline, as if entered at clock()==100.0
    hb._beat()
    got = _read(p)
    assert got == {
        "ts": "2026-06-24T17:15:00Z",
        "phase": "dev-story",
        "story": "5-2-trust",
        "elapsedSec": 52.5,
        "dirty": 3,
        "pid": 4242,
    }


def test_context_manager_beats_on_enter_loop_and_exit(tmp_path):
    p = tmp_path / "activity.json"
    beats = {"n": 0}

    def dirty(repo):
        beats["n"] += 1
        return beats["n"]

    # tiny interval so the background loop beats several times in a brief window
    with Heartbeat(p, phase="create-story", story="s1", repo=None, interval=0.05, dirty_fn=dirty):
        time.sleep(0.3)
    # enter beat + several loop beats + exit beat → well more than 2 calls
    assert beats["n"] >= 3, beats["n"]
    # file is valid JSON with the live fields after exit
    final = _read(p)
    assert final["phase"] == "create-story"
    assert final["story"] == "s1"
    assert "elapsedSec" in final and "ts" in final


def test_elapsed_increases_from_enter_to_exit(tmp_path):
    p = tmp_path / "activity.json"
    # clock() calls in order: started_at (enter), enter-beat, exit-beat. Loop never fires
    # (interval is long + stop is immediate). started_at=10, exit-beat=25 → 15s elapsed.
    seq = iter([10.0, 10.0, 25.0])
    hb = Heartbeat(
        p,
        phase="dev-story",
        story=None,
        repo=None,
        interval=10.0,  # long, so the loop never fires inside the test window
        dirty_fn=lambda r: 0,
        clock=lambda: next(seq, 25.0),
        now=lambda: datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    with hb:
        pass
    assert _read(p)["elapsedSec"] == 15.0


def test_dirty_count_failure_is_zero_not_a_crash(tmp_path):
    p = tmp_path / "activity.json"

    def boom(repo):
        raise RuntimeError("git exploded")

    # write_activity/_beat must not propagate a dirty_fn failure... but our dirty_fn IS injected, so
    # a raising one would propagate; the DEFAULT _git_dirty_count swallows. Assert the default path:
    hb = Heartbeat(p, phase="x", story=None, repo="/not/a/repo", dirty_fn=None)
    hb._started_at = 0.0
    hb._beat()  # repo path is bogus → git fails → dirty 0, no raise
    assert _read(p)["dirty"] == 0
    # and an injected exploding dirty_fn is the caller's contract (we don't catch it) — sanity only
    assert callable(boom)
