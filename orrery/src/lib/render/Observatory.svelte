<script lang="ts">
  // The PixiJS scene — CLIENT-ONLY. Pixi is dynamically imported inside onMount
  // and guarded with SvelteKit's `browser`, so it never runs at build/SSR/
  // prerender time. Data mutates the store; a single rAF loop eases visual
  // props toward store targets (the tiny data rate is decoupled from 60fps).
  //
  // Renders (A1 base + A2 living motion):
  //   Void background · central Star (radius←cumUsd, bloom/pulse←cost.ratePerMin,
  //   color←run.status) · a pooled PixiJS particle stream flowing from the active
  //   region INTO the star (emission ∝ cost rate; cache-hits tinted cache-teal) ·
  //   one ring per group · a planet per item (eased angle + radius) · the frozen
  //   ghost-target wireframe · the cost-horizon ring animating 50/80/100 ·
  //   dusk (warm ember bank) vs polar-night (frost desaturation) quota transition.
  //
  // §F restraint: prefers-reduced-motion freezes ALL decorative motion (orbits,
  // pulse, particles, spin) — only the eased state targets cross-fade. Steady
  // state is never animated; urgency = tightening/slowing, not blinking.

  import { onMount } from 'svelte';
  import { browser } from '$app/environment';
  import { runStore, STAR_R0, STAR_K } from '../stores/run.svelte';

  let host: HTMLDivElement;

  // palette (kept in sync with tokens.css; Pixi needs numbers)
  const C = {
    void: 0x070912,
    brass: 0xc9a24b,
    starlight: 0xeaf0ff,
    ember: 0xff7a3c,
    cyan: 0x46e0ff,
    amber: 0xffc24b,
    green: 0x5bf09b,
    crimson: 0xff3b5c,
    indigo: 0x1a1740,
    auditor: 0xf4f8ff,
    ghostBrass: 0xc9a24b,
    cacheTeal: 0x2fd9c9,
    horizonRose: 0xff6b7e,
    frost: 0x9fb6ff,
    haiku: 0xff6a4d,
    sonnet: 0xc9a24b,
    opus: 0x9fd0ff,
  };

  function starColor(status: string, restState: string | null): number {
    if (restState === 'quota-frost') return C.frost;
    if (restState === 'handoff-beacon') return C.crimson;
    if (restState === 'certified-done') return C.green;
    if (restState === 'stopped-ember') return C.ember;
    if (status === 'error') return C.crimson;
    if (status === 'running') return C.amber;
    return C.starlight;
  }
  function modelColor(m: string): number {
    if (m === 'haiku') return C.haiku;
    if (m === 'opus') return C.opus;
    return C.sonnet;
  }
  function ringColor(status: string): number {
    if (status === 'done') return C.green;
    if (status === 'in-progress') return C.brass;
    return 0x3a3f5c; // backlog — dim indigo-grey
  }
  function planetColor(o: { status: string; certified: boolean; merged: boolean }): number {
    if (o.certified || o.merged) return C.green;
    switch (o.status) {
      case 'done':
        return C.green;
      case 'review':
        return C.amber;
      case 'in-progress':
        return C.cyan;
      case 'blocked':
      case 'failed':
        return C.crimson;
      case 'ready':
        return C.brass;
      default:
        return 0x556089; // backlog
    }
  }

  onMount(() => {
    if (!browser) return;
    let destroyed = false;
    let app: any = null;
    let raf = 0;
    let cleanupResize: (() => void) | null = null;

    // honor prefers-reduced-motion as a first-class mode (§F)
    const rmQuery = window.matchMedia('(prefers-reduced-motion: reduce)');
    let reduced = rmQuery.matches;
    const onRm = () => (reduced = rmQuery.matches);
    rmQuery.addEventListener?.('change', onRm);

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

      // ── scene graph ─────────────────────────────────────────────────────
      const world = new PIXI.Container();
      app.stage.addChild(world);

      const starfield = new PIXI.Graphics(); // backdrop dust
      const nightG = new PIXI.Graphics(); // dusk/polar-night wash + frost creep
      const horizon = new PIXI.Graphics(); // cost-horizon ring
      const ringsG = new PIXI.Graphics(); // group rings
      const ghostG = new PIXI.Graphics(); // frozen target wireframe
      const corona = new PIXI.Graphics(); // star glow / bloom
      const starG = new PIXI.Graphics(); // star core
      const planetsC = new PIXI.Container(); // planets

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

      world.addChild(
        starfield,
        nightG,
        horizon,
        ringsG,
        ghostG,
        particlesC,
        corona,
        starG,
        planetsC,
      );

      // static starfield dust
      function drawStarfield(w: number, h: number) {
        starfield.clear();
        let seed = 1337;
        const rnd = () => {
          seed = (seed * 1103515245 + 12345) & 0x7fffffff;
          return seed / 0x7fffffff;
        };
        for (let i = 0; i < 220; i++) {
          const x = rnd() * w;
          const y = rnd() * h;
          const r = rnd() * 1.1 + 0.2;
          const a = rnd() * 0.5 + 0.05;
          starfield.circle(x, y, r).fill({ color: C.starlight, alpha: a });
        }
      }

      // eased visual state (interpolated toward store targets each frame)
      const vis = {
        starR: STAR_R0,
        starColor: C.starlight,
        emitColor: C.sonnet,
        horizonFrac: 0,
        burn: 0,
        night: 0, // 0 day → 1 night
        nightHue: 0, // 0 dusk(ember) → 1 polar(frost)
        t: 0,
      };
      // per-planet eased angle (so they rotate smoothly)
      const planetAngle = new Map<string, number>();
      let emitAccrue = 0; // fractional particle emission carry

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

      let w = host.clientWidth || 800;
      let h = host.clientHeight || 600;
      drawStarfield(w, h);

      const onResize = () => {
        w = host.clientWidth || 800;
        h = host.clientHeight || 600;
        drawStarfield(w, h);
      };
      const ro = new ResizeObserver(onResize);
      ro.observe(host);
      cleanupResize = () => ro.disconnect();

      // ── render loop ──────────────────────────────────────────────────────
      const PXPER = 1; // geometry units → px (rings already in px-ish units)
      const tick = () => {
        if (destroyed) return;
        const s = runStore.state;
        const cx = w / 2;
        const cy = h / 2;
        if (!reduced) vis.t += 0.016;
        const running = s.run.status === 'running';

        // ── eased global signals ──
        const targetR = STAR_R0 + STAR_K * Math.log1p(Math.max(0, s.run.cumUsd));
        vis.starR = lerp(vis.starR, targetR, 0.08);
        const targetColor = starColor(s.run.status, s.run.restState);
        vis.starColor = lerpColor(vis.starColor, targetColor, 0.08);
        vis.emitColor = lerpColor(vis.emitColor, modelColor(runStore.model), 0.06);
        vis.burn = lerp(vis.burn, running ? runStore.burn : 0, 0.05);

        // night transition (dusk ember vs polar frost), keyed off resetType
        const night = runStore.nightType;
        vis.night = lerp(vis.night, night ? 1 : 0, 0.04);
        vis.nightHue = lerp(vis.nightHue, night === 'polar' ? 1 : 0, 0.03);

        // breathing pulse scaled by burn (telemetry liveness; frozen if reduced)
        const pulseAmt = 0.03 + vis.burn * 0.12;
        const pulse = running && !reduced ? 1 + Math.sin(vis.t * (2.2 + vis.burn * 2.4)) * pulseAmt : 1;

        // ── dusk / polar-night wash (behind everything but the dust) ──
        nightG.clear();
        if (vis.night > 0.01) {
          const dusk = lerpColor(0x2a1606, 0x0c1430, vis.nightHue); // ember bank → indigo
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

        // ── rings (one per group, colored by status) ──
        ringsG.clear();
        for (const ring of runStore.rings) {
          const r = ring.radius * PXPER;
          ringsG
            .circle(cx, cy, r)
            .stroke({ width: ring.status === 'in-progress' ? 1.6 : 1, color: ringColor(ring.status), alpha: 0.5 });
        }

        // ── cost horizon (Roche ring) — invisible <50%, tightens toward star ──
        horizon.clear();
        const frac = runStore.horizonFrac;
        vis.horizonFrac = lerp(vis.horizonFrac, frac, 0.06);
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
          horizon.circle(cx, cy, hr).stroke({ width: 2 + pulseA * 1.2, color: col, alpha: a });
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

        // ── ghost target wireframe for the current item ──
        ghostG.clear();
        const cur = runStore.current;
        let activeX = cx;
        let activeY = cy;
        if (cur) {
          const o = runStore.orbits.find((x) => x.key === cur.key);
          if (o) {
            const a = planetAngle.get(o.key) ?? o.angle;
            const px = cx + Math.cos(a) * o.ringRadius * PXPER;
            const py = cy + Math.sin(a) * o.ringRadius * PXPER;
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

        // ── particle stream: emit from the active region → the star ──────────
        // emission ∝ burn; frozen under reduced-motion. cache-teal fraction is
        // a share of motes drawn from the recycled-fuel reservoir.
        if (!reduced && running && vis.burn > 0.001) {
          const teal = runStore.cacheFrac;
          emitAccrue += vis.burn * 4.2; // up to ~4 motes/frame at full burn
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
          m.prog += m.speed * (0.6 + vis.burn);
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

        // ── corona + star (bloom scaled by burn) ──
        corona.clear();
        const coronaR = vis.starR * pulse;
        const bloom = 1 + vis.burn * 1.4;
        for (let i = 5; i >= 1; i--) {
          corona
            .circle(cx, cy, coronaR + i * 6 * bloom)
            .fill({ color: vis.starColor, alpha: 0.05 * (6 - i) * 0.4 * (0.6 + vis.burn * 0.7) });
        }
        // frost crystal facets when in polar night
        starG.clear();
        if (s.run.restState === 'quota-frost' && vis.nightHue > 0.5) {
          const spikes = 6;
          starG.moveTo(cx + coronaR, cy);
          for (let i = 1; i <= spikes * 2; i++) {
            const ang = (i / (spikes * 2)) * Math.PI * 2;
            const rr = i % 2 === 0 ? coronaR : coronaR * 0.55;
            starG.lineTo(cx + Math.cos(ang) * rr, cy + Math.sin(ang) * rr);
          }
          starG.fill({ color: vis.starColor, alpha: 0.95 });
        } else {
          starG.circle(cx, cy, coronaR).fill({ color: vis.starColor, alpha: 0.98 });
          starG.circle(cx, cy, coronaR * 0.6).fill({ color: C.starlight, alpha: 0.5 });
          // hot inner ring tinted by the active model when burning
          if (vis.burn > 0.05) {
            starG
              .circle(cx, cy, coronaR * 0.85)
              .stroke({ width: 1.5, color: vis.emitColor, alpha: 0.25 + vis.burn * 0.4 });
          }
        }

        // ── planets ──
        planetsC.removeChildren();
        const spin = vis.t * 0.06; // global slow orbital advance
        for (const o of runStore.orbits) {
          const target = o.angle + (running && !reduced ? spin : 0);
          const prev = planetAngle.get(o.key);
          const a = prev === undefined ? target : lerp(prev, target, 0.06);
          planetAngle.set(o.key, a);
          const r = o.ringRadius * PXPER;
          const px = cx + Math.cos(a) * r;
          const py = cy + Math.sin(a) * r;
          const g = new PIXI.Graphics();
          const col = planetColor(o);
          const pr = 5 + (o.merged ? 1.5 : 0);
          g.circle(px, py, pr).fill({ color: col, alpha: 0.95 });
          if (o.certified || o.merged) {
            g.circle(px, py, pr + 3).stroke({ width: 1.2, color: C.brass, alpha: 0.85 });
          }
          if (o.status === 'review' && !o.certified) {
            const ap = reduced ? 0.4 : 0.3 + Math.abs(Math.sin(vis.t * 3)) * 0.4;
            g.circle(px, py, pr + 2.5).stroke({ width: 1, color: C.amber, alpha: ap });
          }
          for (let i = 0; i < Math.min(o.strikes, 3); i++) {
            const na = a + (i - 1) * 0.06;
            const nx = cx + Math.cos(na) * (r + 9);
            const ny = cy + Math.sin(na) * (r + 9);
            g.circle(nx, ny, 1.4).fill({ color: C.crimson, alpha: 0.9 });
          }
          planetsC.addChild(g);
        }

        raf = requestAnimationFrame(tick);
      };
      raf = requestAnimationFrame(tick);
    })();

    return () => {
      destroyed = true;
      if (raf) cancelAnimationFrame(raf);
      cleanupResize?.();
      rmQuery.removeEventListener?.('change', onRm);
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

<div bind:this={host} class="observatory" aria-hidden="true"></div>

<style>
  .observatory {
    position: absolute;
    inset: 0;
    width: 100%;
    height: 100%;
    background: var(--void);
  }
  .observatory :global(canvas) {
    display: block;
  }
</style>
