# Orrery UI Modernization Plan — "Same concept, premium execution"

Written 2026-07-02, synthesized from a four-track research fan-out:
codebase UI audit, prior-art/constraints review, modern desktop-tool design
research, and canvas-visualization polish research.

**Goal**: make the app modern, sleek, and clean **on top of the existing UI
concept**. Nothing conceptual changes — the three-altitude zoom
(Cosmos → System → Body), the grid dock, the orbital scene, the rest-state
glyphs, and the plain-language + astronomy-flavor rule from U1/U2 all stay.
This plan upgrades the *execution*: tokens, surfaces, typography, motion,
and canvas rendering quality.

---

## 0. Constraints already on record (must respect)

From `docs/improvement-plan.md` and the U1–U4 waves (all shipped):

- **Hybrid UX**: ops clarity first, cosmic identity kept as visual language.
- **One metaphor system**: astronomy visuals only; plain-language labels
  (no ember/frost/lighthouse/gear *words*).
- **Never hue alone**: status is always shape + motion + color
  (`tokens.css:2-3`). The five rest-state silhouettes are triple-coded and
  are explicitly a keeper.
- **Keep**: three-altitude zoom, rest-state glyphs, cost-horizon ring,
  Rewind scrubber, LIVE/REPLAY/LAN badge, staged ignite feedback,
  Planetarium concept, MetricsPanel vocabulary, cost/quota strip,
  `needsYou` sorting, U2 grid dock.
- **Reduced motion honored everywhere** — existing invariant; every new
  effect (trails, parallax, flares, grain drift) must freeze under it.
- **Architecture**: visual-only changes don't touch PROTOCOL.md or the
  dual reducers; anything that adds UI *state* must thread through both
  reducers + golden parity tests. This plan is deliberately scoped to be
  render/style-only so golden tests stay untouched.
- Stack: Tauri v2, Svelte 5 runes, PixiJS v8, uPlot, pure CSS custom
  properties (no framework). Fonts self-hosted via @fontsource.

**Validation protocol** (established): `npm run dev` (port 1420) →
`node _shots.mjs <outdir>` — 9 deterministic fixture-driven screenshots
(desktop 1440×900 + mobile 390×844, incl. `failed-dark`). Take a
`shots-before/` baseline on the first commit of this effort; every phase
ends with a screenshot diff review + `svelte-check` + `vitest`.

---

## 1. Design direction

One sentence: **obsidian instrument, quieter and deeper** — near-black
hue-tinted surfaces, hairline borders instead of shadows, brass reserved
as the identity accent, glow demoted from decoration to *signal*, and a
canvas that reads as a light source in an atmosphere instead of flat
neon discs.

Principles distilled from the research (Linear/Vercel/Zed/Raycast +
mission-control/sci-fi analysis):

1. **Elevation by lightness, not shadow.** 3–4 surface steps, each a small
   lightness increment; separation via one hairline alpha (~8% white).
   Shadows only on true overlays (modals, popovers).
2. **Chroma budget by area.** Large fills (panels, rings, coronas, planet
   discs) get muted low-chroma color; only small elements (cores, 1–2px
   rims, chips, text glyphs) get the saturated version of the same hue.
   This is the single biggest "neon → premium" lever.
3. **Two accent families, period.** Brass = identity/certification.
   Cyan = interactive/focus/live. Status hues (green/amber/crimson/frost)
   exist only as status, in two-tier muted/core form. No other saturated
   color anywhere.
4. **Glow is a signal, not a style.** Only the running star, the current
   body, and alert states get multi-layer glow; everything else is flat
   fill. (The bloom-threshold principle: most pixels excluded.)
5. **One attention grammar.** The codebase states "urgency = tightening/
   slowing, never blinking" and then violates it. Enforce it: attention =
   glow-breathe (parameterized single keyframe) or geometry change;
   opacity-blink is retired.
6. **Glass only above the scene.** Restrained translucency+blur on modals
   and popovers only — never on rails/panels over live-updating data
   (contrast + WebView2 jank risk).
7. **Motion is honest.** Orbital motion linear (constant angular velocity,
   physically grounded); UI transitions 100–200ms with standard easings;
   500ms ceiling; springs reserved for user-driven interactions.

