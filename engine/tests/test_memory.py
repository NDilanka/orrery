"""Cross-run lessons memory store — behavior + determinism coverage.

All time inputs are INJECTED so ranking / pruning are reproducible. Covers the
Null store (OFF default), append-only persistence, semantic-first relevance
recall, green-gated + de-duped recording, decay/retention pruning, anti-poisoning
forget/decay, and a disk round-trip.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from loop.memory import (
    FileMemoryStore,
    Lesson,
    MemoryStore,
    NullMemoryStore,
)
from loop.memory.store import score_lesson, _tokens

# A fixed reference clock so every test is deterministic.
DAY = 24 * 3600.0
NOW = 1_700_000_000.0


def _lesson(
    text: str,
    *,
    kind: str = "episodic",
    task: str = "fix the failing build",
    outcome: str = "green",
    run_id: str = "run-1",
    it: int = 1,
    ts: float = NOW,
    weight: float = 1.0,
) -> Lesson:
    return Lesson(
        text=text,
        kind=kind,
        task=task,
        outcome=outcome,
        run_id=run_id,
        iter=it,
        created_ts=ts,
        weight=weight,
    )


# --------------------------------------------------------------------------- #
# Null store: the OFF default.
# --------------------------------------------------------------------------- #

def test_null_store_is_off():
    store: MemoryStore = NullMemoryStore()
    assert store.recall("anything") == ""
    assert store.recall("anything", limit=10) == ""
    assert store.record(_lesson("ignored")) is None
    assert store.prune() == 0
    assert store.prune(max_entries=1, max_age_sec=1.0, now=NOW) == 0
    # record_if_useful exists and always reports "not recorded".
    assert store.record_if_useful(_lesson("ignored")) is False


# --------------------------------------------------------------------------- #
# File store: append-only persistence.
# --------------------------------------------------------------------------- #

def test_record_is_append_only(tmp_path: Path):
    p = tmp_path / "mem.jsonl"
    store = FileMemoryStore(p)

    store.record(_lesson("first lesson", run_id="r1"))
    after_one = p.read_text(encoding="utf-8").splitlines()
    assert len(after_one) == 1

    store.record(_lesson("second lesson", run_id="r2"))
    store.record(_lesson("third lesson", run_id="r3"))
    after_three = p.read_text(encoding="utf-8").splitlines()

    # Line count grows; the earlier lines are byte-identical (not rewritten).
    assert len(after_three) == 3
    assert after_three[0] == after_one[0]
    assert "first lesson" in after_three[0]
    assert "third lesson" in after_three[2]


def test_record_creates_parent_dirs(tmp_path: Path):
    p = tmp_path / "nested" / "deep" / "mem.jsonl"
    store = FileMemoryStore(p)
    store.record(_lesson("makes its own dirs"))
    assert p.exists()
    assert len(p.read_text(encoding="utf-8").splitlines()) == 1


# --------------------------------------------------------------------------- #
# Recall: relevance ranking + semantic-first ordering.
# --------------------------------------------------------------------------- #

def test_recall_ranks_relevant_first_and_semantic_leads(tmp_path: Path):
    p = tmp_path / "mem.jsonl"
    store = FileMemoryStore(p)

    # 3 episodic with varying relevance to a "flaky pytest timeout" task.
    store.record(_lesson("unrelated note about css styling colors", run_id="a"))
    store.record(
        _lesson("retry the flaky pytest timeout with a longer deadline", run_id="b")
    )
    store.record(_lesson("the database migration needs a backup", run_id="c"))
    # 1 semantic durable repo fact.
    store.record(
        _lesson(
            "the test suite lives under engine/tests",
            kind="semantic",
            run_id="facts",
        )
    )

    block = store.recall("how to fix a flaky pytest timeout", limit=5, now=NOW)
    lines = block.splitlines()

    assert lines[0] == "Lessons from prior runs:"
    # Semantic fact must lead the bullet list.
    assert lines[1].startswith("- [fact]")
    assert "engine/tests" in lines[1]
    # The most task-relevant EPISODIC lesson ranks first among episodic ones.
    episodic_lines = [ln for ln in lines[2:] if not ln.startswith("- [fact]")]
    assert "flaky pytest timeout" in episodic_lines[0]
    # The two zero-overlap lessons rank strictly below the relevant one (they
    # tie at score 0 and fall back to stable insertion order among themselves).
    rest = "\n".join(episodic_lines[1:])
    assert "css styling" in rest
    assert "database migration" in rest


def test_recall_respects_limit_and_empty(tmp_path: Path):
    p = tmp_path / "mem.jsonl"
    store = FileMemoryStore(p)
    # Empty store -> empty block.
    assert store.recall("anything", now=NOW) == ""

    for i in range(6):
        store.record(_lesson(f"pytest timeout lesson number {i}", run_id=f"r{i}"))
    block = store.recall("pytest timeout", limit=3, now=NOW)
    bullets = [ln for ln in block.splitlines() if ln.startswith("- ")]
    assert len(bullets) == 3
    assert store.recall("pytest timeout", limit=0, now=NOW) == ""


# --------------------------------------------------------------------------- #
# Green-gated + de-duped recording.
# --------------------------------------------------------------------------- #

def test_record_if_useful_drops_bad_outcomes(tmp_path: Path):
    p = tmp_path / "mem.jsonl"
    store = FileMemoryStore(p)

    assert store.record_if_useful(_lesson("good green lesson", outcome="green"))
    assert store.record_if_useful(_lesson("progress lesson", outcome="progress"))
    # regress + handoff are dropped (must not poison memory).
    assert not store.record_if_useful(_lesson("bad regress", outcome="regress"))
    assert not store.record_if_useful(_lesson("handoff note", outcome="handoff"))

    lines = p.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    assert all("regress" not in ln and "handoff" not in ln for ln in lines)


def test_record_if_useful_dedupes_near_identical_text(tmp_path: Path):
    p = tmp_path / "mem.jsonl"
    store = FileMemoryStore(p)

    assert store.record_if_useful(_lesson("Use --no-verify on commit."))
    # Same text up to case / punctuation / whitespace -> deduped (dropped).
    assert not store.record_if_useful(_lesson("use   --no-verify, on commit!"))
    assert not store.record_if_useful(_lesson("USE --NO-VERIFY ON COMMIT"))
    # A genuinely different lesson is still recorded.
    assert store.record_if_useful(_lesson("rebuild the docker image first"))

    lines = p.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2


# --------------------------------------------------------------------------- #
# Decay / retention: prune.
# --------------------------------------------------------------------------- #

def test_prune_removes_aged_entries(tmp_path: Path):
    p = tmp_path / "mem.jsonl"
    store = FileMemoryStore(p)

    store.record(_lesson("fresh", ts=NOW))
    store.record(_lesson("old", ts=NOW - 30 * DAY))
    store.record(_lesson("ancient", ts=NOW - 90 * DAY))

    # Keep only entries newer than 10 days.
    removed = store.prune(max_age_sec=10 * DAY, now=NOW)
    assert removed == 2
    survivors = p.read_text(encoding="utf-8").splitlines()
    assert len(survivors) == 1
    assert "fresh" in survivors[0]


def test_prune_caps_keeping_highest_scoring(tmp_path: Path):
    p = tmp_path / "mem.jsonl"
    store = FileMemoryStore(p)

    # Older / lower-weight entries should be dropped first when over the cap.
    store.record(_lesson("recent high weight", ts=NOW, weight=2.0))
    store.record(_lesson("recent normal", ts=NOW - 1 * DAY, weight=1.0))
    store.record(_lesson("older low weight", ts=NOW - 60 * DAY, weight=0.2))

    removed = store.prune(max_entries=2, now=NOW)
    assert removed == 1
    kept = p.read_text(encoding="utf-8")
    assert "recent high weight" in kept
    assert "older low weight" not in kept


def test_prune_age_cutoff_requires_now(tmp_path: Path):
    p = tmp_path / "mem.jsonl"
    store = FileMemoryStore(p)
    store.record(_lesson("x"))
    with pytest.raises(ValueError):
        store.prune(max_age_sec=DAY)  # no now injected


def test_prune_empty_store_returns_zero(tmp_path: Path):
    store = FileMemoryStore(tmp_path / "mem.jsonl")
    assert store.prune(now=NOW) == 0


# --------------------------------------------------------------------------- #
# Anti-poisoning: forget + weight decay.
# --------------------------------------------------------------------------- #

def test_forget_removes_matching(tmp_path: Path):
    p = tmp_path / "mem.jsonl"
    store = FileMemoryStore(p)
    store.record(_lesson("good", run_id="ok"))
    store.record(_lesson("poisoned one", run_id="bad"))
    store.record(_lesson("poisoned two", run_id="bad"))

    removed = store.forget(lambda lsn: lsn.run_id == "bad")
    assert removed == 2
    survivors = p.read_text(encoding="utf-8").splitlines()
    assert len(survivors) == 1
    assert "good" in survivors[0]


def test_decay_weights_demotes_without_deleting(tmp_path: Path):
    p = tmp_path / "mem.jsonl"
    store = FileMemoryStore(p)
    store.record(_lesson("suspect lesson", run_id="bad", weight=1.0))
    store.record(_lesson("trusted lesson", run_id="ok", weight=1.0))

    changed = store.decay_weights(lambda lsn: lsn.run_id == "bad", factor=0.25)
    assert changed == 1
    # Both lessons still present (soft-forget keeps provenance).
    assert len(p.read_text(encoding="utf-8").splitlines()) == 2
    reloaded = {lsn.run_id: lsn.weight for lsn in FileMemoryStore(p)._load()}
    assert reloaded["bad"] == pytest.approx(0.25)
    assert reloaded["ok"] == pytest.approx(1.0)


# --------------------------------------------------------------------------- #
# Determinism + disk round-trip.
# --------------------------------------------------------------------------- #

def test_ranking_is_deterministic(tmp_path: Path):
    p = tmp_path / "mem.jsonl"
    store = FileMemoryStore(p)
    store.record(_lesson("alpha pytest timeout", run_id="a"))
    store.record(_lesson("beta build error", run_id="b"))
    store.record(_lesson("gamma pytest flake", kind="semantic", run_id="g"))

    a = store.recall("pytest timeout flake", limit=5, now=NOW)
    b = store.recall("pytest timeout flake", limit=5, now=NOW)
    assert a == b  # identical inputs -> identical output


def test_recency_factor_is_monotonic():
    task = _tokens("pytest timeout")
    fresh = _lesson("pytest timeout", ts=NOW)
    old = _lesson("pytest timeout", ts=NOW - 30 * DAY)
    # Same text/weight; fresher lesson must score strictly higher at fixed now.
    assert score_lesson(fresh, task, NOW) > score_lesson(old, task, NOW)


def test_round_trip_reload_from_disk(tmp_path: Path):
    p = tmp_path / "mem.jsonl"
    writer = FileMemoryStore(p)
    originals = [
        _lesson("first", kind="episodic", run_id="r1", it=1, ts=NOW, weight=1.5),
        _lesson("second durable fact", kind="semantic", run_id="r2", it=2, ts=NOW - DAY),
        _lesson("third with unicode ✓ café", kind="episodic", run_id="r3", it=3),
    ]
    for lsn in originals:
        writer.record(lsn)

    # Fresh instance reads from disk only.
    reloaded = FileMemoryStore(p)._load()
    assert reloaded == originals  # dataclass equality across the full set
