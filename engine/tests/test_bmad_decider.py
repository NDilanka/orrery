"""Tests for the cheap-model Q&A deciders (review_decider / retro_decider).

A MockRunner records every ``run`` call's kwargs and returns a canned answer; the tests assert
the decider passes the cheap model + a prompt containing the question/scope, and returns the
collapsed answer text (with the fallback when the model returns nothing).
"""

from __future__ import annotations

from orrery_loop.bmad.decider import retro_decider, review_decider
from orrery_loop.runners.base import AgentResult, AgentRunner


class MockRunner(AgentRunner):
    """Records run() kwargs; returns a canned AgentResult (text or empty)."""

    name = "mock"

    def __init__(self, text: str = ""):
        self._text = text
        self.calls: list[dict] = []

    def run(self, **kwargs) -> AgentResult:  # type: ignore[override]
        self.calls.append(dict(kwargs))
        return AgentResult(raw="", text=self._text, cost_usd=0.01)


# --- review_decider --------------------------------------------------------


def test_review_decider_returns_answer_and_passes_model_and_prompt():
    runner = MockRunner(text="Option 2: patch the null-deref. It is an in-scope crash.")
    ans = review_decider(
        runner,
        question="Finding F1: possible null-deref. (1) ignore (2) patch?",
        story_scope="3-4",
        model="haiku",
    )
    assert ans == "Option 2: patch the null-deref. It is an in-scope crash."
    assert len(runner.calls) == 1
    call = runner.calls[0]
    # cheap model passed through
    assert call["model"] == "haiku"
    # prompt carries the question + the story scope + the orchestrator framing
    prompt = call["prompt"]
    assert "possible null-deref" in prompt
    assert "story 3-4" in prompt
    assert "NEVER weaken, skip, or delete tests" in prompt
    assert "Be decisive and concise" in prompt


def test_review_decider_collapses_newlines_to_single_line():
    runner = MockRunner(text="Line one.\nLine two.\r\nLine three.")
    ans = review_decider(runner, question="q", story_scope="1-1")
    assert "\n" not in ans
    assert ans == "Line one. Line two. Line three."


def test_review_decider_empty_result_uses_fallback():
    runner = MockRunner(text="   \n  ")
    ans = review_decider(runner, question="q", story_scope="1-1")
    assert ans == (
        "Apply the safest in-scope fix; defer non-blocking items; never weaken tests."
    )


def test_review_decider_default_model_is_haiku():
    runner = MockRunner(text="ok")
    review_decider(runner, question="q", story_scope="2-1")
    assert runner.calls[0]["model"] == "haiku"


# --- retro_decider ---------------------------------------------------------


def test_retro_decider_returns_answer_and_passes_model_and_prompt():
    runner = MockRunner(text="Biggest win: TDD + AC-aware smoke. One action: tighten flaky CI.")
    ans = retro_decider(
        runner,
        question="What went well and what is the top action item?",
        epic_scope="3",
        model="haiku",
    )
    assert ans == "Biggest win: TDD + AC-aware smoke. One action: tighten flaky CI."
    call = runner.calls[0]
    assert call["model"] == "haiku"
    prompt = call["prompt"]
    assert "EPIC 3 RETROSPECTIVE" in prompt
    assert "top action item" in prompt
    assert "constructively and DECISIVELY" in prompt


def test_retro_decider_empty_result_uses_fallback():
    runner = MockRunner(text="")
    ans = retro_decider(runner, question="q", epic_scope="2")
    assert ans == (
        "Carry forward what worked (per-story TDD + gates); keep action items minimal and "
        "concrete; proceed."
    )


def test_retro_decider_collapses_newlines():
    runner = MockRunner(text="a\nb")
    ans = retro_decider(runner, question="q", epic_scope="1")
    assert ans == "a b"
