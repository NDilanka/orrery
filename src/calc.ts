// calc.ts — evaluate arithmetic expressions. DELIBERATELY INCOMPLETE.
// Make `bun test` pass by editing ONLY this file (never src/calc.test.ts).
//
// Required semantics (see src/calc.test.ts for the exact cases):
//   + - * /   standard precedence: * and / bind tighter than + and -
//   ( )       parentheses override precedence
//   -x        unary minus
//   ^         exponent, RIGHT-associative, binds tighter than * and /
//   division  real-valued (7/2 === 3.5)
//   whitespace is ignored
//
// This stub only parses a bare integer, so every non-trivial case fails.

export function evaluate(expr: string): number {
  return Number(expr);
}
