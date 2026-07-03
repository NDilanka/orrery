"""Shared test helpers.

The one thing every seed-loading test needs: the absolute repo root, resolved from THIS
file's on-disk location so the suite passes regardless of the invocation CWD (``pytest`` from
the repo root AND from ``engine/`` both work). Tests previously hardcoded repo-root-relative
strings like ``Path("orrery/loops/hello/loop.json")``, which only resolved when pytest happened
to run from the repo root.
"""

from __future__ import annotations

from pathlib import Path

import pytest

# conftest.py lives in engine/tests/ -> parents[0]=tests, [1]=engine, [2]=repo root (D:/dev/loop).
REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def repo_root() -> Path:
    """Absolute repo-root path, resolved from this file's location (CWD-independent)."""
    return REPO_ROOT
