# Orrery UI Reskin — Refined Spec (t3code design language, v2)

> **SHIPPED as a selectable theme, not an in-place replacement.** The look below is
> implemented as the **"Cobalt"** skin, applied under `<html data-skin='cobalt'>` and
> layered as additive overrides on top of the untouched Classic `:root`. Users switch via
> Settings → Appearance → **Theme** (Classic / Cobalt); Classic is the default. So where this
> doc says "repoint `:root`", the real code scopes the same values under
> `:root[data-skin='cobalt']` (+ its `[data-theme='light']` twin). `theme.ts` FALLBACK is
> therefore left as Classic (the default); the canvases re-probe live vars on a skin flip.
> Setting axis: `appearance.skin` (schema.ts) → `data-skin` (settings store + app.html FOUC).

Re-skin orrery's **DOM chrome** to read as t3code — a cool near-monochrome with a single
indigo affordance — while preserving the information architecture, the three-altitude
metaphor, the Pixi canvases, and orrery's "running = white light" semantics.

Before/after review artifact (gate before code):
<https://claude.ai/code/artifact/7826721d-5eb1-4c98-9e57-8f77f4ef8881>

> **v2 note.** v1 went generic by stripping orrery's cool cast to pure `#0a0a0a`. v2, after a
> 5-lens design critique, governs the whole system on **one cool hue (265)** — the accent and
> its desaturated ghost in every surface, hairline and white. Every value below is
> AA-verified with the same `oklchToRgb` the test uses (ratios in comments).

## Design law

1. **One cool hue (265).** Neutrals, text, hairline, `--primary`, `--ring` all sit on hue 265.
   Amber (85) and red (20) are the only off-axis hues. Neutrals carry a *whisper* of cool
   chroma that **rises with elevation** (0.010 → 0.013) — anodized, fully monochrome-reading.
2. **Indigo = interactive affordance only** (primary CTA, focus ring, toggle/seg/nav/row
   *selected*). **Only white breathes**; the indigo affordance is static (never pulses/glows).
3. **Fill for solids, ring for strokes.** `--primary` fills CTAs; `--ring` (lighter) is every
   thin indigo stroke — focus, selection edge, input border. `--accent-wash` derives from the
   ring, split into hover / selected tiers.
4. **White = liveness.** Live/running marks read `--em-hi` (kept near-white), so they need
   **no edit**. Reduced motion *freezes* the breathe to a held glow — never to nothing.
5. **Canvas stays astronomical.** `--scene-*` jewel-tones and geometric glyphs are kept;
   the void stays deep & cool (`#070912`); indigo never enters the canvas.
6. **AA-first.** `contrast.test.ts` is the arbiter; a red means adjust the token, never the test.
7. **Keep token names, change values** (e.g. `--font-grotesk` → DM Sans) so ~16 consumers
   inherit for free.

## Tokens — `src/lib/render/tokens.css` (both `:root` and `[data-theme='light']`)

Hue 265 throughout. Ratios verified against the lightest surface each tier lands on.

### Neutral ramp (cool graphite — chroma rises with elevation)
| token | dark | light |
|---|---|---|
| `--n1` | `oklch(0.145 0.010 265)` `#080a0e` | `oklch(0.985 0.004 265)` `#f9fafd` |
| `--n2` | `oklch(0.185 0.011 265)` `#101318` | `oklch(0.965 0.006 265)` `#f1f3f8` |
| `--n3` | `oklch(0.225 0.012 265)` `#191c22` | `oklch(0.94 0.008 265)` `#e8ebf1` |
| `--n4` | `oklch(0.275 0.013 265)` `#24282e` | `oklch(0.915 0.010 265)` `#e0e3ea` |

### Text tiers (re-derived for headroom vs n4)
| token | dark | dark AA (on n4) | light | light AA (on n4) |
|---|---|---|---|---|
| `--em-hi` | `oklch(0.985 0.004 265)` `#f9fafd` | 14.2 | `oklch(0.30 0.02 265)` `#292e38` | 10.6 |
| `--em-mid` | `oklch(0.745 0.011 265)` | 6.52 | `oklch(0.44 0.02 265)` | 6.02 |
| `--em-low` | `oklch(0.655 0.013 265)` | 4.68 | `oklch(0.50 0.02 265)` | 4.68 |
| `--em-faint` | `oklch(0.565 0.013 265)` | 3.25 (floor 3) | `oklch(0.58 0.02 265)` | 3.35 (floor 3) |

