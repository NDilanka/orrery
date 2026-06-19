// UI store (A7) — the three viewing MODES and the responsive tier.
//
//   Observatory  — the full interactive instrument (all tiers; the desktop home).
//   Planetarium  — a full-screen AMBIENT view: Tier-1 ONLY (star · cost-horizon ·
//                  the four rest-states · beacon · quota night). HUD/panels/planet
//                  detail are hidden; text appears only on a threshold. The
//                  overnight / second-screen / phone view. (plan §3 MODES)
//   Rewind       — scrub the run to time T; framed in the Plasma-cyan time-shimmer,
//                  with verdicts/strikes/quota pinned on the timeline. (plan §3)
//
// Responsive (plan §7): below a phone breakpoint we DROP to Tier-1 — planets,
// chambers and the gear mechanism are hidden, particles are budget-cut, hover
// becomes tap. On a phone Planetarium is the implicit default. `prefers-reduced-
// motion` (plan §F) is read here too so every renderer can consult one source.

import { browser } from '$app/environment';

export type ViewMode = 'observatory' | 'planetarium' | 'rewind';

/** Phone breakpoint (plan §7 "below a phone breakpoint, drop to Tier-1"). */
export const PHONE_BREAKPOINT = 640;

class UiStore {
  mode = $state<ViewMode>('observatory');
  /** viewport width (px); 0 until measured on the client. */
  vw = $state(0);
  reducedMotion = $state(false);
  /** the user explicitly chose a mode (so we don't keep auto-forcing it). */
  private userPicked = false;

  // ── derived responsive tier ────────────────────────────────────────────────
  isPhone = $derived(this.vw > 0 && this.vw < PHONE_BREAKPOINT);

  /**
   * Tier-1-only rendering is in force when the mode is Planetarium OR we are on
   * a phone. Renderers consult this to hide planets/chambers/mechanism, cut the
   * particle budget, and switch hover→tap.
   */
  tierOne = $derived(this.mode === 'planetarium' || this.isPhone);

  /** Planetarium is "ambient": calm, text only on threshold, no chrome. */
  ambient = $derived(this.mode === 'planetarium');

  /** Rewind shows the cyan time-shimmer + an interactive timeline. */
  rewind = $derived(this.mode === 'rewind');

  setMode(m: ViewMode): void {
    this.mode = m;
    this.userPicked = true;
  }

  toggleObservatoryPlanetarium(): void {
    this.setMode(this.mode === 'planetarium' ? 'observatory' : 'planetarium');
  }

  /**
   * Start measuring the viewport + reduced-motion. Returns a teardown fn. On a
   * phone the first measurement makes Planetarium the implicit default (unless
   * the user has already picked a mode this session).
   */
  init(): () => void {
    if (!browser) return () => {};
    const measure = () => {
      this.vw = window.innerWidth;
      if (!this.userPicked && this.isPhone && this.mode === 'observatory') {
        // implicit phone default = the ambient Tier-1 view (plan §7)
        this.mode = 'planetarium';
      }
    };
    measure();
    window.addEventListener('resize', measure, { passive: true });

    const rm = window.matchMedia('(prefers-reduced-motion: reduce)');
    this.reducedMotion = rm.matches;
    const onRm = () => (this.reducedMotion = rm.matches);
    rm.addEventListener?.('change', onRm);

    return () => {
      window.removeEventListener('resize', measure);
      rm.removeEventListener?.('change', onRm);
    };
  }
}

export const uiStore = new UiStore();
export type { UiStore };
