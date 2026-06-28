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

import json
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
    engine_start_event,
    gate_retry_event,
    new_checkpoint,
    pr_created_event,
    pr_merged_event,
    retro_answer_event,
    retro_complete_event,
    retro_question_event,
    retro_start_event,
    start_event,
    story_start_event,
    token_usage_event,
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
# runner omits `--model` entirely (ClaudeRunner.run). Override per-phase via the loop.json
# `bmad.models` block; set ALL to "" to restore strict bmad-loop.ps1 parity (it never passed
# `--model`, so every phase ran on the inherited default — almost always Opus on a Max plan).
#
# COST-AWARE DEFAULTS (the binding constraint on a Max subscription is TOKENS, and Opus draws
# the scarce weekly-Opus budget fastest). Everything except `dev` routes to a cheaper tier that
# is plenty for the task: the deciders are one-shot, no-tools, single-turn Q&A (the textbook
# `haiku` job — and their `decider.py` default is `haiku`, which an empty tier here would silently
# override back to Opus); create-story is a deterministic skill invocation; code-review /
# browser-smoke / retro are well within Sonnet's range, and the EXTERNAL gate (codegen+lint+test)
# remains the real arbiter of correctness regardless of the review model.
#
# `dev` (the actual implementation) INHERITS the user's Claude Code default so their global model
# choice wins. NOTE: dev-story is the single heaviest phase — an Opus dev-story was measured at
# ~44% of a 5-hour window. For quota-tight runs, pin `dev: "sonnet"` in loop.json; reserve
# `"opus"` for the hardest stories or when you have weekly-Opus budget to spare.
DEFAULT_MODELS: dict[str, str] = {
    "create": "sonnet",
    "dev": "",  # inherit (quota-tight -> pin "sonnet"; hardest stories -> "opus")
    "review": "sonnet",
    "smoke": "sonnet",
    "retro": "sonnet",
    "decider": "haiku",
}

