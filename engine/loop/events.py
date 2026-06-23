"""Event builders — the SINGLE SOURCE OF TRUTH for ``log.jsonl`` event shapes.

Faithful Python port of every ``New-*Event`` / ``New-Checkpoint`` builder in
``loopcore.ps1`` plus the core events PROTOCOL.md §2 defines (some of which are built
inline in ``loop.ps1``, not ``loopcore.ps1`` — those are implemented here from the
PROTOCOL.md §2 spec).

Every function returns a plain ``dict`` (NOT serialized). Field names are **camelCase on
the wire** exactly as PROTOCOL.md and the PowerShell ``New-*Event`` objects emit them.

Fidelity rules locked by the golden corpus (``tests/fixtures/golden_events.jsonl``):

- ``review_question_event`` OMITS the ``story`` key entirely when story is falsy
  (mirrors ``New-ReviewQuestionEvent`` building an ordered map that only adds ``story``
  if truthy).
- ``quota_wait_event`` ALWAYS includes ``resetType``. When the reset type is unknown the
  PowerShell ``[string] $ResetType = $null`` param coerces ``$null`` to the empty string,
  and ``ConvertTo-Json`` emits ``"resetType": ""`` — NOT null. We replicate that: a
  ``reset_type`` of ``None`` serializes as ``""``.
- ``quota_hit_event`` ALWAYS includes ``resetAt``; when no reset is known the value is
  JSON null (``None``).
- ``new_checkpoint`` rounds ``cumUsd`` to 4 dp; ``cache_event`` rounds ``hitRatio`` to 4 dp
  (matching ``[math]::Round(x, 4)`` — banker's rounding, same as Python's ``round``).
- Timestamps are ISO-8601 strings; the ``now`` / ``updated_at`` / ``reset_at`` datetimes
  are injectable and default to ``datetime.now(timezone.utc)``.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    """ISO-8601 string for a timezone-aware datetime.

    Tests compare timestamp-valued fields by parsed instant, so the exact textual format
    need not byte-match the PowerShell ``.ToString('o')`` output (which uses 7 fractional
    digits and a literal ``Z``). We emit a normalized UTC ISO string ending in ``Z``.
    """
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    dt = dt.astimezone(timezone.utc)
    return dt.isoformat().replace("+00:00", "Z")


# ---------------------------------------------------------------------------
# PROTOCOL §2 "Core" events (built inline in loop.ps1; spec'd from PROTOCOL.md)
# ---------------------------------------------------------------------------


def iter_event(
    iter: int,
    cost: float,
    cum: float,
    pass_: int,
    total: int,
    best: int,
    changed: bool,
    stale: int,
    plateau: int,
    regress: int,
    action: str,
    reason: str,
) -> dict[str, Any]:
    """PROTOCOL §2 ``iter`` event — the per-iteration summary line.

    ``action`` is one of ``stop`` | ``rollback`` | ``continue``.
    """
    return {
        "event": "iter",
        "iter": iter,
        "cost": cost,
        "cum": cum,
        "pass": pass_,
        "total": total,
        "best": best,
        "changed": changed,
        "stale": stale,
        "plateau": plateau,
        "regress": regress,
        "action": action,
        "reason": reason,
    }


def stop_event(
    reason: str,
    green: bool,
    iter: int,
    cum: float,
    best_pass: int,
) -> dict[str, Any]:
    """PROTOCOL §2 ``stop`` event."""
    return {
        "event": "stop",
        "reason": reason,
        "green": green,
        "iter": iter,
        "cum": cum,
        "bestPass": best_pass,
    }


def parse_error_event(iter: int) -> dict[str, Any]:
    """PROTOCOL §2 ``parse_error`` event."""
    return {"event": "parse_error", "iter": iter}


def metrics_event(
    first_try_green: bool,
    iters_to_green: int | None,
    cost_to_green: float | None,
    rollbacks: int,
    regression_rate: float,
    total_iters: int,
    total_cost: float,
    final_green: bool,
) -> dict[str, Any]:
    """Engine-v3 additive ``metrics`` event — the run-quality summary line.

    Emitted ONCE at stop when ``metrics.emit`` is on; fields are the camelCase wire form of
    :func:`loop.metrics.compute_metrics`'s output. NEW in engine v3 (no PowerShell
    equivalent): not part of the golden corpus, so it is NOT added to ``gen_golden.ps1``.
    Unknown events are logged-but-ignored by the reducer, so this is backward-compatible.
    """
    return {
        "event": "metrics",
        "firstTryGreen": bool(first_try_green),
        "itersToGreen": iters_to_green,
        "costToGreen": cost_to_green,
        "rollbacks": rollbacks,
        "regressionRate": regression_rate,
        "totalIters": total_iters,
        "totalCost": total_cost,
        "finalGreen": bool(final_green),
    }


def gate_event(
    cum: float,
    green: bool,
    pass_: int,
    fail: int,
    total: int,
    story: str | None = None,
    baseline_pass: int | None = None,
    stages: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """PROTOCOL §2 ``gate`` event.

    ``story``, ``baselinePass`` and ``stages`` are optional in the spec and are omitted
    when not supplied.
    """
    o: dict[str, Any] = {"event": "gate"}
    if story is not None:
        o["story"] = story
    o["cum"] = cum
    o["green"] = green
    o["pass"] = pass_
    o["fail"] = fail
    o["total"] = total
    if baseline_pass is not None:
        o["baselinePass"] = baseline_pass
    if stages is not None:
        o["stages"] = stages
    return o


def model_event(
    phase: str,
    model: str,
    cost_per_turn: float | None = None,
) -> dict[str, Any]:
    """PROTOCOL §2 ``model`` event. ``costPerTurn`` is optional."""
    o: dict[str, Any] = {"event": "model", "phase": phase, "model": model}
    if cost_per_turn is not None:
        o["costPerTurn"] = cost_per_turn
    return o


def cost_alert_event(pct: int, cum: float, ceiling: float) -> dict[str, Any]:
    """PROTOCOL §2 ``cost-alert`` event. ``pct`` is one of 50 | 80 | 100."""
    return {"event": "cost-alert", "pct": pct, "cum": cum, "ceiling": ceiling}


# ---------------------------------------------------------------------------
# verdict — event shape mirrors ConvertFrom-VerdictJson's OUTPUT object
# (the PARSER itself is a later wave; this builds the already-parsed shape)
# ---------------------------------------------------------------------------


def verdict_event(
    pass_: bool,
    failing_criteria: list[str],
    next_action: str | None,
    item: str | None = None,
    evidence: str | None = None,
    model: str | None = None,
) -> dict[str, Any]:
    """``verdict`` event — mirrors the OUTPUT object of ``ConvertFrom-VerdictJson``.

    Takes already-parsed fields. ``pass`` is coerced to bool and ``failingCriteria`` to a
    list, matching the PowerShell ``[bool]$pass`` / ``@($failing)`` coercions.
    """
    return {
        "event": "verdict",
        "item": item,
        "pass": bool(pass_),
        "failingCriteria": list(failing_criteria) if failing_criteria else [],
        "evidence": evidence,
        "nextAction": next_action,
        "model": model,
    }


# ---------------------------------------------------------------------------
# loopcore.ps1 New-*Event / New-Checkpoint ports
# ---------------------------------------------------------------------------


def cache_event(hit_ratio: float, warm: bool) -> dict[str, Any]:
    """Port of ``New-CacheEvent``. ``hitRatio`` rounded to 4 dp."""
    return {
        "event": "cache",
        "hitRatio": round(float(hit_ratio), 4),
        "warm": bool(warm),
    }


def plateau_event(item: str, k: int) -> dict[str, Any]:
    """Port of ``New-PlateauEvent``."""
    return {"event": "plateau", "item": item, "k": k}


def rollback_event(
    item: str,
    to_iter: int,
    best_pass: int,
    strike: int,
    strike_budget: int,
) -> dict[str, Any]:
    """Port of ``New-RollbackEvent``."""
    return {
        "event": "rollback",
        "item": item,
        "toIter": to_iter,
        "bestPass": best_pass,
        "strike": strike,
        "strikeBudget": strike_budget,
    }


def handoff_event(item: str, reason: str, consecutive: int) -> dict[str, Any]:
    """Port of ``New-HandoffEvent``."""
    return {
        "event": "handoff",
        "item": item,
        "reason": reason,
        "consecutive": consecutive,
    }


def phase_timeout_event(label: str, timeout_sec: int) -> dict[str, Any]:
    """Port of ``New-PhaseTimeoutEvent``."""
    return {"event": "phase-timeout", "label": label, "timeoutSec": timeout_sec}


def quota_hit_event(
    label: str,
    cum: float,
    reset_at: datetime | None = None,
) -> dict[str, Any]:
    """Port of ``New-QuotaHitEvent``.

    ``resetAt`` is ALWAYS present: an ISO-8601 string when a reset moment is known, else
    JSON null (``None``).
    """
    return {
        "event": "quota-hit",
        "label": label,
        "cum": cum,
        "resetAt": _iso(reset_at) if reset_at else None,
    }


def quota_wait_event(
    label: str,
    cum: float,
    wait_sec: int,
    probe: int,
    reset_type: str | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Port of ``New-QuotaWaitEvent``.

    ``resumeAt`` = ``now + wait_sec`` (ISO-8601). ``resetType`` is ALWAYS present; when the
    type is unknown the PowerShell ``[string]`` param coerces ``$null`` to the empty
    string, so a ``reset_type`` of ``None`` serializes as ``""`` (NOT null).
    """
    if now is None:
        now = _now()
    return {
        "event": "quota-wait",
        "label": label,
        "cum": cum,
        "waitSec": wait_sec,
        "resumeAt": _iso(now + timedelta(seconds=wait_sec)),
        "probe": probe,
        "resetType": reset_type if reset_type is not None else "",
    }


