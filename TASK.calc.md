# TASK (harder demo)

## Goal
Make `bun test` pass (exit code 0) for the **calc** module, without breaking any
other already-passing test in the repo.

## Module under test
- Implementation: `src/calc.ts`       ← EDIT THIS
- Tests:          `src/calc.test.ts`   ← DO NOT EDIT / SKIP / DELETE

`bun test` runs the WHOLE repo. The `roman` module is already green — do not
touch `src/roman.*`. If you make a passing test fail, the orchestrator detects
the regression and rolls the working tree back to the last best state.

## Required semantics
- `+ - * /` with standard precedence (`*` `/` bind tighter than `+` `-`)
- parentheses; unary minus (`-3`, `2*-3`, `-(1+2)`)
- `^` exponent, **right-associative**, binds tighter than `*` `/`
  (`2^3^2 == 512`)
- division is real-valued (`7/2 == 3.5`)
- whitespace ignored

A precedence-climbing / Pratt parser is the clean approach.

## Definition of done
`bun test` reports 0 failures, all original tests present and unmodified.

## Working agreement (each iteration)
1. Read this file and `.loop/progress.md`.
2. `bun test`; fix the FIRST failing assertion with the smallest change to
   `src/calc.ts`. Re-run to confirm no regression.
3. Update `.loop/progress.md` (changed / still-failing / next).
4. If stuck, write `BLOCKED: <reason>` on the first line of `.loop/progress.md`.
