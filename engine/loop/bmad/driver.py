"""The BMAD multi-story DRIVER — the capstone of the BMAD port (port of ``bmad-loop.ps1``).

Composes the already-built pure pieces (sprint scan/select, the five per-story phases, the
deciders, the event builders, quota survival, git, the guarded ``gh`` wrappers) into the full
epic pipeline:

    preflight (sprint scan + checkout merge_base)
    -> epic/story loop, each story:
         [create-story?] -> dev-story -> code-review(Q&A) -> [browser-smoke] -> PR -> [merge]
       at an epic boundary (all stories done, retro pending) -> EPIC RETROSPECTIVE
    -> shutdown: backlog complete (ok) or handoff (not ok)

EVERY external effect is injected or guarded so the whole orchestration is testable with mocks:
the agent goes through the injected ``runner`` (wrapped in :class:`ResilientRunner` for central
quota survival); the dev server is an injected ``server_ctl`` (the real one is
:class:`loop.bmad.phases.DevServer`); ``gh`` PR/merge go through :mod:`loop.bmad.pr` (which
tests monkeypatch); git is read/written via :mod:`loop.gitutil`; events go through ``emit``
(``append_event``); the gate is a callable. NO network, NO real ``claude`` in tests.

Faithfully ported orchestration from ``bmad-loop.ps1``; deliberate simplifications (each a clean
follow-up) are noted in the module docstring of the final report rather than changing the wiring:

- Concurrency guard is a pid lockfile (like :func:`loop.core.run_loop`), not a Win32 process
  scan.
- ``--manual-continue`` and the ``-AutoRollback`` / ``-SkipCreateStory`` / ``-NoPush`` /
  ``-NoQuotaWait`` knobs are dropped (the OSS surface keeps the core epic pipeline + the
  task-specified flags).
- The dev-server command + the gate stages are CONFIGURABLE (the PS source hard-coded ``bun run
  dev`` + the three ``bun run`` gate stages).
"""

from __future__ import annotations

import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from loop import gitutil
from loop.bmad import phases, pr, recovery
from loop.bmad.decider import retro_decider, review_decider
from loop.bmad.sprint import (
    SprintStatus,
    Story,
    epic_done,
    parse_sprint_status,
    select_actionable,
)
from loop.bmad.story import story_meta
from loop.checkpoint import get_stop_mode
from loop.events import (
    bmad_stop_event,
    cooperative_stop_event,
    new_checkpoint,
    pr_created_event,
    pr_merged_event,
    retro_answer_event,
    retro_complete_event,
    retro_question_event,
    retro_start_event,
    start_event,
    story_start_event,
)
from loop.logio import (
    append_event,
    clear_stop_flag,
    read_stop_flag,
    write_checkpoint,
)
from loop.quota import survive
from loop.runners.base import AgentResult, AgentRunner
from loop.verdict import question_marker

# --- where bmad-loop.ps1 finds the sprint file + the story files (~58-59) -----
_ARTIFACTS_REL = ("_bmad-output", "implementation-artifacts")
_SPRINT_FILE = "sprint-status.yaml"

_LOCK_NAME = "bmad-lock"

# The three authoritative gate stages from bmad-loop.ps1 (~284-286): codegen, lint, test
# (vitest). OVERRIDABLE via config; the test/extension hook may swap the commands for callables.
DEFAULT_GATE_STAGES: list[dict[str, Any]] = [
    {"name": "codegen", "command": "bun run codegen"},
    {"name": "lint", "command": "bun run lint"},
    {
        "name": "test",
        # brain2's `bun run test` is vitest; its summary line is "Tests  N passed / N failed".
        # Anchor on the "Tests" label so we read the test count, NOT the "Test Files" count
        # (bmad-loop.ps1 ~287-289 used the same `Tests\s+(\d+)\s+passed` anchor).
        "command": "bun run test",
        "pass_pattern": r"Tests\s+(\d+)\s+passed",
        "fail_pattern": r"Tests\s+(\d+)\s+failed",
    },
]

# The dev-server command the real DevServer spawns (bmad-loop.ps1 ~516: `bun run dev`).
DEFAULT_DEV_SERVER_ARGV: tuple[str, ...] = ("bun", "run", "dev")

# Model tiers per phase. Empty string = INHERIT the user's Claude Code default model — the
# runner omits `--model` entirely (ClaudeRunner.run), matching bmad-loop.ps1 which never passed
# `--model`. Override per-phase via the loop.json `bmad.models` block if you want pinned tiers.
DEFAULT_MODELS: dict[str, str] = {
    "create": "",
    "dev": "",
    "review": "",
    "smoke": "",
    "retro": "",
    "decider": "",
}


