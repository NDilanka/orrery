"""Cross-platform spawn + process-TREE kill coverage for :mod:`loop.proc`.

Written OS-portable (passes on Windows and POSIX). The tree-kill test spawns a REAL child
that sleeps ~30s AND spawns its OWN grandchild sleeper, runs it with a 1s timeout, then
verifies via psutil that BOTH the child and the grandchild PIDs are actually gone.
"""

from __future__ import annotations

import sys
import time

import psutil

from loop.proc import ProcResult, kill_tree, run_with_timeout

# A child program that: prints its own PID and the spawned grandchild's PID (so the test
# can verify the WHOLE tree died), then sleeps long enough that only a kill ends it.
_CHILD_SRC = """
import os, sys, subprocess, time
# spawn a grandchild sleeper so we can prove the tree (not just the child) is killed.
gc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
# emit both PIDs on one flushed line BEFORE sleeping, so the parent can read them.
print(os.getpid(), gc.pid, flush=True)
time.sleep(30)
"""


def _pid_running(pid: int) -> bool:
    """True iff a live (non-zombie) process with ``pid`` currently exists."""
    try:
        p = psutil.Process(pid)
        return p.is_running() and p.status() != psutil.STATUS_ZOMBIE
    except psutil.Error:
        return False


def _wait_gone(pids, timeout: float = 8.0) -> None:
    """Poll until none of ``pids`` is running, or ``timeout`` elapses."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if not any(_pid_running(p) for p in pids):
            return
        time.sleep(0.1)


def test_fast_command_returncode_and_stdout():
    """(1) A quick command returns the right returncode and captured stdout."""
    res = run_with_timeout(
        [sys.executable, "-c", "import sys; print('hello-proc'); sys.exit(0)"],
        timeout_sec=10,
    )
    assert isinstance(res, ProcResult)
    assert res.returncode == 0
    assert res.timed_out is False
    assert "hello-proc" in res.stdout


def test_nonzero_returncode_captured():
    """A non-zero exit is surfaced verbatim (sanity on returncode plumbing)."""
    res = run_with_timeout([sys.executable, "-c", "import sys; sys.exit(7)"], timeout_sec=10)
    assert res.returncode == 7
    assert res.timed_out is False


def test_timeout_kills_whole_tree():
    """(2) A 30s child that spawns a 30s grandchild, run with timeout_sec=1.

    Asserts ``timed_out is True`` and that BOTH the child and grandchild PIDs are gone
    shortly after (the process TREE was killed, not just the direct child).
    """
    child_pid = None
    gc_pid = None
    try:
        res = run_with_timeout([sys.executable, "-c", _CHILD_SRC], timeout_sec=1)
        assert res.timed_out is True

        # The child flushed "<child_pid> <grandchild_pid>" before sleeping.
        first = (res.stdout or "").strip().splitlines()
        assert first, f"expected the child to print its PIDs; stdout={res.stdout!r}"
        child_pid, gc_pid = (int(x) for x in first[0].split())

        _wait_gone([child_pid, gc_pid])
        assert not _pid_running(child_pid), f"child {child_pid} survived the tree-kill"
        assert not _pid_running(gc_pid), f"grandchild {gc_pid} survived the tree-kill"
    finally:
        # Clean up any survivor so a failed assertion never leaks a 30s sleeper.
        for pid in (child_pid, gc_pid):
            if pid is not None and _pid_running(pid):
                kill_tree(pid, include_parent=True)


def test_timeout_zero_runs_to_completion():
    """(3) ``timeout_sec=0`` (UNBOUNDED) runs a quick command to completion, no timeout."""
    res = run_with_timeout(
        [sys.executable, "-c", "import time; time.sleep(0.2); print('done0')"],
        timeout_sec=0,
    )
    assert res.timed_out is False
    assert res.returncode == 0
    assert "done0" in res.stdout
