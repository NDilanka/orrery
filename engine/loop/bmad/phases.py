"""Per-phase BMAD driver logic — ported from ``bmad-loop.ps1``, externals INJECTED.

Each phase is a plain function that takes:

- a :class:`loop.runners.base.AgentRunner` (``runner``) — the ONLY thing that spawns a real
  ``claude`` process;
- an ``emit(event_dict)`` callback — where the driver would ``Write-BLog``; tests pass a list
  ``.append``;
- a ``gate_fn()`` callable returning a ``run_gate``-style dict (``{green, pass, fail, total,
  stages, ...}``) — the authoritative external gate, injected so tests don't spawn ``bun``;
- (for smoke) a ``server_ctl`` with ``.start() -> url`` / ``.stop()`` — the dev server, injected
  so tests don't spawn a real Next.js/Convex process.

So the per-phase LOGIC (prompt construction, the QUESTION-marker Q&A loop, marker parsing,
iteration bounds, wall-clock timeout handling, always-stop-the-server) is fully unit-testable
with mocks. The driver (a later agent) composes these and owns git / PR / quota-wait.

This module also provides a REAL :class:`DevServer` (uses :mod:`loop.proc`) that the driver
will use in production — it spawns the dev-server command and parses the BOUND port from its
stdout (NOT assumed to be 3000, mirroring the Next.js auto-increment bug the PS source guards
against). It is kept OUT of the unit tests (tests inject a fake ``server_ctl``).

Ported faithfully from:
- ``Invoke-BmadPhase`` + the create-story prompt/retry (``bmad-loop.ps1`` ~736-756)
- the dev-story prompt + gate (``bmad-loop.ps1`` ~766-800)
- ``Invoke-CodeReviewPhase`` (~390-435)
- ``Invoke-BrowserSmokePhase`` + ``Stop-DevServer`` (~501-617)
"""

from __future__ import annotations

import re
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from loop import proc
from loop.events import (
    dev_gate_event,
    review_answer_event,
    review_complete_event,
    review_question_event,
    smoke_iter_event,
    smoke_server_event,
)
from loop.runners.base import AgentRunner
from loop.verdict import question_marker
from loop.bmad.story import story_acs

# The default BMAD tool allow-list (``$script:BmadTools`` in bmad-loop.ps1).
BMAD_TOOLS: tuple[str, ...] = (
    "Skill",
    "Read",
    "Edit",
    "Write",
    "Glob",
    "Grep",
    "Bash(bun:*)",
    "Bash(bunx:*)",
    "Bash(npx:*)",
    "Bash(node:*)",
    "Bash(convex:*)",
    "Bash(git:*)",
    "Bash(python:*)",
    "Bash(python3:*)",
)

# Smoke adds the chrome-devtools MCP tools (``$smokeTools`` in bmad-loop.ps1).
SMOKE_TOOLS: tuple[str, ...] = (*BMAD_TOOLS, "mcp__chrome-devtools__*")

# REVIEW_COMPLETE / RETRO marker — line beginning REVIEW_COMPLETE: (PS (?m)REVIEW_COMPLETE:?...).
_REVIEW_COMPLETE_RX = re.compile(r"^.*?REVIEW_COMPLETE:?\s*(.*)$", re.MULTILINE)
# SMOKE_PASS:/SMOKE_FAIL: verdict line (PS '(SMOKE_(?:PASS|FAIL):[^\r\n]*)').
_SMOKE_VERDICT_RX = re.compile(r"(SMOKE_(?:PASS|FAIL):[^\r\n]*)")
_SMOKE_PASS_RX = re.compile(r"SMOKE_PASS")

# Dev-server bound-port patterns: Next-style 'Local: http://localhost:PORT' AND a generic
# 'http(s)://...:PORT' / 'localhost:PORT' fallback (port is NOT assumed to be 3000).
_PORT_LOCAL_RX = re.compile(r"Local:\s+https?://localhost:(\d+)", re.IGNORECASE)
_PORT_URL_RX = re.compile(r"(https?://[^\s/]+):(\d+)", re.IGNORECASE)
_PORT_LOCALHOST_RX = re.compile(r"localhost:(\d+)", re.IGNORECASE)


