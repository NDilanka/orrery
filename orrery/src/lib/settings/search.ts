// settings/search.ts — pure settings search over the REGISTRY. No Svelte / Tauri
// imports so it stays trivially unit-testable. Reuses the CommandPalette fuzzy
// idiom (substring OR in-order subsequence, case-insensitive) applied across each
// setting's label + description + keywords.

import { REGISTRY } from './schema';

/** One flattened, pre-lowercased search row. */
interface IndexEntry {
  key: string;
  category: string;
  /** label + description + keywords, joined + lowercased once at build time. */
  haystack: string;
}

/** Built once from the static REGISTRY. */
const INDEX: IndexEntry[] = REGISTRY.map((m) => ({
  key: m.key,
  category: m.category,
  haystack: [m.label, m.description, ...m.keywords].join(' ').toLowerCase(),
}));

export interface SettingsSearchResult {
  /** the setting keys that matched. */
  keys: Set<string>;
  /** match count per category (only categories with ≥1 hit appear). */
  byCategory: Record<string, number>;
}

/**
 * Case-insensitive substring-or-subsequence match — the exact CommandPalette
 * idiom (`matches()`), against an already-lowercased haystack.
 */
function matches(haystack: string, query: string): boolean {
  if (!query) return true;
  if (haystack.includes(query)) return true;
  let i = 0;
  for (const ch of haystack) {
    if (ch === query[i]) i++;
    if (i === query.length) return true;
  }
  return false;
}

/** Search the settings registry. Empty/whitespace query → every setting. */
export function searchSettings(query: string): SettingsSearchResult {
  const q = query.trim().toLowerCase();
  const keys = new Set<string>();
  const byCategory: Record<string, number> = {};
  for (const entry of INDEX) {
    if (matches(entry.haystack, q)) {
      keys.add(entry.key);
      byCategory[entry.category] = (byCategory[entry.category] ?? 0) + 1;
    }
  }
  return { keys, byCategory };
}
