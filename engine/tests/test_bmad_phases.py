"""Hermetic tests for the BMAD phase functions.

Everything is injected: a MockRunner returns a queue of canned AgentResults, a FakeServer
implements ``.start()/.stop()``, and ``gate_fn`` is a plain callable returning a run_gate-style
dict. No real process is spawned. The DevServer (real ``server_ctl``) is NOT exercised here.
"""

from __future__ import annotations

from loop.bmad import phases
from loop.bmad.phases import (
    browser_smoke,
    code_review,
    create_story,
    dev_story,
)
from loop.runners.base import AgentResult, AgentRunner


class MockRunner(AgentRunner):
    """Returns queued AgentResults in order; records each run()'s kwargs."""

    name = "mock"

    def __init__(self, results: list[AgentResult]):
        self._results = list(results)
        self.calls: list[dict] = []

    def run(self, **kwargs) -> AgentResult:  # type: ignore[override]
        self.calls.append(dict(kwargs))
        if self._results:
            return self._results.pop(0)
        # default: a benign empty completion
        return AgentResult(raw="", text="", cost_usd=0.0)


class FakeServer:
    """Fake server_ctl: returns a fixed url from start(); records stop() calls."""

    def __init__(self, url: str | None = "http://localhost:4137"):
        self._url = url
        self.started = 0
        self.stopped = 0

    def start(self) -> str | None:
        self.started += 1
        return self._url

    def stop(self) -> None:
        self.stopped += 1


def gate(green=True, pass_=10, fail=0, stages=None):
    """Build a run_gate-style result dict."""
    return {
        "green": green,
        "pass": pass_,
        "fail": fail,
        "total": pass_ + fail,
        "stages": stages
        if stages is not None
        else [
            {"name": "codegen", "ok": True, "exit": 0},
            {"name": "lint", "ok": True, "exit": 0},
            {"name": "test", "ok": green, "exit": 0 if green else 1},
        ],
        "raw": "",
    }


def _events(name):
    """A capturing emit() callback + its event list."""
    log: list[dict] = []
    return log, log.append


# --- create_story ----------------------------------------------------------


def test_create_story_retries_on_greeting_then_succeeds():
    runner = MockRunner(
        [
            AgentResult(raw="", text="Hi there! What would you like to work on?", cost_usd=0.1),
            AgentResult(raw="", text="Story 4-1 drafted and set to ready-for-dev.", cost_usd=0.5),
        ]
    )
    log, emit = _events("create")
    res = create_story(
        runner, emit=emit, cwd="/repo", model="sonnet", max_turns=0
    )
    assert res.ok is True
    assert res.extra["attempts"] == 2
    # cost accumulates across both attempts
    assert res.cost == 0.6
    assert len(runner.calls) == 2
    # the non-interactive create-story instruction is in the prompt
    assert "IMMEDIATELY invoke the bmad-create-story" in runner.calls[0]["prompt"]


def test_create_story_uses_injected_produced_predicate():
    runner = MockRunner(
        [AgentResult(raw="", text="(noise)", cost_usd=0.0) for _ in range(3)]
    )
    log, emit = _events("create")
    seq = iter([False, True])
    res = create_story(
        runner,
        emit=emit,
        cwd="/repo",
        model="sonnet",
        produced=lambda: next(seq),
    )
    assert res.ok is True
    assert res.extra["attempts"] == 2


def test_create_story_fails_after_three_greetings():
    runner = MockRunner(
        [AgentResult(raw="", text="Hello! How can I help?", cost_usd=0.1) for _ in range(3)]
    )
    log, emit = _events("create")
    res = create_story(runner, emit=emit, cwd="/repo", model="sonnet")
    assert res.ok is False
    assert res.extra["attempts"] == 3
    assert len(runner.calls) == 3
    assert "after 3 attempts" in res.reason


