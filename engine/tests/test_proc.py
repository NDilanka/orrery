"""Cross-platform spawn + process-TREE kill coverage for :mod:`loop.proc`.

Written OS-portable (passes on Windows and POSIX). The tree-kill test spawns a REAL child
that sleeps ~30s AND spawns its OWN grandchild sleeper, runs it with a 1s timeout, then
verifies via psutil that BOTH the child and the grandchild PIDs are actually gone.
"""

from __future__ import annotations

import subprocess
import sys
import time

import psutil

from loop.proc import ProcResult, kill_tree, run_with_timeout, spawn_tree

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


def test_kill_by_name_scoped_to_within_dir(monkeypatch):
    """``within_dir`` scopes the by-name reap to one project tree (hermetic, no real procs).

    A ``node`` process whose cwd is OUTSIDE ``within_dir`` (e.g. Orrery's own Vite dev
    server) is SPARED; one INSIDE is killed; one with an unreadable cwd is left alone; a
    differently-named process is ignored. Guards the fix that stopped a smoke teardown from
    collateral-killing Orrery's dev server.
    """
    import os

    from loop import proc as procmod

    proj = os.path.abspath(os.path.join("X", "webapp"))
    inside = os.path.join(proj, "sub")
    outside = os.path.abspath(os.path.join("X", "orrery"))

    class FakeProc:
        def __init__(self, pid, name, cwd, *, cwd_raises=False):
            self.pid = pid
            self.info = {"name": name, "pid": pid}
            self._cwd = cwd
            self._cwd_raises = cwd_raises

        def cwd(self):
            if self._cwd_raises:
                raise OSError("access denied")
            return self._cwd

    procs = [
        FakeProc(101, "node.exe", inside),                  # in scope  -> killed
        FakeProc(202, "node.exe", outside),                 # out scope -> spared (Orrery)
        FakeProc(303, "node.exe", None, cwd_raises=True),   # unreadable -> spared
        FakeProc(404, "python.exe", inside),                # wrong name -> ignored
    ]
    killed_pids: list[int] = []
    monkeypatch.setattr(procmod.psutil, "process_iter", lambda attrs=None: list(procs))
    monkeypatch.setattr(procmod, "_self_and_ancestor_pids", lambda: set())
    monkeypatch.setattr(procmod, "kill_tree", lambda pid, **kw: killed_pids.append(pid))

    n = procmod.kill_by_name("node", within_dir=proj)

    assert killed_pids == [101]
    assert n == 1


def test_run_with_timeout_kills_tree_on_keyboard_interrupt(monkeypatch):
    """Task 2: ANY exception escaping communicate() — not just TimeoutExpired — must kill the
    WHOLE child tree before propagating. A KeyboardInterrupt hitting the harness while blocked
    in run_with_timeout must not leak a hung child process."""
    real_communicate = subprocess.Popen.communicate
    calls = {"n": 0}

    def flaky_communicate(self, *a, **kw):
        calls["n"] += 1
        if calls["n"] == 1:
            raise KeyboardInterrupt()
        return real_communicate(self, *a, **kw)

    monkeypatch.setattr(subprocess.Popen, "communicate", flaky_communicate)

    pid_holder: dict = {}
    real_kill_tree = kill_tree

    def spying_kill_tree(pid, **kw):
        pid_holder["pid"] = pid
        return real_kill_tree(pid, **kw)

    monkeypatch.setattr("loop.proc.kill_tree", spying_kill_tree)

    raised = False
    try:
        run_with_timeout([sys.executable, "-c", "import time; time.sleep(30)"], timeout_sec=10)
    except KeyboardInterrupt:
        raised = True
    assert raised is True, "the original exception must propagate, never be swallowed"
    assert "pid" in pid_holder, "kill_tree must be called on the escaping-exception path"

    # confirm the real child is actually gone (kill_tree really worked, not just called).
    _wait_gone([pid_holder["pid"]])
    assert not _pid_running(pid_holder["pid"]), "child survived a KeyboardInterrupt mid-communicate"


def test_spawn_tree_returns_a_killable_detached_process():
    """spawn_tree (used by loop.supervise) spawns without capturing output and can be
    kill_tree'd like any run_with_timeout child."""
    proc = spawn_tree([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        assert _pid_running(proc.pid)
    finally:
        kill_tree(proc.pid, include_parent=True)
        _wait_gone([proc.pid])
        assert not _pid_running(proc.pid)


def test_timeout_zero_runs_to_completion():
    """(3) ``timeout_sec=0`` (UNBOUNDED) runs a quick command to completion, no timeout."""
    res = run_with_timeout(
        [sys.executable, "-c", "import time; time.sleep(0.2); print('done0')"],
        timeout_sec=0,
    )
    assert res.timed_out is False
    assert res.returncode == 0
    assert "done0" in res.stdout
