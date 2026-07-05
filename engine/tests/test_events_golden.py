"""Golden parity test for ``orrery_loop.events`` builders.

Loads ``fixtures/golden_events.jsonl`` (generated from the authoritative PowerShell
``loopcore.ps1`` via ``gen_golden.ps1``) and, for each case, calls the matching Python
builder with the SAME literal args + same fixed datetime, then asserts parity.

Comparison strategy:
  - The SET OF KEYS must match exactly (catches null-vs-omitted bugs).
  - Non-timestamp fields compared by exact equality.
  - Timestamp-valued fields (resumeAt, resetAt, updatedAt) compared by parsed instant
    (parse both, assert equal moment) to avoid sub-format brittleness.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from orrery_loop import events

FIXTURE = Path(__file__).parent / "fixtures" / "golden_events.jsonl"

# Fixed datetimes mirroring gen_golden.ps1.
FIXED_NOW = datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc)
FIXED_RESET = datetime(2026, 1, 2, 9, 0, 0, tzinfo=timezone.utc)

# Timestamp-valued fields compared by parsed instant, not by text.
TS_FIELDS = {"resumeAt", "resetAt", "updatedAt"}

# Arg table mirroring gen_golden.ps1: case-id -> callable producing the Python event dict.
BUILDERS: dict[str, callable] = {
    "iter": lambda: events.iter_event(
        iter=4, cost=0.12, cum=1.5, pass_=8, total=10, best=8, changed=True,
        stale=0, plateau=1, regress=0, action="continue", reason="advance",
    ),
    "stop": lambda: events.stop_event(
        reason="all tests green at iter 4", green=True, iter=4, cum=1.5, best_pass=8
    ),
    "parse_error": lambda: events.parse_error_event(iter=4),
    "gate": lambda: events.gate_event(
        story="S1", cum=1.5, green=True, pass_=8, fail=0, total=8,
        baseline_pass=6, stages=[{"name": "test", "ok": True, "exit": 0}],
    ),
    "model": lambda: events.model_event(phase="execute", model="sonnet", cost_per_turn=0.05),
    "cost_alert": lambda: events.cost_alert_event(pct=80, cum=2.4, ceiling=3.0),
    "verdict": lambda: events.verdict_event(
        item="roman", pass_=False,
        failing_criteria=["handles 0", "rejects negatives"],
        evidence="two cases fail", next_action="fix zero handling", model="haiku",
    ),
    "cache": lambda: events.cache_event(hit_ratio=0.123456, warm=True),
    "plateau": lambda: events.plateau_event(item="roman", k=3),
    "rollback": lambda: events.rollback_event(
        item="roman", to_iter=7, best_pass=8, strike=2, strike_budget=3
    ),
    "handoff": lambda: events.handoff_event(
        item="roman", reason="strikes exhausted", consecutive=3
    ),
    "phase_timeout": lambda: events.phase_timeout_event(label="iter 4", timeout_sec=600),
    "quota_hit_with_reset": lambda: events.quota_hit_event(
        label="dev", cum=12.5, reset_at=FIXED_RESET
    ),
    "quota_hit_without_reset": lambda: events.quota_hit_event(label="dev", cum=12.5),
    "quota_wait": lambda: events.quota_wait_event(
        label="dev", cum=12.5, wait_sec=3600, probe=2, reset_type="five_hour", now=FIXED_NOW
    ),
    "quota_wait_no_type": lambda: events.quota_wait_event(
        label="dev", cum=12.5, wait_sec=1800, probe=1, now=FIXED_NOW
    ),
    "quota_resume": lambda: events.quota_resume_event(label="dev", probe=3),
    "cooperative_stop": lambda: events.cooperative_stop_event(
        scope="story", mode="phase", stage="dev-story", story="S1", branch="feat/x", cum=12.5
    ),
    "review_question_with_story": lambda: events.review_question_event(
        turn=1, q="Use UTC?", story="S1"
    ),
    "review_question_without_story": lambda: events.review_question_event(
        turn=2, q="Drop seconds?"
    ),
    "review_answer": lambda: events.review_answer_event(turn=1, a="Yes, UTC."),
    "checkpoint": lambda: events.new_checkpoint(
        stage="dev-story", story="S1", branch="feat/x", merge_base="abc123",
        cum_usd=12.34567, resume="pwsh -File loop.ps1 -Resume", updated_at=FIXED_NOW,
    ),
}


def _load_golden() -> dict[str, dict]:
    cases: dict[str, dict] = {}
    for line in FIXTURE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        rec = json.loads(line)
        cases[rec["case"]] = rec["expected"]
    return cases


def _parse_instant(v):
    # PowerShell 'o' format: 2026-01-02T09:00:00.0000000Z. Python's fromisoformat handles
    # 'Z' and fractional seconds on 3.11; normalize 'Z' -> '+00:00' for safety.
    s = str(v).replace("Z", "+00:00")
    return datetime.fromisoformat(s)


GOLDEN = _load_golden()


def test_all_cases_have_builders():
    missing = set(GOLDEN) - set(BUILDERS)
    assert not missing, f"golden cases with no Python builder: {sorted(missing)}"


@pytest.mark.parametrize("case", sorted(GOLDEN))
def test_event_parity(case):
    expected = GOLDEN[case]
    got = BUILDERS[case]()

    # 1. Key set must match exactly (catches null-vs-omitted bugs).
    assert set(got.keys()) == set(expected.keys()), (
        f"key mismatch for {case}: got {sorted(got)} expected {sorted(expected)}"
    )

    # 2. Per-field comparison.
    for k in expected:
        if k in TS_FIELDS:
            ev = expected[k]
            gv = got[k]
            if ev is None or gv is None:
                assert ev == gv, f"{case}.{k}: null mismatch got={gv} expected={ev}"
            else:
                assert _parse_instant(gv) == _parse_instant(ev), (
                    f"{case}.{k}: instant mismatch got={gv} expected={ev}"
                )
        else:
            assert got[k] == expected[k], (
                f"{case}.{k}: got {got[k]!r} expected {expected[k]!r}"
            )
