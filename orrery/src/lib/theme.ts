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
} satisfies Record<Exclude<keyof ThemeColors, 'status'>, string>;

const STATUS_TOKEN_MAP: Record<keyof StatusColors, [core: string, base: string]> = {
  run: ['--status-run-core', '--status-run-base'],
  ok: ['--status-ok-core', '--status-ok-base'],
  warn: ['--status-warn-core', '--status-warn-base'],
  err: ['--status-err-core', '--status-err-base'],
  idle: ['--status-idle-core', '--status-idle-base'],
};

// Static fallback table — used for any non-DOM context (SSR, unit tests) and for any
// individual token that fails to resolve. The non-status entries are exactly today's
// literal hex from tokens.css (those tokens are kept as literal hex there, unchanged, for
// this exact reason: this table and the CSS can never drift apart because neither moved).
// The status core/base entries have no pre-existing hex — they're computed once (offline,
// see docs/ui-modernization-plan.md §M0.1) from the same oklch() values tokens.css declares,
// so a non-DOM fallback still lands on the same color a resolved browser would produce.
export const FALLBACK: ThemeColors = {
  void: 0x070912,
  brass: 0xc9a24b,
  starlight: 0xeaf0ff,
  ember: 0xff7a3c,
  cyan: 0x46e0ff,
  amber: 0xffc24b,
  green: 0x5bf09b,
  crimson: 0xff3b5c,
  indigo: 0x1a1740,
  auditor: 0xf4f8ff,
  ghostBrass: 0xc9a24b,
  cacheTeal: 0x2fd9c9,
  horizonRose: 0xff6b7e,
  frost: 0x9fb6ff,
  haiku: 0xff6a4d,
  sonnet: 0xc9a24b,
  opus: 0x9fd0ff,
  hairline: 0xeaf0ff,
  status: {
    run: { core: 0x25d2fc, base: 0x4892a8 },
    ok: { core: 0x56d57b, base: 0x51895e },
    warn: { core: 0xeab532, base: 0x997c3c },
    err: { core: 0xf75c66, base: 0x984649 },
    idle: { core: 0x9ba5b8, base: 0x505561 },
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
  const out = { ...FALLBACK, status: { ...FALLBACK.status } } as ThemeColors;
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
  resolved = out;
  return out;
}

/** The last table initTheme() resolved, or the static FALLBACK if it hasn't run yet. */
export function theme(): ThemeColors {
  return resolved ?? FALLBACK;
}
