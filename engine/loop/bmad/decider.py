"""Cheap-model Q&A deciders — ports of ``Invoke-ReviewDecider`` / ``Invoke-RetroDecider``.

The BMAD code-review and epic-retrospective phases run the agent NON-INTERACTIVELY: when
the reviewer/facilitator needs a human decision it emits a ``QUESTION:`` marker and stops.
The driver stands in for the human by asking a CHEAP model (haiku) to decide, then feeds the
answer back. These two functions build the exact decisive-and-concise prompts the PowerShell
``bmad-loop.ps1`` used and return the model's answer text (single-line, collapsed newlines).

Both are deliberately THIN and runner-injected: the driver passes a concrete
:class:`loop.runners.base.AgentRunner` in production; tests pass a MockRunner. The decider
NEVER spawns a process itself — that is the runner's job — so the logic is unit-testable.

Faithful to the PowerShell:
- Same prompt body text (the orchestrator-standing-in-for-the-human framing + the
  PATCH/DEFER/DISMISS/never-weaken-tests principles for review; the constructive-and-decisive
  retrospective framing).
- Same fallback answer when the model returns nothing usable.
- The answer's newlines are collapsed to spaces and trimmed (PS ``-replace "`r?`n", " "``).
"""

from __future__ import annotations

import re

from loop.runners.base import AgentRunner

# Collapse any run of CR/LF into a single space — the PS ``-replace "`r?`n", " "`` then Trim.
_NEWLINE_RX = re.compile(r"\r?\n")

# The deciders ask a one-shot question; no tools, plan-mode (read-only), a single turn.
_DECIDER_TOOLS: tuple[str, ...] = ()
_DECIDER_PERMISSION_MODE = "plan"
_DECIDER_MAX_TURNS = 1

# Fallbacks mirror the PS ``return "..."`` lines when ``$o.res.result`` is empty/unparseable.
_REVIEW_FALLBACK = (
    "Apply the safest in-scope fix; defer non-blocking items; never weaken tests."
)
_RETRO_FALLBACK = (
    "Carry forward what worked (per-story TDD + gates); keep action items minimal and "
    "concrete; proceed."
)


def _answer_text(result: object, fallback: str) -> str:
    """Collapse the runner's result text to one trimmed line, or the fallback when empty."""
    text = "" if result is None else str(result)
    collapsed = _NEWLINE_RX.sub(" ", text).strip()
    return collapsed if collapsed else fallback


def review_decider(
    runner: AgentRunner,
    *,
    question: str,
    story_scope: str,
    model: str = "haiku",
    effort: str = "",
) -> str:
    """Port of ``Invoke-ReviewDecider``.

    The reviewer (running a BMAD ``bmad-code-review``) hit a decision point and asked
    ``question``; ``story_scope`` identifies the story whose scope bounds the decision (the PS
    used the story key). Build the decisive-and-concise orchestrator prompt, run it on the
    CHEAP ``model``, and return the answer as a single trimmed line. Falls back to a safe
    in-scope answer when the model returns nothing usable.
    """
    prompt = (
        f"You are the orchestrator, standing in for the human in a BMAD code review of "
        f"story {story_scope}.\n"
        "The reviewer needs you to decide:\n\n"
        f"{question}\n\n"
        "Think step by step, then decide. Principles:\n"
        "- PATCH real, in-scope correctness/security bugs (safe, minimal fixes).\n"
        "- DEFER non-blocking improvements (they go to deferred-work).\n"
        "- DISMISS false positives or out-of-scope items.\n"
        "- NEVER weaken, skip, or delete tests. Stay within this story's scope.\n"
        "Reply with the exact option the reviewer expects (number/label) and a one-sentence "
        "rationale. Be decisive and concise."
    )
    res = runner.run(
        prompt=prompt,
        model=model,
        effort=effort,
        allowed_tools=list(_DECIDER_TOOLS),
        permission_mode=_DECIDER_PERMISSION_MODE,
        max_turns=_DECIDER_MAX_TURNS,
        cwd=None,
    )
    return _answer_text(getattr(res, "text", None), _REVIEW_FALLBACK)


def retro_decider(
    runner: AgentRunner,
    *,
    question: str,
    epic_scope: str,
    model: str = "haiku",
    effort: str = "",
) -> str:
    """Port of ``Invoke-RetroDecider``.

    The retrospective facilitator (running ``bmad-retrospective`` for an epic) asked
    ``question``; ``epic_scope`` identifies the epic (the PS used the epic number). Build the
    constructive-and-decisive team-lead prompt, run it on the CHEAP ``model``, and return the
    answer as a single trimmed line. Falls back to a carry-forward answer when the model
    returns nothing usable.
    """
    prompt = (
        f"You are the team lead / orchestrator standing in for the human in a BMAD EPIC "
        f"{epic_scope} RETROSPECTIVE.\n"
        "The facilitator asks you:\n\n"
        f"{question}\n\n"
        "Answer constructively and DECISIVELY from the perspective of someone who reviewed "
        "this epic's work\n"
        "(it shipped story-by-story with TDD + adversarial code-review + AC-aware browser "
        "smoke; all stories\n"
        "green and merged to develop). Be honest about what went well and what to improve. "
        "When asked to choose\n"
        "or prioritize, pick decisively and justify in one sentence. Keep action items "
        "concrete and few; do not\n"
        "invent problems. Reply concisely with your answer (include the chosen option/label "
        "if options were given)."
    )
    res = runner.run(
        prompt=prompt,
        model=model,
        effort=effort,
        allowed_tools=list(_DECIDER_TOOLS),
        permission_mode=_DECIDER_PERMISSION_MODE,
        max_turns=_DECIDER_MAX_TURNS,
        cwd=None,
    )
    return _answer_text(getattr(res, "text", None), _RETRO_FALLBACK)
