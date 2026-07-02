"""Coverage for ``loop.driver_shell`` — the shared lock/checkpoint/STOP lifecycle every driver
(``loop`` / ``loop-bmad`` / ``loop-qa``) now runs inside (Task 1, wave A2)."""

from __future__ import annotations

import json
import os
from pathlib import Path

from loop import lockfile
from loop.driver_shell import read_stop_request, run_driver, write_checkpoint_now


def test_run_driver_creates_state_dir_and_calls_body(tmp_path):
    state_dir = tmp_path / "state"
    seen: list[Path] = []

    def body(state: Path) -> int:
        seen.append(state)
        assert state.is_dir()
        return 0

    rc = run_driver(state_dir, guard_label="loop", body=body)
    assert rc == 0
    assert seen == [state_dir]


def test_run_driver_refuses_with_2_when_lock_live(tmp_path, monkeypatch):
    state_dir = tmp_path / "state"
    state_dir.mkdir(parents=True)
    other_pid = os.getpid() + 1
    (state_dir / lockfile.LOCK_NAME).write_text(str(other_pid), encoding="utf-8")
    monkeypatch.setattr(lockfile, "pid_alive", lambda pid: True)

    def boom(state: Path) -> int:  # pragma: no cover - must never run
        raise AssertionError("body must not run when the lock is refused")

    rc = run_driver(state_dir, guard_label="loop", body=boom)
    assert rc == 2
    # the live lock is untouched
    assert (state_dir / lockfile.LOCK_NAME).read_text(encoding="utf-8").strip() == str(other_pid)


def test_run_driver_releases_lock_on_clean_return(tmp_path):
    state_dir = tmp_path / "state"
    rc = run_driver(state_dir, guard_label="loop", body=lambda state: 0)
    assert rc == 0
    assert not (state_dir / lockfile.LOCK_NAME).exists()


def test_run_driver_releases_lock_even_when_body_raises(tmp_path):
    state_dir = tmp_path / "state"

    def boom(state: Path) -> int:
        raise RuntimeError("boom")

    try:
        run_driver(state_dir, guard_label="loop", body=boom)
    except RuntimeError:
        pass
    assert not (state_dir / lockfile.LOCK_NAME).exists()


def test_read_stop_request_absent_flag_never_honors(tmp_path):
    req = read_stop_request(tmp_path / "STOP", "story")
    assert req["honor"] is False
    assert req["mode"] is None


def test_read_stop_request_honors_at_matching_scope(tmp_path):
    flag = tmp_path / "STOP"
    flag.write_text("now", encoding="utf-8")
    req = read_stop_request(flag, "phase")
    assert req["honor"] is True
    assert req["mode"] == "now"


def test_read_stop_request_holds_story_mode_at_phase_scope(tmp_path):
    flag = tmp_path / "STOP"
    flag.write_text("story", encoding="utf-8")
    req = read_stop_request(flag, "phase")
    assert req["honor"] is False


def test_write_checkpoint_now_writes_protocol_shape(tmp_path):
    path = tmp_path / "checkpoint.json"
    write_checkpoint_now(
        path,
        stage="iter 3",
        story="2-1",
        branch="feat/x",
        merge_base="develop",
        cum_usd=1.23456,
        resume="loop --task TASK.md",
    )
    cp = json.loads(path.read_text(encoding="utf-8"))
    assert cp["stage"] == "iter 3"
    assert cp["story"] == "2-1"
    assert cp["branch"] == "feat/x"
    assert cp["mergeBase"] == "develop"
    assert cp["cumUsd"] == 1.2346  # rounded to 4dp
    assert cp["resume"] == "loop --task TASK.md"
    assert cp["updatedAt"]  # a real ISO timestamp, not ""
