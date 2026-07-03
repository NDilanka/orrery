// Single color source for Pixi + uPlot (plan §M0.4, docs/ui-modernization-plan.md).
//
// CSS custom properties in tokens.css are the ONE place a color is authored; this module
// resolves them once at startup into numeric 0xRRGGBB values (what Pixi wants) and plain
// "r, g, b" triples (what uPlot/canvas rgba() strings want), so a token change here
// propagates everywhere — including the canvas — without hand-syncing a second table.
//
// The gotcha: getComputedStyle(el).getPropertyValue('--token') returns the RAW DECLARED
// TEXT (e.g. "oklch(0.7 0.1 220)" or "var(--other-token)"), not a resolved color — so it
// can't be parsed directly regardless of how the token was authored. Instead we set a
// hidden probe element's `color` to `var(--token)` and read back `getComputedStyle(probe)
// .color`, which the browser ALWAYS resolves to "rgb(r, g, b)" / "rgba(r, g, b, a)" — that
// resolved string is what we parse.

export type StatusPair = { core: number; base: number };
export type StatusColors = {
  run: StatusPair;
  ok: StatusPair;
  warn: StatusPair;
  err: StatusPair;
  idle: StatusPair;
};

export type EmTiers = {
  hi: number;
  mid: number;
  low: number;
  faint: number;
};

export type ThemeColors = {
  void: number;
  brass: number;
  starlight: number;
  ember: number;
  cyan: number;
  amber: number;
  green: number;
  crimson: number;
  indigo: number;
  auditor: number;
  ghostBrass: number;
  cacheTeal: number;
  horizonRose: number;
  frost: number;
  haiku: number;
  sonnet: number;
  opus: number;
  hairline: number;
  status: StatusColors;
  // Optional (not required) so existing hand-written ThemeColors-shaped object literals —
  // e.g. Observatory.svelte's `C` palette state, which enumerates every field explicitly —
  // keep typechecking without edits; the M4.5 canvas sweep is what wires `em` into actual
  // consumers. FALLBACK and initTheme()'s resolved table always populate it in practice.
  em?: EmTiers;
};

// The CSS custom property each key resolves from.
const TOKEN_MAP = {
  void: '--void',
  brass: '--brass',
  starlight: '--starlight',
  ember: '--ember',
  cyan: '--plasma-cyan',
  amber: '--amber',
  green: '--plasma-green',
  crimson: '--crimson',
  indigo: '--indigo-night',
  auditor: '--auditor-white',
  ghostBrass: '--ghost-brass',
  cacheTeal: '--cache-teal',
  horizonRose: '--horizon-rose',
  frost: '--frost',
  haiku: '--spectral-haiku',
  sonnet: '--spectral-sonnet',
  opus: '--spectral-opus',
  hairline: '--hairline',
} satisfies Record<Exclude<keyof ThemeColors, 'status' | 'em'>, string>;

const STATUS_TOKEN_MAP: Record<keyof StatusColors, [core: string, base: string]> = {
  run: ['--status-run-core', '--status-run-base'],
  ok: ['--status-ok-core', '--status-ok-base'],
  warn: ['--status-warn-core', '--status-warn-base'],
  err: ['--status-err-core', '--status-err-base'],
  idle: ['--status-idle-core', '--status-idle-base'],
};

// M4.1 (docs/ui-modernization-plan.md §5): the four text-emphasis tiers, exposed for the
// Observatory/Cosmos monochrome sweeps (M4.5) that will drive canvas text/glyph brightness
// off these instead of the retired spectral/plasma hex values.
const EM_TOKEN_MAP: Record<keyof EmTiers, string> = {
  hi: '--em-hi',
  mid: '--em-mid',
  low: '--em-low',
  faint: '--em-faint',
};

