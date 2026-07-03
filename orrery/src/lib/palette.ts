// Shared status→color/glyph vocabulary, consumed by both render/Observatory.svelte (the star) and
// render/Cosmos.svelte (a loop's glyph) — previously two copy-pasted implementations of the exact
// same status/restState → hue decision tree ("mirrors starColor", per the Cosmos comment it
// replaced).
//
// M4.5 note: `restColor`'s table now encodes the M4 alert taxonomy (binding owner decision: the
// only chromatic pixels are alerts — red = failed/crashed, amber = needs-you/handoff/quota;
// running = white light + motion; paused/done = grayscale). Prior to this pass each canvas
// LOCALLY patched around this table's stale hues instead of fixing it here; those overrides are
// gone — this is now the single decision, byte-identical for both callers.
//
// Each component keeps its own broader Pixi color palette locally (Observatory needs model-tier /
// planet / ring hues Cosmos never touches) — only the shared DECISION (which status/restState maps
// to which of these colors) lives here, plus the glyph/label vocabulary Cosmos's roster + hover
// cards render (a non-hue separator so status survives greyscale/color-blindness).

// The subset of hues both Observatory and Cosmos need for `restColor` below. Numeric (0xRRGGBB)
// because that's what Pixi consumes. Previously a hand-synced literal copy of tokens.css;
// now resolved through theme.ts (the single color source, plan §M0.4) — read at CALL time
// (not module load) so it reflects whatever initTheme() resolved during onMount, with the
// exact same literal fallback values if it hasn't run yet (e.g. this module import order).
import { theme } from './theme';

function hue() {
  const t = theme();
  // FALLBACK-safe: `em` is optional on the ThemeColors type (see theme.ts) even though the
  // resolved table and FALLBACK both always populate it — guard so a hand-written partial
  // ThemeColors-shaped object never crashes here.
  const em = t.em;
  return {
    starlight: t.starlight,
    amber: t.amber,
    crimson: t.crimson,
    frost: t.frost,
    emHi: em?.hi ?? t.green,
    emLow: em?.low ?? t.ember,
  };
}

/**
 * The star/glyph color for a run's status + restState. Identical precedence in both callers:
 * a rest-state (when the run isn't actively running) wins over the bare status. `fallback` is
 * the color to use when NONE of the above match (the "healthy idle" tone) — Observatory and
 * Cosmos disagree on this one (a bright starlight star vs. a dim roster glyph), so it's an
 * explicit parameter rather than baked in here, keeping every OTHER hue byte-identical between
 * the two callers without forcing them to agree on the one they never did.
 *
 * M4 alert taxonomy (the only chromatic pixels are alerts — everything else is grayscale or the
 * white starlight running-light):
 *   failed-dark      -> crimson        (red alert — crashed)
 *   quota-frost      -> frost          (neutral cool gray — not an alert)
 *   handoff-beacon   -> amber          (needs-you — amber alert, never red)
 *   certified-done   -> em.hi          (near-white grayscale — "done", not an alert)
 *   stopped-ember    -> em.low         (grayscale — paused is calm, not an alert)
 *   status 'error'   -> crimson        (red alert — no restState yet, defensive)
 *   status 'running' -> starlight      (white light source; motion carries liveness, not hue)
 *   status 'quota-wait' -> frost       (same neutral as quota-frost, bare-status form)
 *   else             -> fallback       (caller-supplied "healthy idle" tone)
 */
export function restColor(status: string, restState: string | null, fallback: number): number {
  const HUE = hue();
  if (restState === 'failed-dark') return HUE.crimson;
  if (restState === 'quota-frost') return HUE.frost;
  if (restState === 'handoff-beacon') return HUE.amber;
  if (restState === 'certified-done') return HUE.emHi;
  if (restState === 'stopped-ember') return HUE.emLow;
  if (status === 'error') return HUE.crimson;
  if (status === 'running') return HUE.starlight;
  if (status === 'quota-wait') return HUE.frost;
  return fallback;
}

/** The state key a glyph/row reads from (restState wins, else status). */
export function stateKey(status: string, restState: string | null): string {
  return restState ?? status;
}

/**
 * A non-hue separator: a small geometric glyph paired with the dot so status survives
 * greyscale / color-blindness (design rule: status ≠ hue alone).
 */
export function stateGlyph(key: string): string {
  switch (key) {
    case 'running':
      return '◆'; // live diamond
    case 'certified-done':
      return '✓'; // sealed
    case 'stopped-ember':
      return '▬'; // banked
    case 'quota-frost':
    case 'quota-wait':
      return '❄'; // frost
    case 'handoff-beacon':
    case 'handoff':
      return '!'; // needs you
    case 'failed-dark':
      return '⚠'; // crashed — dim cracked disc
    case 'error':
      return '×'; // crashed (no restState yet — defensive fallback)
    case 'stopping':
      return '◇'; // winding down
    default:
      return '·'; // idle ember
  }
}

/** Plain-language status label (so "does it need me?" reads without a mouse hover). */
export function stateLabel(key: string): string {
  switch (key) {
    case 'running':
      return 'running';
    case 'certified-done':
      return 'done · verified';
    case 'stopped-ember':
      return 'paused';
    case 'quota-frost':
    case 'quota-wait':
      return 'quota pause';
    case 'handoff-beacon':
    case 'handoff':
      return 'needs you';
    case 'failed-dark':
      return 'failed';
    case 'error':
      return 'error';
    case 'stopping':
      return 'stopping';
    default:
      return 'idle';
  }
}
