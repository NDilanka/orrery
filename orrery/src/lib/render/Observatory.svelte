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
      const beamG = new PIXI.Graphics(); // lighthouse sweep / refute beam (under planets)
      const corona = new PIXI.Graphics(); // star glow / bloom
      const supernovaG = new PIXI.Graphics(); // transient crimson supernova on crash
      const starG = new PIXI.Graphics(); // star core
      const planetsC = new PIXI.Container(); // planets
      const lighthouseG = new PIXI.Graphics(); // the off-plane auditor body (Lighthouse)
      const brakeG = new PIXI.Graphics(); // brake-ring "stopping at next <mode>"

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
        beamG,
        particlesC,
        corona,
        supernovaG,
        starG,
        planetsC,
        lighthouseG,
        brakeG,
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
        restForm: 0, // 0 running → 1 the rest-state silhouette is fully formed
        lhWake: 0, // lighthouse idle/dim (0) → awake/bright (1)
        t: 0,
      };
      // per-planet eased angle (so they rotate smoothly)
      const planetAngle = new Map<string, number>();
      let emitAccrue = 0; // fractional particle emission carry

      // ── A3 transient "moments" (edge-detected from steady state; survive scrub)
      // We track per-key prior values and fire a decaying pulse on a transition.
      // sealFlash: certified flipped true (PASS chime/ring bloom)
      // refuteFx:  verdict became fail (Crimson beam snaps on the failing AC)
      // snapFx:    strikes increased (rollback → cyan time-shimmer rewind)
      const prevCertified = new Map<string, boolean>();
      const prevStrikes = new Map<string, number>();
      const prevVerdictPass = new Map<string, boolean | null>();
      const sealFlash = new Map<string, number>(); // key → 0..1 decaying
      const refuteFx = new Map<string, number>();
      const snapFx = new Map<string, number>();
      let prevErrorStop = false;
      let supernova = 0; // 0..1 decaying transient on a crash stop
      // the lighthouse sweep: 0..1 progress of the current beam across the target
      let sweepKey: string | null = null;
      let sweepProg = 1; // 1 = idle (no sweep)
      let lastAuditKey: string | null = null;

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

      // ── click-to-inspect: pick the nearest planet → open its VerdictPanel ──
      const lastPpos = new Map<string, { x: number; y: number; r: number }>();
      const onClick = (e: MouseEvent) => {
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
        runStore.selectItem(best);
      };
      app.canvas.style.pointerEvents = 'auto';
      app.canvas.addEventListener('click', onClick);
      cleanupResize = () => {
        ro.disconnect();
        app.canvas.removeEventListener('click', onClick);
      };

      // ── render loop ──────────────────────────────────────────────────────
      const PXPER = 1; // geometry units → px (rings already in px-ish units)
      const tick = () => {
        if (destroyed) return;
        const s = runStore.state;
        const cx = w / 2;
        const cy = h / 2;
        if (!reduced) vis.t += 0.016;
        const running = s.run.status === 'running';
        const rest = s.run.restState;

        // ── A3: edge-detect transient moments from steady state ──────────────
        // (idempotent w.r.t. scrubbing: a moment fires once per real transition;
        //  re-reducing the same prefix produces the same booleans, no re-fire.)
        const fxDecay = reduced ? 1 : 0.018; // reduced-motion: snap, don't animate
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
        vis.restForm = lerp(vis.restForm, rest ? 1 : 0, 0.05);

        // ── resolve every planet's eased screen position ONCE (shared by the
        //   beam, ghost, snapback shimmer & the planet draw below) ──────────
        const spin = vis.t * 0.06; // global slow orbital advance
        const ppos = new Map<string, { x: number; y: number; r: number; o: any }>();
        for (const o of runStore.orbits) {
          const target = o.angle + (running && !reduced ? spin : 0);
          const prev = planetAngle.get(o.key);
          const a = prev === undefined ? target : lerp(prev, target, 0.06);
          planetAngle.set(o.key, a);
          const r = o.ringRadius * PXPER;
          ppos.set(o.key, { x: cx + Math.cos(a) * r, y: cy + Math.sin(a) * r, r, o });
        }

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

        // ── THE LIGHTHOUSE (auditor) ─────────────────────────────────────────
        // Off-plane cold-white body, idle/dim 95% of the time. Wakes only when a
        // planet is claimed-green-not-certified (something to audit); sweeps a
        // beam to that planet. PASS → the planet seals itself (handled below);
        // FAIL/refute → the beam snaps Crimson on the failing criterion.
        const lhx = Math.max(60, w * 0.13);
        const lhy = Math.max(56, h * 0.16);
        const auditKey = runStore.auditTargetKey;
        // start a sweep when the audit target changes (a new claimed green appears)
        if (auditKey && auditKey !== lastAuditKey) {
          sweepKey = auditKey;
          sweepProg = reduced ? 1 : 0;
        }
        lastAuditKey = auditKey;
        // refute target overrides: aim at whoever was just refuted
        const refuted = [...refuteFx.entries()].sort((a, b) => b[1] - a[1])[0];
        const refuteKey = refuted?.[0] ?? null;
        const wantWake = !!auditKey || !!refuteKey;
        vis.lhWake = lerp(vis.lhWake, wantWake ? 1 : 0, 0.06);
        if (!reduced && sweepProg < 1) sweepProg = Math.min(1, sweepProg + 0.04);

        beamG.clear();
        lighthouseG.clear();
        // beam to the target (sweep = brass/auditor-white; refute = crimson)
        const beamTargetKey = refuteKey ?? sweepKey ?? auditKey;
        const tp = beamTargetKey ? ppos.get(beamTargetKey) : null;
        if (tp && vis.lhWake > 0.04) {
          const isRefute = !!refuteKey && (refuteFx.get(refuteKey) ?? 0) > 0.02;
          const beamCol = isRefute ? C.crimson : C.auditor;
          // the sweep "wipes" from the lighthouse toward the planet (prog 0→1)
          const prog = sweepKey === beamTargetKey ? sweepProg : 1;
          const ex = lerp(lhx, tp.x, prog);
          const ey = lerp(lhy, tp.y, prog);
          const beamA = (isRefute ? 0.5 : 0.16 + vis.lhWake * 0.16) *
            (isRefute ? 0.4 + (refuteFx.get(refuteKey!) ?? 0) * 0.6 : 1);
          // a soft cone: two edges + a bright core line
          const ang = Math.atan2(ey - lhy, ex - lhx);
          const spread = 0.05 + (1 - prog) * 0.02;
          const len = Math.hypot(ex - lhx, ey - lhy);
          beamG
            .moveTo(lhx, lhy)
            .lineTo(lhx + Math.cos(ang - spread) * len, lhy + Math.sin(ang - spread) * len)
            .lineTo(lhx + Math.cos(ang + spread) * len, lhy + Math.sin(ang + spread) * len)
            .closePath()
            .fill({ color: beamCol, alpha: beamA * 0.28 });
          beamG.moveTo(lhx, lhy).lineTo(ex, ey).stroke({ width: isRefute ? 2 : 1.2, color: beamCol, alpha: beamA });
          if (isRefute) {
            // snap a crimson burst on the failing criterion (the AC star)
            const fx = refuteFx.get(refuteKey!) ?? 0;
            beamG.circle(tp.x, tp.y, 4 + fx * 10).stroke({ width: 1.5, color: C.crimson, alpha: fx });
          }
        }
        // the lighthouse body: a tall cold beacon (distinct silhouette: a stacked
        // tower, NOT a disc) — dim when idle, lantern lit when awake.
        {
          const baseW = 7;
          const towerH = 22;
          const dim = 0.18 + vis.lhWake * 0.5;
          // tapered tower
          lighthouseG
            .moveTo(lhx - baseW, lhy + towerH)
            .lineTo(lhx - baseW * 0.55, lhy - towerH * 0.4)
            .lineTo(lhx + baseW * 0.55, lhy - towerH * 0.4)
            .lineTo(lhx + baseW, lhy + towerH)
            .closePath()
            .fill({ color: C.auditor, alpha: 0.1 + vis.lhWake * 0.18 })
            .stroke({ width: 1, color: C.auditor, alpha: 0.35 + vis.lhWake * 0.4 });
          // lantern room (the light source)
          lighthouseG
            .circle(lhx, lhy - towerH * 0.4, 4.5)
            .fill({ color: C.auditor, alpha: dim });
          if (vis.lhWake > 0.3 && !reduced) {
            const halo = (1 + Math.sin(vis.t * 2)) * 0.5;
            lighthouseG
              .circle(lhx, lhy - towerH * 0.4, 7 + halo * 3)
              .stroke({ width: 1, color: C.auditor, alpha: 0.18 * vis.lhWake });
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
        // corona is suppressed for the cold frost state (it should read "cold")
        const coronaAlpha = rest === 'quota-frost' ? 0.4 : 1;
        for (let i = 5; i >= 1; i--) {
          corona
            .circle(cx, cy, coronaR + i * 6 * bloom)
            .fill({
              color: vis.starColor,
              alpha: 0.05 * (6 - i) * 0.4 * (0.6 + vis.burn * 0.7) * coronaAlpha,
            });
        }

        // ── transient crimson SUPERNOVA on a crash stop (E? error) ──
        supernovaG.clear();
        if (supernova > 0.01) {
          const sr = coronaR + (1 - supernova) * Math.min(w, h) * 0.55;
          supernovaG.circle(cx, cy, sr).stroke({ width: 2 + supernova * 4, color: C.crimson, alpha: supernova * 0.7 });
          supernovaG.circle(cx, cy, sr * 0.7).stroke({ width: 1, color: C.crimson, alpha: supernova * 0.4 });
        }

        // ── the star core: FOUR mutually-distinct rest silhouettes ──
        // certified-done = sealed green disc + brass seal · stopped-ember = banked
        // warm dome · quota-frost = cold crystal spikes · handoff-beacon = rotating
        // amber→crimson wedge. Each differs by SHAPE + motion + color (greyscale
        // separable per §F). restForm eases the transition so it "sets" at rest.
        starG.clear();
        const rf = vis.restForm;
        if (rest === 'quota-frost') {
          // cold crystal: sharp 6-point star, no warm core, faint frost shimmer
          const spikes = 6;
          const rr0 = coronaR;
          starG.moveTo(cx + rr0, cy);
          for (let i = 1; i <= spikes * 2; i++) {
            const ang = (i / (spikes * 2)) * Math.PI * 2;
            const rr = i % 2 === 0 ? rr0 : rr0 * 0.5;
            starG.lineTo(cx + Math.cos(ang) * rr, cy + Math.sin(ang) * rr);
          }
          starG.fill({ color: vis.starColor, alpha: 0.9 });
          starG.circle(cx, cy, rr0 * 0.35).fill({ color: C.frost, alpha: 0.5 });
        } else if (rest === 'stopped-ember') {
          // banked ember: a warm DOME (flat-bottomed half-disc) — coasted, parked.
          starG.circle(cx, cy, coronaR).fill({ color: vis.starColor, alpha: 0.5 + rf * 0.45 });
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
          // distress beacon: a rotating amber→crimson wedge sweeping like a siren.
          starG.circle(cx, cy, coronaR).fill({ color: vis.starColor, alpha: 0.92 });
          const sweep = reduced ? 0 : vis.t * 1.1; // slow rotation (urgency=slowing not blinking)
          const wedge = 0.6;
          const beamR = coronaR * 3.4;
          starG.moveTo(cx, cy);
          starG.arc(cx, cy, beamR, sweep - wedge / 2, sweep + wedge / 2);
          starG.closePath();
          starG.fill({ color: C.crimson, alpha: 0.16 });
          // opposite wedge (two-armed beacon) in amber
          starG.moveTo(cx, cy);
          starG.arc(cx, cy, beamR, sweep + Math.PI - wedge / 2, sweep + Math.PI + wedge / 2);
          starG.closePath();
          starG.fill({ color: C.amber, alpha: 0.12 });
          starG.circle(cx, cy, coronaR * 0.55).fill({ color: C.crimson, alpha: 0.7 });
        } else if (rest === 'certified-done') {
          // sealed: calm green disc with a brass certification seal (concentric
          // brass rings + a notch) — steady, NOT animated (it's done).
          starG.circle(cx, cy, coronaR).fill({ color: vis.starColor, alpha: 0.96 });
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
          starG.circle(cx, cy, coronaR).fill({ color: vis.starColor, alpha: 0.98 });
          starG.circle(cx, cy, coronaR * 0.6).fill({ color: C.starlight, alpha: 0.5 });
          if (vis.burn > 0.05) {
            starG
              .circle(cx, cy, coronaR * 0.85)
              .stroke({ width: 1.5, color: vis.emitColor, alpha: 0.25 + vis.burn * 0.4 });
          }
        }

        // ── brake-ring: "stopping at next <mode>" — coasting to a tooth ──
        brakeG.clear();
        if (s.run.stopPending && running) {
          // a tightening brake-shoe arc around the star (urgency = tightening)
          const br = coronaR + 16 + (reduced ? 0 : Math.sin(vis.t * 1.4) * 2);
          const segs = 6;
          for (let i = 0; i < segs; i++) {
            const a0 = (i / segs) * Math.PI * 2 + (reduced ? 0 : vis.t * 0.3);
            brakeG
              .arc(cx, cy, br, a0, a0 + 0.5)
              .stroke({ width: 2.5, color: C.ember, alpha: 0.75 });
          }
        }

        // ── planets ── (positions already eased into `ppos`)
        planetsC.removeChildren();
        lastPpos.clear();
        for (const o of runStore.orbits) {
          const pp = ppos.get(o.key);
          if (!pp) continue;
          const px = pp.x;
          const py = pp.y;
          lastPpos.set(o.key, { x: px, y: py, r: pp.r });
          const a = planetAngle.get(o.key) ?? o.angle;
          const r = pp.r;
          const g = new PIXI.Graphics();
          const col = planetColor(o);
          const pr = 5 + (o.merged ? 1.5 : 0);
          const snap = snapFx.get(o.key) ?? 0; // rollback time-shimmer 0..1
          const seal = sealFlash.get(o.key) ?? 0; // certification chime 0..1
          const refute = refuteFx.get(o.key) ?? 0;

          // rollback snapback: cyan time-shimmer ghost rewinding to best form
          if (snap > 0.02) {
            const ringR = pr + 4 + (1 - snap) * 10;
            g.circle(px, py, ringR).stroke({ width: 1.5, color: C.cyan, alpha: snap * 0.8 });
            g.circle(px, py, ringR + 4).stroke({ width: 1, color: C.cyan, alpha: snap * 0.4 });
          }

          g.circle(px, py, pr).fill({ color: col, alpha: 0.95 });

          // A2: CERTIFIED green (sealed) — a solid brass certification ring; calm.
          if (o.certified) {
            g.circle(px, py, pr + 3).stroke({ width: 1.4, color: C.brass, alpha: 0.9 });
            if (seal > 0.02) {
              // seal "chime": a brass ring blooms outward once on certification
              const bloomR = pr + 3 + (1 - seal) * 14;
              g.circle(px, py, bloomR).stroke({ width: 1.5, color: C.brass, alpha: seal * 0.85 });
            }
          } else if (o.merged) {
            g.circle(px, py, pr + 3).stroke({ width: 1.2, color: C.brass, alpha: 0.7 });
          } else if (o.claimedGreen) {
            // A2: CLAIMED green (asserted, not yet audited) — anxious dashed pulse,
            // NO brass seal. It pulses until the Lighthouse sweeps it.
            const ap = reduced ? 0.4 : 0.25 + Math.abs(Math.sin(vis.t * 3)) * 0.45;
            const wob = reduced ? 0 : Math.sin(vis.t * 3) * 0.8;
            g.circle(px, py, pr + 2.5 + wob).stroke({ width: 1, color: C.green, alpha: ap });
          }

          // refute drain: a crimson flush as the false-green drains back
          if (refute > 0.02) {
            g.circle(px, py, pr + 1).stroke({ width: 1.5, color: C.crimson, alpha: refute });
          }

          // crimson strike-notches on the ring (one per rollback), per budget
          const budget = Math.max(o.strikeBudget, o.strikes);
          for (let i = 0; i < Math.min(o.strikes, Math.max(budget, 3)); i++) {
            const na = a - 0.18 + (i / Math.max(1, budget - 1 || 1)) * 0.36;
            const nx = px + Math.cos(na + Math.PI / 2) * (pr + 6);
            const ny = py + Math.sin(na + Math.PI / 2) * (pr + 6);
            g.moveTo(nx, ny)
              .lineTo(nx + Math.cos(na) * 3, ny + Math.sin(na) * 3)
              .stroke({ width: 1.4, color: C.crimson, alpha: 0.9 });
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
