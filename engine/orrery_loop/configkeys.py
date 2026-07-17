"""Shared config-loading helpers: camel/snake key resolution + unknown-key warnings.

Every ``*_from`` / ``from_loop_json`` parser in this codebase hand-rolled the same "accept
either camelCase or snake_case" fallback: :mod:`orrery_loop.config` had ``_first``, ``BmadConfig
.from_loop_json`` had its own inline key-pair loop, and ``QaConfig.from_loop_json`` used raw
``dict.get`` chains. All three now share ONE implementation (:func:`resolve`) — a plain
generalization of the original ``orrery_loop.config._first``, moved here so it isn't private to one
module.

:func:`warn_unknown_keys` closes a second, separate gap: every parser above silently DROPPED
any key it didn't recognize — a typo'd ``gateStagess`` or a field from a newer/older schema
version just vanished with no signal. Wiring this into a config block's parser prints a
one-line stderr warning instead, without ever raising or blocking the parse (config loading
must stay lenient — a typo should be loud, not fatal).
"""

from __future__ import annotations

import sys
from typing import Any, Iterable

__all__ = ["resolve", "coerce_float", "coerce_int", "warn_unknown_keys"]


def resolve(d: dict[str, Any], *keys: str, default: Any = None) -> Any:
    """First present key's value among ``keys`` (camel/snake tolerance); else ``default``.

    Mirrors the JSON-key resolution every config parser needs: try each spelling in the given
    order (typically camelCase first, snake_case second — callers may pass either order) and
    return the first one present with a non-``None`` value.
    """
    for k in keys:
        if k in d and d[k] is not None:
            return d[k]
    return default


def _coerce(value: Any, default: Any, where: str, key: str, cast) -> Any:
    """``cast(value)`` — but on a non-numeric value, WARN (stderr) and keep ``default``.

    Config loading must stay lenient: a bad ``ceilingUsd`` / ``maxIters`` in ``loop.json`` (a
    stray string, a null, a typo like ``"3.o"``) should be LOUD, not fatal. A raised
    ``ValueError``/``TypeError`` from ``cast`` is swallowed into a one-line advisory (same style
    as :func:`warn_unknown_keys`) and the field falls back to its default instead of crashing
    the whole load.
    """
    try:
        return cast(value)
    except (ValueError, TypeError):
        print(
            f"[orrery_loop.config] '{where}': '{key}'={value!r} is not a valid "
            f"{cast.__name__}; using default {default!r}.",
            file=sys.stderr,
        )
        return default


def coerce_float(value: Any, default: float, where: str, key: str) -> float:
    """``float(value)`` with a lenient warn-and-default fallback (see :func:`_coerce`)."""
    return _coerce(value, default, where, key, float)


def coerce_int(value: Any, default: int, where: str, key: str) -> int:
    """``int(value)`` with a lenient warn-and-default fallback (see :func:`_coerce`)."""
    return _coerce(value, default, where, key, int)


def warn_unknown_keys(
    block: dict[str, Any],
    known: Iterable[str],
    where: str,
    *,
    retired: Iterable[str] = (),
) -> None:
    """Print a one-line stderr warning for any key in ``block`` not in ``known``/``retired``.

    ``known`` should list EVERY accepted spelling (both camelCase and snake_case) so a
    legitimate key never warns. ``retired`` keys (e.g. the removed ``gate.greenWhen`` — see
    PROTOCOL.md §7) get a gentler "no longer has any effect" message instead of "unrecognized",
    so an old config carrying a field this engine version dropped isn't mistaken for a typo.

    Best-effort / advisory only: never raises, never blocks parsing, and is a no-op when
    ``block`` isn't a dict (the caller's own ``or {}`` / type-check already handles absence).
    """
    if not isinstance(block, dict):
        return
    known_set = set(known)
    retired_set = set(retired)
    for key in block:
        if key in known_set:
            continue
        if key in retired_set:
            print(
                f"[orrery_loop.config] '{where}': '{key}' is retired and no longer has any effect.",
                file=sys.stderr,
            )
        else:
            print(
                f"[orrery_loop.config] '{where}': unrecognized key '{key}' (typo? it is ignored).",
                file=sys.stderr,
            )
