"""A tiny git helper — the subprocess calls ``loop.ps1`` makes inline.

The PowerShell engine shells out to ``git`` for a handful of operations: detect a work tree,
read HEAD / the current branch, list a dirty tree (``status --porcelain``), stage+commit, and
hard-reset to a best-known-good commit. This module wraps exactly those, scoped to a ``cwd``,
so :mod:`loop.core` reads cleanly.

Every call runs through :func:`_git`, which captures output and never raises on a non-zero exit
(git's own messages are not the engine's concern) — mirroring the PowerShell ``*>$null`` /
``2>$null`` redirections. ``cwd`` is always explicit so the engine can drive the USER's repo.
"""

from __future__ import annotations

import subprocess


def _git(args: list[str], cwd) -> subprocess.CompletedProcess:
    """Run ``git <args>`` in ``cwd``, capturing stdout+stderr, never raising on failure."""
    return subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        capture_output=True,
        text=True,
    )


def is_repo(cwd) -> bool:
    """True when ``cwd`` is inside a git work tree (port of the ``rev-parse`` guard ~385)."""
    r = _git(["rev-parse", "--is-inside-work-tree"], cwd)
    return r.returncode == 0 and r.stdout.strip() == "true"


def head(cwd) -> str | None:
    """Current HEAD commit sha (``git rev-parse HEAD``), or ``None`` outside a repo."""
    r = _git(["rev-parse", "HEAD"], cwd)
    return r.stdout.strip() if r.returncode == 0 and r.stdout.strip() else None


def current_branch(cwd) -> str | None:
    """Current branch name (``git rev-parse --abbrev-ref HEAD``), or ``None``."""
    r = _git(["rev-parse", "--abbrev-ref", "HEAD"], cwd)
    return r.stdout.strip() if r.returncode == 0 and r.stdout.strip() else None


def is_dirty(cwd) -> bool:
    """True when the work tree has staged or unstaged changes (``status --porcelain``)."""
    r = _git(["status", "--porcelain"], cwd)
    return bool(r.stdout.strip())


def add_all(cwd) -> None:
    """Stage everything (``git add -A``)."""
    _git(["add", "-A"], cwd)


def commit(cwd, message: str) -> None:
    """Commit the staged tree with ``message`` (``git commit -q -m``)."""
    _git(["commit", "-q", "-m", message], cwd)


def reset_hard(cwd, commit_sha: str) -> None:
    """Hard-reset the work tree to ``commit_sha`` (``git reset --hard <sha>``)."""
    _git(["reset", "--hard", commit_sha], cwd)
