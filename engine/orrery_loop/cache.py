"""Cache-usage parser — verbatim port of ``Get-CacheUsage`` (loopcore.ps1 ~708-764).

Pure parser. Given a claude result object's ``usage`` block (a dict, a raw JSON string, or
the whole result object with a nested ``usage``), pull the cache token counters and compute
the cache HIT RATIO and WARM flag. Tolerates the absence of every field (older claude
builds, or text-format results, emit no cache counters) — a missing counter is treated as
0, yielding ``hit_ratio=0`` / ``warm=False`` (a cold/unknown call). No I/O, no claude.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class CacheUsage:
    """Result of :func:`get_cache_usage`."""

    cache_read: int
    cache_creation: int
    input: int
    hit_ratio: float
    warm: bool


def _get(obj: Any, names: tuple[str, ...]) -> int:
    """First present, numeric-parseable field among ``names`` -> int, else 0.

    Mirrors the PowerShell ``$get`` scriptblock: a value is accepted only if it parses as a
    number (``[double]::TryParse``), then truncated to int. Accepts dict-like objects.
    """
    if not obj:
        return 0
    for n in names:
        if isinstance(obj, dict):
            if n not in obj:
                continue
            p = obj[n]
        else:
            if not hasattr(obj, n):
                continue
            p = getattr(obj, n)
        if p is None:
            continue
        try:
            return int(float(p))
        except (TypeError, ValueError):
            continue
    return 0


def get_cache_usage(usage: Any) -> CacheUsage:
    """Port of ``Get-CacheUsage``.

    ``usage`` may be a dict, a raw JSON string (parsed, unparseable -> treated as None), or
    a full result object carrying a nested ``usage`` (automatically unwrapped). Accepts both
    snake_case (``cache_read_input_tokens`` / ``cache_creation_input_tokens`` /
    ``input_tokens``) and camelCase variants.

    ``hit_ratio = round(cache_read / (cache_read + input), 4)`` (0 when the denominator is
    0 — no divide); ``warm = cache_read > 0``.
    """
    u = usage
    if isinstance(u, str):
        try:
            u = json.loads(u)
        except (ValueError, TypeError):
            u = None

    # Unwrap a full result object that carries a nested `usage`.
    if isinstance(u, dict) and u.get("usage"):
        u = u["usage"]
    elif u is not None and not isinstance(u, dict) and getattr(u, "usage", None):
        u = u.usage

    cache_read = _get(u, ("cache_read_input_tokens", "cacheReadInputTokens"))
    cache_creation = _get(u, ("cache_creation_input_tokens", "cacheCreationInputTokens"))
    input_tokens = _get(u, ("input_tokens", "inputTokens"))

    denom = cache_read + input_tokens
    hit_ratio = round(cache_read / float(denom), 4) if denom > 0 else 0.0
    warm = cache_read > 0

    return CacheUsage(
        cache_read=cache_read,
        cache_creation=cache_creation,
        input=input_tokens,
        hit_ratio=hit_ratio,
        warm=warm,
    )


def total_tokens(usage: Any) -> int:
    """Total tokens a single agent run processed: input + output + cache_read + cache_creation.

    Same tolerant parsing as :func:`get_cache_usage` (dict / raw JSON string / a full result
    object with a nested ``usage``; snake_case or camelCase; a missing counter -> 0). Used by
    the generic loop's optional token-budget ceiling (``stop.tokenCeiling``) — the subscription-era
    analogue of the USD cost ceiling, since on a flat-rate plan the binding constraint is tokens,
    not dollars. Counts EVERY token the model saw (the conservative, spend-soonest reading);
    a run with no ``usage`` block yields 0. No I/O, no claude.
    """
    u = usage
    if isinstance(u, str):
        try:
            u = json.loads(u)
        except (ValueError, TypeError):
            u = None
    if isinstance(u, dict) and u.get("usage"):
        u = u["usage"]
    elif u is not None and not isinstance(u, dict) and getattr(u, "usage", None):
        u = u.usage
    return (
        _get(u, ("input_tokens", "inputTokens"))
        + _get(u, ("output_tokens", "outputTokens"))
        + _get(u, ("cache_read_input_tokens", "cacheReadInputTokens"))
        + _get(u, ("cache_creation_input_tokens", "cacheCreationInputTokens"))
    )