@dataclass
class BmadConfig:
    """Typed config for the BMAD driver — mirrors the ``bmad-loop.ps1`` param block.

    ``project_root`` is REQUIRED (no ``/path/to/project`` default). Everything else takes the
    same defaults the PowerShell param block declared, but the gate stages, dev-server command
    and model tiers are OVERRIDABLE (the PS source hard-coded them).
    """

    project_root: str
    merge_base: str = "develop"
    max_stories: int = 100
    max_review_turns: int = 8
    max_smoke_iters: int = 3
    smoke_timeout_min: int = 12
    max_retro_turns: int = 10
    default_quota_wait_min: int = 30
    max_quota_waits: int = 30
    gate_stages: list[dict[str, Any]] = field(
        default_factory=lambda: [dict(s) for s in DEFAULT_GATE_STAGES]
    )
    dev_server_argv: tuple[str, ...] = DEFAULT_DEV_SERVER_ARGV
    models: dict[str, str] = field(default_factory=lambda: dict(DEFAULT_MODELS))
    epic_only: str | None = None
    story: str | None = None
    no_merge: bool = False
    no_retro: bool = False
    no_smoke: bool = False
    dry_run: bool = False
    auto_rollback: bool = False  # on a test regression, git reset --hard <baseline_commit>

    def model_for(self, phase: str) -> str:
        """Model tier for a phase (``create``/``dev``/``review``/``smoke``/``retro``/``decider``)."""
        return self.models.get(phase, DEFAULT_MODELS.get(phase, "sonnet"))

    @classmethod
    def from_args(cls, args: Any) -> BmadConfig:
        """Build from an argparse ``Namespace`` (the ``loop-bmad`` CLI surface)."""
        models = dict(DEFAULT_MODELS)
        return cls(
            project_root=args.project_root,
            merge_base=getattr(args, "merge_base", None) or "develop",
            max_stories=getattr(args, "max_stories", None) or 100,
            max_review_turns=getattr(args, "max_review_turns", None) or 8,
            max_smoke_iters=getattr(args, "max_smoke_iters", None) or 3,
            smoke_timeout_min=getattr(args, "smoke_timeout_min", None) or 12,
            max_retro_turns=getattr(args, "max_retro_turns", None) or 10,
            default_quota_wait_min=getattr(args, "default_quota_wait_min", None) or 30,
            max_quota_waits=getattr(args, "max_quota_waits", None) or 30,
            models=models,
            epic_only=getattr(args, "epic_only", None) or None,
            story=getattr(args, "story", None) or None,
            no_merge=bool(getattr(args, "no_merge", False)),
            no_retro=bool(getattr(args, "no_retro", False)),
            no_smoke=bool(getattr(args, "no_smoke", False)),
            dry_run=bool(getattr(args, "dry_run", False)),
            auto_rollback=bool(getattr(args, "auto_rollback", False)),
        )

    @classmethod
    def from_loop_json(cls, path_or_dict: Any) -> BmadConfig:
        """Build from a ``loop.json`` ``bmad`` block (or a raw dict of the same fields)."""
        import json

        if isinstance(path_or_dict, dict):
            data = path_or_dict
        else:
            data = json.loads(Path(path_or_dict).read_text(encoding="utf-8"))
        b = data.get("bmad", data) if isinstance(data, dict) else {}
        if b is None:
            b = {}
        kwargs: dict[str, Any] = {}
        if "project_root" in b or "projectRoot" in b:
            kwargs["project_root"] = b.get("project_root") or b.get("projectRoot")
        for snake, camel in (
            ("merge_base", "mergeBase"),
            ("max_stories", "maxStories"),
            ("max_review_turns", "maxReviewTurns"),
            ("max_smoke_iters", "maxSmokeIters"),
            ("smoke_timeout_min", "smokeTimeoutMin"),
            ("max_retro_turns", "maxRetroTurns"),
            ("default_quota_wait_min", "defaultQuotaWaitMin"),
            ("max_quota_waits", "maxQuotaWaits"),
            ("epic_only", "epicOnly"),
            ("story", "story"),
            ("no_merge", "noMerge"),
            ("no_retro", "noRetro"),
            ("no_smoke", "noSmoke"),
            ("dry_run", "dryRun"),
            ("auto_rollback", "autoRollback"),
        ):
            if snake in b:
                kwargs[snake] = b[snake]
            elif camel in b:
                kwargs[snake] = b[camel]
        if "gate_stages" in b:
            kwargs["gate_stages"] = list(b["gate_stages"])
        if "models" in b:
            kwargs["models"] = {**DEFAULT_MODELS, **dict(b["models"])}
        return cls(**kwargs)


# ---------------------------------------------------------------------------
# ResilientRunner — central quota survival around every phase's runner.run
# ---------------------------------------------------------------------------