# Reasoning-effort tier per phase (the verified `claude --effort` flag; low|medium|high|xhigh|max).
# Empty string = INHERIT the user's Claude Code effort default (the runner omits `--effort`), so
# the ENGINE default is byte-parity with no effort flag at all — additive/default-off like the
# other capabilities. Set per-phase via loop.json `bmad.effort`, e.g.
# {"dev": "xhigh", "review": "xhigh", "decider": "low"}.
# NOTE: keep deciders LOW — high/xhigh makes a model OVER-deliberate a bounded one-shot decision
# (more tokens, worse decisiveness). Spend xhigh where reasoning cuts iterations (dev, review).
DEFAULT_EFFORTS: dict[str, str] = {
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
    # On a branch-protected merge-base, `gh pr merge` exits 0 but the PR sits QUEUED behind
    # required checks, so pr_state != "MERGED" and the driver would halt. >0 polls pr_state up to
    # this many seconds for the merge to land before halting, so the loop rolls into the next story
    # instead of stopping. 0 = the strict (parity) behavior: halt immediately if not yet MERGED.
    merge_wait_sec: int = 0
    gate_stages: list[dict[str, Any]] = field(
        default_factory=lambda: [dict(s) for s in DEFAULT_GATE_STAGES]
    )
    # Flaky-test tolerance for the external gate. A RED gate whose only failing stage is `test`,
    # with codegen+lint green and a SMALL nonzero fail count, is the signature of a flaky /
    # timing-sensitive test (it passes on a clean re-run) â€” NOT a real regression. `_run_gate`
    # re-runs such a gate up to `gate_flaky_retries` times (first green wins); a deterministic
    # failure (codegen/lint red, or fail > gate_flaky_max_fail) is reported at once, no retry.
    # This stops one flaky test from turning an otherwise-green run into a hard terminal stop.
    gate_flaky_retries: int = 2
    gate_flaky_max_fail: int = 2
    dev_server_argv: tuple[str, ...] = DEFAULT_DEV_SERVER_ARGV
    models: dict[str, str] = field(default_factory=lambda: dict(DEFAULT_MODELS))
    effort: dict[str, str] = field(default_factory=lambda: dict(DEFAULT_EFFORTS))
    epic_only: str | None = None
    story: str | None = None
    no_merge: bool = False
    no_retro: bool = False
    no_smoke: bool = False
    dry_run: bool = False
    auto_rollback: bool = False  # on a test regression, git reset --hard <baseline_commit>
    # Phase EXECUTION modes (default = the faithful PS multi-turn behavior).
    #   review_mode "qa" (default): the reviewer emits QUESTION: markers; a separate cheap
    #     decider answers each via --resume (N cold-start processes per review).
    #   review_mode "single-pass": ONE warm process — the reviewer decides + applies each finding
    #     itself (the decider principles are folded into its prompt), no Q&A round-trips.
    #   smoke_mode "iterative" (default): up to max_smoke_iters cold re-spawns on FAIL.
    #   smoke_mode "single-pass": ONE process; the agent does all open/test/fix/re-test internally
    #     (bounded by the wall-clock timeout), no harness re-spawn.
    #   retro_mode "qa" (default): the facilitator emits QUESTION: markers answered by the decider.
    #   retro_mode "single-pass": ONE warm process — the facilitator decides for itself, no Q&A.
    review_mode: str = "qa"
    smoke_mode: str = "iterative"
    retro_mode: str = "qa"
    # The --loop-json config path, if one was used. Per-phase models/effort have NO CLI flag — they
    # live ONLY in this file — so a Reignite from the checkpoint `resume` string must re-point at it
    # to restore the full tuning (else it silently reverts models/effort to defaults). "" = none.
    loop_json: str = ""

    def model_for(self, phase: str) -> str:
        """Model tier for a phase (``create``/``dev``/``review``/``smoke``/``retro``/``decider``)."""
        return self.models.get(phase, DEFAULT_MODELS.get(phase, "sonnet"))

    def effort_for(self, phase: str) -> str:
        """Reasoning-effort tier for a phase (``""`` = inherit the user's Claude Code default)."""
        return self.effort.get(phase, DEFAULT_EFFORTS.get(phase, ""))

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
            merge_wait_sec=getattr(args, "merge_wait_sec", None) or 0,
            models=models,
            effort=dict(DEFAULT_EFFORTS),
            epic_only=getattr(args, "epic_only", None) or None,
            story=getattr(args, "story", None) or None,
            no_merge=bool(getattr(args, "no_merge", False)),
            no_retro=bool(getattr(args, "no_retro", False)),
            no_smoke=bool(getattr(args, "no_smoke", False)),
            dry_run=bool(getattr(args, "dry_run", False)),
            auto_rollback=bool(getattr(args, "auto_rollback", False)),
            review_mode=getattr(args, "review_mode", None) or "qa",
            smoke_mode=getattr(args, "smoke_mode", None) or "iterative",
            retro_mode=getattr(args, "retro_mode", None) or "qa",
            loop_json=getattr(args, "loop_json", None) or "",
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
            ("merge_wait_sec", "mergeWaitSec"),
            ("epic_only", "epicOnly"),
            ("story", "story"),
            ("no_merge", "noMerge"),
            ("no_retro", "noRetro"),
            ("no_smoke", "noSmoke"),
            ("dry_run", "dryRun"),
            ("auto_rollback", "autoRollback"),
            ("review_mode", "reviewMode"),
            ("smoke_mode", "smokeMode"),
            ("retro_mode", "retroMode"),
            ("gate_flaky_retries", "gateFlakyRetries"),
            ("gate_flaky_max_fail", "gateFlakyMaxFail"),
        ):
            if snake in b:
                kwargs[snake] = b[snake]
            elif camel in b:
                kwargs[snake] = b[camel]
        if "gate_stages" in b:
            kwargs["gate_stages"] = list(b["gate_stages"])
        if "models" in b:
            kwargs["models"] = {**DEFAULT_MODELS, **dict(b["models"])}
        if "effort" in b:
            kwargs["effort"] = {**DEFAULT_EFFORTS, **dict(b["effort"])}
        return cls(**kwargs)


