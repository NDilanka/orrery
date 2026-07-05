"""Parity tests for the BMAD sprint-status parser + actionable-story selection.

Mirrors ``bmad-loop.ps1`` (Get-SprintStories + the First-UnmergedDone/
First-WithStatus selection chain) and the schema in ``sprint.rs``."""

from __future__ import annotations

import textwrap

from orrery_loop.bmad.sprint import (
    Story,
    epic_done,
    epic_scope,
    parse_sprint_status,
    select_actionable,
)

# A small sprint-status.yaml modelled on the real fixture / sprint.rs shape:
# epic-1 fully done (+ retro done), epic-2 in flight (mixed statuses), epic-3
# all backlog. Mixes ready/ready-for-dev aliases and an unknown status to test
# tolerance.
SPRINT = textwrap.dedent(
    """\
    generated: 2026-04-05
    project: demo-project

    development_status:
      epic-1: done
      1-1-scaffold: done
      1-2-app-shell: done
      epic-1-retrospective: done

      epic-2: in-progress
      2-1-capture: done
      2-2-pipeline: review
      2-3-display: in-progress
      2-4-correction: ready-for-dev
      2-5-extraction: backlog
      2-6-weird: not-a-real-status
      epic-2-retrospective: optional

      epic-3: backlog
      3-1-browsing: backlog
      3-2-detail: backlog
    """
)


def test_parse_epics_and_stories():
    s = parse_sprint_status(SPRINT)
    # 3 epics parsed (groups), with retro state captured.
    assert {e.key for e in s.epics} == {"1", "2", "3"}
    e1 = s.epic("1")
    assert e1 is not None and e1.status == "done" and e1.retro == "done"
    e2 = s.epic("2")
    assert e2 is not None and e2.status == "in-progress" and e2.retro == "optional"

    # Stories: the unknown 'not-a-real-status' row is dropped (tolerant).
    keys = {st.key for st in s.stories}
    assert "2-6-weird" not in keys
    assert "2-1-capture" in keys and "3-2-detail" in keys
    # epic + index populated; ready-for-dev canonicalized to 'ready'.
    s24 = s.story("2-4-correction")
    assert s24 is not None
    assert s24.epic == "2"
    assert s24.status == "ready" and s24.raw_status == "ready-for-dev"


def test_parse_tolerates_garbage():
    assert parse_sprint_status("not: : valid: : yaml: [[[").stories == []
    assert parse_sprint_status("just a scalar").stories == []
    # no development_status map
    assert parse_sprint_status("project: x\nfoo: bar").epics == []


def test_select_priority_unmerged_done_wins():
    s = parse_sprint_status(SPRINT)
    # An unmerged 'done' (epic 1) beats in-progress/review/ready/backlog.
    chosen = select_actionable(s.stories, is_unmerged_done=lambda st: st.key == "1-1-scaffold")
    assert chosen is not None and chosen.key == "1-1-scaffold"


def test_select_priority_in_progress():
    s = parse_sprint_status(SPRINT)
    # No unmerged-done -> in-progress wins over review/ready/backlog.
    chosen = select_actionable(s.stories, is_unmerged_done=lambda st: False)
    assert chosen is not None and chosen.key == "2-3-display"


def test_select_priority_review():
    # Drop the in-progress story -> review is next.
    s = parse_sprint_status(SPRINT)
    pool = [st for st in s.stories if st.status != "in-progress"]
    chosen = select_actionable(pool, is_unmerged_done=lambda st: False)
    assert chosen is not None and chosen.key == "2-2-pipeline"


def test_select_priority_ready():
    s = parse_sprint_status(SPRINT)
    pool = [st for st in s.stories if st.status not in ("in-progress", "review")]
    chosen = select_actionable(pool, is_unmerged_done=lambda st: False)
    assert chosen is not None and chosen.key == "2-4-correction"  # ready-for-dev


def test_select_priority_backlog():
    s = parse_sprint_status(SPRINT)
    pool = [
        st for st in s.stories if st.status not in ("in-progress", "review", "ready")
    ]
    chosen = select_actionable(pool, is_unmerged_done=lambda st: False)
    assert chosen is not None and chosen.status == "backlog"


def test_select_none_when_all_merged_done():
    # Everything 'done' and all merged (predicate False) -> nothing actionable.
    only_done = [
        Story(key="1-1-x", status="done", raw_status="done", epic="1", index=0),
        Story(key="1-2-y", status="done", raw_status="done", epic="1", index=1),
    ]
    assert select_actionable(only_done, is_unmerged_done=lambda st: False) is None
    # Default (no predicate injected) also returns None for all-done.
    assert select_actionable(only_done) is None


def test_epic_scope_and_done():
    s = parse_sprint_status(SPRINT)
    scope1 = epic_scope(s, "1")
    assert {st.key for st in scope1} == {"1-1-scaffold", "1-2-app-shell"}
    assert epic_done(s, "1") is True  # all done -> retro due
    assert epic_done(s, "2") is False  # mixed statuses
    assert epic_done(s, "99") is False  # empty epic is not "done"


def test_parse_from_real_fixture_path(tmp_path):
    p = tmp_path / "sprint-status.yaml"
    p.write_text(SPRINT, encoding="utf-8")
    s = parse_sprint_status(p)
    assert s.story("2-1-capture") is not None
    # Missing path is treated as text (no dev_status) -> empty, not a crash.
    assert parse_sprint_status(str(tmp_path / "nope.yaml")).stories == []