def test_create_story_timeout_sec_threaded_to_runner_and_emits_phase_timeout():
    """Task 1b: a timed-out attempt is NOT special-cased beyond a phase-timeout event — it
    falls through to the same produced()/greeting check as any other unproductive attempt
    (conservative: no new halt path), so it is simply retried."""
    runner = MockRunner(
        [
            AgentResult(raw="", text="", is_error=True, timed_out=True),
            AgentResult(raw="", text="Story 4-1 drafted and set to ready-for-dev.", cost_usd=0.5),
        ]
    )
    log, emit = _events("create")
    res = create_story(
        runner, emit=emit, cwd="/repo", model="sonnet", timeout_sec=1800
    )
    assert res.ok is True
    assert res.extra["attempts"] == 2
    assert runner.calls[0]["timeout_sec"] == 1800
    assert runner.calls[1]["timeout_sec"] == 1800
    timeouts = [e for e in log if e["event"] == "phase-timeout"]
    assert len(timeouts) == 1
    assert timeouts[0]["timeoutSec"] == 1800
    assert "create-story" in timeouts[0]["label"]


# --- dev_story -------------------------------------------------------------


def test_dev_story_emits_dev_gate_with_codegen_lint_test_booleans():
    runner = MockRunner([AgentResult(raw="", text="dev complete; status: review", cost_usd=1.0)])
    log, emit = _events("dev")
    res = dev_story(
        runner,
        "3-4",
        emit=emit,
        cwd="/repo",
        gate_fn=lambda: gate(green=True, pass_=42),
        model="sonnet",
        baseline_pass=40,
        cum=2.0,
        status="review",
    )
    assert res.ok is True
    assert res.gate["pass"] == 42
    assert res.cost == 1.0
    devgate = [e for e in log if e["event"] == "dev-gate"]
    assert len(devgate) == 1
    g = devgate[0]
    assert g["story"] == "3-4"
    assert g["green"] is True
    assert g["pass"] == 42
    assert g["baselinePass"] == 40
    assert g["status"] == "review"
    assert g["codegenOk"] is True
    assert g["lintOk"] is True
    assert g["testOk"] is True
    # cum is the carried cum + this phase's cost
    assert g["cum"] == 3.0


def test_dev_story_threads_effort_to_runner():
    runner = MockRunner([AgentResult(raw="", text="done", cost_usd=0.0)])
    log, emit = _events("dev")
    dev_story(
        runner, "1-1", emit=emit, cwd="/repo",
        gate_fn=lambda: gate(green=True), model="opus", effort="xhigh",
    )
    assert runner.calls[0]["model"] == "opus"
    assert runner.calls[0]["effort"] == "xhigh"


def test_dev_story_reports_failed_stages_in_dev_gate():
    runner = MockRunner([AgentResult(raw="", text="x", cost_usd=0.0)])
    log, emit = _events("dev")
    failing_stages = [
        {"name": "codegen", "ok": True, "exit": 0},
        {"name": "lint", "ok": False, "exit": 1},
        {"name": "test", "ok": False, "exit": 1},
    ]
    res = dev_story(
        runner,
        "1-1",
        emit=emit,
        cwd="/repo",
        gate_fn=lambda: gate(green=False, pass_=5, fail=2, stages=failing_stages),
        model="sonnet",
    )
    assert res.ok is False
    g = log[0]
    assert g["codegenOk"] is True
    assert g["lintOk"] is False
    assert g["testOk"] is False
    assert g["green"] is False


def test_dev_story_timeout_emits_phase_timeout_but_still_checks_gate():
    """Task 1b: dev-story never special-cased is_error/parse_failed on the run result — it
    always runs the gate and lets THAT decide `ok`. A timeout follows that SAME existing path:
    it only additionally emits a phase-timeout event before falling through to the gate check."""
    runner = MockRunner([AgentResult(raw="", text="", is_error=True, timed_out=True)])
    log, emit = _events("dev")
    res = dev_story(
        runner,
        "3-4",
        emit=emit,
        cwd="/repo",
        gate_fn=lambda: gate(green=False, pass_=1, fail=1),
        model="sonnet",
        timeout_sec=7200,
    )
    assert runner.calls[0]["timeout_sec"] == 7200
    timeouts = [e for e in log if e["event"] == "phase-timeout"]
    assert len(timeouts) == 1
    assert timeouts[0]["timeoutSec"] == 7200
    assert "dev-story:3-4" in timeouts[0]["label"]
    # the gate (red, since nothing finished) is what actually decides ok — unchanged semantics.
    assert res.ok is False
    devgate = [e for e in log if e["event"] == "dev-gate"]
    assert len(devgate) == 1 and devgate[0]["green"] is False


# --- code_review (Q&A loop) ------------------------------------------------


