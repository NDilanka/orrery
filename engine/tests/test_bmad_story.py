"""Parity tests for the BMAD story .md parsers (Get-StoryMeta / Get-StoryACs)."""

from __future__ import annotations

from loop.bmad.story import story_acs, story_meta

STORY = """\
# Story 3-4: Semantic Search

Status: review
baseline_commit: "a1b2c3d4e5f6"

## Story
As a user I want semantic search.

## Acceptance Criteria
1. A search box appears on the notes screen.
2. Results rank by cosine similarity.
- [ ] Empty query shows a hint, not an error.

## Tasks / Subtasks
- [ ] Wire the embedding query.
"""


def test_story_meta_extracts_status_and_baseline():
    m = story_meta(STORY)
    assert m["status"] == "review"
    assert m["baseline"] == "a1b2c3d4e5f6"


def test_story_meta_missing_fields_are_none():
    m = story_meta("# A story with no status line and no baseline\n\nbody")
    assert m["status"] is None
    assert m["baseline"] is None
    # empty/None input is tolerated.
    assert story_meta("") == {"status": None, "baseline": None}
    assert story_meta(None) == {"status": None, "baseline": None}


def test_story_meta_status_must_be_line_start():
    # 'Status:' only counts at the start of a line (PS (?m)^Status:).
    m = story_meta("intro text Status: nope\nStatus: ready-for-dev\n")
    assert m["status"] == "ready-for-dev"


def test_story_acs_extracts_section_up_to_next_heading():
    acs = story_acs(STORY)
    assert acs.startswith("1. A search box appears")
    assert "Empty query shows a hint" in acs
    # Stops at the next '## ' heading — Tasks/Subtasks must not bleed in.
    assert "Wire the embedding query" not in acs


def test_story_acs_case_insensitive_heading():
    text = "## acceptance criteria\n- do the thing\n\n## Next\nother"
    acs = story_acs(text)
    assert acs == "- do the thing"


def test_story_acs_absent_returns_empty():
    assert story_acs("# No criteria here\n\njust prose") == ""
    assert story_acs("") == ""
    assert story_acs(None) == ""


def test_story_acs_truncates_over_max_chars():
    long_body = "## Acceptance Criteria\n" + ("x" * 5000) + "\n## Next\n"
    acs = story_acs(long_body, max_chars=2500)
    assert len(acs) == 2500 + len(" …(truncated)")
    assert acs.endswith("…(truncated)")
    # A section at/under the limit is returned verbatim (no marker).
    short = story_acs("## Acceptance Criteria\nshort\n## Next\n")
    assert short == "short"