class ResilientRunner(AgentRunner):
    """Quota-survival adapter wrapping a base runner (port of ``Invoke-ResilientClaude``).

    Implements :meth:`AgentRunner.run` by delegating to ``base_runner.run(...)``; when the
    result is quota-limited it calls :func:`loop.quota.survive` (the wait-and-resume driver) and
    RETRIES the same call once survival reports the backend is usable again. Because EVERY phase
    receives THIS wrapped runner, quota survival is centralized — no phase needs its own
    wait-loop. Capability flags + ``probe_quota`` / ``map_model`` delegate to the base so the
    phases' ``--resume`` threading still keys off the real backend's ``supports_sessions``.
    """

    def __init__(
        self,
        base_runner: AgentRunner,
        *,
        emit: Callable[[dict[str, Any]], None],
        quota_cfg: dict[str, Any] | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ):
        self._base = base_runner
        self._emit = emit
        self._sleep = sleep
        cfg = quota_cfg or {}
        self._default_wait_min = int(cfg.get("default_wait_min", 30))
        self._max_waits = int(cfg.get("max_waits", 30))
        self._cum = float(cfg.get("cum", 0.0))
        # Mirror the base backend's capabilities so phases branch identically.
        self.name = getattr(base_runner, "name", "resilient")
        self.supports_quota_probe = getattr(base_runner, "supports_quota_probe", False)
        self.supports_sessions = getattr(base_runner, "supports_sessions", False)
        self.supports_cache_telemetry = getattr(
            base_runner, "supports_cache_telemetry", False
        )

    def set_cum(self, cum: float) -> None:
        """Update the running cumulative cost the quota-hit/wait events report."""
        self._cum = float(cum)

    def run(self, **kwargs) -> AgentResult:  # type: ignore[override]
        label = "bmad-phase"
        while True:
            res = self._base.run(**kwargs)
            if not getattr(res, "quota_limited", False):
                return res
            recovered = survive(
                self._base,
                label=label,
                cum=self._cum,
                emit=self._emit,
                sleep=self._sleep,
                default_wait_min=self._default_wait_min,
                max_waits=self._max_waits,
            )
            if not recovered:
                # Give up — surface the quota-limited result so the phase stops.
                return res
            # recovered -> retry the SAME call.

    def probe_quota(self):
        return self._base.probe_quota()

    def map_model(self, tier: str) -> str:
        return self._base.map_model(tier)


# ---------------------------------------------------------------------------
# small helpers
# ---------------------------------------------------------------------------


def _artifacts_dir(project_root) -> Path:
    return Path(project_root).joinpath(*_ARTIFACTS_REL)


def _sprint_path(project_root) -> Path:
    return _artifacts_dir(project_root) / _SPRINT_FILE


def _load_sprint(project_root) -> SprintStatus:
    return parse_sprint_status(_sprint_path(project_root))


def _story_file(project_root, key: str) -> Path | None:
    """First ``<key>*.md`` story file in the artifacts dir (port of ``Get-ChildItem -Filter``)."""
    d = _artifacts_dir(project_root)
    if not d.is_dir():
        return None
    matches = sorted(p for p in d.glob(f"{key}*.md") if p.is_file())
    return matches[0] if matches else None


def _story_text(project_root, key: str) -> str:
    f = _story_file(project_root, key)
    if f is None:
        return ""
    try:
        return f.read_text(encoding="utf-8")
    except OSError:
        return ""


def _pending_retro(sprint: SprintStatus, *, epic_only: str | None) -> str | None:
    """Port of ``Get-PendingRetro``: lowest epic, all stories done, retro still ``optional``.

    Catch-up safe — finds a skipped retro regardless of how we got here. Restricted to
    ``epic_only`` when set.
    """
    candidates = []
    for e in sprint.epics:
        if e.retro != "optional":
            continue
        if epic_only is not None and e.key != epic_only:
            continue
        if epic_done(sprint, e.key):
            candidates.append(e.key)
    if not candidates:
        return None
    # epics are already sorted numerically in parse_sprint_status; pick the lowest.
    candidates.sort(key=lambda k: (not k.isdigit(), int(k) if k.isdigit() else 0, k))
    return candidates[0]


def _scope_pool(sprint: SprintStatus, *, epic_only: str | None) -> list[Story]:
    if epic_only is None:
        return list(sprint.stories)
    return [s for s in sprint.stories if s.epic == epic_only]


def _pid_alive(pid: int) -> bool:
    try:
        import psutil

        return psutil.pid_exists(pid)
    except Exception:
        try:
            os.kill(pid, 0)
        except OSError:
            return False
        except Exception:
            return True
        return True


def _acquire_lock(lock_path: Path) -> bool:
    """pid lockfile concurrency guard (mirrors :func:`loop.core._acquire_lock`)."""
    if lock_path.exists():
        try:
            existing = int((lock_path.read_text(encoding="utf-8") or "0").strip() or "0")
        except (ValueError, OSError):
            existing = 0
        if existing and existing != os.getpid() and _pid_alive(existing):
            return False
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path.write_text(str(os.getpid()), encoding="utf-8")
    return True


def _checkout(repo, ref: str) -> None:
    gitutil._git(["checkout", ref], repo)


