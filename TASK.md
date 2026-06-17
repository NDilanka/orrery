# TASK

## Goal
Make `bun test` pass (exit code 0) for the `roman` module, with all original
tests present and unmodified.

## Module under test
- Implementation: `src/roman.ts`        ← EDIT THIS
- Tests:          `src/roman.test.ts`    ← DO NOT EDIT / SKIP / DELETE

The orchestrator hashes every `*.test.ts` file before the loop starts and
re-checks it each iteration. Any change to a test file = immediate human
handoff. The point of the loop is to satisfy the tests, not to change them.

## Definition of done
`bun test` reports **0 failures** with all three original tests present.

## Working agreement (each iteration)
1. Read this file and `.loop/progress.md`.
2. Run `bun test`. Read the FIRST failing assertion.
3. Make the SMALLEST change to `src/roman.ts` that fixes it.
4. Update `.loop/progress.md`: what you changed, what still fails, what's next.
5. If you cannot make progress, write `BLOCKED: <reason>` on the FIRST line of
   `.loop/progress.md` and stop — do not thrash.
