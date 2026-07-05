"""Compact failure-feedback extraction — the cheapest high-leverage loop win.

This is the SWE-agent / Self-Debug research insight made concrete: when the gate is red,
the worker does NOT need the whole multi-thousand-line test log. It needs the FIRST failing
test, its assertion/expectation message, and a ``file:line`` to open. Feeding only that
compact signal back (into the volatile steer that :mod:`orrery_loop.prompts` keeps OUT of the cached
prefix) is far cheaper per token and empirically steers the fix better than a log dump.

Everything here is PURE text parsing: stdlib :mod:`re` only, no I/O, no network, no process
spawning. The two public functions are:

- :func:`extract_first_failure` — over a single runner's raw output.
- :func:`compact_feedback` — convenience over a :func:`orrery_loop.gate.run_gate` result, which
  slices out the FIRST failing stage's section first (so a lint/type pre-stage surfaces its
  own precise error, not a downstream test's).

Supported runner dialects (each regex below is tagged with the runner it targets):
bun test, vitest, jest, pytest, go test, plus a generic ``error``/``failed`` fallback.
"""

from __future__ import annotations

import re

# ---------------------------------------------------------------------------------------
# ANSI strip — local copy of the gate's strip so this module imports nothing from gate.py
# (keeps it a leaf module; the byte pattern is identical to ``orrery_loop.gate.strip_ansi``).
# ---------------------------------------------------------------------------------------
_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


def _strip_ansi(text: str) -> str:
    return _ANSI_RE.sub("", text)


# ---------------------------------------------------------------------------------------
# file:line locators — shared across dialects. We prefer a source path with a line number.
# ---------------------------------------------------------------------------------------

# JS/TS stack frame, e.g. "at file:///…/calc.test.ts:12:7" or "  at Object.<anonymous>
# (src/calc.test.ts:12:7)". Targets bun / vitest / jest stack lines. Captures "path:line".
_JS_AT_RE = re.compile(
    r"""(?ix)            # case-insensitive, verbose
    \bat\b              # the literal stack-frame keyword 'at'
    [^\n]*?             # optional 'Object.<anonymous> (' etc.
    (?:file://)?        # vitest/node sometimes emit a file:// URL
    (?P<path>           # ---- path: at least one non-space, non-paren, non-colon run ----
        [^\s():]+        # the path body
        \.[A-Za-z]+      # a file extension (.ts/.js/.tsx/.go/.py…)
    )
    :(?P<line>\d+)      # :line
    (?::\d+)?           # optional :column
    """
)

# pytest location, e.g. "test_calc.py:12: AssertionError" or "src/pkg/test_x.py:8:". The
# trailing colon (with optional column) is the pytest convention. Targets pytest.
_PYTEST_LOC_RE = re.compile(
    r"""(?x)
    (?P<path>[^\s:]+\.py)   # a .py path with no spaces
    :(?P<line>\d+)          # :line
    (?::\d+)?               # optional :col (rare)
    :                       # pytest's trailing colon before the message
    """
)

# Generic "<path>:<line>" fallback used only when no dialect-specific locator hits. Anchored
# to a plausible source extension so we don't grab "10:00:00" timestamps.
_GENERIC_LOC_RE = re.compile(
    r"(?<![\d:])(?P<path>[\w./\\-]+\.[A-Za-z]{1,5}):(?P<line>\d+)(?![\d])"
)


def _find_file_line(text: str) -> str:
    """Return the first ``file:line`` found, trying dialect locators then a generic one."""
    for rx in (_JS_AT_RE, _PYTEST_LOC_RE, _GENERIC_LOC_RE):
        m = rx.search(text)
        if m:
            return f"{m.group('path')}:{m.group('line')}"
    return ""


# ---------------------------------------------------------------------------------------
# Per-dialect extractors. Each returns a list of compact lines, or [] if it doesn't match.
# They are tried in order by extract_first_failure; the first non-empty result wins.
# ---------------------------------------------------------------------------------------

# pytest summary header for an individual failure: "FAILED test_x.py::test_name - reason"
# (with -ra / short summary) OR the section banner "____ test_name ____". Targets pytest.
_PYTEST_FAILED_RE = re.compile(
    r"(?m)^(?:FAILED|ERROR)\s+(?P<name>\S+?)(?:\s+-\s+(?P<reason>.*))?$"
)
_PYTEST_SECTION_RE = re.compile(r"(?m)^_{3,}\s+(?P<name>.+?)\s+_{3,}\s*$")
# pytest assertion lines: the "E   …" error rows and bare "assert …" rows.
_PYTEST_E_RE = re.compile(r"(?m)^E\s{2,}(?P<msg>.+)$")
_PYTEST_ASSERT_RE = re.compile(r"(?m)^\s*assert\b(?P<msg>.*)$")