# ---------------------------------------------------------------------------
# ResilientRunner — central quota survival around every phase's runner.run
# ---------------------------------------------------------------------------


def _usage_tokens(usage: Any) -> tuple[int, int, int, int]:
    """Pull ``(input, output, cache_read, cache_creation)`` from a claude ``usage`` block.

    Self-contained (mirrors :func:`loop.cache.get_cache_usage`'s tolerant unwrapping) so the
    telemetry can also surface ``output_tokens`` — which ``CacheUsage`` does not carry — without
    touching the golden-tested ``cache.py``. Accepts a dict, a raw JSON string, or a full result
    object carrying a nested ``usage``; every field defaults to 0 when absent (older claude
    builds / text-format results emit no usage counters), so a result with no telemetry yields
    ``(0, 0, 0, 0)`` and is skipped by the caller.
    """
    u: Any = usage
    if isinstance(u, str):
        try:
            u = json.loads(u)
        except (ValueError, TypeError):
            u = None
    if isinstance(u, dict) and u.get("usage"):
        u = u["usage"]

    def g(*names: str) -> int:
        if not isinstance(u, dict):
            return 0
        for n in names:
            v = u.get(n)
            if v is None:
                continue
            try:
                return int(float(v))
            except (TypeError, ValueError):
                continue
        return 0

    return (
        g("input_tokens", "inputTokens"),
        g("output_tokens", "outputTokens"),
        g("cache_read_input_tokens", "cacheReadInputTokens"),
        g("cache_creation_input_tokens", "cacheCreationInputTokens"),
    )


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
        # Phase/story context + cumulative TOKEN draw — the REAL Max-plan meter (USD ``cum`` is
        # meaningless on a subscription). ``set_context`` is called by the driver before each
        # phase so every ``token-usage`` event is tagged with which phase + story spent the
        # tokens. Cache *reads* are tracked separately because they barely count against the rate
        # budget, so a high ``cumCacheRead`` relative to ``cumInput`` is the GOOD sign.
        self._phase = "bmad-phase"
        self._story: str | None = None
        self._cum_input = 0
        self._cum_output = 0
        self._cum_cache_read = 0
        self._cum_cache_creation = 0
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

    def set_context(self, phase: str, story: str | None = None) -> None:
        """Tag subsequent runs' ``token-usage`` telemetry with the current phase + story.

        Called by the driver at each phase boundary. Deciders invoked inside a phase inherit
        that phase's label (they are part of its cost); their ``haiku`` ``model`` tag still
        distinguishes them within the phase. A no-op for token accounting itself.
        """
        self._phase = phase
        self._story = story

    def _record_usage(self, res: AgentResult, model: str) -> None:
        """Emit a per-call ``token-usage`` event from the result's ``usage`` block.

        Tokens are the Max-plan meter (USD is not), and the data is already in claude's
        ``--output-format json`` response — so this costs ZERO extra tokens, it just stops
        throwing the numbers away. Results with no usage telemetry (mock runners / text-format)
        carry all-zero counters and emit nothing, keeping non-claude tests' logs clean.
        """
        inp, out, cr, cc = _usage_tokens(getattr(res, "usage", None))
        if not (inp or out or cr or cc):
            return
        self._cum_input += inp
        self._cum_output += out
        self._cum_cache_read += cr
        self._cum_cache_creation += cc
        denom = cr + inp
        hit_ratio = (cr / float(denom)) if denom else 0.0
        self._emit(
            token_usage_event(
                phase=self._phase,
                story=self._story,
                model=str(model or ""),
                input_tokens=inp,
                output_tokens=out,
                cache_read=cr,
                cache_creation=cc,
                hit_ratio=hit_ratio,
                warm=cr > 0,
                cost_usd=float(getattr(res, "cost_usd", 0.0) or 0.0),
                cum_input=self._cum_input,
                cum_output=self._cum_output,
                cum_cache_read=self._cum_cache_read,
                cum_cache_creation=self._cum_cache_creation,
            )
        )

    def run(self, **kwargs) -> AgentResult:  # type: ignore[override]
        label = "bmad-phase"
        while True:
            res = self._base.run(**kwargs)
            if not getattr(res, "quota_limited", False):
                self._record_usage(res, kwargs.get("model", ""))
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
    # Re-point at the --loop-json file FIRST: it carries the per-phase models/effort (which have no
    # CLI flag) plus the phase modes, so a Reignite restores the FULL tuning, not just the modes
    # round-tripped below. (The mode flags below stay for runs that set modes WITHOUT a file.)
    if config.loop_json:
        parts += ["--loop-json", q(config.loop_json)]
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
    if config.review_mode and config.review_mode != "qa":
        parts += ["--review-mode", config.review_mode]
    if config.smoke_mode and config.smoke_mode != "iterative":
        parts += ["--smoke-mode", config.smoke_mode]
    if config.retro_mode and config.retro_mode != "qa":
        parts += ["--retro-mode", config.retro_mode]
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
        ("--merge-wait-sec", config.merge_wait_sec, 0),
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
        # Heartbeat (first thing under the lock): emit BEFORE the slow preflight (git checkout of
        # merge-base + baseline gate) so a watching UI flips to "running" within ~1s of spawn
        # instead of seeing an empty log for the whole cold start. Inside the try so a failure here
        # still reaches the lock-cleanup finally.
        emit(engine_start_event(merge_base=config.merge_base))
        # Self-advertise the cooperative-stop control (the loop never gets killed mid-step; a
        # request is honored at the next safe boundary — between stories, after dev / review /
        # smoke, and right after a story's merge).
        print(
            f"[loop running] continuous until backlog empty. To stop cleanly: "
            f"loop-stop --state-dir {state} --after-story   (or --now / --status / --cancel)"
        )
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