@dataclass
class PhaseResult:
    """Normalized outcome of a phase.

    ``ok`` is the gate-style verdict the driver branches on; ``reason`` explains a non-ok
    stop (mirrors the PS ``@{ ok=$false; reason=... }`` hashtables). ``cost`` is the cumulative
    USD this phase spent (summed across every ``runner.run`` it made). ``gate`` carries the
    final ``run_gate`` result when the phase ran one. ``extra`` holds phase-specific data
    (e.g. the created story key, the smoke url, turn counts) for the driver/tests.
    """

    ok: bool
    reason: str | None = None
    cost: float = 0.0
    gate: dict[str, Any] | None = None
    extra: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# create-story
# ---------------------------------------------------------------------------

_CREATE_STORY_PROMPT = (
    "You are running HEADLESSLY via 'claude -p' — fully NON-INTERACTIVE. Do NOT greet, do NOT "
    "ask 'what would you like to work on', do NOT wait for input. IMMEDIATELY invoke the "
    "bmad-create-story skill and run it to completion: draft the next 'backlog' story from "
    "sprint-status.yaml into a full context-rich story file and set its status to "
    "'ready-for-dev'. Only an unavoidable HALT (missing document or genuinely ambiguous "
    "selection) is permitted; otherwise finish the story end to end."
)


def _looks_like_greeting(text: str | None) -> bool:
    """Heuristic: the model GREETED instead of invoking the skill (the PS retry trigger).

    The PS source detected this indirectly (no new ``ready-for-dev`` story appeared after the
    run). Here ``create_story`` cannot read sprint-status (no file I/O), so a ``produced``
    predicate is injected by the driver; when absent we fall back to this text heuristic so the
    retry-on-greeting behavior is exercised in tests.
    """
    if not text:
        return True
    t = text.strip().lower()
    if not t:
        return True
    greet = ("what would you like", "how can i help", "what can i help", "hello", "hi there")
    return any(g in t for g in greet)


def create_story(
    runner: AgentRunner,
    *,
    emit: Callable[[dict[str, Any]], None],
    cwd,
    model: str,
    allowed_tools=BMAD_TOOLS,
    permission_mode: str = "acceptEdits",
    max_turns: int = 0,
    produced: Callable[[], bool] | None = None,
    max_attempts: int = 3,
) -> PhaseResult:
    """Port of the create-story phase (``bmad-loop.ps1`` ~736-756).

    Run the faithful non-interactive create-story prompt, retrying up to ``max_attempts`` (PS:
    3) when the run produced no story — each fresh attempt self-heals a model that greeted
    instead of invoking the skill. ``produced()`` (injected) is the authoritative "did a
    ready-for-dev story appear?" check the driver wires to sprint-status; without it the phase
    falls back to a greeting heuristic on the run's text. Returns ``ok`` + cumulative cost; the
    created/last result text is in ``extra['text']`` and the attempt count in
    ``extra['attempts']``.
    """
    total_cost = 0.0
    last_text = ""
    for attempt in range(1, max_attempts + 1):
        res = runner.run(
            prompt=_CREATE_STORY_PROMPT,
            model=model,
            allowed_tools=list(allowed_tools),
            permission_mode=permission_mode,
            max_turns=max_turns,
            cwd=cwd,
        )
        total_cost += float(getattr(res, "cost_usd", 0.0) or 0.0)
        last_text = getattr(res, "text", "") or ""

        if produced is not None:
            made = bool(produced())
        else:
            made = not _looks_like_greeting(last_text)

        if made:
            return PhaseResult(
                ok=True,
                cost=total_cost,
                extra={"attempts": attempt, "text": last_text},
            )

    return PhaseResult(
        ok=False,
        reason=(
            f"create-story did not produce a 'ready-for-dev' story after "
            f"{max_attempts} attempts"
        ),
        cost=total_cost,
        extra={"attempts": max_attempts, "text": last_text},
    )


