"""The eval gate — human-written. The loop must NOT edit this file.

These assertions fail against the deliberate bug in ``src/mathlib.py`` and pass
once the implementation is correct. ``pytest -q`` reports the
``N passed`` / ``N failed`` summary the loop's gate parses.
"""

import sys
from pathlib import Path

# Make ``src/`` importable when pytest runs from the example root.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from mathlib import add, multiply  # noqa: E402


def test_add():
    assert add(2, 3) == 5
    assert add(-1, 1) == 0


def test_multiply():
    assert multiply(2, 3) == 6
    assert multiply(0, 9) == 0
