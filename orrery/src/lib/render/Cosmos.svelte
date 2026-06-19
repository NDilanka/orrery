<script lang="ts">
  // THE COSMOS (A4) — the multi-loop home. Every registered loop is a small
  // star-system glyph laid out on a field; the platform made visible. This is
  // Tier-1 ONLY (plan §3): a glance answers "is it healthy / does it need me?"
  //   - star color + glow by status (running = warm live glow)
  //   - the FOUR not-running palettes for rest-states (greyscale-separable
  //     silhouettes: certified-seal · banked-ember dome · frost crystal ·
  //     handoff beacon wedge) — never hue alone
  //   - a thin cost-horizon ring whose tightness reads spend (invisible <50%)
  //   - running loops gently animate; idle ones are dim embers
  // Hover → a label card (name · cumUsd · current item). Click a system → fly
  // into its System view. An "✦ ignite new loop" affordance opens the A5 stub.
  //
  // CLIENT-ONLY: Pixi is dynamically imported inside onMount and browser-guarded
  // so it never runs at build/SSR/prerender time. A single rAF eases motion;
  // prefers-reduced-motion freezes all decorative motion (only state cross-fades).

  import { onMount } from 'svelte';
  import { browser } from '$app/environment';
  import { cosmosStore, type LoopSummary } from '../stores/cosmos.svelte';

  let { onEnter }: { onEnter: (loopId: string) => void } = $props();

  let host: HTMLDivElement;
  // hover label, positioned in screen space over the canvas
  let hover = $state<{ x: number; y: number; loop: LoopSummary } | null>(null);

  const C = {
    void: 0x070912,
    brass: 0xc9a24b,
    starlight: 0xeaf0ff,
    ember: 0xff7a3c,
    cyan: 0x46e0ff,
    amber: 0xffc24b,
    green: 0x5bf09b,
    crimson: 0xff3b5c,
    auditor: 0xf4f8ff,
    horizonRose: 0xff6b7e,
    frost: 0x9fb6ff,
  };

  function fmtUsd(n: number): string {
    return '$' + n.toFixed(2);
  }

  // star color by status / rest-state (mirrors the Observatory's starColor)
  function glyphColor(l: LoopSummary): number {
    if (l.restState === 'quota-frost') return C.frost;
    if (l.restState === 'handoff-beacon') return C.crimson;
    if (l.restState === 'certified-done') return C.green;
    if (l.restState === 'stopped-ember') return C.ember;
    if (l.status === 'error') return C.crimson;
    if (l.status === 'running') return C.amber;
    if (l.status === 'quota-wait') return C.frost;
    return 0x6c779e; // idle ember (dim)
  }

  onMount(() => {
    if (!browser) return;
    let destroyed = false;
    let app: any = null;
    let raf = 0;
    let cleanup: (() => void) | null = null;

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

      const world = new PIXI.Container();
      app.stage.addChild(world);
      const starfield = new PIXI.Graphics();
      const g = new PIXI.Graphics(); // all glyphs, redrawn each frame
      world.addChild(starfield, g);

      let w = host.clientWidth || 800;
      let h = host.clientHeight || 600;

      function drawStarfield(W: number, H: number) {
        starfield.clear();
        let seed = 9173;
        const rnd = () => {
          seed = (seed * 1103515245 + 12345) & 0x7fffffff;
          return seed / 0x7fffffff;
        };
        for (let i = 0; i < 260; i++) {
          starfield
            .circle(rnd() * W, rnd() * H, rnd() * 1.1 + 0.2)
            .fill({ color: C.starlight, alpha: rnd() * 0.45 + 0.04 });
        }
      }
      drawStarfield(w, h);

      // ── layout: a calm grid of systems, centred, that reflows on resize ──
      type Cell = { x: number; y: number; cell: number };
      let cells: Cell[] = [];
      function layout() {
        const loops = cosmosStore.loops;
        const n = Math.max(loops.length, 1);
        const cols = Math.min(n, Math.max(2, Math.ceil(Math.sqrt(n * (w / Math.max(h, 1))))));
        const rows = Math.ceil(n / cols);
        const padX = w * 0.12;
        const padY = h * 0.16;
        const gx = (w - padX * 2) / Math.max(cols, 1);
        const gy = (h - padY * 2) / Math.max(rows, 1);
        const cell = Math.min(gx, gy);
        cells = loops.map((_, i) => {
          const r = Math.floor(i / cols);
          const c = i % cols;
          // centre partial last row
          const rowCount = r === rows - 1 ? n - r * cols : cols;
          const rowW = rowCount * gx;
          const x0 = (w - rowW) / 2 + gx / 2;
          return { x: x0 + c * gx, y: padY + gy / 2 + r * gy, cell };
        });
      }
      layout();

      const onResize = () => {
        w = host.clientWidth || 800;
        h = host.clientHeight || 600;
        drawStarfield(w, h);
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

      const onMove = (e: MouseEvent) => {
        const rect = app.canvas.getBoundingClientRect();
        const mx = e.clientX - rect.left;
        const my = e.clientY - rect.top;
        const id = pick(mx, my);
        const loop = id ? cosmosStore.get(id) : null;
        const p = id ? hits.get(id) : null;
        if (loop && p) {
          hover = { x: p.x, y: p.y - p.r - 14, loop };
          app.canvas.style.cursor = 'pointer';
        } else {
          hover = null;
          app.canvas.style.cursor = 'default';
        }
      };
      const onLeave = () => {
        hover = null;
      };
      const onClick = (e: MouseEvent) => {
        const rect = app.canvas.getBoundingClientRect();
        const id = pick(e.clientX - rect.left, e.clientY - rect.top);
        if (id) onEnter(id);
      };
      app.canvas.style.pointerEvents = 'auto';
      app.canvas.addEventListener('mousemove', onMove);
      app.canvas.addEventListener('mouseleave', onLeave);
      app.canvas.addEventListener('click', onClick);
      cleanup = () => {
        ro.disconnect();
        app.canvas.removeEventListener('mousemove', onMove);
        app.canvas.removeEventListener('mouseleave', onLeave);
        app.canvas.removeEventListener('click', onClick);
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

      let t = 0;
      let lastCount = -1;
      const tick = () => {
        if (destroyed) return;
        const loops = cosmosStore.loops;
        if (loops.length !== lastCount) {
          layout();
          lastCount = loops.length;
        }
        if (!reduced) t += 0.016;
        g.clear();
        hits.clear();

        loops.forEach((l, i) => {
          const cell = cells[i];
          if (!cell) return;
          const { x: cx, y: cy } = cell;
          const baseR = Math.max(10, Math.min(22, cell.cell * 0.12));
          // star radius nudged by spend (log so a big run doesn't dwarf a small)
          const sr = baseR + Math.min(baseR * 0.6, Math.log1p(l.cumUsd) * 2.2);

          const running = l.status === 'running';
          const target = glyphColor(l);
          let e = ease.get(l.id);
          if (!e) {
            e = { glow: 0, col: target };
            ease.set(l.id, e);
          }
          e.col = lerpColor(e.col, target, 0.08);
          // running = warm live glow; idle = dim ember; rest-states sit steady
          const wantGlow = running ? 1 : l.restState ? 0.5 : 0.18;
          e.glow = lerp(e.glow, wantGlow, 0.06);
          const col = e.col;

          // breathing only while running (steady state never animates, §F)
          const pulse =
            running && !reduced ? 1 + Math.sin(t * 2.0 + i * 1.3) * (0.04 + l.ratePerMin * 0.01) : 1;
          const r = sr * pulse;

          hits.set(l.id, { x: cx, y: cy, r });

          // ── cost-horizon ring (thin; invisible <50%, tightens with spend) ──
          if (l.horizonFrac >= 0.5) {
            const maxHR = baseR * 2.6;
            const hr = maxHR * (1.05 - Math.min(1, l.horizonFrac) * 0.5);
            let hcol = C.amber;
            if (l.horizonFrac >= 1) hcol = C.crimson;
            else if (l.horizonFrac >= 0.8) hcol = C.horizonRose;
            g.circle(cx, cy, hr).stroke({ width: 1.2, color: hcol, alpha: 0.55 });
          }

          // ── glow / corona ──
          for (let k = 3; k >= 1; k--) {
            g.circle(cx, cy, r + k * 5).fill({ color: col, alpha: 0.05 * (4 - k) * e.glow });
          }

          // ── the FOUR rest-state silhouettes (greyscale-separable) ──────────
          if (l.restState === 'quota-frost' || l.status === 'quota-wait') {
            // cold crystal: sharp 6-point star
            const spikes = 6;
            g.moveTo(cx + r, cy);
            for (let s = 1; s <= spikes * 2; s++) {
              const ang = (s / (spikes * 2)) * Math.PI * 2;
              const rr = s % 2 === 0 ? r : r * 0.5;
              g.lineTo(cx + Math.cos(ang) * rr, cy + Math.sin(ang) * rr);
            }
            g.fill({ color: col, alpha: 0.9 });
            g.circle(cx, cy, r * 0.35).fill({ color: C.frost, alpha: 0.5 });
          } else if (l.restState === 'stopped-ember') {
            // banked ember: warm dome with a bank line
            g.circle(cx, cy, r).fill({ color: col, alpha: 0.85 });
            g.rect(cx - r, cy + r * 0.15, r * 2, r * 0.55).fill({ color: C.void, alpha: 0.55 });
            g.circle(cx, cy - r * 0.15, r * 0.5).fill({ color: C.ember, alpha: 0.8 });
          } else if (l.restState === 'handoff-beacon') {
            // distress beacon: rotating amber→crimson wedge (slow = urgency)
            g.circle(cx, cy, r).fill({ color: col, alpha: 0.92 });
            const sweep = reduced ? 0 : t * 1.0;
            const beamR = r * 2.6;
            const wedge = 0.6;
            g.moveTo(cx, cy);
            g.arc(cx, cy, beamR, sweep - wedge / 2, sweep + wedge / 2);
            g.closePath();
            g.fill({ color: C.crimson, alpha: 0.16 });
            g.moveTo(cx, cy);
            g.arc(cx, cy, beamR, sweep + Math.PI - wedge / 2, sweep + Math.PI + wedge / 2);
            g.closePath();
            g.fill({ color: C.amber, alpha: 0.12 });
            g.circle(cx, cy, r * 0.55).fill({ color: C.crimson, alpha: 0.75 });
          } else if (l.restState === 'certified-done') {
            // sealed: calm green disc + brass certification ring + notches
            g.circle(cx, cy, r).fill({ color: col, alpha: 0.96 });
            g.circle(cx, cy, r * 0.6).fill({ color: C.starlight, alpha: 0.4 });
            g.circle(cx, cy, r + 4).stroke({ width: 1.6, color: C.brass, alpha: 0.85 });
            for (let s = 0; s < 8; s++) {
              const ang = (s / 8) * Math.PI * 2;
              g.moveTo(cx + Math.cos(ang) * (r + 4), cy + Math.sin(ang) * (r + 4))
                .lineTo(cx + Math.cos(ang) * (r + 7), cy + Math.sin(ang) * (r + 7))
                .stroke({ width: 1, color: C.brass, alpha: 0.7 });
            }
          } else {
            // running / idle: live (or dim) star core
            g.circle(cx, cy, r).fill({ color: col, alpha: running ? 0.98 : 0.6 });
            g.circle(cx, cy, r * 0.55).fill({ color: C.starlight, alpha: running ? 0.5 : 0.22 });
          }

          // a tiny "system" plinth (three faint base ticks). Names live in the
          // DOM legend + hover card, not Pixi text (which would re-alloc/frame).
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

<div class="cosmos">
  <div bind:this={host} class="field" aria-hidden="true"></div>

  <!-- always-visible legend (names are glanceable without hover) -->
  <ul class="legend" aria-label="loops">
    {#each cosmosStore.loops as l (l.id)}
      <li>
        <button
          class="chip"
          onclick={() => onEnter(l.id)}
          onmouseenter={() => (hover = null)}
        >
          <span class="dot {l.restState ?? l.status}"></span>
          <span class="lid mono">{l.id}</span>
          <span class="lcost mono">{fmtUsd(l.cumUsd)}</span>
        </button>
      </li>
    {/each}
  </ul>

  <!-- rich hover card -->
  {#if hover}
    <div class="card" style="left:{hover.x}px; top:{hover.y}px;">
      <div class="cname">{hover.loop.name}</div>
      <div class="crow mono">
        <span class="ccost">{fmtUsd(hover.loop.cumUsd)}</span>
        <span class="cstat {hover.loop.restState ?? hover.loop.status}">
          {hover.loop.restState ?? hover.loop.status}
        </span>
      </div>
      {#if hover.loop.currentItem}
        <div class="cur mono">→ {hover.loop.currentItem}</div>
      {/if}
      <div class="hint">click to enter system</div>
    </div>
  {/if}

  {#if cosmosStore.loading}
    <div class="loading mono">igniting the cosmos…</div>
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
  .legend {
    position: absolute;
    bottom: 22px;
    left: 50%;
    transform: translateX(-50%);
    margin: 0;
    padding: 8px 12px;
    list-style: none;
    display: flex;
    flex-wrap: wrap;
    justify-content: center;
    gap: 6px;
    max-width: min(90vw, 880px);
    background: var(--panel);
    border: 1px solid var(--panel-edge);
    border-radius: var(--radius-pill);
    backdrop-filter: blur(8px);
    z-index: 11;
  }
  .legend li {
    display: flex;
  }
  .chip {
    display: inline-flex;
    align-items: center;
    gap: 7px;
    padding: 5px 11px;
    border-radius: var(--radius-pill);
    border: 1px solid var(--hairline);
    background: transparent;
    color: var(--starlight);
    cursor: pointer;
    transition: border-color 0.18s, background 0.18s;
  }
  .chip:hover {
    border-color: color-mix(in srgb, var(--brass) 45%, transparent);
    background: color-mix(in srgb, var(--brass) 8%, transparent);
  }
  .chip .lid {
    font-size: 11px;
    letter-spacing: 0.08em;
    text-transform: uppercase;
    color: var(--brass);
  }
  .chip .lcost {
    font-size: 10.5px;
    color: var(--text-dim);
  }
  .dot {
    width: 8px;
    height: 8px;
    border-radius: 50%;
    background: var(--text-faint);
    flex: none;
  }
  .dot.running {
    background: var(--amber);
  }
  .dot.certified-done {
    background: var(--plasma-green);
  }
  .dot.stopped-ember {
    background: var(--ember);
  }
  .dot.quota-frost,
  .dot.quota-wait {
    background: var(--frost);
  }
  .dot.handoff-beacon,
  .dot.error {
    background: var(--crimson);
  }
  .card {
    position: absolute;
    transform: translate(-50%, -100%);
    min-width: 160px;
    max-width: 240px;
    padding: 9px 12px;
    background: var(--panel);
    border: 1px solid var(--panel-edge);
    border-radius: var(--radius);
    backdrop-filter: blur(8px);
    pointer-events: none;
    z-index: 12;
  }
  .cname {
    font-size: 12px;
    color: var(--starlight);
    margin-bottom: 5px;
    line-height: 1.3;
  }
  .crow {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 8px;
    font-size: 11px;
  }
  .ccost {
    color: var(--brass);
  }
  .cstat {
    text-transform: uppercase;
    letter-spacing: 0.08em;
    font-size: 9.5px;
    color: var(--text-dim);
  }
  .cstat.running {
    color: var(--amber);
  }
  .cstat.certified-done {
    color: var(--plasma-green);
  }
  .cstat.stopped-ember {
    color: var(--ember);
  }
  .cstat.quota-frost,
  .cstat.quota-wait {
    color: var(--frost);
  }
  .cstat.handoff-beacon,
  .cstat.error {
    color: var(--crimson);
  }
  .cur {
    margin-top: 5px;
    font-size: 10.5px;
    color: var(--text-dim);
  }
  .hint {
    margin-top: 6px;
    font-size: 9px;
    text-transform: uppercase;
    letter-spacing: 0.14em;
    color: var(--text-faint);
  }
  .loading {
    position: absolute;
    top: 50%;
    left: 50%;
    transform: translate(-50%, -50%);
    color: var(--text-dim);
    font-size: 12px;
    letter-spacing: 0.1em;
  }
</style>