def _extract_pytest(text: str) -> list[str]:
    name = ""
    reason = ""
    m = _PYTEST_FAILED_RE.search(text)
    if m:
        name = m.group("name")
        reason = (m.group("reason") or "").strip()
    else:
        ms = _PYTEST_SECTION_RE.search(text)
        if ms:
            name = ms.group("name").strip()

    # The assertion detail: first "E   …" row, else first "assert …" row.
    detail = ""
    me = _PYTEST_E_RE.search(text)
    if me:
        detail = me.group("msg").strip()
    elif (ma := _PYTEST_ASSERT_RE.search(text)) is not None:
        detail = ("assert" + ma.group("msg")).strip()

    if not (name or detail or reason):
        return []

    lines = ["pytest: FAILED " + name if name else "pytest: failure"]
    if reason:
        lines.append(reason)
    if detail and detail != reason:
        lines.append(detail)
    return lines


# A "✗/✕/×" bullet bullet-cross set shared by bun (✗ ×) and jest (✕). Kept as one class so
# we accept whichever cross glyph a runner emits. ASCII 'x' is intentionally excluded — it is
# far too common in prose to use as a failure marker.
_CROSS = "✗✘✕×"
# bun test case marker: "(fail) suite > case" or a leading cross bullet. Targets bun.
_BUN_FAIL_RE = re.compile(rf"(?m)^\s*(?:\(fail\)|[{_CROSS}])\s+(?P<name>.+?)\s*$")
# jest case marker: the "● suite › test name" bullet, or a "✕ test name" row. Targets jest.
_JEST_NAME_RE = re.compile(rf"(?m)^\s*(?:●\s+(?P<dot>.+?)|[{_CROSS}]\s+(?P<cross>.+?))\s*$")
# vitest/jest file-level marker: a "FAIL  file > name" line. The "> name" part is the test
# name (vitest); without it we only have the filename (jest's top FAIL line). Targets both.
_JS_FAIL_FILE_RE = re.compile(
    r"(?m)^\s*FAIL\b\s*(?P<file>\S+)?(?:\s*(?:>|›)\s*(?P<vname>.+?))?\s*$"
)
# expect()-style assertion line shared by bun/vitest/jest. Captures the whole expect row.
_EXPECT_RE = re.compile(r"(?m)^\s*(?P<msg>(?:expect\(|Expected:|Received:|AssertionError).*)$")


def _extract_js(text: str) -> list[str]:
    """bun test / vitest / jest share the expect()/stack-frame shape.

    Name resolution prefers the most specific test-name marker available — bun's "(fail) …",
    jest's "● suite › test" / "✕ test", vitest's "FAIL file > test" — and only falls back to
    a bare filename from a top-level "FAIL  file.js" line when no test name was found.
    """
    name = ""
    mb = _BUN_FAIL_RE.search(text)
    if mb:
        name = mb.group("name").strip()
    if not name:
        mj = _JEST_NAME_RE.search(text)
        if mj:
            name = (mj.group("dot") or mj.group("cross") or "").strip()
    if not name:
        mf = _JS_FAIL_FILE_RE.search(text)
        if mf:
            name = (mf.group("vname") or mf.group("file") or "").strip()

    expect_lines: list[str] = []
    for me in _EXPECT_RE.finditer(text):
        expect_lines.append(me.group("msg").strip())
        if len(expect_lines) >= 3:  # an expect()+Expected:+Received: trio is plenty
            break

    if not (name or expect_lines):
        return []

    lines = [f"FAIL {name}" if name else "FAIL"]
    lines.extend(expect_lines)
    return lines


# go test per-test failure: "--- FAIL: TestName (0.00s)". Targets go test.
_GO_FAIL_RE = re.compile(r"(?m)^\s*--- FAIL:\s+(?P<name>\S+)(?:\s+\([^)]*\))?\s*$")
# go test detail line: the indented "file_test.go:12: message" under the FAIL marker.
_GO_DETAIL_RE = re.compile(r"(?m)^\s*(?P<path>\S+\.go):(?P<line>\d+):\s*(?P<msg>.*)$")


def _extract_go(text: str) -> list[str]:
    m = _GO_FAIL_RE.search(text)
    if not m:
        return []
    lines = [f"--- FAIL: {m.group('name')}"]
    md = _GO_DETAIL_RE.search(text)
    if md:
        msg = md.group("msg").strip()
        loc = f"{md.group('path')}:{md.group('line')}"
        lines.append(f"{loc}: {msg}" if msg else loc)
    return lines


# Generic fallback: first lines that look like an error. Targets lint/type stages and any
# runner not otherwise recognized (the "lint/type pre-gate feeds back precise errors" point).
# Two signals: (a) an 'error'/'fail…' word (prefix-matched so 'errors'/'failed'/'failing'
# all count), or (b) a "path:line:col:" diagnostic prefix as ruff/tsc/eslint/gcc emit.
_GENERIC_ERR_RE = re.compile(
    r"(?im)^.*(?:\b(?:error|fail\w*)\b|^[\w./\\-]+\.[A-Za-z]{1,5}:\d+:\d+:).*$"
)


