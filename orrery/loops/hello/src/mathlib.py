"""mathlib — a tiny module the seeded Orrery loop must fix.

This implementation is DELIBERATELY BROKEN so the seed starts RED and the
fix-until-green loop has something to do. The agent edits ONLY this file; the
test file in ``tests/`` is the human-written eval gate and is hash-locked.

    add(2, 3)        should be 5   (currently returns a - b -> -1)
    multiply(2, 3)   should be 6   (currently returns a + b -> 5)

Fix the bugs below until ``pytest -q`` is green.
"""

from __future__ import annotations


def add(a: int, b: int) -> int:
    # BUG: should be ``a + b``.
    return a - b


def multiply(a: int, b: int) -> int:
    # BUG: should be ``a * b``.
    return a + b
