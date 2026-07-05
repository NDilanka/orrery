"""Hash-lock anti-cheat — verbatim ports of the per-file tamper-detection functions.

Faithful Python port of ``Get-TestHash``, ``Get-TestHashMap``, ``Get-HashMapDigest`` and
``Compare-HashMap`` (loopcore.ps1 ~lines 12-97).

Semantics locked from the PowerShell:

- The map is keyed by RELATIVE POSIX paths (forward slashes) so it is comparable across
  machines / cwd, and is ordered/sorted stably by full path before relativizing — matching
  ``Get-ChildItem -Recurse | Sort-Object FullName``.
- The digest is the order-stable concatenation of the hash VALUES taken in key-sorted
  order (``$Map.Keys | Sort-Object | ForEach-Object { $Map[$_] }``), so insertion order of
  the map never affects it.
- ``compare_hash_map`` is a PURE diff (no I/O) whose ``reason`` string matches the PS byte
  for byte: ``locked test file(s) modified a, b; deleted c; added d`` with only the present
  categories joined by ``; ``; each category is sorted.

Only I/O is reading the locked files to hash them (stdlib ``hashlib``); no test runner, no
network, no secrets.
"""

from __future__ import annotations

import hashlib
from pathlib import Path


def _sha256(path: Path) -> str:
    """SHA-256 of a file's bytes, uppercase hex — matches ``Get-FileHash``'s ``.Hash``."""
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest().upper()


def _glob_files(base: Path, lock_glob: str) -> list[Path]:
    """Files matching ``lock_glob`` under ``base``, recursively, sorted by full path.

    Mirrors ``Get-ChildItem -Path $BasePath -Recurse -Filter $LockGlob -File | Sort-Object
    FullName``. A ``-Filter`` in PowerShell is applied at every recursion depth, so a simple
    glob like ``*.test.ts`` still matches files in subdirectories — we therefore route a
    bare pattern through ``rglob``. An explicitly recursive ``**/...`` pattern also goes
    through ``rglob`` (Path.rglob already prepends ``**/``, so strip a leading ``**/``).
    """
    pattern = lock_glob
    if pattern.startswith("**/"):
        pattern = pattern[3:]
    elif pattern.startswith("**"):
        pattern = pattern[2:].lstrip("/")
    files = [p for p in base.rglob(pattern) if p.is_file()]
    return sorted(files, key=lambda p: str(p.resolve()))


def test_hash_map(
    lock_glob: str = "*.test.ts",
    base_path: str | Path | None = None,
) -> dict[str, str]:
    """Port of ``Get-TestHashMap``.

    Build a PER-FILE map ``relativePosixPath -> sha256`` over the lock glob so tamper
    detection can name WHICH file changed. Keys are relative to ``base_path`` (default cwd)
    with forward slashes, inserted in full-path-sorted order for a stable iteration.
    """
    base = Path(base_path) if base_path is not None else Path.cwd()
    base = base.resolve()
    out: dict[str, str] = {}
    for f in _glob_files(base, lock_glob):
        try:
            rel = f.resolve().relative_to(base).as_posix()
        except ValueError:
            # File is not under base (mirrors GetRelativePath's try/catch fallback to the
            # full path with backslashes swapped for slashes).
            rel = str(f.resolve()).replace("\\", "/")
        out[rel] = _sha256(f)
    return out


def test_hash(
    lock_glob: str = "*.test.ts",
    base_path: str | Path | None = None,
) -> str:
    """Port of ``Get-TestHash`` (back-compat single string).

    The engine now derives this from the per-file map via :func:`hash_map_digest`, exactly
    as the PS comment notes. Returns ``""`` when no files match.
    """
    return hash_map_digest(test_hash_map(lock_glob, base_path))


def hash_map_digest(m: dict[str, str] | None) -> str:
    """Port of ``Get-HashMapDigest``.

    Collapse a per-file hash map into a single order-stable string: the hash values taken
    in key-sorted order, concatenated. Empty / ``None`` map -> ``""``.
    """
    if not m:
        return ""
    return "".join(m[k] for k in sorted(m))


def compare_hash_map(
    baseline: dict[str, str] | None,
    current: dict[str, str] | None,
) -> dict[str, object]:
    """Port of ``Compare-HashMap``.

    PURE tamper detector: diff ``baseline`` against ``current`` and report exactly WHICH
    files were modified / added / removed. Returns a dict with ``tampered`` (bool),
    ``changed`` / ``added`` / ``removed`` (sorted lists) and ``reason`` (``""`` when nothing
    changed). The reason format matches the PS exactly::

        locked test file(s) modified a, b; deleted c; added d

    with only the present categories, joined by ``; ``.
    """
    base = baseline or {}
    cur = current or {}

    changed: list[str] = []
    added: list[str] = []
    removed: list[str] = []

    for k in base:
        if k not in cur:
            removed.append(k)
        elif cur[k] != base[k]:
            changed.append(k)
    for k in cur:
        if k not in base:
            added.append(k)

    changed = sorted(changed)
    added = sorted(added)
    removed = sorted(removed)

    tampered = (len(changed) + len(added) + len(removed)) > 0
    reason = ""
    if tampered:
        parts: list[str] = []
        if changed:
            parts.append("modified " + ", ".join(changed))
        if removed:
            parts.append("deleted " + ", ".join(removed))
        if added:
            parts.append("added " + ", ".join(added))
        reason = "locked test file(s) " + "; ".join(parts)

    return {
        "tampered": tampered,
        "changed": changed,
        "added": added,
        "removed": removed,
        "reason": reason,
    }