def stub_decider(runner, *, question, story_scope, model="haiku", effort=""):
    """A deterministic decider stub for the code-review Q&A loop."""
    return f"ANSWER[{story_scope}]: {question[:20]}"


def test_code_review_question_then_complete_full_qa_cycle():
    runner = MockRunner(
        [
            AgentResult(
                raw="",
                text="QUESTION: Finding F1 — null guard missing. (1) ignore (2) patch?",
                cost_usd=0.3,
                session_id="sess-1",
            ),
            AgentResult(
                raw="",
                text="REVIEW_COMPLETE: applied the null guard; all findings resolved.",
                cost_usd=0.2,
                session_id="sess-1",
            ),
        ]
    )
    log, emit = _events("review")
    res = code_review(
        runner,
        stub_decider,
        "3-4",
        emit=emit,
        cwd="/repo",
        gate_fn=lambda: gate(green=True, pass_=50),
        max_turns=8,
        model="sonnet",
    )
    assert res.ok is True
    assert res.gate["pass"] == 50
    kinds = [e["event"] for e in log]
    assert kinds == ["review-question", "review-answer", "review-complete"]
    # the answer came from the stub decider and was fed back via --resume
    q_evt = log[0]
    assert q_evt["turn"] == 1
    assert q_evt["story"] == "3-4"
    a_evt = log[1]
    assert a_evt["a"].startswith("ANSWER[3-4]:")
    # second runner.run resumed the first session and used the answer as the prompt
    assert runner.calls[1]["resume_session"] == "sess-1"
    assert runner.calls[1]["prompt"] == a_evt["a"]
    # review-complete carries the summary
    assert "all findings resolved" in log[2]["summary"]


def test_code_review_no_marker_treated_as_complete():
    runner = MockRunner(
        [AgentResult(raw="", text="The review looks fine, nothing to ask.", cost_usd=0.1)]
    )
    log, emit = _events("review")
    res = code_review(
        runner,
        stub_decider,
        "2-1",
        emit=emit,
        cwd="/repo",
        gate_fn=lambda: gate(green=True),
        max_turns=8,
        model="sonnet",
    )
    assert res.ok is True
    # no QUESTION/REVIEW_COMPLETE marker -> still completes (no spin), no Q/A events
    assert [e["event"] for e in log] == []
    assert res.extra["summary"] == "no-marker"
    assert len(runner.calls) == 1


def test_code_review_respects_max_turns():
    # Every turn emits a QUESTION (never completes) -> must stop at max_turns.
    runner = MockRunner(
        [
            AgentResult(
                raw="",
                text=f"QUESTION: q{i}? (1) a (2) b",
                cost_usd=0.1,
                session_id="sess-1",
            )
            for i in range(10)
        ]
    )
    log, emit = _events("review")
    res = code_review(
        runner,
        stub_decider,
        "1-1",
        emit=emit,
        cwd="/repo",
        gate_fn=lambda: gate(green=True),
        max_turns=3,
        model="sonnet",
    )
    assert res.ok is False
    assert "exceeded 3 turns" in res.reason
    assert len(runner.calls) == 3
    # 3 question + 3 answer events, no complete
    assert [e["event"] for e in log].count("review-question") == 3
    assert [e["event"] for e in log].count("review-answer") == 3
    assert [e["event"] for e in log].count("review-complete") == 0


def test_code_review_post_complete_gate_red_is_not_ok():
    runner = MockRunner(
        [AgentResult(raw="", text="REVIEW_COMPLETE: done.", cost_usd=0.1)]
    )
    log, emit = _events("review")
    res = code_review(
        runner,
        stub_decider,
        "1-1",
        emit=emit,
        cwd="/repo",
        gate_fn=lambda: gate(green=False, pass_=3, fail=4),
        max_turns=8,
        model="sonnet",
    )
    assert res.ok is False
    assert "review-complete" in [e["event"] for e in log]