// Static fallback table — used for any non-DOM context (SSR, unit tests) and for any
// individual token that fails to resolve. The non-status entries are exactly today's
// literal hex from tokens.css (those tokens are kept as literal hex there, unchanged, for
// this exact reason: this table and the CSS can never drift apart because neither moved).
// The status core/base entries have no pre-existing hex — they're computed once (offline,
// see docs/ui-modernization-plan.md §M0.1) from the same oklch() values tokens.css declares,
// so a non-DOM fallback still lands on the same color a resolved browser would produce.
//
// M4.1 (docs/ui-modernization-plan.md §5) repointed most of these off literal hex and onto
// oklch() grayscale primitives (brass/cyan/amber/crimson/ember/green/frost/cacheTeal/
// horizonRose/haiku/sonnet/opus/ghostBrass) — the values below are the sRGB hex computed
// offline from each token's new oklch() (or, for --ghost-brass, the color channel of its new
// rgba(255,255,255,.12)), by the same OKLab matrices contrast.test.ts uses. void/auditor/
// hairline are untouched (kept literal in tokens.css, not part of the M4 repoint).
//
// White-point raise (2026-07-03, docs/ui-modernization-plan.md §5, owner: "too grayish — use
// pure white when needed"): --em-hi lifted from oklch(0.92 0.005 265)/#e3e4e8 to
// oklch(0.985 0.002 265)/#f9fafb, and --starlight (kept literal in tokens.css) lifted in
// lockstep from #eaf0ff to #f4faff (same oklch chroma/hue, L 0.955 -> 0.985) so the canvas
// light source matches the new DOM white point. Every FALLBACK entry that mirrors one of
// those two tokens (starlight, green, status.run/ok core, em.hi) was recomputed below.
export const FALLBACK: ThemeColors = {
  void: 0x070912,
  brass: 0xc1bdb7, // oklch(0.8 0.01 85) — was gold #c9a24b
  starlight: 0xf4faff, // white-point raise (2026-07-03) — was #eaf0ff (oklch L≈0.955)
  ember: 0x997c3c, // var(--status-warn-base) = oklch(0.6 0.09 85) — was orange #ff7a3c
  cyan: 0xd4d7de, // oklch(0.88 0.01 265) — was cyan #46e0ff
  amber: 0xeab532, // var(--status-warn-core) = oklch(0.8 0.15 85) — unchanged resolved color
  green: 0xf9fafb, // var(--em-hi) = oklch(0.985 0.002 265) — white-point raise (2026-07-03),
  // was #e3e4e8 (oklch(0.92 0.005 265)); originally green #5bf09b pre-M4.1
  crimson: 0xf75c66, // var(--status-err-core) = oklch(0.68 0.19 20) — unchanged resolved color
  indigo: 0x0b0d12, // oklch(0.16 0.01 265) — was #1a1740
  auditor: 0xf4f8ff,
  ghostBrass: 0xffffff, // rgba(255,255,255,.12) — was brass-tinted rgba(201,162,75,.22)
  cacheTeal: 0x7e8085, // oklch(0.6 0.008 265) — was teal #2fd9c9
  horizonRose: 0xf75c66, // var(--status-err-core) — was rose #ff6b7e
  frost: 0x9ea5b2, // oklch(0.72 0.02 265) — was blue #9fb6ff
  haiku: 0x6f7276, // oklch(0.55 0.008 265) — was red dwarf #ff6a4d
  sonnet: 0x9c9ea4, // oklch(0.7 0.008 265) — was gold (var(--brass))
  opus: 0xcbced3, // oklch(0.85 0.008 265) — was blue-white #9fd0ff
  hairline: 0xeaf0ff,
  status: {
    // run/ok now resolve identically (both var(--em-hi) / oklch(0.55 0.008 265) base) —
    // grayscale, distinguished by glyph/shape, never hue alone.
    // core: white-point raise (2026-07-03) — was 0xe3e4e8, var(--em-hi)'s old resolved color
    run: { core: 0xf9fafb, base: 0x6f7276 },
    ok: { core: 0xf9fafb, base: 0x6f7276 },
    warn: { core: 0xeab532, base: 0x997c3c }, // unchanged — still the one amber alert hue
    err: { core: 0xf75c66, base: 0x984649 }, // unchanged — still the one red alert hue
    idle: { core: 0x787a7f, base: 0x383b3f }, // var(--em-low) / oklch(0.35 0.008 265)
  },
  em: {
    hi: 0xf9fafb, // oklch(0.985 0.002 265) — white-point raise (2026-07-03), was oklch(0.92 0.005 265)/#e3e4e8
    mid: 0x9c9ea4, // oklch(0.70 0.008 265)
    low: 0x787a7f, // oklch(0.58 0.008 265) — lifted from the plan's literal .50 (4.5:1 floor)
    faint: 0x616368, // oklch(0.50 0.008 265) — lifted from the plan's literal .38 (3:1 floor)
  },
};

