"""Verdict parsing + frozen-contract extraction + Q&A inbox cores.

Faithful Python ports of the PURE PowerShell functions in ``loopcore.ps1``:

- :func:`parse_verdict`     <- ``ConvertFrom-VerdictJson`` (~269-329)
- :func:`contract_criteria` <- ``ConvertTo-ContractCriteria`` (~230-267)
- :func:`question_marker`   <- ``Get-QuestionMarker`` (~788-803)
- :func:`read_answer_inbox` <- ``Read-AnswerInbox`` (~831-874)

No network, no claude, no file I/O — every function operates on strings and returns plain
data. The wire shape of a parsed verdict is built by :func:`orrery_loop.events.verdict_event` so
there is ONE source of truth for the ``verdict`` event object.
"""

from __future__ import annotations

import json
import re
from typing import Any

from orrery_loop.events import verdict_event

# Regexes ported VERBATIM from loopcore.ps1.
# Fenced ```json block (preferred), non-greedy inner object.
_FENCE_RX = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL)
# First {...} object anywhere in the text (greedy, so it spans nested braces).
_OBJECT_RX = re.compile(r"(\{.*\})", re.DOTALL)

# Markdown heading + the two target section titles (case-insensitive).
_HEADING_RX = re.compile(r"^\s*#{1,6}\s+(.*)$")
_TARGET_RX = re.compile(r"^\s*(acceptance\s+criteria|definition\s+of\s+done)\s*$", re.IGNORECASE)

# List/checkbox/number markup strippers (applied in order, like the PS -replace chain).
_CHECKBOX_RX = re.compile(r"^[-*]\s+\[[ xX]\]\s*")
_BULLET_RX = re.compile(r"^[-*]\s+")
_NUMBER_RX = re.compile(r"^\d+[.)]\s+")

# Agent QUESTION marker — anchored prefix so ordinary prose never trips it.
_QUESTION_RX = re.compile(r"^\s*QUESTION:\s*(.*\S)\s*$")

# pass values accepted as truthy when the field is a string.
_PASS_WORDS = ("true", "pass", "passed", "yes", "ok")


def parse_verdict(raw_text: str | None, item: str | None, model: str | None) -> dict[str, Any]:
    """Port of ``ConvertFrom-VerdictJson``.

    Take the raw text a judge subagent returned and produce the exact PROTOCOL §2
    ``verdict`` event object (built via :func:`orrery_loop.events.verdict_event`). The judge body
    may be bare JSON, prose-wrapped JSON, or fenced in a ```json block. Accepted fields
    (snake OR camel): ``pass``|``verdict``, ``failing_criteria``|``failingCriteria``,
    ``evidence``, ``next_action``|``nextAction``.

    Fail-closed: a missing/unparseable body is treated as a FAIL (never let a malformed
    judge mint a false green). A ``pass`` that still lists failing criteria is
    contradictory and is likewise treated as a fail.
    """
    pass_ = False
    failing: list[str] = []
    evidence: str | None = None
    next_action: str | None = None

    obj: Any = None
    if raw_text:
        # Prefer a fenced ```json block; else the first {...} object in the text.
        body = raw_text
        m = _FENCE_RX.search(raw_text)
        if m:
            body = m.group(1)
        else:
            m = _OBJECT_RX.search(raw_text)
            if m:
                body = m.group(1)
        try:
            parsed = json.loads(body)
            # Only a JSON object carries the named verdict fields.
            obj = parsed if isinstance(parsed, dict) else None
        except (ValueError, TypeError):
            obj = None

    if obj is not None:
        # pass: accept bool, or string "pass"/"true"/"yes"/...
        pass_val = obj.get("pass")
        if pass_val is None:
            pass_val = obj.get("verdict")
        if isinstance(pass_val, bool):
            pass_ = pass_val
        elif pass_val is not None:
            pass_ = str(pass_val).strip().lower() in _PASS_WORDS

        fc = obj.get("failingCriteria")
        if fc is None:
            fc = obj.get("failing_criteria")
        if fc:
            failing = [str(x) for x in fc]

        if obj.get("evidence") is not None:
            evidence = str(obj["evidence"])

        na = obj.get("nextAction")
        if na is None:
            na = obj.get("next_action")
        if na is not None:
            next_action = str(na)
    else:
        # fail-closed: unparseable judge output cannot certify done.
        failing = ["verifier output unparseable"]
        evidence = "judge did not return parseable JSON"

    # A pass with a non-empty failing list is contradictory -> treat as fail.
    if pass_ and len(failing) > 0:
        pass_ = False

    return verdict_event(
        pass_=pass_,
        failing_criteria=failing,
        next_action=next_action,
        item=item,
        evidence=evidence,
        model=model,
    )


