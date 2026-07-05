"""Coverage for :mod:`orrery_loop.supervise` — the built-in restart-on-failure supervisor (Task 6).

Everything effectful (spawn/sleep/clock) is injected, so the whole restart/thrash-guard/STOP
logic runs with FAKE processes and NO real sleeps — fast and deterministic. A couple of tests
also exercise real tiny ``python -c`` children to prove the wiring end-to-end.
"""

from __future__ import annotations

import sys

from orrery_loop.supervise import STOP_NAME, STOP_SUPERVISOR_NAME, SupervisorConfig, supervise


class FakeProc:
    """A fake ``subprocess.Popen``-alike: ``.wait()`` returns a queued exit code."""

    def __init__(self, pid: int, rc: int):
        self.pid = pid
        self._rc = rc
        self.waited = False

    def wait(self) -> int:
        self.waited = True
        return self._rc


def _fake_spawn(exit_codes: list[int]):
    """A ``spawn`` callable returning FakeProcs from ``exit_codes`` in order (last repeats)."""
    calls: list[list[str]] = []

    def _spawn(argv):
        calls.append(list(argv))
        rc = exit_codes[len(calls) - 1] if len(calls) <= len(exit_codes) else exit_codes[-1]
        return FakeProc(pid=1000 + len(calls), rc=rc)

    _spawn.calls = calls  # type: ignore[attr-defined]
    return _spawn


def _clock_seq(start: float = 0.0, step: float = 1.0):
    state = {"t": start}

    def _clock() -> float:
        state["t"] += step
        return state["t"]

    return _clock


def test_clean_exit_ends_supervision_with_no_restart(tmp_path):
    spawn = _fake_spawn([0])
    cfg = SupervisorConfig(state_dir=str(tmp_path), command=["x"], max_restarts=5, window_min=90)
    sleeps: list[float] = []
    rc = supervise(cfg, spawn=spawn, sleep=sleeps.append, clock=_clock_seq(), emit=lambda e: None)
    assert rc == 0
    assert len(spawn.calls) == 1
    assert sleeps == []  # never restarted -> never slept


def test_nonzero_exit_restarts_then_eventually_succeeds(tmp_path):
    spawn = _fake_spawn([1, 1, 0])
    cfg = SupervisorConfig(state_dir=str(tmp_path), command=["x"], max_restarts=5, window_min=90)
    events: list[dict] = []
    sleeps: list[float] = []
    rc = supervise(cfg, spawn=spawn, sleep=sleeps.append, clock=_clock_seq(), emit=events.append)
    assert rc == 0
    assert len(spawn.calls) == 3
    assert len(sleeps) == 2  # one sleep before each of the 2 restarts
    kinds = [e["event"] for e in events]
    assert kinds == ["supervisor-restart", "supervisor-restart"]
    assert events[0]["attempt"] == 1 and events[0]["exitCode"] == 1
    assert events[1]["attempt"] == 2
    # a human line was appended to supervisor.log
    assert (tmp_path / "supervisor.log").exists()
    assert "restarting" in (tmp_path / "supervisor.log").read_text(encoding="utf-8")


def test_default_emit_appends_supervisor_restart_to_log_jsonl(tmp_path):
    """With NO ``emit`` override, restarts append a real ``supervisor-restart`` line to
    ``<state_dir>/log.jsonl`` (the default wiring, PROTOCOL-style)."""
    import json

    spawn = _fake_spawn([1, 0])
    cfg = SupervisorConfig(state_dir=str(tmp_path), command=["x"], max_restarts=5, window_min=90)
    rc = supervise(cfg, spawn=spawn, sleep=lambda s: None, clock=_clock_seq())
    assert rc == 0
    lines = (tmp_path / "log.jsonl").read_text(encoding="utf-8").splitlines()
    assert [json.loads(ln)["event"] for ln in lines] == ["supervisor-restart"]


def test_stop_flag_present_after_nonzero_exit_prevents_restart(tmp_path):
    (tmp_path / STOP_NAME).write_text("now", encoding="utf-8")
    spawn = _fake_spawn([1])
    cfg = SupervisorConfig(state_dir=str(tmp_path), command=["x"])
    rc = supervise(cfg, spawn=spawn, sleep=lambda s: None, clock=_clock_seq(), emit=lambda e: None)
    assert rc == 1
    assert len(spawn.calls) == 1  # no restart attempted


def test_stop_supervisor_sentinel_prevents_restart(tmp_path):
    (tmp_path / STOP_SUPERVISOR_NAME).write_text("", encoding="utf-8")
    spawn = _fake_spawn([1])
    cfg = SupervisorConfig(state_dir=str(tmp_path), command=["x"])
    rc = supervise(cfg, spawn=spawn, sleep=lambda s: None, clock=_clock_seq(), emit=lambda e: None)
    assert rc == 1
    assert len(spawn.calls) == 1


