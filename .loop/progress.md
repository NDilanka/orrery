# Progress

## Status: DONE (TASK.calc.md) — bun test green

## Done
- Replaced the `Number(expr)` stub in `src/calc.ts` with a precedence-climbing
  recursive-descent parser:
  - `expr` = `+ -` (left-assoc) over `term`
  - `term` = `* /` (left-assoc, real division) over `power`
  - `power` = `^` right-associative, binds above `* /`
  - `unary` = leading `-`
  - `primary` = number | `( expr )`
  - whitespace skipped between tokens; throws on trailing/unexpected input.
- Did NOT touch `src/roman.*` or any `*.test.ts`.

## Result
`bun test`: 9 pass / 0 fail (6 calc + 3 roman). All original tests present
and unmodified.

## Failing / Next
Nothing failing. Task complete. If extending: add support for decimals in
input is already handled; could add modulo/`%` or function calls if a future
TASK requires it.

## Notes
calc is independent of roman, so no regression risk across modules.
