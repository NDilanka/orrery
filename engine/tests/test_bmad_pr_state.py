"""Hermetic tests for ``loop.bmad.pr.pr_state`` — the merge-verification wrapper.

``pr_state`` shells out via :func:`loop.proc.run_with_timeout` (same indirection as
``create_pr`` / ``merge_pr``). These tests stub ``loop.bmad.pr.proc.run_with_timeout`` with a
fake that records the argv and returns a canned result object exposing ``.stdout`` /
``.stderr`` / ``.returncode`` — proving NO real ``gh`` is spawned and NO network is hit.
"""

from __future__ import annotations

import pytest

from loop.bmad import pr


class _FakeResult:
    """Minimal stand-in for ``loop.proc.ProcResult``."""

    def __init__(self, *, returncode: int, stdout: str = "", stderr: str = ""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def _stub_proc(monkeypatch, result: _FakeResult) -> list[list[str]]:
    """Patch ``pr.proc.run_with_timeout`` to record argv and return ``result``."""
    seen: list[list[str]] = []

    def fake_run(argv, *args, **kwargs):
        seen.append(list(argv))
        return result

    monkeypatch.setattr(pr.proc, "run_with_timeout", fake_run)
    return seen


def test_pr_state_returns_merged(monkeypatch):
    seen = _stub_proc(monkeypatch, _FakeResult(returncode=0, stdout="MERGED\n"))

    state = pr.pr_state(branch="feat/story-x", cwd=".")

    assert state == "MERGED"
    # the exact gh argv (with the leading "gh") the wrapper built
    assert seen == [
        ["gh", "pr", "view", "feat/story-x", "--json", "state", "-q", ".state"]
    ]


def test_pr_state_raises_on_nonzero_exit(monkeypatch):
    seen = _stub_proc(
        monkeypatch,
        _FakeResult(returncode=1, stderr="no pull requests found"),
    )

    with pytest.raises(pr.PrError) as excinfo:
        pr.pr_state(branch="feat/story-x", cwd=".")

    assert "no pull requests found" in str(excinfo.value)
    assert seen == [
        ["gh", "pr", "view", "feat/story-x", "--json", "state", "-q", ".state"]
    ]
