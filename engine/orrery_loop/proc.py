"""Cross-platform process spawn + process-TREE kill.

The Python replacement for the PowerShell harness's ``taskkill /T /F`` /
``Invoke-ClaudeExecute`` timeout-and-kill (loop.ps1 ~237-261). Authorized local
dev-tool plumbing for a test-runner harness on the user's own repo.

Design goals (parity with the PowerShell source, but portable):

- :func:`run_with_timeout` spawns ``argv`` (a list — NO shell) and, when it overruns
  ``timeout_sec``, kills the WHOLE process tree, sets ``timed_out=True``, and returns
  whatever output was captured. ``timeout_sec <= 0`` means UNBOUNDED (preserves the
  pre-timeout behavior of just waiting).
- :func:`kill_tree` walks descendants with **psutil** on both OSes (graceful terminate,
  brief wait, then force-kill survivors). On POSIX the process is spawned in its own
  session/group so a group signal is also delivered; on Windows it gets a new process
  group. The psutil tree walk is the unified, authoritative mechanism on both.
- :func:`kill_by_name` tree-kills processes whose name matches (case-insensitive) and
  returns the count — used later to reap stray dev-server / chrome processes. It guards
  against killing the current process or any of its ancestors.
"""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass

import psutil

# Brief grace window (seconds) between graceful terminate() and force kill().
_GRACE_SEC = 3.0


@dataclass
class ProcResult:
    returncode: int
    stdout: str
    stderr: str
    timed_out: bool = False


def _self_and_ancestor_pids() -> set[int]:
    """PIDs of the current process and its whole ancestor chain — never to be killed."""
    protected: set[int] = set()
    try:
        proc: psutil.Process | None = psutil.Process(os.getpid())
    except psutil.Error:
        return {os.getpid()}
    guard = 0
    while proc is not None and guard < 64:
        protected.add(proc.pid)
        try:
            proc = proc.parent()
        except psutil.Error:
            break
        guard += 1
    return protected


def kill_tree(pid: int, *, include_parent: bool = True) -> None:
    """Kill a process and (recursively) all of its descendants.

    Graceful first: ``terminate()`` every member, wait briefly, then ``kill()`` whatever
    is still alive. Walks the tree with psutil so it behaves the same on Windows and
    POSIX. Missing / already-dead processes are ignored. A non-existent ``pid`` is a no-op.
    """
    try:
        parent = psutil.Process(pid)
    except psutil.Error:
        return  # already gone / never existed

    try:
        children = parent.children(recursive=True)
    except psutil.Error:
        children = []

    targets: list[psutil.Process] = list(children)
    if include_parent:
        targets.append(parent)

    # Phase 1: ask politely.
    for proc in targets:
        try:
            proc.terminate()
        except psutil.Error:
            pass

    # Wait for the polite request to take effect.
    _, alive = psutil.wait_procs(targets, timeout=_GRACE_SEC)

    # Phase 2: force-kill survivors.
    for proc in alive:
        try:
            proc.kill()
        except psutil.Error:
            pass
    psutil.wait_procs(alive, timeout=_GRACE_SEC)


