"""Loop decision cores — verbatim ports of ``Get-LoopDecision`` and ``Update-ConsecutiveFail``.

Pure, deterministic, no I/O. The priority order and exact reason strings are preserved
from ``loopcore.ps1`` so the Python engine decides identically to the PowerShell one.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Decision:
    """Result of :func:`decide`. ``action`` is one of ``stop`` | ``rollback`` | ``continue``."""

    action: str
    green: bool
    reason: str


def decide(
    green: bool,
    tampered: bool,
    count_dropped: bool,
    blocked: bool,
    pass_: int,
    best_pass: int,
    changed: bool,
    regress_count: int,
    regress_limit: int,
    plateau: int,
    plateau_limit: int,
    stale: int,
    stagnation_limit: int,
    cum: float,
    ceiling: float,
    iter: int,
    max_iters: int,
    verifier_refuted: bool = False,
) -> Decision:
    """Verbatim port of ``Get-LoopDecision`` (loopcore.ps1 ~lines 331-383).

    Priority order matters — integrity checks beat success; success beats spend:
    tamper -> count-dropped -> (green AND NOT verifier_refuted) -> blocked ->
    cost-ceiling -> regression (rollback, or handoff at regress_limit) -> stagnation ->
    plateau -> max-iters -> continue.

    ``verifier_refuted`` defaults False so default behavior is unchanged: a gate-green the
    independent verifier refuted is NOT a real stop-green.
    """
    # Integrity checks beat success; success beats spend.
    if tampered:
        return Decision("stop", False, "test files were modified (tamper)")
    if count_dropped:
        return Decision("stop", False, "test count dropped (deleted/skipped tests)")
    # Anti-false-green: a gate-green that the independent verifier REFUTED is not a real
    # stop-green. Suppress the green-stop and fall through.
    if green and not verifier_refuted:
        return Decision("stop", True, f"all tests green at iter {iter}")
    if blocked:
        return Decision("stop", False, "agent reported BLOCKED")
    if cum >= ceiling:
        return Decision("stop", False, f"cost ceiling ${ceiling} reached")

    if pass_ < best_pass:  # regression = silent drift
        if (regress_count + 1) >= regress_limit:
            return Decision(
                "stop",
                False,
                f"repeated regressions ({regress_count + 1}/{regress_limit}) — handoff",
            )
        return Decision(
            "rollback",
            False,
            f"regression ({pass_} < best {best_pass}) — rolling back to best",
        )

    if not changed and stale >= stagnation_limit:
        return Decision("stop", False, f"stagnation: {stale} iters with no change")
    if changed and pass_ == best_pass and plateau >= plateau_limit:
        return Decision(
            "stop", False, f"plateau: {plateau} iters changed with no net progress"
        )
    if iter >= max_iters:
        return Decision(
            "stop", False, f"max iterations ({max_iters}) reached without green"
        )
    return Decision("continue", False, "advance")


def floor_breach(pass_count: int, floor: int) -> bool:
    """Pure regression-floor decision: has ``pass_count`` dropped below ``floor``?

    The ONE place expressing "did the passing-test count drop" — BMAD's dev-gate, post-review,
    and post-smoke phase boundaries (:mod:`orrery_loop.bmad.driver`) each independently reimplemented
    this exact ``<`` check before this helper existed, one per boundary. Kept deliberately
    trivial: the point isn't the arithmetic, it's having ONE definition every call site shares,
    so a future refinement (e.g. a tolerance) changes in one place instead of three.
    """
    return pass_count < floor


@dataclass(frozen=True)
class FailState:
    """Result of :func:`update_consecutive_fail`.

    ``recover`` and ``handoff`` are mutually exclusive.
    """

    count: int
    recovered: bool
    recover: bool
    handoff: bool
    reason: str


def update_consecutive_fail(
    green: bool,
    made_progress: bool,
    count: int = 0,
    recovered: bool = False,
    limit: int = 3,
) -> FailState:
    """Verbatim port of ``Update-ConsecutiveFail`` (loopcore.ps1 ~lines 641-696).

    Counts consecutive non-green iterations that made NO net progress. A green OR a
    progress iter RESETS the streak (and re-arms the one-shot recover token). After
    ``limit`` such iters we spend the ONE recover attempt; if already recovered and still
    failing, we hand off. ``recover`` and ``handoff`` are mutually exclusive.
    """
    # A good iteration (green or net progress) clears the streak entirely.
    if green or made_progress:
        return FailState(count=0, recovered=False, recover=False, handoff=False, reason="")

    count = count + 1
    recover = False
    handoff = False
    reason = ""

    if count >= limit:
        if not recovered:
            # First time we hit the limit: spend the ONE recover attempt.
            recover = True
            recovered = True
            reason = f"consecutive no-progress failures ({count}/{limit}) — recover-once"
        else:
            # Recover already spent and we are STILL failing -> hand off.
            handoff = True
            reason = f"consecutive no-progress failures ({count}) after recover — handoff"

    return FailState(
        count=count,
        recovered=recovered,
        recover=recover,
        handoff=handoff,
        reason=reason,
    )
