// roman.ts — convert between integers (1..3999) and Roman numerals.
//
// This implementation is DELIBERATELY INCOMPLETE. It is the module the loop
// must fix. Make `bun test` pass by editing ONLY this file.
//
//   toRoman(4)   should be "IV"   (naive repetition gives "IIII")
//   toRoman(9)   should be "IX"
//   fromRoman    is not implemented at all
//
// Do not edit src/roman.test.ts — the orchestrator verifies it is unchanged.

export function toRoman(n: number): string {
  // Naive additive-only encoding. Handles X/V/I by repetition.
  // Fails every subtractive case (4, 9, 40, 90, 400, 900, ...).
  let out = "";
  while (n >= 10) { out += "X"; n -= 10; }
  while (n >= 5)  { out += "V"; n -= 5; }
  while (n >= 1)  { out += "I"; n -= 1; }
  return out;
}

export function fromRoman(s: string): number {
  // TODO: not implemented.
  return 0;
}
