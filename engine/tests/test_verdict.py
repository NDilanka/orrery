"""Parity tests for the verdict parser, contract extractor, QUESTION marker and answer
inbox — mirrors ``selftest-verify.ps1`` (verdict + contract) and ``selftest-final.ps1``
(QUESTION marker + answer inbox)."""

from __future__ import annotations

from orrery_loop.verdict import (
    contract_criteria,
    parse_verdict,
    question_marker,
    read_answer_inbox,
)

# =====================================================================
# 1. VERDICT PARSER -> exact PROTOCOL §2 `verdict` event
# =====================================================================


def test_clean_pass_bare_json():
    raw = '{ "pass": true, "failingCriteria": [], "evidence": "all AC met", "nextAction": "" }'
    v = parse_verdict(raw, item="TASK.md", model="haiku")
    assert v["event"] == "verdict"
    assert v["item"] == "TASK.md"
    assert v["pass"] is True
    assert v["model"] == "haiku"
    assert v["failingCriteria"] == []
    assert v["evidence"] == "all AC met"
    # exact PROTOCOL field names, no snake leakage
    assert set(v.keys()) == {
        "event", "item", "pass", "failingCriteria", "evidence", "nextAction", "model",
    }


def test_refute_fenced_json_with_prose():
    raw = """Here is my assessment after reviewing the diff against the contract.

```json
{ "pass": false,
  "failingCriteria": ["2^3^2 == 512 not handled", "unary minus missing"],
  "evidence": "diff only adds + and -, no exponent operator",
  "nextAction": "implement right-associative ^ in src/calc.ts" }
```
"""
    v = parse_verdict(raw, item="TASK.calc.md", model="haiku")
    assert v["pass"] is False
    assert v["failingCriteria"] == ["2^3^2 == 512 not handled", "unary minus missing"]
    assert v["nextAction"] == "implement right-associative ^ in src/calc.ts"


def test_snake_case_accepted():
    raw = '{ "verdict": "fail", "failing_criteria": ["x"], "evidence": "e", "next_action": "n" }'
    v = parse_verdict(raw, item="k", model="sonnet")
    assert v["pass"] is False
    assert v["failingCriteria"] == ["x"]
    assert v["nextAction"] == "n"


def test_contradiction_pass_with_failing_is_fail():
    # fail-closed: a pass=true that still lists failing criteria is a FAIL.
    raw = '{ "pass": true, "failingCriteria": ["still broken"] }'
    v = parse_verdict(raw, item="k", model="haiku")
    assert v["pass"] is False
    assert v["failingCriteria"] == ["still broken"]


def test_unparseable_fail_closed():
    v = parse_verdict("I could not produce JSON, sorry.", item="k", model="haiku")
    assert v["pass"] is False
    assert v["failingCriteria"] == ["verifier output unparseable"]
    assert v["evidence"] == "judge did not return parseable JSON"


def test_empty_and_none_body_fail_closed():
    for raw in ("", None):
        v = parse_verdict(raw, item="k", model="haiku")
        assert v["pass"] is False
        assert len(v["failingCriteria"]) >= 1


def test_string_true_yes_ok_accepted_as_pass():
    for val in ('"true"', '"yes"', '"ok"', '"pass"', '"passed"'):
        v = parse_verdict(f'{{ "pass": {val}, "failingCriteria": [] }}', item="k", model="haiku")
        assert v["pass"] is True, val
    # a non-pass word is NOT a pass
    v = parse_verdict('{ "pass": "nope", "failingCriteria": [] }', item="k", model="haiku")
    assert v["pass"] is False


def test_prose_wrapped_bare_object():
    raw = "Sure, here you go: { \"pass\": false, \"failingCriteria\": [\"a\"] } — done."
    v = parse_verdict(raw, item="k", model="haiku")
    assert v["pass"] is False
    assert v["failingCriteria"] == ["a"]


# --- string failingCriteria: one criterion, never char-by-char --------------


def test_string_failing_criteria_is_single_criterion_and_refutes():
    # Regression: a STRING failingCriteria was iterated char-by-char (dozens of 1-char
    # "criteria") — still refuting a pass, but with garbage detail. Now it's ONE criterion.
    raw = '{ "pass": true, "failingCriteria": "spec still failing" }'
    v = parse_verdict(raw, item="k", model="haiku")
    assert v["failingCriteria"] == ["spec still failing"]
    assert v["pass"] is False  # non-empty -> refutes the pass


def test_string_none_failing_criteria_treated_as_one_criterion_and_refutes():
    # Deliberate rule: any NON-EMPTY string is one criterion (judges must return a LIST). We do
    # NOT special-case "none" with an NLP heuristic, so "none" refutes — documented contract.
    raw = '{ "pass": true, "failingCriteria": "none" }'
    v = parse_verdict(raw, item="k", model="haiku")
    assert v["failingCriteria"] == ["none"]
    assert v["pass"] is False


