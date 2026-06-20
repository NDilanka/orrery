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
