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


def _init_bmad_project(tmp_path: Path) -> Path:
    """A minimal BMAD project: a git repo with a sprint-status.yaml under the artifacts dir."""
    root = tmp_path / "project"
    artifacts = root / "_bmad-output" / "implementation-artifacts"
    artifacts.mkdir(parents=True)
    (artifacts / "sprint-status.yaml").write_text(
        "development_status:\n"
        "  epic-2: in-progress\n"
        "  2-1-capture: ready-for-dev\n",
        encoding="utf-8",
    )
    subprocess.run(["git", "init"], cwd=root, capture_output=True, text=True)
    subprocess.run(["git", "config", "user.email", "t@t.t"], cwd=root, capture_output=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=root, capture_output=True)
    subprocess.run(["git", "add", "-A"], cwd=root, capture_output=True)
    subprocess.run(["git", "commit", "-q", "-m", "base"], cwd=root, capture_output=True)
    subprocess.run(["git", "branch", "develop"], cwd=root, capture_output=True)
    return root


def test_loop_bmad_dry_run_returns_0_and_calls_no_runner(tmp_path, monkeypatch):
    root = _init_bmad_project(tmp_path)
    # any runner get_runner hands back must never be called during --dry-run
    monkeypatch.setattr(cli, "get_runner", lambda name: ExplodingRunner())
    rc = cli.main_bmad(
        [
            "--project-root", str(root),
            "--state-dir", str(tmp_path / "state"),
            "--dry-run",
        ]
    )
    assert rc == 0


# ---------------------------------------------------------------------------
# Task 2 — KeyboardInterrupt during a driver call exits 130, no traceback
# ---------------------------------------------------------------------------


def test_loop_main_keyboard_interrupt_exits_130(tmp_path, monkeypatch):
    repo = _init_repo(tmp_path)
    state = tmp_path / "state"

    def boom(*a, **kw):
        raise KeyboardInterrupt()

    monkeypatch.setattr(cli, "run_loop", boom)
    monkeypatch.setattr(cli, "get_runner", lambda name: ExplodingRunner())
    rc = cli.main(["--task", "TASK.md", "--cwd", str(repo), "--state-dir", str(state)])
    assert rc == 130


def test_loop_bmad_main_keyboard_interrupt_exits_130(tmp_path, monkeypatch):
    root = _init_bmad_project(tmp_path)
    from loop.bmad import driver

    def boom(*a, **kw):
        raise KeyboardInterrupt()

    monkeypatch.setattr(driver, "run", boom)
    monkeypatch.setattr(cli, "get_runner", lambda name: ExplodingRunner())
    rc = cli.main_bmad(
        ["--project-root", str(root), "--state-dir", str(tmp_path / "state")]
    )
    assert rc == 130


# ---------------------------------------------------------------------------
# Task 1a/1b — new timeout CLI flags flow through to the config
# ---------------------------------------------------------------------------


def test_loop_iter_timeout_min_flag_overrides_config(tmp_path, monkeypatch):
    repo = _init_repo(tmp_path)
    state = tmp_path / "state"
    captured: dict = {}

    def fake_run_loop(config, **kw):
        captured["iter_timeout_min"] = config.iter_timeout_min
        return 0

    monkeypatch.setattr(cli, "run_loop", fake_run_loop)
    monkeypatch.setattr(cli, "get_runner", lambda name: ExplodingRunner())
    rc = cli.main(
        [
            "--task", "TASK.md", "--cwd", str(repo), "--state-dir", str(state),
            "--iter-timeout-min", "5",
        ]
    )
    assert rc == 0
    assert captured["iter_timeout_min"] == 5


def test_loop_bmad_timeout_flags_override_config(tmp_path, monkeypatch):
    root = _init_bmad_project(tmp_path)
    from loop.bmad import driver

    captured: dict = {}

    def fake_run(config, **kw):
        captured["create"] = config.create_timeout_min
        captured["dev"] = config.dev_timeout_min
        captured["review"] = config.review_timeout_min
        captured["retro"] = config.retro_timeout_min
        return 0

    monkeypatch.setattr(driver, "run", fake_run)
    monkeypatch.setattr(cli, "get_runner", lambda name: ExplodingRunner())
    rc = cli.main_bmad(
        [
            "--project-root", str(root), "--state-dir", str(tmp_path / "state"),
            "--create-timeout-min", "1", "--dev-timeout-min", "2",
            "--review-timeout-min", "3", "--retro-timeout-min", "4",
        ]
    )
    assert rc == 0
    assert captured == {"create": 1, "dev": 2, "review": 3, "retro": 4}


def test_loop_bmad_timeout_flag_of_zero_disables_not_default(tmp_path, monkeypatch):
    """0 is a legitimate explicit 'disable this timeout' value — it must NOT be coerced back
    to the nonzero default (the classic `x or default` footgun)."""
    root = _init_bmad_project(tmp_path)
    from loop.bmad import driver

    captured: dict = {}

    def fake_run(config, **kw):
        captured["dev"] = config.dev_timeout_min
        return 0

    monkeypatch.setattr(driver, "run", fake_run)
    monkeypatch.setattr(cli, "get_runner", lambda name: ExplodingRunner())
    rc = cli.main_bmad(
        [
            "--project-root", str(root), "--state-dir", str(tmp_path / "state"),
            "--dev-timeout-min", "0",
        ]
    )
    assert rc == 0
    assert captured["dev"] == 0


# ---------------------------------------------------------------------------
# Task 6 — loop-supervise CLI arg parsing
# ---------------------------------------------------------------------------


def test_loop_supervise_parses_flags_and_command_after_double_dash(tmp_path, monkeypatch):
    captured: dict = {}

    def fake_supervise(config):
        captured["config"] = config
        return 0

    monkeypatch.setattr("loop.supervise.supervise", fake_supervise)
    rc = cli.main_supervise(
        [
            "--state-dir", str(tmp_path / "state"),
            "--max-restarts", "9",
            "--window-min", "12.5",
            "--poll-sec", "0.5",
            "--",
            "loop-bmad", "--project-root", "D:/p",
        ]
    )
    assert rc == 0
    cfg = captured["config"]
    assert cfg.max_restarts == 9
    assert cfg.window_min == 12.5
    assert cfg.poll_sec == 0.5
    assert cfg.command == ["loop-bmad", "--project-root", "D:/p"]


def test_loop_supervise_missing_command_errors(tmp_path):
    import pytest

    with pytest.raises(SystemExit):
        cli.main_supervise(["--state-dir", str(tmp_path / "state")])


def test_loop_qa_main_keyboard_interrupt_exits_130(tmp_path, monkeypatch):
    from loop.qa import discover

    mpath = tmp_path / "ac-manifest.json"
    mpath.write_text('{"app": "x", "epics": []}', encoding="utf-8")

    def boom(*a, **kw):
        raise KeyboardInterrupt()

    monkeypatch.setattr(discover, "run", boom)
    rc = cli.main_qa(
        [
            "--project-root", str(tmp_path),
            "--manifest", str(mpath),
            "--state-dir", str(tmp_path / "state"),
        ]
    )
    assert rc == 130
