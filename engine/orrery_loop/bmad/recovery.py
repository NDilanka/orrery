"""Crash-recovery predicate — port of ``First-UnmergedDone`` (``bmad-loop.ps1`` ~214-237).

A ``done`` story whose ``feat/story-<key>`` branch is a CLEAN DESCENDANT of the current
``<merge_base>`` HEAD with extra commits on top = dev'd + code-reviewed but NOT yet
browser-smoked / merged (a graceful stop BEFORE smoke, or a crash between review and
merge). The driver resumes such a story at smoke+merge.

The safety of the resume rests on the merge-base test, ported verbatim:

    merge-base(<merge_base>, feat/story-<key>) == rev-parse(<merge_base>)

i.e. the branch's fork point IS the current ``<merge_base>`` HEAD, and the branch is ahead
of it. That EXCLUDES stale/old story branches and already-(squash-)merged branches (whose
merge-base with ``<merge_base>`` is no longer its current HEAD).

Pure-ish: the only effect is read-only ``git`` via :mod:`orrery_loop.gitutil` (extended here with
the rev-list / merge-base calls the PS source makes). ANY git error -> ``False`` (the
conservative no-resume path).
"""

from __future__ import annotations

from collections.abc import Callable

from orrery_loop import gitutil
from orrery_loop.bmad.sprint import Story


def _rev_parse(ref: str, *, repo) -> str | None:
    """``git rev-parse <ref>`` (full sha), or ``None`` when the ref doesn't resolve."""
    r = gitutil._git(["rev-parse", "--verify", "--quiet", ref], repo)
    sha = (r.stdout or "").strip()
    return sha if r.returncode == 0 and sha else None


def _commits_ahead(base: str, branch: str, *, repo) -> int:
    """``git rev-list <base>..<branch> --count`` — commits on ``branch`` not on ``base``."""
    r = gitutil._git(["rev-list", f"{base}..{branch}", "--count"], repo)
    if r.returncode != 0:
        return 0
    txt = (r.stdout or "").strip().splitlines()
    if not txt:
        return 0
    try:
        return int(txt[0].strip())
    except ValueError:
        return 0


def _merge_base(a: str, b: str, *, repo) -> str | None:
    """``git merge-base <a> <b>`` (the fork point sha), or ``None`` on any error."""
    r = gitutil._git(["merge-base", a, b], repo)
    sha = (r.stdout or "").strip()
    return sha if r.returncode == 0 and sha else None


def is_unmerged_done(story: Story, *, repo, merge_base: str = "develop") -> bool:
    """Port of ``First-UnmergedDone``'s per-story test (returns the boolean predicate).

    True iff ``story`` is ``done`` AND its ``feat/story-<key>`` branch exists, is AHEAD of
    ``<merge_base>`` HEAD by >=1 commit, and forks EXACTLY off the current ``<merge_base>``
    HEAD (``merge-base == rev-parse(merge_base)``). Any git failure (missing merge_base,
    missing branch, unparseable counts) yields ``False`` — the safe no-resume default.
    """
    if story.status != "done":
        return False

    base_head = _rev_parse(merge_base, repo=repo)
    if not base_head:
        return False

    branch = f"feat/story-{story.key}"
    if _rev_parse(branch, repo=repo) is None:
        return False

    if _commits_ahead(merge_base, branch, repo=repo) <= 0:
        return False

    mb = _merge_base(merge_base, branch, repo=repo)
    return bool(mb) and mb == base_head


def unmerged_done_predicate(
    *, repo, merge_base: str = "develop"
) -> Callable[[Story], bool]:
    """Build the ``is_unmerged_done`` predicate the driver passes to ``select_actionable``.

    Binds ``repo`` + ``merge_base`` so the result is the single-arg ``Story -> bool``
    callable :func:`orrery_loop.bmad.sprint.select_actionable` expects.
    """
    return lambda story: is_unmerged_done(story, repo=repo, merge_base=merge_base)
