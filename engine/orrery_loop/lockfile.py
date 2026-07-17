"""Single-flight PID lockfile — the ONE concurrency guard shared by every driver.

Previously duplicated: :mod:`orrery_loop.core` guarded a state dir with a file named ``lock``, while
:mod:`orrery_loop.bmad.driver` guarded the SAME kind of state dir with a file named ``bmad-lock`` — two
different filenames meant a generic-loop run and a BMAD run launched against the same state dir
(or the same repo) could never see each other's live lock and would race. :mod:`orrery_loop.qa.discover`
took no lock at all.

Every driver now acquires/releases through this module with ONE filename (:data:`LOCK_NAME` =
``"lock"``), so any two of ``loop`` / ``loop-bmad`` / ``loop-qa`` racing the same state dir are
correctly serialized regardless of which driver either one is.

Migration note: a leftover ``bmad-lock`` file from an older BMAD run is simply ignored — this
module never reads or writes that name. It is a harmless stale file; delete it manually if it
bothers you.
"""

from __future__ import annotations

import os
from pathlib import Path

from orrery_loop.logio import read_text, write_text

LOCK_NAME = "lock"


def pid_alive(pid: int) -> bool:
    """True if a process with ``pid`` is currently alive (best-effort, cross-platform)."""
    try:
        import psutil

        return psutil.pid_exists(pid)
    except Exception:
        try:
            os.kill(pid, 0)
        except OSError:
            return False
        except Exception:
            return True
        return True


def acquire_lock(lock_path: Path) -> bool:
    """Write a pid lockfile; refuse (return False) if a LIVE lock already exists.

    The create is atomic — ``os.open`` with ``O_CREAT | O_EXCL`` — so two simultaneous starts
    can never both win the exists()-then-write race that this replaced: exactly one caller
    creates the file, the loser gets ``FileExistsError``.

    A stale lock (its pid is gone, or is already OUR pid from a re-entrant call) is reclaimed
    on the ``FileExistsError`` path. Reclaim is best-effort and NOT fully race-free: we confirm
    staleness by reading the recorded pid, then ``os.replace`` our pid in atomically. Two
    processes both reclaiming the SAME stale lock at once can each succeed the replace (last
    writer wins), so reclaim is only safe against a genuinely dead holder — the common case.
    """
    try:
        fd = os.open(os.fspath(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
    except FileExistsError:
        # A lockfile already exists. Reclaim it only if the recorded pid is dead (or is ours).
        try:
            existing = int((read_text(lock_path) or "0").strip() or "0")
        except ValueError:
            existing = 0
        if existing and existing != os.getpid() and pid_alive(existing):
            return False  # a LIVE holder — refuse.
        # Stale (dead pid / ours / unreadable): reclaim by atomically replacing the file's pid.
        write_text(lock_path, str(os.getpid()))
        return True
    else:
        try:
            os.write(fd, str(os.getpid()).encode("utf-8"))
        finally:
            os.close(fd)
        return True


def release_lock(lock_path: Path) -> None:
    """Remove ``lock_path`` iff it still holds OUR pid. Best-effort — never raises."""
    try:
        if lock_path.exists() and (read_text(lock_path) or "").strip() == str(os.getpid()):
            os.remove(lock_path)
    except OSError:
        pass
