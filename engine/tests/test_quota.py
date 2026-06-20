"""Quota parser/arithmetic parity — mirrors selftest-resume.ps1 (section a).

Deterministic: a fixed clock, INJECTED rate_limit_info fragment strings (captured shapes),
no real sleep, no claude. Exercises the highest-risk branch — the REJECTED-window reset
precedence (a rejected weekly fragment wins the sleep target over a present five_hour one).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from loop.quota import (
    get_quota_wait_sec,
    resolve_quota_status,
    test_quota_limited_text as is_quota_limited_text,
)

# Fixed clock so every time computation is deterministic (no real now()).
NOW = datetime(2026, 6, 19, 10, 0, 0)


def epoch(dt: datetime) -> int:
    """Local-naive datetime -> unix seconds, matching selftest-resume.ps1's Epoch helper
    (``DateTimeOffset(dt, TimeSpan.Zero)`` treats the naive wall-clock as UTC)."""
    return int(dt.replace(tzinfo=timezone.utc).timestamp())


def from_epoch(e: int) -> datetime:
    """Round-trip the SAME way resolve_quota_status does, so expected values are
    timezone-correct on any machine (FromUnixTimeSeconds(..).LocalDateTime, naive)."""
    return datetime.fromtimestamp(e, tz=timezone.utc).astimezone().replace(tzinfo=None)


def rli(status: str, resets_at: int | None, rli_type: str | None) -> str:
    """Build a captured stream-json ``rate_limit_info`` fragment exactly as claude emits it
    (mirrors the RLI helper in selftest-resume.ps1)."""
    parts = [f'"status": "{status}"']
    if resets_at is not None:
        parts.append(f'"resetsAt": {resets_at}')
    if rli_type:
        parts.append(f'"rateLimitType": "{rli_type}"')
    return '{ "type":"system", "rate_limit_info": { ' + ", ".join(parts) + " } }"


# --- Test-QuotaLimitedText: strong phrases only ---------------------------


def test_strong_phrases_flag_limited():
    assert is_quota_limited_text("API error: 429 too many requests") is True
    assert is_quota_limited_text("usage limit reached") is True
    assert is_quota_limited_text("rate_limit_error") is True
    assert is_quota_limited_text("HTTP 529 overloaded") is True


def test_benign_stream_json_field_names_not_limited():
    # The benign stream-json field names must NOT trip the strong matcher.
    assert is_quota_limited_text('{"rate_limit_info": {"status":"allowed"}}') is False
    assert is_quota_limited_text('{"rate_limit_event": {}}') is False


def test_empty_or_none_not_limited():
    assert is_quota_limited_text("") is False
    assert is_quota_limited_text(None) is False


# --- resolve_quota_status: window selection -------------------------------


def test_allowed_status_not_limited():
    s = resolve_quota_status(rli("allowed", None, "five_hour"))
    assert s.limited is False
    assert s.reset_at is None
    assert s.reset_type is None


def test_rejected_five_hour_window():
    five_at = NOW + timedelta(minutes=45)
    e = epoch(five_at)
    s = resolve_quota_status(rli("rejected", e, "five_hour"))
    assert s.limited is True
    assert s.reset_type == "five_hour"
    assert s.reset_at == from_epoch(e)


def test_rejected_weekly_wins_over_present_five_hour():
    # An ALLOWED five_hour + a REJECTED weekly: the rejected weekly window must win the
    # sleep target so a weekly limit waits for the WEEKLY reset, not the nearer five_hour.
    five_at = NOW + timedelta(minutes=45)
    week_at = NOW + timedelta(days=3)
    five_e, week_e = epoch(five_at), epoch(week_at)
    text = rli("allowed", five_e, "five_hour") + "\n" + rli("rejected", week_e, "weekly")
    s = resolve_quota_status(text)
    assert s.limited is True
    assert s.reset_type == "weekly"
    assert s.reset_at == from_epoch(week_e)


def test_limited_but_no_reset_time():
    s = resolve_quota_status(rli("rejected", None, None))
    assert s.limited is True
    assert s.reset_at is None


def test_ansi_wrapped_input_still_parses():
    five_at = NOW + timedelta(minutes=45)
    e = epoch(five_at)
    frag = rli("rejected", e, "five_hour")
    wrapped = f"\x1b[31m{frag}\x1b[0m"
    s = resolve_quota_status(wrapped)
    assert s.limited is True
    assert s.reset_type == "five_hour"
    assert s.reset_at == from_epoch(e)


def test_benign_allowed_fragment_not_limited():
    s = resolve_quota_status(rli("allowed", None, "five_hour"))
    assert s.limited is False


def test_http_429_plain_text_limited_no_reset():
    s = resolve_quota_status("API error: 429 too many requests; usage limit reached")
    assert s.limited is True
    assert s.reset_at is None
    assert s.reset_type is None


# --- get_quota_wait_sec: buffer + clamps ----------------------------------


def test_wait_sec_five_hour_applies_buffer():
    five_at = NOW + timedelta(minutes=45)
    assert get_quota_wait_sec(five_at, default_wait_min=30, now=NOW) == 45 * 60 + 120


def test_wait_sec_weekly_clamps_to_6h_ceiling():
    week_at = NOW + timedelta(days=3)
    assert get_quota_wait_sec(week_at, default_wait_min=30, now=NOW) == 21600


def test_wait_sec_no_reset_falls_back_to_default():
    assert get_quota_wait_sec(None, default_wait_min=30, now=NOW) == 30 * 60


def test_wait_sec_past_reset_clamps_to_60_floor():
    past = NOW - timedelta(minutes=10)
    assert get_quota_wait_sec(past, default_wait_min=30, now=NOW) == 60