def test_code_review_timeout_halts_instead_of_treating_no_marker_as_complete():
    """Task 1b: a timed-out turn must take the SAME non-ok exit as is_error/parse_failed — it
    must NOT fall through to the 'no marker -> treat as complete' branch, which would wrongly
    accept a killed run as a finished review."""
    runner = MockRunner([AgentResult(raw="", text="", is_error=True, timed_out=True)])
    log, emit = _events("review")
    res = code_review(
        runner,
        stub_decider,
        "1-1",
        emit=emit,
        cwd="/repo",
        gate_fn=lambda: gate(green=True),  # would be ok=True if wrongly treated as complete
        max_turns=8,
        model="sonnet",
        timeout_sec=3600,
    )
    assert res.ok is False
    assert "timed out" in res.reason
    assert runner.calls[0]["timeout_sec"] == 3600
    kinds = [e["event"] for e in log]
    assert "phase-timeout" in kinds
    assert "review-complete" not in kinds  # never wrongly marked complete


# --- code_review_single_pass (one warm process, no Q&A) --------------------


def test_code_review_single_pass_one_process_no_qa():
    runner = MockRunner(
        [AgentResult(raw="", text="REVIEW_COMPLETE: decided + applied 2 findings; all green.", cost_usd=0.4)]
    )
    log, emit = _events("review")
    res = phases.code_review_single_pass(
        runner,
        "3-4",
        emit=emit,
        cwd="/repo",
        gate_fn=lambda: gate(green=True, pass_=50),
        model="opus",
    )
    assert res.ok is True
    assert res.gate["pass"] == 50
    assert res.extra["mode"] == "single-pass"
    # ONE process, no Q&A round-trips, no separate decider
    assert len(runner.calls) == 1
    assert runner.calls[0]["max_turns"] == 0
    assert [e["event"] for e in log] == ["review-complete"]
    # the decider principles are folded into the single prompt (no QUESTION protocol)
    assert "DECIDE every finding yourself" in runner.calls[0]["prompt"]
    assert "PATCH" in runner.calls[0]["prompt"] and "NEVER weaken" in runner.calls[0]["prompt"]


def test_code_review_single_pass_post_gate_red_is_not_ok():
    runner = MockRunner([AgentResult(raw="", text="REVIEW_COMPLETE: done.", cost_usd=0.1)])
    log, emit = _events("review")
    res = phases.code_review_single_pass(
        runner,
        "1-1",
        emit=emit,
        cwd="/repo",
        gate_fn=lambda: gate(green=False, pass_=3, fail=4),
        model="sonnet",
    )
    assert res.ok is False
    assert "review-complete" in [e["event"] for e in log]


def test_code_review_single_pass_timeout_halts():
    runner = MockRunner([AgentResult(raw="", text="", is_error=True, timed_out=True)])
    log, emit = _events("review")
    res = phases.code_review_single_pass(
        runner,
        "1-1",
        emit=emit,
        cwd="/repo",
        gate_fn=lambda: gate(green=True),
        model="sonnet",
        timeout_sec=1200,
    )
    assert res.ok is False
    assert "timed out" in res.reason
    assert runner.calls[0]["timeout_sec"] == 1200
    kinds = [e["event"] for e in log]
    assert "phase-timeout" in kinds
    assert "review-complete" not in kinds


# --- browser_smoke ---------------------------------------------------------

STORY_TEXT = """\
# Story 3-4

## Acceptance Criteria
1. A search box appears.
2. Results rank by similarity.

## Tasks
- wire it
"""


def test_browser_smoke_passes_on_smoke_pass_marker():
    runner = MockRunner(
        [AgentResult(raw="", text="SMOKE_PASS: verified AC1 and AC2 in browser.", cost_usd=0.5)]
    )
    server = FakeServer(url="http://localhost:4137")
    log, emit = _events("smoke")
    res = browser_smoke(
        runner,
        "3-4",
        STORY_TEXT,
        emit=emit,
        cwd="/repo",
        server_ctl=server,
        gate_fn=lambda: gate(green=True, pass_=42),
        max_iters=3,
        timeout_sec=720,
        model="sonnet",
    )
    assert res.ok is True
    assert res.gate["pass"] == 42
    assert res.extra["url"] == "http://localhost:4137"
    # emits smoke-server then a passing smoke-iter
    assert log[0]["event"] == "smoke-server"
    assert log[0]["url"] == "http://localhost:4137"
    assert log[1]["event"] == "smoke-iter"
    assert log[1]["passed"] is True
    assert "SMOKE_PASS:" in log[1]["verdict"]
    # server always stopped
    assert server.started == 1
    assert server.stopped == 1
    # AC-aware prompt: the story's ACs are embedded
    assert "A search box appears" in runner.calls[0]["prompt"]
    assert "http://localhost:4137" in runner.calls[0]["prompt"]
    # chrome-devtools tool present
    assert "mcp__chrome-devtools__*" in runner.calls[0]["allowed_tools"]
    # the per-iter wall-clock timeout was forwarded to the runner
    assert runner.calls[0]["timeout_sec"] == 720


