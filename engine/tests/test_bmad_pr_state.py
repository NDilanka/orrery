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


def test_merge_pr_argv_has_no_base_flag(monkeypatch):
    # ``gh pr merge`` has NO --base flag (that's ``gh pr create`` only). Regression guard
    # for the port bug that passed --base, made gh exit 2, and stalled every merge — which
    # left the story done-but-unmerged so each reignite re-ran browser-smoke.
    seen = _stub_proc(monkeypatch, _FakeResult(returncode=0, stdout="Merged.\n"))

    out = pr.merge_pr(branch="feat/story-x", base="develop", cwd=".")

    assert out == "Merged."
    assert seen == [["gh", "pr", "merge", "feat/story-x", "--squash", "--delete-branch"]]
    assert "--base" not in seen[0]


def test_create_pr_argv_keeps_base_flag(monkeypatch):
    # By contrast, ``gh pr create`` DOES take --base; assert it is preserved.
    seen = _stub_proc(monkeypatch, _FakeResult(returncode=0, stdout="https://x/pull/1\n"))

    url = pr.create_pr(branch="feat/story-x", base="develop", title="t", body="b", cwd=".")

    assert url == "https://x/pull/1"
    assert seen == [
        [
            "gh", "pr", "create",
            "--base", "develop",
            "--head", "feat/story-x",
            "--title", "t",
            "--body", "b",
        ]
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


def test_pr_url_returns_url(monkeypatch):
    seen = _stub_proc(monkeypatch, _FakeResult(returncode=0, stdout="https://x/pull/11\n"))

    url = pr.pr_url(branch="feat/story-x", cwd=".")

    assert url == "https://x/pull/11"
    assert seen == [["gh", "pr", "view", "feat/story-x", "--json", "url", "-q", ".url"]]


def test_pr_url_empty_when_no_pr(monkeypatch):
    # No PR for the branch -> gh exits non-zero -> pr_url returns "" (NOT an error), so the
    # resume tail can tell "create failed because one exists" from "no PR at all".
    _stub_proc(monkeypatch, _FakeResult(returncode=1, stderr="no pull requests found"))

    assert pr.pr_url(branch="feat/story-x", cwd=".") == ""
