"""The eval gate — verbatim ports of ``Get-GateCounts`` and ``Invoke-Gate``.

Faithful Python port of the multi-stage gate parser (loopcore.ps1 ~lines 99-181).

Semantics locked from the PowerShell:

- ``parse_counts`` is a PURE regex parser: it extracts the first capture group of the
  pass / fail patterns. ``matched`` is True if EITHER pattern hit. The PS patterns such as
  ``(\\d+)\\s+pass`` port directly to Python ``re``.
- ``run_gate`` runs an ordered list of stages. A stage's ``command`` may be a string (run
  via the shell) OR a Python callable (the test/extension hook — mirrors the PS scriptblock
  path, returning ``(output_text, exit_code)`` so tests inject captured output without
  spawning a process).
- **Exit code is truth.** Green = every stage exited 0. ``pass`` / ``fail`` / ``total`` come
  from the LAST stage that reported any counts, so a no-count pre-stage (codegen / lint)
  does not zero the real totals.
- ANSI escape sequences (``\\x1b[...m``) are stripped before parsing, mirroring the strip in
  ``loopcore.ps1`` line 409.

No secrets, no network. The only side effect is the optional shell exec for string commands.
"""

from __future__ import annotations

import re
import subprocess
from collections.abc import Callable
from typing import Any, Union

# ANSI strip — port of the PowerShell ``-replace "\x1b\[[0-9;]*m", ""`` (loopcore.ps1:409).
_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")

# Default patterns reproduce the original single ``bun test`` stage exactly.
_DEFAULT_PASS = r"(\d+)\s+pass"
_DEFAULT_FAIL = r"(\d+)\s+fail"

# A stage's command is either a shell string or a callable returning (output, exit_code).
Command = Union[str, Callable[[], tuple[str, int]]]


def strip_ansi(text: str | None) -> str:
    """Strip ANSI color/style escapes; ``None`` -> ``""`` (mirrors the PS null guard)."""
    if text is None:
        return ""
    return _ANSI_RE.sub("", text)


def parse_counts(
    text: str | None,
    pass_pattern: str | None,
    fail_pattern: str | None,
) -> dict[str, Any]:
    """Port of ``Get-GateCounts`` (loopcore.ps1 ~lines 99-112).

    PURE parser: extract pass/fail counts from a captured test-runner string. Returns
    ``{"pass": int, "fail": int, "matched": bool}`` where ``matched`` is True if EITHER
    pattern hit. A missing/empty pattern is skipped (matching the PS ``if ($PassPattern
    -and ...)`` guard). Only the FIRST match's first group is used, like the PS
    ``$Matches[1]``.
    """
    text = text or ""
    pass_ = 0
    fail = 0
    matched = False

    if pass_pattern:
        m = re.search(pass_pattern, text)
        if m:
            pass_ = int(m.group(1))
            matched = True
    if fail_pattern:
        m = re.search(fail_pattern, text)
        if m:
            fail = int(m.group(1))
            matched = True

    return {"pass": pass_, "fail": fail, "matched": matched}


def _run_command(command: Command, cwd: str | None = None) -> tuple[str, int]:
    """Execute a stage command, returning ``(output_text, exit_code)``.

    A callable is the test/extension hook (the PS scriptblock path): it returns its own
    ``(output, exit)`` and spawns NO external process — ``cwd`` does not apply to it. A string is
    run via the shell with stdout+stderr captured (combined), mirroring ``cmd /c "$cmd 2>&1"``,
    inside ``cwd`` when given (``None`` -> the current process directory, today's behavior).
    """
    if callable(command):
        output, exit_code = command()
        if exit_code is None:
            exit_code = 0
        return output if output is not None else "", int(exit_code)

    proc = subprocess.run(
        command,
        shell=True,
        capture_output=True,
        text=True,
        cwd=cwd,
        # Decode as UTF-8 and tolerate stray bytes. Gate stages (tsc/eslint/vitest via
        # bun) emit non-cp1252 bytes (✓/✗ glyphs, box-drawing, snippet text); the Windows
        # default text decode (cp1252) raises UnicodeDecodeError in subprocess's reader
        # thread, which silently drops ALL gate output → the gate reads 0 pass (baseline
        # 400→0) and the run can't progress. Mirrors proc.run_with_timeout.
        encoding="utf-8",
        errors="replace",
    )
    output = (proc.stdout or "") + (proc.stderr or "")
    return output, proc.returncode if proc.returncode is not None else 0


