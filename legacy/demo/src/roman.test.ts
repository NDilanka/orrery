// roman.test.ts — the EVAL GATE. Human-written, authoritative.
//
// The loop orchestrator (loop.ps1) snapshots a SHA-256 hash of every *.test.ts
// file before iteration 1 and re-checks it every iteration. If this file is
// edited, skipped, or deleted, the loop stops and hands off to a human.
// The agent fixes src/roman.ts to satisfy these tests — it never touches them.

import { test, expect } from "bun:test";
import { toRoman, fromRoman } from "./roman";

const cases: Array<[number, string]> = [
  [1, "I"],
  [3, "III"],
  [4, "IV"],
  [9, "IX"],
  [14, "XIV"],
  [40, "XL"],
  [90, "XC"],
  [400, "CD"],
  [900, "CM"],
  [1984, "MCMLXXXIV"],
  [2026, "MMXXVI"],
  [3888, "MMMDCCCLXXXVIII"],
  [3999, "MMMCMXCIX"],
];

test("toRoman encodes subtractive forms", () => {
  for (const [n, r] of cases) expect(toRoman(n)).toBe(r);
});

test("fromRoman decodes subtractive forms", () => {
  for (const [n, r] of cases) expect(fromRoman(r)).toBe(n);
});

test("round-trips across the full range", () => {
  for (let n = 1; n <= 3999; n++) {
    expect(fromRoman(toRoman(n))).toBe(n);
  }
});
