# Progress

## Status: not started

## Done
(none yet)

## Failing / Next
Run `bun test` and start from the first failing assertion in src/roman.test.ts.

## Notes
This file is the loop's memory. Each iteration runs with FRESH context (no
session resume), so the only state that survives is what is written here, in
git history, and in .loop/log.jsonl. Keep this concise and current.
