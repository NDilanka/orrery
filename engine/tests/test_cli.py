"""CLI tests — ``loop --dry-run``, ``loop-stop`` flag write/status/cancel.

No real ``claude`` and no real model. The dry-run path is proven to call NO runner (a runner
whose ``run`` raises is registered and never reached). ``loop-stop`` is exercised purely on
the STOP flag file.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from loop import cli
from loop.runners.base import AgentRunner


def _init_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "TASK.md").write_text("# Task\n\n## Acceptance Criteria\n- done\n", encoding="utf-8")
    (repo / "thing.txt").write_text("x\n", encoding="utf-8")
    subprocess.run(["git", "init"], cwd=repo, capture_output=True, text=True)
    subprocess.run(["git", "config", "user.email", "t@t.t"], cwd=repo, capture_output=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=repo, capture_output=True)
    subprocess.run(["git", "add", "-A"], cwd=repo, capture_output=True)
    subprocess.run(["git", "commit", "-q", "-m", "base"], cwd=repo, capture_output=True)
    return repo


class ExplodingRunner(AgentRunner):
    name = "boom"

    def run(self, **kwargs):  # pragma: no cover - dry-run must never reach it
        raise AssertionError("runner must not be called during --dry-run")


def test_loop_dry_run_returns_0_and_calls_no_runner(tmp_path, monkeypatch):
    repo = _init_repo(tmp_path)
    state = tmp_path / "state"
    # force get_runner to hand back the exploding runner regardless of name
    monkeypatch.setattr(cli, "get_runner", lambda name: ExplodingRunner())

    rc = cli.main(
        [
            "--task", "TASK.md",
            "--cwd", str(repo),
            "--state-dir", str(state),
            "--dry-run",
        ]
    )
    assert rc == 0
    # no log lines were written during dry-run
    log = state / "log.jsonl"
    assert not log.exists() or log.read_text(encoding="utf-8").strip() == ""


def test_loop_stop_now_writes_flag_and_status_reports_then_cancel_clears(tmp_path, capsys):
    state = tmp_path / "state"

    # --now writes the flag as 'now'
    rc = cli.main_stop(["--state-dir", str(state), "--now"])
    assert rc == 0
    flag = state / "STOP"
    assert flag.read_text(encoding="utf-8").strip() == "now"

    # --status reports the pending mode
    rc = cli.main_stop(["--state-dir", str(state), "--status"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "STOP pending" in out
    assert "now" in out

    # --cancel clears the flag
    rc = cli.main_stop(["--state-dir", str(state), "--cancel"])
    assert rc == 0
    assert not flag.exists()

    # --status now reports nothing pending
    rc = cli.main_stop(["--state-dir", str(state), "--status"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "No stop pending" in out


def test_loop_stop_default_is_phase_after_story_is_story(tmp_path):
    state = tmp_path / "state"
    cli.main_stop(["--state-dir", str(state)])
    assert (state / "STOP").read_text(encoding="utf-8").strip() == "phase"

    cli.main_stop(["--state-dir", str(state), "--after-story"])
    assert (state / "STOP").read_text(encoding="utf-8").strip() == "story"


def test_loop_bmad_stub_returns_2(capsys):
    rc = cli.main_bmad([])
    assert rc == 2
    assert "later phase" in capsys.readouterr().out
