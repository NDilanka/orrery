<script lang="ts">
  // THE COSMOS (A4) — the multi-loop home. Every registered loop is a small
  // star-system glyph laid out on a field; the platform made visible. This is
  // Tier-1 ONLY (plan §3): a glance answers "is it healthy / does it need me?"
  //   - star color + glow by status (M4.5: running is grayscale, distinguished from
  //     idle/done by motion + brightness, never hue — only failed/handoff/quota stay
  //     chromatic, plan §5 "the only chromatic pixels in the app are alerts")
  //   - the FOUR not-running palettes for rest-states (greyscale-separable
  //     silhouettes: certified-seal · banked dome · frost crystal ·
  //     handoff beacon wedge) — never hue alone
  //   - a thin cost-horizon ring whose tightness reads spend (invisible <50%)
  //   - running loops gently animate; idle ones are dim
  //
  // The DOM roster below the field is the single home for both ENTER and EDIT
  // (the duplicate edit-rail in +page.svelte is removed). Each row carries a
  // status dot (shape + label, never hue alone), the loop id, its cost, an
  // ENTER action, and a brass edit action → cosmosStore.editLoop(id). On a phone
  // it is a scrollable bottom sheet; on desktop a compact bottom bar.
  //
  // Hover → a label card clamped to the host (flips below the glyph near the
  // top). On touch the first tap reveals the card; a second tap / the explicit
  // button navigates — we never dive straight in on first touch.
  //
  // CLIENT-ONLY: Pixi is dynamically imported inside onMount and browser-guarded
  // so it never runs at build/SSR/prerender time. A single rAF eases motion;
  // reduced motion (uiStore.reducedMotion) freezes all decorative motion (only
  // state cross-fades).

  import { onMount } from 'svelte';
  import { browser } from '$app/environment';
  import { cosmosStore, type LoopSummary } from '../stores/cosmos.svelte';
  import { uiStore } from '../stores/ui.svelte';
  import { settingsStore } from '../stores/settings.svelte';
  import { fmtRelative } from '../timefmt';
  import { restColor, stateKey, stateGlyph, stateLabel } from '../palette';
  import { initTheme, FALLBACK } from '../theme';
  import {
    makeGlowTexture,
    makeVignetteTexture,
    makeStarfieldLayers,
    muteColor,
    mixColor,
    sizeGlowSprite,
    type GlowTexture,
  } from './fx';

  let { onEnter }: { onEnter: (loopId: string) => void } = $props();

  let host: HTMLDivElement;
  // Each glyph publishes its on-canvas screen position here. Set from layout(), so it
  // changes only on resize / loop-count change (NOT every frame) — the always-visible
  // "station" labels read these to sit directly under their glyph.
  let positions = $state<{ id: string; x: number; y: number; r: number }[]>([]);

  // U3 Task 4 — the first-run onboarding checklist card (only when zero loops exist).
  // Session-scoped dismiss: a fresh mount always re-offers it, but the user can close
  // it without creating a loop and it stays gone for the rest of this visit.
  let onboardingDismissed = $state(false);

  // Session-local dismiss for the "showing demo fixtures" backend-error strip below.
  // Holds the LAST dismissed message so a *changed* backendError (a new/different
  // failure) re-shows the strip instead of staying suppressed forever.
  let dismissedBackendError = $state<string | null>(null);

  // M5.1 (docs/ui-modernization-plan.md §6): the canvas-only jewel-tone scene palette
  // (atmosphere tint + the runCore burn-white) — same fallback literals as theme.ts's
  // FALLBACK.scene, reused here so `C.scene` has a valid value before onMount resolves
  // the live tokens (see the FALLBACK-safe guard in palette.ts's hue()).
  const SCENE_FALLBACK = {
    runCore: 0xfdf8ee,
    run: 0xf6cb6c,
    done: 0x35d298,
    paused: 0xff894a,
    quota: 0xa0c1ff,
    needs: 0xeab532,
    fail: 0xf75c66,
    atmo: 0x28d3be,
  };

  // palette — resolved from tokens.css via theme.ts (the single color source, plan §M0.4)
  // as soon as onMount runs, below; starts as the static fallback (== today's literal hex)
  // so there's a valid value even for the instant before that resolution happens.
  let C = {
    void: FALLBACK.void,
    brass: FALLBACK.brass,
    starlight: FALLBACK.starlight,
    ember: FALLBACK.ember,
    cyan: FALLBACK.cyan,
    amber: FALLBACK.amber,
    green: FALLBACK.green,
    crimson: FALLBACK.crimson,
    auditor: FALLBACK.auditor,
    horizonRose: FALLBACK.horizonRose,
    frost: FALLBACK.frost,
    // M4.5 monochrome sweep — grayscale text-emphasis tiers (theme.ts exposes these
    // specifically so Cosmos/Observatory can draw calm states off them instead of the
    // retired hue aliases; see glyphColor() below).
    em: FALLBACK.em,
    // M5.1 — canvas-only scene hues (atmosphere tint, runCore); see SCENE_FALLBACK above.
    scene: FALLBACK.scene ?? SCENE_FALLBACK,
  };

  // WS-C (full light theme): re-tint the whole Pixi scene when the app theme flips
  // (dark ⇄ light). `retintScene` is assigned once Pixi is up (inside onMount); this effect
  // watches the sanctioned reactive source (settingsStore.resolvedTheme). The re-tint ALSO
  // runs off a MutationObserver on <html data-theme> inside onMount, so a raw attribute
  // toggle re-tints too — both paths converge on one guarded function, so a double-fire is a
  // cheap no-op. Dark is the default and untouched: with data-theme=dark this never re-tints.
  let retintScene: (() => void) | null = null;
  $effect(() => {
    // read BOTH the mode (light/dark) and the skin (classic/cobalt) so a flip of
    // either re-tints the scene; the guarded function below is idempotent.
    void settingsStore.resolvedSkin;
    if (settingsStore.resolvedTheme) retintScene?.();
  });

  function fmtUsd(n: number): string {
    return '$' + n.toFixed(2);
  }

  // A full glanceable aria-label for a roster row / hover card.
  function loopAria(l: LoopSummary): string {
    const parts = [
      `Loop ${l.id}`,
      stateLabel(stateKey(l.status, l.restState)),
      `spend ${fmtUsd(l.cumUsd)}`,
    ];
    if (l.currentItem) parts.push(`on ${l.currentItem}`);
    return parts.join(', ');
  }

  // star color by status / rest-state (shared with the Observatory's star — src/lib/palette.ts).
  // The fallback (0x6c779e, a dim idle ember) is Cosmos-specific — the Observatory's star instead
  // falls back to a bright starlight, so it's passed explicitly rather than baked into the shared
  // function. restColor() now encodes the full M4 alert taxonomy itself (including bare
  // 'quota-wait' and 'running'), so no local overrides are needed here.
  function glyphColor(l: LoopSummary): number {
    return restColor(l.status, l.restState, 0x6c779e);
  }

  // Enter a loop's system.
  function enterFromRow(id: string) {
    onEnter(id);
  }

  // WS-R: delete a loop from the roster. cosmosStore.deleteLoop removes it from local state
  // itself (and tears down the persisted loop dir under Tauri). Gated by the app's
  // confirm-destructive setting: when ON we ask first (id in the message); when OFF we delete
  // straight away — the setting is the single source of the "are you sure?" policy.
  async function removeLoop(id: string) {
    // a RUNNING (or stopping) loop deserves a sterner warning — the roster summary's
    // `status` is the live run status (restState only describes NOT-running palettes).
    const summary = cosmosStore.get(id);
    const active = summary?.status === 'running' || summary?.status === 'stopping';
    const msg = active
      ? `Loop ${id} is currently running. Delete it and its state? Its files, logs, and run state are removed.`
      : `Delete loop ${id}? This removes it from the cosmos.`;
    if (
      settingsStore.data.general.confirmDestructive &&
      typeof window !== 'undefined' &&
      !window.confirm(msg)
    ) {
      return;
    }
    await cosmosStore.deleteLoop(id);
  }

  // ── at-scale filter ──────────────────────────────────────────────────────
  // The labelled station glyphs ARE the roster now; below FILTER_THRESHOLD loops
  // the field needs no chrome. For a WALL of loops a slim top filter DIMS the
  // stations that don't match so "what needs me?" still answers at a glance — the
  // Pixi field stays positional (glyphs never move).
  type RosterFilter = 'all' | 'needs' | 'running';
  let filter = $state<RosterFilter>('all');
  const FILTER_THRESHOLD = 6;
  const showFilters = $derived(cosmosStore.loops.length > FILTER_THRESHOLD);

  // How many loops are waiting on a human — the one glance-first count.
  const needsYouCount = $derived(cosmosStore.needsYouCount);
  const runningCount = $derived(cosmosStore.loops.filter((l) => l.status === 'running').length);

  // Does a loop pass the active filter? (drives station dimming; all-pass when no filter UI)
  function matches(l: LoopSummary): boolean {
    if (!showFilters || filter === 'all') return true;
    if (filter === 'needs') return l.needsYou;
    if (filter === 'running') return l.status === 'running';
    return true;
  }

  onMount(() => {
    if (!browser) return;
    let destroyed = false;
    let app: any = null;
    let raf = 0;
    let cleanup: (() => void) | null = null;

    // resolve the live CSS custom properties into numeric colors BEFORE any Pixi setup —
    // see ../theme.ts. Falls back to the FALLBACK-seeded C above if resolution fails.
    const t = initTheme();
    C = {
      void: t.void,
      brass: t.brass,
      starlight: t.starlight,
      ember: t.ember,
      cyan: t.cyan,
      amber: t.amber,
      green: t.green,
      crimson: t.crimson,
      auditor: t.auditor,
      horizonRose: t.horizonRose,
      frost: t.frost,
      em: t.em ?? FALLBACK.em,
      scene: t.scene ?? FALLBACK.scene ?? SCENE_FALLBACK,
    };

    (async () => {
      const PIXI = await import('pixi.js');
      if (destroyed) return;

      app = new PIXI.Application();
      await app.init({
        background: C.void,
        antialias: true,
        resizeTo: host,
        autoDensity: true,
        resolution: Math.min(window.devicePixelRatio || 1, 2),
      });
      if (destroyed) {
        // teardown may have already run during `await app.init()` and set `app = null` after
        // destroying it — guard so this cleanup path never TypeErrors on a null app.
        app?.destroy(true);
        return;
      }
      host.appendChild(app.canvas);

      // ── shared fx (plan §M2.9 "Cosmos parity"): the SAME glow-texture builder
      // Observatory uses (fx.ts), built once against the real renderer — every glyph's
      // halo below is a tinted/scaled Sprite draw instead of the old fixed 3-ring
      // stacked-alpha-circle falloff.
      const glowTex: GlowTexture = makeGlowTexture(PIXI, app.renderer);

      const world = new PIXI.Container();
      app.stage.addChild(world);

      // WS-C: the scene follows the LIVE <html data-theme> attribute (not the store) so both
      // the store-driven path and a raw devtools/harness attribute toggle re-tint identically.
      const currentTheme = () =>
        document.documentElement.dataset.theme === 'light' ? 'light' : 'dark';
      // the selectable skin (theme family); a same-lightness skin flip must re-tint too
      const currentSkin = () =>
        document.documentElement.dataset.skin === 'cobalt' ? 'cobalt' : 'classic';
      // WS-R (light-mode glow): the screen-blended glow layers below (the aurora wash + every
      // per-glyph corona) VANISH on the light theme — 'screen' over a near-white --void is a
      // near no-op, so state glows wash out to paper. On light they flip to 'multiply' so the
      // white glow texture, tinted a saturated state hue, DARKENS the paper into a visible
      // color wash (watercolor) instead. Dark is untouched: this returns 'screen'.
      const glowBlend = (): 'screen' | 'multiply' =>
        currentTheme() === 'light' ? 'multiply' : 'screen';

      // M5 (plan §6 "aurora-teal-led atmosphere"): a faint scene.atmo wash sitting behind
      // EVERYTHING — reuses the same glow texture as a big soft radial, tinted teal at very
      // low alpha so it reads as sky atmosphere, not paint. Sized on resize; a slow shimmer
      // (frozen under reduced motion) lives in tick() below.
      const aurora = new PIXI.Sprite(glowTex.texture);
      aurora.anchor.set(0.5);
      aurora.blendMode = glowBlend(); // WS-R: 'screen' on dark, 'multiply' on light
      aurora.tint = C.scene.atmo;
      aurora.alpha = 0; // sized+set by rebuildVignette/onResize + tick
      const starfieldFar = new PIXI.Graphics(); // M2.9: two-layer parallax field, far (dim/cool/small)
      const starfieldNear = new PIXI.Graphics(); // near (brighter/bigger)
      const glowsC = new PIXI.Container(); // pooled per-glyph glow sprites (screen blend), BEHIND the discs
      const g = new PIXI.Graphics(); // all glyph discs/silhouettes/rings, redrawn each frame
      world.addChild(aurora, starfieldFar, starfieldNear, glowsC, g);

      // M2.9: pooled glow sprite per loop id — mirrors Observatory's per-planet glow pool
      // (plan §M2.7 "pool Graphics keyed by o.key") so glow sprites aren't torn down/rebuilt
      // every frame; stale ids (a loop removed from the roster) get pruned in tick().
      const glowPool = new Map<string, any>();
      function getGlowSprite(id: string) {
        let s = glowPool.get(id);
        if (!s) {
          s = new PIXI.Sprite(glowTex.texture);
          s.anchor.set(0.5);
          s.blendMode = glowBlend(); // WS-R: 'screen' on dark, 'multiply' on light
          glowsC.addChild(s);
          glowPool.set(id, s);
        }
        return s;
      }

      let w = host.clientWidth || 800;
      let h = host.clientHeight || 600;

      // ── M2.9: two-tier parallax starfield data (fx.ts, same params as Observatory) —
      // regenerated on resize; the per-frame twinkle/drift redraw lives in tick(). ──
      let starData: ReturnType<typeof makeStarfieldLayers>;
      // M5: far layer leans cooler/tealer (was starlight/frost) — the atmosphere aurora cast
      // reaches the starfield itself, not just the wash below. WS-C: now `let` so the theme
      // flip (applyThemeToScene) can recompute it from the light-tuned starlight/atmo.
      let farTint = mixColor(C.starlight, C.scene.atmo, 0.4);

      // ── M2.9: cached vignette (rebuild on resize only) ──
      let vignetteSprite: any = null;
      function rebuildVignette(vw: number, vh: number) {
        // Dark path is byte-identical to before (default black, cornerAlpha 0.7). Light gets a
        // gentle dark-slate corner at low alpha so a light void still frames with a little
        // depth instead of heavy black corners.
        const tex =
          currentTheme() === 'light'
            ? makeVignetteTexture(PIXI, app.renderer, vw, vh, { color: 0x2a2f3e, cornerAlpha: 0.16 })
            : makeVignetteTexture(PIXI, app.renderer, vw, vh);
        if (vignetteSprite) {
          const old = vignetteSprite.texture;
          vignetteSprite.texture = tex;
          old.destroy(true);
        } else {
          vignetteSprite = new PIXI.Sprite(tex);
          world.addChild(vignetteSprite); // top-most layer, below the DOM station labels
        }
      }
      // M5: size the aurora wash to comfortably cover the canvas on any aspect ratio; its
      // own texture falloff (see makeGlowTexture) gives it a soft radial edge for free.
      function resizeAurora(vw: number, vh: number) {
        sizeGlowSprite(aurora, glowTex, vw / 2, vh * 0.42, Math.max(vw, vh) * 0.85);
      }
      starData = makeStarfieldLayers(w, h);
      rebuildVignette(w, h);
      resizeAurora(w, h);

      // ── WS-C: theme flip (dark ⇄ light) re-tint ──────────────────────────────
      // Re-probe the now-overridden CSS vars (theme.ts), rebuild the palette, flip the
      // renderer background, re-tint the aurora, recompute the starfield tint, and regenerate
      // the baked vignette texture for the new theme. The rAF tick reads C/farTint fresh every
      // frame and eases each glyph color via restColor(theme()) — so the glyph discs/glows
      // cross-fade to the new scene hues on their own; no per-glyph texture is baked. Guarded
      // on the live attribute so both the store effect and the observer below are idempotent.
      let appliedSceneTheme = currentTheme();
      let appliedSkin = currentSkin();
      function applyThemeToScene() {
        const th = currentTheme();
        const sk = currentSkin();
        if (th === appliedSceneTheme && sk === appliedSkin) return;
        appliedSceneTheme = th;
        appliedSkin = sk;
        const t = initTheme();
        C = {
          void: t.void,
          brass: t.brass,
          starlight: t.starlight,
          ember: t.ember,
          cyan: t.cyan,
          amber: t.amber,
          green: t.green,
          crimson: t.crimson,
          auditor: t.auditor,
          horizonRose: t.horizonRose,
          frost: t.frost,
          em: t.em ?? FALLBACK.em,
          scene: t.scene ?? FALLBACK.scene ?? SCENE_FALLBACK,
        };
        try {
          app.renderer.background.color = C.void;
        } catch {
          /* ignore — older/newer Pixi background API shape */
        }
        aurora.tint = C.scene.atmo;
        farTint = mixColor(C.starlight, C.scene.atmo, 0.4);
        rebuildVignette(w, h);
        // WS-R: blendMode is set at CONSTRUCTION — flip every live glow sprite (+ the aurora
        // wash) to the new theme's mode so a runtime dark⇄light toggle re-applies the light
        // branch (screen→multiply / multiply→screen). The tints self-correct in tick (they
        // read `muteTarget`, which keys off appliedSceneTheme just updated above).
        const gb = glowBlend();
        aurora.blendMode = gb;
        for (const s of glowPool.values()) s.blendMode = gb;
      }
      retintScene = applyThemeToScene;
      const themeObserver = new MutationObserver(() => applyThemeToScene());
      themeObserver.observe(document.documentElement, {
        attributes: true,
        attributeFilter: ['data-theme', 'data-skin'],
      });

      // ── layout: a balanced, CENTRED constellation that reflows on resize ──
      // Cell size is CAPPED so a couple of loops cluster together in the middle
      // (each with its label beneath) instead of being marooned at the far edges;
      // glyphs scale up when there's room so they never read as lost dots.
      type Cell = { x: number; y: number; cell: number; baseR: number };
      let cells: Cell[] = [];
      function baseRadiusFor(cell: number): number {
        return Math.max(14, Math.min(34, cell * 0.16));
      }
      function layout() {
        const loops = cosmosStore.loops;
        const n = loops.length;
        if (n === 0) {
          cells = [];
          positions = [];
          return;
        }
        const cols = Math.max(1, Math.min(n, Math.round(Math.sqrt(n * (w / Math.max(h, 1))))));
        const rows = Math.ceil(n / cols);
        const cellW = Math.min(300, (w * 0.82) / cols);
        const cellH = Math.min(260, (h * 0.66) / rows);
        const cell = Math.min(cellW, cellH);
        const baseR = baseRadiusFor(cell);
        const gridW = cols * cellW;
        const gridH = rows * cellH;
        const x0 = (w - gridW) / 2 + cellW / 2;
        // bias the cluster slightly ABOVE centre so each glyph's label has room below
        const y0 = (h - gridH) / 2 + cellH / 2 - h * 0.04;
        cells = loops.map((_, i) => {
          const r = Math.floor(i / cols);
          const c = i % cols;
          const rowCount = r === rows - 1 ? n - r * cols : cols;
          const rowW = rowCount * cellW;
          const rx0 = (w - rowW) / 2 + cellW / 2; // centre a partial last row
          return { x: rx0 + c * cellW, y: y0 + r * cellH, cell, baseR };
        });
        // publish positions for the HTML station labels (x/y centre + base radius)
        positions = loops.map((l, i) => ({ id: l.id, x: cells[i].x, y: cells[i].y, r: cells[i].baseR }));
      }
      layout();

      const onResize = () => {
        w = host.clientWidth || 800;
        h = host.clientHeight || 600;
        starData = makeStarfieldLayers(w, h);
        rebuildVignette(w, h);
        resizeAurora(w, h);
        layout();
      };
      const ro = new ResizeObserver(onResize);
      ro.observe(host);

      // hit-positions for hover/click (screen space)
      const hits = new Map<string, { x: number; y: number; r: number }>();

      function pick(mx: number, my: number): string | null {
        let best: string | null = null;
        let bestD = Infinity;
        for (const [id, p] of hits) {
          const d = (p.x - mx) ** 2 + (p.y - my) ** 2;
          if (d < (p.r + 14) ** 2 && d < bestD) {
            bestD = d;
            best = id;
          }
        }
        return best;
      }

      // Clicking a glyph enters its system; hover just shows a pointer cursor.
      // The always-visible station labels carry name/status/cost + enter/edit, so
      // touch & keyboard go through those real buttons (no canvas tap-dance needed).
      const onMove = (e: MouseEvent) => {
        const rect = app.canvas.getBoundingClientRect();
        const id = pick(e.clientX - rect.left, e.clientY - rect.top);
        app.canvas.style.cursor = id ? 'pointer' : 'default';
      };
      const onClick = (e: MouseEvent) => {
        const rect = app.canvas.getBoundingClientRect();
        const id = pick(e.clientX - rect.left, e.clientY - rect.top);
        if (id) onEnter(id);
      };
      app.canvas.style.pointerEvents = 'auto';
      app.canvas.addEventListener('mousemove', onMove);
      app.canvas.addEventListener('click', onClick);
      cleanup = () => {
        ro.disconnect();
        themeObserver.disconnect();
        retintScene = null;
        app.canvas.removeEventListener('mousemove', onMove);
        app.canvas.removeEventListener('click', onClick);
        // explicit texture teardown — app.destroy(true, { children: true }) below tears down
        // display objects but not these two generated-once textures (renderer.generateTexture
        // output, not loaded assets): the shared glow texture every pooled sprite/aurora tints,
        // and the final live vignette sprite's texture (rebuildVignette already destroys the
        // PREVIOUS texture on every resize — this mirrors that same call for the last one).
        // Guarded: PIXI's own Texture.destroy() is already a safe no-op on an already-destroyed
        // texture, but wrap anyway so a future PIXI version can't turn that into a throw here.
        try {
          glowTex.texture.destroy(true);
        } catch {
          /* ignore */
        }
        try {
          vignetteSprite?.texture?.destroy(true);
        } catch {
          /* ignore */
        }
      };

      // eased per-glyph signals (keyed by loop id)
      const ease = new Map<string, { glow: number; col: number }>();
      function lerp(a: number, b: number, k: number) {
        return a + (b - a) * k;
      }
      function lerpColor(a: number, b: number, k: number) {
        const ar = (a >> 16) & 255, ag = (a >> 8) & 255, ab = a & 255;
        const br = (b >> 16) & 255, bg = (b >> 8) & 255, bb = b & 255;
        return (
          (Math.round(lerp(ar, br, k)) << 16) |
          (Math.round(lerp(ag, bg, k)) << 8) |
          Math.round(lerp(ab, bb, k))
        );
      }
      // M2.9/M2.7: dt-correct a per-60fps-frame lerp constant against the REAL frame delta
      // (`dt`, ms) so easing speed no longer depends on the display's refresh rate — same
      // recipe as Observatory. k_dt = 1 − (1 − k60)^(dt/16.67); reduces to exactly k60 at a
      // nominal 60fps frame.
      function kdt(k60: number, dt: number): number {
        return 1 - Math.pow(1 - k60, dt / 16.67);
      }

      // M2.9/M5: two-tier color muting — large fills (the glyph disc/silhouette body) move to
      // a "base" tier muted toward void; small accents (inner highlight circles, crack/notch
      // strokes, the plinth ticks) keep the un-muted "core" hue. These hues (the M5.1
      // jewel-tone scene.* palette via restColor/palette.ts) are the per-silhouette identity
      // hues fx.ts's `muteColor` doc comment calls out by name, not the generic
      // run/ok/warn/err/idle status vocabulary. M5 (plan §6 "the scene is the color"): the
      // old values (0.16/0.4) crushed the jewel tones toward gray — worse for the glow
      // (MORE muted than the fill it haloed, backwards for a "living, colorful sky") than
      // for the fill. Retuned so fills stay rich and glow reads as clearly, saturatedly
      // colored; wantGlow (below) still keeps failed-dark's glow near-zero so "dead" stays
      // dead regardless of how saturated the tint is.
      const MUTE_FILL = 0.08;
      const MUTE_GLOW = 0.15;
      // WS-R (light-mode glow): on the LIGHT theme --void is near-WHITE, so muting a scene hue
      // toward it (as dark does, to darken/desaturate the fill/glow tint) instead washes it out
      // to paper and the glow disappears. On light we mute toward this fixed deep ink instead —
      // SAME MUTE_* strengths, opposite direction — so the hue stays saturated for the multiply
      // wash. Dark theme is byte-identical: `muteTarget` (in tick) stays C.void there.
      const LIGHT_INK = 0x11142a;

      let t = 0;
      let lastCount = -1;
      let lastTime = 0; // real ms timestamp of the previous frame (dt-correction)
      const tick = () => {
        if (destroyed) return;
        const now = performance.now();
        // clamp a huge gap (backgrounded tab) so returning doesn't produce one giant step
        const dt = lastTime ? Math.min(64, now - lastTime) : 16.67;
        lastTime = now;
        // single reduced-motion source (uiStore); read fresh each frame, like the Observatory.
        const reduced = uiStore.reducedMotion;
        // WS-R (light-mode glow): read the applied theme (kept in sync by applyThemeToScene) and
        // pick the fill/glow mute TARGET — C.void on dark (near-black: darkens, unchanged), the
        // fixed deep ink on light (keeps the hue saturated so the multiply glow reads on paper).
        const light = appliedSceneTheme === 'light';
        const muteTarget = light ? LIGHT_INK : C.void;
        const loops = cosmosStore.loops;
        if (loops.length !== lastCount) {
          layout();
          lastCount = loops.length;
        }
        if (!reduced) t += dt / 1000; // real elapsed seconds — every sin(t*…) below is
        // therefore already dt-correct without further changes (same as Observatory's vis.t).

        // ── M2.9: two-layer parallax starfield (far: dim/cool/small, near: bigger/brighter)
        // — per-star twinkle phase + a slow differential drift between layers; both freeze
        // under reduced-motion (same recipe as Observatory). ──
        const driftFar = reduced ? 0 : (t * 3) % w;
        const driftNear = reduced ? 0 : (t * 5.5) % w;
        starfieldFar.clear();
        for (const st of starData.far) {
          const tw = reduced ? 1 : 0.4 + Math.abs(Math.sin(t * 0.9 + st.phase)) * 0.6;
          let x = st.x + driftFar;
          if (x > w) x -= w;
          starfieldFar.circle(x, st.y, st.r).fill({ color: farTint, alpha: st.alpha * tw });
        }
        starfieldNear.clear();
        for (const st of starData.near) {
          const tw = reduced ? 1 : 0.4 + Math.abs(Math.sin(t * 0.9 + st.phase)) * 0.6;
          let x = st.x + driftNear;
          if (x > w) x -= w;
          starfieldNear.circle(x, st.y, st.r).fill({ color: C.starlight, alpha: st.alpha * tw });
        }

        // M5: aurora wash — a faint, slow shimmer (frozen under reduced motion); never a
        // blink, just a gentle breathe on top of the constant low base alpha.
        // WS-R: on light the aurora is a MULTIPLY wash — it needs a higher base to read as a
        // gentle teal atmosphere darkening the white paper; dark is byte-identical (0.045).
        const auroraBase = light ? 0.14 : 0.045;
        aurora.alpha = reduced ? auroraBase : auroraBase + Math.sin(t * 0.12) * 0.015;

        // prune glow sprites for loops no longer in the roster
        const activeIds = new Set(loops.map((l) => l.id));
        for (const [id, s] of glowPool) {
          if (!activeIds.has(id)) {
            s.destroy();
            glowPool.delete(id);
          }
        }
        // same prune, same activeIds set — `ease` grows one entry per loop ever seen
        // (created/retired) if left unbounded, unlike glowPool above which was already pruned.
        for (const id of ease.keys()) {
          if (!activeIds.has(id)) ease.delete(id);
        }

        g.clear();
        hits.clear();

        loops.forEach((l, i) => {
          const cell = cells[i];
          if (!cell) return;
          const { x: cx, y: cy } = cell;
          const baseR = cell.baseR;
          // star radius nudged by spend (log so a big run doesn't dwarf a small)
          const sr = baseR + Math.min(baseR * 0.6, Math.log1p(l.cumUsd) * 2.2);

          const running = l.status === 'running';
          const target = glyphColor(l);
          let e = ease.get(l.id);
          if (!e) {
            e = { glow: 0, col: target };
            ease.set(l.id, e);
          }
          e.col = lerpColor(e.col, target, kdt(0.08, dt));
          // running / needs-you (handoff beacon, active error) = stronger glow; calm
          // done/paused rest-states get a faint halo at most; failed-dark gets NEXT TO
          // NO glow (it must read dead, not radiant) — plan §M2.9/M2.1.
          const wantGlow = running
            ? 1
            : l.restState === 'failed-dark'
              ? 0.05
              : l.needsYou
                ? 0.95
                : l.restState
                  ? 0.3
                  : 0.18;
          e.glow = lerp(e.glow, wantGlow, kdt(0.06, dt));
          const col = e.col; // the saturated "core" hue (restColor's silhouette identity)
          const base = muteColor(col, muteTarget, MUTE_FILL); // muted "base" hue — large fills (WS-R: ink target on light)

          // breathing only while running (steady state never animates, §F)
          const pulse =
            running && !reduced ? 1 + Math.sin(t * 2.0 + i * 1.3) * (0.04 + l.ratePerMin * 0.01) : 1;
          const r = sr * pulse;

          hits.set(l.id, { x: cx, y: cy, r });

          // ── cost-horizon ring (thin; invisible <50%, tightens with spend) ──
          if (l.horizonFrac >= 0.5) {
            const maxHR = baseR * 2.6;
            const hr = maxHR * (1.05 - Math.min(1, l.horizonFrac) * 0.5);
            // cross-wave contract (Hud.svelte's ladder): <80% is NEUTRAL (no alert yet), only
            // 80-99% earns amber and >=100% earns red — a color is still mandatory for the
            // stroke below, so <80% uses the same idle grayscale tier as the rest of the
            // monochrome sweep instead of amber.
            let hcol = C.em?.low ?? 0x787a7f; // em?.low is optional only on the ThemeColors
            // type (see theme.ts) — always populated in practice; 0x787a7f is FALLBACK.em.low
            // itself, so this never actually diverges from the resolved token.
            if (l.horizonFrac >= 1) hcol = C.crimson;
            else if (l.horizonFrac >= 0.8) hcol = C.horizonRose; // now amber, see tokens.css
            g.circle(cx, cy, hr).stroke({ width: 1.2, color: hcol, alpha: 0.55 });
          }

          // ── glow / corona (plan §M2.9, retuned M5 §6 "visibly blooms"): a tinted
          // glow-texture sprite, screen-blended, replacing the old fixed 3-ring
          // stacked-alpha falloff. Tint carries the MUTED base hue (glow always carries the
          // base tint, same rule as Observatory); size and alpha both track e.glow so
          // failed-dark (wantGlow ≈ 0.05 above) reads as dead, not radiant. M5: raised the
          // size/alpha ceiling substantially (was r*(2.3+glow*1.7), alpha glow*0.7) so a
          // running/done glyph visibly blooms — roughly 2x the old presence at full glow —
          // and added a slow glow-only pulse on running loops (independent of the disc's
          // own `pulse` above) so the corona itself breathes, not just the silhouette. ──
          const glowPulse =
            running && !reduced ? 1 + Math.sin(t * 1.4 + i * 0.9) * 0.15 : 1;
          const glowSprite = getGlowSprite(l.id);
          sizeGlowSprite(glowSprite, glowTex, cx, cy, r * (2.8 + e.glow * 2.6) * glowPulse);
          glowSprite.tint = muteColor(col, muteTarget, MUTE_GLOW); // WS-R: ink target on light keeps the wash saturated
          glowSprite.alpha = Math.min(0.95, e.glow * 0.95 * glowPulse);

          // ── the FIVE rest-state silhouettes (greyscale-separable) ──────────
          // M2.9: EXACT same shapes/motion as before — only the main-body fill moves
          // to the muted `base` tier; small accents (inner highlight circles, crack/
          // notch strokes) keep the saturated `col` (core) tier, per plan §M2.2.
          if (l.restState === 'failed-dark') {
            // crashed: a dim crimson disc, no glow (see wantGlow above), cut by a
            // fixed jagged fracture — mirrors the Observatory's star silhouette.
            const flicker = reduced ? 1 : 0.82 + Math.sin(t * 0.55 + i * 0.4) * 0.18;
            g.circle(cx, cy, r).fill({ color: base, alpha: 0.4 * flicker });
            g.circle(cx, cy, r * 0.5).fill({ color: C.void, alpha: 0.5 });
            const crack: [number, number][] = [
              [-0.05, -0.95],
              [0.22, -0.5],
              [-0.15, -0.15],
              [0.3, 0.1],
              [-0.1, 0.45],
              [0.18, 0.95],
            ];
            g.moveTo(cx + crack[0][0] * r, cy + crack[0][1] * r);
            for (let k = 1; k < crack.length; k++) g.lineTo(cx + crack[k][0] * r, cy + crack[k][1] * r);
            g.stroke({ width: 1.8, color: C.void, alpha: 0.85 });
            g.moveTo(cx + crack[0][0] * r, cy + crack[0][1] * r);
            for (let k = 1; k < crack.length; k++) g.lineTo(cx + crack[k][0] * r, cy + crack[k][1] * r);
            g.stroke({ width: 0.7, color: col, alpha: 0.6 });
          } else if (l.restState === 'quota-frost' || l.status === 'quota-wait') {
            // cold crystal: sharp 6-point star
            const spikes = 6;
            g.moveTo(cx + r, cy);
            for (let s = 1; s <= spikes * 2; s++) {
              const ang = (s / (spikes * 2)) * Math.PI * 2;
              const rr = s % 2 === 0 ? r : r * 0.5;
              g.lineTo(cx + Math.cos(ang) * rr, cy + Math.sin(ang) * rr);
            }
            g.fill({ color: base, alpha: 0.9 });
            g.circle(cx, cy, r * 0.35).fill({ color: C.frost, alpha: 0.6 });
          } else if (l.restState === 'stopped-ember') {
            // banked dome with a bank line — idle/paused, not an alert (M4.5: grayscale
            // only), so the accent uses the eased `col` (now glyphColor()'s grayscale
            // override), not the chromatic C.ember token.
            g.circle(cx, cy, r).fill({ color: base, alpha: 0.85 });
            g.rect(cx - r, cy + r * 0.15, r * 2, r * 0.55).fill({ color: C.void, alpha: 0.55 });
            g.circle(cx, cy - r * 0.15, r * 0.5).fill({ color: col, alpha: 0.8 });
          } else if (l.restState === 'handoff-beacon') {
            // distress beacon: rotating amber wedge (slow = urgency). Handoff is "needs you",
            // the amber/warn bucket — NOT a crash — so both arms + the core dot stay the same
            // amber family (previously the primary arm/core were crimson, which read as
            // crashed); the two-armed silhouette stays motion-distinct via alpha (bright arm /
            // dim echo arm), not a second hue. Mirrors Observatory's beacon (~1196-1214).
            g.circle(cx, cy, r).fill({ color: base, alpha: 0.92 });
            const sweep = reduced ? 0 : t * 1.0;
            const beamR = r * 2.6;
            const wedge = 0.6;
            g.moveTo(cx, cy);
            g.arc(cx, cy, beamR, sweep - wedge / 2, sweep + wedge / 2);
            g.closePath();
            g.fill({ color: C.amber, alpha: 0.16 });
            g.moveTo(cx, cy);
            g.arc(cx, cy, beamR, sweep + Math.PI - wedge / 2, sweep + Math.PI + wedge / 2);
            g.closePath();
            g.fill({ color: C.amber, alpha: 0.12 });
            g.circle(cx, cy, r * 0.55).fill({ color: C.amber, alpha: 0.75 });
          } else if (l.restState === 'certified-done') {
            // sealed: emerald disc + brass certification ring + notches. Brass stays
            // LITERAL brass (identity/certification accent, never muted — plan §1). M5: a
            // brighter core (0.4→0.58 alpha) — done is a healthy state, so it earns the
            // same luminous-core treatment as running (plan §6 item 2).
            g.circle(cx, cy, r).fill({ color: base, alpha: 0.96 });
            g.circle(cx, cy, r * 0.6).fill({ color: C.starlight, alpha: 0.58 });
            g.circle(cx, cy, r + 4).stroke({ width: 1.6, color: C.brass, alpha: 0.85 });
            for (let s = 0; s < 8; s++) {
              const ang = (s / 8) * Math.PI * 2;
              g.moveTo(cx + Math.cos(ang) * (r + 4), cy + Math.sin(ang) * (r + 4))
                .lineTo(cx + Math.cos(ang) * (r + 7), cy + Math.sin(ang) * (r + 7))
                .stroke({ width: 1, color: C.brass, alpha: 0.7 });
            }
          } else {
            // running / idle: a muted base disc + a smaller bright core — the same "light
            // source in an atmosphere" anatomy as Observatory's star, replacing the old
            // flat full-saturation disc. M5: running's core now burns scene.runCore (the
            // near-white gold-corona hue restColor already resolves for `col`) at a much
            // higher alpha (0.55→0.85) instead of a flatter starlight tint — the glyph
            // itself, not just its halo, should visibly bloom (plan §6 items 1-2).
            g.circle(cx, cy, r).fill({ color: base, alpha: running ? 0.92 : 0.55 });
            g.circle(cx, cy, r * 0.5).fill({
              color: running ? C.scene.runCore : C.starlight,
              alpha: running ? 0.85 : 0.22,
            });
          }

          // a tiny "system" plinth (three faint base ticks). Names live in the
          // DOM roster + hover card, not Pixi text (which would re-alloc/frame).
          for (let s = 0; s < 3; s++) {
            const ang = Math.PI * 0.5 + (s - 1) * 0.5;
            g.moveTo(cx + Math.cos(ang) * (r + 2), cy + Math.sin(ang) * (r + 2))
              .lineTo(cx + Math.cos(ang) * (r + 5), cy + Math.sin(ang) * (r + 5))
              .stroke({ width: 1, color: col, alpha: 0.25 });
          }
        });

        raf = requestAnimationFrame(tick);
      };
      raf = requestAnimationFrame(tick);
    })();

    return () => {
      destroyed = true;
      if (raf) cancelAnimationFrame(raf);
      cleanup?.();
      if (app) {
        try {
          app.destroy(true, { children: true });
        } catch {
          /* ignore */
        }
        app = null;
      }
    };
  });
