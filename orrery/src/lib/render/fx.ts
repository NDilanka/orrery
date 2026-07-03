// Shared PixiJS canvas-fx builders (plan §M2.1/M2.3/M2.4, docs/ui-modernization-plan.md).
// Consumed by Observatory this wave; Cosmos reuses the SAME builders next wave (§M2.9) so
// glow/vignette/starfield/ring rendering doesn't fork into two hand-duplicated implementations
// the way the old per-file `C = {}` palettes did (plan audit item #6).
//
// Deliberately has ZERO runtime dependency on 'pixi.js' — only TYPE-ONLY imports (`import
// type`), erased at build time. Observatory (and Cosmos) already dynamically `await
// import('pixi.js')` inside onMount, client-only, specifically so Pixi never runs at
// SSR/prerender time; a static runtime import here would defeat that. Every function below
// instead takes the already-imported PIXI namespace as its first argument.

import type * as PixiNS from 'pixi.js';

type Pixi = typeof PixiNS;

// ── color math ───────────────────────────────────────────────────────────────

/** "0xRRGGBB, alpha" -> "rgba(r, g, b, a)" (what FillGradient color stops want). */
export function rgba(hex: number, alpha: number): string {
  const r = (hex >> 16) & 255;
  const g = (hex >> 8) & 255;
  const b = hex & 255;
  return `rgba(${r}, ${g}, ${b}, ${alpha})`;
}

/** Linear-mix two 0xRRGGBB colors, k=0 -> a, k=1 -> b. */
export function mixColor(a: number, b: number, k: number): number {
  const ar = (a >> 16) & 255,
    ag = (a >> 8) & 255,
    ab = a & 255;
  const br = (b >> 16) & 255,
    bg = (b >> 8) & 255,
    bb = b & 255;
  const r = Math.round(ar + (br - ar) * k);
  const g = Math.round(ag + (bg - ag) * k);
  const bl = Math.round(ab + (bb - ab) * k);
  return (r << 16) | (g << 8) | bl;
}

/**
 * Derive a muted "base" tier variant of a hue that isn't one of theme.ts's 5 fixed status
 * pairs (e.g. the star's rest-state silhouette hues — ember/frost/crimson/green/starlight —
 * which are a per-silhouette identity, not the generic run/ok/warn/err/idle vocabulary).
 * Mixing toward `voidColor` lowers both lightness and effective chroma together, the same
 * direction theme.ts's -base tokens take relative to their -core sibling (plan §1 "chroma
 * budget by area"). Small accents within the same drawing keep the un-muted color (the
 * "core" tier) — only large fills/glow move through this.
 */
export function muteColor(hex: number, voidColor: number, k = 0.4): number {
  return mixColor(hex, voidColor, k);
}

// ── glow sprite texture (plan §M2.1) ─────────────────────────────────────────

export type GlowStop = { r: number; alpha: number };

/** Inverse-square-ish falloff: r×1.0/1.6/2.6/4.0 -> alpha 0.12/0.06/0.03/0.015. */
export const DEFAULT_GLOW_STOPS: GlowStop[] = [
  { r: 1.0, alpha: 0.12 },
  { r: 1.6, alpha: 0.06 },
  { r: 2.6, alpha: 0.03 },
  { r: 4.0, alpha: 0.015 },
];

/**
 * M5.3 (docs/ui-modernization-plan.md §6, additive — DEFAULT_GLOW_STOPS/makeGlowTexture's
 * default behavior is untouched, Cosmos's existing `makeGlowTexture(PIXI, renderer)` calls
 * still get DEFAULT_GLOW_STOPS unchanged). A gentler, wider-reaching falloff for the running
 * star's second "wide bleed" halo layer — a lower peak alpha spread over a bigger radius so a
 * large on-screen scale reads as an atmosphere the light bleeds into (screen-blend, reaching
 * past the orbits) rather than a bigger hot disc. Pass via `makeGlowTexture(PIXI, renderer,
 * { stops: WIDE_GLOW_STOPS, size: 320 })`.
 */
export const WIDE_GLOW_STOPS: GlowStop[] = [
  { r: 1.0, alpha: 0.13 },
  { r: 2.2, alpha: 0.075 },
  { r: 3.6, alpha: 0.04 },
  { r: 5.5, alpha: 0.018 },
];

