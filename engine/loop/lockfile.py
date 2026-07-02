"""Single-flight PID lockfile — the ONE concurrency guard shared by every driver.

Previously duplicated: :mod:`loop.core` guarded a state dir with a file named ``lock``, while
:mod:`loop.bmad.driver` guarded the SAME kind of state dir with a file named ``bmad-lock`` — two
different filenames meant a generic-loop run and a BMAD run launched against the same state dir
(or the same repo) could never see each other's live lock and would race. :mod:`loop.qa.discover`
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

from loop.logio import read_text, write_text

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

    A stale lock (its pid is gone, or is already OUR pid from a re-entrant call) is reclaimed.
    """
    if lock_path.exists():
        try:
            existing = int((read_text(lock_path) or "0").strip() or "0")
        except ValueError:
            existing = 0
        if existing and existing != os.getpid() and pid_alive(existing):
            return False
    write_text(lock_path, str(os.getpid()))
    return True


def release_lock(lock_path: Path) -> None:
    """Remove ``lock_path`` iff it still holds OUR pid. Best-effort — never raises."""
    try:
        if lock_path.exists() and (read_text(lock_path) or "").strip() == str(os.getpid()):
            os.remove(lock_path)
    except OSError:
        pass