def _ensure_branch(repo, branch: str, base: str) -> None:
    """Check out ``branch`` (off ``base`` when it doesn't yet exist) — PS branch dance ~722-727."""
    _checkout(repo, base)
    r = gitutil._git(["rev-parse", "--verify", "--quiet", branch], repo)
    if r.returncode != 0 or not (r.stdout or "").strip():
        gitutil._git(["checkout", "-b", branch], repo)
    else:
        _checkout(repo, branch)


def _commit_if_dirty(repo, message: str) -> None:
    if gitutil.is_dirty(repo):
        gitutil.add_all(repo)
        gitutil.commit(repo, message)


# ---------------------------------------------------------------------------
# the driver
# ---------------------------------------------------------------------------


def _resume_command(config: BmadConfig, state_dir: Any) -> str:
    """Reconstruct the ``loop-bmad`` command that resumes THIS run (checkpoint ``resume``, §7).

    The desktop / LAN control surface PREFERS this string when Reignite-ing a banked loop, so it
    must carry the REAL project root + state dir (not a ``<root>`` placeholder) and the flags that
    materially change the run. Paths with whitespace are quoted; the Rust resume parser
    (``parse_command_string``) understands the quoting.
    """

    def q(value: Any) -> str:
        s = str(value)
        return f'"{s}"' if (" " in s or "\t" in s) else s

    parts = [
        "loop-bmad",
        "--project-root", q(config.project_root),
        "--state-dir", q(state_dir),
        "--merge-base", q(config.merge_base),
    ]
    if config.epic_only:
        parts += ["--epic-only", q(config.epic_only)]
    if config.story:
        parts += ["--story", q(config.story)]
    if config.no_merge:
        parts.append("--no-merge")
    if config.no_retro:
        parts.append("--no-retro")
    if config.no_smoke:
        parts.append("--no-smoke")
    if config.auto_rollback:
        parts.append("--auto-rollback")
    # Round-trip the tuning knobs so a Reignite preserves them instead of silently reverting to
    # defaults (only emit the ones the user actually changed, to keep the command readable).
    for flag, value, default in (
        ("--max-stories", config.max_stories, 100),
        ("--max-review-turns", config.max_review_turns, 8),
        ("--max-smoke-iters", config.max_smoke_iters, 3),
        ("--smoke-timeout-min", config.smoke_timeout_min, 12),
        ("--max-retro-turns", config.max_retro_turns, 10),
        ("--default-quota-wait-min", config.default_quota_wait_min, 30),
        ("--max-quota-waits", config.max_quota_waits, 30),
    ):
        if value != default:
            parts += [flag, str(value)]
    return " ".join(parts)


def run(config: BmadConfig, *, runner: AgentRunner, state_dir, cwd=None) -> int:
    """Run the BMAD multi-story pipeline. Returns a process exit code.

    ``0`` = backlog complete / clean cooperative stop / dry-run; ``1`` = a halt/handoff (a
    phase failed, retro halted, merge incomplete); ``2`` = refused (a live concurrency lock).

    ``runner`` is the BASE :class:`AgentRunner` (wrapped in :class:`ResilientRunner` here so
    quota survival is central). ``state_dir`` holds ``log.jsonl`` / ``checkpoint.json`` / the
    STOP flag / the lock. ``cwd``, when given, overrides the dev-server / gate working dir
    (defaults to ``project_root``).
    """
    state = Path(state_dir)
    state.mkdir(parents=True, exist_ok=True)
    project_root = Path(config.project_root)
    repo = Path(cwd) if cwd is not None else project_root

    log_path = state / "log.jsonl"
    checkpoint_path = state / "checkpoint.json"
    stop_path = state / "STOP"
    lock_path = state / _LOCK_NAME

    def emit(event: dict[str, Any]) -> None:
        append_event(log_path, event)

    # --- dry-run: sprint scan + preflight plan, NO runner, NO lock (PS ~653-664) ---
    if config.dry_run:
        return _dry_run(config, project_root, repo)

    # --- concurrency guard --------------------------------------------------
    if not _acquire_lock(lock_path):
        print(f"[GUARD] another bmad driver is already running against '{state}'.")
        return 2

    try:
        return _run_inner(
            config,
            base_runner=runner,
            project_root=project_root,
            repo=repo,
            state=state,
            checkpoint_path=checkpoint_path,
            stop_path=stop_path,
            emit=emit,
        )
    finally:
        try:
            if lock_path.exists() and (
                lock_path.read_text(encoding="utf-8") or ""
            ).strip() == str(os.getpid()):
                os.remove(lock_path)
        except OSError:
            pass


