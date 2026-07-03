// Shared status→color/glyph vocabulary, consumed by both render/Observatory.svelte (the star) and
// render/Cosmos.svelte (a loop's glyph) — previously two copy-pasted implementations of the exact
// same status/restState → hue decision tree ("mirrors starColor", per the Cosmos comment it
// replaced).
//
// M5.1 note (docs/ui-modernization-plan.md §6, SUPERSEDES the M4.5 taxonomy below):
// "the scene is the color; the chrome stays calm." `restColor` now maps onto the canvas-only
// --scene-* jewel-tone palette (theme.ts's `scene` group) instead of the grayscale em-*/status-*
// tones the M4 monochrome retheme pointed it at — this function feeds ONLY the two Pixi canvases
// (verified: no chrome/DOM consumer calls it), so recoloring it cannot recolor a chip. Chrome
// still reads status-*/em-* directly and stays monochrome + red/amber alerts; scene.needs/
// scene.fail are themselves bare aliases of status.warn/err.core in tokens.css, so an alert is
// still one meaning, one color, just now literally glowing on the canvas instead of reading as
// a dim gray dot.
//
// (Prior M4.5 taxonomy, for history: the only chromatic pixels were alerts — red = failed/
// crashed, amber = needs-you/handoff/quota; running = white light + motion; paused/done =
// grayscale. Each canvas previously LOCALLY patched around this table's stale hues instead of
// fixing it here; those overrides are gone — this is the single decision, byte-identical for
// both callers.)
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
  // FALLBACK-safe: `em`/`scene` are optional on the ThemeColors type (see theme.ts) even
  // though the resolved table and FALLBACK both always populate them — guard so a
  // hand-written partial ThemeColors-shaped object never crashes here. Scene fields fall back
  // to the pre-M5 grayscale fields (same tone the chrome still uses) if `scene` is absent.
  const em = t.em;
  const scene = t.scene;
  return {
    // M5.1 jewel-tone scene hues (docs/ui-modernization-plan.md §6) — canvas-only.
    sceneFail: scene?.fail ?? t.crimson,
    sceneQuota: scene?.quota ?? t.frost,
    sceneNeeds: scene?.needs ?? t.amber,
    sceneDone: scene?.done ?? (em?.hi ?? t.green),
    scenePaused: scene?.paused ?? (em?.low ?? t.ember),
    sceneRunCore: scene?.runCore ?? t.starlight,
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
 * M5.1 scene taxonomy (docs/ui-modernization-plan.md §6, SUPERSEDES the M4 grayscale-alert
 * table below for the canvas — chrome keeps reading status-* / em-* and stays monochrome):
 *   failed-dark      -> scene.fail     (crimson — same hue as the chrome red alert)
 *   quota-frost      -> scene.quota    (ice frost)
 *   handoff-beacon   -> scene.needs    (amber — same hue as the chrome amber alert)
 *   certified-done   -> scene.done     (emerald)
 *   stopped-ember    -> scene.paused   (banked ember orange)
 *   status 'error'   -> scene.fail     (crimson — no restState yet, defensive)
 *   status 'stopping' -> scene.paused  (banked ember — winding down reads as approaching-paused)
 *   status 'running' -> scene.runCore  (near-white burn core; motion still carries liveness)
 *   status 'quota-wait' -> scene.quota (same as quota-frost, bare-status form)
 *   else             -> fallback       (caller-supplied "healthy idle" tone)
 */
export function restColor(status: string, restState: string | null, fallback: number): number {
  const HUE = hue();
  if (restState === 'failed-dark') return HUE.sceneFail;
  if (restState === 'quota-frost') return HUE.sceneQuota;
  if (restState === 'handoff-beacon') return HUE.sceneNeeds;
  if (restState === 'certified-done') return HUE.sceneDone;
  if (restState === 'stopped-ember') return HUE.scenePaused;
  if (status === 'error') return HUE.sceneFail;
  if (status === 'stopping') return HUE.scenePaused; // winding down reads as approaching-paused
  if (status === 'running') return HUE.sceneRunCore;
  if (status === 'quota-wait') return HUE.sceneQuota;
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