def quota_resume_event(label: str, probe: int) -> dict[str, Any]:
    """Port of ``New-QuotaResumeEvent``."""
    return {"event": "quota-resume", "label": label, "probe": probe}


def cooperative_stop_event(
    scope: str,
    mode: str,
    stage: str,
    story: str | None,
    branch: str,
    cum: float,
) -> dict[str, Any]:
    """Port of ``New-CooperativeStopEvent``."""
    return {
        "event": "cooperative-stop",
        "scope": scope,
        "mode": mode,
        "stage": stage,
        "story": story,
        "branch": branch,
        "cum": cum,
    }


def review_question_event(
    turn: int,
    q: str,
    story: str | None = None,
) -> dict[str, Any]:
    """Port of ``New-ReviewQuestionEvent``.

    ``story`` is OMITTED entirely when falsy (``None`` or empty string), matching the
    PowerShell ordered map that only adds ``story`` when truthy.
    """
    o: dict[str, Any] = {"event": "review-question", "turn": turn, "q": q}
    if story:
        o["story"] = story
    return o


def review_answer_event(turn: int, a: str) -> dict[str, Any]:
    """Port of ``New-ReviewAnswerEvent``."""
    return {"event": "review-answer", "turn": turn, "a": a}


def new_checkpoint(
    stage: str,
    story: str | None,
    branch: str,
    merge_base: str,
    cum_usd: float,
    resume: str,
    updated_at: datetime | None = None,
) -> dict[str, Any]:
    """Port of ``New-Checkpoint`` — the PROTOCOL §7 ``checkpoint.json`` object.

    ``cumUsd`` is rounded to 4 dp; ``updatedAt`` is an ISO-8601 string. Key order mirrors
    the PowerShell ordered map for a stable round-trip.
    """
    if updated_at is None:
        updated_at = _now()
    return {
        "updatedAt": _iso(updated_at),
        "stage": stage,
        "story": story,
        "branch": branch,
        "mergeBase": merge_base,
        "cumUsd": round(float(cum_usd), 4),
        "resume": resume,
    }


