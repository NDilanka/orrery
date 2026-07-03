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

import fnmatch
import json
import re
import time
from dataclasses import dataclass, field
from functools import partial
from pathlib import Path
from typing import Any, Callable

from loop import gitutil
from loop.configkeys import resolve, warn_unknown_keys
from loop.decide import floor_breach
from loop.driver_shell import read_stop_request, run_driver, write_checkpoint_now
from loop.bmad import phases, pr, recovery
from loop.bmad.decider import retro_decider, review_decider
from loop.bmad.sprint import (
    SprintStatus,
    Story,
    epic_done,
    parse_sprint_status,
    select_actionable,
)
from loop.bmad.story import story_acs, story_meta
from loop.events import (
    bmad_metrics_event,
    bmad_stop_event,
    cooperative_stop_event,
    engine_start_event,
    gate_retry_event,
    phase_timeout_event,
    plan_check_event,
    pr_created_event,
    pr_merged_event,
    retro_answer_event,
    retro_complete_event,
    retro_question_event,
    retro_start_event,
    start_event,
    story_start_event,
    test_integrity_event,
    verify_event,
)
from loop.logio import (
    append_event,
    clear_stop_flag,
)
from loop.resilient import ResilientRunner, _usage_tokens  # re-exported for back-compat  # noqa: F401
from loop.runners.base import AgentRunner
from loop.verdict import question_marker

# --- where bmad-loop.ps1 finds the sprint file + the story files (~58-59) -----
_ARTIFACTS_REL = ("_bmad-output", "implementation-artifacts")
_SPRINT_FILE = "sprint-status.yaml"

