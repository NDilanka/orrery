<script lang="ts">
  // The PixiJS scene — CLIENT-ONLY. Pixi is dynamically imported inside onMount
  // and guarded with SvelteKit's `browser`, so it never runs at build/SSR/
  // prerender time. Data mutates the store; a single rAF loop eases visual
  // props toward store targets (the tiny data rate is decoupled from 60fps).
  //
  // Renders: Void background · central Star (radius←cumUsd, color←run.status) ·
  // one ring per group (color←group.status) · a planet per item on its ring
  // (angle by index, color by item.status) · the frozen ghost-target wireframe
  // for the current item · the cost-horizon ring (visible ≥50%).

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
      const horizon = new PIXI.Graphics(); // cost-horizon ring
      const ringsG = new PIXI.Graphics(); // group rings
      const ghostG = new PIXI.Graphics(); // frozen target wireframe
      const corona = new PIXI.Graphics(); // star glow
      const starG = new PIXI.Graphics(); // star core
      const planetsC = new PIXI.Container(); // planets
      world.addChild(starfield, horizon, ringsG, ghostG, corona, starG, planetsC);

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
        horizonFrac: 0,
        t: 0,
      };
      // per-planet eased angle (so they rotate smoothly)
      const planetAngle = new Map<string, number>();

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
        vis.t += 0.016;

        // ease star radius (from cumUsd) + color (from status/restState)
        const targetR = STAR_R0 + STAR_K * Math.log1p(Math.max(0, s.run.cumUsd));
        vis.starR = lerp(vis.starR, targetR, 0.08);
        const targetColor = starColor(s.run.status, s.run.restState);
        vis.starColor = lerpColor(vis.starColor, targetColor, 0.08);

        // breathing pulse (telemetry liveness; freezes if data freezes)
        const running = s.run.status === 'running';
        const pulse = running ? 1 + Math.sin(vis.t * 2.2) * 0.04 : 1;

        // ── rings (one per group, colored by status) ──
        ringsG.clear();
        for (const ring of runStore.rings) {
          const r = ring.radius * PXPER;
          ringsG
            .circle(cx, cy, r)
            .stroke({ width: ring.status === 'in-progress' ? 1.6 : 1, color: ringColor(ring.status), alpha: 0.5 });
        }

        // ── cost horizon (Roche ring) — invisible <50% ──
        horizon.clear();
        const frac = runStore.horizonFrac;
        vis.horizonFrac = lerp(vis.horizonFrac, frac, 0.06);
        if (vis.horizonFrac >= 0.5) {
          // contracts toward the star as it approaches 1.0
          const maxR = Math.min(w, h) * 0.46;
          const hr = maxR * (1.05 - Math.min(1, vis.horizonFrac) * 0.55);
          let col = C.amber;
          if (vis.horizonFrac >= 1) col = C.crimson;
          else if (vis.horizonFrac >= 0.8) col = C.horizonRose;
          const a = 0.35 + Math.min(0.4, (vis.horizonFrac - 0.5) * 0.8);
          horizon.circle(cx, cy, hr).stroke({ width: 2, color: col, alpha: a });
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

        // ── corona + star ──
        corona.clear();
        const coronaR = vis.starR * pulse;
        for (let i = 4; i >= 1; i--) {
          corona
            .circle(cx, cy, coronaR + i * 6)
            .fill({ color: vis.starColor, alpha: 0.05 * (5 - i) * 0.4 });
        }
        // frost crystal facets when in polar night
        starG.clear();
        if (s.run.restState === 'quota-frost') {
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
        }

        // ── ghost target wireframe for the current item ──
        ghostG.clear();
        const cur = runStore.current;
        if (cur) {
          const o = runStore.orbits.find((x) => x.key === cur.key);
          if (o) {
            const a = planetAngle.get(o.key) ?? o.angle;
            const px = cx + Math.cos(a) * o.ringRadius * PXPER;
            const py = cy + Math.sin(a) * o.ringRadius * PXPER;
            // faint brass wireframe ring around the current planet's destination
            ghostG.circle(px, py, 12).stroke({ width: 1, color: C.ghostBrass, alpha: 0.5 });
            // AC constellation: faint spokes (criteria), lit if met
            const crit = cur.ghost?.criteria ?? [];
            const n = Math.max(crit.length, 4);
            for (let i = 0; i < n; i++) {
              const ang = (i / n) * Math.PI * 2 + vis.t * 0.2;
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

        // ── planets ──
        // rebuild planet gfx each frame (counts are tiny)
        planetsC.removeChildren();
        const spin = vis.t * 0.06; // global slow orbital advance
        for (const o of runStore.orbits) {
          const target = o.angle + (running ? spin : 0);
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
          // certified seal = brass ring
          if (o.certified || o.merged) {
            g.circle(px, py, pr + 3).stroke({ width: 1.2, color: C.brass, alpha: 0.85 });
          }
          // review (claimed-green) pulses anxiously
          if (o.status === 'review' && !o.certified) {
            const ap = 0.3 + Math.abs(Math.sin(vis.t * 3)) * 0.4;
            g.circle(px, py, pr + 2.5).stroke({ width: 1, color: C.amber, alpha: ap });
          }
          // strike notches on its ring
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
