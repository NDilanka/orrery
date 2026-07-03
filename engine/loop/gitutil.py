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
        # Decode as UTF-8 and tolerate stray bytes. Git output can carry non-cp1252
        # bytes (branch names, file paths, commit text); the Windows default text
        # decode (cp1252) raises UnicodeDecodeError in the reader thread and kills
        # the call. Mirrors proc.run_with_timeout / gate._run_command.
        encoding="utf-8",
        errors="replace",
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


def discard_worktree(cwd) -> None:
    """Drop all uncommitted changes to tracked files (``git reset --hard HEAD``).

    Restores tracked files to the current HEAD (untracked files are left untouched). Used right
    before a branch switch where the only thing that can be dirty is throwaway, regenerated
    output — e.g. a gate that runs ``codegen`` and re-emits tracked generated files, or
    line-ending normalization — which would otherwise make ``git checkout`` refuse with "your
    local changes would be overwritten by checkout". Real work is committed by the caller before
    this point, so nothing of value is lost. Never raises (like :func:`_git`)."""
    _git(["reset", "--hard", "HEAD"], cwd)


def diff_name_only(cwd, base: str, *, max_files: int = 40) -> list[str]:
    """Files changed on this branch vs ``base`` (e.g. a story's ``baseline_commit``), capped.

    Prefers the three-dot ``base...HEAD`` form (changes introduced ON this branch since it diverged
    from ``base``); falls back to a plain ``base`` diff if that errors. Returns at most ``max_files``
    repo-relative paths; ``[]`` on any git failure or a falsy ``base``. Never raises (like
    :func:`_git`). Used to tell the browser-smoke agent WHICH surfaces this story actually changed,
    so it drives the story's real code instead of a generic health check.
    """
    if not base:
        return []
    r = _git(["diff", "--name-only", f"{base}...HEAD"], cwd)
    if r.returncode != 0:
        r = _git(["diff", "--name-only", base], cwd)
    if r.returncode != 0:
        return []
    files = [ln.strip() for ln in (r.stdout or "").splitlines() if ln.strip()]
    return files[:max_files]


def diff_name_status(cwd, base: str) -> list[tuple[str, str]]:
    """``git diff --name-status -M <base> HEAD`` -> list of ``(status, path)`` pairs.

    ``status`` is git's single-letter code: ``A`` added, ``M`` modified, ``D`` deleted,
    ``R`` renamed (a rename line is ``R<score>\\told\\tnew`` — reported here as ``("R", new)``,
    since a renamed test file is NOT a deletion). ``-M`` enables rename detection so an in-place
    move isn't miscounted as a delete+add. Returns ``[]`` on any git failure or a falsy ``base``;
    never raises (like :func:`_git`). Used by the driver's test-integrity check to spot deleted /
    in-place-edited PRE-EXISTING test files (survives crash/resume — no state file, git is truth).
    """
    if not base:
        return []
    r = _git(["diff", "--name-status", "-M", base, "HEAD"], cwd)
    if r.returncode != 0:
        return []
    out: list[tuple[str, str]] = []
    for ln in (r.stdout or "").splitlines():
        parts = ln.rstrip("\n").split("\t")
        if len(parts) < 2:
            continue
        code = parts[0].strip()
        letter = code[:1].upper() if code else ""
        # For a rename (R<score>) / copy (C<score>), the DESTINATION path is the last column.
        path = parts[-1].strip()
        if not path:
            continue
        out.append((letter, path))
    return out