def test_thrash_guard_trips_after_max_restarts_in_window(tmp_path):
    # 8 consecutive nonzero exits, all "close together" in the fake clock (step=1s, well within
    # a 90-min window) with max_restarts=3 -> restart 1,2,3 happen, the 4th trips the guard.
    spawn = _fake_spawn([1] * 8)
    cfg = SupervisorConfig(state_dir=str(tmp_path), command=["x"], max_restarts=3, window_min=90)
    events: list[dict] = []
    rc = supervise(
        cfg, spawn=spawn, sleep=lambda s: None, clock=_clock_seq(step=1.0), emit=events.append
    )
    assert rc == 1
    # launched once + 3 restarts = 4 spawns; the guard trips before a 5th
    assert len(spawn.calls) == 4
    assert len(events) == 3


def test_restarts_outside_the_window_do_not_count_toward_the_thrash_guard(tmp_path):
    # A big clock jump between restarts (bigger than the window) means old restarts age out, so
    # the guard never trips even after many restarts — bounded by a FINITE exit-code queue
    # ending in 0 so the test terminates deterministically (no risk of an infinite retry loop).
    spawn = _fake_spawn([1, 1, 1, 1, 1, 0])
    cfg = SupervisorConfig(state_dir=str(tmp_path), command=["x"], max_restarts=1, window_min=1)
    # step = 120s > the 60s (1 min) window -> every restart ages the previous one out.
    rc = supervise(
        cfg, spawn=spawn, sleep=lambda s: None, clock=_clock_seq(step=120.0), emit=lambda e: None
    )
    assert rc == 0
    # 1 launch + 5 restarts (guard never tripped despite exceeding max_restarts=1 in COUNT,
    # because each restart falls outside the rolling window).
    assert len(spawn.calls) == 6


def test_poll_sec_is_passed_to_the_injected_sleep(tmp_path):
    spawn = _fake_spawn([1, 0])
    cfg = SupervisorConfig(state_dir=str(tmp_path), command=["x"], poll_sec=42.5)
    sleeps: list[float] = []
    supervise(cfg, spawn=spawn, sleep=sleeps.append, clock=_clock_seq(), emit=lambda e: None)
    assert sleeps == [42.5]


def test_keyboard_interrupt_while_waiting_kills_the_child_tree_then_propagates(tmp_path, monkeypatch):
    killed: list[int] = []
    monkeypatch.setattr("orrery_loop.supervise.kill_tree", lambda pid, **kw: killed.append(pid))

    class RaisingProc:
        pid = 4242

        def wait(self):
            raise KeyboardInterrupt()

    cfg = SupervisorConfig(state_dir=str(tmp_path), command=["x"])
    try:
        supervise(cfg, spawn=lambda argv: RaisingProc(), sleep=lambda s: None, clock=_clock_seq())
        raised = False
    except KeyboardInterrupt:
        raised = True
    assert raised is True
    assert killed == [4242]


def test_real_child_processes_end_to_end(tmp_path):
    """Integration: real `python -c` children, real orrery_loop.proc.spawn_tree via the default spawn.

    The child fails (exit 1) once, then succeeds (exit 0) — a counter file tracks how many
    times it has run, since each restart is a fresh process with no in-memory state.
    """
    counter = tmp_path / "count.txt"
    counter.write_text("0", encoding="utf-8")
    cmd = [
        sys.executable,
        "-c",
        (
            "import pathlib,sys; "
            f"p = pathlib.Path(r'{counter}'); "
            "n = int(p.read_text()); p.write_text(str(n+1)); "
            "sys.exit(0 if n >= 1 else 1)"
        ),
    ]
    cfg = SupervisorConfig(state_dir=str(tmp_path), command=cmd, max_restarts=5, window_min=90, poll_sec=0.01)
    rc = supervise(cfg)
    assert rc == 0
    assert counter.read_text(encoding="utf-8") == "2"


def test_default_spawn_uses_proc_spawn_tree(monkeypatch, tmp_path):
    """The DEFAULT spawn goes through orrery_loop.proc.spawn_tree (Task 6: 'via proc.py's spawn
    utilities so the tree is killable') — not a bare subprocess.Popen call of its own."""
    seen = {}

    def fake_spawn_tree(argv, **kwargs):
        seen["argv"] = list(argv)
        return FakeProc(pid=1, rc=0)

    monkeypatch.setattr("orrery_loop.supervise.spawn_tree", fake_spawn_tree)
    cfg = SupervisorConfig(state_dir=str(tmp_path), command=["echo", "hi"])
    rc = supervise(cfg, sleep=lambda s: None, clock=_clock_seq())
    assert rc == 0
    assert seen["argv"] == ["echo", "hi"]
