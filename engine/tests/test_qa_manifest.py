"""Coverage for ``orrery_loop.qa.manifest`` — the AC-oracle parser.

Hermetic: synthesises a tiny story tree in ``tmp_path`` (an em-dash AC header, a
status line, a Tasks section that must be excluded) and asserts identity + AC
extraction + epic grouping, plus that non-story files are ignored.
"""

from __future__ import annotations

from orrery_loop.qa.manifest import build_manifest, parse_story_file

STORY_21 = """---
baseline_commit: deadbeef
---

# Story 2.1: Capture Input & Thread Display

Status: done

## Acceptance Criteria

**AC1 — Capture input renders (messaging model)**
Given the user is on the Capture tab, then:
1. A textarea is displayed.

**AC2 — Send behavior on mobile (< 768px)**
Given the input is rendered on mobile, then:
1. The send button is visible.

## Tasks / Subtasks

- [x] Task 1 — should NOT appear in the AC block (AC9 mentioned here is a trap)
"""

STORY_2_10 = """# Story 2.10: Tenth Story

Status: review

## Acceptance Criteria

**AC1 — Only criterion**
Given x, then y.

## Dev Notes
nothing
"""


def _write(tmp_path, name, text):
    p = tmp_path / name
    p.write_text(text, encoding="utf-8")
    return p


def test_parse_story_identity_and_criteria(tmp_path):
    f = _write(tmp_path, "2-1-capture-input-and-thread-display.md", STORY_21)
    s = parse_story_file(f)
    assert s is not None
    assert s.id == "2.1"
    assert s.epic == 2
    assert s.title == "Capture Input & Thread Display"
    assert s.status == "done"
    assert [c.id for c in s.criteria] == ["AC1", "AC2"]
    assert s.criteria[0].title == "Capture input renders (messaging model)"
    # The Tasks section (and its "AC9" trap) must be excluded from the AC block.
    assert "Tasks" not in s.ac_markdown
    assert "trap" not in s.ac_markdown
    assert "AC1" in s.ac_markdown


def test_non_story_files_ignored(tmp_path):
    assert parse_story_file(_write(tmp_path, "sprint-status.yaml", "x: 1")) is None
    assert parse_story_file(_write(tmp_path, "deferred-work.md", "# Deferred")) is None
    assert parse_story_file(_write(tmp_path, "epic-2-retro-2026-06-18.md", "# Retro")) is None


def test_build_manifest_groups_and_sorts(tmp_path):
    _write(tmp_path, "2-1-capture-input-and-thread-display.md", STORY_21)
    _write(tmp_path, "2-10-tenth-story.md", STORY_2_10)
    _write(tmp_path, "1-1-scaffold.md", STORY_21.replace("2.1", "1.1").replace("Story 2.1", "Story 1.1"))
    _write(tmp_path, "deferred-work.md", "# noise")

    m = build_manifest(tmp_path, app="webapp", base_url="http://localhost:3000")
    assert m["app"] == "webapp"
    assert m["baseUrl"] == "http://localhost:3000"
    assert m["storyCount"] == 3  # the non-story md is ignored
    assert m["epicCount"] == 2
    assert m["acCount"] == 2 + 1 + 2  # 2.1 -> 2, 2.10 -> 1, 1.1 -> 2

    epic2 = next(e for e in m["epics"] if e["epic"] == 2)
    # Numeric story sort: 2.1 before 2.10 (not lexicographic).
    assert [s["id"] for s in epic2["stories"]] == ["2.1", "2.10"]
    assert epic2["storyCount"] == 2