def contract_criteria(text: str | None) -> list[str]:
    """Port of ``ConvertTo-ContractCriteria``.

    Extract the acceptance criteria from a TaskFile's text. Looks for an
    ``## Acceptance Criteria`` or ``## Definition of done`` heading (case-insensitive) and
    collects its bullet / checkbox / numbered / plain-prose items until the next heading.
    Returns trimmed criteria (markup stripped); ``[]`` if no such section.
    """
    if not text:
        return []
    lines = re.split(r"\r?\n", text)
    in_section = False
    crit: list[str] = []
    for ln in lines:
        hm = _HEADING_RX.match(ln)
        if hm:
            title = hm.group(1).strip()
            if _TARGET_RX.match(title):
                in_section = True
                continue
            elif in_section:
                break  # next heading closes the section
            else:
                continue
        if not in_section:
            continue
        t = ln.strip()
        if not t:
            continue
        # strip leading list/checkbox/number markup (same order as the PS -replace chain).
        item = _CHECKBOX_RX.sub("", t)
        item = _BULLET_RX.sub("", item)
        item = _NUMBER_RX.sub("", item)
        item = item.strip()
        if item:
            crit.append(item)
    return crit


def question_marker(text: str | None) -> str | None:
    """Port of ``Get-QuestionMarker``.

    Detect an agent ``QUESTION:`` marker — a line beginning with ``QUESTION:`` (case
    handled by the literal prefix, leading whitespace tolerated). Returns the rest of that
    line, trimmed; only the FIRST such line is honored. ``None`` when absent. Deliberately
    strict (anchored prefix) so ordinary prose mentioning "question" never trips it.
    """
    if not text:
        return None
    for ln in re.split(r"\r?\n", text):
        m = _QUESTION_RX.match(ln)
        if m:
            return m.group(1).strip()
    return None


def read_answer_inbox(content: str | None, turn: int) -> dict[str, Any]:
    """Port of ``Read-AnswerInbox``.

    Parse the UI->engine ``answer.json`` contents (PROTOCOL §1 shape
    ``{ qid, kind, epic?, a }``) and decide whether the answer is FOR the open question
    ``turn``. An answer matches when its ``qid`` (or legacy ``turn``) equals the open turn,
    OR when neither is present (an untargeted answer applies to whatever question is open).

    Returns ``{matched, a, qid, kind}``. No answer body -> not matched. Unparseable / empty
    contents -> not matched (never invent an answer).
    """
    none = {"matched": False, "a": None, "qid": None, "kind": None}
    if not content or not str(content).strip():
        return none
    try:
        j = json.loads(content)
    except (ValueError, TypeError):
        return none
    if not isinstance(j, dict) or not j:
        return none

    a = str(j["a"]) if j.get("a") is not None else None
    if a is None:
        return none  # no answer body -> nothing to consume

    qid = str(j["qid"]) if j.get("qid") is not None else None
    kind = str(j["kind"]) if j.get("kind") is not None else None
    # legacy/explicit turn key
    turn_key = str(j["turn"]) if j.get("turn") is not None else None

    target = qid if qid is not None else turn_key
    # An answer with no qid/turn is untargeted -> applies to the open question.
    matched = (target is None) or (target == str(turn))

    return {
        "matched": bool(matched),
        "a": a if matched else None,
        "qid": qid,
        "kind": kind,
    }
