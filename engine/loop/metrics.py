"""Run-quality metrics derived purely from the ``log.jsonl`` event stream.

Why not pass@k
--------------
``pass@k`` ("did at least one of k independent samples pass?") is the standard
code-generation benchmark metric, but it is MISLEADING for an iterative agent loop:
the loop is not k independent draws — it is one trajectory that edits, re-runs the
gate, and learns from feedback. pass@k rewards a loop that flails for many expensive
iterations exactly the same as one that nails it immediately. For agent loops we
report **first-try-green** (did the very first iteration reach green?) plus the COST
of getting there (iterations-to-green, dollars-to-green) and the **regression rate**
(how often the loop had to roll back). Those capture efficiency and stability, which
pass@k throws away.

This module is PURE: it folds a list of event dicts (the same shapes
:mod:`loop.events` builds and PROTOCOL.md §2 defines) into a metrics dict. It keys on
the ``event`` field and tolerates missing fields — a partial / truncated log never
raises, it just yields ``None`` for the metrics it cannot determine.

Inputs it reads (all optional per event):
* ``iter`` events: ``iter`` (index), ``cost``, ``cum``, ``pass``, ``total``,
  ``action`` (``stop`` | ``rollback`` | ``continue``).
* ``stop`` events: ``green``, ``iter``, ``cum`` (PROTOCOL §2 core ``stop``).
* ``gate`` events: ``green``, ``cum`` (used to detect a green on iter 1 even when the
  ``stop`` line omits the iter).
* ``rollback`` events: counted toward the regression rate.

Outputs:
* ``first_try_green`` (bool) — did iteration 1 reach green?
* ``iters_to_green`` (int|None) / ``cost_to_green`` (float|None) — the iter index and
  cumulative cost at the green stop, else ``None``.
* ``rollbacks`` (int), ``regression_rate`` (float = rollbacks / total_iters),
  ``final_green`` (bool), ``total_iters`` (int), ``total_cost`` (float = max ``cum``).
"""

from __future__ import annotations

from typing import Any


def _num(value: Any) -> float | None:
    """Coerce to float, tolerating None / non-numeric -> None (never raises)."""
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _int(value: Any) -> int | None:
    """Coerce to int, tolerating None / non-numeric -> None (never raises)."""
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def compute_metrics(events: list[dict[str, Any]] | None) -> dict[str, Any]:
    """Fold an event list into run-quality metrics (see module docstring).

    Pure and total: an empty / ``None`` list yields the all-zero / ``None`` baseline.
    The green moment is taken from the FIRST green-marking event encountered (a
    ``stop`` with ``green: true``, or failing that a ``gate`` with ``green: true``),
    so a loop that stops green is measured at the iteration it actually went green.
    """
    events = events or []

    total_iters = 0
    rollbacks = 0
    total_cost = 0.0

    green_iter: int | None = None
    green_cost: float | None = None
    final_green = False

    # Track the running cum + the latest iter index so a green-marking event that
    # omits its own iter / cum can borrow the most recent iteration's values.
    last_iter_index: int | None = None
    cum_at_last_iter: float | None = None

    for ev in events:
        if not isinstance(ev, dict):
            continue
        kind = ev.get("event")

        cum = _num(ev.get("cum"))
        if cum is not None and cum > total_cost:
            total_cost = cum

        if kind == "iter":
            total_iters += 1
            idx = _int(ev.get("iter"))
            if idx is not None:
                last_iter_index = idx
            if cum is not None:
                cum_at_last_iter = cum
            # An iter line may itself declare the rollback action.
            if ev.get("action") == "rollback":
                rollbacks += 1

        elif kind == "rollback":
            rollbacks += 1

        elif kind == "stop":
            # ``stop`` is the authoritative end-of-run signal (PROTOCOL §2 core).
            if ev.get("green"):
                final_green = True
                if green_iter is None:
                    green_iter = _int(ev.get("iter"))
                    if green_iter is None:
                        green_iter = last_iter_index
                    gc = _num(ev.get("cum"))
                    green_cost = gc if gc is not None else cum_at_last_iter

        elif kind == "gate":
            # A green gate marks the green moment even if the stop line is terse.
            if ev.get("green") and green_iter is None:
                green_iter = last_iter_index
                green_cost = cum if cum is not None else cum_at_last_iter

    first_try_green = green_iter == 1
    iters_to_green = green_iter
    cost_to_green = green_cost if green_iter is not None else None
    regression_rate = (rollbacks / total_iters) if total_iters else 0.0

    return {
        "first_try_green": first_try_green,
        "iters_to_green": iters_to_green,
        "cost_to_green": cost_to_green,
        "rollbacks": rollbacks,
        "regression_rate": regression_rate,
        "final_green": final_green,
        "total_iters": total_iters,
        "total_cost": total_cost,
    }