# ---------------------------------------------------------------------------
# dev-story
# ---------------------------------------------------------------------------

_DEV_STORY_PROMPT = (
    "You are running HEADLESSLY via 'claude -p' — NON-INTERACTIVE. Do NOT greet or ask what to "
    "work on; IMMEDIATELY invoke the bmad-dev-story skill. Implement the 'ready-for-dev' (or "
    "resume the 'in-progress') story in sprint-status.yaml using its Tasks/Subtasks with TDD. "
    "Run continuously to completion; do not stop for milestones. Honor all BMAD HALT "
    "conditions. On completion set the story to 'review'."
)


def _stage_ok(gate: dict[str, Any], name: str) -> bool:
    """True if the named gate stage exited 0; False if it ran-and-failed or is absent.

    The external gate (``run_gate``) reports per-stage ``{name, ok, exit}``; bmad-loop's
    ``dev-gate`` event carries codegenOk/lintOk/testOk derived from the codegen/lint/test
    stages. A stage that didn't run is reported as not-ok (conservative, like a non-zero exit).
    """
    for s in gate.get("stages", []) or []:
        if s.get("name") == name:
            return bool(s.get("ok"))
    return False


def dev_story(
    runner: AgentRunner,
    story: str,
    *,
    emit: Callable[[dict[str, Any]], None],
    cwd,
    gate_fn: Callable[[], dict[str, Any]],
    model: str,
    allowed_tools=BMAD_TOOLS,
    permission_mode: str = "acceptEdits",
    max_turns: int = 0,
    baseline_pass: int = 0,
    cum: float = 0.0,
    status: str = "review",
) -> PhaseResult:
    """Port of the dev-story phase (``bmad-loop.ps1`` ~766-800).

    Run the dev-story prompt (implement ready / resume in-progress with TDD; honor BMAD HALT;
    set status ``review`` on completion), then run the injected ``gate_fn()`` and ``emit`` a
    ``dev-gate`` event whose codegen/lint/test booleans come from the gate's per-stage results.
    ``ok`` is the gate's ``green``. The story's resulting ``status`` is passed through (the
    driver reads it from the story file after the run); ``cum`` is the running cumulative cost
    the driver feeds in for the event. Returns the gate in ``.gate``.
    """
    res = runner.run(
        prompt=_DEV_STORY_PROMPT,
        model=model,
        allowed_tools=list(allowed_tools),
        permission_mode=permission_mode,
        max_turns=max_turns,
        cwd=cwd,
    )
    cost = float(getattr(res, "cost_usd", 0.0) or 0.0)

    gate = gate_fn()
    codegen_ok = _stage_ok(gate, "codegen")
    lint_ok = _stage_ok(gate, "lint")
    test_ok = _stage_ok(gate, "test")

    emit(
        dev_gate_event(
            story=story,
            cum=cum + cost,
            green=bool(gate.get("green")),
            pass_=int(gate.get("pass", 0)),
            fail=int(gate.get("fail", 0)),
            total=int(gate.get("total", 0)),
            baseline_pass=baseline_pass,
            status=status,
            codegen_ok=codegen_ok,
            lint_ok=lint_ok,
            test_ok=test_ok,
        )
    )

    ok = bool(gate.get("green"))
    return PhaseResult(
        ok=ok,
        reason=None if ok else f"dev-story gate not green for {story}",
        cost=cost,
        gate=gate,
        extra={"status": status, "text": getattr(res, "text", "") or ""},
    )


# ---------------------------------------------------------------------------
# code-review (QUESTION-marker Q&A loop)
# ---------------------------------------------------------------------------