def _dry_run(config: BmadConfig, project_root: Path, repo: Path) -> int:
    """Sprint scan + preflight gate; print the plan; return 0 (no runner, no quota)."""
    sprint = _load_sprint(project_root)
    pool = _scope_pool(sprint, epic_only=config.epic_only)
    first = select_actionable(pool)
    scope = (
        f"epic {config.epic_only} only"
        if config.epic_only
        else "ALL epics (until no story remains)"
    )
    print(
        f"Sprint scan: first actionable = {first.key if first else '(none)'} "
        f"(status {first.status if first else 'n/a'}). Scope = {scope}. "
        f"Merge base = {config.merge_base}."
    )
    print("\n--- Plan ---")
    print(
        "  Each story: [create?] -> dev-story -> code-review(Q&A) -> "
        f"{'(no smoke) ' if config.no_smoke else 'browser-smoke -> '}PR(base "
        f"{config.merge_base}) -> {'(no merge)' if config.no_merge else 'merge'}"
    )
    g = _run_gate(config, repo)
    print(f"\n--- Gate dry-run on current tree ---\n  green={g['green']}  {g['pass']} pass")
    print("\nDryRun complete. No runner calls, no branch changes, no quota spent.")
    return 0


def _run_gate(config: BmadConfig, repo: Path) -> dict[str, Any]:
    """Run the configured gate stages from ``repo``.

    ``run_gate`` honors ``cwd`` for string-command stages, so the repo is passed straight
    through (no shell ``cd`` wrapper). Callable hooks (tests) ignore ``cwd``.
    """
    from loop.gate import run_gate

    return run_gate(config.gate_stages, str(repo))


def _run_inner(
    config: BmadConfig,
    *,
    base_runner: AgentRunner,
    project_root: Path,
    repo: Path,
    state: Path,
    checkpoint_path: Path,
    stop_path: Path,
    emit: Callable[[dict[str, Any]], None],
) -> int:
    cum = 0.0
    resilient = ResilientRunner(
        base_runner,
        emit=emit,
        quota_cfg={
            "default_wait_min": config.default_quota_wait_min,
            "max_waits": config.max_quota_waits,
            "cum": cum,
        },
    )

    def gate_fn() -> dict[str, Any]:
        return _run_gate(config, repo)

    def smoke_server():
        return phases.DevServer(config.dev_server_argv, cwd=str(repo))

    def write_cp(stage: str, story: str | None, branch: str) -> None:
        cp = new_checkpoint(
            stage=stage,
            story=story,
            branch=branch,
            merge_base=config.merge_base,
            cum_usd=cum,
            resume=_resume_command(config, state),
        )
        write_checkpoint(checkpoint_path, cp)

    def honor_stop(scope: str, *, stage: str, story: str | None, branch: str) -> bool:
        """Cooperative stop at a safe boundary. Returns True when a stop was honored."""
        req = get_stop_mode(read_stop_flag(stop_path), scope=scope)
        if not req["honor"]:
            return False
        write_cp(stage, story, branch)
        emit(
            cooperative_stop_event(
                scope=scope,
                mode=req["mode"],
                stage=stage,
                story=story,
                branch=branch,
                cum=cum,
            )
        )
        clear_stop_flag(stop_path)
        print(f"[STOPPED] graceful stop honored ({req['mode']}) at {stage}")
        return True

    # --- preflight: parse sprint, ensure merge_base checked out (PS ~666-675) ---
    _checkout(repo, config.merge_base)

    processed = 0
    for si in range(1, config.max_stories + 1):
        resilient.set_cum(cum)
        sprint = _load_sprint(project_root)

        # Cleanest safe stop: between stories, repo clean on merge_base.
        write_cp("between-stories (clean)", None, config.merge_base)
        if honor_stop("story", stage="between-stories (clean)", story=None, branch=config.merge_base):
            return 0

        # --- EPIC RETROSPECTIVE (catch-up safe) ---------------------------------
        if not config.no_retro:
            pr_epic = _pending_retro(sprint, epic_only=config.epic_only)
            if pr_epic is not None:
                _checkout(repo, config.merge_base)
                emit(retro_start_event(epic=pr_epic))
                print(f"\n######## EPIC {pr_epic} RETROSPECTIVE (all stories done) ########")
                ok, cost = _run_retro(
                    config, resilient, pr_epic, emit=emit, cwd=str(repo)
                )
                cum += cost
                resilient.set_cum(cum)
                if not ok:
                    emit(bmad_stop_event(False, f"epic-{pr_epic} retrospective halted", cum))
                    return 1
                _commit_if_dirty(repo, f"retro(epic-{pr_epic}): epic {pr_epic} retrospective complete")
                continue  # re-scan for the next retro or story

        # --- SELECT the next actionable story -----------------------------------
        pool = _scope_pool(sprint, epic_only=config.epic_only)
        predicate = recovery.unmerged_done_predicate(
            repo=repo, merge_base=config.merge_base
        )
        if config.story and si == 1:
            target = next((s for s in sprint.stories if s.key == config.story), None)
        else:
            target = select_actionable(pool, is_unmerged_done=predicate)

        if target is None:
            scope_txt = f" in epic {config.epic_only}" if config.epic_only else " across any epic"
            emit(
                bmad_stop_event(
                    True,
                    f"backlog complete{scope_txt} — processed {processed} stories this run",
                    cum,
                )
            )
            print(f"[OK] backlog complete — processed {processed} stories.")
            return 0

        rc = _process_story(
            config,
            target,
            resilient=resilient,
            project_root=project_root,
            repo=repo,
            si=si,
            gate_fn=gate_fn,
            smoke_server=smoke_server,
            emit=emit,
            honor_stop=honor_stop,
            write_cp=write_cp,
            cum_box=[cum],
        )
        # _process_story returns (exit_or_None, new_cum). When exit is not None, stop now.
        exit_code, cum = rc
        resilient.set_cum(cum)
        if exit_code is not None:
            return exit_code
        processed += 1

    emit(
        bmad_stop_event(
            True, f"reached MaxStories backstop ({config.max_stories})", cum
        )
    )
    return 0


