"""Coverage for the hash-lock anti-cheat (``test_hash_map`` / ``hash_map_digest`` /
``compare_hash_map``).

Creates a temp dir with a couple of ``*.test.ts`` files, asserts the baseline map, then
modifies / adds / removes files and asserts ``compare_hash_map`` flags the right category
and produces the exact PS-format reason string. Also asserts digest stability regardless of
insertion order.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from orrery_loop.config import GateConfig
from orrery_loop.core import _hash_map_over, _lock_glob_set
from orrery_loop.hashlock import (
    compare_hash_map,
    hash_map_digest,
)
from orrery_loop.hashlock import test_hash as hash_digest_for
from orrery_loop.hashlock import test_hash_map as build_hash_map


def _cfg(gate: GateConfig):
    """A minimal stand-in exposing just the ``.gate`` attribute ``_lock_glob_set`` reads."""
    return SimpleNamespace(gate=gate)


def _write(p: Path, text: str) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


# === test_hash_map =====================================================================


def test_baseline_map_keys_and_hashes(tmp_path: Path):
    _write(tmp_path / "a.test.ts", "test('a', () => {})\n")
    _write(tmp_path / "b.test.ts", "test('b', () => {})\n")
    m = build_hash_map("*.test.ts", tmp_path)
    assert set(m.keys()) == {"a.test.ts", "b.test.ts"}
    # SHA-256 hex is 64 chars, uppercase (matches Get-FileHash .Hash).
    for h in m.values():
        assert len(h) == 64
        assert h == h.upper()


def test_map_keys_are_relative_posix(tmp_path: Path):
    _write(tmp_path / "sub" / "deep.test.ts", "test('d', () => {})\n")
    m = build_hash_map("*.test.ts", tmp_path)
    assert "sub/deep.test.ts" in m
    assert all("\\" not in k for k in m)


def test_recursive_glob_matches_subdirs(tmp_path: Path):
    _write(tmp_path / "top.test.ts", "x")
    _write(tmp_path / "pkg" / "nested.test.ts", "y")
    m = build_hash_map("**/*.test.ts", tmp_path)
    assert set(m.keys()) == {"top.test.ts", "pkg/nested.test.ts"}


def test_simple_glob_is_recursive_like_powershell_filter(tmp_path: Path):
    # PowerShell -Filter applies at every depth, so a bare *.test.ts still finds subdir
    # files. Our simple-glob path must match that.
    _write(tmp_path / "top.test.ts", "x")
    _write(tmp_path / "pkg" / "nested.test.ts", "y")
    m = build_hash_map("*.test.ts", tmp_path)
    assert set(m.keys()) == {"top.test.ts", "pkg/nested.test.ts"}


def test_no_match_empty_map(tmp_path: Path):
    _write(tmp_path / "readme.md", "not a test")
    assert build_hash_map("*.test.ts", tmp_path) == {}


def test_back_compat_hash_string(tmp_path: Path):
    _write(tmp_path / "a.test.ts", "a")
    _write(tmp_path / "b.test.ts", "b")
    m = build_hash_map("*.test.ts", tmp_path)
    assert hash_digest_for("*.test.ts", tmp_path) == hash_map_digest(m)
    # No files -> empty string.
    assert hash_digest_for("*.test.ts", tmp_path / "missing") == ""


# === hash_map_digest ===================================================================


def test_digest_stable_regardless_of_insertion_order():
    m1 = {"a": "AAA", "b": "BBB", "c": "CCC"}
    m2 = {"c": "CCC", "a": "AAA", "b": "BBB"}
    assert hash_map_digest(m1) == hash_map_digest(m2)
    # Order is by SORTED key -> values concatenated.
    assert hash_map_digest(m1) == "AAABBBCCC"


def test_digest_empty_or_none():
    assert hash_map_digest({}) == ""
    assert hash_map_digest(None) == ""


# === compare_hash_map ==================================================================


def test_no_change_not_tampered():
    base = {"a.test.ts": "H1", "b.test.ts": "H2"}
    r = compare_hash_map(base, dict(base))
    assert r["tampered"] is False
    assert r["reason"] == ""
    assert r["changed"] == [] and r["added"] == [] and r["removed"] == []


def test_modified_flagged():
    base = {"a.test.ts": "H1", "b.test.ts": "H2"}
    cur = {"a.test.ts": "H1", "b.test.ts": "HX"}
    r = compare_hash_map(base, cur)
    assert r["tampered"] is True
    assert r["changed"] == ["b.test.ts"]
    assert r["reason"] == "locked test file(s) modified b.test.ts"


def test_removed_flagged():
    base = {"a.test.ts": "H1", "b.test.ts": "H2"}
    cur = {"a.test.ts": "H1"}
    r = compare_hash_map(base, cur)
    assert r["removed"] == ["b.test.ts"]
    assert r["reason"] == "locked test file(s) deleted b.test.ts"


def test_added_flagged():
    base = {"a.test.ts": "H1"}
    cur = {"a.test.ts": "H1", "c.test.ts": "H3"}
    r = compare_hash_map(base, cur)
    assert r["added"] == ["c.test.ts"]
    assert r["reason"] == "locked test file(s) added c.test.ts"


def test_reason_combines_categories_in_order():
    # modified ; deleted ; added — only present categories, each sorted.
    base = {"a.test.ts": "H1", "b.test.ts": "H2", "c.test.ts": "H3"}
    cur = {"b.test.ts": "HX", "a.test.ts": "HY", "d.test.ts": "H4"}
    r = compare_hash_map(base, cur)
    assert r["changed"] == ["a.test.ts", "b.test.ts"]
    assert r["removed"] == ["c.test.ts"]
    assert r["added"] == ["d.test.ts"]
    assert r["reason"] == (
        "locked test file(s) modified a.test.ts, b.test.ts; "
        "deleted c.test.ts; added d.test.ts"
    )


def test_none_baseline_treats_all_as_added():
    cur = {"a.test.ts": "H1"}
    r = compare_hash_map(None, cur)
    assert r["added"] == ["a.test.ts"]
    assert r["tampered"] is True


# === _lock_glob_set (HASH-LOCK: lock ALL configured lockGlobs) =========================


def test_lock_glob_set_includes_all_configured_globs():
    # Regression: the old code used only lock_globs[0], silently dropping every extra glob
    # from tamper detection. Now ALL configured globs are in the lock set, order-stable.
    cfg = _cfg(GateConfig(stages=[], lock_globs=["*.test.ts", "*.spec.ts", "tests/**/*.py"]))
    assert _lock_glob_set(cfg, []) == ["*.test.ts", "*.spec.ts", "tests/**/*.py"]


def test_lock_glob_set_dedupes_order_stable():
    cfg = _cfg(GateConfig(stages=[], lock_globs=["*.test.ts", "*.spec.ts", "*.test.ts"]))
    assert _lock_glob_set(cfg, []) == ["*.test.ts", "*.spec.ts"]


def test_lock_glob_set_empty_defaults_to_star():
    cfg = _cfg(GateConfig(stages=[], lock_globs=[]))
    assert _lock_glob_set(cfg, []) == ["*"]


def test_lock_glob_set_merges_held_out_after_all_configured():
    cfg = _cfg(GateConfig(stages=[], lock_globs=["*.test.ts", "*.spec.ts"]))
    stages = [
        {"name": "hidden", "held_out": True, "lock_globs": ["*.hidden", "*.spec.ts"]},
    ]
    # All configured globs first, then the held-out glob(s), de-duped (*.spec.ts not repeated).
    assert _lock_glob_set(cfg, stages) == ["*.test.ts", "*.spec.ts", "*.hidden"]


def test_all_configured_globs_reach_the_hash_map(tmp_path: Path):
    # End-to-end: two DIFFERENT lock globs each match a file; both files must appear in the
    # merged hash map (the old first-glob-only behavior lost the second file entirely).
    _write(tmp_path / "a.test.ts", "test('a', () => {})\n")
    _write(tmp_path / "b.spec.ts", "test('b', () => {})\n")
    cfg = _cfg(GateConfig(stages=[], lock_globs=["*.test.ts", "*.spec.ts"]))
    globs = _lock_glob_set(cfg, [])
    hmap = _hash_map_over(globs, tmp_path)
    assert set(hmap.keys()) == {"a.test.ts", "b.spec.ts"}


def test_lock_infra_globs_merged_when_enabled():
    cfg = _cfg(GateConfig(stages=[], lock_globs=["*.test.ts"], lock_infra=True))
    out = _lock_glob_set(cfg, [])
    assert out[0] == "*.test.ts"
    assert "conftest.py" in out  # curated infra glob merged in


def test_end_to_end_real_files(tmp_path: Path):
    _write(tmp_path / "a.test.ts", "test('a', () => {})\n")
    _write(tmp_path / "b.test.ts", "test('b', () => {})\n")
    base = build_hash_map("*.test.ts", tmp_path)

    # modify a, delete b, add c
    _write(tmp_path / "a.test.ts", "test('a', () => { /* changed */ })\n")
    (tmp_path / "b.test.ts").unlink()
    _write(tmp_path / "c.test.ts", "test('c', () => {})\n")
    cur = build_hash_map("*.test.ts", tmp_path)

    r = compare_hash_map(base, cur)
    assert r["changed"] == ["a.test.ts"]
    assert r["removed"] == ["b.test.ts"]
    assert r["added"] == ["c.test.ts"]
    assert r["tampered"] is True
    assert r["reason"] == (
        "locked test file(s) modified a.test.ts; deleted b.test.ts; added c.test.ts"
    )
