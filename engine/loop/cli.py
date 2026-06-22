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

    Flags that the user explicitly passed override the JSON / defaults. ``--task`` always
    wins (it has a CLI default of ``TASK.md`` which matches the config default anyway).
    """
    if args.loop_json:
        config = from_loop_json(args.loop_json)
    else:
        config = EngineConfig()

    if args.task is not None:
        config.task = args.task
    if args.max_iters is not None:
        config.stop.max_iters = args.max_iters
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
    parser.add_argument("--task", default="TASK.md", help="task spec the agent reads each iter")
    parser.add_argument("--state-dir", default=".loop", help="state dir (log/checkpoint/STOP/...)")
    parser.add_argument("--cwd", default=".", help="repo the gate + git operate on")
    parser.add_argument("--loop-json", default=None, help="optional loop.json engine config")
    parser.add_argument("--runner", default="claude", help="agent backend (default: claude)")
    parser.add_argument("--max-iters", type=int, default=None, help="iteration cap")
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
    return run_loop(
        config,
        runner=runner,
        state_dir=args.state_dir,
        cwd=args.cwd,
        dry_run=args.dry_run,
    )


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
    parser.add_argument("--merge-base", default="develop", help="base branch for PRs + merge")
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
    parser.add_argument("--default-quota-wait-min", type=int, default=None, help="fallback quota wait")
    parser.add_argument("--max-quota-waits", type=int, default=None, help="quota wait give-up backstop")
    args = parser.parse_args(argv)

    config = driver.BmadConfig.from_args(args)
    runner = get_runner(args.runner)
    return driver.run(config, runner=runner, state_dir=args.state_dir, cwd=args.cwd)


if __name__ == "__main__":
    sys.exit(main())