def _process_story(
    config: BmadConfig,
    target: Story,
    *,
    resilient: ResilientRunner,
    project_root: Path,
    repo: Path,
    si: int,
    gate_fn: Callable[[], dict[str, Any]],
    smoke_server: Callable[[], Any],
    emit: Callable[[dict[str, Any]], None],
    honor_stop: Callable[..., bool],
    write_cp: Callable[..., None],
    cum_box: list[float],
) -> tuple[int | None, float]:
    """Run one story through the pipeline. Returns ``(exit_or_None, cum)``.

    ``exit_or_None`` is ``None`` to continue the epic loop, or a process exit code to stop now
    (a halt/handoff -> 1, or a clean cooperative-stop / terminal-ok -> 0). ``cum`` is the
    updated cumulative cost.
    """
    cum = cum_box[0]
    resume_tail = target.status == "done"  # only First-UnmergedDone yields a 'done' target
    epic = target.epic or (target.key.split("-")[0])
    branch = f"feat/story-{target.key}"

    emit(story_start_event(story=target.key, status=target.raw_status, epic=epic, index=si))
    print(f"\n######## EPIC {epic} — STORY {target.key} ({target.raw_status}) — #{si} ########")

    _ensure_branch(repo, branch, config.merge_base)

    baseline = gate_fn()
    baseline_pass = int(baseline.get("pass", 0))
    floor_pass = baseline_pass
    emit(start_event(target=target.key, branch=branch, baseline_pass=baseline_pass))

    # entry status: prefer the story file's Status:, else the sprint status.
    text = _story_text(project_root, target.key)
    meta = story_meta(text)
    st = (meta.get("status") or target.raw_status or "").strip().lower()
    if resume_tail:
        st = "done"
        print(f"  resuming {target.key} at browser-smoke + merge")

    create_model = config.model_for("create")
    dev_model = config.model_for("dev")
    review_model = config.model_for("review")
    smoke_model = config.model_for("smoke")
    decider_model = config.model_for("decider")

    # --- PHASE: create-story (only a fresh 'backlog' story) ---------------------
    if st == "backlog" and not resume_tail:
        def produced() -> bool:
            sp = _load_sprint(project_root)
            for s in sp.stories:
                if s.epic == epic and s.status == "ready":
                    return True
            return any(s.status == "ready" for s in sp.stories)

        res = phases.create_story(
            resilient,
            emit=emit,
            cwd=str(repo),
            model=create_model,
            produced=produced,
        )
        cum += res.cost
        resilient.set_cum(cum)
        if not res.ok:
            emit(bmad_stop_event(False, res.reason or "create-story failed", cum))
            return 1, cum
        # re-read the freshly created story's status.
        sp = _load_sprint(project_root)
        made = next((s for s in sp.stories if s.epic == epic and s.status == "ready"), None)
        if made is None:
            made = next((s for s in sp.stories if s.status == "ready"), None)
        if made is not None:
            target = made
            branch = f"feat/story-{target.key}"
            _ensure_branch(repo, branch, config.merge_base)
            text = _story_text(project_root, target.key)
        st = "ready"

    # --- PHASE: dev-story (ready / in-progress) ---------------------------------
    if st in ("ready", "ready-for-dev", "in-progress"):
        res = phases.dev_story(
            resilient,
            target.key,
            emit=emit,
            cwd=str(repo),
            gate_fn=gate_fn,
            model=dev_model,
            baseline_pass=baseline_pass,
            cum=cum,
            status="review",
        )
        cum += res.cost
        resilient.set_cum(cum)
        dev_gate = res.gate or {}
        # codegen failure is a distinct P1 blocker (bmad-loop.ps1 ~780) — report it before the
        # generic "not green" so the handoff is actionable.
        if not res.ok and not phases._stage_ok(dev_gate, "codegen"):
            emit(bmad_stop_event(False, f"codegen failed (P1 blocker) for {target.key}", cum))
            return 1, cum
        # regression guard (bmad-loop.ps1 ~781-788): a drop in passing tests vs the branch
        # baseline halts — auto-rollback to baseline_commit when enabled, else report the exact
        # revert command. Fires even on an otherwise-green gate (e.g. tests were deleted).
        dev_pass = int(dev_gate.get("pass", baseline_pass))
        if dev_pass < baseline_pass:
            base_commit = story_meta(_story_text(project_root, target.key)).get("baseline")
            if config.auto_rollback and base_commit:
                gitutil._git(["reset", "--hard", base_commit], repo)
                emit(bmad_stop_event(
                    False,
                    f"regression on {target.key}: tests {baseline_pass}->{dev_pass}; "
                    f"auto-rolled back to baseline_commit {base_commit}",
                    cum,
                ))
                return 1, cum
            revert = f'git -C "{project_root}" reset --hard {base_commit}' if base_commit else "(no baseline_commit recorded)"
            emit(bmad_stop_event(
                False,
                f"regression on {target.key}: passing tests dropped {baseline_pass}->{dev_pass}. "
                f"Work on {branch}. Revert: {revert}",
                cum,
            ))
            return 1, cum
        if not res.ok:
            emit(bmad_stop_event(False, res.reason or f"dev-story not green for {target.key}", cum))
            return 1, cum
        floor_pass = int(dev_gate.get("pass", floor_pass))
        _commit_if_dirty(repo, f"feat({target.key}): dev-story complete — {floor_pass} tests green")
        write_cp(f"dev-story done ({target.key})", target.key, branch)
        if honor_stop("phase", stage=f"dev-story done ({target.key})", story=target.key, branch=branch):
            return 0, cum
        st = "review"

    # --- PHASE: code-review (Q&A) -----------------------------------------------
    if st == "review":
        res = phases.code_review(
            resilient,
            review_decider,
            target.key,
            emit=emit,
            cwd=str(repo),
            gate_fn=gate_fn,
            max_turns=config.max_review_turns,
            model=review_model,
            decider_model=decider_model,
        )
        cum += res.cost
        resilient.set_cum(cum)
        if not res.ok:
            emit(bmad_stop_event(False, res.reason or f"code-review halted for {target.key}", cum))
            return 1, cum
        # regression guard (bmad-loop.ps1 ~808): review must not drop the passing-test floor.
        review_pass = int((res.gate or {}).get("pass", floor_pass))
        if review_pass < floor_pass:
            emit(bmad_stop_event(
                False,
                f"regression on {target.key}: post-review tests {floor_pass}->{review_pass}. Work on {branch}.",
                cum,
            ))
            return 1, cum
        floor_pass = max(floor_pass, review_pass)
        _commit_if_dirty(repo, f"review({target.key}): apply code-review outcomes — {floor_pass} green")
        write_cp(f"code-review done ({target.key})", target.key, branch)
        if honor_stop("phase", stage=f"code-review done ({target.key})", story=target.key, branch=branch):
            return 0, cum

    # --- PHASE: browser-smoke (unless no_smoke) ---------------------------------
    if not config.no_smoke:
        res = phases.browser_smoke(
            resilient,
            target.key,
            text,
            emit=emit,
            cwd=str(repo),
            server_ctl=smoke_server(),
            gate_fn=gate_fn,
            max_iters=config.max_smoke_iters,
            timeout_sec=config.smoke_timeout_min * 60,
            model=smoke_model,
        )
        cum += res.cost
        resilient.set_cum(cum)
        if not res.ok:
            emit(bmad_stop_event(False, res.reason or f"browser-smoke halted for {target.key}", cum))
            return 1, cum
        # regression guard (bmad-loop.ps1 ~615): post-smoke gate must not drop the floor.
        smoke_pass = int((res.gate or {}).get("pass", floor_pass))
        if smoke_pass < floor_pass:
            emit(bmad_stop_event(
                False,
                f"regression on {target.key}: post-smoke tests {floor_pass}->{smoke_pass}. Work on {branch}.",
                cum,
            ))
            return 1, cum
        _commit_if_dirty(repo, f"smoke({target.key}): browser smoke fixes")

    # --- PHASE: PR (always) -----------------------------------------------------
    gitutil._git(["push", "-u", "origin", branch], repo)
    title = f"feat({target.key}): automated by loop-bmad"
    body = (
        f"Automated end-to-end by loop-bmad: dev-story + code-review"
        f"{'' if config.no_smoke else ' + AC-aware browser smoke'}, all green.\n\n"
        f"- Story: {target.key}  (epic {epic})\n"
    )
    pr_url = pr.create_pr(branch=branch, base=config.merge_base, title=title, body=body, cwd=str(repo))
    emit(pr_created_event(story=target.key, branch=branch, base=config.merge_base, url=pr_url))
    print(f"  PR (base {config.merge_base}): {pr_url}")

    if config.no_merge:
        emit(bmad_stop_event(True, f"story {target.key} PR opened (not merged, --no-merge): {pr_url}", cum))
        print(f"[OK] PR opened for {target.key}; --no-merge -> stopping.")
        return 0, cum

    # --- PHASE: merge -----------------------------------------------------------
    _checkout(repo, config.merge_base)
    try:
        pr.merge_pr(branch=branch, base=config.merge_base, cwd=str(repo))
    except pr.PrError as e:
        emit(bmad_stop_event(False, f"auto-merge for {target.key} failed: {e}", cum))
        return 1, cum
    gitutil._git(["pull", "origin", config.merge_base], repo)
    # Verify the merge actually landed (bmad-loop.ps1 ~857): `gh pr merge` can exit 0 while the
    # PR is only QUEUED behind branch protection. Branching the next story off an un-merged base
    # would stack/lose work, so we confirm state == MERGED before continuing.
    state = pr.pr_state(branch=branch, cwd=str(repo))
    if state != "MERGED":
        emit(bmad_stop_event(
            False,
            f"auto-merge for {target.key} did not complete (PR state: {state!r}); merge it manually.",
            cum,
        ))
        return 1, cum
    emit(pr_merged_event(story=target.key, base=config.merge_base, pr=pr_url))
    print(f"  merged {target.key} -> {config.merge_base}.")
    return None, cum


