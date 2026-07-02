"""Coverage for the ``.loop/`` state-file writer IO (:mod:`loop.logio`)."""

from __future__ import annotations

import json
import os

from loop.logio import (
    append_event,
    clear_stop_flag,
    consume_answer,
    read_answer_inbox,
    read_stop_flag,
    read_text,
    write_checkpoint,
    write_run_output,
    write_text,
)


def test_append_event_two_compact_lines(tmp_path):
    """Two appended events -> exactly two lines, each COMPACT (no ', ' or ': ')."""
    log = tmp_path / "nested" / "log.jsonl"  # parent created on demand
    append_event(log, {"event": "iter", "iter": 1, "pass": 2})
    append_event(log, {"event": "stop", "reason": "green done", "green": True})

    raw = log.read_text(encoding="utf-8")
    lines = raw.splitlines()
    assert len(lines) == 2, f"expected exactly two lines, got {len(lines)}: {lines!r}"

    for ln in lines:
        # compact: no space after a comma or colon separator.
        assert ", " not in ln, f"non-compact (', ' present): {ln!r}"
        assert ": " not in ln, f"non-compact (': ' present): {ln!r}"
        # still valid JSON, round-trips to a dict.
        obj = json.loads(ln)
        assert isinstance(obj, dict)

    # the events parse back to what we wrote.
    assert json.loads(lines[0])["iter"] == 1
    assert json.loads(lines[1])["reason"] == "green done"


def test_append_event_creates_parent_dir(tmp_path):
    log = tmp_path / "a" / "b" / "log.jsonl"
    append_event(log, {"event": "x"})
    assert log.exists()


def test_checkpoint_round_trips(tmp_path):
    """A checkpoint written by write_checkpoint reloads via json.load identically."""
    path = tmp_path / "checkpoint.json"
    cp = {
        "updatedAt": "2026-06-20T00:00:00Z",
        "stage": "iter 3",
        "story": None,
        "branch": "feat/x",
        "mergeBase": "abc123",
        "cumUsd": 1.25,
        "resume": "pwsh -File loop.ps1",
    }
    write_checkpoint(path, cp)

    with open(path, encoding="utf-8") as fh:
        loaded = json.load(fh)
    assert loaded == cp


def test_answer_inbox_read_and_consume(tmp_path):
    """read returns the raw contents; consume deletes the file."""
    path = tmp_path / "answer.json"
    body = json.dumps({"qid": "3", "kind": "review", "a": "go ahead"})
    write_text(path, body)

    got = read_answer_inbox(path)
    assert got == body

    consume_answer(path)
    assert not path.exists()
    # reading a now-absent inbox yields None.
    assert read_answer_inbox(path) is None
    # consuming again is a harmless no-op.
    consume_answer(path)


def test_stop_flag_read_and_clear(tmp_path):
    """read_stop_flag returns the contents; clear_stop_flag deletes the flag."""
    flag = tmp_path / "STOP"
    write_text(flag, "story")

    assert read_stop_flag(flag) == "story"

    clear_stop_flag(flag)
    assert not flag.exists()
    assert read_stop_flag(flag) is None
    # clearing an absent flag is a no-op.
    clear_stop_flag(flag)


def test_read_text_missing_returns_none(tmp_path):
    assert read_text(tmp_path / "nope.md") is None


def test_write_then_read_text_round_trip(tmp_path):
    path = tmp_path / "sub" / "progress.md"
    write_text(path, "# Progress\nline two\n")
    assert read_text(path) == "# Progress\nline two\n"
    assert os.path.exists(path)


def test_write_run_output_writes_raw_as_is(tmp_path):
    path = tmp_path / "run-1.out"
    write_run_output(path, '{"result": "ok"}\nsecond line')
    assert read_text(path) == '{"result": "ok"}\nsecond line'


def test_write_run_output_creates_parent_dir(tmp_path):
    path = tmp_path / "state" / "run-3.out"
    write_run_output(path, "raw output")
    assert path.exists()


def test_write_run_output_none_is_a_noop(tmp_path):
    path = tmp_path / "run-none.out"
    write_run_output(path, None)
    assert not path.exists()


def test_write_run_output_empty_string_is_a_noop(tmp_path):
    path = tmp_path / "run-empty.out"
    write_run_output(path, "")
    assert not path.exists()


def test_write_run_output_overwrites_existing_file(tmp_path):
    path = tmp_path / "run-2.out"
    write_run_output(path, "first attempt")
    write_run_output(path, "second attempt")
    assert read_text(path) == "second attempt"
