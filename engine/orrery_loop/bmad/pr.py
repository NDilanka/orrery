"""Guarded ``gh`` wrappers — the ONLY network-touching functions in the BMAD driver.

Ports of the PR-create + auto-merge calls in ``bmad-loop.ps1`` (~828-861):

- :func:`create_pr`  <- ``gh pr create --base <base> --head <branch> --title .. --body ..``
- :func:`merge_pr`   <- ``gh pr merge <url> --squash --delete-branch``
- :func:`pr_state`   <- ``gh pr view <ref> --json state -q .state``

The driver calls BOTH through a tiny indirection (it imports this module and references
``pr.create_pr`` / ``pr.merge_pr`` at call time) so a test can ``monkeypatch`` them with a
stub — proving the end-to-end orchestration never spawns ``gh`` and never hits the network.
``--no-merge`` makes the driver skip :func:`merge_pr` entirely.

Both shell out via :func:`orrery_loop.proc.run_with_timeout` (no shell, argv list) and return the
trimmed stdout (the PR url for ``create``; the merge output for ``merge``). A non-zero exit
raises :class:`PrError` so the driver can stop with a diagnosis (mirrors the PS handoff).
"""

from __future__ import annotations

from orrery_loop import proc


class PrError(RuntimeError):
    """A ``gh`` invocation failed (non-zero exit). Carries the trimmed combined output."""


def _run_gh(argv: list[str], *, cwd) -> str:
    """Run ``gh <argv>`` in ``cwd``; return trimmed stdout or raise :class:`PrError`."""
    res = proc.run_with_timeout(["gh", *argv], cwd=str(cwd) if cwd is not None else None)
    out = (res.stdout or "").strip()
    if res.returncode != 0:
        err = (res.stderr or "").strip()
        detail = (out + "\n" + err).strip()
        raise PrError(f"gh {' '.join(argv)} failed (exit {res.returncode}): {detail}")
    return out


def create_pr(*, branch: str, base: str, title: str, body: str, cwd) -> str:
    """Open a PR for ``branch`` against ``base`` and return its url (``gh pr create``).

    Faithful to ``bmad-loop.ps1`` ~843: ``gh pr create --base <base> --head <branch>
    --title <title> --body <body>``. (The PS source also passed ``--repo example/demo-project``;
    that hard-coded repo is dropped here — ``gh`` infers the repo from ``cwd``'s remote.)
    """
    return _run_gh(
        [
            "pr",
            "create",
            "--base",
            base,
            "--head",
            branch,
            "--title",
            title,
            "--body",
            body,
        ],
        cwd=cwd,
    )


def merge_pr(*, branch: str, base: str, cwd) -> str:
    """Squash-merge ``branch``'s PR and delete the branch (``gh pr merge``).

    Faithful to ``bmad-loop.ps1`` ~855: ``gh pr merge <ref> --squash --delete-branch``.
    The ref is the head ``branch`` (``gh`` resolves the open PR for it). Returns the merge
    command's output.

    NOTE: ``gh pr merge`` has NO ``--base`` flag — ``--base`` belongs to ``gh pr create``
    (where it fixed this PR's target branch). Passing ``--base`` here made ``gh`` exit 2
    ("unknown flag: --base") → :class:`PrError`, which stalled the loop right before every
    merge and left the story ``done``-but-unmerged (so each reignite re-ran browser-smoke).
    ``base`` is kept in the signature for symmetry with :func:`create_pr` and the driver's
    call site, but is intentionally NOT forwarded to ``gh``.
    """
    return _run_gh(
        ["pr", "merge", branch, "--squash", "--delete-branch"],
        cwd=cwd,
    )


def pr_state(*, branch: str, cwd) -> str:
    """Return the GitHub state of ``branch``'s PR (``gh pr view --json state``).

    Faithful to ``bmad-loop.ps1`` ~857: ``gh pr view <ref> --json state -q .state``.
    The ref is the head ``branch`` (``gh`` resolves its open/most-recent PR), matching
    :func:`merge_pr`. Returns the trimmed state string, e.g. ``"MERGED"`` / ``"OPEN"`` /
    ``"CLOSED"``. Raises :class:`PrError` on a non-zero ``gh`` exit.
    """
    return _run_gh(["pr", "view", branch, "--json", "state", "-q", ".state"], cwd=cwd)


def pr_url(*, branch: str, cwd) -> str:
    """Return the url of ``branch``'s PR (``gh pr view --json url``), or "" if none exists.

    Unlike the other wrappers, this does NOT raise when there is simply no PR for the branch
    (``gh`` exits non-zero with "no pull requests found"): a missing PR is a normal "none"
    answer. It lets the resume tail detect a PR a PRIOR run already opened before dying at
    merge — so the retry reuses that PR instead of failing ``gh pr create`` (which errors when
    a PR already exists). Any non-zero ``gh`` exit yields "" (best-effort lookup).
    """
    res = proc.run_with_timeout(
        ["gh", "pr", "view", branch, "--json", "url", "-q", ".url"],
        cwd=str(cwd) if cwd is not None else None,
    )
    if res.returncode != 0:
        return ""
    return (res.stdout or "").strip()
