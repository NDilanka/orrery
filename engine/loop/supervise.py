"""Built-in loop supervisor — restarts a driver command on failure. Replaces ``supervise.ps1``.

Wraps a long-running loop invocation (``loop-bmad ...``, ``loop ...``) so an unattended
overnight run survives a single crashed/flaky-gated iteration: spawn the command, wait for it
to exit, and on a NONZERO exit restart it — unless (a) a ``STOP`` file exists in the state dir,
(b) a ``STOP-SUPERVISOR`` sentinel exists in the state dir, or (c) the thrash guard trips (too
many restarts inside a rolling time window — the classic crash-loop signature). A clean exit
(code 0) ends supervision immediately, no restart.

This generalizes ``orrery/loops/bmad/supervise.ps1`` (a project-specific PowerShell polling
loop written because BMAD's checkpoint ``resume`` string used to drop ``--loop-json`` on
restart) into a project-agnostic console command any ``loop.json``-driven project can point at
ANY command line, not just ``loop-bmad``.

Everything effectful is injected (``spawn``, ``sleep``, ``clock``, ``emit``, ``log_line``) so
:func:`supervise` is fully unit-testable with tiny fake/real subprocesses and no real waits —
follows the same injection pattern as :mod:`loop.heartbeat`.
"""

from __future__ import annotations

import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Sequence

from loop.events import supervisor_restart_event
from loop.logio import append_event
from loop.proc import kill_tree, spawn_tree

STOP_NAME = "STOP"
STOP_SUPERVISOR_NAME = "STOP-SUPERVISOR"

_DEFAULT_MAX_RESTARTS = 5
_DEFAULT_WINDOW_MIN = 90.0
_DEFAULT_POLL_SEC = 5.0


@dataclass
class SupervisorConfig:
    """Inputs for one supervised run."""

    state_dir: str
    command: Sequence[str]
    max_restarts: int = _DEFAULT_MAX_RESTARTS
    window_min: float = _DEFAULT_WINDOW_MIN
    poll_sec: float = _DEFAULT_POLL_SEC


def _default_spawn(argv: Sequence[str]) -> subprocess.Popen:
    return spawn_tree(argv)


def supervise(
    config: SupervisorConfig,
    *,
    spawn: Callable[[Sequence[str]], subprocess.Popen] = _default_spawn,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
    emit: Callable[[dict], None] | None = None,
    log_line: Callable[[str], None] | None = None,
) -> int:
    """Run ``config.command``, restarting it on a nonzero exit. Returns the LAST exit code seen.

    Stops (returns, no further restart) when: the child exits ``0``; a ``STOP`` file exists in
    the state dir; a ``STOP-SUPERVISOR`` sentinel exists in the state dir; or the thrash guard
    trips (more than ``max_restarts`` restarts within the rolling ``window_min`` window).

    Each restart appends a ``supervisor-restart`` event to ``<state_dir>/log.jsonl`` (via
    ``emit``, default :func:`loop.logio.append_event`) and a human line to
    ``<state_dir>/supervisor.log`` (via ``log_line``, default: append to that file). A
    ``KeyboardInterrupt`` (or any other exception) while the child is running kills the child's
    WHOLE process tree before propagating — the supervisor itself never orphans what it spawned.
    """
    state = Path(config.state_dir)
    state.mkdir(parents=True, exist_ok=True)
    stop_flag = state / STOP_NAME
    stop_supervisor_flag = state / STOP_SUPERVISOR_NAME
    log_path = state / "log.jsonl"
    supervisor_log_path = state / "supervisor.log"

    _emit = emit or (lambda ev: append_event(log_path, ev))

    def _log(line: str) -> None:
        if log_line is not None:
            log_line(line)
        else:
            ts = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            try:
                with open(supervisor_log_path, "a", encoding="utf-8") as fh:
                    fh.write(f"[{ts}] {line}\n")
            except OSError:
                pass
        print(f"[supervise] {line}")

    restart_times: list[float] = []
    attempt = 0
    rc = 0
    command = list(config.command)
    _log(f"supervisor started — command: {' '.join(command)}")

    while True:
        attempt += 1
        _log(f"launching (attempt {attempt})")
        proc = spawn(command)
        try:
            rc = proc.wait()
        except BaseException:
            # A KeyboardInterrupt (Ctrl-C to the supervisor) or any other escape from wait()
            # must not leave the wrapped driver (and ITS whole tree — the loop it launched)
            # running unattended. Kill it, then propagate.
            kill_tree(proc.pid, include_parent=True)
            raise
        _log(f"exited with code {rc}")

        if rc == 0:
            _log("clean exit (0) — supervision complete, no restart.")
            return 0

        if stop_flag.exists():
            _log(f"STOP flag present at {stop_flag} — not restarting.")
            return rc

        if stop_supervisor_flag.exists():
            _log(f"STOP-SUPERVISOR sentinel present at {stop_supervisor_flag} — not restarting.")
            return rc

        now = clock()
        restart_times.append(now)
        window_sec = max(0.0, config.window_min) * 60.0
        restart_times[:] = [t for t in restart_times if now - t <= window_sec]
        if len(restart_times) > max(0, config.max_restarts):
            _log(
                f"thrash guard tripped: {len(restart_times)} restarts within "
                f"{config.window_min} min (max {config.max_restarts}) — not restarting. "
                "Investigate the log; something is failing repeatedly."
            )
            return rc

        _emit(
            supervisor_restart_event(
                attempt=attempt,
                exit_code=rc,
                restarts_in_window=len(restart_times),
                max_restarts=config.max_restarts,
            )
        )
        _log(
            f"restarting ({len(restart_times)}/{config.max_restarts} restarts in the last "
            f"{config.window_min}min window)..."
        )
        sleep(config.poll_sec)
