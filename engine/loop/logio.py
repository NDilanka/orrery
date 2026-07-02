"""The ``.loop/`` state-file IO — the WRITER side of the Orrery loop protocol.

Pure file IO for the engine's side of ``orrery/PROTOCOL.md`` §1. The PARSE / match
logic for these files already lives in pure modules (:func:`loop.verdict.read_answer_inbox`,
:func:`loop.checkpoint.get_stop_mode`); this module only does the actual disk read / write /
delete.

- :func:`append_event`     — append ONE compact JSON line to ``log.jsonl`` (§1).
- :func:`write_checkpoint`  — write ``checkpoint.json`` (indent=2; the orrery just parses it).
- :func:`read_answer_inbox` / :func:`consume_answer` — read then delete ``answer.json``.
- :func:`read_stop_flag`    / :func:`clear_stop_flag` — read then delete the ``STOP`` flag.
- :func:`read_text` / :func:`write_text` — progress.md and friends.
- :func:`write_run_output`  — persist an agent call's raw stdout (``run-<n>.out`` and friends).

All paths accept ``str`` or ``os.PathLike``. Reads of a missing file return ``None``.
"""

from __future__ import annotations

import json
import os
import time
from typing import Callable


def _ensure_parent(path) -> None:
    """Create the parent directory of ``path`` if it does not already exist."""
    parent = os.path.dirname(os.fspath(path))
    if parent:
        os.makedirs(parent, exist_ok=True)


def append_event(
    log_path,
    event: dict,
    now_ms: Callable[[], float] = time.time,
) -> None:
    """Append ONE line of compact JSON (``one object per line`` — PROTOCOL §1).

    Compact = ``json.dumps(event, separators=(',', ':'))`` (no space after ``:`` or ``,``)
    plus a trailing newline. Creates the parent directory if needed.

    Stamps a numeric ``_t`` (epoch milliseconds) onto the event before writing, UNLESS the
    caller already set one. This is PROTOCOL.md §2's wire convention for a real per-line
    timestamp: both reducers (Rust ``control.rs`` / TS ``reduce.ts``) already treat a
    numeric ``_t`` as the authoritative event time, falling back to a synthesized
    ``index * 1000`` only when no line in the run carries one. Before this, no engine path
    ever wrote ``_t``, so LIVE runs always fell back to synthetic time. ``now_ms`` is the
    injected wall clock — seconds since the epoch, e.g. ``time.time`` (the codebase's usual
    clock-injection shape; see ``loop.supervise``'s ``clock`` param) — multiplied by 1000
    to produce the millisecond stamp. Callers that pre-stamp ``_t`` (e.g. replaying a
    fixture with real historical times) are passed through unchanged.
    """
    _ensure_parent(log_path)
    if "_t" not in event:
        event = {**event, "_t": int(now_ms() * 1000)}
    line = json.dumps(event, separators=(",", ":"))
    with open(log_path, "a", encoding="utf-8") as fh:
        fh.write(line + "\n")


def write_checkpoint(path, checkpoint: dict) -> None:
    """Write ``checkpoint`` as a single JSON object to ``path`` (indent=2 is fine)."""
    _ensure_parent(path)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(checkpoint, fh, indent=2)


def read_answer_inbox(path) -> str | None:
    """Return the raw contents of the ``answer.json`` inbox, or ``None`` if absent.

    This is the file READ only — the parse / turn-match logic is
    :func:`loop.verdict.read_answer_inbox`.
    """
    return read_text(path)


def consume_answer(path) -> None:
    """Delete the ``answer.json`` inbox file (a no-op if it is already gone)."""
    _remove(path)


def read_stop_flag(path) -> str | None:
    """Return the contents of the ``STOP`` flag, or ``None`` if the flag is absent.

    Mode normalization is :func:`loop.checkpoint.get_stop_mode`; this is just the read.
    """
    return read_text(path)


def clear_stop_flag(path) -> None:
    """Delete the ``STOP`` flag file if present (a no-op otherwise)."""
    _remove(path)


def read_text(path) -> str | None:
    """Read a UTF-8 text file and return its contents, or ``None`` if it does not exist."""
    try:
        with open(path, encoding="utf-8") as fh:
            return fh.read()
    except FileNotFoundError:
        return None


def write_text(path, s: str) -> None:
    """Write ``s`` as a UTF-8 text file (e.g. progress.md), creating parents as needed."""
    _ensure_parent(path)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(s)


def write_run_output(path, raw: str | None) -> None:
    """Persist an agent call's raw stdout to ``path`` (e.g. ``.loop/run-3.out``), as-is.

    A debugging artifact for postmortem on a failed/parse-failed run, whose ``raw`` output
    was previously thrown away entirely. A no-op when ``raw`` is falsy (``None`` or empty) —
    a timed-out or otherwise resultless call leaves no stray empty file. Overwrites any
    existing file at ``path``; the caller picks a name stable enough that repeated calls
    (retries, iterations) either accumulate distinct files or intentionally overwrite one.
    """
    if not raw:
        return
    write_text(path, raw)


def _remove(path) -> None:
    """Delete ``path`` if it exists; swallow the not-found case."""
    try:
        os.remove(path)
    except FileNotFoundError:
        pass


__all__: list[str] = [
    "append_event",
    "write_checkpoint",
    "read_answer_inbox",
    "consume_answer",
    "read_stop_flag",
    "clear_stop_flag",
    "read_text",
    "write_text",
    "write_run_output",
]
