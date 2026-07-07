"""Loop configuration cores — model tiering + a ``loop.json`` engine-block loader.

- :func:`model_for_phase` is a verbatim port of ``Get-ModelForPhase`` (loopcore.ps1
  ~214-228).
- :class:`EngineConfig` + :func:`from_loop_json` parse the ``engine`` block of a
  ``loop.json`` (PROTOCOL §7) into typed fields, applying the SAME defaults the PowerShell
  ``loop.ps1`` param block declares (~lines 79-105) when a field is absent.

Pure parsing only — no network, no claude. The only I/O is reading a ``loop.json`` path
(stdlib ``json``) when one is passed instead of an already-parsed dict.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from orrery_loop.configkeys import resolve, warn_unknown_keys

# Defaults for Get-ModelForPhase (loopcore.ps1 ~223).
_MODEL_DEFAULTS = {
    "discover": "haiku",
    "execute": "sonnet",
    "judge": "haiku",
    "hard": "opus",
}

# Defaults from the loop.ps1 param block (~79-105) + loopcore gate default (~113-117).
_DEFAULT_MAX_TURNS = 30
_DEFAULT_MAX_ITERS = 15
# Per-iteration execute wall-clock cap (minutes; 0 = disabled/unbounded). Guards against a hung
# runner process silently blocking an unattended overnight run forever (Wave A1 "don't hang").
_DEFAULT_ITER_TIMEOUT_MIN = 60
_DEFAULT_STAGNATION_LIMIT = 2
_DEFAULT_PLATEAU_LIMIT = 3
_DEFAULT_REGRESS_LIMIT = 3
_DEFAULT_CEILING_USD = 3.00
_DEFAULT_ALERT_PCT = [50, 80, 100]
_DEFAULT_PERMISSION_MODE = "acceptEdits"
_DEFAULT_ALLOWED_TOOLS = ["Read", "Edit", "Write", "Bash(bun test)", "Bash(bun test:*)"]
_DEFAULT_GRACEFUL_AT_PHASE = True
_DEFAULT_LOCK_GLOBS = ["*.test.ts"]
# Curated *pure* test-infrastructure files whose only job is test collection/config. When
# ``gate.lock_infra`` is on, these globs are merged into the hash-lock set
# (:func:`orrery_loop.core._lock_glob_set`) so an actor cannot neuter the suite by editing
# collection/config (e.g. skipping tests in ``conftest.py`` or a ``vitest.config``) instead of the
# already-locked test files. Deliberately EXCLUDES dual-purpose files (``pyproject.toml``,
# ``package.json``, ``setup.cfg``): those hold real dependencies/scripts too, so locking them would
# trip a false tamper on a legitimate edit — a project that keeps its test config there should add
# the specific file to ``gate.lockGlobs`` explicitly.
INFRA_LOCK_GLOBS = [
    "conftest.py",
    "pytest.ini",
    "tox.ini",
    "bunfig.toml",
    "jest.config.*",
    "vitest.config.*",
    "playwright.config.*",
    "cypress.config.*",
    "karma.conf.*",
    ".mocharc.*",
]
_DEFAULT_JUDGE_MODEL = "haiku"
_DEFAULT_GATE_STAGES = [
    {
        "name": "test",
        "command": "bun test",
        "pass_pattern": r"(\d+)\s+pass",
        "fail_pattern": r"(\d+)\s+fail",
    }
]


def model_for_phase(phase: str, models: dict[str, str] | None) -> str:
    """Port of ``Get-ModelForPhase``.

    Pick the model tier for a phase (``discover`` | ``execute`` | ``judge`` | ``hard``)
    from a user map, falling back to defaults (discover=haiku, execute=sonnet, judge=haiku,
    hard=opus). The phase name is matched case-insensitively; an unknown phase falls back
    to ``'sonnet'`` (the safe middle tier). Any string the user supplies is honored, so
    custom aliases work.
    """
    key = str(phase).lower()
    if models and models.get(key):
        return str(models[key])
    if key in _MODEL_DEFAULTS:
        return _MODEL_DEFAULTS[key]
    return "sonnet"  # unknown phase -> safe middle tier


def _as_str_list(value: Any) -> list[str]:
    """Coerce a JSON value to a ``list[str]``: a string -> ``[string]``; a list -> list."""
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    return [str(v) for v in value]


@dataclass
class GateStage:
    """One gate stage: a command plus the regexes that read pass/fail counts from its output.

    ``held_out`` opts the stage into the HIDDEN suite split (orrery_loop.verify): it still runs and
    still counts toward overall green, but its output is stripped from everything the agent
    sees and its ``lock_globs`` are merged into the hash-lock set. Both default OFF so a stage
    without them behaves exactly as before.
    """

    name: str
    command: str
    pass_pattern: str | None = None
    fail_pattern: str | None = None
    held_out: bool = False
    lock_globs: list[str] = field(default_factory=list)


@dataclass
class GateConfig:
    """The ``gate`` block: ordered stages + the locked-file globs.

    Green semantics are NOT configurable (there is no ``green_when`` field — it was parsed but
    never consulted; see PROTOCOL.md §7): a stage passes when its command exits ``0``, and the
    gate is green when EVERY stage passes (:func:`orrery_loop.gate.run_gate`).
    """

    stages: list[GateStage] = field(default_factory=list)
    lock_globs: list[str] = field(default_factory=lambda: list(_DEFAULT_LOCK_GLOBS))
    # Opt-in short-circuit: stop launching gate stages after the first non-zero exit (skipped
    # stages still appear in the result — see :func:`orrery_loop.gate.run_gate`). Default OFF so the gate
    # runs every stage exactly as before (parity).
    fail_fast: bool = False
    # Opt-in: also hash-lock the curated test-INFRASTRUCTURE files (:data:`INFRA_LOCK_GLOBS`) so an
    # actor can't neuter the suite by editing collection/config (conftest.py, pytest.ini, a
    # jest/vitest/playwright config, …) instead of the already-locked test files. Default OFF so
    # the hash-lock set is byte-identical to before (parity).
    lock_infra: bool = False


@dataclass
class CostConfig:
    """The ``cost`` block: the cumulative USD ceiling and the alert thresholds (percent)."""

    ceiling_usd: float = _DEFAULT_CEILING_USD
    alert_pct: list[int] = field(default_factory=lambda: list(_DEFAULT_ALERT_PCT))


@dataclass
class StopConfig:
    """The ``stop`` block: iteration cap + the stagnation/plateau/regress limits."""

    max_iters: int = _DEFAULT_MAX_ITERS
    stagnation_limit: int = _DEFAULT_STAGNATION_LIMIT
    plateau_limit: int = _DEFAULT_PLATEAU_LIMIT
    regress_limit: int = _DEFAULT_REGRESS_LIMIT
    graceful_at_phase: bool = _DEFAULT_GRACEFUL_AT_PHASE
    # Opt-in cumulative TOKEN budget (input+output+cache tokens summed across iterations). 0 =
    # disabled (parity). The subscription-era companion to ``cost.ceiling_usd``: on a flat-rate
    # plan the CLI's dollar figure is ~meaningless, so a token cap is the real spend backstop.
    # A run stops (not-green) the first iteration cumulative tokens reach this ceiling.
    token_ceiling: int = 0


@dataclass
class VerifyConfig:
    """The ``verify`` block: the judge model + the frozen acceptance-criteria contract.

    ``enabled`` is the switch (mirrors ``loop.ps1``'s ``-Verify``): only when True does the loop
    emit the judge ``model`` event and run the anti-false-green VERIFY pass. A non-empty
    ``contract`` is NOT sufficient on its own — it only seeds the frozen criteria.

    ``mutation_audit`` enables the advisory mutation-strength probe (orrery_loop.verify) when the gate
    is green; ``mutation_every`` throttles it (run only every Nth green iter; 0/1 = every green
    iter). Both default OFF so behavior is unchanged.
    """

    judge_model: str = _DEFAULT_JUDGE_MODEL
    contract: list[str] = field(default_factory=list)
    enabled: bool = False
    mutation_audit: bool = False
    mutation_every: int = 0


@dataclass
class FeedbackConfig:
    """The ``feedback`` block: compact the gate feedback shown to the agent (orrery_loop.feedback).

    ``compact`` OFF (default) -> today's behavior (the raw gate dump is the volatile steer);
    ON -> only the FIRST failing stage's first failure is fed back.
    """

    compact: bool = False


@dataclass
class MemoryConfig:
    """The ``memory`` block: cross-run lessons store (orrery_loop.memory).

    ``enabled`` OFF (default) -> a NullMemoryStore (recall ``""``, record a no-op). When ON a
    ``FileMemoryStore`` is built at ``path`` (or ``<state_dir>/memory.jsonl`` when unset).
    ``recall_limit`` caps how many lessons recall surfaces into the stable prefix.
    """

    enabled: bool = False
    path: str | None = None
    recall_limit: int = 5


@dataclass
class MetricsConfig:
    """The ``metrics`` block: emit a run-quality ``metrics`` event at stop (orrery_loop.metrics).

    ``emit`` OFF (default) -> no ``metrics`` event (parity preserved).
    """

    emit: bool = False


@dataclass
class EngineConfig:
    """Typed view of a ``loop.json`` ``engine`` block (PROTOCOL §7).

    Fields absent from the JSON take the same defaults the PowerShell ``loop.ps1`` param
    block declares, so a partial ``engine`` block parses to a fully-populated config.
    """

    task: str = "TASK.md"
    models: dict[str, str] = field(default_factory=dict)
    max_turns: int = _DEFAULT_MAX_TURNS
    # Per-iteration execute wall-clock cap in MINUTES (0 = disabled/unbounded). Threaded into the
    # execute `runner.run(..., timeout_sec=...)` call; a hung agent process is killed and the
    # iteration follows the existing phase-timeout path (orrery_loop.core._run_loop_body).
    iter_timeout_min: int = _DEFAULT_ITER_TIMEOUT_MIN
    allowed_tools: list[str] = field(default_factory=lambda: list(_DEFAULT_ALLOWED_TOOLS))
    permission_mode: str = _DEFAULT_PERMISSION_MODE
    gate: GateConfig = field(default_factory=GateConfig)
    cost: CostConfig = field(default_factory=CostConfig)
    stop: StopConfig = field(default_factory=StopConfig)
    verify: VerifyConfig = field(default_factory=VerifyConfig)
    feedback: FeedbackConfig = field(default_factory=FeedbackConfig)
    memory: MemoryConfig = field(default_factory=MemoryConfig)
    metrics: MetricsConfig = field(default_factory=MetricsConfig)
    # Wave-4 Task A — overload resilience: a comma-separated model chain the claude CLI tries when
    # the primary model is overloaded/unavailable (``--fallback-model``). "" = omitted (parity).
    # Threaded into the execute + verify runner.run calls (empty -> byte-identical argv).
    fallback_model: str = ""
    # Wave-4 Task C (EXPERIMENTAL, default "off") — in-session gate prototype for the GENERIC loop
    # only. "off" = no change (parity); "stop-hook" installs a Stop hook (via --settings) that
    # re-runs the gate and BLOCKS turn-end until green; "goal" prepends a /goal condition line to
    # the execute prompt. The orchestrator's real external gate remains the sole arbiter either way.
    session_gate: str = "off"
    # The --loop-json path THIS run was launched with, if any. Not sourced from the JSON content
    # itself (a file doesn't know its own path) — cli.py sets it after loading, mirroring
    # orrery_loop.bmad.driver.BmadConfig.loop_json. A resume/Reignite must re-point at it (orrery_loop.core's
    # checkpoint `resume` string) or per-run tuning that has no other CLI surface (e.g. custom
    # gate stages) silently reverts to defaults. "" = none.
    loop_json: str = ""

    def model_for(self, phase: str) -> str:
        """Resolve a phase to its model tier through this config's ``models`` map."""
        return model_for_phase(phase, self.models)


# Keys `_gate_from` actually reads (both spellings) — anything else warns. `greenWhen` /
# `green_when` are RETIRED (Task 5): the field was parsed but the gate has always hardcoded
# "green = every stage exit 0" (orrery_loop.gate.run_gate); old configs that still carry it get a
# gentler "retired" notice instead of "unrecognized key".
_GATE_KNOWN_KEYS = {
    "stages",
    "lockGlobs",
    "lock_globs",
    "failFast",
    "fail_fast",
    "lockInfra",
    "lock_infra",
}
_GATE_RETIRED_KEYS = {"greenWhen", "green_when"}


def _gate_from(d: dict[str, Any]) -> GateConfig:
    warn_unknown_keys(d, _GATE_KNOWN_KEYS, "engine.gate", retired=_GATE_RETIRED_KEYS)
    stages_raw = resolve(d, "stages", default=None)
    if stages_raw is None:
        stages = [GateStage(**s) for s in _DEFAULT_GATE_STAGES]
    else:
        stages = [
            GateStage(
                name=resolve(s, "name", default=""),
                command=resolve(s, "command", default=""),
                pass_pattern=resolve(s, "passPattern", "pass_pattern"),
                fail_pattern=resolve(s, "failPattern", "fail_pattern"),
                held_out=bool(resolve(s, "heldOut", "held_out", default=False)),
                lock_globs=_as_str_list(resolve(s, "lockGlobs", "lock_globs", default=[])),
            )
            for s in stages_raw
        ]
    return GateConfig(
        stages=stages,
        lock_globs=list(resolve(d, "lockGlobs", "lock_globs", default=list(_DEFAULT_LOCK_GLOBS))),
        fail_fast=bool(resolve(d, "failFast", "fail_fast", default=False)),
        lock_infra=bool(resolve(d, "lockInfra", "lock_infra", default=False)),
    )


# Per-block known-key sets (both spellings) — a typo in any sub-block warns instead of silently
# vanishing, matching `_gate_from`/`_stop_from`. Keep in sync with the fields each parser reads.
_COST_KNOWN_KEYS = {"ceilingUsd", "ceiling_usd", "alertPct", "alert_pct"}


def _cost_from(d: dict[str, Any]) -> CostConfig:
    warn_unknown_keys(d, _COST_KNOWN_KEYS, "engine.cost")
    return CostConfig(
        ceiling_usd=float(resolve(d, "ceilingUsd", "ceiling_usd", default=_DEFAULT_CEILING_USD)),
        alert_pct=list(resolve(d, "alertPct", "alert_pct", default=list(_DEFAULT_ALERT_PCT))),
    )


# Keys `_stop_from` actually reads (both spellings) — anything else warns, so a typo like
# `tokenCeilng` is LOUD instead of silently disabling the budget backstop (parity with
# `_gate_from`'s `_GATE_KNOWN_KEYS` check).
_STOP_KNOWN_KEYS = {
    "maxIters", "max_iters",
    "stagnationLimit", "stagnation_limit",
    "plateauLimit", "plateau_limit",
    "regressLimit", "regress_limit",
    "gracefulAtPhase", "graceful_at_phase",
    "tokenCeiling", "token_ceiling",
}


def _stop_from(d: dict[str, Any]) -> StopConfig:
    warn_unknown_keys(d, _STOP_KNOWN_KEYS, "engine.stop")
    return StopConfig(
        max_iters=int(resolve(d, "maxIters", "max_iters", default=_DEFAULT_MAX_ITERS)),
        stagnation_limit=int(
            resolve(d, "stagnationLimit", "stagnation_limit", default=_DEFAULT_STAGNATION_LIMIT)
        ),
        plateau_limit=int(
            resolve(d, "plateauLimit", "plateau_limit", default=_DEFAULT_PLATEAU_LIMIT)
        ),
        regress_limit=int(
            resolve(d, "regressLimit", "regress_limit", default=_DEFAULT_REGRESS_LIMIT)
        ),
        graceful_at_phase=bool(
            resolve(d, "gracefulAtPhase", "graceful_at_phase", default=_DEFAULT_GRACEFUL_AT_PHASE)
        ),
        token_ceiling=int(resolve(d, "tokenCeiling", "token_ceiling", default=0)),
    )


_VERIFY_KNOWN_KEYS = {
    "judgeModel", "judge_model",
    "contract",
    "enabled",
    "mutationAudit", "mutation_audit",
    "mutationEvery", "mutation_every",
}


def _verify_from(d: dict[str, Any]) -> VerifyConfig:
    warn_unknown_keys(d, _VERIFY_KNOWN_KEYS, "engine.verify")
    return VerifyConfig(
        judge_model=resolve(d, "judgeModel", "judge_model", default=_DEFAULT_JUDGE_MODEL),
        contract=list(resolve(d, "contract", default=[])),
        enabled=bool(resolve(d, "enabled", default=False)),
        mutation_audit=bool(resolve(d, "mutationAudit", "mutation_audit", default=False)),
        mutation_every=int(resolve(d, "mutationEvery", "mutation_every", default=0)),
    )


_FEEDBACK_KNOWN_KEYS = {"compact"}


def _feedback_from(d: dict[str, Any]) -> FeedbackConfig:
    warn_unknown_keys(d, _FEEDBACK_KNOWN_KEYS, "engine.feedback")
    return FeedbackConfig(compact=bool(resolve(d, "compact", default=False)))


_MEMORY_KNOWN_KEYS = {"enabled", "path", "recallLimit", "recall_limit"}


def _memory_from(d: dict[str, Any]) -> MemoryConfig:
    warn_unknown_keys(d, _MEMORY_KNOWN_KEYS, "engine.memory")
    return MemoryConfig(
        enabled=bool(resolve(d, "enabled", default=False)),
        path=resolve(d, "path", default=None),
        recall_limit=int(resolve(d, "recallLimit", "recall_limit", default=5)),
    )


_METRICS_KNOWN_KEYS = {"emit"}


def _metrics_from(d: dict[str, Any]) -> MetricsConfig:
    warn_unknown_keys(d, _METRICS_KNOWN_KEYS, "engine.metrics")
    return MetricsConfig(emit=bool(resolve(d, "emit", default=False)))


# Keys the top-level `engine` block itself reads (both spellings); the per-block parsers
# (`_gate_from`, `_cost_from`, ...) warn on their own sub-dicts separately.
_ENGINE_KNOWN_KEYS = {
    "task",
    "models",
    "maxTurns", "max_turns",
    "iterTimeoutMin", "iter_timeout_min",
    "allowedTools", "allowed_tools",
    "permissionMode", "permission_mode",
    "fallbackModel", "fallback_model",
    "sessionGate", "session_gate",
    "gate", "cost", "stop", "verify", "feedback", "memory", "metrics",
}


def from_loop_json(path_or_dict: str | Path | dict[str, Any]) -> EngineConfig:
    """Parse the ``engine`` block of a ``loop.json`` into an :class:`EngineConfig`.

    Accepts a path to a ``loop.json`` file, an already-parsed full loop dict, or just the
    ``engine`` sub-dict. camelCase JSON keys (``maxTurns``, ``iterTimeoutMin``, ``allowedTools``,
    ``permissionMode``, ``passPattern``, ``failPattern``, ``lockGlobs``, ``ceilingUsd``,
    ``alertPct``, ``maxIters``, ``stagnationLimit``, ``plateauLimit``, ``regressLimit``,
    ``gracefulAtPhase``, ``judgeModel``) are accepted, as are snake_case equivalents. Absent
    fields fall back to the ``loop.ps1`` defaults. An unrecognized key anywhere in the block
    (or one of its ``gate``/``cost``/``stop``/``verify``/``feedback``/``memory``/``metrics``
    sub-blocks) prints a stderr warning (:func:`orrery_loop.configkeys.warn_unknown_keys`) instead of
    silently vanishing — a retired key (``gate.greenWhen`` — PROTOCOL.md §7) gets a gentler
    "retired" notice.
    """
    if isinstance(path_or_dict, dict):
        data = path_or_dict
    else:
        data = json.loads(Path(path_or_dict).read_text(encoding="utf-8"))

    # Accept either the whole loop.json (with an "engine" key) or the engine block itself.
    eng = data.get("engine", data) if isinstance(data, dict) else {}
    if eng is None:
        eng = {}
    warn_unknown_keys(eng, _ENGINE_KNOWN_KEYS, "engine")

    return EngineConfig(
        task=resolve(eng, "task", default="TASK.md"),
        models=dict(resolve(eng, "models", default={})),
        max_turns=int(resolve(eng, "maxTurns", "max_turns", default=_DEFAULT_MAX_TURNS)),
        iter_timeout_min=int(
            resolve(eng, "iterTimeoutMin", "iter_timeout_min", default=_DEFAULT_ITER_TIMEOUT_MIN)
        ),
        allowed_tools=list(
            resolve(eng, "allowedTools", "allowed_tools", default=list(_DEFAULT_ALLOWED_TOOLS))
        ),
        permission_mode=resolve(
            eng, "permissionMode", "permission_mode", default=_DEFAULT_PERMISSION_MODE
        ),
        fallback_model=str(resolve(eng, "fallbackModel", "fallback_model", default="") or ""),
        session_gate=str(resolve(eng, "sessionGate", "session_gate", default="off") or "off"),
        gate=_gate_from(resolve(eng, "gate", default={}) or {}),
        cost=_cost_from(resolve(eng, "cost", default={}) or {}),
        stop=_stop_from(resolve(eng, "stop", default={}) or {}),
        verify=_verify_from(resolve(eng, "verify", default={}) or {}),
        feedback=_feedback_from(resolve(eng, "feedback", default={}) or {}),
        memory=_memory_from(resolve(eng, "memory", default={}) or {}),
        metrics=_metrics_from(resolve(eng, "metrics", default={}) or {}),
    )
