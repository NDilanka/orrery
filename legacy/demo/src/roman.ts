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

// Value/symbol table ordered high→low, including subtractive forms.
const TABLE: Array<[number, string]> = [
  [1000, "M"], [900, "CM"], [500, "D"], [400, "CD"],
  [100, "C"], [90, "XC"], [50, "L"], [40, "XL"],
  [10, "X"], [9, "IX"], [5, "V"], [4, "IV"], [1, "I"],
];

export function toRoman(n: number): string {
  let out = "";
  for (const [value, symbol] of TABLE) {
    while (n >= value) { out += symbol; n -= value; }
  }
  return out;
}

export function fromRoman(s: string): number {
  let n = 0;
  let i = 0;
  for (const [value, symbol] of TABLE) {
    while (s.startsWith(symbol, i)) { n += value; i += symbol.length; }
  }
  return n;
}
