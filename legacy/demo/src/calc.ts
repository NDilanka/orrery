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
// Precedence-climbing (recursive descent) parser.
//   expr    = term (('+'|'-') term)*
//   term    = power (('*'|'/') power)*
//   power   = unary ('^' power)?        // right-associative, binds above * /
//   unary   = '-' unary | primary
//   primary = number | '(' expr ')'

export function evaluate(expr: string): number {
  let i = 0;
  const s = expr;

  function skipWs(): void {
    while (i < s.length && /\s/.test(s[i]!)) i++;
  }

  function peek(): string | undefined {
    skipWs();
    return s[i];
  }

  function parseExpr(): number {
    let value = parseTerm();
    for (;;) {
      const op = peek();
      if (op === "+") { i++; value += parseTerm(); }
      else if (op === "-") { i++; value -= parseTerm(); }
      else return value;
    }
  }

  function parseTerm(): number {
    let value = parsePower();
    for (;;) {
      const op = peek();
      if (op === "*") { i++; value *= parsePower(); }
      else if (op === "/") { i++; value /= parsePower(); }
      else return value;
    }
  }

  function parsePower(): number {
    const base = parseUnary();
    if (peek() === "^") { i++; return base ** parsePower(); }
    return base;
  }

  function parseUnary(): number {
    if (peek() === "-") { i++; return -parseUnary(); }
    return parsePrimary();
  }

  function parsePrimary(): number {
    const c = peek();
    if (c === "(") {
      i++;
      const value = parseExpr();
      if (peek() !== ")") throw new Error("expected )");
      i++;
      return value;
    }
    skipWs();
    const start = i;
    while (i < s.length && /[0-9.]/.test(s[i]!)) i++;
    if (i === start) throw new Error(`unexpected token at ${start}: ${s[start] ?? "EOF"}`);
    return Number(s.slice(start, i));
  }

  const result = parseExpr();
  if (peek() !== undefined) throw new Error(`unexpected trailing input at ${i}`);
  return result;
}