### Accent family (NEW — chrome had none)
| token | dark | light | note |
|---|---|---|---|
| `--primary` | `oklch(0.53 0.202 265)` `#305ede` | `oklch(0.50 0.216 265)` `#2452dc` | CTA fill; white label 5.32 / 6.07 |
| `--primary-hover` | `oklch(0.47 0.19 265)` | `oklch(0.44 0.21 265)` | **DARKENS** on hover; white label 6.88 / 7.91 |
| `--primary-foreground` | `oklch(0.985 0.004 265)` `#f9fafd` | same | cool-white label, both themes |
| `--ring` | `oklch(0.68 0.16 265)` `#6793fa` | `oklch(0.47 0.20 265)` `#214bc9` | strokes/focus/selection; vs n1 6.73 / 6.90 |
| `--accent-wash-hover` | `color-mix(in oklab, var(--ring) 10%, var(--surface-panel))` | `…8%…` | ghost hover |
| `--accent-wash-sel` | `color-mix(in oklab, var(--ring) 20%, var(--surface-panel))` | `…16%…` | selected; em-mid on it 6.32 / 5.46 |

> Mix in **oklab** (not sRGB) so the tint stays on-hue. Split hover (fainter) from selected
> (stronger) so a selection always out-reads a passing hover.

### Status (NO indigo `info`)
Keep `warn`/`err` two-tier structure. Drop the planned indigo `--status-info-*` entirely —
an info *status* rendered indigo violates law #2. "Reviewing/info" is a **neutral** chip; if a
state must stand apart it's amber (the sanctioned attention hue).
- dark `--status-warn-core: oklch(0.80 0.15 85)`, `--status-err-core: oklch(0.68 0.19 20)` (kept)
- light `--status-warn-core: oklch(0.49 0.13 85)` `#825800`, `--status-err-core: oklch(0.505 0.19 20)` `#b81834`
- `--status-idle-core: var(--em-low)`; bases unchanged.

### Hairline (cool, 3-weight discipline)
Base ink cool. Give each weight a job: `--line-2` structural (panel edges), `--line` internal
rules, `--line-tick` readout gridlines.
- dark: `--line-tick oklch(0.82 0.03 265 / 4%)`, `--hairline (--line) …/8%`, `--line-2 …/14%`
- light: `--line-tick oklch(0.30 0.03 265 / 5%)`, `--hairline …/9%`, `--line-2 …/14%`
- **Add** `--border-input: var(--line-2)` alias for form-field borders.

### Canvas / misc
- `--void`: **keep `#070912`** (deep cool — do NOT flatten to `#0a0a0a`).
- `--starlight`: `oklch(0.985 0.006 265)` `#f8fafe` (cool white; ties chrome white to canvas).
- `--scrim`: `rgb(0 0 0 / 55%)` dark / `rgb(0 0 0 / 22%)` light, paired with overlay `blur(2px)`.
- Radii: **keep the existing 6 / 10 / 14 + pill** — do NOT add `--radius-md: 8px` (6px reads
  crisper/more instrument; buttons & inputs stay at `--radius-sm` 6px).
- Type additions (additive): `--text-2xl: 24px` (altitude/section header rung),
  `--space-7: 48px`, `--space-0: 2px`, and tracking tokens `--tk-caps 0.14em`,
  `--tk-caps-lg 0.10em`, `--tk-tight -0.02em`.
- `.mono`/`.num` (tokens.css:333): add `font-feature-settings: "tnum" 1, "ss01" 1`
  (slashed zero) and `font-variant-ligatures: none`; drop the `letter-spacing: 0.01em`.
- `--font-grotesk` value → DM Sans stack. **(Already committed — see fonts.ts.)**