# ---------------------------------------------------------------------------
# BMAD adapter events (PROTOCOL §2 "BMAD adapter"; built inline in bmad-loop.ps1
# as ``Write-BLog @{ event=... }`` hashtables). camelCase wire keys exactly.
#
# ``review-question`` / ``review-answer`` / ``cooperative-stop`` / ``quota-*`` are
# already defined above (shared with the core/loopcore events) — NOT duplicated here.
# ---------------------------------------------------------------------------


def engine_start_event(merge_base: str | None = None) -> dict[str, Any]:
    """Heartbeat written as the VERY FIRST log line — before the slow preflight (git checkout of
    the merge-base + the baseline gate) — so a watcher/UI sees the run is alive within ~1s instead
    of staring at an empty log through the whole cold start. The reducer treats it as 'running'."""
    o: dict[str, Any] = {"event": "engine-start"}
    if merge_base is not None:
        o["mergeBase"] = merge_base
    return o


def start_event(target: str, branch: str, baseline_pass: int) -> dict[str, Any]:
    """BMAD ``start`` event — ``Write-BLog @{ event="start"; target=...; branch=...;
    baselinePass=... }``."""
    return {
        "event": "start",
        "target": target,
        "branch": branch,
        "baselinePass": baseline_pass,
    }


def story_start_event(
    story: str,
    status: str,
    epic: str | None = None,
    index: int | None = None,
) -> dict[str, Any]:
    """BMAD ``story-start`` event.

    ``epic`` and ``index`` are OMITTED when ``None`` (the real bmad log is
    non-deterministic and frequently writes lines with neither — the adapter
    backfills ``epic`` from the story key). When supplied, bmad-loop.ps1 emits both.
    """
    o: dict[str, Any] = {"event": "story-start", "story": story, "status": status}
    if epic is not None:
        o["epic"] = epic
    if index is not None:
        o["index"] = index
    return o