def test_browser_smoke_prompt_uses_configured_commands_not_hardcoded_bun():
    runner = MockRunner([AgentResult(raw="", text="SMOKE_PASS: ok", cost_usd=0.1)])
    log, emit = _events("smoke")
    res = browser_smoke(
        runner,
        "3-4",
        STORY_TEXT,
        emit=emit,
        cwd="/repo",
        server_ctl=FakeServer(url="http://localhost:4137"),
        gate_fn=lambda: gate(green=True),
        max_iters=1,
        timeout_sec=60,
        model="sonnet",
        dev_command="pnpm dev",
        gate_commands=["pnpm test", "pnpm lint"],
    )
    assert res.ok is True
    prompt = runner.calls[0]["prompt"]
    assert "'pnpm dev'" in prompt
    assert "'pnpm test' and 'pnpm lint'" in prompt
    assert "bun run" not in prompt


def test_browser_smoke_prompt_generic_fallback_without_commands():
    # No configured commands -> generic wording, never a hardcoded tool.
    runner = MockRunner([AgentResult(raw="", text="SMOKE_PASS: ok", cost_usd=0.1)])
    log, emit = _events("smoke")
    browser_smoke(
        runner,
        "3-4",
        STORY_TEXT,
        emit=emit,
        cwd="/repo",
        server_ctl=FakeServer(url="http://localhost:4137"),
        gate_fn=lambda: gate(green=True),
        max_iters=1,
        timeout_sec=60,
        model="sonnet",
    )
    prompt = runner.calls[0]["prompt"]
    assert "the dev-server command" in prompt
    assert "the project's test and lint commands" in prompt
    assert "bun run" not in prompt


def test_browser_smoke_timeout_emits_timed_out_and_stops_server():
    # Two consecutive timeouts -> stop (PS rule: smokeTimeouts >= 2).
    runner = MockRunner(
        [
            AgentResult(raw="", text="", timed_out=True, is_error=True),
            AgentResult(raw="", text="", timed_out=True, is_error=True),
        ]
    )
    server = FakeServer()
    log, emit = _events("smoke")
    res = browser_smoke(
        runner,
        "3-4",
        STORY_TEXT,
        emit=emit,
        cwd="/repo",
        server_ctl=server,
        gate_fn=lambda: gate(green=True),
        max_iters=3,
        timeout_sec=720,
        model="sonnet",
    )
    assert res.ok is False
    assert "TIMED OUT" in res.reason
    assert res.extra["timeouts"] == 2
    # both smoke-iter events flag timedOut
    smoke_iters = [e for e in log if e["event"] == "smoke-iter"]
    assert len(smoke_iters) == 2
    assert all(e["timedOut"] is True for e in smoke_iters)
    # server stopped despite the timeouts
    assert server.stopped == 1


def test_browser_smoke_stops_server_even_when_runner_raises():
    class BoomRunner(AgentRunner):
        name = "boom"

        def run(self, **kwargs):
            raise RuntimeError("boom")

    server = FakeServer()
    log, emit = _events("smoke")
    raised = False
    try:
        browser_smoke(
            BoomRunner(),
            "3-4",
            STORY_TEXT,
            emit=emit,
            cwd="/repo",
            server_ctl=server,
            gate_fn=lambda: gate(),
            max_iters=3,
            timeout_sec=720,
            model="sonnet",
        )
    except RuntimeError:
        raised = True
    assert raised is True
    # finally still ran -> server stopped
    assert server.stopped == 1


def test_browser_smoke_no_bound_port_is_not_ok():
    runner = MockRunner([])
    server = FakeServer(url=None)
    log, emit = _events("smoke")
    res = browser_smoke(
        runner,
        "3-4",
        STORY_TEXT,
        emit=emit,
        cwd="/repo",
        server_ctl=server,
        gate_fn=lambda: gate(),
        max_iters=3,
        timeout_sec=720,
        model="sonnet",
    )
    assert res.ok is False
    assert "bound port" in res.reason
    # no runner call (never got a url), but server.stop() still ran
    assert len(runner.calls) == 0
    assert server.stopped == 1