</script>

<div class="cosmos">
  <div bind:this={host} class="field" aria-hidden="true"></div>

  <!-- always-visible STATION labels: each glyph names itself (id · status · cost)
       and IS the accessible enter/edit affordance, sitting just under its glyph.
       This replaces the old anonymous-dots + disconnected bottom roster. -->
  <div class="stations" aria-label="loops">
    {#each cosmosStore.loops as l (l.id)}
      {@const p = positions.find((q) => q.id === l.id)}
      {#if p}
        {@const key = stateKey(l.status, l.restState)}
        <div
          class="station"
          class:dim={!matches(l)}
          class:needs={l.needsYou}
          style="left:{p.x}px; top:{p.y + p.r + 12}px;"
        >
          <button class="enter" onclick={() => enterFromRow(l.id)} aria-label="Enter {loopAria(l)}">
            <span class="s-id mono">{l.id}</span>
            <span class="s-row">
              <span class="s-stat {key}">
                <span class="s-glyph" aria-hidden="true">{stateGlyph(key)}</span>
                {stateLabel(key)}
              </span>
              <span class="s-cost num">{fmtUsd(l.cumUsd)}</span>
              {#if l.lastEventAt}
                {@const fresh = fmtRelative(l.lastEventAt)}
                {#if fresh}
                  <span class="s-fresh num">· {fresh}</span>
                {/if}
              {/if}
            </span>
            {#if l.currentItem}
              <span class="s-cur mono" title={l.currentItem}>→ {l.currentItem}</span>
            {/if}
            <!-- trust chip (Task 2): the claimed-vs-verified signal. Skipped when the loop's
                 rest-state badge already says "done · verified" above (would be redundant). -->
            {#if l.trust && key !== 'certified-done'}
              <span class="s-trust {l.trust}">
                <span aria-hidden="true">{l.trust === 'verified' ? '✓' : '◌'}</span>
                {l.trust === 'verified' ? 'verified' : 'unverified'}
              </span>
            {/if}
            {#if l.retroStatus}
              <span class="s-retro {l.retroStatus}">
                <span aria-hidden="true">{l.retroStatus === 'pending' ? '◷' : '✓'}</span>
                retro {l.retroStatus}
              </span>
            {/if}
          </button>
          <button
            class="edit"
            onclick={() => cosmosStore.editLoop(l.id)}
            aria-label="Edit loop {l.id}"
            title="Edit {l.name}"
          >
            <span aria-hidden="true">✎</span>
          </button>
          <button
            class="del"
            onclick={() => removeLoop(l.id)}
            aria-label="delete loop {l.id}"
            title="Delete {l.name}"
          >
            <span aria-hidden="true">✕</span>
          </button>
        </div>
      {/if}
    {/each}
  </div>

  <!-- ── N-needs-you badge: the one glance-first, loud-allowed count ──────────
       glyph + label, never hue alone; only shown when something waits on you -->
  {#if !cosmosStore.loading && needsYouCount > 0}
    <button
      class="needsbadge"
      onclick={() => (filter = filter === 'needs' ? 'all' : 'needs')}
      aria-pressed={filter === 'needs'}
      aria-label="{needsYouCount} {needsYouCount === 1 ? 'loop needs' : 'loops need'} you — filter the roster"
    >
      <span class="nbglyph" aria-hidden="true">!</span>
      <span class="nbcount num">{needsYouCount}</span>
      <span class="nblabel">{needsYouCount === 1 ? 'needs you' : 'need you'}</span>
    </button>
  {/if}

  <!-- at-scale filter bar — only when there are many loops; it DIMS the stations
       that don't match so triage still works on a wall of loops. -->
  {#if showFilters}
    <div class="filters" role="group" aria-label="filter loops">
      <button class="fchip" class:on={filter === 'all'} aria-pressed={filter === 'all'} onclick={() => (filter = 'all')}>
        All<span class="pcount num">{cosmosStore.loops.length}</span>
      </button>
      <button
        class="fchip"
        class:on={filter === 'needs'}
        class:has={needsYouCount > 0}
        aria-pressed={filter === 'needs'}
        onclick={() => (filter = 'needs')}
      >
        Needs you<span class="pcount num">{needsYouCount}</span>
      </button>
      <button
        class="fchip"
        class:on={filter === 'running'}
        aria-pressed={filter === 'running'}
        onclick={() => (filter = 'running')}
      >
        Running<span class="pcount num">{runningCount}</span>
      </button>
    </div>
  {/if}

  <!-- ── overlays (HTML, so they survive any canvas suppression) ──────────── -->
  {#if cosmosStore.loading}
    <!-- M3.2: the standard empty-state pattern — dim glyph + one line, on the shared
         .floating-card surface (no action: loading is transient, nothing to do). -->
    <div class="empty-state floating-card" role="status">
      <span class="empty-glyph" aria-hidden="true">✧</span>
      <p class="empty-line mono">loading the cosmos…</p>
    </div>
  {:else if cosmosStore.loops.length === 0 && !onboardingDismissed}
    <!-- U3 Task 4: a compact 4-step checklist, not a wizard. Only step 1 is a button —
         steps 2-4 are one-line previews of what happens next, so the foot-gun catches
         (describe done, test the gate) are visible before the user ever hits them. -->
    <div class="onboard floating-card" role="status">
      <button class="onboard-x" aria-label="dismiss" onclick={() => (onboardingDismissed = true)}>
        ✕
      </button>
      <p class="etitle">No loops yet</p>
      <p class="esub">Four steps from an empty cosmos to a running loop.</p>
      <ol class="steps">
        <li class="step">
          <span class="stepnum">1</span>
          <div class="stepbody">
            <button class="stepbtn" onclick={() => cosmosStore.igniteNew()}>
              <span aria-hidden="true">✦</span> Create the loop
            </button>
            <p class="stepsub">Opens the Tuning Console — pick a blueprint, calibrate the dials.</p>
          </div>
        </li>
        <li class="step">
          <span class="stepnum">2</span>
          <div class="stepbody">
            <p class="steptitle">Describe done</p>
            <p class="stepsub">
              Write the acceptance criteria and the task spec — right inside the console.
            </p>
          </div>
        </li>
        <li class="step">
          <span class="stepnum">3</span>
          <div class="stepbody">
            <p class="steptitle">Test your gate</p>
            <p class="stepsub">
              ▸ test each gate stage — find a broken command for free, not on iteration 1.
            </p>
          </div>
        </li>
        <li class="step">
          <span class="stepnum">4</span>
          <div class="stepbody">
            <p class="steptitle">Start it</p>
            <p class="stepsub">✦ Start inside the System view spawns the engine and the star lights up.</p>
          </div>
        </li>
      </ol>
    </div>
  {/if}

  {#if cosmosStore.error}
    <div class="errline floating-card" role="alert">
      <span class="err-glyph" aria-hidden="true">⚠</span>
      <span class="emsg mono">couldn't load the cosmos</span>
      <button class="retry" onclick={() => cosmosStore.load()}>retry</button>
    </div>
  {/if}

  <!-- backend-unreachable strip (fix-wave): Tauri's real loop registry failed to load
       and the Cosmos silently fell back to demo fixtures — without this, the roster
       still looks perfectly healthy while showing fake data. Amber (needs-you/warning
       taxonomy — this isn't a crash), mirrors .errline's crimson pill but its own slim
       strip pinned just below the top rail so it never competes with the centered
       needsbadge/filters row. Session-local dismiss; re-shown if the message changes. -->
  {#if cosmosStore.backendError && cosmosStore.backendError !== dismissedBackendError}
    <div class="backend-strip floating-card" role="alert">
      <span class="bs-glyph" aria-hidden="true">⚠</span>
      <span class="bs-msg mono">backend unreachable — showing demo fixtures · {cosmosStore.backendError}</span>
      <button
        class="bs-x"
        aria-label="dismiss backend warning"
        onclick={() => (dismissedBackendError = cosmosStore.backendError)}
      >
        ✕
      </button>
    </div>
  {/if}
</div>

<style>
  .cosmos {
    position: absolute;
    inset: 0;
    overflow: hidden;
  }
  .field {
    position: absolute;
    inset: 0;
  }
  .field :global(canvas) {
    display: block;
  }

  /* ── station labels (one per glyph) — each loop names itself under its star ── */
  .stations {
    position: absolute;
    inset: 0;
    pointer-events: none; /* gaps pass clicks through to the canvas glyphs */
    z-index: var(--z-scene-overlay);
  }
  .station {
    position: absolute;
    transform: translateX(-50%); /* centre on the glyph; `top` already sits below it */
    display: flex;
    align-items: flex-start;
    gap: var(--space-1);
    transition: opacity var(--dur-mid) var(--ease-standard);
  }
  .station.dim {
    opacity: 0.28; /* at-scale filter: non-matching stations recede */
  }
  .station .enter,
  .station .edit,
  .station .del {
    pointer-events: auto;
    border: 1px solid var(--hairline);
    background: color-mix(in srgb, var(--surface-panel) 88%, transparent);
    color: var(--starlight);
    cursor: pointer;
    backdrop-filter: blur(6px);
    transition: border-color var(--dur-fast) var(--ease-standard),
      background var(--dur-fast) var(--ease-standard),
      color var(--dur-fast) var(--ease-standard),
      opacity var(--dur-fast) var(--ease-standard);
  }
  .station .enter {
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: 2px;
    min-width: 96px;
    max-width: 210px;
    padding: var(--space-1) var(--space-3);
    border-radius: var(--radius);
    text-align: center;
  }
  .station .enter:hover {
    border-color: color-mix(in srgb, var(--brass) 50%, transparent);
    background: color-mix(in srgb, var(--brass) 12%, var(--surface-panel));
  }
  .station.needs .enter {
    /* "needs you" (handoff or crash awaiting a human) — amber, matching .needsbadge
       below (plan §5 M4.5 #5: needs-you/handoff stays amber, not the failed red). */
    border-color: color-mix(in srgb, var(--status-warn-core) 45%, var(--hairline));
  }
  .s-id {
    font-size: var(--text-xs);
    letter-spacing: 0.1em;
    text-transform: uppercase;
    color: var(--brass);
    line-height: 1.2;
  }
  .s-row {
    display: inline-flex;
    align-items: center;
    gap: var(--space-2);
    line-height: 1.2;
  }
  .s-stat {
    display: inline-flex;
    align-items: center;
    gap: 3px;
    font-size: var(--text-2xs);
    letter-spacing: 0.04em;
    text-transform: uppercase;
    color: var(--text-meta);
  }
  .s-glyph {
    font-size: var(--text-xs);
  }
  /* M4.5 monochrome sweep: running/done/idle are grayscale-only (motion + the shape/
     glyph already carry the distinction, never hue alone) — only failed (red) and
     needs-you/handoff/quota (amber) stay chromatic (plan §5). */
  .s-stat.running {
    color: var(--status-run-core);
  }
  .s-stat.certified-done {
    color: var(--status-ok-core);
  }
  /* PAUSED is a calm rest state, not an alert — idle-gray, matching Hud.svelte's
     treatment. Quota (frost/wait) stays in the amber attention set (owner call). */
  .s-stat.stopped-ember {
    color: var(--status-idle-core);
  }
  .s-stat.quota-frost,
  .s-stat.quota-wait {
    color: var(--status-warn-core);
  }
  .s-stat.failed-dark,
  .s-stat.error {
    color: var(--status-err-core);
  }
  .s-stat.handoff-beacon,
  .s-stat.handoff {
    color: var(--status-warn-core);
  }
  .s-cost {
    font-size: var(--text-2xs);
    color: var(--text-meta);
  }
  /* relative freshness — "· 5m ago" from run.lastEventAt (Task 4) */
  .s-fresh {
    font-size: var(--text-2xs);
    color: var(--text-faint);
  }
  .s-cur {
    max-width: 180px;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    font-size: var(--text-2xs);
    color: var(--text-faint);
  }
  /* trust chip (Task 2) — claimed-vs-verified, never hue-alone (glyph + word both carry it) */
  .s-trust {
    display: inline-flex;
    align-items: center;
    gap: 3px;
    font-size: var(--text-2xs);
    letter-spacing: 0.04em;
    text-transform: uppercase;
  }
  .s-trust.unverified {
    color: var(--status-warn-core);
  }
  .s-trust.verified {
    color: var(--status-ok-core);
  }
  .s-retro {
    display: inline-flex;
    align-items: center;
    gap: 3px;
    font-size: var(--text-2xs);
    letter-spacing: 0.04em;
    text-transform: uppercase;
    color: var(--text-faint);
  }
  /* retro-pending is an informational "not done yet" meta state, not an alert — stays
     grayscale (M4.5); only failed/needs-you/quota keep chroma. */
  .s-retro.pending {
    color: var(--em-mid);
  }
  /* the per-station edit + delete affordances — quiet until hover / keyboard focus */
  .station .edit,
  .station .del {
    align-self: stretch;
    display: inline-flex;
    align-items: center;
    justify-content: center;
    width: 30px;
    border-radius: var(--radius);
    color: var(--text-meta);
    font-size: var(--text-sm);
    opacity: 0;
  }
  .station:hover .edit,
  .station:focus-within .edit,
  .station:hover .del,
  .station:focus-within .del {
    opacity: 1;
  }
  .station .edit:hover {
    border-color: var(--brass);
    color: var(--brass);
  }
  /* delete is destructive — its hover earns the sanctioned red alert tint (window.confirm is
     the real safety when confirmDestructive is on); the ✎ stays brass, so the two never read
     the same on hover. Both are otherwise identical monochrome ghost buttons. */
  .station .del:hover {
    border-color: var(--status-err-core);
    color: var(--status-err-core);
  }

  /* ── N-needs-you badge — the one element allowed to be loud (amber, plan §5 M4.5 #5:
     needs-you/handoff — including the "waiting on a human" crash case cosmosStore folds
     in here — is the amber alert, distinct from the failed/crashed red used elsewhere) ── */
  .needsbadge {
    position: absolute;
    top: var(--chrome-inset);
    left: 50%;
    transform: translateX(-50%);
    z-index: var(--z-scene-overlay);
    display: inline-flex;
    align-items: center;
    gap: var(--space-2);
    padding: var(--space-1) var(--space-3);
    border-radius: var(--radius-pill);
    border: 1px solid color-mix(in srgb, var(--status-warn-core) 55%, var(--panel-edge));
    background: color-mix(in srgb, var(--status-warn-core) 16%, var(--panel));
    color: var(--starlight);
    backdrop-filter: blur(8px);
    cursor: pointer;
    font-family: var(--font-grotesk);
    transition: border-color var(--dur-fast) var(--ease-standard),
      background var(--dur-fast) var(--ease-standard);
  }
  .needsbadge:hover,
  .needsbadge[aria-pressed='true'] {
    border-color: var(--status-warn-core);
    background: color-mix(in srgb, var(--status-warn-core) 24%, var(--panel));
  }
  .needsbadge .nbglyph {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    width: 16px;
    height: 16px;
    border-radius: 50%;
    background: var(--status-warn-core);
    color: var(--void);
    font-weight: 700;
    font-size: var(--text-xs);
    line-height: 1;
  }
  .needsbadge .nbcount {
    font-size: var(--text-lg);
    font-weight: 700;
    color: var(--starlight);
  }
  .needsbadge .nblabel {
    font-size: var(--text-2xs);
    text-transform: uppercase;
    letter-spacing: 0.12em;
    color: color-mix(in srgb, var(--status-warn-core) 30%, var(--starlight));
  }

  /* ── at-scale filter bar (top, below the needs-you badge) ─────────────────── */
  .filters {
    position: absolute;
    top: calc(var(--chrome-inset) + 46px);
    left: 50%;
    transform: translateX(-50%);
    display: flex;
    justify-content: center;
    flex-wrap: wrap;
    gap: var(--space-1);
    padding: var(--space-1) var(--space-2);
    background: var(--panel);
    border: 1px solid var(--panel-edge);
    border-radius: var(--radius-pill);
    backdrop-filter: blur(8px);
    z-index: var(--z-scene-overlay);
  }
  /* named .fchip (not .pill) to avoid colliding with the new shared .pill primitive
     (primitives.css) — this filter chip is its own look (transparent, no blur),
     unrelated to the navbar/ignite-fab chip shape. */
  .fchip {
    display: inline-flex;
    align-items: center;
    gap: var(--space-1);
    padding: var(--space-1) var(--space-2);
    border-radius: var(--radius-pill);
    border: 1px solid var(--hairline);
    background: transparent;
    color: var(--text-meta);
    cursor: pointer;
    font-family: var(--font-grotesk);
    font-size: var(--text-2xs);
    letter-spacing: 0.06em;
    text-transform: uppercase;
    transition: border-color var(--dur-fast) var(--ease-standard),
      color var(--dur-fast) var(--ease-standard),
      background var(--dur-fast) var(--ease-standard);
  }
  .fchip:hover {
    border-color: color-mix(in srgb, var(--brass) 45%, transparent);
    color: var(--starlight);
  }
  .fchip.on {
    border-color: var(--brass);
    color: var(--brass);
    background: color-mix(in srgb, var(--brass) 8%, transparent);
  }
  .fchip .pcount {
    font-size: var(--text-2xs);
    padding: 0 5px;
    border-radius: var(--radius-pill);
    background: var(--surface-3);
    color: var(--text-meta);
  }
  .fchip.on .pcount {
    background: color-mix(in srgb, var(--brass) 22%, var(--surface-3));
    color: var(--brass);
  }
  /* the Needs-you chip earns an amber edge when the count is nonzero — matches
     .needsbadge (M4.5: needs-you is amber, not the failed/crashed red). */
  .fchip.has {
    border-color: color-mix(in srgb, var(--status-warn-core) 40%, var(--hairline));
    color: var(--starlight);
  }
  .fchip.has .pcount {
    background: color-mix(in srgb, var(--status-warn-core) 30%, var(--surface-3));
    color: var(--starlight);
  }

  /* (the old roster-row, status-dot and hover-card styles were removed —
     the per-glyph stations above replace them entirely) */

  /* ── overlays: the standard empty-state pattern (M3.2) — a dim glyph, one line,
       an optional primary action, on the shared .floating-card surface. Loading and
       error both use it (error adds the retry action); the onboarding checklist below
       is the richer multi-step variant of the same surface. ── */
  .empty-state {
    position: absolute;
    top: 50%;
    left: 50%;
    transform: translate(-50%, -50%);
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: var(--space-2);
    max-width: 320px;
    padding: var(--space-4) var(--space-5);
    text-align: center;
  }
  .empty-glyph {
    font-size: var(--text-xl);
    line-height: 1;
    color: var(--text-faint);
  }
  .empty-line {
    margin: 0;
    font-size: var(--text-sm);
    color: var(--text-meta);
    letter-spacing: 0.02em;
  }
  .etitle {
    margin: 0 0 var(--space-1);
    font-size: var(--text-xl);
    font-weight: 600;
    color: var(--starlight);
  }
  .esub {
    margin: 0;
    font-size: var(--text-sm);
    color: var(--text-meta);
    line-height: 1.5;
  }

  /* ── U3 Task 4: first-run onboarding checklist card ─────────────────────── */
  .onboard {
    position: absolute;
    top: 50%;
    left: 50%;
    transform: translate(-50%, -50%);
    width: min(380px, calc(100vw - 2 * var(--space-5)));
    text-align: left;
    padding: var(--space-5);
    backdrop-filter: blur(10px);
  }
  .onboard .etitle,
  .onboard .esub {
    text-align: left;
  }
  .onboard-x {
    position: absolute;
    top: var(--space-2);
    right: var(--space-2);
    width: 22px;
    height: 22px;
    background: transparent;
    border: 1px solid var(--hairline);
    color: var(--text-faint);
    border-radius: var(--radius-pill);
    font-size: var(--text-2xs);
    cursor: pointer;
    pointer-events: auto;
  }
  .onboard-x:hover {
    border-color: var(--crimson);
    color: var(--crimson);
  }
  .steps {
    list-style: none;
    margin: var(--space-4) 0 0;
    padding: 0;
    display: flex;
    flex-direction: column;
    gap: var(--space-3);
  }
  .step {
    display: flex;
    align-items: flex-start;
    gap: var(--space-3);
  }
  .stepnum {
    flex: none;
    width: 20px;
    height: 20px;
    display: flex;
    align-items: center;
    justify-content: center;
    border: 1px solid var(--hairline);
    border-radius: 50%;
    font-family: var(--font-mono);
    font-size: var(--text-2xs);
    color: var(--text-meta);
  }
  .stepbody {
    flex: 1;
    min-width: 0;
  }
  .steptitle {
    margin: 0 0 2px;
    font-size: var(--text-sm);
    font-weight: 600;
    color: var(--starlight);
  }
  .stepsub {
    margin: 0;
    font-size: var(--text-xs);
    color: var(--text-faint);
    line-height: 1.45;
  }
  .stepbtn {
    pointer-events: auto;
    display: inline-flex;
    align-items: center;
    gap: var(--space-1);
    margin-bottom: 3px;
    padding: var(--space-1) var(--space-3);
    background: color-mix(in srgb, var(--brass) 14%, transparent);
    border: 1px solid var(--brass);
    color: var(--brass);
    border-radius: var(--radius-pill);
    font-family: var(--font-grotesk);
    font-size: var(--text-xs);
    font-weight: 600;
    letter-spacing: 0.04em;
    cursor: pointer;
    transition: background var(--dur-fast) var(--ease-standard);
  }
  .stepbtn:hover {
    background: color-mix(in srgb, var(--brass) 24%, transparent);
  }
  /* M3.2: reskinned onto .floating-card's surface material (gradient + shadow) for
     consistency with the onboarding card and the rest of the app's modal/popover
     surfaces — position/pill-shape and the crimson error tint stay bespoke (this is
     a non-blocking banner that can sit atop an already-populated roster, not a
     takeover), and the .floating-card rule is deliberately overridden below (later
     in source order = wins the cascade at equal specificity). */
  .errline {
    position: absolute;
    top: var(--chrome-inset);
    left: 50%;
    transform: translateX(-50%);
    display: flex;
    align-items: center;
    gap: var(--space-2);
    padding: var(--space-1) var(--space-3);
    border-radius: var(--radius-pill);
    border-color: color-mix(in srgb, var(--crimson) 40%, var(--panel-edge));
    z-index: var(--z-scene-overlay);
  }
  .err-glyph {
    font-size: var(--text-sm);
    color: var(--crimson);
    line-height: 1;
  }
  .emsg {
    font-size: var(--text-xs);
    color: var(--text-meta);
  }
  .retry {
    font-family: var(--font-grotesk);
    font-size: var(--text-xs);
    font-weight: 600;
    letter-spacing: 0.04em;
    padding: var(--space-1) var(--space-3);
    border-radius: var(--radius-pill);
    border: 1px solid var(--hairline);
    background: var(--surface-2);
    color: var(--starlight);
    cursor: pointer;
    transition: border-color var(--dur-fast) var(--ease-standard);
  }
  .retry:hover {
    border-color: var(--brass);
  }

  /* ── backend-unreachable strip — amber taxonomy (needs-you/warning, not a crash),
     mirrors .errline's crimson pill above but sits in its own row clear of the
     centered needsbadge (chrome-inset)/filters (chrome-inset+46) band so the three
     never stack on top of each other when all happen to be present at once. */
  .backend-strip {
    position: absolute;
    top: calc(var(--chrome-inset) + 84px);
    left: 50%;
    transform: translateX(-50%);
    max-width: min(640px, calc(100vw - 2 * var(--space-5)));
    display: flex;
    align-items: center;
    gap: var(--space-2);
    padding: var(--space-1) var(--space-3);
    border-radius: var(--radius-pill);
    border-color: color-mix(in srgb, var(--status-warn-core) 40%, var(--panel-edge));
    z-index: var(--z-scene-overlay);
  }
  .bs-glyph {
    flex: none;
    font-size: var(--text-sm);
    color: var(--status-warn-core);
    line-height: 1;
  }
  .bs-msg {
    min-width: 0;
    font-size: var(--text-xs);
    color: var(--text-meta);
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .bs-x {
    flex: none;
    width: 18px;
    height: 18px;
    display: inline-flex;
    align-items: center;
    justify-content: center;
    background: transparent;
    border: 1px solid var(--hairline);
    color: var(--text-faint);
    border-radius: var(--radius-pill);
    font-size: var(--text-2xs);
    cursor: pointer;
    pointer-events: auto;
    transition: border-color var(--dur-fast) var(--ease-standard),
      color var(--dur-fast) var(--ease-standard);
  }
  .bs-x:hover {
    border-color: var(--panel-edge);
    color: var(--starlight);
  }

  /* phone: the centred needs-you badge has nowhere to go on a ~360-390px width
     without overlapping the top-left ignite-fab (+page.svelte) — drop it (and the
     filter bar that stacks below it) beneath that row instead of colliding. Only
     surfaces once a real loop needs attention, so this was previously untested
     at narrow widths. */
  @media (max-width: 640px) {
    .needsbadge {
      top: 60px;
      padding: 4px var(--space-2);
      gap: var(--space-1);
    }
    .needsbadge .nblabel {
      display: none;
    }
    .filters {
      top: 100px;
    }
  }
</style>