def _extract_generic(text: str) -> list[str]:
    out: list[str] = []
    for m in _GENERIC_ERR_RE.finditer(text):
        line = m.group(0).strip()
        if line:
            out.append(line)
        if len(out) >= 3:  # a couple of error lines is enough signal
            break
    return out


# Order matters: dialect-specific extractors first, generic last. Each is (probe, extractor)
# where probe is a cheap presence check that gates the more expensive extractor.
_DIALECTS: tuple[tuple[re.Pattern[str], object], ...] = (
    # pytest: "FAILED …::…" / "E   …" / "____ name ____" / ".py:NN:" are unmistakable.
    (re.compile(r"(?m)^(?:FAILED|ERROR)\s|\bassert\b|^E\s{2,}|_{3,}\s", re.M), _extract_pytest),
    # go test: the "--- FAIL:" marker is unique to go.
    (re.compile(r"(?m)^\s*--- FAIL:"), _extract_go),
    # bun/vitest/jest: expect(/Expected:/cross-bullet/FAIL file markers.
    (
        re.compile(rf"(?m)(?:^\s*(?:\(fail\)|FAIL\b|●|[{_CROSS}])|expect\(|Expected:)", re.M),
        _extract_js,
    ),
)


def extract_first_failure(raw: str, *, max_chars: int = 1200) -> str:
    """Return a COMPACT block describing the FIRST failure in ``raw``, or ``""`` if none.

    ``raw`` is a single runner's combined stdout+stderr (ANSI may be present; it is stripped
    first). The block names the failing test/symbol, its assertion/expectation message, and a
    ``file:line`` when one is present, then is truncated to ``max_chars``.

    Dialects are tried in order — pytest, go test, bun/vitest/jest — and the first that
    matches wins; if none match, a generic ``error``/``failed`` line scan is the fallback.
    """
    if not raw:
        return ""
    text = _strip_ansi(raw)

    lines: list[str] = []
    for probe, extractor in _DIALECTS:
        if probe.search(text):
            lines = extractor(text)  # type: ignore[operator]
            if lines:
                break

    if not lines:
        lines = _extract_generic(text)

    if not lines:
        return ""

    # Append a file:line if we found one and it isn't already shown in the chosen lines.
    loc = _find_file_line(text)
    if loc and not any(loc in ln for ln in lines):
        lines.append(f"at {loc}")

    block = "\n".join(lines).strip()
    if len(block) > max_chars:
        # Hard truncate with an ellipsis marker so the worker knows it was clipped.
        block = block[: max(0, max_chars - 1)].rstrip() + "…"
    return block


# Section header that ``run_gate`` writes per stage: "### stage '<name>' (exit=<n>)".
# We split on it to recover each stage's own raw slice. Mirrors gate.py line 157.
_STAGE_HEADER_RE = re.compile(
    r"(?m)^### stage '(?P<name>.*?)' \(exit=(?P<exit>-?\d+)\)\s*$"
)


def _split_stage_sections(raw: str) -> list[tuple[str, int, str]]:
    """Split a ``run_gate`` ``raw`` into ``[(name, exit, body), …]`` by stage header."""
    sections: list[tuple[str, int, str]] = []
    matches = list(_STAGE_HEADER_RE.finditer(raw))
    for i, m in enumerate(matches):
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(raw)
        body = raw[start:end].strip("\n")
        sections.append((m.group("name"), int(m.group("exit")), body))
    return sections


def compact_feedback(gate_result: dict, *, max_chars: int = 1200) -> str:
    """Compact the FIRST failing stage of a :func:`orrery_loop.gate.run_gate` result, or ``""``.

    If the gate is ``green`` (or has no failing stage) returns ``""``. Otherwise it finds the
    first stage with ``ok is False``, slices that stage's section out of ``raw`` (which
    ``run_gate`` builds as ``### stage '<name>' (exit=..)`` blocks), and runs
    :func:`extract_first_failure` over only that slice. Slicing first is what lets a lint or
    type pre-stage surface ITS specific error instead of a later test stage's noise.
    """
    if not gate_result or gate_result.get("green"):
        return ""

    stages = gate_result.get("stages") or []
    first_failed = next((s for s in stages if s.get("ok") is False), None)
    if first_failed is None:
        return ""

    raw = gate_result.get("raw") or ""
    sections = _split_stage_sections(raw)

    target_body = ""
    if sections:
        failed_name = first_failed.get("name")
        # Match the failing stage's section by name; fall back to the first failing-exit
        # section, then to the whole raw if headers were absent/garbled.
        by_name = next((b for (n, _e, b) in sections if n == failed_name), None)
        if by_name is not None:
            target_body = by_name
        else:
            by_exit = next((b for (_n, e, b) in sections if e != 0), None)
            target_body = by_exit if by_exit is not None else raw
    else:
        target_body = raw

    return extract_first_failure(target_body, max_chars=max_chars)