def run_gate(
    stages: list[dict[str, Any]] | None = None,
    cwd: str | None = None,
    fail_fast: bool = False,
) -> dict[str, Any]:
    """Port of ``Invoke-Gate`` (loopcore.ps1 ~lines 114-181).

    Runs an ordered list of stages; each stage is ``{name, command, pass_pattern?,
    fail_pattern?}``. ``command`` is a shell string or a callable hook returning
    ``(output, exit)``.

    ``cwd`` is the working directory STRING-command stages run in (``None`` -> the current
    process directory, byte-identical to today's behavior). Callable hooks are unaffected.

    ``fail_fast`` (default ``False`` -> byte-identical to prior behavior: every stage always
    runs) short-circuits the pipeline: once a stage exits non-zero, no later stage's command is
    launched. This saves paying for a full (build-heavy) test run after lint already failed. The
    skipped stages STILL appear in ``stages`` — see below — so every consumer's shape is intact.

    Green = every stage exited 0. ``pass`` / ``fail`` / ``total`` come from the LAST stage
    that reported any counts (so a no-count pre-stage does not zero the totals). When no
    stages are supplied the default reproduces the single ``bun test`` stage with patterns
    ``(\\d+)\\s+pass`` / ``(\\d+)\\s+fail``.

    Returns ``{green, pass, fail, total, stages, raw}`` where ``stages`` is a list of
    ``{name, ok, exit}`` for stages that RAN, plus ``{name, ok: False, exit: None,
    skipped: True}`` for any stage short-circuited by ``fail_fast``. ``raw`` is the concatenated
    per-stage output (a skipped stage produced none, so it adds no ``### stage`` section).

    Skipped-stage shape rationale (safe for every known consumer):
    - ``ok`` is ``False`` — a stage that did not run is NOT green (matches ``phases._stage_ok``'s
      "absent/didn't-run = not ok" contract) and keeps the gate red, which it already is.
    - ``exit`` is ``None`` — there is no real exit code. The only reader is ``phases._gate_summary``
      (``f"{name}={s.get('exit')}"`` -> renders ``test=None``, no crash). ``feedback`` parses exit
      codes out of ``raw`` headers, and a skipped stage writes no header, so it is never parsed.
    - No per-stage count/pass field is emitted, so a skipped TEST stage feeds NOTHING into the
      pass/fail/total floor logic: ``last_counts`` is only updated by a stage whose command ran
      and matched, so ``total``/``fail`` stay zero-safe. BMAD's ``_is_flaky_shape`` then reads a
      gate-level ``fail`` of 0 (``0 < fail`` is False) -> NOT flaky -> the red gate is reported at
      once with no wasted retries, which is correct: the real failure was the earlier stage.
    """
    if not stages:
        stages = [
            {
                "name": "test",
                "command": "bun test",
                "pass_pattern": _DEFAULT_PASS,
                "fail_pattern": _DEFAULT_FAIL,
            }
        ]

    stage_results: list[dict[str, Any]] = []
    all_green = True
    last_counts: dict[str, Any] | None = None
    raw_parts: list[str] = []
    short_circuited = False  # fail_fast: a prior stage failed -> launch no more commands

    for s in stages:
        name = s.get("name")
        cmd = s.get("command")

        if short_circuited:
            # fail_fast already tripped on an earlier stage — record this one as skipped WITHOUT
            # running its command. ``ok=False`` / ``exit=None`` / no counts keeps every consumer
            # safe (see the docstring); it contributes nothing to pass/fail/total.
            stage_results.append({"name": name, "ok": False, "exit": None, "skipped": True})
            continue

        # ``is not None`` guard mirrors the PS ``if ($null -ne $s.PassPattern)`` — an
        # explicit empty/None pattern falls back to the default.
        pass_pattern = s.get("pass_pattern")
        if pass_pattern is None:
            pass_pattern = _DEFAULT_PASS
        fail_pattern = s.get("fail_pattern")
        if fail_pattern is None:
            fail_pattern = _DEFAULT_FAIL

        output, exit_code = _run_command(cmd, cwd)
        clean = strip_ansi(output)

        counts = parse_counts(clean, pass_pattern, fail_pattern)
        if counts["matched"]:
            last_counts = counts

        ok = exit_code == 0
        if not ok:
            all_green = False
            if fail_fast:
                short_circuited = True

        stage_results.append({"name": name, "ok": ok, "exit": exit_code})
        raw_parts.append(f"### stage '{name}' (exit={exit_code})\n{output}")

    pass_ = last_counts["pass"] if last_counts else 0
    fail = last_counts["fail"] if last_counts else 0

    return {
        "green": all_green,
        "pass": pass_,
        "fail": fail,
        "total": pass_ + fail,
        "stages": stage_results,
        "raw": "\n".join(raw_parts),
    }
