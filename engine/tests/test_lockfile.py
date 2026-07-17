"""Coverage for the shared single-flight lockfile (:mod:`orrery_loop.lockfile`) — Task 4.

``orrery_loop.core`` and ``orrery_loop.bmad.driver`` used to each duplicate this pid-lockfile logic under
DIFFERENT filenames ("lock" vs "bmad-lock"), so the two drivers could race the same state dir
undetected. This module is the single, shared implementation both (and ``orrery_loop.qa.discover``)
now use.
"""

from __future__ import annotations

import os

from orrery_loop import lockfile


def test_acquire_writes_own_pid_when_no_lock_exists(tmp_path):
    lock_path = tmp_path / "lock"
    assert lockfile.acquire_lock(lock_path) is True
    assert lock_path.read_text(encoding="utf-8").strip() == str(os.getpid())


def test_acquire_reclaims_a_stale_lock_whose_pid_is_dead(tmp_path, monkeypatch):
    lock_path = tmp_path / "lock"
    lock_path.write_text("999999999", encoding="utf-8")  # a pid that isn't alive
    monkeypatch.setattr(lockfile, "pid_alive", lambda pid: False)
    assert lockfile.acquire_lock(lock_path) is True
    assert lock_path.read_text(encoding="utf-8").strip() == str(os.getpid())


def test_acquire_refuses_when_a_different_live_pid_holds_it(tmp_path, monkeypatch):
    lock_path = tmp_path / "lock"
    other_pid = os.getpid() + 1
    lock_path.write_text(str(other_pid), encoding="utf-8")
    monkeypatch.setattr(lockfile, "pid_alive", lambda pid: True)
    assert lockfile.acquire_lock(lock_path) is False
    # the live lock is left untouched (not overwritten by the refused acquirer)
    assert lock_path.read_text(encoding="utf-8").strip() == str(other_pid)


def test_acquire_is_reentrant_for_our_own_pid(tmp_path):
    lock_path = tmp_path / "lock"
    assert lockfile.acquire_lock(lock_path) is True
    # a second acquire by the SAME process succeeds (the lock already names us)
    assert lockfile.acquire_lock(lock_path) is True


def test_second_acquire_fails_while_first_holds_then_stale_reclaim(tmp_path, monkeypatch):
    # Atomic O_CREAT|O_EXCL acquire: with the lockfile already present and its holder LIVE, a
    # second (different-pid) start is refused — it can't win the old exists()-then-write race.
    lock_path = tmp_path / "lock"

    monkeypatch.setattr(os, "getpid", lambda: 111)
    assert lockfile.acquire_lock(lock_path) is True
    assert lock_path.read_text(encoding="utf-8").strip() == "111"

    # a different, LIVE process tries to acquire the same lock -> refused, holder untouched.
    monkeypatch.setattr(os, "getpid", lambda: 222)
    monkeypatch.setattr(lockfile, "pid_alive", lambda pid: pid == 111)
    assert lockfile.acquire_lock(lock_path) is False
    assert lock_path.read_text(encoding="utf-8").strip() == "111"

    # once the holder dies, the stale lock is reclaimed by the next acquirer.
    monkeypatch.setattr(lockfile, "pid_alive", lambda pid: False)
    assert lockfile.acquire_lock(lock_path) is True
    assert lock_path.read_text(encoding="utf-8").strip() == "222"


def test_release_removes_our_own_lock_but_not_someone_elses(tmp_path):
    lock_path = tmp_path / "lock"
    lockfile.acquire_lock(lock_path)
    assert lock_path.exists()
    lockfile.release_lock(lock_path)
    assert not lock_path.exists()

    # a lock owned by a DIFFERENT pid is left alone by release_lock (never steals a release)
    other_pid = os.getpid() + 1
    lock_path.write_text(str(other_pid), encoding="utf-8")
    lockfile.release_lock(lock_path)
    assert lock_path.read_text(encoding="utf-8").strip() == str(other_pid)


def test_release_on_missing_lock_is_a_safe_no_op(tmp_path):
    lock_path = tmp_path / "does-not-exist" / "lock"
    lockfile.release_lock(lock_path)  # must not raise


def test_pid_alive_true_for_self_false_for_bogus_pid():
    assert lockfile.pid_alive(os.getpid()) is True
    assert lockfile.pid_alive(999999999) is False


def test_lock_name_is_shared_single_name():
    assert lockfile.LOCK_NAME == "lock"
