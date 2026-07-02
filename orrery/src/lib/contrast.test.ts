// M3.3 automated contrast audit (docs/ui-modernization-plan.md §M3.3): resolves the actual
// color tokens out of render/tokens.css as text (not via jsdom getComputedStyle — jsdom has
// no real paint engine and never resolves oklch()/var() at all, see theme.test.ts's header
// comment for the same reasoning) and checks WCAG contrast ratios against the semantic
// surfaces those tokens are meant to sit on.
//
// This is deliberately independent of theme.ts: theme.ts is the Pixi/uPlot runtime bridge
// (CSS var -> resolved rgb string -> packed int) and only covers named v1/v2 colors + status
// core/base, not the tier-1/tier-2 CSS custom properties (--n1..--n4, --text-*, --surface-*)
// this audit needs. Re-implementing a tiny oklch->sRGB conversion here keeps this test
// self-contained and honest about what tokens.css actually declares, byte for byte.

import { describe, it, expect } from 'vitest';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import path from 'node:path';

const TOKENS_PATH = path.join(path.dirname(fileURLToPath(import.meta.url)), 'render', 'tokens.css');
const css = readFileSync(TOKENS_PATH, 'utf8');

// ── parse every custom property out of the :root block ─────────────────────────────────────
const rootBlockMatch = css.match(/:root\s*{([\s\S]*?)\n}/);
if (!rootBlockMatch) throw new Error('tokens.css: could not locate :root block');
const rootBlock = rootBlockMatch[1];

const rawTokens: Record<string, string> = {};
const tokenRe = /--([\w-]+):\s*([^;]+);/g;
let tm: RegExpExecArray | null;
while ((tm = tokenRe.exec(rootBlock))) {
  rawTokens['--' + tm[1]] = tm[2].trim();
}

// ── oklch -> linear-sRGB -> gamma-encoded sRGB (Björn Ottosson's OKLab matrices) ────────────
function oklchToRgb(L: number, C: number, H: number): { r: number; g: number; b: number } {
  const hRad = (H * Math.PI) / 180;
  const a = C * Math.cos(hRad);
  const b = C * Math.sin(hRad);
  const l_ = L + 0.3963377774 * a + 0.2158037573 * b;
  const m_ = L - 0.1055613458 * a - 0.0638541728 * b;
  const s_ = L - 0.0894841775 * a - 1.291485548 * b;
  const l = l_ ** 3;
  const m = m_ ** 3;
  const s = s_ ** 3;
  const rLin = 4.0767416621 * l - 3.3077115913 * m + 0.2309699292 * s;
  const gLin = -1.2684380046 * l + 2.6097574011 * m - 0.3413193965 * s;
  const bLin = -0.0041960863 * l - 0.7034186147 * m + 1.707614701 * s;
  const enc = (c: number) => {
    const clamped = Math.min(1, Math.max(0, c));
    return (clamped <= 0.0031308 ? 12.92 * clamped : 1.055 * Math.pow(clamped, 1 / 2.4) - 0.055) * 255;
  };
  return { r: enc(rLin), g: enc(gLin), b: enc(bLin) };
}

type RGBA = { r: number; g: number; b: number; a: number };

