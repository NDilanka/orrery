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


def _run_command(command: Command) -> tuple[str, int]:
    """Execute a stage command, returning ``(output_text, exit_code)``.

    A callable is the test/extension hook (the PS scriptblock path): it returns its own
    ``(output, exit)`` and spawns NO external process. A string is run via the shell with
    stdout+stderr captured (combined), mirroring ``cmd /c "$cmd 2>&1"``.
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
    )
    output = (proc.stdout or "") + (proc.stderr or "")
    return output, proc.returncode if proc.returncode is not None else 0


def run_gate(stages: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """Port of ``Invoke-Gate`` (loopcore.ps1 ~lines 114-181).

    Runs an ordered list of stages; each stage is ``{name, command, pass_pattern?,
    fail_pattern?}``. ``command`` is a shell string or a callable hook returning
    ``(output, exit)``.

    Green = every stage exited 0. ``pass`` / ``fail`` / ``total`` come from the LAST stage
    that reported any counts (so a no-count pre-stage does not zero the totals). When no
    stages are supplied the default reproduces the single ``bun test`` stage with patterns
    ``(\\d+)\\s+pass`` / ``(\\d+)\\s+fail``.

    Returns ``{green, pass, fail, total, stages, raw}`` where ``stages`` is a list of
    ``{name, ok, exit}`` and ``raw`` is the concatenated per-stage output.
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

    for s in stages:
        name = s.get("name")
        cmd = s.get("command")
        # ``is not None`` guard mirrors the PS ``if ($null -ne $s.PassPattern)`` — an
        # explicit empty/None pattern falls back to the default.
        pass_pattern = s.get("pass_pattern")
        if pass_pattern is None:
            pass_pattern = _DEFAULT_PASS
        fail_pattern = s.get("fail_pattern")
        if fail_pattern is None:
            fail_pattern = _DEFAULT_FAIL

        output, exit_code = _run_command(cmd)
        clean = strip_ansi(output)

        counts = parse_counts(clean, pass_pattern, fail_pattern)
        if counts["matched"]:
            last_counts = counts

        ok = exit_code == 0
        if not ok:
            all_green = False

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