def dev_gate_event(
    story: str,
    cum: float,
    green: bool,
    pass_: int,
    fail: int,
    total: int,
    baseline_pass: int,
    status: str,
    codegen_ok: bool,
    lint_ok: bool,
    test_ok: bool,
) -> dict[str, Any]:
    """BMAD ``dev-gate`` event. Wire key for ``pass_`` is ``pass``.

    Note: bmad-loop.ps1's inline ``Write-BLog`` omits ``codegenOk``/``lintOk``/``testOk``
    on the hashtable it writes, but PROTOCOL §2 lists them as required fields of the
    ``dev-gate`` shape; this builder follows PROTOCOL and always includes all three.
    """
    return {
        "event": "dev-gate",
        "story": story,
        "cum": cum,
        "green": green,
        "pass": pass_,
        "fail": fail,
        "total": total,
        "baselinePass": baseline_pass,
        "status": status,
        "codegenOk": codegen_ok,
        "lintOk": lint_ok,
        "testOk": test_ok,
    }


def review_complete_event(turn: int, summary: str) -> dict[str, Any]:
    """BMAD ``review-complete`` event."""
    return {"event": "review-complete", "turn": turn, "summary": summary}


def retro_start_event(epic: str) -> dict[str, Any]:
    """BMAD ``retro-start`` event — ``Write-BLog @{ event='retro-start'; epic=... }``."""
    return {"event": "retro-start", "epic": epic}


def retro_question_event(epic: str, turn: int, q: str) -> dict[str, Any]:
    """BMAD ``retro-question`` event."""
    return {"event": "retro-question", "epic": epic, "turn": turn, "q": q}


def retro_answer_event(epic: str, turn: int, a: str) -> dict[str, Any]:
    """BMAD ``retro-answer`` event."""
    return {"event": "retro-answer", "epic": epic, "turn": turn, "a": a}


def retro_complete_event(epic: str, summary: str) -> dict[str, Any]:
    """BMAD ``retro-complete`` event.

    bmad-loop.ps1 also writes a ``turn`` here; PROTOCOL §2 spec's the ``retro-*`` shape
    as ``{ epic, turn?, ... }`` (turn optional). This builder follows the PROTOCOL/task
    signature (``epic`` + ``summary``) and omits ``turn``.
    """
    return {"event": "retro-complete", "epic": epic, "summary": summary}


def smoke_server_event(url: str, root_code: int) -> dict[str, Any]:
    """BMAD ``smoke-server`` event. Wire key for ``root_code`` is ``rootCode``."""
    return {"event": "smoke-server", "url": url, "rootCode": root_code}