function parseLiteral(str: string): RGBA {
  const s = str.trim();
  let m: RegExpMatchArray | null;
  if ((m = s.match(/^oklch\(\s*([\d.]+)\s+([\d.]+)\s+([\d.]+)\s*(?:\/\s*([\d.]+)(%)?\s*)?\)$/i))) {
    const L = parseFloat(m[1]);
    const C = parseFloat(m[2]);
    const H = parseFloat(m[3]);
    let A = m[4] !== undefined ? parseFloat(m[4]) : 1;
    if (m[5]) A = A / 100;
    return { ...oklchToRgb(L, C, H), a: A };
  }
  if ((m = s.match(/^#([0-9a-f]{3})$/i))) {
    const hex = m[1].split('').map((c) => parseInt(c + c, 16));
    return { r: hex[0], g: hex[1], b: hex[2], a: 1 };
  }
  if ((m = s.match(/^#([0-9a-f]{6})$/i))) {
    const h = m[1];
    return { r: parseInt(h.slice(0, 2), 16), g: parseInt(h.slice(2, 4), 16), b: parseInt(h.slice(4, 6), 16), a: 1 };
  }
  if ((m = s.match(/^#([0-9a-f]{8})$/i))) {
    const h = m[1];
    return {
      r: parseInt(h.slice(0, 2), 16),
      g: parseInt(h.slice(2, 4), 16),
      b: parseInt(h.slice(4, 6), 16),
      a: parseInt(h.slice(6, 8), 16) / 255,
    };
  }
  if ((m = s.match(/^rgba?\(\s*([\d.]+)\s*,\s*([\d.]+)\s*,\s*([\d.]+)\s*(?:,\s*([\d.]+)\s*)?\)$/i))) {
    return {
      r: parseFloat(m[1]),
      g: parseFloat(m[2]),
      b: parseFloat(m[3]),
      a: m[4] !== undefined ? parseFloat(m[4]) : 1,
    };
  }
  throw new Error(`contrast.test.ts: unparseable color literal "${str}"`);
}

function resolveToken(name: string, depth = 0): RGBA {
  if (depth > 10) throw new Error(`contrast.test.ts: var() cycle resolving ${name}`);
  const raw = rawTokens[name];
  if (raw === undefined) throw new Error(`contrast.test.ts: unknown token ${name}`);
  const varMatch = raw.match(/^var\(\s*(--[\w-]+)\s*\)$/);
  if (varMatch) return resolveToken(varMatch[1], depth + 1);
  return parseLiteral(raw);
}

// ── WCAG relative luminance + contrast ratio ────────────────────────────────────────────────
function flattenOver(fg: RGBA, bg: RGBA): RGBA {
  if (fg.a >= 1) return fg;
  return {
    r: fg.r * fg.a + bg.r * (1 - fg.a),
    g: fg.g * fg.a + bg.g * (1 - fg.a),
    b: fg.b * fg.a + bg.b * (1 - fg.a),
    a: 1,
  };
}

function relativeLuminance({ r, g, b }: RGBA): number {
  const lin = (c: number) => {
    const v = c / 255;
    return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4);
  };
  return 0.2126 * lin(r) + 0.7152 * lin(g) + 0.0722 * lin(b);
}

/** Contrast ratio of `fgName` composited over `bgName`'s opaque surface. */
function contrast(fgName: string, bgName: string): number {
  const bg = resolveToken(bgName);
  const fg = flattenOver(resolveToken(fgName), bg);
  const L1 = relativeLuminance(fg);
  const L2 = relativeLuminance(bg);
  const lighter = Math.max(L1, L2);
  const darker = Math.min(L1, L2);
  return (lighter + 0.05) / (darker + 0.05);
}

// ── fixtures ─────────────────────────────────────────────────────────────────────────────
// --surface-panel is a Tier-2 alias for --n2 (tokens.css line 95: `--surface-panel: var(--n2)`)
// — kept as two separate check targets anyway per the plan's explicit "surface-panel and
// n1/n2" wording, since a future edit could point surface-panel elsewhere.
const BACKGROUNDS = ['--surface-panel', '--n1', '--n2'] as const;

// normal-weight body/label text — WCAG 1.4.3 AA (4.5:1) applies in full.
const BODY_TEXT_TOKENS = ['--text-primary', '--text-dim', '--text-meta'] as const;

const STATUS_CORE_TOKENS = [
  '--status-run-core',
  '--status-ok-core',
  '--status-warn-core',
  '--status-err-core',
  '--status-idle-core',
] as const;

describe('contrast audit (tokens.css, docs/ui-modernization-plan.md §M3.3)', () => {
  it.each(BODY_TEXT_TOKENS.flatMap((fg) => BACKGROUNDS.map((bg) => [fg, bg] as const)))(
    '%s on %s is >=4.5:1 (WCAG AA text)',
    (fg, bg) => {
      const ratio = contrast(fg, bg);
      expect(ratio, `${fg} on ${bg} = ${ratio.toFixed(2)}:1`).toBeGreaterThanOrEqual(4.5);
    },
  );

  // --text-faint (tokens.css lines 84-85) is documented as decorative-only: "lifted 0.34 ->
  // 0.46: decorative-only glyphs >=11px (WCAG non-text 3:1)" — it is NOT held to the 4.5:1
  // text bar, only the 3:1 non-text/graphical-object bar, by the token's own comment.
  it.each(BACKGROUNDS.map((bg) => [bg] as const))(
    '--text-faint on %s is >=3:1 (documented decorative-only exception, tokens.css:84-85)',
    (bg) => {
      const ratio = contrast('--text-faint', bg);
      expect(ratio, `--text-faint on ${bg} = ${ratio.toFixed(2)}:1`).toBeGreaterThanOrEqual(3);
    },
  );

  // status -core tier is spec'd for "small/bright: cores, rims, chips, glyph text"
  // (tokens.css lines 38-40) — treated as the 3:1 UI-component/large-text bar, checked
  // against both void-tier backgrounds it actually renders on.
  it.each(STATUS_CORE_TOKENS.flatMap((fg) => (['--n1', '--n2'] as const).map((bg) => [fg, bg] as const)))(
    '%s on %s is >=3:1 (WCAG UI component)',
    (fg, bg) => {
      const ratio = contrast(fg, bg);
      expect(ratio, `${fg} on ${bg} = ${ratio.toFixed(2)}:1`).toBeGreaterThanOrEqual(3);
    },
  );

  it('--border-focus (== --plasma-cyan) on --n1 is >=3:1 (WCAG focus-indicator UI component)', () => {
    const focusRatio = contrast('--border-focus', '--n1');
    const cyanRatio = contrast('--plasma-cyan', '--n1');
    expect(focusRatio, `--border-focus on --n1 = ${focusRatio.toFixed(2)}:1`).toBeGreaterThanOrEqual(3);
    // --border-focus is a bare alias of --plasma-cyan (tokens.css line 100) — same resolved
    // color, so both must report identical ratios.
    expect(cyanRatio).toBeCloseTo(focusRatio, 6);
  });
});

describe('oklch -> sRGB conversion self-check', () => {
  it('oklch(1 0 0) (pure white, zero chroma) resolves to white', () => {
    const white = oklchToRgb(1, 0, 0);
    expect(white.r).toBeCloseTo(255, 0);
    expect(white.g).toBeCloseTo(255, 0);
    expect(white.b).toBeCloseTo(255, 0);
  });

  it('oklch(0 0 0) (pure black) resolves to black', () => {
    const black = oklchToRgb(0, 0, 0);
    expect(black.r).toBeCloseTo(0, 0);
    expect(black.g).toBeCloseTo(0, 0);
    expect(black.b).toBeCloseTo(0, 0);
  });

  it('parses the alpha-bearing --hairline token (oklch(1 0 0 / 8%))', () => {
    const hairline = resolveToken('--hairline');
    expect(hairline.a).toBeCloseTo(0.08, 6);
    expect(hairline.r).toBeCloseTo(255, 0);
  });
});
