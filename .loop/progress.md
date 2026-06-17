# Progress

## Status: DONE

## Done
- Replaced naive additive `toRoman` with a value/symbol table (high→low,
  including subtractive forms CM/CD/XC/XL/IX/IV) using greedy subtraction.
- Implemented `fromRoman` by scanning the same table in order, consuming each
  symbol with `startsWith(symbol, i)`.
- `bun test`: 3 pass, 0 fail (4025 expect() calls). All three original tests
  present and unmodified.

## Failing / Next
Nothing failing. Definition of done met (0 failures, all tests intact).

## Notes
This file is the loop's memory. Each iteration runs with FRESH context (no
session resume), so the only state that survives is what is written here, in
git history, and in .loop/log.jsonl. Keep this concise and current.