**Kept byte-for-byte:** all `--scene-*`, `--spectral-*`, retired-hue named palette,
`--void-2/3`, `--surface-1..4`, motion/z scales.

## Component anatomy — `src/lib/render/primitives.css`

- **`.btn-primary`** — lit key, **no lift, no colored bloom.**
  rest `background: var(--primary); color: var(--primary-foreground);
  box-shadow: inset 0 1px 0 rgb(255 255 255 / 12%), 0 1px 2px rgb(0 0 0 / 24%)`;
  `:hover` `background: var(--primary-hover)` (inset highlight **held** at 12%, not brightened);
  `:active` `box-shadow: inset 0 1px 2px rgb(0 0 0 / 28%)` (top-light inverts → seats in).
  **Remove the `translateY(-1px)` lift** from the base `.btn` hover/active (delete the transform;
  keep background/box-shadow transitions on `var(--dur-feedback) var(--ease-standard)`).
- **`.btn-ghost`** — border `var(--border-input)`; hover `var(--accent-wash-hover)`.
- **`.btn:disabled`** — inert plate: `background: var(--n3); color: var(--em-faint);
  box-shadow: none; opacity: 1` (not opacity-dimmed).
- **Unified focus ring** — one shape for `:focus-visible` on button/input/seg/toggle and the
  global rule (tokens.css:352) + `.slider` (primitives.css:316,319):
  `box-shadow: 0 0 0 1px var(--surface-void), 0 0 0 3px var(--ring), 0 0 0 6px color-mix(in srgb, var(--ring) 22%, transparent)`.
  Renders instantly — **no transition on the ring's spread/opacity.** Repoint
  `--border-focus` (tokens.css:164) → `var(--ring)`.
- **`.input`** — border `var(--border-input)`, radius `--radius-sm` (6px); `:focus` adds the
  unified ring + `border-color: var(--ring)`; keep `.invalid` red.
- **`.panel`/`.panel-tier-a`** — borders-over-shadows: strengthen border to `--line-2`, flatten
  gradient to `color-mix(in srgb, var(--surface-panel) 96%, white 4%)`, inset highlight to
  `rgb(255 255 255 / 5%)`, drop the big drop-shadows (keep tier-a a clear step above).
- **`.floating-card`** (glass) — tokens.css: `--floating-card-grad → color-mix(in srgb,
  var(--surface-panel) 70%, transparent)`; primitives.css: add
  `backdrop-filter: blur(20px) saturate(1.4)` (the `saturate` keeps jewel-tones alive through
  the frost), `border-color: var(--line-2)`, shadow `inset 0 1px 0 rgb(255 255 255 / 14%),
  0 1px 2px rgb(0 0 0 / 45%), 0 18px 48px -24px rgb(0 0 0 / 55%)`. **Blur is static — never in a
  transition/animation** (WebView2 re-rasterizes every frame → jank).
- **NEW `.glass`** — factor the blur/translucency so popovers/ModeBar can opt in.
- **NEW `.chip` family — TWO channels.** colored dot (the one saturated mark) + neutral
  `--em-hi` text + ambient tint `color-mix(in srgb, var(--status-X-core) 10%, transparent)` +
  neutral `--line-2` border. `.chip-info`/`reviewing` = neutral dot; `.chip-ok` = neutral.
  (No colored text, no colored border — calmer, and resolves the info-indigo issue.)
- **`.seg-item` selected** — `background: var(--accent-wash-sel);
  box-shadow: inset 0 0 0 1px color-mix(in srgb, var(--ring) 35%, transparent)`; text `--em-hi`.
- **Toggle** — off knob `--em-mid` + `box-shadow: 0 1px 2px rgb(0 0 0 / 35%)`, recessed track
  `inset 0 1px 1px rgb(0 0 0 / 25%)`; on = `--primary` track + white knob.

## Accent ripple — exact sites (repoint to `--ring` / `--accent-wash-sel`)
- `Toggle.svelte` `.switch.on` (:40) bg → `--primary`.
- `SettingsNav.svelte` `.navitem.active` (:95) — **filled wash, NO left bar** (reviewer
  rejected the left-edge highlight): `background: var(--accent-wash-sel); color: var(--em-hi)`;
  remove the `border-left-color`/inset-edge entirely. Optional extra definition: a full hairline
  ring `box-shadow: inset 0 0 0 1px color-mix(in srgb, var(--ring) 30%, transparent)`.
