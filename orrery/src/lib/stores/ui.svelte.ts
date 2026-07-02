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
// Responsive (plan §7): on a phone, Planetarium (ambient/Tier-1) is the implicit
// DEFAULT — but wave U2 gave Observatory a real single-column phone layout, so
// isPhone alone no longer forces Tier-1 rendering (see `tierOne` below). A phone
// user who explicitly picks Observatory gets planets, the HUD and controls, just
// docked in one column instead of three. `prefers-reduced-motion` (plan §F) is
// read here too so every renderer can consult one source.

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
   * Tier-1-only rendering is in force when the mode is Planetarium (ambient).
   * wave U2: a phone is no longer FORCED into Tier-1 just for being narrow — a
   * phone user who explicitly picks Observatory gets the real instrument (a
   * responsive single-column dock), not a secret ambient view wearing the
   * desktop panel stack. Phones still DEFAULT to ambient (see init() below);
   * this only changes what happens once they leave it.
   */
  tierOne = $derived(this.mode === 'planetarium');

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
