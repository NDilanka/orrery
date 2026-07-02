"""Liveness heartbeat — writes ``<stateDir>/activity.json`` while a long agent step runs.

The event log (``log.jsonl``) only gains a line at PHASE BOUNDARIES (dev-gate, pr-created, …),
so during the long ``dev-story`` agent run — one continuous ``claude -p`` call that can last tens
of minutes — nothing is appended and a watching UI can't tell the loop apart from a hung one.

This module fills that gap with a *state* file, not an event: ``activity.json`` is a single object
OVERWRITTEN in place every few seconds (like ``checkpoint.json``), so it never floods the event
log or its replay cap. A background thread wraps each blocking agent call (see
``ResilientRunner.run``) and re-stamps the file with the current phase, story, elapsed seconds, a
cheap "files changed" count, and a fresh timestamp. The orrery Tauri app reads it and renders a
real freshness indicator ("● dev-story · 5-2 · 4m12s · 3 files"), going stale then idle if the
beats stop.

Everything external is injected (the clock, the sleep, the dirty-count function) so the thread is
unit-testable without a real git repo or real wall-clock waits. Writes are atomic (temp +
``os.replace``) so a reader never sees a half-written file.
"""

from __future__ import annotations

import json
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable


def _iso(dt: datetime) -> str:
    """ISO-8601 in UTC with a ``Z`` suffix (matches ``events._iso`` / ``checkpoint.updatedAt``)."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def write_activity(path, payload: dict[str, Any]) -> None:
    """Atomically overwrite ``path`` with ``payload`` as compact JSON (temp file + ``os.replace``).

    Best-effort: any ``OSError`` (a transient Windows lock on the temp/rename, a missing dir mid
    tear-down) is swallowed — a dropped heartbeat is cosmetic, never a reason to crash a phase.
    """
    p = Path(path)
    tmp = p.with_name(p.name + ".tmp")
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")
        os.replace(tmp, p)
    except OSError:
        try:
            tmp.unlink()
        except OSError:
            pass


def _git_dirty_count(repo) -> int:
    """Count changed files in ``repo`` (``git status --porcelain`` lines); 0 on any failure.

    A live proxy for "the agent is actually producing work": during dev-story this climbs as files
    are written. Imported lazily so importing this module never pulls in git, keeping it cheap for
    the (mock-runner) tests that inject their own ``dirty_fn``.
    """
    if repo is None:
        return 0
    try:
        from loop import gitutil

        r = gitutil._git(["status", "--porcelain"], repo)
        if r.returncode != 0:
            return 0
        return sum(1 for ln in (r.stdout or "").splitlines() if ln.strip())
    except Exception:
        return 0


class Heartbeat:
    """A background thread that re-stamps ``activity.json`` every ``interval`` seconds until stopped.

    Use as a context manager around a blocking agent call::

        with Heartbeat(path, phase="dev-story", story=key, repo=cwd, pid=os.getpid()):
            res = runner.run(...)

    A first beat is written immediately on ``__enter__`` (so the UI flips to "working" within the
    poll interval of the call starting), then every ``interval`` seconds, then once more on exit
    with the final elapsed/dirty. ``clock`` (monotonic, for elapsed), ``now`` (wall-clock, for the
    ``ts`` stamp), ``sleep_event`` (the stop wait) and ``dirty_fn`` are all injectable for tests.
    """

    def __init__(
        self,
        path,
        *,
        phase: str | None,
        story: str | None,
        repo: Any = None,
        pid: int | None = None,
        interval: float = 12.0,
        dirty_fn: Callable[[Any], int] | None = None,
        clock: Callable[[], float] | None = None,
        now: Callable[[], datetime] | None = None,
    ):
        self._path = path
        self._phase = phase
        self._story = story
        self._repo = repo
        self._pid = pid if pid is not None else os.getpid()
        self._interval = max(0.05, float(interval))  # floor guards a misconfigured 0 (busy loop)
        self._dirty_fn = dirty_fn or _git_dirty_count
        self._clock = clock or __import__("time").monotonic
        self._now = now or (lambda: datetime.now(timezone.utc))
        self._started_at = 0.0
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def _beat(self) -> None:
        write_activity(
            self._path,
            {
                "ts": _iso(self._now()),
                "phase": self._phase,
                "story": self._story,
                "elapsedSec": round(max(0.0, self._clock() - self._started_at), 1),
                "dirty": self._dirty_fn(self._repo),
                "pid": self._pid,
            },
        )

    def _loop(self) -> None:
        # wait(interval) returns True the instant stop() is set, so shutdown is prompt (no
        # lingering up-to-`interval` sleep) and a long phase still beats on schedule.
        while not self._stop.wait(self._interval):
            self._beat()

    def __enter__(self) -> "Heartbeat":
        self._started_at = self._clock()
        self._beat()  # immediate first beat
        self._thread = threading.Thread(target=self._loop, name="loop-heartbeat", daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *exc: Any) -> None:
        self._stop.set()
        t = self._thread
        if t is not None:
            t.join(timeout=2.0)
        self._beat()  # final beat with the true end-of-phase elapsed/dirty