/**
 * M5.3 (additive, same non-goal as WIDE_GLOW_STOPS above): a hotter, brighter-peaked falloff
 * for the star's own tight corona specifically — the owner's core M5 complaint was the
 * monochrome scene reading "dull"; DEFAULT_GLOW_STOPS' 0.12 peak (tuned for planet glows,
 * which stay small/restrained per plan §6) reads too faint blown up to star-corona scale.
 * ~1.8× DEFAULT_GLOW_STOPS at every stop. Pass via `makeGlowTexture(PIXI, renderer, { stops:
 * BURN_GLOW_STOPS })` — planets keep using the DEFAULT-stops texture, untouched.
 */
export const BURN_GLOW_STOPS: GlowStop[] = [
  { r: 1.0, alpha: 0.22 },
  { r: 1.6, alpha: 0.11 },
  { r: 2.6, alpha: 0.055 },
  { r: 4.0, alpha: 0.025 },
];

export type GlowTexture = {
  texture: PixiNS.Texture;
  /** texture px per r=1.0 unit. Size a sprite for a desired on-screen radius R with
   *  `sprite.scale.set(R / unitPx)` (anchor must be 0.5/0.5). */
  unitPx: number;
};

/**
 * Pre-render a white radial glow once (at startup, after the renderer exists) so every
 * corona/halo/mote at runtime is a single tinted+scaled Sprite draw instead of N stacked
 * alpha circles per frame — the banded "stacked-circle corona" this replaces. Tint via
 * `sprite.tint`; the last stop is padded ~15% further out and faded to 0 so the texture
 * never shows a hard circular clip edge (no banding at the boundary).
 */
export function makeGlowTexture(
  PIXI: Pixi,
  renderer: PixiNS.Renderer,
  opts?: { stops?: GlowStop[]; size?: number },
): GlowTexture {
  const stops = opts?.stops ?? DEFAULT_GLOW_STOPS;
  const size = opts?.size ?? 256;
  const maxR = stops[stops.length - 1].r * 1.15;
  const half = size / 2;

  const g = new PIXI.Graphics();
  const grad = new PIXI.FillGradient({
    type: 'radial',
    center: { x: 0.5, y: 0.5 },
    innerRadius: 0,
    outerCenter: { x: 0.5, y: 0.5 },
    outerRadius: 0.5,
    colorStops: [
      { offset: 0, color: rgba(0xffffff, stops[0].alpha) },
      ...stops.map((s) => ({ offset: s.r / maxR, color: rgba(0xffffff, s.alpha) })),
      { offset: 1, color: rgba(0xffffff, 0) },
    ],
    textureSpace: 'local',
  });
  g.circle(half, half, half).fill(grad);
  const texture = renderer.generateTexture(g);
  g.destroy(true);
  return { texture, unitPx: half / maxR };
}

/** Scale+position a glow sprite so its texture reads at world radius `r`, centered at (x,y). */
export function sizeGlowSprite(
  sprite: PixiNS.Sprite,
  glow: GlowTexture,
  x: number,
  y: number,
  r: number,
): void {
  sprite.position.set(x, y);
  sprite.scale.set(r / glow.unitPx);
}

// ── vignette texture (plan §M2.3) ────────────────────────────────────────────

/**
 * A circular vignette: transparent to ~50% of the corner radius, ~`cornerAlpha` (default
 * 0.7) at the corners. Uses `textureSpace: 'global'` (real pixel coordinates) rather than
 * the shape-local 0-1 space, so the gradient stays perfectly circular regardless of the
 * canvas's aspect ratio (a local-space radial gradient would stretch into an ellipse on a
 * non-square canvas). Rebuild on resize only — it's a static texture otherwise.
 */
