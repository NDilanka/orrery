"""Transition coverage for ``get_stop_mode`` — mirrors the ``Get-StopMode`` self-test intent."""

from __future__ import annotations

from orrery_loop.checkpoint import get_stop_mode


def test_none_content_not_requested():
    s = get_stop_mode(None)
    assert s == {"requested": None, "honor": False, "mode": None}


def test_empty_defaults_to_phase():
    s = get_stop_mode("")
    assert s["requested"] == "phase"
    assert s["mode"] == "phase"
    assert s["honor"] is True


def test_whitespace_defaults_to_phase():
    s = get_stop_mode("   \n\t")
    assert s["mode"] == "phase"
    assert s["honor"] is True


def test_unrecognized_defaults_to_phase():
    s = get_stop_mode("frobnicate")
    assert s["mode"] == "phase"
    assert s["honor"] is True


def test_phase_fires_at_any_scope():
    assert get_stop_mode("phase", scope="phase")["honor"] is True
    assert get_stop_mode("phase", scope="story")["honor"] is True


def test_now_fires_at_any_scope():
    assert get_stop_mode("now", scope="phase")["honor"] is True
    assert get_stop_mode("now", scope="story")["honor"] is True
    assert get_stop_mode("now")["mode"] == "now"


def test_story_held_at_phase_scope():
    s = get_stop_mode("story", scope="phase")
    assert s["requested"] == "story"
    assert s["mode"] == "story"
    assert s["honor"] is False  # held until a between-iteration boundary


def test_story_fires_at_story_scope():
    s = get_stop_mode("story", scope="story")
    assert s["honor"] is True


def test_case_and_whitespace_normalized():
    s = get_stop_mode("  STORY  ", scope="phase")
    assert s["mode"] == "story"
    assert s["honor"] is False