def test_browser_smoke_fail_then_no_progress_stops():
    # Same FAIL verdict twice -> no-progress guard stops after 2 iters (not 3).
    runner = MockRunner(
        [
            AgentResult(raw="", text="SMOKE_FAIL: AC2 broken on /search", cost_usd=0.2),
            AgentResult(raw="", text="SMOKE_FAIL: AC2 broken on /search", cost_usd=0.2),
            AgentResult(raw="", text="SMOKE_PASS: fixed", cost_usd=0.2),
        ]
    )
    server = FakeServer()
    log, emit = _events("smoke")
    res = browser_smoke(
        runner,
        "3-4",
        STORY_TEXT,
        emit=emit,
        cwd="/repo",
        server_ctl=server,
        gate_fn=lambda: gate(green=True),
        max_iters=3,
        timeout_sec=720,
        model="sonnet",
    )
    assert res.ok is False
    # stopped after the 2nd identical FAIL (no-progress), never reached the 3rd PASS
    assert len(runner.calls) == 2
    smoke_iters = [e for e in log if e["event"] == "smoke-iter"]
    assert len(smoke_iters) == 2
    assert all(e["passed"] is False for e in smoke_iters)


def test_browser_smoke_progress_sig_unchanged_stops_even_on_different_text():
    # With a git-signature probe injected, an UNCHANGED signature stops after the 2nd failed
    # iteration — even though the FAIL verdicts differ (so the text-fallback would NOT have
    # stopped). This is the git-parity fix: same tree, different words -> no real progress.
    runner = MockRunner(
        [
            AgentResult(raw="", text="SMOKE_FAIL: AC2 broken on /search", cost_usd=0.2),
            AgentResult(raw="", text="SMOKE_FAIL: AC2 STILL broken, tried a new fix", cost_usd=0.2),
            AgentResult(raw="", text="SMOKE_PASS: fixed", cost_usd=0.2),
        ]
    )
    server = FakeServer()
    log, emit = _events("smoke")
    res = browser_smoke(
        runner,
        "3-4",
        STORY_TEXT,
        emit=emit,
        cwd="/repo",
        server_ctl=server,
        gate_fn=lambda: gate(green=True),
        max_iters=3,
        timeout_sec=720,
        model="sonnet",
        progress_sig=lambda: "HEAD|constant-tree",  # never changes -> no code moved
    )
    assert res.ok is False
    # sig unchanged after the 2nd failed iter -> stop; never reached the 3rd PASS
    assert len(runner.calls) == 2


def test_browser_smoke_progress_sig_changed_continues_even_on_same_text():
    # A CHANGING signature means the agent DID move code, so smoke keeps going — even with an
    # identical repeated FAIL verdict that the text-fallback WOULD have stopped on. Proves the
    # injected git signature takes precedence over verdict-text equality.
    runner = MockRunner(
        [
            AgentResult(raw="", text="SMOKE_FAIL: AC2 broken on /search", cost_usd=0.2),
            AgentResult(raw="", text="SMOKE_FAIL: AC2 broken on /search", cost_usd=0.2),
            AgentResult(raw="", text="SMOKE_PASS: fixed on the third try", cost_usd=0.2),
        ]
    )
    server = FakeServer()
    log, emit = _events("smoke")
    sigs = iter(["HEAD|tree-a", "HEAD|tree-b", "HEAD|tree-c"])
    res = browser_smoke(
        runner,
        "3-4",
        STORY_TEXT,
        emit=emit,
        cwd="/repo",
        server_ctl=server,
        gate_fn=lambda: gate(green=True, pass_=7),
        max_iters=3,
        timeout_sec=720,
        model="sonnet",
        progress_sig=lambda: next(sigs),
    )
    # kept going through all 3 iters (sig moved each time) and passed on the 3rd
    assert res.ok is True
    assert len(runner.calls) == 3
    assert res.gate["pass"] == 7