_CODE_REVIEW_PROMPT_TEMPLATE = (
    "Use the bmad-code-review skill to review story {story} — the changes since its "
    "baseline_commit.\n"
    "You are running NON-INTERACTIVELY. Follow this protocol exactly:\n"
    "- When you reach a decision point where you would normally ask the user how to handle a "
    "finding,\n"
    "  output EXACTLY one line beginning 'QUESTION:' containing the finding and the options, "
    "then STOP and\n"
    "  end your turn. Exactly one question per turn. Never ask the same question twice.\n"
    "- When you receive the answer, apply it and continue the review.\n"
    "- When the review is fully complete and the story status is updated, output one line "
    "beginning\n"
    "  'REVIEW_COMPLETE:' with a one-line summary, then stop."
)


def code_review(
    runner: AgentRunner,
    decider: Callable[..., str],
    story: str,
    *,
    emit: Callable[[dict[str, Any]], None],
    cwd,
    gate_fn: Callable[[], dict[str, Any]],
    max_turns: int,
    model: str,
    decider_model: str = "haiku",
    allowed_tools=BMAD_TOOLS,
    permission_mode: str = "acceptEdits",
) -> PhaseResult:
    """Port of ``Invoke-CodeReviewPhase`` (``bmad-loop.ps1`` ~390-435).

    Run the ``bmad-code-review`` skill, then loop while the agent emits a ``QUESTION:`` marker
    (detected by :func:`loop.verdict.question_marker`), bounded by ``max_turns``:

    1. ``emit`` a ``review-question`` event,
    2. get an answer from ``decider`` (the ``review_decider`` Q&A) and ``emit`` a
       ``review-answer`` event,
    3. feed the answer back via ``--resume`` (a fresh ``runner.run`` with ``resume_session``).

    On a ``REVIEW_COMPLETE:`` marker — or NO marker at all (treat as complete, don't spin) —
    ``emit`` a ``review-complete`` event and run the injected ``gate_fn()``. Exceeding
    ``max_turns`` (a question loop) is a non-ok stop. Returns the gate in ``.gate``.
    """
    prompt = _CODE_REVIEW_PROMPT_TEMPLATE.format(story=story)
    session_id: str | None = None
    answer: str | None = None
    total_cost = 0.0

    for turn in range(1, max_turns + 1):
        if session_id is None:
            res = runner.run(
                prompt=prompt,
                model=model,
                allowed_tools=list(allowed_tools),
                permission_mode=permission_mode,
                max_turns=0,
                cwd=cwd,
            )
        else:
            res = runner.run(
                prompt=answer or "",
                model=model,
                allowed_tools=list(allowed_tools),
                permission_mode=permission_mode,
                max_turns=0,
                cwd=cwd,
                resume_session=session_id,
            )
        total_cost += float(getattr(res, "cost_usd", 0.0) or 0.0)

        if getattr(res, "is_error", False) or getattr(res, "parse_failed", False):
            return PhaseResult(
                ok=False,
                reason="unparseable code-review output",
                cost=total_cost,
                extra={"turns": turn},
            )

        if session_id is None:
            session_id = getattr(res, "session_id", None)

        text = getattr(res, "text", "") or ""

        cm = _REVIEW_COMPLETE_RX.search(text)
        if cm:
            summary = cm.group(1).strip()
            emit(review_complete_event(turn=turn, summary=summary))
            return _review_finish(gate_fn, total_cost, turn, summary)

        q = question_marker(text)
        if q is not None:
            emit(review_question_event(turn=turn, q=q, story=story))
            answer = decider(
                runner, question=q, story_scope=story, model=decider_model
            )
            emit(review_answer_event(turn=turn, a=answer))
            continue

        # No protocol marker — treat as complete (no decisions) rather than spin.
        return _review_finish(gate_fn, total_cost, turn, "no-marker")

    return PhaseResult(
        ok=False,
        reason=f"code-review exceeded {max_turns} turns (possible question loop)",
        cost=total_cost,
        extra={"turns": max_turns},
    )


