"""CLI entry points — ``loop`` / ``loop-stop`` / ``loop-bmad`` (referenced by pyproject).

- :func:`main` (``loop``)      — run the generic fix-until-green loop (port of ``loop.ps1``'s
  param surface, reduced to the OSS essentials). Builds an :class:`~loop.config.EngineConfig`
  (from ``--loop-json`` if given, else from flags/defaults; CLI flags override), gets a runner
  via :func:`~loop.runners.get_runner`, and calls :func:`~loop.core.run_loop`.
- :func:`main_stop` (``loop-stop``) — self-contained cooperative-stop controller (port of
  ``stop-loop.ps1``): write / cancel / status the ``STOP`` flag in the state dir.
- :func:`main_bmad` (``loop-bmad``) — the BMAD multi-story driver entrypoint: build a
  :class:`~loop.bmad.driver.BmadConfig` from the CLI flags, get a base runner, and run the
  full epic pipeline (:func:`loop.bmad.driver.run`).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from loop.config import EngineConfig, from_loop_json
from loop.core import run_loop
from loop.logio import read_text, write_text
from loop.runners import get_runner

# Sentinel so ``--memory`` (flag absent) is distinguishable from ``--memory`` with no PATH.
_MEMORY_UNSET = object()


def _build_config(args) -> EngineConfig:
    """Build an :class:`EngineConfig`: load ``--loop-json`` if given, then apply CLI overrides.

    File config is the base; a CLI flag overrides ONLY when the user explicitly passed it
    (every override flag defaults to ``None``/unset sentinels). In particular ``--task`` now
    defaults to ``None`` — its old argparse default of ``"TASK.md"`` silently clobbered a
    file-provided ``engine.task`` on every run that didn't pass ``--task`` explicitly.
    """
    if args.loop_json:
        config = from_loop_json(args.loop_json)
    else:
        config = EngineConfig()
    # Carry the --loop-json path (if any) into the checkpoint `resume` string (Task 5 — a
    # resume/Reignite must re-point at it, not silently drop the file's tuning).
    config.loop_json = args.loop_json or ""

    if args.task is not None:
        config.task = args.task
    if args.max_iters is not None:
        config.stop.max_iters = args.max_iters
    if args.iter_timeout_min is not None:
        config.iter_timeout_min = args.iter_timeout_min
    if args.cost_ceiling is not None:
        config.cost.ceiling_usd = args.cost_ceiling
    if args.verify:
        config.verify.enabled = True
    if args.compact_feedback:
        config.feedback.compact = True
    if args.memory is not _MEMORY_UNSET:
        # --memory with no value -> a sentinel ""; --memory PATH -> that path.
        config.memory.enabled = True
        if args.memory:
            config.memory.path = args.memory
    if args.mutation_audit:
        config.verify.mutation_audit = True
    if args.emit_metrics:
        config.metrics.emit = True
    return config


def main(argv: list[str] | None = None) -> int:
    """``loop`` entry point — run the generic fix-until-green loop."""
    parser = argparse.ArgumentParser(prog="loop", description="Run the fix-until-green loop.")
    parser.add_argument(
        "--task", default=None,
        help="task spec the agent reads each iter (default: TASK.md, or the loop.json engine.task)",
    )
    parser.add_argument("--state-dir", default=".loop", help="state dir (log/checkpoint/STOP/...)")
    parser.add_argument("--cwd", default=".", help="repo the gate + git operate on")
    parser.add_argument("--loop-json", default=None, help="optional loop.json engine config")
    parser.add_argument("--runner", default="claude", help="agent backend (default: claude)")
    parser.add_argument("--max-iters", type=int, default=None, help="iteration cap")
    parser.add_argument(
        "--iter-timeout-min", type=int, default=None,
        help="per-iteration execute wall-clock cap in minutes (0=disabled; default 60)",
    )
    parser.add_argument("--cost-ceiling", type=float, default=None, help="cumulative USD ceiling")
    parser.add_argument(
        "--verify",
        action="store_true",
        help="enable the anti-false-green judge pass (emits model{judge} + verdict events)",
    )
    parser.add_argument(
        "--compact-feedback",
        action="store_true",
        help="feed back only the first failing test (loop.feedback) instead of the raw gate dump",
    )
    parser.add_argument(
        "--memory",
        nargs="?",
        const="",
        default=_MEMORY_UNSET,
        metavar="PATH",
        help="enable the cross-run lessons store (loop.memory); optional PATH to the JSONL file",
    )
    parser.add_argument(
        "--mutation-audit",
        action="store_true",
        help="run the advisory mutation-strength audit when the gate is green (loop.verify)",
    )
    parser.add_argument(
        "--emit-metrics",
        action="store_true",
        help="emit a run-quality metrics event at stop (loop.metrics)",
    )
    parser.add_argument("--dry-run", action="store_true", help="INIT + baseline gate only")
    args = parser.parse_args(argv)

    config = _build_config(args)
    runner = get_runner(args.runner)
    try:
        return run_loop(
            config,
            runner=runner,
            state_dir=args.state_dir,
            cwd=args.cwd,
            dry_run=args.dry_run,
        )
    except KeyboardInterrupt:
        # run_loop's own try/finally has already released the lock and proc.py has already
        # killed any child process tree by the time this unwinds here — just exit cleanly
        # (130 = "terminated by Ctrl-C", the POSIX convention) instead of a raw traceback.
        print("\n[INTERRUPTED] stopping.")
        return 130


def main_stop(argv: list[str] | None = None) -> int:
    """``loop-stop`` entry point — write / cancel / status the cooperative STOP flag.

    Port of ``stop-loop.ps1``: ``--after-story`` writes ``story``, ``--now`` writes ``now``,
    the default writes ``phase``. ``--cancel`` deletes the flag; ``--status`` prints the
    pending mode + a checkpoint summary.
    """
    parser = argparse.ArgumentParser(prog="loop-stop", description="Cooperative-stop controller.")
    parser.add_argument("--state-dir", default=".loop", help="state dir holding STOP/checkpoint")
    parser.add_argument("--after-story", action="store_true", help="stop at next STORY boundary")
    parser.add_argument("--now", action="store_true", help="stop as soon as safely possible")
    parser.add_argument("--cancel", action="store_true", help="cancel a pending stop")
    parser.add_argument("--status", action="store_true", help="report stop + checkpoint state")
    args = parser.parse_args(argv)

    state = Path(args.state_dir)
    state.mkdir(parents=True, exist_ok=True)
    flag = state / "STOP"
    checkpoint = state / "checkpoint.json"

    if args.status:
        content = read_text(flag)
        if content is not None:
            mode = content.strip() or "phase"
            print(f"STOP pending (mode: {mode}) — the loop will stop at its next {mode} checkpoint.")
        else:
            print("No stop pending — the loop (if running) will keep going.")
        cp = read_text(checkpoint)
        if cp is not None:
            print("\nLast checkpoint:")
            print(cp)
        else:
            print("(no checkpoint written yet)")
        return 0

    if args.cancel:
        try:
            os.remove(flag)
        except FileNotFoundError:
            pass
        print("Stop request cancelled — the loop will keep running.")
        return 0

    mode = "now" if args.now else ("story" if args.after_story else "phase")
    write_text(flag, mode)
    print(f"Graceful stop requested (mode: {mode}).")
    print("  Nothing is killed mid-step: in-flight work finishes and commits first.")
    print(f"  Change your mind:  loop-stop --state-dir {args.state_dir} --cancel")
    return 0


def main_bmad(argv: list[str] | None = None) -> int:
    """``loop-bmad`` entry point — run the BMAD multi-story epic pipeline.

    Port of the ``bmad-loop.ps1`` param surface (reduced to the OSS essentials). Builds a
    :class:`~loop.bmad.driver.BmadConfig` from the flags, gets the BASE runner via
    :func:`~loop.runners.get_runner` (the driver wraps it in a quota-surviving
    ``ResilientRunner``), and dispatches to :func:`loop.bmad.driver.run`. ``--project-root`` is
    REQUIRED. ``--dry-run`` scans the sprint + runs the gate once and returns 0 without any
    runner call.
    """
    from loop.bmad import driver

    parser = argparse.ArgumentParser(prog="loop-bmad", description="Run the BMAD epic pipeline.")
    parser.add_argument("--project-root", required=True, help="the BMAD project repo (REQUIRED)")
    parser.add_argument(
        "--loop-json",
        default=None,
        help="optional loop.json with a `bmad` block (per-phase models/effort, gateStages, "
        "review/smoke modes); CLI flags stay authoritative for run-location + toggles",
    )
    parser.add_argument(
        "--merge-base", default=None, help="base branch for PRs + merge (default: develop)"
    )
    parser.add_argument("--state-dir", default=".loop", help="state dir (log/checkpoint/STOP/lock)")
    parser.add_argument("--cwd", default=None, help="override dev-server/gate dir (default project-root)")
    parser.add_argument("--runner", default="claude", help="agent backend (default: claude)")
    parser.add_argument("--epic-only", default=None, help="restrict to one epic (e.g. '2')")
    parser.add_argument("--story", default=None, help="force a specific story key")
    parser.add_argument("--no-merge", action="store_true", help="open PRs but do NOT auto-merge")
    parser.add_argument("--no-retro", action="store_true", help="skip epic retrospectives")
    parser.add_argument("--no-smoke", action="store_true", help="skip the browser-smoke phase")
    parser.add_argument(
        "--auto-rollback",
        action="store_true",
        help="on a test regression, git reset --hard the story's baseline_commit (else report only)",
    )
    parser.add_argument("--dry-run", action="store_true", help="sprint scan + gate only; no runner")
    parser.add_argument("--max-stories", type=int, default=None, help="stories-per-launch backstop")
    parser.add_argument("--max-review-turns", type=int, default=None, help="code-review Q&A cap")
    parser.add_argument("--max-smoke-iters", type=int, default=None, help="browser-smoke iter cap")
    parser.add_argument("--smoke-timeout-min", type=int, default=None, help="per-smoke wall-clock cap")
    parser.add_argument("--max-retro-turns", type=int, default=None, help="retrospective Q&A cap")
    parser.add_argument(
        "--create-timeout-min", type=int, default=None,
        help="create-story phase wall-clock cap in minutes (0=disabled; default 30)",
    )
    parser.add_argument(
        "--dev-timeout-min", type=int, default=None,
        help="dev-story phase wall-clock cap in minutes (0=disabled; default 120)",
    )
    parser.add_argument(
        "--review-timeout-min", type=int, default=None,
        help="code-review phase per-call wall-clock cap in minutes (0=disabled; default 60)",
    )
    parser.add_argument(
        "--retro-timeout-min", type=int, default=None,
        help="epic-retro phase per-call wall-clock cap in minutes (0=disabled; default 30)",
    )
    parser.add_argument("--default-quota-wait-min", type=int, default=None, help="fallback quota wait")
    parser.add_argument("--max-quota-waits", type=int, default=None, help="quota wait give-up backstop")
    parser.add_argument(
        "--merge-wait-sec", type=int, default=None,
        help="poll a QUEUED PR merge up to N s before halting (0=halt immediately if not MERGED)",
    )
    parser.add_argument(
        "--review-mode",
        choices=("qa", "single-pass"),
        default=None,
        help="code-review: 'qa' (default, decider Q&A loop) or 'single-pass' (one warm process)",
    )
    parser.add_argument(
        "--smoke-mode",
        choices=("iterative", "single-pass"),
        default=None,
        help="browser-smoke: 'iterative' (default, re-spawn on FAIL) or 'single-pass' (one process)",
    )
    parser.add_argument(
        "--retro-mode",
        choices=("qa", "single-pass"),
        default=None,
        help="epic-retro: 'qa' (default, decider Q&A loop) or 'single-pass' (one warm process)",
    )
    args = parser.parse_args(argv)

    # File config is the BASE; CLI flags override ONLY when the user explicitly passed them
    # (every override flag below defaults to `None` — a `store_true` toggle counts as "passed"
    # only when True, since argparse can't distinguish "absent" from an explicit False for those).
    # Previously this cherry-picked ~8 fields from the file onto a CLI-built config, silently
    # discarding everything else the file set (maxStories, mergeBase, timeouts, no-merge/no-retro/
    # no-smoke, auto-rollback, epic-only/story, quota-wait knobs, ...) — inverted here so a
    # `--loop-json` config is fully honored except where the CLI explicitly says otherwise.
    if args.loop_json:
        data = json.loads(Path(args.loop_json).read_text(encoding="utf-8"))
        b = data.get("bmad", data) if isinstance(data, dict) else {}
        b = dict(b) if isinstance(b, dict) else {}
        b.setdefault("project_root", args.project_root)  # from_loop_json needs a project_root
        config = driver.BmadConfig.from_loop_json({"bmad": b})
    else:
        config = driver.BmadConfig(project_root=args.project_root)
    # --project-root is REQUIRED on this CLI (always explicitly passed) -> always authoritative,
    # regardless of any project_root/projectRoot the file itself carries.
    config.project_root = args.project_root
    config.loop_json = args.loop_json or ""

    if args.merge_base is not None:
        config.merge_base = args.merge_base
    if args.epic_only is not None:
        config.epic_only = args.epic_only
    if args.story is not None:
        config.story = args.story
    if args.no_merge:
        config.no_merge = True
    if args.no_retro:
        config.no_retro = True
    if args.no_smoke:
        config.no_smoke = True
    if args.auto_rollback:
        config.auto_rollback = True
    if args.dry_run:
        config.dry_run = True
    if args.max_stories is not None:
        config.max_stories = args.max_stories
    if args.max_review_turns is not None:
        config.max_review_turns = args.max_review_turns
    if args.max_smoke_iters is not None:
        config.max_smoke_iters = args.max_smoke_iters
    if args.smoke_timeout_min is not None:
        config.smoke_timeout_min = args.smoke_timeout_min
    if args.max_retro_turns is not None:
        config.max_retro_turns = args.max_retro_turns
    if args.create_timeout_min is not None:
        config.create_timeout_min = args.create_timeout_min
    if args.dev_timeout_min is not None:
        config.dev_timeout_min = args.dev_timeout_min
    if args.review_timeout_min is not None:
        config.review_timeout_min = args.review_timeout_min
    if args.retro_timeout_min is not None:
        config.retro_timeout_min = args.retro_timeout_min
    if args.default_quota_wait_min is not None:
        config.default_quota_wait_min = args.default_quota_wait_min
    if args.max_quota_waits is not None:
        config.max_quota_waits = args.max_quota_waits
    if args.merge_wait_sec is not None:
        config.merge_wait_sec = args.merge_wait_sec
    if args.review_mode is not None:
        config.review_mode = args.review_mode
    if args.smoke_mode is not None:
        config.smoke_mode = args.smoke_mode
    if args.retro_mode is not None:
        config.retro_mode = args.retro_mode

    runner = get_runner(args.runner)
    try:
        return driver.run(config, runner=runner, state_dir=args.state_dir, cwd=args.cwd)
    except KeyboardInterrupt:
        # driver.run's own try/finally has already released the lock and proc.py has already
        # killed any child process tree by the time this unwinds here.
        print("\n[INTERRUPTED] stopping.")
        return 130


def main_qa(argv: list[str] | None = None) -> int:
    """``loop-qa`` entry point — run the AC-driven functional QA discovery pass.

    Drives a headless, pre-authenticated browser (Playwright MCP) through each epic's screens,
    judging browser-observable acceptance criteria and authoring regression specs. ``--project-root``
    (the app repo the agent writes specs into) and ``--manifest`` (the AC oracle from
    :mod:`loop.qa.manifest`) are REQUIRED; an optional ``--loop-json`` ``qa`` block supplies tuning
    (baseUrl / storageState / specDir / seedSummary / model / effort / timeouts). CLI flags override.
    """
    from loop.qa import discover

    parser = argparse.ArgumentParser(prog="loop-qa", description="Run the QA discovery pass.")
    parser.add_argument("--project-root", required=True, help="app repo under test (REQUIRED)")
    parser.add_argument("--manifest", required=True, help="ac-manifest.json oracle (REQUIRED)")
    parser.add_argument("--state-dir", default=".loop", help="state dir (log/checkpoint/STOP)")
    parser.add_argument("--loop-json", default=None, help="optional loop.json with a `qa` block")
    parser.add_argument("--base-url", default=None, help="base URL of the running app")
    parser.add_argument("--storage-state", default=None, help="Playwright auth storageState path")
    parser.add_argument("--spec-dir", default=None, help="where authored specs land (rel to root)")
    parser.add_argument("--model", default=None, help="agent model ('' inherits the default)")
    parser.add_argument("--effort", default=None, help="reasoning effort (low|medium|high|xhigh|max)")
    parser.add_argument(
        "--epic", action="append", type=int, default=None,
        help="restrict to epic N (repeatable); default = all epics in the manifest",
    )
    parser.add_argument("--timeout-sec", type=int, default=None, help="per-epic wall-clock cap")
    parser.add_argument("--cost-ceiling", type=float, default=None, help="cumulative USD ceiling")
    args = parser.parse_args(argv)

    if args.loop_json:
        data = json.loads(Path(args.loop_json).read_text(encoding="utf-8"))
        config = discover.QaConfig.from_loop_json(data, project_root=args.project_root)
    else:
        config = discover.QaConfig(project_root=args.project_root, manifest_path=args.manifest)

    config.manifest_path = args.manifest  # CLI is authoritative for the oracle location
    if args.base_url is not None:
        config.base_url = args.base_url
    if args.storage_state is not None:
        config.storage_state = args.storage_state
    if args.spec_dir is not None:
        config.spec_dir = args.spec_dir
    if args.model is not None:
        config.model = args.model
    if args.effort is not None:
        config.effort = args.effort
    if args.epic is not None:
        config.epics = args.epic
    if args.timeout_sec is not None:
        config.timeout_sec = args.timeout_sec
    if args.cost_ceiling is not None:
        config.cost_ceiling_usd = args.cost_ceiling

    try:
        return discover.run(config, state_dir=args.state_dir)
    except KeyboardInterrupt:
        print("\n[INTERRUPTED] stopping.")
        return 130


def main_supervise(argv: list[str] | None = None) -> int:
    """``loop-supervise`` entry point — restart a wrapped command on failure (replaces
    ``supervise.ps1``).

    Usage::

        loop-supervise --state-dir <dir> [--max-restarts 5] [--window-min 90] \\
            [--poll-sec 5] -- <command...>

    ``<command...>`` (everything after the literal ``--``) is spawned as a killable process
    tree and waited on; a nonzero exit restarts it unless a ``STOP`` file or a
    ``STOP-SUPERVISOR`` sentinel exists in ``--state-dir``, or the thrash guard trips (more than
    ``--max-restarts`` restarts within the rolling ``--window-min`` window). Exit code ``0``
    from the wrapped command ends supervision cleanly. See :mod:`loop.supervise`.
    """
    from loop.supervise import SupervisorConfig, supervise

    parser = argparse.ArgumentParser(
        prog="loop-supervise",
        description="Restart a wrapped loop command on failure (replaces supervise.ps1).",
    )
    parser.add_argument("--state-dir", required=True, help="state dir (STOP/STOP-SUPERVISOR/log.jsonl/supervisor.log)")
    parser.add_argument(
        "--max-restarts", type=int, default=5,
        help="thrash guard: max restarts within --window-min before giving up (default 5)",
    )
    parser.add_argument(
        "--window-min", type=float, default=90.0,
        help="thrash guard rolling window in minutes (default 90)",
    )
    parser.add_argument(
        "--poll-sec", type=float, default=5.0,
        help="pause before each restart, in seconds (default 5)",
    )
    parser.add_argument(
        "command", nargs=argparse.REMAINDER,
        help="the command to supervise, e.g.: -- loop-bmad --project-root ...",
    )
    args = parser.parse_args(argv)

    command = list(args.command)
    if command and command[0] == "--":
        command = command[1:]
    if not command:
        parser.error("missing command to supervise — pass it after a literal '--'")

    config = SupervisorConfig(
        state_dir=args.state_dir,
        command=command,
        max_restarts=args.max_restarts,
        window_min=args.window_min,
        poll_sec=args.poll_sec,
    )
    try:
        return supervise(config)
    except KeyboardInterrupt:
        print("\n[INTERRUPTED] stopping.")
        return 130


if __name__ == "__main__":
    sys.exit(main())