def test_browser_smoke_red_gate_reason_names_failing_stage():
    # Smoke PASSES but the post-smoke gate is RED. The stop reason must surface the gate
    # SUMMARY (per-stage exit codes), matching the committed bmad-log fixtures — not a
    # raw[:120] slice that truncated inside the passing codegen stage and hid the failure.
    runner = MockRunner([AgentResult(raw="", text="SMOKE_PASS: looks good", cost_usd=0.1)])
    server = FakeServer()
    log, emit = _events("smoke")
    red = gate(green=False, pass_=25, fail=1)  # test stage exit=1; codegen/lint exit=0
    res = browser_smoke(
        runner,
        "3-4",
        STORY_TEXT,
        emit=emit,
        cwd="/repo",
        server_ctl=server,
        gate_fn=lambda: red,
        max_iters=3,
        timeout_sec=720,
        model="sonnet",
    )
    assert res.ok is False
    assert res.reason == "post-smoke gate red: codegen=0 lint=0 test=1  (25 pass / 1 fail)"
    # the FAILING stage is visible in the reason (the old raw[:120] hid it)
    assert "test=1" in res.reason
    assert server.stopped == 1


# --- smoke targets THIS story's changes + records per-AC evidence -----------


def test_routes_from_files_maps_next_pages():
    routes = phases._routes_from_files([
        "app/page.tsx", "app/settings/page.tsx", "app/(main)/dash/page.tsx",
        "app/blog/[slug]/page.tsx", "pages/about.tsx", "pages/index.tsx",
        "convex/search.ts", "lib/util.ts",
    ])
    assert "/" in routes
    assert "/settings" in routes
    assert "/dash" in routes          # route-group (main) dropped
    assert "/blog/:slug" in routes    # dynamic segment
    assert "/about" in routes
    # non-routed files contribute no route
    assert all("convex" not in r and "util" not in r for r in routes)


def test_browser_smoke_targets_changed_files_and_records_acs():
    runner = MockRunner([
        AgentResult(raw="", text=(
            "SMOKE_PASS: AC1 verified in browser; AC2 deferred to tests.\n"
            'SMOKE_ACS: {"verified":[{"ac":"AC1","how":"opened /search, typed query, results rendered",'
            '"evidence":"snapshot+console"}],"deferred":[{"ac":"AC2","why":"backend-only"}]}'
        ), cost_usd=0.3)
    ])
    server = FakeServer(url="http://localhost:4137")
    log, emit = _events("smoke")
    res = browser_smoke(
        runner, "3-4", STORY_TEXT, emit=emit, cwd="/repo", server_ctl=server,
        gate_fn=lambda: gate(green=True, pass_=42), max_iters=1, timeout_sec=720,
        model="sonnet", changed_files=["app/search/page.tsx", "convex/search.ts"],
    )
    assert res.ok is True
    # the changed files (+ derived route) steer the prompt at THIS story's actual surfaces
    p = runner.calls[0]["prompt"]
    assert "THIS STORY'S CHANGED FILES" in p
    assert "app/search/page.tsx" in p
    assert "/search" in p  # route derived from app/search/page.tsx
    # per-AC evidence parsed out of the SMOKE_ACS line into the smoke-iter event
    si = [e for e in log if e["event"] == "smoke-iter"][0]
    assert si["verifiedAcs"] == ["AC1"]
    assert si["deferredAcs"] == ["AC2"]


def test_browser_smoke_without_changed_or_acs_is_parity():
    # No changed_files -> no targeting block; no SMOKE_ACS line -> verified/deferred OMITTED.
    runner = MockRunner([AgentResult(raw="", text="SMOKE_PASS: healthy", cost_usd=0.1)])
    server = FakeServer()
    log, emit = _events("smoke")
    browser_smoke(
        runner, "3-4", STORY_TEXT, emit=emit, cwd="/repo", server_ctl=server,
        gate_fn=lambda: gate(green=True), max_iters=1, timeout_sec=720, model="sonnet",
    )
    assert "THIS STORY'S CHANGED FILES" not in runner.calls[0]["prompt"]
    si = [e for e in log if e["event"] == "smoke-iter"][0]
    assert "verifiedAcs" not in si
    assert "deferredAcs" not in si


# --- DevServer port parsing (pure helper; no process spawned) ---------------


def test_devserver_parse_port_prefers_local_then_url_then_localhost():
    assert phases.DevServer._parse_port("  Local:   http://localhost:4137") == 4137
    assert phases.DevServer._parse_port("ready on https://0.0.0.0:5173/") == 5173
    assert phases.DevServer._parse_port("listening localhost:8080 now") == 8080
    assert phases.DevServer._parse_port("no port here") is None
