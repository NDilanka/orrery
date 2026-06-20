"""Wire-contract tests for the BMAD-adapter event builders in ``loop.events``.

For each builder we assert:
  - the ``event`` name string,
  - the EXACT key set (catches null-vs-omitted bugs: ``story_start_event`` omits
    ``epic``/``index`` when None; ``smoke_iter_event`` omits ``timedOut`` when None;
    ``retro_complete_event`` omits ``turn``),
  - the wire-key renames (``pass``, ``baselinePass``, ``codegenOk``/``lintOk``/``testOk``,
    ``rootCode``, ``timedOut``),
  - that the BMAD ``bmad_stop_event`` shape DIFFERS from the generic ``stop_event``.

A few cases are cross-checked against real lines in
``orrery/static/fixtures/bmad-log.jsonl`` (the authoritative bmad-loop.ps1 output).
"""

from __future__ import annotations

import json
from pathlib import Path

from loop import events

# Real bmad-loop.ps1 output, used to cross-check exact field shapes.
FIXTURE = (
    Path(__file__).resolve().parents[2]
    / "orrery"
    / "static"
    / "fixtures"
    / "bmad-log.jsonl"
)


def _fixture_lines() -> list[dict]:
    return [
        json.loads(line)
        for line in FIXTURE.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _first_with(records: list[dict], event: str, **must_have) -> dict:
    """First fixture record matching ``event`` and having all ``must_have`` keys."""
    for rec in records:
        if rec.get("event") == event and all(k in rec for k in must_have):
            return rec
    raise AssertionError(f"no fixture line for event={event!r} with {list(must_have)}")


# ---------------------------------------------------------------------------
# start
# ---------------------------------------------------------------------------


def test_start_event():
    e = events.start_event(target="2-2-x", branch="feat/story-2-2-x", baseline_pass=89)
    assert e["event"] == "start"
    assert set(e) == {"event", "target", "branch", "baselinePass"}
    assert e["baselinePass"] == 89


def test_start_event_matches_fixture():
    rec = _first_with(_fixture_lines(), "start", target=True, baselinePass=True)
    e = events.start_event(
        target=rec["target"], branch=rec["branch"], baseline_pass=rec["baselinePass"]
    )
    assert set(e) == set(rec)
    assert e == rec


# ---------------------------------------------------------------------------
# story-start — epic/index omitted when None
# ---------------------------------------------------------------------------


def test_story_start_minimal_omits_epic_and_index():
    e = events.story_start_event(story="2-3-x", status="backlog")
    assert e["event"] == "story-start"
    assert set(e) == {"event", "story", "status"}
    assert "epic" not in e and "index" not in e


def test_story_start_full_includes_epic_and_index():
    e = events.story_start_event(story="2-3-x", status="backlog", epic="2", index=1)
    assert set(e) == {"event", "story", "status", "epic", "index"}
    assert e["epic"] == "2" and e["index"] == 1


def test_story_start_epic_only():
    e = events.story_start_event(story="2-3-x", status="backlog", epic="2")
    assert set(e) == {"event", "story", "status", "epic"}


def test_story_start_index_only():
    e = events.story_start_event(story="2-3-x", status="backlog", index=2)
    assert set(e) == {"event", "story", "status", "index"}


def test_story_start_matches_fixture_with_epic_index():
    rec = _first_with(_fixture_lines(), "story-start", epic=True, index=True)
    e = events.story_start_event(
        story=rec["story"], status=rec["status"], epic=rec["epic"], index=rec["index"]
    )
    assert set(e) == set(rec)
    assert e == rec


# ---------------------------------------------------------------------------
# dev-gate — pass / baselinePass / codegenOk / lintOk / testOk renames
# ---------------------------------------------------------------------------


def test_dev_gate_event_keys_and_renames():
    e = events.dev_gate_event(
        story="2-2-x",
        cum=11.8,
        green=True,
        pass_=135,
        fail=0,
        total=135,
        baseline_pass=89,
        status="review",
        codegen_ok=True,
        lint_ok=True,
        test_ok=True,
    )
    assert e["event"] == "dev-gate"
    assert set(e) == {
        "event", "story", "cum", "green", "pass", "fail", "total",
        "baselinePass", "status", "codegenOk", "lintOk", "testOk",
    }
    # wire-key renames: the python param `pass_` lands on `pass`, never `pass_`.
    assert "pass_" not in e
    assert e["pass"] == 135
    assert e["baselinePass"] == 89
    assert e["codegenOk"] is True and e["lintOk"] is True and e["testOk"] is True


def test_dev_gate_event_matches_fixture_common_fields():
    # The real fixture's dev-gate line that DOES carry codegenOk/lintOk/testOk.
    rec = _first_with(_fixture_lines(), "dev-gate", codegenOk=True)
    e = events.dev_gate_event(
        story=rec["story"],
        cum=rec["cum"],
        green=rec["green"],
        pass_=rec["pass"],
        fail=rec["fail"],
        total=rec["total"],
        baseline_pass=rec["baselinePass"],
        status=rec["status"],
        codegen_ok=rec["codegenOk"],
        lint_ok=rec["lintOk"],
        test_ok=rec["testOk"],
    )
    assert set(e) == set(rec)
    assert e == rec


# ---------------------------------------------------------------------------
# review-complete
# ---------------------------------------------------------------------------


def test_review_complete_event():
    e = events.review_complete_event(turn=2, summary="done")
    assert e["event"] == "review-complete"
    assert set(e) == {"event", "turn", "summary"}


# ---------------------------------------------------------------------------
# retro-*
# ---------------------------------------------------------------------------


def test_retro_start_event():
    e = events.retro_start_event(epic="2")
    assert e["event"] == "retro-start"
    assert set(e) == {"event", "epic"}


def test_retro_question_event():
    e = events.retro_question_event(epic="2", turn=1, q="live e2e?")
    assert e["event"] == "retro-question"
    assert set(e) == {"event", "epic", "turn", "q"}


def test_retro_answer_event():
    e = events.retro_answer_event(epic="2", turn=1, a="option B")
    assert e["event"] == "retro-answer"
    assert set(e) == {"event", "epic", "turn", "a"}


def test_retro_complete_event_omits_turn():
    e = events.retro_complete_event(epic="2", summary="retro saved")
    assert e["event"] == "retro-complete"
    assert set(e) == {"event", "epic", "summary"}
    assert "turn" not in e


def test_retro_events_match_fixture_epic():
    recs = _fixture_lines()
    rs = _first_with(recs, "retro-start", epic=True)
    assert events.retro_start_event(epic=rs["epic"]) == rs

    rq = _first_with(recs, "retro-question", epic=True, turn=True)
    e = events.retro_question_event(epic=rq["epic"], turn=rq["turn"], q=rq["q"])
    assert set(e) == set(rq)
    assert e == rq


# ---------------------------------------------------------------------------
# smoke-server — rootCode rename
# ---------------------------------------------------------------------------


def test_smoke_server_event():
    e = events.smoke_server_event(url="http://localhost:3000", root_code=404)
    assert e["event"] == "smoke-server"
    assert set(e) == {"event", "url", "rootCode"}
    assert "root_code" not in e
    assert e["rootCode"] == 404


def test_smoke_server_event_matches_fixture():
    rec = _first_with(_fixture_lines(), "smoke-server", url=True, rootCode=True)
    e = events.smoke_server_event(url=rec["url"], root_code=rec["rootCode"])
    assert set(e) == set(rec)
    assert e == rec


# ---------------------------------------------------------------------------
# smoke-iter — iter rename, timedOut omitted when None
# ---------------------------------------------------------------------------


def test_smoke_iter_event_result_omits_timed_out():
    e = events.smoke_iter_event(iter_=1, passed=True, verdict="SMOKE_PASS: ok")
    assert e["event"] == "smoke-iter"
    assert set(e) == {"event", "iter", "passed", "verdict"}
    assert "timedOut" not in e
    assert "iter_" not in e
    assert e["iter"] == 1


def test_smoke_iter_event_includes_timed_out_when_set():
    e = events.smoke_iter_event(iter_=1, passed=False, verdict="", timed_out=True)
    assert set(e) == {"event", "iter", "passed", "verdict", "timedOut"}
    assert e["timedOut"] is True


def test_smoke_iter_event_timed_out_false_still_present():
    # Only None omits; an explicit False must serialize.
    e = events.smoke_iter_event(iter_=2, passed=True, verdict="x", timed_out=False)
    assert "timedOut" in e
    assert e["timedOut"] is False


def test_smoke_iter_event_matches_fixture_pass_line():
    rec = _first_with(_fixture_lines(), "smoke-iter", passed=True, verdict=True)
    e = events.smoke_iter_event(
        iter_=rec["iter"], passed=rec["passed"], verdict=rec["verdict"]
    )
    assert set(e) == set(rec)
    assert e == rec


# ---------------------------------------------------------------------------
# pr-created / pr-merged
# ---------------------------------------------------------------------------


def test_pr_created_event():
    e = events.pr_created_event(
        story="2-3-x", branch="feat/x", base="develop", url="https://gh/pr/3"
    )
    assert e["event"] == "pr-created"
    assert set(e) == {"event", "story", "branch", "base", "url"}


def test_pr_merged_event():
    e = events.pr_merged_event(story="2-3-x", base="develop", pr="https://gh/pr/3")
    assert e["event"] == "pr-merged"
    assert set(e) == {"event", "story", "base", "pr"}


def test_pr_events_match_fixture():
    recs = _fixture_lines()
    created = _first_with(recs, "pr-created", base=True, url=True)
    e = events.pr_created_event(
        story=created["story"],
        branch=created["branch"],
        base=created["base"],
        url=created["url"],
    )
    assert set(e) == set(created)
    assert e == created

    merged = _first_with(recs, "pr-merged", base=True, pr=True)
    m = events.pr_merged_event(
        story=merged["story"], base=merged["base"], pr=merged["pr"]
    )
    assert set(m) == set(merged)
    assert m == merged


# ---------------------------------------------------------------------------
# stop — BMAD shape DIFFERS from the generic stop_event
# ---------------------------------------------------------------------------


def test_bmad_stop_event():
    e = events.bmad_stop_event(ok=True, reason="complete", cum=11.8)
    assert e["event"] == "stop"
    assert set(e) == {"event", "ok", "reason", "cum"}


def test_bmad_stop_event_differs_from_generic_stop_event():
    bmad = events.bmad_stop_event(ok=True, reason="done", cum=11.8)
    generic = events.stop_event(
        reason="done", green=True, iter=4, cum=11.8, best_pass=8
    )
    # Both share event name "stop" but carry DIFFERENT key sets.
    assert bmad["event"] == generic["event"] == "stop"
    assert set(bmad) != set(generic)
    assert set(bmad) == {"event", "ok", "reason", "cum"}
    assert set(generic) == {"event", "reason", "green", "iter", "cum", "bestPass"}
    # The discriminating keys: bmad has `ok`, generic has green/iter/bestPass.
    assert "ok" in bmad and "ok" not in generic
    assert "green" in generic and "green" not in bmad


def test_bmad_stop_event_matches_fixture():
    rec = _first_with(_fixture_lines(), "stop", ok=True)
    e = events.bmad_stop_event(ok=rec["ok"], reason=rec["reason"], cum=rec["cum"])
    assert set(e) == set(rec)
    assert e == rec
