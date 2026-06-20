"""Cost-alert core — verbatim port of ``Update-CostAlert`` (loopcore.ps1 ~183-212).

Pure cost-threshold tracker. Given cumulative spend, a ceiling, and the set of thresholds
already fired, return any NEW thresholds crossed (ascending) plus the updated fired-set.
Fires each of 50/80/100 exactly once, in order, only when the ceiling is positive. No I/O —
the caller prints/logs the returned alerts.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass


@dataclass(frozen=True)
class CostAlert:
    """Result of :func:`update_cost_alert`.

    ``newly`` are thresholds crossed for the first time on this call (ascending).
    ``fired`` is the cumulative fired-set after this call (ascending).
    """

    newly: list[int]
    fired: list[int]


def update_cost_alert(
    cum: float,
    ceiling: float,
    thresholds: Iterable[int] = (50, 80, 100),
    fired: Iterable[int] = (),
) -> CostAlert:
    """Port of ``Update-CostAlert``.

    Computes ``pct = cum / ceiling * 100`` (only when ``ceiling > 0``) and fires every
    threshold ``<= pct`` not already in ``fired``, in ascending order. A jump past several
    thresholds in one call fires all of them at once. A non-positive ceiling fires nothing.
    """
    newly: list[int] = []
    fired_set = set(fired)

    if ceiling > 0:
        pct = (cum / ceiling) * 100.0
        for t in sorted(thresholds):
            if pct >= t and t not in fired_set:
                newly.append(t)
                fired_set.add(t)

    return CostAlert(newly=newly, fired=sorted(fired_set))