let probe: HTMLElement | null = null;
let resolved: ThemeColors | null = null;

function ensureProbe(): HTMLElement | null {
  if (typeof document === 'undefined') return null;
  if (!probe) {
    probe = document.createElement('span');
    probe.style.position = 'absolute';
    probe.style.visibility = 'hidden';
    probe.style.pointerEvents = 'none';
    probe.setAttribute('aria-hidden', 'true');
    document.body.appendChild(probe);
  }
  return probe;
}

/** "rgb(r, g, b)" | "rgba(r, g, b, a)" -> [r, g, b] (0-255 each), or null if unparseable. */
export function parseRgbTriple(rgbString: string): [number, number, number] | null {
  const m = rgbString.match(/rgba?\(\s*(-?[\d.]+)[\s,]+(-?[\d.]+)[\s,]+(-?[\d.]+)/i);
  if (!m) return null;
  const clamp = (v: string) => Math.round(Math.min(255, Math.max(0, parseFloat(v))));
  return [clamp(m[1]), clamp(m[2]), clamp(m[3])];
}

/** "rgb(r, g, b)" | "rgba(r, g, b, a)" -> 0xRRGGBB, or null if unparseable. */
export function parseRgbToHex(rgbString: string): number | null {
  const t = parseRgbTriple(rgbString);
  if (!t) return null;
  return (t[0] << 16) | (t[1] << 8) | t[2];
}

/** Resolve a single `--token` (any syntax: hex, oklch(), color-mix(), …) to "r, g, b", or null
 *  outside a browser / before the DOM is ready. */
export function resolveVarRgbTriple(token: string): string | null {
  const el = ensureProbe();
  if (!el) return null;
  el.style.color = `var(${token})`;
  const computed = getComputedStyle(el).color;
  const t = parseRgbTriple(computed);
  return t ? t.join(', ') : null;
}

function resolveVarHex(token: string): number | null {
  const triple = resolveVarRgbTriple(token);
  if (!triple) return null;
  const [r, g, b] = triple.split(',').map((n) => parseInt(n, 10));
  return (r << 16) | (g << 8) | b;
}

/**
 * Resolve every themed color from the live CSS custom properties ONCE. Call during onMount,
 * before any Pixi/uPlot setup that needs numeric colors — safe to call more than once
 * (idempotent; just re-resolves and replaces the cached table). Any token that fails to
 * resolve (shouldn't happen in a browser, but never trust it) keeps its FALLBACK value so a
 * caller never sees `undefined`/NaN.
 */
export function initTheme(): ThemeColors {
  if (typeof document === 'undefined') return FALLBACK;
  // FALLBACK.em is always populated (it's a fully-specified literal above) — `em` is only
  // optional on the ThemeColors *type* so hand-written partial literals elsewhere (e.g.
  // Observatory.svelte's `C` palette state) keep typechecking without listing every em key.
  const fallbackEm: EmTiers = FALLBACK.em ?? { hi: 0, mid: 0, low: 0, faint: 0 };
  const out = { ...FALLBACK, status: { ...FALLBACK.status }, em: { ...fallbackEm } } as ThemeColors;
  for (const key of Object.keys(TOKEN_MAP) as (keyof typeof TOKEN_MAP)[]) {
    const v = resolveVarHex(TOKEN_MAP[key]);
    if (v != null) out[key] = v;
  }
  for (const key of Object.keys(STATUS_TOKEN_MAP) as (keyof StatusColors)[]) {
    const [coreTok, baseTok] = STATUS_TOKEN_MAP[key];
    const core = resolveVarHex(coreTok);
    const base = resolveVarHex(baseTok);
    out.status[key] = {
      core: core ?? FALLBACK.status[key].core,
      base: base ?? FALLBACK.status[key].base,
    };
  }
  for (const key of Object.keys(EM_TOKEN_MAP) as (keyof EmTiers)[]) {
    const v = resolveVarHex(EM_TOKEN_MAP[key]);
    out.em![key] = v ?? fallbackEm[key];
  }
  resolved = out;
  return out;
}

/** The last table initTheme() resolved, or the static FALLBACK if it hasn't run yet. */
export function theme(): ThemeColors {
  return resolved ?? FALLBACK;
}