def smoke_iter_event(
    iter_: int,
    passed: bool,
    verdict: str,
    timed_out: bool | None = None,
    verified: list[str] | None = None,
    deferred: list[str] | None = None,
) -> dict[str, Any]:
    """BMAD ``smoke-iter`` event. Wire keys: ``iter`` (from ``iter_``), ``timedOut``.

    ``timedOut`` is OMITTED when ``None`` — bmad-loop.ps1 writes EITHER a timeout line
    (``@{ event='smoke-iter'; iter=..; timedOut=$true }``, no ``passed``/``verdict``) OR
    a result line (``@{ ...; passed=..; verdict=.. }``, no ``timedOut``). This builder
    keeps ``passed``/``verdict`` always present and only adds ``timedOut`` when supplied.

    ``verifiedAcs`` / ``deferredAcs`` (the ACs the agent drove in-browser vs deferred to the test
    suite, parsed from the optional ``SMOKE_ACS:`` evidence line) are likewise OMITTED when
    ``None`` — additive observability, so the golden corpus / gen_golden.ps1 are untouched (same
    omit-when-absent contract as ``timedOut``).
    """
    o: dict[str, Any] = {
        "event": "smoke-iter",
        "iter": iter_,
        "passed": passed,
        "verdict": verdict,
    }
    if timed_out is not None:
        o["timedOut"] = timed_out
    if verified is not None:
        o["verifiedAcs"] = list(verified)
    if deferred is not None:
        o["deferredAcs"] = list(deferred)
    return o


def pr_created_event(story: str, branch: str, base: str, url: str) -> dict[str, Any]:
    """BMAD ``pr-created`` event."""
    return {
        "event": "pr-created",
        "story": story,
        "branch": branch,
        "base": base,
        "url": url,
    }


def pr_merged_event(story: str, base: str, pr: str) -> dict[str, Any]:
    """BMAD ``pr-merged`` event."""
    return {"event": "pr-merged", "story": story, "base": base, "pr": pr}


def bmad_stop_event(ok: bool, reason: str, cum: float) -> dict[str, Any]:
    """BMAD ``stop`` event — ``Write-BLog @{ event="stop"; ok=..; reason=..; cum=.. }``.

    DISTINCT from the generic :func:`stop_event` (whose shape is
    ``{event, reason, green, iter, cum, bestPass}``). Both are kept on purpose: the BMAD
    adapter's ``stop`` carries ``ok`` and NO ``green``/``iter``/``bestPass``.
    """
    return {"event": "stop", "ok": ok, "reason": reason, "cum": cum}


def token_usage_event(
    phase: str,
    model: str,
    input_tokens: int,
    output_tokens: int,
    cache_read: int,
    cache_creation: int,
    hit_ratio: float,
    warm: bool,
    cost_usd: float,
    cum_input: int,
    cum_output: int,
    cum_cache_read: int,
    cum_cache_creation: int,
    story: str | None = None,
) -> dict[str, Any]:
    """Additive ``token-usage`` event — per-call token + cache telemetry for one agent run.

    NEW (no PowerShell equivalent): NOT part of the golden corpus and NOT added to
    ``gen_golden.ps1``; the reducer logs-but-ignores unknown events, so this is
    backward-compatible (same contract as :func:`metrics_event`).

    Rationale: on a Max **subscription** the binding constraint is TOKENS against the 5-hour /
    weekly windows, NOT dollars — and cache *reads* are far cheaper against that budget than
    fresh input. ``cum*`` is the USD ``cum`` field's missing counterpart: the running token draw
    so a watcher can see WHICH phase + WHICH model ate the window (the data is already in
    claude's ``--output-format json`` ``usage`` block; emitting it costs zero extra tokens).

    ``model`` is the resolved tier the run used (``""``/inherit is surfaced as ``"(inherit)"``).
    ``story`` is OMITTED when falsy (deciders / non-story calls).
    """
    o: dict[str, Any] = {
        "event": "token-usage",
        "phase": phase,
        "model": model or "(inherit)",
        "input": int(input_tokens),
        "output": int(output_tokens),
        "cacheRead": int(cache_read),
        "cacheCreation": int(cache_creation),
        "hitRatio": round(float(hit_ratio), 4),
        "warm": bool(warm),
        "costUsd": round(float(cost_usd), 6),
        "cumInput": int(cum_input),
        "cumOutput": int(cum_output),
        "cumCacheRead": int(cum_cache_read),
        "cumCacheCreation": int(cum_cache_creation),
    }
    if story:
        o["story"] = story
    return o