export function makeVignetteTexture(
  PIXI: Pixi,
  renderer: PixiNS.Renderer,
  w: number,
  h: number,
  opts?: { color?: number; innerFrac?: number; cornerAlpha?: number },
): PixiNS.Texture {
  const color = opts?.color ?? 0x000000;
  const innerFrac = opts?.innerFrac ?? 0.5;
  const cornerAlpha = opts?.cornerAlpha ?? 0.7;
  const cx = w / 2;
  const cy = h / 2;
  const corner = Math.max(1, Math.hypot(cx, cy));

  const g = new PIXI.Graphics();
  const grad = new PIXI.FillGradient({
    type: 'radial',
    center: { x: cx, y: cy },
    innerRadius: corner * innerFrac,
    outerCenter: { x: cx, y: cy },
    outerRadius: corner,
    colorStops: [
      { offset: 0, color: rgba(color, 0) },
      { offset: 1, color: rgba(color, cornerAlpha) },
    ],
    textureSpace: 'global',
  });
  g.rect(0, 0, w, h).fill(grad);
  const texture = renderer.generateTexture(g);
  g.destroy(true);
  return texture;
}

// ── parallax starfield (plan §M2.4) ──────────────────────────────────────────

export type StarfieldStar = { x: number; y: number; r: number; alpha: number; phase: number };
export type StarfieldLayers = { far: StarfieldStar[]; near: StarfieldStar[] };

/** Small deterministic LCG so a given (w,h,seed) always yields the same field — a resize
 *  regenerates the layout instead of visibly "popping" to unrelated random stars. */
function makeRng(seed: number): () => number {
  let s = seed >>> 0 || 1;
  return () => {
    s = (s * 1103515245 + 12345) & 0x7fffffff;
    return s / 0x7fffffff;
  };
}

/**
 * Two-tier starfield: far (small, dim, cool-tinted by the caller) + near (bigger, brighter,
 * warmer/neutral). Density is derived from canvas area so a small pane isn't over-cluttered
 * and a large one doesn't look sparse. Each star carries a `phase` for per-star twinkle —
 * animate `alpha * (0.4 + Math.abs(Math.sin(t*freq + phase)) * 0.6)` in the caller's tick
 * (kept out of this module since it needs the live clock + reduced-motion flag).
 */
export function makeStarfieldLayers(
  w: number,
  h: number,
  opts?: { farDensity?: number; nearDensity?: number; seed?: number },
): StarfieldLayers {
  const area = Math.max(1, w * h);
  const farDensity = opts?.farDensity ?? 1 / 2800; // px² per star
  const nearDensity = opts?.nearDensity ?? 1 / 6500;
  const rnd = makeRng(opts?.seed ?? 1337);

  const build = (
    count: number,
    rMin: number,
    rMax: number,
    aMin: number,
    aMax: number,
  ): StarfieldStar[] => {
    const out: StarfieldStar[] = [];
    for (let i = 0; i < count; i++) {
      out.push({
        x: rnd() * w,
        y: rnd() * h,
        r: rMin + rnd() * (rMax - rMin),
        alpha: aMin + rnd() * (aMax - aMin),
        phase: rnd() * Math.PI * 2,
      });
    }
    return out;
  };

  return {
    far: build(Math.round(area * farDensity), 0.2, 0.8, 0.05, 0.25),
    near: build(Math.round(area * nearDensity), 0.5, 1.7, 0.15, 0.5),
  };
}

// ── segmented ring technique (plan §M2.5) ────────────────────────────────────

/**
 * Draw a ring as `segments` short arcs instead of one continuous `circle().stroke()` — lets
 * `alphaAt` vary per-segment (e.g. brightening toward a body's current angle) while a flat
 * `() => constAlpha` reproduces a plain uniform ring using the same code path. A tiny overlap
 * between segments hides seams.
 */
export function drawSegmentedRing(
  g: PixiNS.Graphics,
  cx: number,
  cy: number,
  r: number,
  color: number,
  opts: {
    segments?: number;
    width?: number;
    alphaAt: (midAngle: number, t: number) => number;
  },
): void {
  const segments = Math.max(4, opts.segments ?? 48);
  const width = opts.width ?? 1;
  const step = (Math.PI * 2) / segments;
  const overlap = step * 0.04;
  for (let i = 0; i < segments; i++) {
    const a0 = i * step;
    const a1 = a0 + step + overlap;
    const mid = a0 + step / 2;
    const alpha = opts.alphaAt(mid, i / segments);
    if (alpha <= 0.002) continue;
    g.arc(cx, cy, r, a0, a1).stroke({ width, color, alpha });
  }
}
