// calc.test.ts — eval gate for the calc module. Human-written, authoritative.
// The loop must satisfy these without editing this file (hash-checked each iter).

import { test, expect } from "bun:test";
import { evaluate } from "./calc";

test("precedence: * and / bind tighter than + and -", () => {
  expect(evaluate("1+2*3")).toBe(7);
  expect(evaluate("2*3+4")).toBe(10);
  expect(evaluate("10-2*3")).toBe(4);
  expect(evaluate("8/2+1")).toBe(5);
});

test("parentheses override precedence", () => {
  expect(evaluate("(1+2)*3")).toBe(9);
  expect(evaluate("2*(3+4)")).toBe(14);
  expect(evaluate("((1+2)*(3+4))")).toBe(21);
});

test("unary minus", () => {
  expect(evaluate("-3")).toBe(-3);
  expect(evaluate("-3+4")).toBe(1);
  expect(evaluate("2*-3")).toBe(-6);
  expect(evaluate("-(1+2)")).toBe(-3);
});

test("exponent is right-associative and binds above multiply", () => {
  expect(evaluate("2^3")).toBe(8);
  expect(evaluate("2^3^2")).toBe(512); // 2^(3^2) = 2^9, not (2^3)^2 = 64
  expect(evaluate("2*2^3")).toBe(16);  // 2*(2^3)
});

test("whitespace is ignored", () => {
  expect(evaluate("  1 +  2 * 3 ")).toBe(7);
});

test("division is real-valued", () => {
  expect(evaluate("7/2")).toBeCloseTo(3.5, 10);
  expect(evaluate("1/4")).toBeCloseTo(0.25, 10);
});