def _review_finish(
    gate_fn: Callable[[], dict[str, Any]],
    cost: float,
    turn: int,
    summary: str,
) -> PhaseResult:
    """Run the post-review gate and build the terminal PhaseResult for code-review."""
    gate = gate_fn()
    ok = bool(gate.get("green"))
    return PhaseResult(
        ok=ok,
        reason=None if ok else f"post-code-review gate red: {gate.get('raw', '')[:120]}",
        cost=cost,
        gate=gate,
        extra={"turns": turn, "summary": summary},
    )


# ---------------------------------------------------------------------------
# browser-smoke
# ---------------------------------------------------------------------------


def _build_smoke_prompt(url: str, story: str, cwd, acs: str) -> str:
    """Build the AC-aware smoke prompt (``bmad-loop.ps1`` ~549-579)."""
    if acs:
        ac_block = (
            "This story's ACCEPTANCE CRITERIA (verify each in the browser where it has a UI "
            f"surface):\n{acs}"
        )
    else:
        ac_block = (
            "(No Acceptance Criteria section found in the story file — fall back to a general "
            "health check.)"
        )
    return (
        f"THIS app (Next.js + Convex, repo at {cwd}) is ALREADY RUNNING at exactly {url}.\n"
        f"Use ONLY {url}. Do NOT start, restart, or hunt for the dev server on any other port, "
        "and do NOT run\n"
        "'bun run dev' yourself (it is already running and would block).\n\n"
        f"You are doing STORY-AC-AWARE browser verification of story {story}, not just a health "
        "check.\n\n"
        f"{ac_block}\n\n"
        "Auth note: the app uses Clerk. The browser may carry a persisted session (then you "
        "can drive the\n"
        "authenticated flows directly) or be unauthenticated (then '/' and gated routes "
        "legitimately 401/404 —\n"
        "that is NOT a failure; verify what you can on the public surface + sign-in/onboarding)."
        "\n\n"
        "Using the chrome-devtools MCP tools:\n"
        f"1) open {url}, take a snapshot, read the console + network.\n"
        "2) For each AC that has a UI behavior, DRIVE it (navigate, click, type, submit) and "
        "confirm the\n"
        "   expected result in the DOM/console/network. For ACs that are purely backend (server "
        "actions,\n"
        "   validation, rate limits, audit writes — no UI), state they are covered by the "
        "automated test suite\n"
        "   and skip browser checks for them.\n"
        "3) If a UI-verifiable AC fails OR there is a real crash/5xx/broken page, FIX it with "
        "the smallest code\n"
        f"   change, then RELOAD {url} and RE-TEST. After any code change run 'bun run test' and "
        "'bun run lint'\n"
        "   (no regression). Benign warnings / favicon 404 / auth redirects are NOT failures.\n\n"
        "DO NOT BLOCK: if a page never becomes idle / the tab is unresponsive / content keeps "
        "re-rendering\n"
        "(a runaway render or animation loop), do NOT wait on it — take an immediate snapshot, "
        "treat it as a\n"
        "real issue (fix it if you can, else 'SMOKE_FAIL: runaway render on <route>'). Never "
        "wait indefinitely.\n\n"
        "Output exactly one line:\n"
        "'SMOKE_PASS:' + which ACs you verified in the browser (and which were deferred to "
        "tests as backend-only), if all UI-verifiable ACs hold and the app is healthy; OR\n"
        "'SMOKE_FAIL:' + the specific AC or behavior that failed and could not be fixed."
    )


