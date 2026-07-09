<script lang="ts">
  // The PixiJS scene — CLIENT-ONLY. Pixi is dynamically imported inside onMount
  // and guarded with SvelteKit's `browser`, so it never runs at build/SSR/
  // prerender time. Data mutates the store; a single rAF loop eases visual
  // props toward store targets (the tiny data rate is decoupled from 60fps).
  //
  // Renders (A1 base + A2 living motion + M2 canvas-premium pass):
  //   Void background · central Star (radius←cumUsd, bloom/pulse←cost.ratePerMin,
  //   color←run.status) · a pooled PixiJS particle stream flowing from the active
  //   region INTO the star (emission ∝ cost rate; cache-hits tinted cache-teal) ·
  //   one ring per group · a planet per item (eased angle + radius) · the frozen
  //   ghost-target wireframe · the cost-horizon ring animating 50/80/100 ·
  //   dusk (warm ember bank) vs polar-night (frost desaturation) quota transition ·
  //   a two-tier parallax starfield · a corner vignette · a ring-buffer trail on
  //   the current body.
  //
  // §F restraint: prefers-reduced-motion freezes ALL decorative motion (orbits,
  // pulse, particles, spin, starfield drift/twinkle, trails) — only the eased
  // state targets cross-fade. Steady state is never animated; urgency =
  // tightening/slowing, not blinking.
  //
  // M2 canvas-premium pass (docs/ui-modernization-plan.md §M2): glow is now a
  // pre-rendered texture (fx.ts) tinted/scaled per use instead of stacked alpha
  // circles; colors split into a muted "base" tier (large fills/halos) and a
  // saturated "core" tier (small accents/rims) fed by theme.ts's status pairs;
  // a cached vignette sits above the scene; the starfield is two parallax
  // layers with per-star twinkle; rings are flat hairlines except the CURRENT
  // one, which gets a segmented per-arc alpha gradient; every lerp is dt-
  // corrected off the real frame delta; planet Graphics are pooled by key.

  import { onMount } from 'svelte';
  import { browser } from '$app/environment';
  import { runStore, STAR_R0, STAR_K, RING_BASE } from '../stores/run.svelte';
  import { uiStore } from '../stores/ui.svelte';
  import { settingsStore } from '../stores/settings.svelte';
  import { restColor } from '../palette';
  import { initTheme, FALLBACK, type EmTiers } from '../theme';
  import {
    makeGlowTexture,
    makeVignetteTexture,
    makeStarfieldLayers,
    drawSegmentedRing,
    muteColor,
    mixColor,
    sizeGlowSprite,
    WIDE_GLOW_STOPS,
    BURN_GLOW_STOPS,
    type GlowTexture,
  } from './fx';
  import ObservatoryLabels from './ObservatoryLabels.svelte';

  // M4.5 (plan §5 M4.3): a sibling wave moves this canvas onto a full-viewport stage layer
  // behind the grid, so it no longer sits inside the `.g-center` box that used to center it
  // for free. `safeInsets` is the occluded-chrome margin (px, measured from the empty
  // `.g-center` cell) the shell passes down; the scene centers/fits within that unobstructed
  // rect instead of the full canvas — the vignette/starfield/rewind-frame below still span the
  // FULL canvas (open sky behind the panels is the point; only the orbital system itself
  // steers clear of the chrome). Defaults to zero insets so Observatory still centers on the
  // full canvas if no stage layer passes anything (e.g. in isolation / a test host).
  let { safeInsets = { top: 0, right: 0, bottom: 0, left: 0 } }: {
    safeInsets?: { top: number; right: number; bottom: number; left: number };
  } = $props();

  let host: HTMLDivElement;

  // ── on-canvas label overlay state ──────────────────────────────────────────
  // The rAF resolves star + planet screen coords in host CSS px; it publishes a
  // THROTTLED snapshot here (every ~6th frame / on meaningful change) so the
  // Svelte overlay (<ObservatoryLabels/>) can annotate the scene without making
  // the 60fps loop reactive. Values are in host CSS px (overlay shares the box).
  // Task 3: every orbit body gets a small label (key + status glyph); the current body gets the
  // full treatment (larger, undimmed) plus the Task 2 claimed-vs-verified trust glyph prefix.
  type BodyLabel = {
    key: string;
    x: number;
    y: number;
    status: string;
    trust: 'verified' | 'unverified' | null;
    current: boolean;
    // wave U2 Task 3: this body IS runStore.auditTargetKey — the "verifying…" pulse
    // (formerly the Observatory lighthouse's sweeping beam) renders on its label.
    auditing: boolean;
  };
  type Labels = {
    cumUsd: number;
    ratePerMin: number;
    star: { x: number; y: number };
    bodies: BodyLabel[];
    horizonPct: number | null;
  };
  let labels = $state<Labels>({
    cumUsd: 0,
    ratePerMin: 0,
    star: { x: 0, y: 0 },
    bodies: [],
    horizonPct: null,
  });

  // palette — resolved from tokens.css via theme.ts (the single color source, plan §M0.4)
  // as soon as onMount runs, below; starts as the static fallback (== today's literal
  // hex) so there's a valid value even for the instant before that resolution happens.
  // `status` (added M2.2) carries the two-tier {core,base} pairs the canvas now consumes
  // for planets/rings: large fills/halos take `.base` (muted), small
  // rims/cores/accents take `.core` (saturated) — plan §1 "chroma budget by area".
  let C: typeof FALLBACK = {
    void: FALLBACK.void,
    brass: FALLBACK.brass,
    starlight: FALLBACK.starlight,
    ember: FALLBACK.ember,
    cyan: FALLBACK.cyan,
    amber: FALLBACK.amber,
    green: FALLBACK.green,
    crimson: FALLBACK.crimson,
    indigo: FALLBACK.indigo,
    auditor: FALLBACK.auditor,
    ghostBrass: FALLBACK.ghostBrass,
    cacheTeal: FALLBACK.cacheTeal,
    horizonRose: FALLBACK.horizonRose,
    frost: FALLBACK.frost,
    haiku: FALLBACK.haiku,
    sonnet: FALLBACK.sonnet,
    opus: FALLBACK.opus,
    hairline: FALLBACK.hairline,
    status: FALLBACK.status,
    // M5.3 (docs/ui-modernization-plan.md §6): the canvas-only jewel-tone scene palette —
    // consumed below by the star's burn corona, planet tints, and the aurora atmosphere.
    scene: FALLBACK.scene,
  };
  // M4.5: the four text-emphasis tiers (theme.ts) — the canvas's monochrome vocabulary for
  // every element that isn't one of the 5 fixed status pairs or a genuine per-silhouette
  // identity hue (seal/brass, model spectral). Used below for selection/hover rings, the
  // rollback snapback flash, the rewind time-shimmer frame, the seal-flash bloom, and the
  // brake ring — all retired from their old cyan/brass tints (plan §5 M4.5). Kept as its own
  // always-defined variable (rather than folded into `C`) since `em` is only OPTIONAL on
  // ThemeColors/`typeof FALLBACK` (theme.ts's compat note for hand-written partials) — this
  // way every read below is non-nullable without a chain of `!`/`?.` at every call site.
  let emC: EmTiers = FALLBACK.em as EmTiers;

  // WS-C (full light theme): re-tint the whole Pixi scene when the app theme flips
  // (dark ⇄ light). `retintScene` is assigned once Pixi is up (inside onMount); this effect
  // watches the sanctioned reactive source (settingsStore.resolvedTheme). The re-tint ALSO
  // runs off a MutationObserver on <html data-theme> inside onMount, so a raw attribute toggle
  // re-tints too — both converge on one guarded function, so a double-fire is a cheap no-op.
  // Dark is the default and untouched: with data-theme=dark this never re-tints.
  let retintScene: (() => void) | null = null;
  $effect(() => {
    if (settingsStore.resolvedTheme) retintScene?.();
  });

  function modelColor(m: string): number {
    if (m === 'haiku') return C.haiku;
    if (m === 'opus') return C.opus;
    return C.sonnet;
  }
  // M2.2: ring stroke color. 'done'/backlog map onto the theme status pair's BASE (muted)
  // tier; 'in-progress' keeps literal brass — brass is the identity/certification accent
  // (plan §1 principle 3), not a status hue, so it's never muted.
  function ringBaseColor(status: string): number {
    if (status === 'done') return C.status.ok.base;
    if (status === 'in-progress') return C.brass;
    return C.status.idle.base; // backlog
  }
  // M2.2: a planet's two-tier status pair. `.base` drives the disc fill (large area);
  // `.core` drives rims/notches/pulses (small accents). 'ready' stays literal brass (an
  // identity marker, not a status hue) for both tiers — unchanged from before.
  // M5.3 (docs/ui-modernization-plan.md §6): planets may pick up a RESTRAINED scene-hue tint
  // from their state — but muted far harder than the star's own corona (mute 0.5 on the base
  // fill vs the star's ~0.1) so the hierarchy stays "star first" (plan invariant). Only the
  // two non-alert states that have a matching scene hue (done/in-progress) get tinted; warn/
  // err/ready are already chrome-shared alert or identity colors and are untouched.
  // WS-R (light-mode glow): on the LIGHT theme --void is near-WHITE, so muting a scene hue
  // toward it (as dark does, to darken/desaturate) instead washes it to paper — the planet
  // fills/glows disappear. On light we mute toward this fixed deep ink instead (same strengths,
  // opposite direction) so the hue stays saturated for the multiply glow. Dark uses C.void.
  const LIGHT_INK = 0x11142a;
  // The APPLIED scene theme — hoisted out of the Pixi closure so scenePair keys off the
  // same source the rest of tick() does (muteTarget etc.), instead of re-reading the live
  // DOM dataset per planet per frame. Written at Pixi init and by applyThemeToScene, so a
  // dark⇄light flip re-tints the planet pairs in the SAME frame as the scene re-tint (no
  // one-frame inconsistency around the toggle) and the hot path stays DOM-free.
  let appliedSceneTheme: 'light' | 'dark' = 'dark';
  function scenePair(hex: number): { core: number; base: number } {
    const target = appliedSceneTheme === 'light' ? LIGHT_INK : C.void;
    return { core: muteColor(hex, target, 0.12), base: muteColor(hex, target, 0.5) };
  }
  function planetPair(o: { status: string; certified: boolean; merged: boolean }): {
    core: number;
    base: number;
  } {
    const scene = C.scene ?? FALLBACK.scene!;
    if (o.certified || o.merged) return scenePair(scene.done);
    switch (o.status) {
      case 'done':
        return scenePair(scene.done);
      // M4.5 alert taxonomy: 'review' and 'blocked' both mean "a human needs to look at this",
      // not "this crashed" — the warn/amber bucket. Previously 'blocked' was lumped in with
      // 'failed' under err/red; split out so red is reserved for genuinely failed/crashed
      // bodies (plan §5: "ONLY failed/crashed bodies use --status-err … ONLY needs-you/handoff
      // use --status-warn"). M5.3: warn/err stay the chrome-shared alert hues, untinted.
      case 'review':
      case 'blocked':
        return C.status.warn;
      case 'in-progress':
        return scenePair(scene.run);
      case 'failed':
        return C.status.err;
      case 'ready':
        return { core: C.brass, base: C.brass };
      default:
        return C.status.idle; // backlog
    }
  }

  onMount(() => {
    if (!browser) return;
    let destroyed = false;
    let app: any = null;
    let raf = 0;
    let cleanupResize: (() => void) | null = null;

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
      indigo: t.indigo,
      auditor: t.auditor,
      ghostBrass: t.ghostBrass,
      cacheTeal: t.cacheTeal,
      horizonRose: t.horizonRose,
      frost: t.frost,
      haiku: t.haiku,
      sonnet: t.sonnet,
      opus: t.opus,
      hairline: t.hairline,
      status: t.status,
      scene: t.scene,
    };
    emC = t.em as EmTiers;

    // reduced-motion comes from the single uiStore source (read per-frame in tick)

    // M2.2: how far a large fill/halo is muted toward void relative to its "core" hue —
    // used for hues outside theme.ts's 5 fixed status pairs (the star's rest-state
    // silhouettes: ember/frost/crimson/green/starlight are a per-silhouette identity, not
    // the generic run/ok/warn/err/idle vocabulary). See fx.ts `muteColor`. Two strengths:
    // the star's own disc is the one deliberate "this is a light source" identity element
    // of the whole scene (plan §M2 acceptance: "reads as a light source in an atmosphere"),
    // so it's only LIGHTLY muted; the corona/glow tint (already at ≤~12% alpha from the
    // glow texture itself) can take the fuller mute without going dark.
    // M5.3 (docs/ui-modernization-plan.md §6, owner: "the monochrome scene read as DULL"):
    // both constants pulled back toward the source hue — the old 0.16/0.4 crushed the new
    // jewel-tone --scene-* colors (esp. the gold running corona) toward gray. MUTE_FILL still
    // only lightly mutes the star's own "light source" disc; MUTE_GLOW no longer nearly halves
    // saturation on the corona tint (the glow TEXTURE itself already tops out ~12% alpha, so a
    // less-muted tint doesn't blow out — it just lets the hue actually read).
    const MUTE_FILL = 0.1;
    const MUTE_GLOW = 0.15;
    // M5.3: the WIDE gold bleed halo (running only) is the one place the gold should read at
    // near-full saturation — the tight corona and the disc itself are already near-white by
    // design (scene.runCore), so this is the layer that carries the "gold corona" identity;
    // muted far less than MUTE_GLOW.
    const MUTE_GLOW_WIDE = 0.04;

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
        app.destroy(true);
        return;
      }
      host.appendChild(app.canvas);

      // ── shared fx: glow texture (plan §M2.1), built once against the real renderer ──
      const glowTex: GlowTexture = makeGlowTexture(PIXI, app.renderer);
      // M5.3: a second, wider/gentler falloff profile (fx.ts WIDE_GLOW_STOPS) for the running
      // star's "wide bleed" halo layer — see coronaGlowWide below.
      const wideGlowTex: GlowTexture = makeGlowTexture(PIXI, app.renderer, {
        stops: WIDE_GLOW_STOPS,
        size: 320,
      });
      // M5.3: a hotter-peaked falloff (fx.ts BURN_GLOW_STOPS) for the star's own tight corona
      // ONLY — planet glows (pv.glow below) keep the original glowTex/DEFAULT_GLOW_STOPS.
      const burnGlowTex: GlowTexture = makeGlowTexture(PIXI, app.renderer, {
        stops: BURN_GLOW_STOPS,
      });

      // ── scene graph ─────────────────────────────────────────────────────
      const world = new PIXI.Container();
      app.stage.addChild(world);

      const starfieldFar = new PIXI.Graphics(); // backdrop dust, far layer (M2.4)
      const starfieldNear = new PIXI.Graphics(); // backdrop dust, near layer (M2.4)
      const nightG = new PIXI.Graphics(); // dusk/polar-night wash + frost creep
      const horizon = new PIXI.Graphics(); // cost-horizon ring
      const ringsG = new PIXI.Graphics(); // group rings
      const ghostG = new PIXI.Graphics(); // frozen target wireframe
      const trailG = new PIXI.Graphics(); // M2.6: current-body ring-buffer trail
      // M5.3: a second, wider gold bleed halo — running only, drawn BEHIND coronaGlow so the
      // tight corona still reads on top; makes the star's light visibly reach past the orbits.
      const coronaGlowWide = new PIXI.Sprite(wideGlowTex.texture);
      const coronaGlow = new PIXI.Sprite(burnGlowTex.texture); // M2.1/M5.3: star halo (hot-profile glow-texture sprite)
      const starFlareG = new PIXI.Graphics(); // M2.1: 4-point cross-flare, running only
      const supernovaG = new PIXI.Graphics(); // transient crimson supernova on crash
      const starG = new PIXI.Graphics(); // star core
      const planetsC = new PIXI.Container(); // pooled per-planet containers (M2.7)
      const brakeG = new PIXI.Graphics(); // brake-ring "stopping at next <mode>"
      const shimmerG = new PIXI.Graphics(); // Rewind-mode time-shimmer frame (neutral frost)

      // WS-R (light-mode glow): the screen/add-blended glow + transient layers below VANISH on
      // the light theme — screen/add over a near-white --void is a near no-op, so the corona,
      // planet glows and transient blooms wash out. On light they flip to 'multiply' (the white
      // glow texture, tinted a saturated hue, darkens the paper into a color wash — watercolor)
      // and 'normal' (transient strokes read directly). currentTheme reads the LIVE attribute so
      // a runtime dark⇄light toggle re-applies the branch. Dark is byte-identical (screen/add).
      const currentTheme = () =>
        document.documentElement.dataset.theme === 'light' ? 'light' : 'dark';
      const glowBlend = (): 'screen' | 'multiply' =>
        currentTheme() === 'light' ? 'multiply' : 'screen';
      const transientBlend = (): 'add' | 'normal' =>
        currentTheme() === 'light' ? 'normal' : 'add';

      // M2.1: glow discipline — corona reads as an atmosphere (screen), never a hard
      // additive hotspot; 'add' is reserved for true transient moments below.
      coronaGlow.anchor.set(0.5);
      coronaGlow.blendMode = glowBlend();
      coronaGlowWide.anchor.set(0.5);
      coronaGlowWide.blendMode = glowBlend();
      coronaGlowWide.visible = false;
      starFlareG.blendMode = glowBlend();
      supernovaG.blendMode = transientBlend();

      // ── particle stream (pooled ParticleContainer) ──────────────────────
      // A budget of pre-allocated particle sprites flow from the active region
      // into the star; emission rate ∝ cost burn, a cache-teal fraction tinted.
      const PARTICLE_BUDGET = 1500;
      const particleTex = (() => {
        const g = new PIXI.Graphics();
        g.circle(4, 4, 4).fill({ color: 0xffffff });
        const tex = app.renderer.generateTexture(g);
        g.destroy();
        return tex;
      })();
      // position + per-frame size (vertex) + color/alpha update every frame
      const particlesC = new PIXI.ParticleContainer({
        dynamicProperties: { position: true, vertex: true, color: true },
      });
      particlesC.blendMode = glowBlend(); // M2.1: screen-blended on dark; WS-R: 'multiply' on light
      type Mote = {
        sprite: any;
        active: boolean;
        x: number;
        y: number;
        // travel from spawn → star centre, parameterised 0..1
        sx: number;
        sy: number;
        prog: number;
        speed: number;
        teal: boolean;
        size: number;
      };
      const pool: Mote[] = [];
      for (let i = 0; i < PARTICLE_BUDGET; i++) {
        const sprite = new PIXI.Particle({
          texture: particleTex,
          anchorX: 0.5,
          anchorY: 0.5,
        });
        sprite.alpha = 0;
        particlesC.addParticle(sprite);
        pool.push({
          sprite,
          active: false,
          x: 0,
          y: 0,
          sx: 0,
          sy: 0,
          prog: 0,
          speed: 0,
          teal: false,
          size: 1,
        });
      }
      let poolCursor = 0;
      function spawnMote(sx: number, sy: number, teal: boolean) {
        // find a free mote (round-robin; budget is generous so overrun is rare)
        for (let n = 0; n < pool.length; n++) {
          poolCursor = (poolCursor + 1) % pool.length;
          const m = pool[poolCursor];
          if (!m.active) {
            m.active = true;
            m.sx = sx;
            m.sy = sy;
            m.x = sx;
            m.y = sy;
            m.prog = 0;
            m.speed = 0.012 + Math.random() * 0.02;
            m.teal = teal;
            m.size = 0.5 + Math.random() * 0.7;
            return;
          }
        }
      }

      // ── M2.7: pooled per-planet visuals, keyed by o.key ─────────────────────
      // Replaces the old per-frame `planetsC.removeChildren()` + `new Graphics()`: each
      // key gets ONE small container (created once) holding three leaf renderables —
      // `glow` (glow-texture sprite, screen-blended, only visible for the current/
      // selected/alert bodies — plan §M2.1 "only the star, current/selected body, and
      // alert states get true glow"), `disc` (normal-blended: fill + rims + notches), and
      // `fx` (add-blended: transient seal/refute/rollback blooms only). Stale keys (a
      // run reset onto a different item set) are pruned; a key merely hidden by Tier-1 is
      // kept pooled (`visible=false`) so re-entering Observatory doesn't rebuild it.
      type PlanetVisual = {
        container: any;
        glow: any;
        disc: any;
        fx: any;
      };
      const planetPool = new Map<string, PlanetVisual>();
      function getPlanetVisual(key: string): PlanetVisual {
        let v = planetPool.get(key);
        if (v) return v;
        const container = new PIXI.Container();
        const glow = new PIXI.Sprite(glowTex.texture);
        glow.anchor.set(0.5);
        glow.blendMode = glowBlend(); // WS-R: 'screen' on dark, 'multiply' on light
        glow.visible = false;
        const disc = new PIXI.Graphics();
        const fxG = new PIXI.Graphics();
        fxG.blendMode = transientBlend(); // WS-R: 'add' on dark, 'normal' on light
        container.addChild(glow, disc, fxG);
        planetsC.addChild(container);
        v = { container, glow, disc, fx: fxG };
        planetPool.set(key, v);
        return v;
      }

      world.addChild(
        starfieldFar,
        starfieldNear,
        nightG,
        horizon,
        ringsG,
        ghostG,
        trailG,
        particlesC,
        coronaGlowWide,
        coronaGlow,
        starFlareG,
        supernovaG,
        starG,
        planetsC,
        brakeG,
        shimmerG,
      );

      // ── two-tier parallax starfield data (plan §M2.4) — populated below once w/h are
      //    first measured, and regenerated on resize; the PER-FRAME twinkle/drift redraw
      //    lives in tick(). ──
      let starData: ReturnType<typeof makeStarfieldLayers>;
      // far layer reads slightly cool (a touch toward frost) vs. the near layer's neutral
      // starlight — computed once against the resolved theme, not per-star. M5.3 (plan §6
      // "aurora atmosphere: starfield twinkle picks up a faint teal cast"): the far layer's
      // cool lean now bends a further touch toward scene.atmo (aurora teal) on top of the
      // pre-existing frost mix — kept faint (0.22 of the way) so it reads as atmosphere, not a
      // hue swap. A subset of the NEAR layer is tinted the same way below (every 9th star) for
      // a "the sky itself has color" read without recoloring the whole field.
      // WS-C: `let` (was const) so applyThemeToScene can recompute all three from the
      // light-tuned starlight/frost/atmo when the theme flips — the starfield tick reads them
      // fresh each frame, so it re-tints immediately after a flip.
      let sceneAtmo = (C.scene ?? FALLBACK.scene!).atmo;
      let farTint = mixColor(mixColor(C.starlight, C.frost, 0.35), sceneAtmo, 0.22);
      let nearAuroraTint = mixColor(C.starlight, sceneAtmo, 0.45);

      // ── M2.3: cached vignette (rebuild on resize only) ──
      let vignetteSprite: any = null;
      // M5.3 (docs/ui-modernization-plan.md §6): a second, VERY low-alpha vignette tinted
      // scene.atmo (aurora teal) sitting above the black one, screen-blended so it only ever
      // brightens the corners toward teal rather than muddying the black falloff. Its alpha is
      // further modulated per-frame (a slow "breath") in tick() — see vignetteAuroraSprite use
      // below. Uses fx.ts's EXISTING makeVignetteTexture opts (color/cornerAlpha/innerFrac) —
      // no fx.ts signature change needed, this is purely a second call with different opts.
      let vignetteAuroraSprite: any = null;
      function rebuildVignette(vw: number, vh: number) {
        // M5.3 finding: makeVignetteTexture's default cornerAlpha (0.7, fx.ts — UNCHANGED
        // there, Cosmos still gets it as-is) renders as a near-UNIFORM dark wash across the
        // whole canvas in this app's actual Pixi/browser combo, not a true corner-only
        // falloff (verified empirically: corner vs. center brightness is ~identical at any
        // cornerAlpha). At 0.7 that was crushing EVERYTHING ~65-70% dark — almost certainly a
        // major, previously-unnoticed contributor to the owner's "reads as dull" complaint,
        // independent of the color-mute constants above. Scoped down hard for Observatory's
        // own call only (fx.ts's exported default is untouched — Cosmos unaffected) so the
        // new scene hues + corona actually have room to read.
        // WS-C: dark path unchanged (cornerAlpha 0.2, default black). Light gets a gentler
        // dark-slate corner so a light void keeps a touch of framing depth without heavy black.
        const tex =
          currentTheme() === 'light'
            ? makeVignetteTexture(PIXI, app.renderer, vw, vh, { color: 0x2a2f3e, cornerAlpha: 0.1 })
            : makeVignetteTexture(PIXI, app.renderer, vw, vh, { cornerAlpha: 0.2 });
        if (vignetteSprite) {
          const old = vignetteSprite.texture;
          vignetteSprite.texture = tex;
          old.destroy(true);
        } else {
          vignetteSprite = new PIXI.Sprite(tex);
          world.addChild(vignetteSprite); // top-most non-transient layer, below DOM labels
        }
        // M5.3: kept deliberately faint — a first pass at 0.55/0.16 washed the WHOLE frame
        // teal (screen-blend disproportionately brightens/tints a near-black --void), not just
        // the corners the plan calls for. innerFrac pulled out further so the transparent zone
        // covers most of the visible scene; cornerAlpha dropped hard so even the true corners
        // only get a breath, not a tint.
        const scene = C.scene ?? FALLBACK.scene!;
        const auroraTex = makeVignetteTexture(PIXI, app.renderer, vw, vh, {
          color: scene.atmo,
          innerFrac: 0.68,
          cornerAlpha: 0.07,
        });
        if (vignetteAuroraSprite) {
          const old = vignetteAuroraSprite.texture;
          vignetteAuroraSprite.texture = auroraTex;
          old.destroy(true);
        } else {
          vignetteAuroraSprite = new PIXI.Sprite(auroraTex);
          world.addChild(vignetteAuroraSprite);
        }
        // WS-R: set the blend every rebuild (runs on resize AND on theme flip) so the aurora
        // corner wash flips screen⇄multiply with the theme. 'screen' on dark; 'multiply' on
        // light so a low-alpha teal breath darkens the paper corners rather than no-op'ing.
        vignetteAuroraSprite.blendMode = glowBlend();
      }

      // eased visual state (interpolated toward store targets each frame)
      const vis = {
        starR: STAR_R0,
        starColor: C.starlight,
        // M5.3: the running star's gold CORONA hue, eased separately from the near-white core
        // (starColor) — running targets scene.run (gold), every rest state just mirrors
        // starColor (one hue per silhouette, not two).
        coronaColor: (C.scene ?? FALLBACK.scene!).run,
        emitColor: C.sonnet,
        horizonFrac: 0,
        burn: 0,
        night: 0, // 0 day → 1 night
        nightHue: 0, // 0 dusk(ember) → 1 polar(frost)
        restForm: 0, // 0 running → 1 the rest-state silhouette is fully formed
        rewind: 0, // 0 normal → 1 Rewind-mode time-shimmer frame fully on
        // M4.5 safeInsets: the scene center, eased toward the current safe rect's centroid so a
        // prop change (the shell's chrome growing/shrinking) glides rather than snaps — see tick().
        cx: 0,
        cy: 0,
        t: 0,
      };
      // per-planet eased angle (so they rotate smoothly)
      const planetAngle = new Map<string, number>();
      let emitAccrue = 0; // fractional particle emission carry

      // ── A3 transient "moments" (edge-detected from steady state; survive scrub)
      // We track per-key prior values and fire a decaying pulse on a transition.
      // sealFlash: certified flipped true (PASS chime/ring bloom)
      // refuteFx:  verdict became fail (Crimson beam snaps on the failing AC)
      // snapFx:    strikes increased (rollback → a white/gray snapback flash on the body)
      const prevCertified = new Map<string, boolean>();
      const prevStrikes = new Map<string, number>();
      const prevVerdictPass = new Map<string, boolean | null>();
      const sealFlash = new Map<string, number>(); // key → 0..1 decaying
      const refuteFx = new Map<string, number>();
      const snapFx = new Map<string, number>();
      let prevErrorStop = false;
      let supernova = 0; // 0..1 decaying transient on a crash stop

      // hover (set by the mousemove listener; consumed by the planet draw)
      let hoveredKey: string | null = null;
      // throttle for publishing the label snapshot out of the rAF
      let labelFrame = 0;

      // M2.6: ring-buffer trail on the CURRENT body only — [0]=oldest .. [last]=newest.
      const TRAIL_N = 16;
      const trailBuf: { x: number; y: number }[] = [];
      let trailKey: string | null = null; // resets the buffer when the current body changes

      function lerp(a: number, b: number, k: number) {
        return a + (b - a) * k;
      }
      function lerpColor(a: number, b: number, k: number) {
        const ar = (a >> 16) & 255,
          ag = (a >> 8) & 255,
          ab = a & 255;
        const br = (b >> 16) & 255,
          bg = (b >> 8) & 255,
          bb = b & 255;
        const r = Math.round(lerp(ar, br, k));
        const g = Math.round(lerp(ag, bg, k));
        const bl = Math.round(lerp(ab, bb, k));
        return (r << 16) | (g << 8) | bl;
      }
      // M2.7: dt-correct a per-60fps-frame lerp constant against the REAL frame delta
      // (`dt`, ms) so easing speed no longer depends on the display's refresh rate.
      // k_dt = 1 − (1 − k60)^(dt/16.67); reduces to exactly k60 at a nominal 60fps frame.
      function kdt(k60: number, dt: number): number {
        return 1 - Math.pow(1 - k60, dt / 16.67);
      }

      // M4.5 safeInsets: the centroid of the unobstructed rect (full canvas minus the
      // occluded-chrome margin the shell measured) — the orbital system's home point.
      // `safeInsets` is read live (it's the reactive $props binding) so this always reflects
      // whatever the stage layer most recently passed down.
      function safeRectCenter(vw: number, vh: number): { x: number; y: number } {
        const si = safeInsets;
        const sw = Math.max(1, vw - si.left - si.right);
        const sh = Math.max(1, vh - si.top - si.bottom);
        return { x: si.left + sw / 2, y: si.top + sh / 2 };
      }

      let w = host.clientWidth || 800;
      let h = host.clientHeight || 600;
      starData = makeStarfieldLayers(w, h);
      rebuildVignette(w, h);
      // seed the eased center at the real target so mount doesn't glide in from (0,0).
      {
        const c0 = safeRectCenter(w, h);
        vis.cx = c0.x;
        vis.cy = c0.y;
      }

      // ── WS-C: theme flip (dark ⇄ light) re-tint ──────────────────────────────
      // Re-probe the now-overridden CSS vars (theme.ts), rebuild the palette + emphasis tiers,
      // flip the renderer background, recompute the starfield tints, and regenerate the baked
      // vignette textures (black + aurora) for the new theme. The eased star/corona/emit colors
      // self-correct in tick() (they lerp toward restColor(theme())/modelColor() each frame), so
      // the star and planets cross-fade to the new scene hues on their own — no per-body texture
      // is baked (the glow/particle textures are WHITE and tinted at runtime). Guarded on the
      // live attribute so both the store effect and the observer below stay idempotent.
      appliedSceneTheme = currentTheme(); // (component-scope — scenePair reads it too)
      function applyThemeToScene() {
        const th = currentTheme();
        if (th === appliedSceneTheme) return;
        appliedSceneTheme = th;
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
          indigo: t.indigo,
          auditor: t.auditor,
          ghostBrass: t.ghostBrass,
          cacheTeal: t.cacheTeal,
          horizonRose: t.horizonRose,
          frost: t.frost,
          haiku: t.haiku,
          sonnet: t.sonnet,
          opus: t.opus,
          hairline: t.hairline,
          status: t.status,
          scene: t.scene,
        };
        emC = t.em as EmTiers;
        try {
          app.renderer.background.color = C.void;
        } catch {
          /* ignore — older/newer Pixi background API shape */
        }
        sceneAtmo = (C.scene ?? FALLBACK.scene!).atmo;
        farTint = mixColor(mixColor(C.starlight, C.frost, 0.35), sceneAtmo, 0.22);
        nearAuroraTint = mixColor(C.starlight, sceneAtmo, 0.45);
        rebuildVignette(w, h); // (re-sets vignetteAuroraSprite.blendMode for the new theme)
        // WS-R: blendMode is set at CONSTRUCTION — flip every live glow/transient layer to the
        // new theme's mode so a runtime dark⇄light toggle re-applies the light-glow branch. The
        // tints self-correct in tick (they read `muteTarget`, keyed off appliedSceneTheme above).
        const gb = glowBlend();
        const tb = transientBlend();
        coronaGlow.blendMode = gb;
        coronaGlowWide.blendMode = gb;
        starFlareG.blendMode = gb;
        particlesC.blendMode = gb;
        supernovaG.blendMode = tb;
        for (const v of planetPool.values()) {
          v.glow.blendMode = gb;
          v.fx.blendMode = tb;
        }
      }
      retintScene = applyThemeToScene;
      const themeObserver = new MutationObserver(() => applyThemeToScene());
      themeObserver.observe(document.documentElement, {
        attributes: true,
        attributeFilter: ['data-theme'],
      });

      const onResize = () => {
        w = host.clientWidth || 800;
        h = host.clientHeight || 600;
        starData = makeStarfieldLayers(w, h);
        rebuildVignette(w, h);
      };
      const ro = new ResizeObserver(onResize);
      ro.observe(host);

      // ── click / touch / hover: pick the nearest planet → its VerdictPanel ──
      const lastPpos = new Map<string, { x: number; y: number; r: number }>();
      // nearest planet under the pointer (null if none within the hit radius)
      const pickAt = (e: { clientX: number; clientY: number }): string | null => {
        const rect = app.canvas.getBoundingClientRect();
        const mx = e.clientX - rect.left;
        const my = e.clientY - rect.top;
        let best: string | null = null;
        let bestD = 18 * 18; // hit radius²
        for (const [key, p] of lastPpos) {
          const d = (p.x - mx) ** 2 + (p.y - my) ** 2;
          if (d < bestD) {
            bestD = d;
            best = key;
          }
        }
        return best;
      };
      const onClick = (e: MouseEvent) => runStore.selectItem(pickAt(e));
      // touch: same selection path as click (Observatory owns no nav)
      const onPointerDown = (e: PointerEvent) => {
        if (e.pointerType === 'touch') runStore.selectItem(pickAt(e));
      };
      // hover: cursor → pointer over a planet; the rAF draws a hover ring on it
      const onMove = (e: MouseEvent) => {
        const k = pickAt(e);
        hoveredKey = k;
        app.canvas.style.cursor = k ? 'pointer' : 'default';
      };
      const onLeave = () => {
        hoveredKey = null;
        app.canvas.style.cursor = 'default';
      };
      app.canvas.style.pointerEvents = 'auto';
      app.canvas.addEventListener('click', onClick);
      app.canvas.addEventListener('pointerdown', onPointerDown);
      app.canvas.addEventListener('mousemove', onMove);
      app.canvas.addEventListener('mouseleave', onLeave);
      cleanupResize = () => {
        ro.disconnect();
        themeObserver.disconnect();
        retintScene = null;
        app.canvas.removeEventListener('click', onClick);
        app.canvas.removeEventListener('pointerdown', onPointerDown);
        app.canvas.removeEventListener('mousemove', onMove);
        app.canvas.removeEventListener('mouseleave', onLeave);
        // explicit texture teardown — app.destroy(true, { children: true }) below tears down
        // display objects but not these generated-once textures (renderer.generateTexture
        // output, not loaded assets): the three glow textures every corona/pooled sprite
        // tints, the particle-stream dot texture, and the final live vignette sprite's
        // texture (rebuildVignette already destroys the PREVIOUS texture on every resize —
        // this mirrors that same call for the last one). Guarded: Texture.destroy() doesn't
        // throw on a second call, but wrap anyway so a future PIXI version can't turn that
        // into one here.
        try {
          glowTex.texture.destroy(true);
        } catch {
          /* ignore */
        }
        try {
          wideGlowTex.texture.destroy(true);
        } catch {
          /* ignore */
        }
        try {
          burnGlowTex.texture.destroy(true);
        } catch {
          /* ignore */
        }
        try {
          particleTex.destroy(true);
        } catch {
          /* ignore */
        }
        try {
          vignetteSprite?.texture?.destroy(true);
        } catch {
          /* ignore */
        }
      };

      // ── render loop ──────────────────────────────────────────────────────
      const PXPER = 1; // geometry units → px (rings already in px-ish units)
      let lastTime = 0; // real ms timestamp of the previous frame (dt-correction, §M2.7)
      const tick = () => {
        if (destroyed) return;
        const now = performance.now();
        // clamp a huge gap (backgrounded tab) so returning doesn't produce one giant step
        const dt = lastTime ? Math.min(64, now - lastTime) : 16.67;
        lastTime = now;

        // single reduced-motion source (uiStore); read fresh each frame
        const reduced = uiStore.reducedMotion;
        // WS-R (light-mode glow): the fill/glow mute target — C.void on dark (near-black:
        // darkens, unchanged), the fixed deep ink on light (keeps the scene hue saturated so the
        // multiply corona/planet washes read on white paper instead of bleaching out).
        const light = appliedSceneTheme === 'light';
        const muteTarget = light ? LIGHT_INK : C.void;
        const s = runStore.state;
        // M4.5 safeInsets: the orbital system centers on the unobstructed rect (full canvas
        // minus occluded chrome), not the raw canvas center — eased so a safeInsets change
        // (chrome growing/shrinking) glides instead of jump-cutting. The vignette/starfield/
        // rewind-frame below still key off the RAW w/h (full canvas) — only the system itself
        // steers clear of the chrome.
        const safeTarget = safeRectCenter(w, h);
        vis.cx = lerp(vis.cx, safeTarget.x, kdt(0.08, dt));
        vis.cy = lerp(vis.cy, safeTarget.y, kdt(0.08, dt));
        const cx = vis.cx;
        const cy = vis.cy;
        if (!reduced) vis.t += dt / 1000; // real elapsed seconds — every sin(vis.t*…) below
        // is therefore already dt-correct without further changes.
        const running = s.run.status === 'running';
        const rest = s.run.restState;
        // Tier-1 mode (Ambient/Planetarium ONLY since wave U2 — a phone in
        // Observatory renders the full scene now): star + cost-horizon +
        // rest-states + beacon + quota-night ONLY. Planets, rings, the ghost
        // and the brake-ring are dropped; particles are budget-cut.
        const tierOne = uiStore.tierOne;

        // ── M2.4: two-tier parallax starfield (far: dim/cool/small, near: bigger/
        //   brighter) — per-star twinkle phase + a slow differential drift between
        //   layers; both freeze under reduced-motion. ──
        const driftFar = reduced ? 0 : (vis.t * 3) % w;
        const driftNear = reduced ? 0 : (vis.t * 5.5) % w;
        starfieldFar.clear();
        for (const st of starData.far) {
          const tw = reduced ? 1 : 0.4 + Math.abs(Math.sin(vis.t * 0.9 + st.phase)) * 0.6;
          let x = st.x + driftFar;
          if (x > w) x -= w;
          starfieldFar.circle(x, st.y, st.r).fill({ color: farTint, alpha: st.alpha * tw });
        }
        starfieldNear.clear();
        let nearI = 0;
        for (const st of starData.near) {
          const tw = reduced ? 1 : 0.4 + Math.abs(Math.sin(vis.t * 0.9 + st.phase)) * 0.6;
          let x = st.x + driftNear;
          if (x > w) x -= w;
          // M5.3: every 9th near star carries the aurora tint (a stable, deterministic subset
          // — index-based, not random, so it doesn't flicker between colors frame to frame).
          const col = nearI % 9 === 0 ? nearAuroraTint : C.starlight;
          starfieldNear.circle(x, st.y, st.r).fill({ color: col, alpha: st.alpha * tw });
          nearI++;
        }

        // M5.3 (plan §6 "the vignette may carry a very low-alpha teal breath at the edges"):
        // a slow, gentle alpha drift on the aurora vignette layer built in rebuildVignette();
        // frozen (held at its midpoint) under reduced-motion. Faded down when the dusk/polar
        // night wash is active (vis.night, below) so the two atmospheres don't fight for the
        // same corners — the night wash stays the dominant read during a quota pause.
        if (vignetteAuroraSprite) {
          const breath = reduced ? 0.55 : 0.4 + Math.sin(vis.t * 0.15) * 0.15;
          vignetteAuroraSprite.alpha = breath * (1 - vis.night * 0.6);
        }

        // ── A3: edge-detect transient moments from steady state ──────────────
        // (idempotent w.r.t. scrubbing: a moment fires once per real transition;
        //  re-reducing the same prefix produces the same booleans, no re-fire.)
        const fxDecay = reduced ? 1 : 0.018 * (dt / 16.67); // reduced-motion: snap, don't animate
        for (const it of Object.values(s.items)) {
          const pc = prevCertified.get(it.key);
          if (pc !== undefined && !pc && it.certified) sealFlash.set(it.key, 1); // PASS seal
          prevCertified.set(it.key, it.certified);

          const ps = prevStrikes.get(it.key);
          if (ps !== undefined && it.strikes > ps) snapFx.set(it.key, 1); // rollback snapback
          prevStrikes.set(it.key, it.strikes);
        }
        for (const [key, v] of Object.entries(s.verdicts)) {
          const pv = prevVerdictPass.get(key) ?? null;
          if (pv !== false && v.pass === false) refuteFx.set(key, 1); // refute beam
          prevVerdictPass.set(key, v.pass);
        }
        // supernova: an error/crash stop (status flips to error)
        const isError = s.run.status === 'error';
        if (isError && !prevErrorStop) supernova = 1;
        prevErrorStop = isError;
        // decay all transient pulses
        for (const m of [sealFlash, refuteFx, snapFx]) {
          for (const [k, v] of m) {
            const nv = v - fxDecay;
            if (nv <= 0) m.delete(k);
            else m.set(k, nv);
          }
        }
        supernova = Math.max(0, supernova - fxDecay * 0.7);

        // rest-state form factor: 1 when the system is at rest (so the four
        // silhouettes "set" rather than animate), 0 while running.
        vis.restForm = lerp(vis.restForm, rest ? 1 : 0, kdt(0.05, dt));

        // ── resolve every planet's eased screen position ONCE (shared by the
        //   beam, ghost, snapback shimmer & the planet draw below) ──────────
        const spin = vis.t * 0.06; // global slow orbital advance — strictly linear in real
        // elapsed time (vis.t), so angular velocity never depends on frame rate.
        // fit all rings/orbits inside the SAFE rect (not the raw canvas — M4.5 safeInsets)
        // AND the cost-horizon: one shared scale so the fixed RING_BASE/GAP geometry never
        // overflows a small/short safe area or a many-group run (planets, rings & ghost all
        // consult it). Uses the raw (un-eased) safe rect so the fit itself tracks the current
        // chrome instantly; only the system's CENTER (cx/cy above) glides.
        let maxOrbitR = RING_BASE;
        for (const o of runStore.orbits) if (o.ringRadius > maxOrbitR) maxOrbitR = o.ringRadius;
        for (const ring of runStore.rings) if (ring.radius > maxOrbitR) maxOrbitR = ring.radius;
        const safeW = Math.max(1, w - safeInsets.left - safeInsets.right);
        const safeH = Math.max(1, h - safeInsets.top - safeInsets.bottom);
        const fitScale = PXPER * Math.min(1, (Math.min(safeW, safeH) * 0.42) / Math.max(1, maxOrbitR));
        const ppos = new Map<string, { x: number; y: number; r: number; o: any }>();
        for (const o of runStore.orbits) {
          const target = o.angle + (running && !reduced ? spin : 0);
          const prev = planetAngle.get(o.key);
          const a = prev === undefined ? target : lerp(prev, target, kdt(0.06, dt));
          planetAngle.set(o.key, a);
          const r = o.ringRadius * fitScale;
          ppos.set(o.key, { x: cx + Math.cos(a) * r, y: cy + Math.sin(a) * r, r, o });
        }

        // ── eased global signals ──
        const targetR = STAR_R0 + STAR_K * Math.log1p(Math.max(0, s.run.cumUsd));
        vis.starR = lerp(vis.starR, targetR, kdt(0.08, dt));
        // restColor() (../palette.ts, shared with Cosmos) encodes the M4 alert taxonomy directly
        // now — running is the white starlight light source (liveness is carried by motion, not
        // hue), handoff-beacon is amber (needs-you), failed-dark/error are crimson, and
        // paused/done are grayscale. No local override needed.
        const targetColor = restColor(s.run.status, s.run.restState, C.starlight);
        vis.starColor = lerpColor(vis.starColor, targetColor, kdt(0.08, dt));
        // M5.3: the corona/halo hue — gold (scene.run) while actively burning (running, no
        // rest-state settled yet), otherwise mirrors the core color so every rest-state
        // silhouette still reads as ONE hue, not two.
        const targetCorona = running && !rest ? (C.scene ?? FALLBACK.scene!).run : targetColor;
        vis.coronaColor = lerpColor(vis.coronaColor, targetCorona, kdt(0.08, dt));
        vis.emitColor = lerpColor(vis.emitColor, modelColor(runStore.model), kdt(0.06, dt));
        vis.burn = lerp(vis.burn, running ? runStore.burn : 0, kdt(0.05, dt));

        // night transition (dusk ember vs polar frost), keyed off resetType
        const night = runStore.nightType;
        vis.night = lerp(vis.night, night ? 1 : 0, kdt(0.04, dt));
        vis.nightHue = lerp(vis.nightHue, night === 'polar' ? 1 : 0, kdt(0.03, dt));

        // breathing pulse scaled by burn (telemetry liveness; frozen if reduced). M5.3: the
        // amplitude is deepened slightly (was 0.03/0.12) now that the running star actually
        // burns bright enough for the breathing to read.
        const pulseAmt = 0.035 + vis.burn * 0.14;
        const pulse = running && !reduced ? 1 + Math.sin(vis.t * (2.2 + vis.burn * 2.4)) * pulseAmt : 1;
        // M5.3: a slow, independent corona shimmer (separate phase from the breathing pulse)
        // — a gentle size/alpha drift on the WIDE bleed halo only, frozen under reduced-motion.
        const coronaShimmer = reduced ? 1 : 1 + Math.sin(vis.t * 0.35 + 1.7) * 0.06;

        // ── dusk / polar-night wash (behind everything but the dust) ──
        nightG.clear();
        if (vis.night > 0.01) {
          // M4.5: was two hardcoded literal hexes (a hue leak tokens.css/theme.ts couldn't
          // reach) — now token-derived. Quota IS a genuine warn-family alert per the owner's own
          // M4 taxonomy ("the only chromatic pixels are alerts: red…and amber (needs-you/
          // handoff/quota)"), and nightType is ONLY ever non-null while quota is active (see
          // runStore.nightType) — so the dusk (daily-wait) end keeps a whisper of amber via
          // C.ember (already the muted warn-base token), heavily muted further since this is a
          // full-canvas wash, not a small accent. The polar (weekly-wait) end goes fully neutral
          // — its own identity color is the cold frost crystal-spoke motif drawn below, so the
          // base wash underneath it doesn't need its own hue too.
          const duskWash = muteColor(C.ember, muteTarget, 0.45); // WS-R: ink target on light
          const polarWash = C.indigo;
          const dusk = lerpColor(duskWash, polarWash, vis.nightHue);
          nightG.rect(0, 0, w, h).fill({ color: dusk, alpha: 0.34 * vis.night });
          if (vis.nightHue > 0.4) {
            // frost creep: pale crystalline spokes growing from the corners (polar)
            const creep = (vis.nightHue - 0.4) / 0.6;
            const reach = Math.min(w, h) * 0.5 * creep;
            for (const [ox, oy] of [
              [0, 0],
              [w, 0],
              [0, h],
              [w, h],
            ]) {
              for (let i = 0; i < 5; i++) {
                const ang = Math.atan2(cy - oy, cx - ox) + (i - 2) * 0.22;
                const len = reach * (0.5 + (i % 2) * 0.5);
                nightG
                  .moveTo(ox, oy)
                  .lineTo(ox + Math.cos(ang) * len, oy + Math.sin(ang) * len)
                  .stroke({ width: 1, color: C.frost, alpha: 0.12 * vis.night });
              }
            }
          } else {
            // dusk: warm ember bank glow pooling at the bottom
            nightG
              .rect(0, h * 0.6, w, h * 0.4)
              .fill({ color: C.ember, alpha: 0.06 * vis.night });
          }
        }

        // ── rings (one per group, colored by status) — Tier-2, hidden in Tier-1 ──
        // M2.5: every ring drops to a flat hairline (~0.18 alpha) EXCEPT the ring the
        // current body orbits, which gets a segmented per-arc alpha gradient brightening
        // toward that body's current angle (visually "attaches" the ring to its planet).
        ringsG.clear();
        if (!tierOne) {
          const cur0 = runStore.current;
          const curRingIndex = cur0 ? ppos.get(cur0.key)?.o.ringIndex : undefined;
          const curAngle = cur0 ? planetAngle.get(cur0.key) : undefined;
          for (const ring of runStore.rings) {
            const r = ring.radius * fitScale;
            const col = ringBaseColor(ring.status);
            if (curRingIndex !== undefined && ring.ringIndex === curRingIndex && curAngle !== undefined) {
              drawSegmentedRing(ringsG, cx, cy, r, col, {
                segments: 44,
                width: ring.status === 'in-progress' ? 1.6 : 1.2,
                alphaAt: (ang) => {
                  let d = Math.abs(((ang - curAngle + Math.PI) % (Math.PI * 2)) - Math.PI);
                  const t = 1 - d / Math.PI; // 1 at the body, 0 at the opposite side
                  return 0.14 + t * 0.55;
                },
              });
            } else {
              ringsG.circle(cx, cy, r).stroke({ width: 1, color: col, alpha: 0.18 });
            }
          }
        }

        // ── cost horizon (Roche ring) — invisible <50%, tightens toward star ──
        // M2.5: exact thresholds/ladder/pulse math is unchanged — only the base ring
        // stroke is restyled from one continuous circle into the same segmented-arc
        // technique used above (uniform alpha here; there's no single body to brighten
        // toward). Tick marks are untouched.
        horizon.clear();
        const frac = runStore.horizonFrac;
        vis.horizonFrac = lerp(vis.horizonFrac, frac, kdt(0.06, dt));
        if (vis.horizonFrac >= 0.5) {
          const maxR = Math.min(w, h) * 0.46;
          const hr = maxR * (1.05 - Math.min(1, vis.horizonFrac) * 0.55);
          let col = C.amber;
          // urgency = slowing/tightening, not blinking: a slow pulse only at ≥80%
          let pulseA = 0;
          if (vis.horizonFrac >= 1) {
            col = C.crimson;
            pulseA = reduced ? 0 : (1 + Math.sin(vis.t * 1.6)) * 0.5;
          } else if (vis.horizonFrac >= 0.8) {
            col = C.horizonRose;
            pulseA = reduced ? 0 : (1 + Math.sin(vis.t * 1.1)) * 0.5;
          }
          const a = 0.35 + Math.min(0.4, (vis.horizonFrac - 0.5) * 0.8) + pulseA * 0.18;
          const hw = 2 + pulseA * 1.2;
          drawSegmentedRing(horizon, cx, cy, hr, col, { segments: 48, width: hw, alphaAt: () => a });
          // ticks
          for (let i = 0; i < 48; i++) {
            const ang = (i / 48) * Math.PI * 2;
            const x1 = cx + Math.cos(ang) * hr;
            const y1 = cy + Math.sin(ang) * hr;
            const x2 = cx + Math.cos(ang) * (hr - 4);
            const y2 = cy + Math.sin(ang) * (hr - 4);
            horizon.moveTo(x1, y1).lineTo(x2, y2).stroke({ width: 1, color: col, alpha: a * 0.6 });
          }
        }

        // ── ghost target wireframe for the current item (Tier-2; off in Tier-1) ──
        ghostG.clear();
        const cur = runStore.current;
        let activeX = cx;
        let activeY = cy;
        if (cur && !tierOne) {
          const pp = ppos.get(cur.key);
          if (pp) {
            const px = pp.x;
            const py = pp.y;
            activeX = px;
            activeY = py;
            ghostG.circle(px, py, 12).stroke({ width: 1, color: C.ghostBrass, alpha: 0.5 });
            const crit = cur.ghost?.criteria ?? [];
            const n = Math.max(crit.length, 4);
            for (let i = 0; i < n; i++) {
              const ang = (i / n) * Math.PI * 2 + (reduced ? 0 : vis.t * 0.2);
              const met = crit[i]?.met;
              const sr = 8;
              const sx = px + Math.cos(ang) * sr;
              const sy = py + Math.sin(ang) * sr;
              ghostG
                .circle(sx, sy, met ? 1.8 : 1)
                .fill({ color: met ? C.green : C.ghostBrass, alpha: met ? 0.95 : 0.4 });
            }
          }
        }

        // ── audit signal (wave U2 Task 3) ─────────────────────────────────────
        // The lighthouse tower + sweeping beam used to be the only way to see that
        // a claimed-green item was being audited. It's retired: the SAME state
        // (runStore.auditTargetKey — claimed-green, not yet certified) now drives a
        // "verifying…" pulse on that body's on-canvas label (ObservatoryLabels) and
        // next to the HUD trust chip instead. A refute still auto-opens VerdictPanel
        // (runStore.latestVerdict, unrelated to this signal) and still gets its own
        // crimson flush ring in the planet-draw loop below (refuteFx).
        const auditKey = runStore.auditTargetKey;

        // ── M2.6: ring-buffer trail on the current body only — off under reduced
        //   motion AND in Tier-1/Ambient (tierOne === uiStore.ambient in this store).
        //   Reset immediately if the current body itself changes (a different item
        //   became current) so the trail never jump-cuts between two bodies' paths. ──
        trailG.clear();
        const curPp = cur ? ppos.get(cur.key) : undefined;
        if (!reduced && !tierOne && cur && curPp) {
          if (trailKey !== cur.key) {
            trailBuf.length = 0;
            trailKey = cur.key;
          }
          trailBuf.push({ x: curPp.x, y: curPp.y });
          while (trailBuf.length > TRAIL_N) trailBuf.shift();
        } else {
          trailBuf.length = 0;
          trailKey = null;
        }
        if (trailBuf.length > 1 && curPp) {
          const n = trailBuf.length;
          const trailColor = planetPair(curPp.o).core;
          for (let i = 1; i < n; i++) {
            const tt = i / n;
            const p0 = trailBuf[i - 1];
            const p1 = trailBuf[i];
            trailG
              .moveTo(p0.x, p0.y)
              .lineTo(p1.x, p1.y)
              .stroke({ width: Math.max(0.4, 2.4 * tt), color: trailColor, alpha: tt * tt * 0.4 });
          }
        }

        // ── particle stream: emit from the active region → the star ──────────
        // emission ∝ burn; frozen under reduced-motion. cache-teal fraction is
        // a share of motes drawn from the recycled-fuel reservoir.
        if (!reduced && running && vis.burn > 0.001) {
          const teal = runStore.cacheFrac;
          // Tier-1/Planetarium cuts the particle budget hard (plan §7): a thin
          // trickle into the star, not the full Observatory stream.
          emitAccrue += vis.burn * (tierOne ? 0.7 : 4.2) * (dt / 16.67); // up to ~4 motes/frame@60fps at full burn
          while (emitAccrue >= 1) {
            emitAccrue -= 1;
            const jx = activeX + (Math.random() - 0.5) * 18;
            const jy = activeY + (Math.random() - 0.5) * 18;
            spawnMote(jx, jy, Math.random() < teal);
          }
        } else {
          emitAccrue = 0;
        }
        // advance motes toward the star centre
        for (const m of pool) {
          if (!m.active) continue;
          if (reduced) {
            // freeze in place but keep them visible (steady state, no motion)
            m.sprite.x = m.x;
            m.sprite.y = m.y;
            continue;
          }
          m.prog += m.speed * (0.6 + vis.burn) * (dt / 16.67);
          if (m.prog >= 1) {
            m.active = false;
            m.sprite.alpha = 0;
            continue;
          }
          // ease toward star with a slight inward curl
          const e = m.prog * m.prog * (3 - 2 * m.prog); // smoothstep
          m.x = lerp(m.sx, cx, e);
          m.y = lerp(m.sy, cy, e);
          m.sprite.x = m.x;
          m.sprite.y = m.y;
          m.sprite.scaleX = m.sprite.scaleY = m.size * (1 - e * 0.4);
          m.sprite.tint = m.teal ? C.cacheTeal : vis.emitColor;
          m.sprite.alpha = (m.prog < 0.15 ? m.prog / 0.15 : 1 - (m.prog - 0.15) / 0.85) * 0.85;
        }

        // ── corona/halo (M2.1: glow-texture sprite, replaces stacked alpha circles) +
        //    star (bloom scaled by burn) ──
        const coronaR = vis.starR * pulse;
        // M5.3 (plan §6 "rest-states keep their scene hue with calmer, state-appropriate
        // glow"): corona brightness now varies per silhouette instead of one flat "1 unless
        // frost/failed" — done/paused read serene/banked, handoff stays visible-but-not-
        // maximal amber, quota-frost stays cold/suppressed, failed-dark reads near-dead (red
        // lives only in the fracture accents drawn in the star silhouette below, not the glow).
        const coronaAlpha =
          rest === 'failed-dark'
            ? 0.08
            : rest === 'quota-frost'
              ? 0.4
              : rest === 'certified-done'
                ? 0.55
                : rest === 'stopped-ember'
                  ? 0.35
                  : rest === 'handoff-beacon'
                    ? 0.8
                    : 1; // running / idle — the star BURNS
        // M2.2: the star's OWN disc fill is only lightly muted (it's the one deliberate
        // "light source" identity element — plan acceptance: "reads as a light source in
        // an atmosphere"); the corona/glow sprite tint takes the fuller mute (M2.2 "glow
        // tint always = base tier"), safe since the glow texture itself tops out ~12% alpha.
        // M5.3: both MUTE_* constants pulled back (see their declaration) so the new
        // jewel-tone scene hues actually survive this mute instead of reading gray/dull.
        const starFill = muteColor(vis.starColor, muteTarget, MUTE_FILL); // WS-R: ink target on light
        const glowTint = muteColor(vis.starColor, muteTarget, MUTE_GLOW); // WS-R: keeps the corona wash saturated on light
        if (coronaAlpha > 0.01) {
          coronaGlow.visible = true;
          // M5.3: raised from (3 + burn*3) — the owner's core complaint was "dull"; a bigger,
          // more decisive halo scale is the single biggest lever on that.
          const haloR = coronaR * (3.2 + Math.min(1, vis.burn) * 3.6); // ~3.2–6.8× core radius
          sizeGlowSprite(coronaGlow, burnGlowTex, cx, cy, haloR);
          coronaGlow.tint = glowTint;
          // the glow texture's own stops (peak 0.12) already ARE the intended falloff
          // alpha — modulate lightly by burn/suppression rather than crushing it further.
          coronaGlow.alpha = coronaAlpha * (0.8 + Math.min(1, vis.burn) * 0.2);
        } else {
          coronaGlow.visible = false;
        }
        // M5.3: the WIDE gold bleed halo — running only (no rest-state settled), so it never
        // appears on a parked/failed/quota star; screen-blended and reaching well past the
        // orbits (plan §6 "the star's glow should visibly bleed into the scene … reaching past
        // the orbits when running"). Uses vis.coronaColor (gold while burning) muted the same
        // amount as the tight corona so the two layers read as one light source at two radii.
        const isBurningCorona = running && !rest;
        if (isBurningCorona) {
          coronaGlowWide.visible = true;
          const wideR = coronaR * (5.5 + Math.min(1, vis.burn) * 3.5) * coronaShimmer;
          sizeGlowSprite(coronaGlowWide, wideGlowTex, cx, cy, wideR);
          coronaGlowWide.tint = muteColor(vis.coronaColor, muteTarget, MUTE_GLOW_WIDE); // WS-R: ink target on light
          coronaGlowWide.alpha = (0.5 + Math.min(1, vis.burn) * 0.3) * coronaShimmer;
        } else {
          coronaGlowWide.visible = false;
        }

        // ── M2.1: 4-point cross-flare, central star ONLY while actively running ──
        starFlareG.clear();
        if (running && !tierOne) {
          const flareColor = lerpColor(vis.starColor, C.starlight, 0.5);
          const flareLen = coronaR * 2.6;
          const arms: [number, number][] = [
            [1, 0],
            [-1, 0],
            [0, 1],
            [0, -1],
          ];
          const segs = 5;
          for (const [dx, dy] of arms) {
            for (let i = 0; i < segs; i++) {
              const t0 = i / segs;
              const t1 = (i + 1) / segs;
              const fade = (1 - t0) * 0.15 * pulse;
              starFlareG
                .moveTo(cx + dx * flareLen * t0, cy + dy * flareLen * t0)
                .lineTo(cx + dx * flareLen * t1, cy + dy * flareLen * t1)
                .stroke({ width: 1, color: flareColor, alpha: Math.max(0, fade) });
            }
          }
        }

        // ── transient crimson SUPERNOVA on a crash stop (E? error) ──
        supernovaG.clear();
        if (supernova > 0.01) {
          const sr = coronaR + (1 - supernova) * Math.min(w, h) * 0.55;
          supernovaG.circle(cx, cy, sr).stroke({ width: 2 + supernova * 4, color: C.crimson, alpha: supernova * 0.7 });
          supernovaG.circle(cx, cy, sr * 0.7).stroke({ width: 1, color: C.crimson, alpha: supernova * 0.4 });
        }

        // ── the star core: FIVE mutually-distinct rest silhouettes ──
        // certified-done = sealed near-white disc + brass seal · stopped-ember = banked
        // warm dome · quota-frost = cold crystal spikes · handoff-beacon = rotating
        // two-armed amber wedge (M4.5: needs-you = amber only, no red) · failed-dark = a dim
        // crimson disc, no glow, cut by a jagged fracture. Each differs by SHAPE + motion +
        // color (greyscale separable per §F — M4.5: now trivially so, since color itself is
        // gone from every silhouette except the two genuine alerts, handoff/amber and failed/
        // crimson). restForm eases the transition so it "sets" at rest.
        // M2.2: the big disc FILLS below use `starFill` (lightly muted); small accents (the
        // frost/ember core dot, brass seal ticks, crack lines) keep the full-saturation
        // hue — the same two-tier split as the planets, applied to the star's own
        // per-silhouette identity color instead of the fixed 5-slot status vocabulary.
        starG.clear();
        const rf = vis.restForm;
        if (rest === 'failed-dark') {
          // crashed: a DIM crimson disc — the corona above is already suppressed
          // (coronaAlpha), so there is no radiant glow, only the wound. A fixed
          // jagged fracture cuts across the disc (a glass-crack read, not
          // decoration) with a dark under-stroke for depth + a short branch.
          // Motion is a slow, faint flicker (a guttering ember, never a blink);
          // reduced-motion holds it perfectly still. Triple-coded: shape
          // (cracked disc) + motion (flicker/still) + color (crimson).
          const flicker = reduced ? 1 : 0.82 + Math.sin(vis.t * 0.55) * 0.18;
          starG
            .circle(cx, cy, coronaR)
            .fill({ color: starFill, alpha: (0.24 + rf * 0.1) * flicker });
          starG.circle(cx, cy, coronaR * 0.55).fill({ color: C.void, alpha: 0.5 * rf });
          // the fracture: a fixed jagged crack through the disc, plus a short branch
          const crack: [number, number][] = [
            [-0.05, -0.95],
            [0.22, -0.5],
            [-0.15, -0.15],
            [0.3, 0.1],
            [-0.1, 0.45],
            [0.18, 0.95],
          ];
          starG.moveTo(cx + crack[0][0] * coronaR, cy + crack[0][1] * coronaR);
          for (let i = 1; i < crack.length; i++) {
            starG.lineTo(cx + crack[i][0] * coronaR, cy + crack[i][1] * coronaR);
          }
          starG.stroke({ width: 2.5, color: C.void, alpha: 0.85 * rf });
          starG.moveTo(cx + crack[0][0] * coronaR, cy + crack[0][1] * coronaR);
          for (let i = 1; i < crack.length; i++) {
            starG.lineTo(cx + crack[i][0] * coronaR, cy + crack[i][1] * coronaR);
          }
          starG.stroke({ width: 0.9, color: C.crimson, alpha: 0.55 * rf });
          starG.moveTo(cx + crack[2][0] * coronaR, cy + crack[2][1] * coronaR);
          starG.lineTo(cx - 0.6 * coronaR, cy + 0.1 * coronaR);
          starG.stroke({ width: 1.6, color: C.void, alpha: 0.75 * rf });
        } else if (rest === 'quota-frost') {
          // cold crystal: sharp 6-point star, no warm core, faint frost shimmer
          const spikes = 6;
          const rr0 = coronaR;
          starG.moveTo(cx + rr0, cy);
          for (let i = 1; i <= spikes * 2; i++) {
            const ang = (i / (spikes * 2)) * Math.PI * 2;
            const rr = i % 2 === 0 ? rr0 : rr0 * 0.5;
            starG.lineTo(cx + Math.cos(ang) * rr, cy + Math.sin(ang) * rr);
          }
          starG.fill({ color: starFill, alpha: 0.9 });
          starG.circle(cx, cy, rr0 * 0.35).fill({ color: C.frost, alpha: 0.5 });
        } else if (rest === 'stopped-ember') {
          // banked ember: a warm DOME (flat-bottomed half-disc) — coasted, parked.
          starG.circle(cx, cy, coronaR).fill({ color: starFill, alpha: 0.5 + rf * 0.45 });
          // the "bank" line: a horizontal ember bar across the dome
          starG
            .rect(cx - coronaR, cy + coronaR * 0.15, coronaR * 2, coronaR * 0.5)
            .fill({ color: C.void, alpha: 0.55 * rf });
          starG.circle(cx, cy - coronaR * 0.15, coronaR * 0.5).fill({ color: C.ember, alpha: 0.55 + rf * 0.3 });
          // a few slow embers rising (frozen if reduced) — banked, not extinguished
          if (!reduced && rf > 0.4) {
            for (let i = 0; i < 3; i++) {
              const ph = (vis.t * 0.4 + i * 0.6) % 1;
              const ex = cx + (i - 1) * coronaR * 0.4;
              const ey = cy - coronaR * 0.2 - ph * coronaR * 1.6;
              starG.circle(ex, ey, 1.6 * (1 - ph)).fill({ color: C.ember, alpha: (1 - ph) * 0.6 });
            }
          }
        } else if (rest === 'handoff-beacon') {
          // distress beacon: a rotating two-armed wedge sweeping like a siren. M4.5: handoff is
          // "needs you", the amber/warn bucket — NOT a crash — so both arms are now the same
          // amber family (previously one arm was crimson); the two-armed silhouette stays
          // motion-distinct via alpha (bright arm / dim echo arm) rather than a second hue.
          starG.circle(cx, cy, coronaR).fill({ color: starFill, alpha: 0.92 });
          const sweep = reduced ? 0 : vis.t * 1.1; // slow rotation (urgency=slowing not blinking)
          const wedge = 0.6;
          const beamR = coronaR * 3.4;
          starG.moveTo(cx, cy);
          starG.arc(cx, cy, beamR, sweep - wedge / 2, sweep + wedge / 2);
          starG.closePath();
          starG.fill({ color: muteColor(C.amber, muteTarget, MUTE_FILL), alpha: 0.18 }); // WS-R: ink target on light
          // opposite (echo) arm — same amber family, dimmer, keeps the two-armed read
          starG.moveTo(cx, cy);
          starG.arc(cx, cy, beamR, sweep + Math.PI - wedge / 2, sweep + Math.PI + wedge / 2);
          starG.closePath();
          starG.fill({ color: muteColor(C.amber, muteTarget, MUTE_FILL), alpha: 0.08 }); // WS-R: ink target on light
          starG.circle(cx, cy, coronaR * 0.55).fill({ color: C.amber, alpha: 0.7 });
        } else if (rest === 'certified-done') {
          // sealed: calm green disc with a brass certification seal (concentric
          // brass rings + a notch) — steady, NOT animated (it's done).
          starG.circle(cx, cy, coronaR).fill({ color: starFill, alpha: 0.96 });
          starG.circle(cx, cy, coronaR * 0.62).fill({ color: C.starlight, alpha: 0.45 });
          starG.circle(cx, cy, coronaR + 5).stroke({ width: 2, color: C.brass, alpha: 0.8 * rf });
          starG.circle(cx, cy, coronaR + 9).stroke({ width: 1, color: C.brass, alpha: 0.5 * rf });
          // seal notches (brass ticks around the certification ring)
          for (let i = 0; i < 8; i++) {
            const ang = (i / 8) * Math.PI * 2;
            const r1 = coronaR + 5;
            const r2 = coronaR + 9;
            starG
              .moveTo(cx + Math.cos(ang) * r1, cy + Math.sin(ang) * r1)
              .lineTo(cx + Math.cos(ang) * r2, cy + Math.sin(ang) * r2)
              .stroke({ width: 1, color: C.brass, alpha: 0.7 * rf });
          }
        } else {
          // running / idle: live star, hot inner ring tinted by the active model
          starG.circle(cx, cy, coronaR).fill({ color: starFill, alpha: 0.98 });
          starG.circle(cx, cy, coronaR * 0.6).fill({ color: C.starlight, alpha: 0.5 });
          if (vis.burn > 0.05) {
            starG
              .circle(cx, cy, coronaR * 0.85)
              .stroke({ width: 1.5, color: vis.emitColor, alpha: 0.25 + vis.burn * 0.4 });
          }
        }

        // ── brake-ring: "stopping at next <mode>" — coasting to a tooth (Tier-2) ──
        brakeG.clear();
        if (!tierOne && s.run.stopPending && running) {
          // a tightening brake-shoe arc around the star (urgency = tightening). M4.5: a
          // requested/cooperative stop is a routine operational signal, not an alert — gray
          // (em-hi), not the warm ember tint it used to borrow.
          const br = coronaR + 16 + (reduced ? 0 : Math.sin(vis.t * 1.4) * 2);
          const segs = 6;
          for (let i = 0; i < segs; i++) {
            const a0 = (i / segs) * Math.PI * 2 + (reduced ? 0 : vis.t * 0.3);
            brakeG
              .arc(cx, cy, br, a0, a0 + 0.5)
              .stroke({ width: 2.5, color: emC.hi, alpha: 0.75 });
          }
        }

        // ── planets ── (positions already eased into `ppos`)
        // Tier-1/Planetarium drops the planets entirely (plan §7): the star, the
        // cost-horizon, the rest-state silhouette + beacon and the quota-night are
        // the whole picture. M2.7: pooled per-key visuals — hide (don't destroy) when
        // Tier-1 hides all planets; prune only keys no longer in runStore.orbits at all.
        const activeKeys = new Set(runStore.orbits.map((o) => o.key));
        for (const [key, v] of planetPool) {
          if (!activeKeys.has(key)) {
            v.container.destroy({ children: true });
            planetPool.delete(key);
          }
        }
        lastPpos.clear();
        if (tierOne) {
          for (const v of planetPool.values()) v.container.visible = false;
        } else {
          for (const o of runStore.orbits) {
            const pp = ppos.get(o.key);
            if (!pp) continue;
            const px = pp.x;
            const py = pp.y;
            lastPpos.set(o.key, { x: px, y: py, r: pp.r });
            const a = planetAngle.get(o.key) ?? o.angle;
            const r = pp.r;
            const pv = getPlanetVisual(o.key);
            pv.container.visible = true;
            const pair = planetPair(o);
            const pr = 5 + (o.merged ? 1.5 : 0);
            const snap = snapFx.get(o.key) ?? 0; // rollback time-shimmer 0..1
            const seal = sealFlash.get(o.key) ?? 0; // certification chime 0..1
            const refute = refuteFx.get(o.key) ?? 0;

            // M2.1: true glow only on the star, current/selected body, and alert states
            // (blocked/failed/claimed-green) — every other planet is a flat fill.
            const isCurrent = cur?.key === o.key;
            const isSelected = o.key === runStore.selectedItem;
            const isAlert = o.status === 'blocked' || o.status === 'failed' || o.claimedGreen;
            if (isCurrent || isSelected || isAlert) {
              pv.glow.visible = true;
              sizeGlowSprite(pv.glow, glowTex, px, py, pr * 4.2);
              pv.glow.tint = pair.base;
              pv.glow.alpha = isAlert && !isCurrent && !isSelected ? 0.55 : 0.8;
            } else {
              pv.glow.visible = false;
            }

            // ── disc (normal blend): fill, rims, notches ──
            const g = pv.disc;
            g.clear();
            g.circle(px, py, pr).fill({ color: pair.base, alpha: 0.95 });

            // selection ring — the planet whose VerdictPanel/dossier is open (mouse
            // click or the keyboard work-item list both set runStore.selectedItem). M4.5:
            // white/gray interaction only (owner: "white double-ring focus… interaction =
            // white/gray only") — was the retired cyan accent.
            if (isSelected) {
              g.circle(px, py, pr + 6).stroke({ width: 1.5, color: emC.hi, alpha: 0.9 });
            }

            // hover highlight — a subtle ring on the planet under the pointer (kept
            // dimmer than the selection ring so the two never read as the same).
            if (o.key === hoveredKey && !isSelected) {
              g.circle(px, py, pr + 5).stroke({ width: 1, color: emC.mid, alpha: 0.4 });
            }

            // A2: CERTIFIED green (sealed) — a solid brass certification ring; calm.
            if (o.certified) {
              g.circle(px, py, pr + 3).stroke({ width: 1.4, color: C.brass, alpha: 0.9 });
            } else if (o.merged) {
              g.circle(px, py, pr + 3).stroke({ width: 1.2, color: C.brass, alpha: 0.7 });
            } else if (o.claimedGreen) {
              // A2: CLAIMED green (asserted, not yet audited) — anxious dashed pulse,
              // NO brass seal. It pulses until the verifier certifies or refutes it
              // (the "verifying…" label + HUD trust chip carry the same signal in text).
              const ap = reduced ? 0.4 : 0.25 + Math.abs(Math.sin(vis.t * 3)) * 0.45;
              const wob = reduced ? 0 : Math.sin(vis.t * 3) * 0.8;
              g.circle(px, py, pr + 2.5 + wob).stroke({ width: 1, color: C.status.ok.core, alpha: ap });
            }

            // crimson strike-notches on the ring (one per rollback), per budget
            const budget = Math.max(o.strikeBudget, o.strikes);
            for (let i = 0; i < Math.min(o.strikes, Math.max(budget, 3)); i++) {
              const na = a - 0.18 + (i / Math.max(1, budget - 1 || 1)) * 0.36;
              const nx = px + Math.cos(na + Math.PI / 2) * (pr + 6);
              const ny = py + Math.sin(na + Math.PI / 2) * (pr + 6);
              g.moveTo(nx, ny)
                .lineTo(nx + Math.cos(na) * 3, ny + Math.sin(na) * 3)
                .stroke({ width: 1.4, color: C.status.err.core, alpha: 0.9 });
            }

            // ── fx (add blend): transient blooms only — seal chime, rollback
            //    snapback, refute drain (plan §M2.1 "'add' reserved for … transients") ──
            const fxG = pv.fx;
            fxG.clear();
            if (seal > 0.02) {
              // M4.5: success is pure monochrome (owner: "bright white + seal glyph") — em-hi,
              // not the brass identity tint the static certification ring keeps (that one's
              // correct as-is; this is the one-shot chime bloom, a different element).
              const bloomR = pr + 3 + (1 - seal) * 14;
              fxG.circle(px, py, bloomR).stroke({ width: 1.5, color: emC.hi, alpha: seal * 0.85 });
            }
            if (snap > 0.02) {
              // M4.5: rollback snapback flash → white/gray (was the retired cyan accent).
              const ringR = pr + 4 + (1 - snap) * 10;
              fxG.circle(px, py, ringR).stroke({ width: 1.5, color: emC.hi, alpha: snap * 0.8 });
              fxG.circle(px, py, ringR + 4).stroke({ width: 1, color: emC.hi, alpha: snap * 0.4 });
            }
            if (refute > 0.02) {
              fxG.circle(px, py, pr + 1).stroke({ width: 1.5, color: C.status.err.core, alpha: refute });
            }
          }
        }

        // ── Rewind-mode time-shimmer frame (plan §3) ─────────────────────────
        // M4.5: a neutral cool-gray (frost) frame + faint horizontal scan-lines, instead of
        // the retired plasma-cyan accent — Rewind is a mode change, not an alert, so it stays
        // in the monochrome vocabulary (frost reads "outside normal time" without spending a
        // hue). Frames the whole orrery while you scrub time; eased on/off so entering Rewind
        // reads as "stepping into the timeline". Steady-state safe: the scan drift is
        // suppressed under reduced-motion (only the frame shows).
        vis.rewind = lerp(vis.rewind, uiStore.rewind ? 1 : 0, kdt(0.08, dt));
        shimmerG.clear();
        if (vis.rewind > 0.01) {
          const a = vis.rewind;
          // frost border frame
          const inset = 6;
          shimmerG
            .rect(inset, inset, w - inset * 2, h - inset * 2)
            .stroke({ width: 1.5, color: C.frost, alpha: 0.5 * a });
          shimmerG
            .rect(inset + 4, inset + 4, w - (inset + 4) * 2, h - (inset + 4) * 2)
            .stroke({ width: 1, color: C.frost, alpha: 0.2 * a });
          // soft top/bottom frost vignette wash
          shimmerG.rect(0, 0, w, h * 0.12).fill({ color: C.frost, alpha: 0.05 * a });
          shimmerG.rect(0, h * 0.88, w, h * 0.12).fill({ color: C.frost, alpha: 0.05 * a });
          // drifting scan-lines (the "time-shimmer"); frozen under reduced-motion
          const drift = reduced ? 0 : (vis.t * 30) % 26;
          for (let y = -26 + drift; y < h; y += 26) {
            shimmerG.moveTo(0, y).lineTo(w, y).stroke({ width: 1, color: C.frost, alpha: 0.04 * a });
          }
        }

        // ── publish the THROTTLED label snapshot (every ~6th frame) ──────────
        // Annotations don't need 60fps; updating reactive $state every frame
        // would thrash. Positions are already eased, so a 6-frame cadence reads
        // smooth (the overlay also eases its own CSS transitions).
        labelFrame = (labelFrame + 1) % 6;
        if (labelFrame === 0) {
          const hf = runStore.horizonFrac;
          // declutter (Task 3): tally bodies per ring so a dense ring (>12) only labels the
          // current body + failed/unverified ones (the bodies that need attention), not every
          // planet — a wall of tiny overlapping labels is worse than no labels.
          const ringCounts = new Map<number, number>();
          for (const { o } of ppos.values()) {
            ringCounts.set(o.ringIndex, (ringCounts.get(o.ringIndex) ?? 0) + 1);
          }
          // Tier-1 (Ambient) draws no planets at all (see the planet loop above) — no
          // labels either, or they'd float over nothing (mirrors the existing !uiStore.ambient
          // gate around <ObservatoryLabels/> below).
          const bodies: BodyLabel[] = [];
          if (!tierOne && uiStore.isPhone) {
            // wave U2 Task 4: phone Observatory now draws real planets, but keeps the label
            // layer to just the current body — a small screen can't carry a full legend too.
            if (cur) {
              const pp = ppos.get(cur.key);
              if (pp) {
                const o = pp.o;
                const trust: BodyLabel['trust'] = o.certified ? 'verified' : o.claimedGreen ? 'unverified' : null;
                bodies.push({
                  key: cur.key,
                  x: pp.x,
                  y: pp.y,
                  status: o.status,
                  trust,
                  current: true,
                  auditing: cur.key === auditKey,
                });
              }
            }
          } else if (!tierOne) {
            for (const [key, pp] of ppos) {
              const o = pp.o;
              const isCurrent = cur?.key === key;
              const dense = (ringCounts.get(o.ringIndex) ?? 0) > 12;
              const flagged = o.status === 'failed' || o.claimedGreen;
              if (dense && !isCurrent && !flagged) continue;
              const trust: BodyLabel['trust'] = o.certified ? 'verified' : o.claimedGreen ? 'unverified' : null;
              bodies.push({
                key,
                x: pp.x,
                y: pp.y,
                status: o.status,
                trust,
                current: isCurrent,
                auditing: key === auditKey,
              });
            }
          }
          labels = {
            cumUsd: s.run.cumUsd,
            ratePerMin: s.run.status === 'running' ? s.cost.ratePerMin : 0,
            star: { x: cx, y: cy + vis.starR * pulse },
            bodies,
            horizonPct: hf >= 0.5 ? Math.round(hf * 100) : null,
          };
        }

        raf = requestAnimationFrame(tick);
      };
      raf = requestAnimationFrame(tick);
    })();

    return () => {
      destroyed = true;
      if (raf) cancelAnimationFrame(raf);
      cleanupResize?.();
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

<!-- outer box owns layout/background; the inner .ofield is the SOLE parent of the
     Pixi-appended <canvas>, so the canvas and the Svelte label overlay never fight
     over one element's children. -->
<div class="observatory">
  <div bind:this={host} class="ofield" aria-hidden="true"></div>
  <!-- the on-canvas labels are the Observatory's legibility layer; in ambient
       Planetarium the PlanetariumOverlay owns the load-bearing numbers, so we
       don't double them (and avoid a label floating where a Tier-1 planet isn't). -->
  {#if !uiStore.ambient}
    <ObservatoryLabels {labels} reduced={uiStore.reducedMotion} />
  {/if}
</div>

<style>
  /* wave U2 Task 1: the canvas is now a real grid cell (the System dock's "center"
     area), not a full-viewport background panels float over — `position: relative`
     (not `absolute; inset: 0`) so it fills exactly the box the grid gives it and
     genuinely resizes ("breathes") as the left/right rails collapse or expand.
     `.ofield`'s `absolute; inset: 0` below still anchors to THIS box. */
  .observatory {
    position: relative;
    width: 100%;
    height: 100%;
    min-width: 0;
    min-height: 0;
    background: var(--void);
  }
  .ofield {
    position: absolute;
    inset: 0;
  }
  .observatory :global(canvas) {
    display: block;
  }
</style>