def _group_kwargs() -> dict:
    """``Popen`` kwargs that put a child in its own process group/session.

    POSIX: ``start_new_session`` so a group-level signal (``os.killpg``) reaches grandchildren.
    Windows: ``CREATE_NEW_PROCESS_GROUP`` so the child gets a distinct group. Shared by every
    spawn path in this module (:func:`run_with_timeout`, :func:`spawn_tree`) so the WHOLE tree
    is always killable via :func:`kill_tree`, regardless of which one launched it.
    """
    if os.name == "nt":
        return {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
    return {"start_new_session": True}


def spawn_tree(argv, *, cwd=None, env=None) -> subprocess.Popen:
    """Spawn ``argv`` as a detached, killable process tree — NO capture, inherits stdio.

    For long-running supervisory callers (:mod:`orrery_loop.supervise`) that need to ``.wait()`` on the
    child themselves rather than go through the blocking, output-capturing
    :func:`run_with_timeout`. Placed in its own process group/session exactly like
    ``run_with_timeout`` so :func:`kill_tree` reaches its whole descendant tree.
    """
    return subprocess.Popen(list(argv), cwd=cwd, env=env, **_group_kwargs())


def run_with_timeout(
    argv,
    *,
    cwd=None,
    timeout_sec: float = 0,
    env=None,
) -> ProcResult:
    """Spawn ``argv`` (a list, no shell) and capture its output.

    ``timeout_sec <= 0`` waits UNBOUNDED. On timeout the WHOLE process tree is killed,
    ``timed_out`` is set, and whatever output was captured so far is returned.
    """
    popen_kwargs: dict = {
        "cwd": cwd,
        "env": env,
        "stdout": subprocess.PIPE,
        "stderr": subprocess.PIPE,
        # decode to str; tolerate non-UTF8 bytes rather than raising.
        "universal_newlines": True,
        "encoding": "utf-8",
        "errors": "replace",
        **_group_kwargs(),
    }

    proc = subprocess.Popen(argv, **popen_kwargs)

    timeout = timeout_sec if (timeout_sec and timeout_sec > 0) else None
    timed_out = False
    try:
        stdout, stderr = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        timed_out = True
        # Best effort group-signal on POSIX (reaches the whole session), then the
        # unified psutil tree-kill on both OSes (authoritative).
        if os.name != "nt":
            try:
                os.killpg(os.getpgid(proc.pid), 15)  # SIGTERM the group
            except (OSError, ProcessLookupError):
                pass
        kill_tree(proc.pid, include_parent=True)
        # Drain whatever the child managed to emit before it was killed.
        try:
            stdout, stderr = proc.communicate(timeout=_GRACE_SEC)
        except (subprocess.TimeoutExpired, ValueError):
            try:
                proc.kill()
            except OSError:
                pass
            stdout, stderr = "", ""
    except BaseException:
        # ANY other escape from communicate() — KeyboardInterrupt (Ctrl+C / a supervisor signal
        # mid-wait), OSError, or anything else — must not leak the child (or its whole tree)
        # running unattended. Kill it first, THEN propagate the original exception so the caller
        # (cli.py) can still report/exit cleanly; we never swallow the exception here.
        if os.name != "nt":
            try:
                os.killpg(os.getpgid(proc.pid), 15)  # SIGTERM the group
            except (OSError, ProcessLookupError):
                pass
        kill_tree(proc.pid, include_parent=True)
        raise

    return ProcResult(
        returncode=proc.returncode if proc.returncode is not None else -1,
        stdout=stdout or "",
        stderr=stderr or "",
        timed_out=timed_out,
    )


def kill_by_name(name: str, *, within_dir: str | None = None) -> int:
    """Tree-kill every process whose name matches ``name`` (case-insensitive).

    Returns the number of matching root processes killed. Guards against killing the
    current process or any of its ancestors (so reaping "python"/"node" can't suicide the
    harness). Matches on the executable name with and without a trailing ``.exe`` so a
    caller can pass ``"node"`` on Windows where the image is ``node.exe``.

    When ``within_dir`` is given, ONLY processes whose working directory resolves under
    ``within_dir`` are killed. This scopes a dev-server teardown's "node"/"chrome" reap to
    the project under test, so it can NOT collateral-kill an unrelated same-named process —
    e.g. the Orrery app's own Vite dev server (a ``node`` process whose cwd is the orrery
    dir) when the loop runs INSIDE Orrery. A process whose cwd cannot be read is SKIPPED
    (conservative: never kill what we can't confirm is in scope).
    """
    if not name:
        return 0
    wanted = name.strip().lower()
    wanted_exe = wanted if wanted.endswith(".exe") else wanted + ".exe"
    protected = _self_and_ancestor_pids()
    scope = os.path.normcase(os.path.abspath(within_dir)) if within_dir else None

    killed = 0
    for proc in psutil.process_iter(["name", "pid"]):
        try:
            pname = (proc.info.get("name") or "").lower()
        except psutil.Error:
            continue
        if pname != wanted and pname != wanted_exe:
            continue
        if proc.pid in protected:
            continue  # never kill ourselves / an ancestor
        if scope is not None:
            try:
                pcwd = proc.cwd()
            except (psutil.Error, OSError):
                continue  # cwd unreadable -> can't confirm scope -> leave it alone
            norm = os.path.normcase(os.path.abspath(pcwd))
            if norm != scope and not norm.startswith(scope + os.sep):
                continue
        kill_tree(proc.pid, include_parent=True)
        killed += 1
    return killed