def browser_smoke(
    runner: AgentRunner,
    story: str,
    story_text: str,
    *,
    emit: Callable[[dict[str, Any]], None],
    cwd,
    server_ctl,
    gate_fn: Callable[[], dict[str, Any]],
    max_iters: int,
    timeout_sec: int,
    model: str,
    allowed_tools=SMOKE_TOOLS,
    permission_mode: str = "acceptEdits",
    root_code: int = 200,
) -> PhaseResult:
    """Port of ``Invoke-BrowserSmokePhase`` (``bmad-loop.ps1`` ~508-617).

    Start the injected ``server_ctl`` (``.start() -> url`` — the REAL impl parses the bound
    port from the dev-server stdout; tests inject a fake), ``emit`` a ``smoke-server`` event,
    then build the AC-aware prompt from :func:`loop.bmad.story.story_acs` of ``story_text`` and
    loop up to ``max_iters``:

    - ``runner.run`` with the chrome-devtools tools and a per-iteration wall-clock
      ``timeout_sec``;
    - on timeout: ``emit`` ``smoke-iter(timedOut=True)`` and (PS rule) stop after the 2nd
      consecutive timeout;
    - else parse the ``SMOKE_PASS:``/``SMOKE_FAIL:`` marker, ``emit`` ``smoke-iter(passed,
      verdict)``, and stop on PASS.

    ALWAYS calls ``server_ctl.stop()`` in a ``finally``. On a pass, runs the injected
    ``gate_fn()`` and returns it in ``.gate``; a non-pass / exhausted / timed-out run is a
    non-ok stop. The bound ``url`` is in ``extra['url']``.
    """
    total_cost = 0.0
    smoke_ok = False
    smoke_timeouts = 0
    last_verdict: str | None = None
    iters_done = 0
    url: str | None = None

    try:
        url = server_ctl.start()
        if not url:
            return PhaseResult(
                ok=False,
                reason="dev server never reported a bound port",
                cost=total_cost,
            )
        emit(smoke_server_event(url=url, root_code=root_code))

        acs = story_acs(story_text)
        prompt = _build_smoke_prompt(url, story, cwd, acs)

        for s in range(1, max_iters + 1):
            iters_done = s
            res = runner.run(
                prompt=prompt,
                model=model,
                allowed_tools=list(allowed_tools),
                permission_mode=permission_mode,
                max_turns=0,
                cwd=cwd,
                timeout_sec=timeout_sec,
            )
            total_cost += float(getattr(res, "cost_usd", 0.0) or 0.0)

            if getattr(res, "timed_out", False):
                smoke_timeouts += 1
                emit(smoke_iter_event(iter_=s, passed=False, verdict="", timed_out=True))
                if smoke_timeouts >= 2:
                    break
                continue

            text = getattr(res, "text", "") or ""
            vm = _SMOKE_VERDICT_RX.search(text)
            verdict = (
                vm.group(1).strip()
                if vm
                else re.sub(r"\r?\n", " ", text).strip()
            )
            if len(verdict) > 280:
                verdict = verdict[:280]
            passed = bool(_SMOKE_PASS_RX.search(text))
            emit(smoke_iter_event(iter_=s, passed=passed, verdict=verdict))

            if passed:
                smoke_ok = True
                break

            # no-progress guard: the driver re-checks the git signature; here, a repeated
            # identical FAIL verdict means the agent made no headway -> stop to avoid spin.
            if verdict == last_verdict:
                break
            last_verdict = verdict
    finally:
        server_ctl.stop()

    if not smoke_ok:
        if smoke_timeouts >= 2:
            reason = (
                f"browser smoke TIMED OUT {smoke_timeouts}x (agent hung on the page — likely a "
                "runaway render/animation loop)"
            )
        else:
            reason = f"browser smoke did not pass within {max_iters} iters"
        return PhaseResult(
            ok=False,
            reason=reason,
            cost=total_cost,
            extra={"url": url, "iters": iters_done, "timeouts": smoke_timeouts},
        )

    gate = gate_fn()
    ok = bool(gate.get("green"))
    return PhaseResult(
        ok=ok,
        reason=None if ok else f"post-smoke gate red: {gate.get('raw', '')[:120]}",
        cost=total_cost,
        gate=gate,
        extra={"url": url, "iters": iters_done, "timeouts": smoke_timeouts},
    )


# ---------------------------------------------------------------------------
# REAL DevServer (production server_ctl) — uses loop.proc; NOT in the unit tests
# ---------------------------------------------------------------------------


