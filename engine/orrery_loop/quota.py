"""Quota cores — verbatim ports of ``Resolve-QuotaStatus``, ``Test-QuotaLimitedText``
and ``Get-QuotaWaitSec`` (loopcore.ps1 ~lines 394-468).

Pure, deterministic, no I/O, no claude, no real sleep. Given the combined stdout+stderr
text of a stream-json probe, decide whether quota is LIMITED and, if so, which reset
moment to sleep to. The reset-window precedence and the limit-phrase regex are preserved
exactly from the PowerShell source so the Python engine waits identically.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone

# Strip ANSI SGR sequences (ESC [ ... m) — matches the PS `\x1b\[[0-9;]*m` replace.
_ANSI_RX = re.compile(r"\x1b\[[0-9;]*m")

# Every `"rate_limit_info": { ... }` fragment (non-nested, single-level braces), as the
# PowerShell `'"rate_limit_info"\s*:\s*\{[^}]*\}'` regex matches.
_FRAGMENT_RX = re.compile(r'"rate_limit_info"\s*:\s*\{[^}]*\}')

_STATUS_RX = re.compile(r'"status"\s*:\s*"([^"]+)"')
_RESETS_RX = re.compile(r'"resetsAt"\s*:\s*(\d+)')
_TYPE_RX = re.compile(r'"rateLimitType"\s*:\s*"([^"]+)"')

# A fragment's status that means the window was REJECTED (case-insensitive substring).
_REJECT_RX = re.compile(r"reject|block|exceed|limited", re.IGNORECASE)

# STRONG limit phrases only — real limit text + the API error type, but NOT the benign
# 'rate_limit_info'/'rate_limit_event' stream-json field names. Ported verbatim.
_STRONG_RX = re.compile(
    r"usage limit|limit will reset|reset[s]?\s+(at|in)\b|reset[s]?\s+\d|"
    r"\d\s*-?\s*hour limit|too many requests|\b429\b|overloaded|\b529\b|"
    r"rate[ -]limit|rate_limit_error",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class QuotaStatus:
    """Result of :func:`resolve_quota_status`."""

    limited: bool
    reset_at: datetime | None
    reset_type: str | None


def _from_unix(secs: int) -> datetime:
    """Unix seconds -> local-naive datetime.

    Mirrors the PowerShell ``[DateTimeOffset]::FromUnixTimeSeconds(..).LocalDateTime``:
    the wall-clock time in the machine's local zone, as a naive datetime (no tzinfo) so it
    composes with an injected naive ``now`` the way the PS ``$Now`` does.
    """
    return datetime.fromtimestamp(secs, tz=timezone.utc).astimezone().replace(tzinfo=None)


def test_quota_limited_text(text: str | None) -> bool:
    """Port of ``Test-QuotaLimitedText``.

    True only for STRONG limit phrasing (HTTP 429/529, "usage limit", "rate-limit",
    "rate_limit_error", etc.). Does NOT trip on the benign ``rate_limit_info`` /
    ``rate_limit_event`` stream-json field names (no underscore form in the regex).
    """
    if not text:
        return False
    return bool(_STRONG_RX.search(text))


def resolve_quota_status(text: str | None) -> QuotaStatus:
    """Port of ``Resolve-QuotaStatus`` (loopcore.ps1 ~394-437).

    Strip ANSI first, then scan every ``"rate_limit_info": { ... }`` fragment. A fragment
    is REJECTED when its ``status`` matches ``reject|block|exceed|limited``. The sleep
    target prefers the REJECTED window's ``resetsAt`` (which may be five_hour OR weekly —
    so a weekly limit waits for the weekly reset), else the five_hour reset, else None.
    Falls back to :func:`test_quota_limited_text` to set ``limited`` when no fragment marks
    it limited.
    """
    all_text = _ANSI_RX.sub("", text) if text is not None else ""
    limited = False
    reject_reset: datetime | None = None
    reject_type: str | None = None
    five_hour_reset: datetime | None = None

    for m in _FRAGMENT_RX.finditer(all_text):
        frag = m.group(0)
        st_m = _STATUS_RX.search(frag)
        st = st_m.group(1) if st_m else ""
        rs_m = _RESETS_RX.search(frag)
        rs = _from_unix(int(rs_m.group(1))) if rs_m else None
        rt_m = _TYPE_RX.search(frag)
        rt = rt_m.group(1) if rt_m else None
        if _REJECT_RX.search(st):
            limited = True
            if rs is not None:
                reject_reset = rs
                reject_type = rt
        if rt == "five_hour" and rs is not None:
            five_hour_reset = rs

    # Fallback: hard limit phrasing still flags limited even with no fragment present.
    if not limited and test_quota_limited_text(all_text):
        limited = True

    if reject_reset is not None:
        return QuotaStatus(limited=limited, reset_at=reject_reset, reset_type=reject_type)
    if five_hour_reset is not None:
        return QuotaStatus(limited=limited, reset_at=five_hour_reset, reset_type="five_hour")
    return QuotaStatus(limited=limited, reset_at=None, reset_type=None)


def get_quota_wait_sec(
    reset_at: datetime | None,
    default_wait_min: int = 30,
    now: datetime | None = None,
    buffer_sec: int = 120,
    min_sec: int = 60,
    max_sec: int = 21600,
) -> int:
    """Port of ``Get-QuotaWaitSec`` (loopcore.ps1 ~449-468).

    When a concrete reset moment is known, sleep until reset + ``buffer_sec``, clamped to
    ``[min_sec, max_sec]`` (default 1m..6h — a single cycle never sleeps more than 6h).
    When no reset is known, fall back to ``default_wait_min`` minutes. ``now`` is injectable
    so tests are deterministic (defaults to the local wall clock, matching PS ``Get-Date``).
    """
    if reset_at is not None:
        if now is None:
            now = datetime.now()
        # int((reset_at - now).TotalSeconds) — truncates toward zero, then + buffer.
        sec = int((reset_at - now).total_seconds()) + buffer_sec
        return min(max(sec, min_sec), max_sec)
    return default_wait_min * 60


# ---------------------------------------------------------------------------
# survive — the wait-and-resume DRIVER (port of bmad-loop.ps1 Wait-ForQuota, ~120-137)
# ---------------------------------------------------------------------------


def survive(
    runner,
    *,
    label,
    cum,
    emit,
    sleep,
    default_wait_min: int = 30,
    max_waits: int = 30,
    blind_waits: int = 0,
    max_blind_waits: int = 4,
    now_fn=None,
) -> bool:
    """Wait-and-resume around a quota limit; return True when the runner is usable again.

    A faithful port of ``Wait-ForQuota``. Call this the moment a run looks quota-limited.
    The event sequence mirrors the PowerShell harness: ``quota-hit`` (once, on entry) ->
    ``quota-wait`` (per sleep cycle) -> ``quota-resume`` (when the probe clears).

    Two paths, chosen by ``runner.supports_quota_probe``:

    - **Probing backend** (e.g. claude): loop up to ``max_waits`` cycles. Each cycle calls
      ``runner.probe_quota()`` FIRST — a false positive then costs no sleep. When the probe is
      NOT limited, emit ``quota_resume_event`` and return True. When it IS limited, compute the
      wait via :func:`get_quota_wait_sec` (using the probe's ``reset_at``), emit
      ``quota_wait_event``, ``sleep`` it, and probe again. Exhausting ``max_waits`` without
      clearing returns False (the caller should give up / hand off).

    - **Non-probing backend**: it can't tell us when quota frees up, so emit a single fallback
      ``quota_wait_event`` (no reset known), sleep ``default_wait_min`` minutes, and return True
      so the caller simply re-attempts the run. To stop a PERSISTENT rate limit from making the
      caller re-attempt forever (bypassing max_iters / cost ceilings — there is no probe to ever
      clear it), these fallback waits are BOUNDED: ``blind_waits`` is the count of consecutive
      fallback waits already spent in this retry sequence, and once it reaches ``max_blind_waits``
      survive returns False WITHOUT waiting, so the caller falls into its normal quota-failure
      path. The caller owns the counter (incrementing it after each fallback wait, resetting it
      when a run succeeds); a fresh retry sequence starts at ``blind_waits=0``.

    ``emit`` (event sink), ``sleep`` (seconds -> None) and ``now_fn`` (-> datetime) are injected
    so tests are deterministic — no real clock, no real sleep, no real ``claude``.
    """
    # Local import keeps this addition strictly append-only (no edit to the module header) and
    # avoids any import cycle with orrery_loop.events.
    from orrery_loop.events import quota_hit_event, quota_resume_event, quota_wait_event

    if now_fn is None:
        now_fn = datetime.now

    # quota-hit fires once, at the moment the limit was detected (before any wait). For a
    # probing backend we surface the reset moment from a first probe so the hit event carries
    # the same resetAt the wait events will sleep to.
    if runner.supports_quota_probe:
        first = runner.probe_quota()
        emit(quota_hit_event(label, cum, getattr(first, "reset_at", None)))

        probe = 1
        status = first
        while probe <= max_waits:
            if not status.limited:
                emit(quota_resume_event(label, probe))
                return True
            wait = get_quota_wait_sec(status.reset_at, default_wait_min, now=now_fn())
            emit(
                quota_wait_event(
                    label,
                    cum,
                    wait,
                    probe,
                    reset_type=status.reset_type,
                    now=now_fn(),
                )
            )
            sleep(wait)
            probe += 1
            if probe > max_waits:
                break
            status = runner.probe_quota()
        return False

    # Non-probing backend: one bounded fallback wait, then let the caller re-attempt. Once the
    # caller has already spent max_blind_waits consecutive fallback waits, a persistent limit is
    # no longer worth blindly re-waiting on — give up (return False) so the caller's quota-failure
    # path runs instead of looping forever past max_iters / cost ceilings.
    if blind_waits >= max_blind_waits:
        return False
    emit(quota_hit_event(label, cum, None))
    wait = default_wait_min * 60
    emit(quota_wait_event(label, cum, wait, blind_waits + 1, reset_type=None, now=now_fn()))
    sleep(wait)
    return True