def test_empty_string_failing_criteria_keeps_pass():
    # Falsy ("") short-circuits -> [] -> a clean pass survives.
    raw = '{ "pass": true, "failingCriteria": "" }'
    v = parse_verdict(raw, item="k", model="haiku")
    assert v["failingCriteria"] == []
    assert v["pass"] is True


def test_null_failing_criteria_keeps_pass():
    raw = '{ "pass": true, "failingCriteria": null }'
    v = parse_verdict(raw, item="k", model="haiku")
    assert v["failingCriteria"] == []
    assert v["pass"] is True


def test_list_failing_criteria_multi_element_preserved():
    raw = '{ "pass": false, "failingCriteria": ["a", "b"] }'
    v = parse_verdict(raw, item="k", model="haiku")
    assert v["failingCriteria"] == ["a", "b"]
    assert v["pass"] is False


# =====================================================================
# 2. FROZEN-CONTRACT EXTRACTOR
# =====================================================================


def test_acceptance_criteria_mixed_markup_closed_by_heading():
    task = """# TASK

## Goal
make it work

## Acceptance Criteria
- 401 on expired token
- [ ] tests are green
- [x] handles unary minus
1. supports parentheses

## Working agreement
do not edit tests
"""
    c = contract_criteria(task)
    assert c == [
        "401 on expired token",
        "tests are green",
        "handles unary minus",
        "supports parentheses",
    ]


def test_definition_of_done_prose_line():
    task = (
        "## Definition of done\n"
        "`bun test` reports 0 failures with all three original tests present.\n"
        "\n"
        "## Working agreement\n"
        "1. read the file\n"
    )
    c = contract_criteria(task)
    assert len(c) == 1
    assert "reports 0 failures" in c[0]


def test_heading_case_insensitive():
    c = contract_criteria("## DEFINITION OF DONE\n- done when green")
    assert c == ["done when green"]


def test_no_section_returns_empty():
    assert contract_criteria("# TASK\n## Goal\njust do it") == []


def test_empty_and_none_text_returns_empty():
    assert contract_criteria("") == []
    assert contract_criteria(None) == []


def test_numbered_paren_form_stripped():
    c = contract_criteria("## Acceptance Criteria\n1) first\n2) second")
    assert c == ["first", "second"]


# =====================================================================
# 3. QUESTION MARKER (strict, anchored prefix)
# =====================================================================


def test_marker_first_line():
    q = question_marker(
        "QUESTION: should the parser support hex literals like 0x1F?\nmore notes below"
    )
    assert q == "should the parser support hex literals like 0x1F?"


def test_marker_mid_text_with_leading_ws():
    q = question_marker("## Failing / Next\n  QUESTION: pick base 10 or 16 default?")
    assert q == "pick base 10 or 16 default?"


def test_prose_mentioning_question_does_not_trip():
    assert question_marker("I considered the question of precedence and fixed it.") is None


def test_empty_null_no_marker():
    assert question_marker("") is None
    assert question_marker(None) is None


def test_bare_prefix_no_body_no_marker():
    assert question_marker("QUESTION:   ") is None


def test_first_marker_wins():
    assert question_marker("QUESTION: first?\nQUESTION: second?") == "first?"


# =====================================================================
# 4. ANSWER INBOX (parse + match for the open turn)
# =====================================================================


def test_inbox_match_by_qid():
    i = read_answer_inbox('{ "qid": "4", "kind": "review", "a": "Yes, support hex." }', turn=4)
    assert i["matched"] is True
    assert i["a"] == "Yes, support hex."
    assert i["kind"] == "review"
    assert i["qid"] == "4"


def test_inbox_wrong_turn_withholds_answer():
    i = read_answer_inbox('{ "qid": "9", "a": "later" }', turn=4)
    assert i["matched"] is False
    assert i["a"] is None


def test_inbox_legacy_turn_key_matches():
    i = read_answer_inbox('{ "turn": 4, "a": "ok" }', turn=4)
    assert i["matched"] is True
    assert i["a"] == "ok"


def test_inbox_untargeted_applies_to_open_turn():
    i = read_answer_inbox('{ "a": "use base 10" }', turn=7)
    assert i["matched"] is True
    assert i["a"] == "use base 10"


def test_inbox_no_body_not_matched():
    i = read_answer_inbox('{ "qid": "4", "kind": "review" }', turn=4)
    assert i["matched"] is False


def test_inbox_garbage_empty_none_not_matched():
    assert read_answer_inbox("not json", turn=4)["matched"] is False
    assert read_answer_inbox("", turn=4)["matched"] is False
    assert read_answer_inbox(None, turn=4)["matched"] is False