def _is_flaky_shape(gate: dict[str, Any], max_fail: int) -> bool:
    """True when a RED gate looks like a flaky ``test`` failure rather than a real regression.

    The flaky signature is: ``codegen`` and ``lint`` both green, the ``test`` stage is the one
    that failed, and the fail count is small + nonzero (``0 < fail <= max_fail``). A deterministic
    failure (codegen/lint red, or a large fail count = a genuine regression) returns ``False`` so
    the gate fails FAST instead of wasting retries. A stage that isn't present is treated as ok
    (so a gate configured without codegen/lint still works).
    """
    stages = {s.get("name"): s for s in gate.get("stages", [])}
    codegen_ok = stages.get("codegen", {}).get("ok", True)
    lint_ok = stages.get("lint", {}).get("ok", True)
    test_ok = stages.get("test", {}).get("ok", True)
    fail = int(gate.get("fail", 0))
    return bool(codegen_ok and lint_ok and not test_ok and 0 < fail <= max_fail)


def _run_gate(
    config: BmadConfig,
    repo: Path,
    emit: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    """Run the configured gate stages from ``repo``, with flaky-test tolerance.

    ``run_gate`` honors ``cwd`` for string-command stages, so the repo is passed straight
    through (no shell ``cd`` wrapper). Callable hooks (tests) ignore ``cwd``.

    A red gate whose shape matches :func:`_is_flaky_shape` (a small flaky-looking ``test`` failure
    with codegen+lint green) is RE-RUN up to ``config.gate_flaky_retries`` times â€” the first green
    result wins. This stops a single flaky/timing-sensitive test from turning an otherwise-green
    run into a hard terminal stop (the recurring failure mode), while a real regression
    (deterministic, or many failures) still fails immediately. Re-running is idempotent (codegen
    regenerates the same files; lint/test are read-only); the only cost is time on a RED gate.
    Each retry emits a ``gate-retry`` event (when ``emit`` is given) so the flakiness is visible.
    """
    from loop.gate import run_gate

    g = run_gate(config.gate_stages, str(repo))
    for attempt in range(1, max(0, config.gate_flaky_retries) + 1):
        if g.get("green") or not _is_flaky_shape(g, config.gate_flaky_max_fail):
            return g
        if emit is not None:
            emit(
                gate_retry_event(
                    attempt=attempt,
                    retries=config.gate_flaky_retries,
                    fail=int(g.get("fail", 0)),
                    max_fail=config.gate_flaky_max_fail,
                )
            )
        g = run_gate(config.gate_stages, str(repo))
    return g


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
        return _run_gate(config, repo, emit)

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
        print(f"          resume: {_resume_command(config, state)}")
        return True

    # --- preflight: parse sprint, ensure merge_base checked out (PS ~666-675) ---
    _checkout(repo, config.merge_base)

    processed = 0
    # Spin guard: remember the last story we processed. If select_actionable hands back the SAME
    # story at the SAME status on the next pass, the previous pass RAN but did NOT advance it (e.g.
    # review/smoke/merge ran yet sprint-status still says 'review'), so the loop would re-run it
    # forever and burn quota. Halt with an actionable reason instead (same spirit as the retro
    # flag-check above). A story that legitimately advances changes status between passes, so this
    # only fires on a genuine no-progress re-selection.
    last_key: str | None = None
    last_status: str | None = None
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
                # Sync the merge base before reflecting + writing the retro (bmad-loop.ps1 ~693)
                # so the retro commit fast-forwards cleanly when pushed below.
                gitutil._git(["pull", "origin", config.merge_base], repo)
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
                # Push the retro commit to the merge base (bmad-loop.ps1 ~698-700 pushed it): the
                # artifact + 'done' flag must land on origin/<merge_base>, not stay a local commit
                # that later rides along in (and pollutes) the next story's PR diff.
                gitutil._git(["push", "origin", config.merge_base], repo)
                # The skill is responsible for flipping epic-N-retrospective to 'done'. Verify it
                # actually did — otherwise the still-'optional' flag would re-trigger this same
                # retro on every iteration (up to --max-stories). Halt with an actionable message
                # instead of silently re-running.
                if any(
                    e.key == pr_epic and e.retro == "optional"
                    for e in _load_sprint(project_root).epics
                ):
                    emit(bmad_stop_event(
                        False,
                        f"epic-{pr_epic} retrospective reported complete but its sprint-status "
                        f"flag is still 'optional' — the bmad-retrospective skill did not mark it "
                        f"done; set it 'done' (or re-run) before resuming.",
                        cum,
                    ))
                    return 1
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

        # Spin guard (see above): the same story re-selected at the same status = no progress.
        if target.key == last_key and target.status == last_status:
            emit(bmad_stop_event(
                False,
                f"story {target.key} was re-selected at status '{target.raw_status}' WITHOUT "
                f"advancing — the previous pass ran (e.g. review/smoke/merge) but did not move it "
                f"forward, so the loop would re-run it indefinitely (burning quota). Its "
                f"sprint-status is most likely stuck at '{target.raw_status}' — a reviewed+merged "
                f"story must reach 'done'. Set {target.key} to 'done' in sprint-status.yaml if it "
                f"merged (or fix the phase that should advance it), then resume: "
                f"{_resume_command(config, state)}",
                cum,
            ))
            print(
                f"\n[HALT] story {target.key} stuck at '{target.raw_status}' — re-selected without "
                "progress (would loop + burn quota). See the stop reason / resume command above."
            )
            return 1
        last_key, last_status = target.key, target.status

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
            resume_cmd=_resume_command(config, state),
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
    resume_cmd: str = "",
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
    create_effort = config.effort_for("create")
    dev_effort = config.effort_for("dev")
    review_effort = config.effort_for("review")
    smoke_effort = config.effort_for("smoke")
    decider_effort = config.effort_for("decider")

    # --- PHASE: create-story (only a fresh 'backlog' story) ---------------------
    if st == "backlog" and not resume_tail:
        def produced() -> bool:
            sp = _load_sprint(project_root)
            for s in sp.stories:
                if s.epic == epic and s.status == "ready":
                    return True
            return any(s.status == "ready" for s in sp.stories)

        resilient.set_context("create-story", target.key)
        res = phases.create_story(
            resilient,
            emit=emit,
            cwd=str(repo),
            model=create_model,
            effort=create_effort,
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
        resilient.set_context("dev-story", target.key)
        res = phases.dev_story(
            resilient,
            target.key,
            emit=emit,
            cwd=str(repo),
            gate_fn=gate_fn,
            model=dev_model,
            effort=dev_effort,
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

    # --- PHASE: code-review (Q&A loop, or single-pass) --------------------------
    if st == "review":
        resilient.set_context("code-review", target.key)
        if config.review_mode == "single-pass":
            # ONE warm process: the reviewer decides + applies each finding itself (decider
            # principles folded into the prompt), no QUESTION/--resume round-trips.
            res = phases.code_review_single_pass(
                resilient,
                target.key,
                emit=emit,
                cwd=str(repo),
                gate_fn=gate_fn,
                model=review_model,
                effort=review_effort,
            )
        else:
            res = phases.code_review(
                resilient,
                review_decider,
                target.key,
                emit=emit,
                cwd=str(repo),
                gate_fn=gate_fn,
                max_turns=config.max_review_turns,
                model=review_model,
                effort=review_effort,
                decider_model=decider_model,
                decider_effort=decider_effort,
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
        resilient.set_context("browser-smoke", target.key)
        # single-pass => ONE process; the agent does all open/test/fix/re-test internally
        # (bounded by the per-iter wall-clock timeout) instead of the harness cold-re-spawning.
        smoke_iters = 1 if config.smoke_mode == "single-pass" else config.max_smoke_iters
        # Tell smoke WHICH files this story changed (vs its baseline_commit) so it drives the
        # story's ACTUAL surfaces, not a generic health check. None when no baseline is recorded
        # (then the prompt is the untargeted-but-AC-aware form, as before).
        base_commit = story_meta(_story_text(project_root, target.key)).get("baseline")
        changed = gitutil.diff_name_only(repo, base_commit) if base_commit else None
        res = phases.browser_smoke(
            resilient,
            target.key,
            text,
            emit=emit,
            cwd=str(repo),
            server_ctl=smoke_server(),
            gate_fn=gate_fn,
            max_iters=smoke_iters,
            timeout_sec=config.smoke_timeout_min * 60,
            model=smoke_model,
            effort=smoke_effort,
            changed_files=changed,
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
        # Safe stop boundary: gate green + work committed, before the push/PR. A `--now`/`--after`
        # request placed during/after smoke is honored here instead of waiting a whole next story.
        write_cp(f"browser-smoke done ({target.key})", target.key, branch)
        if honor_stop("phase", stage=f"browser-smoke done ({target.key})", story=target.key, branch=branch):
            return 0, cum

    # --- PHASE: PR (always) -----------------------------------------------------
    gitutil._git(["push", "-u", "origin", branch], repo)
    title = f"feat({target.key}): automated by loop-bmad"
    body = (
        f"Automated end-to-end by loop-bmad: dev-story + code-review"
        f"{'' if config.no_smoke else ' + AC-aware browser smoke'}, all green.\n\n"
        f"- Story: {target.key}  (epic {epic})\n"
    )
    try:
        pr_url = pr.create_pr(
            branch=branch, base=config.merge_base, title=title, body=body, cwd=str(repo)
        )
    except pr.PrError as e:
        # Resume-tail safety: a prior run may have opened the PR then died before the merge
        # landed (the stuck state this loop is built to recover from). `gh pr create` errors
        # when a PR already exists for the branch — fall back to that open PR instead of
        # stalling, so the retry proceeds to merge.
        pr_url = pr.pr_url(branch=branch, cwd=str(repo))
        if not pr_url:
            emit(bmad_stop_event(False, f"PR create for {target.key} failed: {e}", cum))
            return 1, cum
        print(f"  reusing existing PR for {target.key}: {pr_url}")
    emit(pr_created_event(story=target.key, branch=branch, base=config.merge_base, url=pr_url))
    print(f"  PR (base {config.merge_base}): {pr_url}")

    if config.no_merge:
        emit(bmad_stop_event(
            True,
            f"story {target.key} PR opened (not merged, --no-merge): {pr_url}. CONTINUOUS "
            f"multi-story runs require merging — the next story branches off {config.merge_base}, "
            f"so it can't inherit an un-merged PR. Drop --no-merge (optionally add --merge-wait-sec "
            f"for branch-protected bases) to chain stories.",
            cum,
        ))
        print(f"\n[OK] PR opened for {target.key}: {pr_url}")
        print("     --no-merge -> stopping. Drop --no-merge for continuous multi-story runs.")
        return 0, cum

    # --- PHASE: merge -----------------------------------------------------------
    _checkout(repo, config.merge_base)
    try:
        pr.merge_pr(branch=branch, base=config.merge_base, cwd=str(repo))
    except pr.PrError as e:
        emit(bmad_stop_event(
            False,
            f"auto-merge for {target.key} failed: {e}. Resolve it (conflicts / required review / "
            f"mergeability), then resume: {resume_cmd}",
            cum,
        ))
        print(f"\n[HALT] auto-merge for {target.key} failed: {e}")
        if resume_cmd:
            print(f"       resume: {resume_cmd}")
        return 1, cum
    gitutil._git(["pull", "origin", config.merge_base], repo)
    # Verify the merge actually landed (bmad-loop.ps1 ~857): `gh pr merge` can exit 0 while the
    # PR is only QUEUED behind branch protection. Branching the next story off an un-merged base
    # would stack/lose work, so we confirm state == MERGED before continuing. On a branch-protected
    # base the merge lands a few seconds later — poll up to merge_wait_sec for it before halting so
    # the loop rolls into the next story (config; 0 = strict immediate halt = parity).
    state = pr.pr_state(branch=branch, cwd=str(repo))
    if state != "MERGED" and config.merge_wait_sec > 0:
        deadline = time.monotonic() + config.merge_wait_sec
        while state in ("QUEUED", "OPEN", "BLOCKED", "") and time.monotonic() < deadline:
            time.sleep(5)
            gitutil._git(["pull", "origin", config.merge_base], repo)
            state = pr.pr_state(branch=branch, cwd=str(repo))
    if state != "MERGED":
        waited = (
            f" Waited {config.merge_wait_sec}s for it to land."
            if config.merge_wait_sec > 0
            else " Set --merge-wait-sec=N (e.g. 120) to wait for a QUEUED merge before halting."
        )
        emit(bmad_stop_event(
            False,
            f"auto-merge for {target.key} did not complete (PR state: {state!r}) — likely QUEUED "
            f"behind branch protection / required checks.{waited} Merge it manually: "
            f"gh pr merge {branch} --squash --delete-branch. Then resume: {resume_cmd}",
            cum,
        ))
        print(f"\n[HALT] merge of {target.key} did not complete (PR state {state!r}).{waited}")
        if resume_cmd:
            print(f"       resume: {resume_cmd}")
        return 1, cum
    emit(pr_merged_event(story=target.key, base=config.merge_base, pr=pr_url))
    print(f"  merged {target.key} -> {config.merge_base}.")
    # The story is COMPLETE (merged, repo clean on merge_base) — the literal "stop after THIS
    # story" boundary. A `--after-story` request is honored here, not only on the next iteration's
    # between-stories scan.
    write_cp(f"story-merged ({target.key})", None, config.merge_base)
    if honor_stop("story", stage=f"story-merged ({target.key})", story=None, branch=config.merge_base):
        return 0, cum
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

# Single-pass retro: the facilitator decides for ITSELF (no QUESTION/decider Q&A loop), folding the
# retro_decider's constructive-and-decisive stance into its own prompt — one warm process instead of
# up to ~(2N+1) cold ones. Mirrors :func:`loop.bmad.phases.code_review_single_pass`.
_RETRO_SINGLE_PASS_PROMPT_TEMPLATE = (
    "You are running HEADLESSLY via 'claude -p' — NON-INTERACTIVE and AUTONOMOUS. Do NOT greet. "
    "IMMEDIATELY invoke the bmad-retrospective skill to run the retrospective for EPIC {epic} (all "
    "its stories are done).\n"
    "There is NO human team to answer questions, so do NOT emit 'QUESTION:' lines or wait for input "
    "— DECIDE every point yourself and proceed, all in this one pass. Stand in for the team with "
    "this stance: the epic shipped story-by-story with TDD + adversarial code-review + AC-aware "
    "browser smoke, all green and merged to develop. Be honest about what went well and what to "
    "improve; when a choice or priority comes up, pick decisively and justify briefly; keep action "
    "items concrete and few; do not invent problems.\n"
    "When it is fully complete (retro artifact written AND sprint-status epic-{epic}-retrospective "
    "set to 'done'), output one line beginning 'RETRO_COMPLETE:' with a one-line summary."
)


def _run_retro_single_pass(
    config: BmadConfig,
    runner: AgentRunner,
    epic: str,
    *,
    emit: Callable[[dict[str, Any]], None],
    cwd,
) -> tuple[bool, float]:
    """Single-pass epic retrospective — ONE warm process, no QUESTION/decider Q&A loop.

    Same collapse as the single-pass code review: the facilitator's prompt carries the decider's
    constructive-and-decisive stance so it self-answers in one run, instead of stopping on each
    ``QUESTION:`` for a separate (cold) ``retro_decider`` process. Returns ``(ok, cost)`` exactly
    like :func:`_run_retro`.
    """
    retro_model = config.model_for("retro")
    retro_effort = config.effort_for("retro")
    if hasattr(runner, "set_context"):
        runner.set_context(f"retro-epic-{epic}", None)
    res = runner.run(
        prompt=_RETRO_SINGLE_PASS_PROMPT_TEMPLATE.format(epic=epic),
        model=retro_model,
        allowed_tools=list(phases.BMAD_TOOLS),
        permission_mode="acceptEdits",
        max_turns=0,
        cwd=cwd,
        effort=retro_effort,
    )
    cost = float(getattr(res, "cost_usd", 0.0) or 0.0)
    if getattr(res, "is_error", False) or getattr(res, "parse_failed", False):
        return False, cost
    text = getattr(res, "text", "") or ""
    cm = _RETRO_COMPLETE_RX.search(text)
    summary = cm.group(1).strip() if cm else "no-marker"
    emit(retro_complete_event(epic=epic, summary=summary))
    return True, cost


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
    if getattr(config, "retro_mode", "qa") == "single-pass":
        return _run_retro_single_pass(config, runner, epic, emit=emit, cwd=cwd)
    prompt = _RETRO_PROMPT_TEMPLATE.format(epic=epic)
    session_id: str | None = None
    answer: str | None = None
    total_cost = 0.0
    decider_model = config.model_for("decider")
    retro_model = config.model_for("retro")
    retro_effort = config.effort_for("retro")
    decider_effort = config.effort_for("decider")
    use_sessions = getattr(runner, "supports_sessions", False)
    if hasattr(runner, "set_context"):
        runner.set_context(f"retro-epic-{epic}", None)

    for turn in range(1, config.max_retro_turns + 1):
        if session_id is None or not use_sessions:
            res = runner.run(
                prompt=prompt if session_id is None else (answer or ""),
                model=retro_model,
                allowed_tools=list(phases.BMAD_TOOLS),
                permission_mode="acceptEdits",
                max_turns=0,
                cwd=cwd,
                effort=retro_effort,
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
                effort=retro_effort,
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
            answer = retro_decider(
                runner, question=q, epic_scope=epic, model=decider_model, effort=decider_effort
            )
            emit(retro_answer_event(epic=epic, turn=turn, a=answer))
            continue
        # No marker -> treat as complete (don't spin).
        emit(retro_complete_event(epic=epic, summary="no-marker"))
        return True, total_cost

    return False, total_cost