# The three authoritative gate stages from bmad-loop.ps1 (~284-286): codegen, lint, test
# (vitest). OVERRIDABLE via config; the test/extension hook may swap the commands for callables.
DEFAULT_GATE_STAGES: list[dict[str, Any]] = [
    {"name": "codegen", "command": "bun run codegen"},
    {"name": "lint", "command": "bun run lint"},
    {
        "name": "test",
        # brain2's `bun run test` is vitest. Anchor on the "Tests" label so we read the test
        # count, NOT the "Test Files" count. The moment ANY test fails, vitest REORDERS the
        # summary to "Tests  <f> failed | <p> passed (<n>)" — the passed count is then no
        # longer adjacent to "Tests". `(?:\d+\s+\w+\s+\|\s+)*` skips any leading "<n> failed |"
        # / "<n> skipped |" segments before capturing the passed count; without it a single
        # failing test made the parser read 0 passed and trip a FALSE "N->0 regression" halt
        # (bmad-loop.ps1 ~287-289's plain `Tests\s+(\d+)\s+passed` had the same latent bug).
        "command": "bun run test",
        "pass_pattern": r"Tests\s+(?:\d+\s+\w+\s+\|\s+)*(\d+)\s+passed",
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

# Keys `BmadConfig.from_loop_json` actually reads (both spellings) — anything else warns
# (loop.configkeys.warn_unknown_keys) instead of silently vanishing.
_BMAD_KNOWN_KEYS = {
    "project_root", "projectRoot",
    "merge_base", "mergeBase",
    "max_stories", "maxStories",
    "max_review_turns", "maxReviewTurns",
    "max_smoke_iters", "maxSmokeIters",
    "smoke_timeout_min", "smokeTimeoutMin",
    "create_timeout_min", "createTimeoutMin",
    "dev_timeout_min", "devTimeoutMin",
    "review_timeout_min", "reviewTimeoutMin",
    "retro_timeout_min", "retroTimeoutMin",
    "decider_timeout_min", "deciderTimeoutMin",
    "max_retro_turns", "maxRetroTurns",
    "default_quota_wait_min", "defaultQuotaWaitMin",
    "max_quota_waits", "maxQuotaWaits",
    "merge_wait_sec", "mergeWaitSec",
    "epic_only", "epicOnly",
    "story",
    "no_merge", "noMerge",
    "no_retro", "noRetro",
    "no_smoke", "noSmoke",
    "dry_run", "dryRun",
    "auto_rollback", "autoRollback",
    "review_mode", "reviewMode",
    "smoke_mode", "smokeMode",
    "retro_mode", "retroMode",
    "gate_flaky_retries", "gateFlakyRetries",
    "gate_flaky_max_fail", "gateFlakyMaxFail",
    "gate_stages", "gateStages",
    "dev_server_argv", "devServerArgv",
    "models", "effort",
    # Wave-2 quality feature blocks / flags (both spellings for the flat keys).
    "verify",
    "test_integrity", "testIntegrity",
    "plan_gate", "planGate",
    "metrics_emit", "metricsEmit",
    "gate_fail_fast", "gateFailFast",
    # Wave-4 flat knobs (both spellings).
    "fallback_model", "fallbackModel",
    "structured_verdicts", "structuredVerdicts",
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
    # Per-phase wall-clock caps in MINUTES (0 = disabled/unbounded), threaded into each phase's
    # `runner.run(..., timeout_sec=...)` call so a hung agent process can't block an unattended
    # run forever (Wave A1 "don't hang"). `smoke_timeout_min` above already existed for
    # browser-smoke; these cover the other agent-spawning phases.
    create_timeout_min: int = 30
    dev_timeout_min: int = 120
    review_timeout_min: int = 60
    retro_timeout_min: int = 30
    # The cheap-model review/retro DECIDER's own per-call cap (minutes). FINITE by default (10) —
    # hang-protection is the point: the default `qa` review/retro path calls the decider with a
    # bounded wall clock so one wedged decider call can't hang an unattended overnight run forever.
    # 0 = disabled/unbounded (opt-out). A hung decider yields no text, so the phase falls back to
    # the decider's safe default answer and proceeds.
    decider_timeout_min: int = 10
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
    # timing-sensitive test (it passes on a clean re-run) — NOT a real regression. `_run_gate`
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
    # --- Wave-2 quality features (all default-ON except gate_fail_fast) ------------------------
    # FEATURE 1 — adversarial verify-before-merge: an INDEPENDENT cheap-model checker tries to
    # REFUTE that the story diff satisfies EVERY frozen acceptance criterion, right before the PR.
    # Breaks the generator<->reviewer correlated false-green (both were the same warm process).
    verify_enabled: bool = True
    verify_model: str = "haiku"
    verify_effort: str = "low"
    verify_timeout_min: int = 10
    # FEATURE 2 — test-integrity check via git: catch in-place edits / deletions of PRE-EXISTING
    # test files (the pass-count floor can't see a deleted test). Uses `git diff` vs the story
    # baseline (survives crash/resume; no state file). A DELETION halts; a MODIFICATION is fed to
    # the verifier (BMAD legitimately touches tests, so it is not itself a halt).
    test_integrity_enabled: bool = True
    test_integrity_globs: list[str] = field(
        default_factory=lambda: ["**/*.test.*", "**/*.spec.*"]
    )
    test_integrity_halt_on_deletion: bool = True
    # FEATURE 3 — plan-gate before dev-story: a bounded one-shot check that the story's ACs + Tasks
    # are unambiguous, testable, and implementable as ONE story — insurance against grinding hours
    # on an ambiguous spec. Reuses the decider model/effort tiers + decider_timeout_min.
    plan_gate_enabled: bool = True
    # FEATURE 4 — BMAD run-quality metrics: emit ONE additive `metrics` event at stop (zero model
    # tokens — folded purely from the event stream + wall clock).
    metrics_emit: bool = True
    # FEATURE 5 — gate fail-fast (OPT-IN, unlike the above): once a gate stage exits non-zero, skip
    # the later (build-heavy) stages instead of always running the whole pipeline.
    gate_fail_fast: bool = False
    # --- Wave-4 (all default-OFF / experimental, flagless — ride the --loop-json re-point) ------
    # Task A — overload resilience: a comma-separated model chain the claude CLI tries when the
    # primary is overloaded (``--fallback-model``). "" = omitted (parity). Threaded into EVERY phase
    # by the ResilientRunner (which wraps every BMAD agent call), so it is empty-safe everywhere.
    fallback_model: str = ""
    # Task B — structured verdicts: when ON, the adversarial verify + plan-gate calls request a
    # validated ``structured_output`` via ``--json-schema`` and PREFER it over free-text VERDICT
    # parsing, falling back to the existing text parse (and its fail-open polarity) when the
    # structured field is absent/invalid. OFF (default) -> zero argv change.
    structured_verdicts: bool = False
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
            # `is not None` (NOT `or`) — 0 is a legitimate explicit "disable this timeout"
            # value on the CLI and must not be coerced back to the default.
            create_timeout_min=(
                args.create_timeout_min
                if getattr(args, "create_timeout_min", None) is not None
                else 30
            ),
            dev_timeout_min=(
                args.dev_timeout_min if getattr(args, "dev_timeout_min", None) is not None else 120
            ),
            review_timeout_min=(
                args.review_timeout_min
                if getattr(args, "review_timeout_min", None) is not None
                else 60
            ),
            retro_timeout_min=(
                args.retro_timeout_min
                if getattr(args, "retro_timeout_min", None) is not None
                else 30
            ),
            decider_timeout_min=(
                args.decider_timeout_min
                if getattr(args, "decider_timeout_min", None) is not None
                else 10
            ),
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
            # Wave-2: the only new knobs with a CLI flag are the two default-ON disablers.
            verify_enabled=not bool(getattr(args, "no_verify", False)),
            plan_gate_enabled=not bool(getattr(args, "no_plan_gate", False)),
            # Wave-4: flagless knobs (like models/effort) — read from args when present, else "".
            # They otherwise live in --loop-json and ride its re-point in _resume_command.
            fallback_model=getattr(args, "fallback_model", None) or "",
            structured_verdicts=bool(getattr(args, "structured_verdicts", False)),
            loop_json=getattr(args, "loop_json", None) or "",
        )

    @classmethod
    def from_loop_json(cls, path_or_dict: Any) -> BmadConfig:
        """Build from a ``loop.json`` ``bmad`` block (or a raw dict of the same fields).

        Every field accepts EITHER camelCase or snake_case (:func:`loop.configkeys.resolve`);
        an unrecognized key in the block warns on stderr instead of silently vanishing
        (:func:`loop.configkeys.warn_unknown_keys`). ``gate_stages``/``gateStages`` (previously
        snake_case ONLY, despite camelCase being the documented wire convention — a footgun) and
        ``dev_server_argv``/``devServerArgv`` (previously not read at all, despite being
        documented as configurable) are both wired here.
        """
        if isinstance(path_or_dict, dict):
            data = path_or_dict
        else:
            data = json.loads(Path(path_or_dict).read_text(encoding="utf-8"))
        b = data.get("bmad", data) if isinstance(data, dict) else {}
        if b is None:
            b = {}
        warn_unknown_keys(b, _BMAD_KNOWN_KEYS, "bmad")
        kwargs: dict[str, Any] = {}
        pr = resolve(b, "project_root", "projectRoot")
        if pr is not None:
            kwargs["project_root"] = pr
        for snake, camel in (
            ("merge_base", "mergeBase"),
            ("max_stories", "maxStories"),
            ("max_review_turns", "maxReviewTurns"),
            ("max_smoke_iters", "maxSmokeIters"),
            ("smoke_timeout_min", "smokeTimeoutMin"),
            ("create_timeout_min", "createTimeoutMin"),
            ("dev_timeout_min", "devTimeoutMin"),
            ("review_timeout_min", "reviewTimeoutMin"),
            ("retro_timeout_min", "retroTimeoutMin"),
            ("decider_timeout_min", "deciderTimeoutMin"),
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
            v = resolve(b, snake, camel)
            if v is not None:
                kwargs[snake] = v
        gate_stages = resolve(b, "gate_stages", "gateStages")
        if gate_stages is not None:
            kwargs["gate_stages"] = list(gate_stages)
        dev_server_argv = resolve(b, "dev_server_argv", "devServerArgv")
        if dev_server_argv is not None:
            kwargs["dev_server_argv"] = tuple(dev_server_argv)
        if "models" in b:
            kwargs["models"] = {**DEFAULT_MODELS, **dict(b["models"])}
        if "effort" in b:
            kwargs["effort"] = {**DEFAULT_EFFORTS, **dict(b["effort"])}
        # --- Wave-2 quality feature blocks (nested) + flat flags (both spellings) --------------
        # FEATURE 1: `bmad.verify` = { enabled, model, effort, timeoutMin }
        verify = resolve(b, "verify")
        if isinstance(verify, dict):
            ve = resolve(verify, "enabled")
            if ve is not None:
                kwargs["verify_enabled"] = bool(ve)
            vm = resolve(verify, "model")
            if vm is not None:
                kwargs["verify_model"] = vm
            vf = resolve(verify, "effort")
            if vf is not None:
                kwargs["verify_effort"] = vf
            vt = resolve(verify, "timeout_min", "timeoutMin")
            if vt is not None:
                kwargs["verify_timeout_min"] = int(vt)
        # FEATURE 2: `bmad.testIntegrity` = { enabled, globs, haltOnDeletion }
        ti = resolve(b, "test_integrity", "testIntegrity")
        if isinstance(ti, dict):
            te = resolve(ti, "enabled")
            if te is not None:
                kwargs["test_integrity_enabled"] = bool(te)
            tg = resolve(ti, "globs")
            if tg is not None:
                kwargs["test_integrity_globs"] = list(tg)
            th = resolve(ti, "halt_on_deletion", "haltOnDeletion")
            if th is not None:
                kwargs["test_integrity_halt_on_deletion"] = bool(th)
        # FEATURE 3: `bmad.planGate` = { enabled }
        pg = resolve(b, "plan_gate", "planGate")
        if isinstance(pg, dict):
            pe = resolve(pg, "enabled")
            if pe is not None:
                kwargs["plan_gate_enabled"] = bool(pe)
        # FEATURE 4/5: flat booleans.
        me = resolve(b, "metrics_emit", "metricsEmit")
        if me is not None:
            kwargs["metrics_emit"] = bool(me)
        ff = resolve(b, "gate_fail_fast", "gateFailFast")
        if ff is not None:
            kwargs["gate_fail_fast"] = bool(ff)
        # Wave-4 flat knobs.
        fbm = resolve(b, "fallback_model", "fallbackModel")
        if fbm is not None:
            kwargs["fallback_model"] = str(fbm)
        sv = resolve(b, "structured_verdicts", "structuredVerdicts")
        if sv is not None:
            kwargs["structured_verdicts"] = bool(sv)
        return cls(**kwargs)


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
    # Wave-2: the two default-ON quality gates each have a disabler flag; round-trip it only when
    # the user turned the gate OFF (the flagless knobs — verify model/effort/timeout, testIntegrity,
    # metricsEmit, gateFailFast, and the Wave-4 fallbackModel/structuredVerdicts — ride the
    # --loop-json re-point above, like models/effort).
    if not config.verify_enabled:
        parts.append("--no-verify")
    if not config.plan_gate_enabled:
        parts.append("--no-plan-gate")
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
        ("--create-timeout-min", config.create_timeout_min, 30),
        ("--dev-timeout-min", config.dev_timeout_min, 120),
        ("--review-timeout-min", config.review_timeout_min, 60),
        ("--retro-timeout-min", config.retro_timeout_min, 30),
        ("--decider-timeout-min", config.decider_timeout_min, 10),
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

    # --- dry-run: sprint scan + preflight plan, NO runner, NO lock (PS ~653-664) ---
    if config.dry_run:
        return _dry_run(config, project_root, repo)

    def body(state: Path) -> int:
        log_path = state / "log.jsonl"
        checkpoint_path = state / "checkpoint.json"
        stop_path = state / "STOP"

        def emit(event: dict[str, Any]) -> None:
            append_event(log_path, event)

        # Heartbeat (first thing under the lock): emit BEFORE the slow preflight (git checkout of
        # merge-base + baseline gate) so a watching UI flips to "running" within ~1s of spawn
        # instead of seeing an empty log for the whole cold start.
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

    # ONE lockfile name ("lock") shared with loop.core / loop.qa.discover (loop.lockfile) — a
    # generic loop, a BMAD run, and a QA run racing the SAME state dir now correctly see each
    # other. A leftover "bmad-lock" from before this unification is simply ignored (not read).
    return run_driver(state, guard_label="bmad driver", body=body)


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
    with codegen+lint green) is RE-RUN up to ``config.gate_flaky_retries`` times — the first green
    result wins. This stops a single flaky/timing-sensitive test from turning an otherwise-green
    run into a hard terminal stop (the recurring failure mode), while a real regression
    (deterministic, or many failures) still fails immediately. Re-running is idempotent (codegen
    regenerates the same files; lint/test are read-only); the only cost is time on a RED gate.
    Each retry emits a ``gate-retry`` event (when ``emit`` is given) so the flakiness is visible.
    """
    from loop.gate import run_gate

    g = run_gate(config.gate_stages, str(repo), fail_fast=config.gate_fail_fast)
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
        g = run_gate(config.gate_stages, str(repo), fail_fast=config.gate_fail_fast)
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
    # FEATURE 4 — run-quality metrics. Wrap `emit` so we (a) keep an in-memory copy of every event
    # for the summary and (b) inject ONE additive `metrics` event immediately BEFORE the terminal
    # bmad `stop` event, on EVERY exit path (ok or halt), computed purely from the stream + the
    # wall clock (zero model tokens). The BMAD driver injects no clock, so wall-clock duration uses
    # time.monotonic() (consistent with the merge-wait polling above), never time.time().
    _run_start = time.monotonic()
    _collected: list[dict[str, Any]] = []
    _orig_emit = emit
    _metrics_emitted = {"v": False}

    def emit(event: dict[str, Any]) -> None:  # noqa: F811 — intentionally shadows the param
        if (
            config.metrics_emit
            and not _metrics_emitted["v"]
            and isinstance(event, dict)
            and event.get("event") == "stop"
            and "ok" in event
        ):
            _metrics_emitted["v"] = True
            m = _compute_bmad_metrics(
                _collected, stop_event=event, duration_sec=time.monotonic() - _run_start
            )
            _orig_emit(m)
            _collected.append(m)
        _orig_emit(event)
        _collected.append(event)

    resilient = ResilientRunner(
        base_runner,
        emit=emit,
        quota_cfg={
            "default_wait_min": config.default_quota_wait_min,
            "max_waits": config.max_quota_waits,
            "cum": cum,
        },
        activity_path=state / "activity.json",
        fallback_model=config.fallback_model,
    )

    def gate_fn() -> dict[str, Any]:
        return _run_gate(config, repo, emit)

    def smoke_server():
        return phases.DevServer(config.dev_server_argv, cwd=str(repo))

    def write_cp(stage: str, story: str | None, branch: str) -> None:
        write_checkpoint_now(
            checkpoint_path,
            stage=stage,
            story=story,
            branch=branch,
            merge_base=config.merge_base,
            cum_usd=cum,
            resume=_resume_command(config, state),
        )

    def honor_stop(scope: str, *, stage: str, story: str | None, branch: str) -> bool:
        """Cooperative stop at a safe boundary. Returns True when a stop was honored."""
        req = read_stop_request(stop_path, scope)
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


# ---------------------------------------------------------------------------
# Wave-2 quality gates: plan-gate (before dev), test-integrity (before smoke),
# adversarial verify (before PR), + the run-quality metrics summary.
# ---------------------------------------------------------------------------

# '## Tasks' / '## Tasks / Subtasks' section, up to the next '## ' heading or EOF.
_TASKS_RX = re.compile(
    r"^##\s*Tasks?(?:\s*/\s*Subtasks?)?\s*\r?\n(.+?)(?=^\#\#\s|\Z)",
    re.IGNORECASE | re.MULTILINE | re.DOTALL,
)


def _story_tasks(text: str | None, *, max_chars: int = 2500) -> str:
    """The story's ``## Tasks`` section as raw text, truncated (mirrors ``story_acs``)."""
    if not text:
        return ""
    m = _TASKS_RX.search(text)
    if not m:
        return ""
    t = m.group(1).strip()
    return (t[:max_chars] + " …(truncated)") if len(t) > max_chars else t


# FEATURE 3 — plan-gate: a bounded, single-turn readiness check on the story's ACs + Tasks.
_PLAN_GATE_PROMPT_TEMPLATE = (
    "You are an INDEPENDENT planning reviewer. You have NOT seen any implementation — judge ONLY "
    "the story's Acceptance Criteria and Tasks below.\n"
    "Question: are they unambiguous, testable, and implementable as ONE story (not secretly "
    "several stories, not missing information a developer would have to invent)?\n\n"
    "=== STORY {story} ===\n"
    "--- Acceptance Criteria ---\n{acs}\n\n"
    "--- Tasks ---\n{tasks}\n\n"
    "Reply with EXACTLY one line and nothing else:\n"
    "  PLAN_OK\n"
    "or\n"
    "  BLOCKED: <one-line reason>\n"
    "Default to PLAN_OK unless there is a concrete, blocking ambiguity/untestability/oversize "
    "problem."
)


# --- Wave-4 Task B: structured-verdict schemas + resolver (opt-in via structured_verdicts) ------
# Inline JSON Schemas handed to claude's ``--json-schema``; the validated result rides the result
# JSON's ``structured_output`` field (-> AgentResult.structured). Each mirrors the free-text verdict
# these gates already parse, so resolution PREFERS the structured field and falls back to the text
# parse (and its fail-open polarity) whenever the field is absent/invalid — never a behavior break.
_PLAN_GATE_SCHEMA = json.dumps({
    "type": "object",
    "properties": {
        "verdict": {"enum": ["PLAN_OK", "BLOCKED"]},
        "reason": {"type": "string"},
    },
    "required": ["verdict"],
})
_VERIFY_SCHEMA = json.dumps({
    "type": "object",
    "properties": {
        "verdict": {"enum": ["PASS", "REFUTE"]},
        "reason": {"type": "string"},
    },
    "required": ["verdict"],
})


def _structured_verdict(res: Any, allowed: set[str]) -> tuple[str, str] | None:
    """``(verdict, reason)`` from a run's validated ``structured_output``, or None to fall back.

    Returns None (so the caller uses its existing text parse) unless ``res.structured`` is a dict
    carrying a ``verdict`` string in ``allowed`` — i.e. only a PRESENT and VALID structured verdict
    short-circuits the text path. ``reason`` defaults to "" when absent.
    """
    so = getattr(res, "structured", None)
    if not isinstance(so, dict):
        return None
    v = so.get("verdict")
    if not isinstance(v, str):
        return None
    vu = v.strip().upper()
    if vu not in allowed:
        return None
    reason = so.get("reason")
    return vu, (str(reason).strip() if reason is not None else "")


def _run_plan_gate(
    config: BmadConfig,
    resilient: ResilientRunner,
    target: Story,
    story_text: str,
    *,
    emit: Callable[[dict[str, Any]], None],
    cwd,
    cum: float,
    resume_cmd: str,
) -> tuple[int | None, float]:
    """FEATURE 3 — plan-gate before dev-story. Returns ``(exit_or_None, cost)``.

    An explicit ``BLOCKED:`` halts (return 1); ``PLAN_OK``, an unparseable reply, or an
    errored/timed-out call all FAIL-OPEN and proceed (return ``None``). Reuses the decider
    model/effort tiers + ``decider_timeout_min``, single-turn.
    """
    acs = story_acs(story_text) or "(no Acceptance Criteria section found)"
    tasks = _story_tasks(story_text) or "(no Tasks section found)"
    timeout = config.decider_timeout_min * 60 if config.decider_timeout_min > 0 else 0
    resilient.set_context("plan-gate", target.key)
    # Task B: request a validated structured verdict when enabled (OFF -> no --json-schema flag).
    pg_extra: dict[str, Any] = {}
    if config.structured_verdicts:
        pg_extra["json_schema"] = _PLAN_GATE_SCHEMA
    res = resilient.run(
        prompt=_PLAN_GATE_PROMPT_TEMPLATE.format(story=target.key, acs=acs, tasks=tasks),
        model=config.model_for("decider"),
        effort=config.effort_for("decider"),
        allowed_tools=[],
        permission_mode="plan",
        max_turns=1,
        cwd=cwd,
        timeout_sec=timeout,
        **pg_extra,
    )
    cost = float(getattr(res, "cost_usd", 0.0) or 0.0)
    if (
        getattr(res, "timed_out", False)
        or getattr(res, "is_error", False)
        or getattr(res, "parse_failed", False)
    ):
        emit(plan_check_event(
            story=target.key, ok=True, verdict="inconclusive",
            reason="plan-gate call errored/timed out; proceeding", cum=cum + cost,
        ))
        return None, cost
    # Prefer a valid structured verdict; else fall back to the free-text BLOCKED:/PLAN_OK parse.
    sv = _structured_verdict(res, {"PLAN_OK", "BLOCKED"}) if config.structured_verdicts else None
    text = getattr(res, "text", "") or ""
    if sv is not None:
        sv_verdict, sv_reason = sv
        if sv_verdict == "BLOCKED":
            reason = sv_reason or "story judged not ready to implement as one story"
            emit(plan_check_event(
                story=target.key, ok=False, verdict="blocked", reason=reason, cum=cum + cost,
            ))
            emit(bmad_stop_event(
                False,
                f"plan-gate BLOCKED {target.key}: {reason}. Its ACs/Tasks are ambiguous, "
                f"untestable, or too large for one story — split/clarify it, then resume: "
                f"{resume_cmd}",
                cum + cost,
            ))
            print(f"\n[HALT] plan-gate BLOCKED {target.key}: {reason}")
            if resume_cmd:
                print(f"       resume: {resume_cmd}")
            return 1, cost
        emit(plan_check_event(
            story=target.key, ok=True, verdict="ok", reason=None, cum=cum + cost
        ))
        return None, cost
    bm = re.search(r"BLOCKED:\s*(.*)", text, re.IGNORECASE)
    if bm:
        reason = bm.group(1).strip() or "story judged not ready to implement as one story"
        emit(plan_check_event(
            story=target.key, ok=False, verdict="blocked", reason=reason, cum=cum + cost,
        ))
        emit(bmad_stop_event(
            False,
            f"plan-gate BLOCKED {target.key}: {reason}. Its ACs/Tasks are ambiguous, untestable, "
            f"or too large for one story — split/clarify it, then resume: {resume_cmd}",
            cum + cost,
        ))
        print(f"\n[HALT] plan-gate BLOCKED {target.key}: {reason}")
        if resume_cmd:
            print(f"       resume: {resume_cmd}")
        return 1, cost
    if "PLAN_OK" in text:
        emit(plan_check_event(story=target.key, ok=True, verdict="ok", reason=None, cum=cum + cost))
    else:
        emit(plan_check_event(
            story=target.key, ok=True, verdict="inconclusive",
            reason="no PLAN_OK/BLOCKED verdict parsed; proceeding", cum=cum + cost,
        ))
    return None, cost


# FEATURE 2 — test-integrity: git-based tamper check on PRE-EXISTING test files.
def _test_globs_match(path: Any, globs: list[str]) -> bool:
    """True when ``path`` (repo-relative) is a test file per ``globs``, excluding node_modules.

    Each glob's basename tail (e.g. ``**/*.test.*`` -> ``*.test.*``) is matched against the file's
    basename so a test file at ANY depth matches; the full glob is also tried against the whole
    path for anchored patterns.
    """
    p = str(path).replace("\\", "/")
    if "node_modules/" in p or p.startswith("node_modules"):
        return False
    base = p.rsplit("/", 1)[-1]
    for g in globs:
        tail = str(g).rsplit("/", 1)[-1]
        if fnmatch.fnmatch(base, tail) or fnmatch.fnmatch(p, str(g)):
            return True
    return False


def _run_test_integrity(
    config: BmadConfig,
    target: Story,
    *,
    repo: Path,
    project_root: Path,
    emit: Callable[[dict[str, Any]], None],
    cum: float,
    resume_cmd: str,
) -> tuple[int | None, list[str]]:
    """FEATURE 2 — test-integrity check via git. Returns ``(exit_or_None, modified_test_files)``.

    Diffs the story baseline_commit -> HEAD, filtered to the test globs. A DELETED pre-existing
    test file is a tamper (halt, unless ``halt_on_deletion`` is off -> warn+proceed); a MODIFIED
    one is NOT a halt (BMAD legitimately edits tests) but is returned so the verifier scrutinizes
    it. No baseline -> skip silently (emit nothing, return ``(None, [])``).
    """
    base_commit = story_meta(_story_text(project_root, target.key)).get("baseline")
    if not base_commit:
        return None, []
    deleted: list[str] = []
    modified: list[str] = []
    for code, path in gitutil.diff_name_status(repo, base_commit):
        if not _test_globs_match(path, config.test_integrity_globs):
            continue
        if code == "D":
            deleted.append(path)
        elif code in ("M", "R", "C"):  # rename/copy = OK, treat as modified; A (added) ignored
            modified.append(path)
    if not deleted and not modified:
        return None, []
    ok = not deleted
    emit(test_integrity_event(
        story=target.key, deleted=deleted, modified=modified, ok=ok, cum=cum,
    ))
    if deleted and config.test_integrity_halt_on_deletion:
        files = ", ".join(deleted)
        emit(bmad_stop_event(
            False,
            f"test-integrity: {target.key} DELETED pre-existing test file(s): {files}. A story "
            f"must not remove existing tests to reach green. Restore them (or justify + re-run), "
            f"then resume: {resume_cmd}",
            cum,
        ))
        print(f"\n[HALT] test-integrity: {target.key} deleted pre-existing test(s): {files}")
        if resume_cmd:
            print(f"       resume: {resume_cmd}")
        return 1, modified
    if deleted:
        print(
            f"  [warn] {target.key} deleted pre-existing test(s): {', '.join(deleted)} "
            f"(test_integrity_halt_on_deletion off — proceeding)"
        )
    return None, modified


# FEATURE 1 — adversarial verify-before-merge.
_VERIFY_MAX_DIFF = 60000
_VERIFY_PROMPT_TEMPLATE = (
    "You are an INDEPENDENT adversarial verifier. You have NOT seen the implementation "
    "conversation, the developer's reasoning, or the code reviewer's notes — you see ONLY the "
    "story's FROZEN Acceptance Criteria and the diff below.\n"
    "Your job is to REFUTE, not to rubber-stamp: actively try to prove the diff does NOT satisfy "
    "EVERY acceptance criterion. Over-weight NEGATIVE / 'DO NOT' criteria — a diff that does a "
    "forbidden thing FAILS even if everything else passes. Assume nothing the diff does not show; "
    "you MAY read files in the repo to confirm a claim.\n\n"
    "=== STORY {story} — FROZEN ACCEPTANCE CRITERIA ===\n{acs}\n\n"
    "{tests_note}"
    "=== DIFF (git diff --stat + patch, baseline_commit -> HEAD) ===\n{diff}\n\n"
    "Check EACH acceptance criterion against the diff. Then reply with EXACTLY one FINAL line, "
    "and nothing after it:\n"
    "  VERDICT: PASS\n"
    "or\n"
    "  VERDICT: REFUTE — <one-line concrete reason citing the AC number>\n"
    "Only answer PASS if you could NOT refute any criterion."
)


def _story_diff(repo: Path, base: str) -> str:
    """``git diff --stat`` + patch, baseline -> HEAD, truncated to ~60k chars with a marker."""
    stat = gitutil._git(["diff", "--stat", base, "HEAD"], repo)
    patch = gitutil._git(["diff", base, "HEAD"], repo)
    stat_txt = (stat.stdout or "") if stat.returncode == 0 else ""
    patch_txt = (patch.stdout or "") if patch.returncode == 0 else ""
    combined = (stat_txt + "\n" + patch_txt).strip()
    if len(combined) > _VERIFY_MAX_DIFF:
        combined = combined[:_VERIFY_MAX_DIFF] + "\n[diff truncated]"
    return combined


def _run_verify(
    config: BmadConfig,
    resilient: ResilientRunner,
    target: Story,
    story_text: str,
    *,
    repo: Path,
    project_root: Path,
    modified_tests: list[str],
    emit: Callable[[dict[str, Any]], None],
    cwd,
    cum: float,
    branch: str,
    resume_cmd: str,
) -> tuple[int | None, float]:
    """FEATURE 1 — adversarial verify-before-merge. Returns ``(exit_or_None, cost)``.

    An independent refute-biased checker (its own cheap model, single-turn) tries to break the
    claim that the diff satisfies EVERY frozen acceptance criterion. ``VERDICT: REFUTE`` blocks
    the PR (halt, return 1). ``VERDICT: PASS`` proceeds. No baseline diff -> ``skipped``; no
    parseable verdict / errored / timed-out call -> ``inconclusive``; both FAIL-OPEN (proceed),
    because the external gate + browser smoke have already passed and a wedged verifier must not
    strand a good story.
    """
    base_commit = story_meta(_story_text(project_root, target.key)).get("baseline")
    if not base_commit:
        emit(verify_event(
            story=target.key, verdict="skipped",
            reason="no baseline_commit recorded; cannot diff to verify", cum=cum,
        ))
        return None, 0.0
    acs = story_acs(story_text) or "(no Acceptance Criteria section found)"
    diff = _story_diff(repo, base_commit) or "(empty diff)"
    tests_note = ""
    if modified_tests:
        tests_note = (
            "NOTE: this story MODIFIED these PRE-EXISTING test files — scrutinize them for "
            "WEAKENED, skipped, or deleted assertions that could mask a real failure:\n- "
            + "\n- ".join(modified_tests)
            + "\n\n"
        )
    prompt = _VERIFY_PROMPT_TEMPLATE.format(
        story=target.key, acs=acs, tests_note=tests_note, diff=diff,
    )
    timeout = config.verify_timeout_min * 60 if config.verify_timeout_min > 0 else 0
    resilient.set_context("adversarial-verify", target.key)
    # Task B: request a validated structured verdict when enabled (OFF -> no --json-schema flag).
    vf_extra: dict[str, Any] = {}
    if config.structured_verdicts:
        vf_extra["json_schema"] = _VERIFY_SCHEMA
    res = resilient.run(
        prompt=prompt,
        model=config.verify_model,
        effort=config.verify_effort,
        allowed_tools=["Read", "Grep", "Glob"],
        permission_mode="plan",
        max_turns=1,
        cwd=cwd,
        timeout_sec=timeout,
        **vf_extra,
    )
    cost = float(getattr(res, "cost_usd", 0.0) or 0.0)
    if (
        getattr(res, "timed_out", False)
        or getattr(res, "is_error", False)
        or getattr(res, "parse_failed", False)
    ):
        emit(verify_event(
            story=target.key, verdict="inconclusive",
            reason="verifier call errored/timed out; fail-open (gate + smoke already passed)",
            cum=cum + cost,
        ))
        return None, cost
    # Prefer a valid structured verdict; else fall back to the free-text VERDICT: line parse. Both
    # paths keep the wave-2 fail-open polarity (no parseable verdict -> proceed).
    sv = _structured_verdict(res, {"PASS", "REFUTE"}) if config.structured_verdicts else None
    text = getattr(res, "text", "") or ""
    if sv is not None:
        verdict, sv_reason = sv
        reason = sv_reason
    else:
        matches = list(re.finditer(r"VERDICT:\s*(PASS|REFUTE)[^\n]*", text, re.IGNORECASE))
        if not matches:
            emit(verify_event(
                story=target.key, verdict="inconclusive",
                reason="no parseable VERDICT line; fail-open", cum=cum + cost,
            ))
            return None, cost
        last = matches[-1]
        verdict = last.group(1).upper()
        reason = re.sub(
            r"^VERDICT:\s*REFUTE\s*[—:\-]*\s*", "", last.group(0), flags=re.IGNORECASE
        ).strip()
    if verdict == "REFUTE":
        reason = reason or "diff does not satisfy the frozen acceptance criteria"
        emit(verify_event(story=target.key, verdict="refute", reason=reason, cum=cum + cost))
        emit(bmad_stop_event(
            False,
            f"adversarial verify REFUTED {target.key} on {branch}: {reason}. The diff does not "
            f"satisfy its frozen acceptance criteria — fix it (or, if the verifier is wrong, "
            f"re-run), then resume: {resume_cmd}",
            cum + cost,
        ))
        print(f"\n[HALT] verify REFUTED {target.key}: {reason}")
        print(f"       branch: {branch}")
        if resume_cmd:
            print(f"       resume: {resume_cmd}")
        return 1, cost
    emit(verify_event(story=target.key, verdict="pass", reason=None, cum=cum + cost))
    return None, cost


def _compute_bmad_metrics(
    events: list[dict[str, Any]],
    *,
    stop_event: dict[str, Any],
    duration_sec: float,
) -> dict[str, Any]:
    """Fold the emitted event stream into the additive BMAD ``metrics`` event (zero tokens)."""
    counts: dict[Any, int] = {}
    inp = out = cr = cc = 0
    gate_reds = 0
    for ev in events:
        if not isinstance(ev, dict):
            continue
        k = ev.get("event")
        counts[k] = counts.get(k, 0) + 1
        if k == "token-usage":
            inp += int(ev.get("input", 0) or 0)
            out += int(ev.get("output", 0) or 0)
            cr += int(ev.get("cacheRead", 0) or 0)
            cc += int(ev.get("cacheCreation", 0) or 0)
        elif k == "dev-gate" and not ev.get("green"):
            gate_reds += 1
    denom = cr + inp
    hit = (cr / float(denom)) if denom else 0.0
    stop_ok = bool(stop_event.get("ok"))
    return bmad_metrics_event(
        stories_completed=counts.get("pr-merged", 0),
        stories_halted=0 if stop_ok else 1,
        dev_gates=counts.get("dev-gate", 0),
        reviews=counts.get("review-complete", 0),
        smoke_iters=counts.get("smoke-iter", 0),
        prs_created=counts.get("pr-created", 0),
        prs_merged=counts.get("pr-merged", 0),
        retros=counts.get("retro-complete", 0),
        plan_checks=counts.get("plan-check", 0),
        verifies=counts.get("verify", 0),
        gate_reds=gate_reds,
        flaky_retries=counts.get("gate-retry", 0),
        quota_waits=counts.get("quota-wait", 0),
        input_tokens=inp,
        output_tokens=out,
        cache_read_tokens=cr,
        cache_creation_tokens=cc,
        hit_ratio=hit,
        cum_usd=float(stop_event.get("cum", 0.0) or 0.0),
        duration_sec=duration_sec,
    )


def _gate_checkpoint(
    res: phases.PhaseResult,
    *,
    stage: str,
    phase_label: str,
    floor: int,
    story: str,
    branch: str,
    cum: float,
    emit: Callable[[dict[str, Any]], None],
) -> tuple[int | None, int]:
    """Shared post-phase gate boundary for code-review & browser-smoke (identical logic).

    Both phases end the same way: a non-ok result HALTS with the phase's reason; otherwise a drop
    below the running ``floor`` (:func:`loop.decide.floor_breach`) HALTS as a regression; otherwise
    the floor rises to the phase's pass count. Returns ``(exit_or_None, new_floor)`` — ``exit_or_None``
    is ``1`` to halt now (AFTER emitting the stop event) or ``None`` to continue with the returned
    ``new_floor``. Byte-identical to the two inline blocks it replaces.

    dev-story deliberately does NOT route through here: it checks a codegen-P1 blocker first, uses a
    DIFFERENT floor (the branch baseline) with an auto-rollback SIDE EFFECT + a distinct revert
    reason, and evaluates the floor even on a non-ok gate — there, deciding and acting stay together
    (the refactor's explicit carve-out) while still sharing the :func:`floor_breach` primitive.
    """
    if not res.ok:
        emit(bmad_stop_event(False, res.reason or f"{phase_label} halted for {story}", cum))
        return 1, floor
    phase_pass = int((res.gate or {}).get("pass", floor))
    if floor_breach(phase_pass, floor):
        emit(bmad_stop_event(
            False,
            f"regression on {story}: post-{stage} tests {floor}->{phase_pass}. Work on {branch}.",
            cum,
        ))
        return 1, floor
    return None, max(floor, phase_pass)


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
    # FEATURE 2 -> FEATURE 1: pre-existing test files this story edited in place (computed by the
    # test-integrity check before smoke), fed to the adversarial verifier before the PR.
    modified_tests: list[str] = []

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
    # FINITE decider cap (hang-protection): the review/retro decider is a cheap one-shot call, so
    # a wedged one must not hang the phase. 0 = disabled/unbounded.
    decider_timeout = config.decider_timeout_min * 60 if config.decider_timeout_min > 0 else 0

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
            timeout_sec=config.create_timeout_min * 60 if config.create_timeout_min > 0 else 0,
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
        # FEATURE 3 — plan-gate: bounded readiness check BEFORE spending hours in dev-story. Runs
        # on the ready-for-dev / resumed-in-progress entry (NOT the resume-tail path, which is
        # 'done' and skips this whole block). A BLOCKED verdict halts; everything else fails open.
        if config.plan_gate_enabled:
            pg_exit, pg_cost = _run_plan_gate(
                config, resilient, target, text,
                emit=emit, cwd=str(repo), cum=cum, resume_cmd=resume_cmd,
            )
            cum += pg_cost
            resilient.set_cum(cum)
            if pg_exit is not None:
                return pg_exit, cum
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
            timeout_sec=config.dev_timeout_min * 60 if config.dev_timeout_min > 0 else 0,
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
        if floor_breach(dev_pass, baseline_pass):
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
        # FIX 5 — restore the dev-story completion check (bmad-loop.ps1:789-791). A green gate is
        # NECESSARY but NOT SUFFICIENT: the agent may hit a BMAD HALT partway (unfinished tasks)
        # yet leave a passing gate. The PS original additionally required the story FILE's Status
        # to have reached review|done, halting otherwise so half-done work can't slip into
        # code-review. Re-read the story file and honor its ACTUAL status instead of force-writing.
        dev_status = (
            story_meta(_story_text(project_root, target.key)).get("status") or ""
        ).strip().lower()
        if dev_status not in ("review", "done"):
            reason = (
                f"dev-story did not complete {target.key}: story status is "
                f"{dev_status or '(none)'!r} (expected 'review' or 'done') despite a green gate "
                f"— likely a BMAD HALT / unfinished tasks. Work preserved on {branch}."
            )
            emit(bmad_stop_event(False, reason, cum))
            print(f"\n[HALT] dev-story for {target.key} left status {dev_status or '(none)'!r} "
                  f"(expected 'review'|'done') — likely a BMAD HALT. Inspect {branch}.")
            if resume_cmd:
                print(f"       resume: {resume_cmd}")
            return 1, cum
        _commit_if_dirty(repo, f"feat({target.key}): dev-story complete — {floor_pass} tests green")
        write_cp(f"dev-story done ({target.key})", target.key, branch)
        if honor_stop("phase", stage=f"dev-story done ({target.key})", story=target.key, branch=branch):
            return 0, cum
        # Honor the story file's ACTUAL status (review|done) — no unconditional force-write. A
        # 'done' story correctly skips re-review, matching PS `$st = $meta.Status` (line 797).
        st = dev_status

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
                timeout_sec=config.review_timeout_min * 60 if config.review_timeout_min > 0 else 0,
            )
        else:
            res = phases.code_review(
                resilient,
                # Bind the FINITE decider timeout so phases.code_review's decider(...) call (which
                # doesn't forward a timeout) can't spawn an unbounded decider process.
                partial(review_decider, timeout_sec=decider_timeout),
                target.key,
                emit=emit,
                cwd=str(repo),
                gate_fn=gate_fn,
                max_turns=config.max_review_turns,
                model=review_model,
                effort=review_effort,
                decider_model=decider_model,
                decider_effort=decider_effort,
                timeout_sec=config.review_timeout_min * 60 if config.review_timeout_min > 0 else 0,
            )
        cum += res.cost
        resilient.set_cum(cum)
        # regression guard (bmad-loop.ps1 ~808): review must not drop the passing-test floor.
        rv_exit, floor_pass = _gate_checkpoint(
            res, stage="review", phase_label="code-review",
            floor=floor_pass, story=target.key, branch=branch, cum=cum, emit=emit,
        )
        if rv_exit is not None:
            return rv_exit, cum
        _commit_if_dirty(repo, f"review({target.key}): apply code-review outcomes — {floor_pass} green")
        write_cp(f"code-review done ({target.key})", target.key, branch)
        if honor_stop("phase", stage=f"code-review done ({target.key})", story=target.key, branch=branch):
            return 0, cum

    # --- test-integrity (git vs baseline) — before smoke, so tampering is caught before paying
    #     for the smoke phase. A DELETED pre-existing test halts; MODIFIED ones flow to verify.
    if config.test_integrity_enabled:
        ti_exit, modified_tests = _run_test_integrity(
            config, target,
            repo=repo, project_root=project_root, emit=emit, cum=cum, resume_cmd=resume_cmd,
        )
        if ti_exit is not None:
            return ti_exit, cum

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

        def _smoke_progress_sig() -> str:
            # git work-tree signature (bmad-loop.ps1 ~600): HEAD commit + porcelain status. An
            # unchanged signature across two failed smoke iterations means the agent touched no
            # code, so browser_smoke stops instead of spinning on a "different words, same tree".
            head = gitutil.head(repo) or ""
            r = gitutil._git(["status", "--porcelain"], repo)
            porcelain = (r.stdout or "") if r.returncode == 0 else ""
            return head + "|" + porcelain

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
            progress_sig=_smoke_progress_sig,
        )
        cum += res.cost
        resilient.set_cum(cum)
        # regression guard (bmad-loop.ps1 ~615): post-smoke gate must not drop the floor.
        sm_exit, floor_pass = _gate_checkpoint(
            res, stage="smoke", phase_label="browser-smoke",
            floor=floor_pass, story=target.key, branch=branch, cum=cum, emit=emit,
        )
        if sm_exit is not None:
            return sm_exit, cum
        _commit_if_dirty(repo, f"smoke({target.key}): browser smoke fixes")
        # Safe stop boundary: gate green + work committed, before the push/PR. A `--now`/`--after`
        # request placed during/after smoke is honored here instead of waiting a whole next story.
        write_cp(f"browser-smoke done ({target.key})", target.key, branch)
        if honor_stop("phase", stage=f"browser-smoke done ({target.key})", story=target.key, branch=branch):
            return 0, cum

    # --- FEATURE 1: adversarial verify-before-merge — an INDEPENDENT refute-biased checker tries
    #     to break the claim that the diff satisfies every frozen AC, right before push/PR. REFUTE
    #     blocks the PR (halt); skipped/inconclusive fail open. Runs on the resume-tail too.
    if config.verify_enabled:
        vf_exit, vf_cost = _run_verify(
            config, resilient, target, text,
            repo=repo, project_root=project_root, modified_tests=modified_tests,
            emit=emit, cwd=str(repo), cum=cum, branch=branch, resume_cmd=resume_cmd,
        )
        cum += vf_cost
        resilient.set_cum(cum)
        if vf_exit is not None:
            return vf_exit, cum

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
    # Discard any uncommitted churn before switching branches. Every phase commits its real work
    # via _commit_if_dirty, so the only thing that can be dirty here is THROWAWAY gate output —
    # most often a tracked file the baseline gate's `codegen` re-emits (brain2's
    # convex/_generated/api.d.ts) or line-ending normalization. This bites hardest on the
    # resume-tail ("done") path, where the baseline gate at story entry runs but NO later phase
    # commits, so the churn survives to here. Left dirty it makes the branch switch fail ("your
    # local changes would be overwritten by checkout") — both the _checkout below AND the one
    # `gh pr merge --delete-branch` performs internally — aborting the merge mid-flight.
    gitutil.discard_worktree(repo)
    _checkout(repo, config.merge_base)
    try:
        pr.merge_pr(branch=branch, base=config.merge_base, cwd=str(repo))
    except pr.PrError as e:
        # `gh pr merge --squash --delete-branch` exits non-zero when its LOCAL post-merge cleanup
        # (checkout base / delete the branch) fails EVEN IF the squash merge already landed on the
        # remote. Don't conflate that benign local failure with a real merge failure: confirm the
        # PR's actual state first. If it's already MERGED, fall through to the normal post-merge
        # path (which pulls merge_base, so the next story sees this one as merged) instead of
        # halting — otherwise the loop re-opens + re-merges the SAME story on every resume (this is
        # exactly what merged story 5-1 twice and would have kept opening duplicate PRs).
        if pr.pr_state(branch=branch, cwd=str(repo)) != "MERGED":
            emit(bmad_stop_event(
                False,
                f"auto-merge for {target.key} failed: {e}. Resolve it (conflicts / required review "
                f"/ mergeability), then resume: {resume_cmd}",
                cum,
            ))
            print(f"\n[HALT] auto-merge for {target.key} failed: {e}")
            if resume_cmd:
                print(f"       resume: {resume_cmd}")
            return 1, cum
        # Squash merge already on the remote; only gh's local cleanup tripped. Get back onto
        # merge_base (the failed cleanup may have left us on the now-merged feature branch) so the
        # pull below updates the RIGHT branch, then continue as a normal successful merge.
        _checkout(repo, config.merge_base)
        print(f"  {target.key}: squash merge already landed on the remote; gh's local cleanup hit "
              f"a benign error and was skipped ({e}).")
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
    timeout_sec = config.retro_timeout_min * 60 if config.retro_timeout_min > 0 else 0
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
        timeout_sec=timeout_sec,
    )
    cost = float(getattr(res, "cost_usd", 0.0) or 0.0)
    if getattr(res, "timed_out", False):
        emit(phase_timeout_event(f"retro-epic-{epic}", timeout_sec))
        return False, cost
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
    timeout_sec = config.retro_timeout_min * 60 if config.retro_timeout_min > 0 else 0
    # FINITE decider cap (hang-protection) — independent of the retro phase's own per-call cap.
    decider_timeout = config.decider_timeout_min * 60 if config.decider_timeout_min > 0 else 0
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
                timeout_sec=timeout_sec,
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
                timeout_sec=timeout_sec,
            )
        total_cost += float(getattr(res, "cost_usd", 0.0) or 0.0)

        if getattr(res, "timed_out", False):
            emit(phase_timeout_event(f"retro-epic-{epic} (turn {turn})", timeout_sec))
            return False, total_cost
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
                runner,
                question=q,
                epic_scope=epic,
                model=decider_model,
                effort=decider_effort,
                timeout_sec=decider_timeout,
            )
            emit(retro_answer_event(epic=epic, turn=turn, a=answer))
            continue
        # No marker -> treat as complete (don't spin).
        emit(retro_complete_event(epic=epic, summary="no-marker"))
        return True, total_cost

    return False, total_cost