class DevServer:
    """Production ``server_ctl``: spawn the dev-server command, parse its BOUND port, stop it.

    ``start()`` spawns ``argv`` (e.g. ``["bun", "run", "dev"]``) as a detached process with its
    stdout captured to a background reader thread, then waits up to ``startup_timeout_sec`` for
    a line revealing the bound port. The port is NOT assumed to be 3000 (Next.js auto-increments
    past a taken port — the exact bug the PS source guards against): it is read from the server's
    own output via, in order, ``Local: http(s)://localhost:PORT``, a generic
    ``http(s)://host:PORT``, or a bare ``localhost:PORT``. Returns the ``http://localhost:PORT``
    URL (or ``None`` if no port appeared in time).

    ``stop()`` tree-kills the spawned process (``loop.proc.kill_tree``) and reaps stray
    dev-server / chrome processes by name (``loop.proc.kill_by_name``) — the cross-platform
    replacement for the PS ``taskkill /T /F`` + ``Get-NetTCPConnection`` cleanup.

    Kept OUT of the unit tests (tests inject a fake ``server_ctl``); only the driver uses it.
    """

    def __init__(
        self,
        argv,
        *,
        cwd=None,
        env=None,
        startup_timeout_sec: float = 150.0,
        poll_interval_sec: float = 0.5,
        stray_names=("node", "chrome"),
    ):
        self.argv = list(argv)
        self.cwd = cwd
        self.env = env
        self.startup_timeout_sec = startup_timeout_sec
        self.poll_interval_sec = poll_interval_sec
        self.stray_names = tuple(stray_names)
        self._proc = None
        self._reader: threading.Thread | None = None
        self._buf: list[str] = []
        self._lock = threading.Lock()
        self.port: int | None = None
        self.url: str | None = None

    def _read_stdout(self) -> None:
        stream = self._proc.stdout if self._proc else None
        if stream is None:
            return
        try:
            for line in stream:
                with self._lock:
                    self._buf.append(line)
        except (ValueError, OSError):
            pass

    @staticmethod
    def _parse_port(text: str) -> int | None:
        m = _PORT_LOCAL_RX.search(text)
        if m:
            return int(m.group(1))
        m = _PORT_URL_RX.search(text)
        if m:
            return int(m.group(2))
        m = _PORT_LOCALHOST_RX.search(text)
        if m:
            return int(m.group(1))
        return None

    def start(self) -> str | None:
        """Spawn the dev server and return its bound ``http://localhost:PORT`` URL (or None)."""
        import subprocess

        popen_kwargs: dict = {
            "cwd": self.cwd,
            "env": self.env,
            "stdout": subprocess.PIPE,
            "stderr": subprocess.STDOUT,
            "universal_newlines": True,
            "encoding": "utf-8",
            "errors": "replace",
            "bufsize": 1,
        }
        import os

        if os.name == "nt":
            popen_kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
        else:
            popen_kwargs["start_new_session"] = True

        self._proc = subprocess.Popen(self.argv, **popen_kwargs)
        self._reader = threading.Thread(target=self._read_stdout, daemon=True)
        self._reader.start()

        deadline = time.monotonic() + self.startup_timeout_sec
        while time.monotonic() < deadline:
            with self._lock:
                snapshot = "".join(self._buf)
            port = self._parse_port(snapshot)
            if port:
                self.port = port
                self.url = f"http://localhost:{port}"
                return self.url
            if self._proc.poll() is not None:
                break  # server exited before binding a port
            time.sleep(self.poll_interval_sec)
        return None

    def stop(self) -> None:
        """Tree-kill the dev server and reap stray dev-server/browser processes by name."""
        if self._proc is not None:
            try:
                proc.kill_tree(self._proc.pid, include_parent=True)
            except Exception:
                pass
        for name in self.stray_names:
            try:
                proc.kill_by_name(name)
            except Exception:
                pass