# ---------------------------------------------------------------------------
# epic retrospective (Q&A loop — ported from Invoke-EpicRetroPhase ~456-499)
# ---------------------------------------------------------------------------

_RETRO_PROMPT_TEMPLATE = (
    "You are running HEADLESSLY via 'claude -p' — NON-INTERACTIVE. Do NOT greet. IMMEDIATELY "
    "invoke the bmad-retrospective skill to run the retrospective for EPIC {epic} (all its "
    "stories are done).\n"
    "You are standing in for an interactive team, so use this protocol:\n"
    "- When the skill needs a decision/answer from the team, output EXACTLY one line beginning "
    "'QUESTION:' with the question and any options, then STOP and end your turn. One question "
    "per turn; never repeat one.\n"
    "- When you receive the answer, continue the retrospective.\n"
    "- When it is fully complete (retro artifact written AND sprint-status "
    "epic-{epic}-retrospective set to 'done'), output one line beginning 'RETRO_COMPLETE:' with "
    "a one-line summary."
)

_RETRO_COMPLETE_RX = re.compile(r"^.*?RETRO_COMPLETE:?\s*(.*)$", re.MULTILINE)


def _run_retro(
    config: BmadConfig,
    runner: AgentRunner,
    epic: str,
    *,
    emit: Callable[[dict[str, Any]], None],
    cwd,
) -> tuple[bool, float]:
    """Run the epic retrospective Q&A loop. Returns ``(ok, cost)``.

    Faithful to ``Invoke-EpicRetroPhase``: loop while the facilitator emits a ``QUESTION:``
    marker (bounded by ``max_retro_turns``), answering via :func:`retro_decider` and feeding the
    answer back via ``--resume`` (when the runner supports sessions; else single-shot). A
    ``RETRO_COMPLETE:`` marker — or NO marker — completes; exceeding the turn cap is a halt.
    """
    prompt = _RETRO_PROMPT_TEMPLATE.format(epic=epic)
    session_id: str | None = None
    answer: str | None = None
    total_cost = 0.0
    decider_model = config.model_for("decider")
    retro_model = config.model_for("retro")
    use_sessions = getattr(runner, "supports_sessions", False)

    for turn in range(1, config.max_retro_turns + 1):
        if session_id is None or not use_sessions:
            res = runner.run(
                prompt=prompt if session_id is None else (answer or ""),
                model=retro_model,
                allowed_tools=list(phases.BMAD_TOOLS),
                permission_mode="acceptEdits",
                max_turns=0,
                cwd=cwd,
            )
        else:
            res = runner.run(
                prompt=answer or "",
                model=retro_model,
                allowed_tools=list(phases.BMAD_TOOLS),
                permission_mode="acceptEdits",
                max_turns=0,
                cwd=cwd,
                resume_session=session_id,
            )
        total_cost += float(getattr(res, "cost_usd", 0.0) or 0.0)

        if getattr(res, "is_error", False) or getattr(res, "parse_failed", False):
            return False, total_cost
        if session_id is None and use_sessions:
            session_id = getattr(res, "session_id", None)

        text = getattr(res, "text", "") or ""
        cm = _RETRO_COMPLETE_RX.search(text)
        if cm:
            emit(retro_complete_event(epic=epic, summary=cm.group(1).strip()))
            return True, total_cost
        q = question_marker(text)
        if q is not None:
            emit(retro_question_event(epic=epic, turn=turn, q=q))
            answer = retro_decider(runner, question=q, epic_scope=epic, model=decider_model)
            emit(retro_answer_event(epic=epic, turn=turn, a=answer))
            continue
        # No marker -> treat as complete (don't spin).
        emit(retro_complete_event(epic=epic, summary="no-marker"))
        return True, total_cost

    return False, total_cost