**Fonts: keep Space Grotesk + JetBrains Mono.** Both fit the instrument
identity and the research consensus ("geometric-technical display + strict
mono for data"); swapping to Inter/Geist buys nothing the token cleanup
doesn't. The fix is *usage*, not typeface: everything moves onto the
existing `--text-*` scale, mono strictly for data/logs/numerals.

---

## 2. What the audit found (the gap this plan closes)

Full audit lives in the session record; the actionable summary:

| # | Debt | Evidence |
|---|------|----------|
| 1 | 86 raw `font-size: Npx` bypassing the 7-step `--text-*` scale | TuningConsole 34, +page/LogPanel 8 each, Hud/QAConsole/Planetarium 6 each; near-duplicate micro-sizes 9/9.5/10/10.5/11px |
| 2 | Modal gradient+shadow combo copy-pasted 4–5× | HelpOverlay:190, DecisionSheet:123, TuningConsole:1069, ShareButton:184, Cosmos:921 |
| 3 | No z-index scale | 9 magic values (2,8,9,11,12,13,20,30,40,41,45) coordinated by comments |
| 4 | Native unstyled scrollbars everywhere | zero `scrollbar` styling in src/ |
| 5 | Two competing attention grammars | RunControlBar `workPulse`/`brakePulse`/`stopPulse` opacity-blinks vs PlanetariumOverlay's `--glow`-parameterized breathe |
| 6 | Three hand-synced color tables | tokens.css + palette.ts HUE + local `C={}` objects in Observatory:62 and Cosmos:47 |
| 7 | TuningConsole is a visual outlier | own scrim value, bespoke slider thumbs (≠ TransportBar's), 34 raw sizes, 1659 lines |
| 8 | Slider focus invisible | custom `::-webkit-slider-thumb` has no focus state; BodyView drawer `outline:none` with no alternative |
| 9 | Corona bands, rings too loud | linear alpha ramp over stacked circles reads "flat disc"; backlog orbit rings at 0.5 alpha |
| 10 | Per-frame GC churn in Pixi | `planetsC.removeChildren()` + `new Graphics()` per planet per frame; fixed-dt lerps (`0.016`) frame-rate dependent |
| 11 | uPlot re-types token colors as literals | CostQuotaStrip:104 — token changes don't propagate |
| 12 | `--num` vs `--font-mono` redundant tokens | tokens.css:44-46 |

---

## 3. The plan — four phases (M0–M3)

Phases are ordered by dependency: tokens first, then panels, then canvas,
then finishing layer. Each phase is shippable and screenshot-verified
independently. M1 and M2 are internally parallelizable across subagents
(per-panel / per-effect) once M0 lands.

### Phase M0 — Foundation: tokens v3 + shared primitives
*(everything else builds on this; do first, single agent, ~1 session)*

**M0.1 Restructure `tokens.css` into 3 tiers** (primitive → semantic →
component), converting primitives to OKLCH:

```css
/* tier 1: primitives (oklch, hue-tinted near-black neutral ramp) */
--n1: oklch(0.13 0.012 265);   /* app void  — keeps the indigo tint */
--n2: oklch(0.16 0.012 265);   /* panel     */
--n3: oklch(0.19 0.012 265);   /* raised    */
--n4: oklch(0.23 0.012 265);   /* hover     */
--hairline: oklch(1 0 0 / 8%);
/* two-tier status: -core (small/bright) + -base (large/muted), same hue */
--status-run-core:  oklch(0.80 0.14 220);  --status-run-base:  oklch(0.62 0.08 220);
--status-ok-core:   oklch(0.78 0.17 150);  --status-ok-base:   oklch(0.58 0.09 150);
--status-warn-core: oklch(0.80 0.15 85);   --status-warn-base: oklch(0.60 0.09 85);
--status-err-core:  oklch(0.68 0.19 20);   --status-err-base:  oklch(0.50 0.11 20);
--status-idle-core: oklch(0.72 0.03 265);  --status-idle-base: oklch(0.45 0.02 265);
/* tier 2: semantic — what components consume */
--surface-panel: var(--n2); --text-primary: …; --border-focus: …;
```

Keep every existing v1/v2 token name as an alias into the new tiers so
nothing breaks mid-migration; retire aliases at the end of M1. Merge
`--num` into `--font-mono`. Current core hues (crimson `#ff3b5c`, green
`#5bf09b`, cyan `#46e0ff`) are already near the recommended cores — the
*new* half is the muted base tier.

**M0.2 Add missing token groups**:
- `--z-labels:2 … --z-layer-body:8 … --z-chrome:20 --z-a11y:30
  --z-modal:40 --z-sheet:41 --z-popover:45` — then replace all 9 magic
  numbers.
- Radius scale: `--radius-sm: 6px; --radius: 10px; --radius-lg: 14px;
  --radius-pill: 999px` (radius grades with elevation tier).
- Motion additions: `--dur-feedback: 120ms` (hover/press) alongside the
  existing fast/mid/zoom; document the 500ms ceiling.
- Component tokens: `--accent-border-w: 3px` (QAConsole/VerdictPanel),
  `--scrim` used by *all* modals (kill TuningConsole's private value).

**M0.3 Shared primitives** in a new `src/lib/render/primitives.css`
(imported next to tokens.css):
- `.floating-card` — the one modal/popover surface: gradient, shadow,
  border, `--radius-lg`, optional `backdrop-filter: blur(12px)` behind a
  `@supports` + reduced-transparency guard. Replaces the 5 copy-pasted
  blocks (HelpOverlay, DecisionSheet, TuningConsole, ShareButton, Cosmos
  onboarding card).
- `.pill` — the navbar/fab/badge chip shape (navbar and ignite-fab
  currently duplicate it).
- One parameterized attention keyframe:
  `@keyframes breathe { … box-shadow: 0 0 var(--breathe-r) var(--glow) }`
  — consumed via `--glow`/`--breathe-r` custom properties (the pattern
  PlanetariumOverlay already proved). Retire `workPulse`, `brakePulse`,
  `stopPulse`, and the LogPanel dot opacity-blink.
- Styled scrollbars, app-wide: thin (8px), `--n4` thumb on transparent
  track, hover brighten — `::-webkit-scrollbar*` (WebView2/Chromium is
  the only engine that matters in Tauri on Windows) plus
  `scrollbar-width: thin; scrollbar-color:` for cross-engine dev use.
- Shared slider styling (`.slider`): one thumb/track treatment consumed
  by both TransportBar and TuningConsole, **with a visible focus state**
  (`:focus-visible` on the input → styled thumb via
  `box-shadow: 0 0 0 2px var(--void), 0 0 0 4px var(--plasma-cyan)` —
  double-ring so it reads on any surface; same recipe becomes the global
  focus ring).

**M0.4 Single color source for Pixi.** New `src/lib/theme.ts`: reads
resolved CSS custom properties once at startup (`getComputedStyle`) and
exposes numeric `0xRRGGBB` values + the status core/base pairs. Rewrite
`palette.ts`'s HUE map and both local `C = {}` objects in Observatory and
Cosmos to consume it; fix CostQuotaStrip's uPlot literals to pull from the
same bridge. One place to change a color, everywhere follows — including
the canvas.

**Acceptance**: screenshots ≈ identical to baseline except scrollbars,
focus rings, and unified pulse timing; svelte-check 0 errors; all 42
vitest green; no golden-test changes.

### Phase M1 — Panel & chrome sweep
*(parallelizable: one subagent per panel group; ~5–7 agents)*

**M1.1 Type-scale migration**: replace all 86 raw `font-size` declarations
with `--text-*` tokens; collapse the 9/9.5/10/10.5/11px cluster onto
`--text-2xs`/`--text-xs`. Batches: (a) TuningConsole, (b) Hud + LogPanel,
(c) QAConsole + VerdictPanel + MetricsPanel, (d) +page + Planetarium +
remaining. Rule for ambiguity: round to nearest step; if a size genuinely
needs to exist, add it to the scale — never inline it.

**M1.2 Surface modernization pass** (visual, per panel, on top of M0
tokens): flatten to `--surface-panel` + hairline borders; remove per-panel
shadows on docked rails (shadows only on BodyView drawer + modals);
consistent `--space-*` padding rhythm (panels: `--space-4` outer,
`--space-3` inner groups); `--radius` on rails, `--radius-lg` on
overlays; headers unified (11px caps-spaced `--text-xs` label + right-side
meta in mono — the pattern Hud/Metrics already gesture at).

**M1.3 TuningConsole overhaul** (own agent — biggest item): adopt
`.floating-card`, shared `.slider`, shared `--scrim`, type scale (34
fixes), spacing rhythm; keep every feature (blueprint picker, dials,
SVG preview, probe runner) — this is re-skinning, not redesign. Cut its
bespoke slider thumbs in favor of the shared one.

**M1.4 State completeness sweep**: every interactive element gets
hover (+1 surface step, 120ms), focus-visible (double-ring), active,
and disabled (`opacity:.4` + `cursor:not-allowed`) states. Fix the two
known focus holes (slider thumbs, BodyView landing target — give the
drawer a subtle inset hairline highlight when focused instead of
nothing).

**M1.5 Micro-interactions (restrained)**: 120ms hover transitions on all
controls; skeleton shimmer for the three CostQuotaStrip/Metrics empty
states (perceived-speed win); AlertBanner entrance kept, everything else
state-driven only. No animation on data ticks.

**Acceptance**: screenshot review of all 9 shots + a new TuningConsole
detail shot; contrast spot-check ≥4.5:1 text / ≥3:1 UI on the new
surfaces; svelte-check + vitest green.

### Phase M2 — Canvas premium pass (Observatory + Cosmos)
*(parallelizable per effect after M2.1; the highest-visibility phase)*

Ranked by visual leverage per line of code:

1. **M2.1 Glow sprites + blend discipline.** Pre-render one radial-
   gradient glow texture (inverse-square falloff: stops at r×1.0/1.6/2.6/
   4.0 with alpha 0.12/0.06/0.03/0.015), reuse as a tinted/scaled sprite
   for star corona, planet highlights, and motes — replaces the banded
   stacked-circle coronas. Corona/particle containers get
   `BLEND_MODES.SCREEN`; `ADD` reserved for transient moments (supernova,
   seal-flash). Star anatomy: near-white hot core + large dim hue-tinted
   halo (3–6× radius, alpha 0.04–0.10) + a 4-point flare (thin, ≤0.15
   alpha) on the central star only, only while running.
2. **M2.2 Two-tier status color on canvas** (from M0.4 theme bridge):
   planet discs / ring strokes / halos use `-base`; cores, selection rims,
   and label text use `-core`. Rest-state silhouettes keep their exact
   shapes and motion — only the fills move to the muted tier. Glow always
   carries the base (muted) tint.
3. **M2.3 Atmosphere layer**: cached vignette texture (transparent to
   ~50% radius, alpha ~0.7 at corners, redrawn on resize only) + a CSS
   grain overlay div (256–512px noise data-URI tile, `opacity:.03`,
   `mix-blend-mode:overlay`, `pointer-events:none`) over both canvases.
   Near-zero runtime cost, large premium delta.
4. **M2.4 Parallax starfield**: split the single 220–260-star layer into
   two (far: 0.2–0.8px, alpha 0.05–0.25, cool-tinted; near: up to 1.7px,
   alpha ≤0.5) with a 1–3px differential slow drift; density derived from
   area. Twinkle via per-star phase offset. Freezes under reduced motion.
5. **M2.5 Orbit ring discipline**: backlog/idle rings drop to a single
   hairline stroke at ~0.18 alpha; only the *current* ring gets a
   segmented per-arc alpha gradient (30–60 segments) brightening toward
   its planet — visually attaches ring to body. Cost-horizon ring keeps
   its exact ladder/behavior (a keeper), restyled with the same segment
   technique.
6. **M2.6 Trails**: ring-buffer polyline (N=8–24 positions) on the
   current body only, alpha `(i/N)² × 0.4`, width tapering to 0. Off
   under reduced motion and in Ambient tier.
7. **M2.7 Motion & perf hygiene**: dt-correct all lerps
   (`k = 1 − (1 − k60)^(dt/16.67)`); orbital angular velocity strictly
   linear; pool planet Graphics keyed by `o.key` (kill the per-frame
   removeChildren/new); DPR cap 2 stays.
8. **M2.8 Label placement upgrade** in ObservatoryLabels: greedy
   4-candidate anchor placement (below/above/right/left) against placed
   AABBs, priority current > failed/unverified > rest, with anchor
   hysteresis (keep position until actual collision); bare single-word
   labels get `text-shadow: 0 1px 2px rgb(0 0 0/.8), 0 0 6px rgb(0 0 0/.5)`;
   leader line (1px, 0.35 alpha, status hue) only when displaced beyond
   ~1.5× body radius. DOM overlay architecture stays (confirmed correct).
9. **M2.9 Cosmos parity**: apply glow sprite, two-tier color, vignette/
   grain, starfield split, and the muted-ring rule to Cosmos with the
   same shared code (theme.ts + a small shared `fx.ts` for the sprite/
   vignette builders) — ending the hand-duplicated silhouette/glow code
   drift where practical.

**Acceptance**: before/after screenshot pairs for 01/03/04/05/08 shots;
frame-time sanity check (no regression at 60fps with 12+ bodies — use the
bmad fixture); reduced-motion run shows frozen trails/drift/flare;
rest-state silhouettes still greyscale-separable (screenshot in grayscale
to verify the never-hue-alone invariant).

### Phase M3 — Finishing layer
*(optional-but-recommended items; each independently shippable)*

- **M3.1 Command palette (Cmd/Ctrl-K)**: the one *new* chrome element —
  by 2026 an expected pattern for any tool with >10 actions. Actions:
  start/resume/stop-now/brake, switch mode (Observatory/Ambient/Rewind),
  jump to loop/body, open help/share/tuning, toggle filters. Styled as
  the flagship `.floating-card`. Pure UI dispatch onto existing actions —
  no reducer/protocol changes.
- **M3.2 Empty-state & onboarding polish**: restyle the Cosmos 4-step
  onboarding checklist and `liveAndEmpty` hint onto the new primitives;
  one consistent empty-state pattern (dim glyph + one-line explanation +
  primary action).
- **M3.3 A11y/contrast audit**: automated contrast check of the final
  semantic tokens (script over resolved values, ≥4.5:1 text / ≥3:1 UI);
  keyboard walk of every surface; verify the grayscale silhouette test.
- **M3.4 App icon / window chrome check**: titlebar/window background
  matches `--n1` so the shell doesn't flash white on launch (Tauri config).
- **Deliberately skipped** (research says they age badly or don't fit):
  glassmorphism on data panels, bento layout (Cosmos already *is* the
  overview), neo-brutalism, mesh-gradient backdrops behind data, decorative
  always-on animation.

---

## 4. Execution notes

- **Order**: M0 → M1/M2 (parallel waves) → M3. M0 is one focused agent;
  M1 fans out per panel group; M2 fans out per effect after M2.1+M2.2
  land (glow sprite + theme bridge are the shared dependencies).
- **Branch/commits**: one branch (`feat/ui-modernization`), one commit per
  M-item, screenshot dirs `shots-before/` and `shots-m{0..3}/` kept out of
  git (already untracked pattern).
- **Risk register**:
  - *Pixi/token drift* — mitigated by M0.4 theme bridge being the only
    source; add a tiny vitest that asserts theme.ts resolves every status
    token to a valid color.
  - *OKLCH in WebView2* — supported since Chromium 111; Tauri v2 on
    Win11 is fine. Keep hex fallbacks in the alias layer during migration
    anyway.
  - *Perf on modal blur* — `backdrop-filter` only on modals/popovers
    (static content beneath is paused visually anyway); guarded by
    `@supports`.
  - *Scope creep into behavior* — every item above is render/style-only.
    If any task wants new state (e.g. command-palette *actions* beyond
    existing dispatches), it stops and gets its own protocol review.
- **Definition of done (whole effort)**: all 9 harness shots pass visual
  review against the direction in §1; zero raw `font-size` px literals
  outside tokens.css; zero magic z-indexes; one modal surface class; one
  attention keyframe; one color source feeding CSS + Pixi + uPlot;
  svelte-check 0 errors; vitest + cargo + golden all green and untouched
  in count.

---

## 5. Phase M4 — Monochrome retheme + hierarchy system
*(added 2026-07-03 after owner interview; supersedes §1 principle 3)*

**Owner decisions** (interview, 2026-07-03): near-monochrome "Linear
vibe"; **no signature hue** — brass and cyan retire as accents; dense data
areas fully monochrome; canvas cinematic monochrome; **the only chromatic
pixels in the app are alerts**: red (failed/crashed) and amber
(needs-you / handoff / quota). Success = pure monochrome (bright white +
seal glyph). Interaction = white/gray only (white double-ring focus,
breathing white live dot). The never-hue-alone invariant already holds
(shape+motion coding), so removing hue from calm states is safe.

**Owner diagnosis**: the UI lacks consistency and precise hierarchical
arrangement. Analysis: (1) no altitude between panels — HUD reads equal
to QAConsole; (2) three competing top clusters (StaleBadge / ModeBar /
breadcrumb) with no shared rail; (3) flat typography — everything
10-12px mono caps, headers = content; (4) no shared component anatomy —
5 button species, 3 header styles; (5) ragged alignment — no single page
gutter, bottom is three stacked unrelated objects; (6) duplicated info at
equal weight (spend in HUD + canvas caption). In monochrome, hierarchy is
carried entirely by scale/weight/lightness/arrangement — these are one
design problem.

### M4.1 Token retheme (foundation, sequential)
- Four text-emphasis tiers: `--em-hi` (oklch .92 — primary values),
  `--em-mid` (.70 — body), `--em-low` (.50 — labels), `--em-faint`
  (.38 — meta/decorative). Semantic text tokens repoint onto them.
- Status remap keeping token STRUCTURE (consumers unchanged):
  `--status-run-*`, `--status-ok-*`, `--status-idle-*` → grayscale tiers
  (run = em-hi + motion; ok = em-hi; idle = em-low). Only
  `--status-err-*` (red) and `--status-warn-*` (amber) stay chromatic.
- Alias repoint: `--brass`→ light neutral (identity = brightness, not
  hue); `--plasma-cyan`/`--plasma-green`/`--auditor-white` → em tiers;
  `--frost`/`--cache-teal` → cool/mid grays; `--horizon-rose` → red
  family; `--ghost-brass` → white @ 12%; model spectral colors → three
  grayscale steps. `--indigo-night` wash → neutral dark.
- Focus ring → white double-ring. theme.ts fallback table + contrast
  test updated to match.

### M4.2 Component anatomy (primitives.css)
- `.panel` + `.panel-hd`: THE card — --space-4 padding, hairline,
  --radius; header = --text-xs caps 600 ls .08em at --em-low + optional
  right `.panel-meta` (mono, --em-faint); internal gap --space-3.
- `.btn` system: `-primary` (solid light: bg em-hi / text n1 — the
  monochrome inversion is the strongest CTA), `-ghost` (hairline),
  `-danger` (red ghost); sizes `-sm/-md/-lg`; icon-square variant.
- `.seg`: segmented control (ModeBar, replay speed, blueprint picker).

### M4.3 Shell hierarchy (+page.svelte)
- **Full-bleed canvas** (owner request): Observatory moves out of
  `.g-center` onto a stage-level absolute layer behind the grid; panels
  float over the scene. Observatory gains a `safeInsets` prop
  ({top,right,bottom,left}px of occluded chrome) so the system centers
  and fit-scales in the unobstructed region ( = old .g-center box).
  Vignette + grain become viewport-sized (grain div moves to stage
  level, below chrome).
- **One top bar**: single rail, one baseline — left: breadcrumb ·
  center: ModeBar · right: live/replay + staleness + share + ⌘K. All
  ghost pills of one height.
- **One bottom dock**: RunControlBar + TransportBar merge into a single
  full-width bar on the page gutter (scrubber fills, controls grouped
  right); cost strip shares the same gutter.
- `--page-inset`: one gutter aligning rails, top bar, dock, strip.

### M4.4 Altitude tiers
- **Tier A (primary)**: HUD status block — the only elevated rail
  surface (--n3); status word / current story / spend at em-hi with
  real scale jumps (--text-lg → --text-display).
- **Tier B (working)**: log/metrics/verdict/QA — standard `.panel`,
  em-mid content, em-low headers.
- **Tier C (meta)**: top bar, cost-strip axis, staleness — --text-2xs
  em-faint, borderless where possible.
- Kill duplicate emphasis: canvas spend caption drops to em-faint (HUD
  is canonical).

### M4.5 Component sweeps (parallel agents)
Per-file mapping onto `.panel`/`.btn`/`.seg` + monochrome: LogPanel
chips → text-only kinds (only err/warn kinds keep color); HUD;
right rail; RunControlBar/TransportBar as dock segments; CostQuotaStrip
curves → white/gray (quota window amber); Observatory monochrome
(planetColor switch, spectral, motes → gray, night wash → neutral,
rings gray, trail white — alert bodies red/amber); Cosmos same;
TuningConsole/CommandPalette/overlays sweep.

**Acceptance**: a desktop screenshot in which the only chromatic pixels
are genuine alerts; squint test — the eye lands on HUD status → star →
controls, in that order; all prior invariants (reduced motion,
greyscale-separable silhouettes, protocol untouched) hold.

## 6. Phase M5 — Colorful cosmos, calm chrome
*(added 2026-07-03 after second owner interview; SUPERSEDES §5's
"alerts-only color" rule for the CANVAS — the chrome rule stands)*

**Owner decisions**: the strict monochrome read as dull. Color returns,
ruled: **the scene is the color; the chrome stays calm.** Canvas states
get jewel-tone identity hues with real glow; panels/HUD/buttons/chips
remain dark monochrome (red/amber keep their exclusive alert meanings in
the DOM). Hue family lead: **teal / aurora green** — glow language,
starfield/nebula tints, and the vignette bleed all lean aurora.

### M5.1 Scene palette (foundation)
A NEW canvas-scoped token tier — `--scene-*` — separate from the
`status-*` / `em-*` chrome tokens, so recoloring the sky cannot recolor
a chip. Semantic set (exact oklch tuned in implementation; hues below
are the targets):
- `--scene-run`: warm gold-white burn (the living star; core near-white,
  corona gold ~#f5c96b)
- `--scene-done`: emerald ~#34d399 (seal ring native to the aurora lead)
- `--scene-paused`: banked ember orange ~#ff8a4a
- `--scene-quota`: ice frost ~#9fc0ff
- `--scene-needs`: amber (same resolved hue as the chrome alert amber —
  one meaning, one color)
- `--scene-fail`: crimson (ditto)
- `--scene-atmo`: aurora teal ~#2dd4bf — starfield/nebula/vignette
  tint, glow wash, at low alpha only
theme.ts exposes the set to Pixi; palette.ts `restColor()` maps onto it
(chrome consumers keep reading `status-*`).

### M5.2 Chrome depth + micro-motion (foundation, monochrome)
- `.panel`: top-lit gradient surface, elevation shadow, brighter top
  edge (1px inside highlight) — real altitude instead of hairline-on-flat.
- `.btn`/interactive: hover lift (translateY −1px + shadow), pressed
  state, focus sheen. All monochrome; reduced-motion freezes lifts.

### M5.3 Canvas sweeps (parallel: Cosmos, Observatory)
- Glyphs/star consume `--scene-*`: bigger luminous cores, glow 2–3×
  (size + alpha), colored glow bleeding into the vignette.
- Aurora atmosphere: starfield twinkle picks up a faint teal cast;
  nebula/vignette tinted `--scene-atmo`; light is the material.
- DOM overlays inside the canvases (station cards, labels, captions)
  are CHROME — they stay monochrome + red/amber alerts.

**Acceptance**: the scene reads as a living, colorful sky through a
dark instrument; panels/chips contain no chromatic pixels besides
red/amber alerts; state hues remain greyscale-separable (shape/motion
coding unchanged); reduced-motion + protocol invariants hold.