- `CommandPalette.svelte` `.row.active` (:520) — same treatment (fill only, no directional edge).
- `.slider:focus-visible` (:316,319) `var(--em-hi)` → the unified ring.
- **Leave white (no edit):** CostQuotaStrip/LogPanel `.dot.live`, StaleBadge `.badge.live .dot`,
  Hud `.pdot.active`, ShareButton `.trigger .dot` — all read `--em-hi`/`--text-primary`, which
  stays near-white.

## Motion
- Use orrery's existing `--dur-*` / `--ease-*` ladder; replace hardcoded `.12s`/`.15s`.
- **Do NOT animate:** odometer/count-up numerals, card mount stagger, `backdrop-filter`,
  any spring/bounce ease, the indigo affordance (no focus-glow pulse).
- Reduced motion (reuse the app's `:not([data-motion='full'])` scoping): freeze the breathe to a
  **static held glow**, not `animation: none` (a live loop must not look dead). Freeze button
  displacement (moot once the lift is removed).
- Optional orrery-native flourishes (nice-to-have): seg selected indicator that *slews*
  (translateX) between altitudes; breadcrumb/altitude chrome coupled to the 450ms Pixi zoom.

## `theme.ts` FALLBACK (SSR/first-frame twin — must move with tokens.css)
Recompute the changed fields via the `oklchToRgb` in `contrast.test.ts` (pack to `0xRRGGBB`):
- `starlight → 0xf8fafe`; `void` **unchanged** (`0x070912`).
- `green`, `em.hi`, `status.run.core`, `status.ok.core` → `#f9fafd` = `0xf9fafd`.
- `em.mid/low/faint`, `status.idle.core` → the new cool values.
- **No `info` key** — do NOT add `--status-info-*` to `StatusColors`/`STATUS_TOKEN_MAP`/`FALLBACK`.
- Preserve `status.run.core === status.ok.core === em.hi`.

## Guard rails — tests
- `contrast.test.ts` (parses tokens.css live): drop the `--border-focus == --em-hi` equality
  asserts (lines 243, 298; fix titles 237/294); the 3:1 focus check now resolves through
  `--ring`. Add: `--primary-foreground` on `--primary` **and** on `--primary-hover` ≥ 4.5, both
  themes. No `info` assert (info dropped).
- `theme.test.ts`: `STATUS_KEYS` stays `run/ok/warn/err/idle` (info dropped). run/ok==em.hi
  invariant unchanged.

## Sequencing (per commit)
1. **fonts** — DM Sans install + `--font-grotesk` value. ✅ *done, committed on branch.*
2. **accent + type tokens** — add `--primary*`, `--ring`, `--accent-wash-*`, `--border-input`,
   `--text-2xl`, `--space-7/0`, tracking tokens, `.num` ss01. Additive; tests green.
3. **guard rails** — update `contrast.test.ts` / `theme.test.ts`; green before surfaces.
4. **neutral/surface remap + `theme.ts` FALLBACK** — SAME COMMIT (cool graphite, em, hairline,
   starlight, scrim + FALLBACK recompute).
5. **primitives anatomy** — btn reseat, unified focus ring, glass, `.chip-*`, `.glass`, panels,
   seg/toggle, `.slider` ring.
6. **accent ripple** — Toggle, SettingsNav (flush inset), CommandPalette.
7. **verify** — `npm run test:unit` + `npm run check` + `npm run test:e2e`; manual matrix
   (dark/light × density × motion), confirm canvas re-tint via `reinitTheme()`, live marks
   white, blur perf on TuningConsole.

## Out of scope (follow-up PR)
Full Lucide (`@lucide/svelte`) migration — ~296 glyphs / 27 files, DOM chrome only; canvas
glyph sets stay geometric. Optional: viewport registration brackets on the Cosmos/Observatory
frame (hairline, chrome-layer, never indigo).
