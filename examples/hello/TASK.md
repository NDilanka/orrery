# Task: make `mathlib` correct

`src/mathlib.py` has two deliberately wrong implementations. Fix them so the
test suite passes. Edit **only** `src/mathlib.py` — do not touch anything under
`tests/`.

## Goal

`pytest -q` reports all tests passing (green).

## Acceptance Criteria

- `add(a, b)` returns the arithmetic sum of `a` and `b`.
- `multiply(a, b)` returns the arithmetic product of `a` and `b`.
- All tests in `tests/test_mathlib.py` pass.
- The test file is unchanged (it is hash-locked by the loop).
