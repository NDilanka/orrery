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

  let { onEnter }: { onEnter: (loopId: string) => void } = $props();

  let host: HTMLDivElement;
  // hover label, positioned in screen space over the canvas. `below` flips the
  // card under the glyph when it would otherwise clip the top edge; `pinned`
  // marks a touch-revealed card (first tap shows, button/second tap enters).
  let hover = $state<{
    x: number;
    y: number;
    r: number;
    loop: LoopSummary;
    below: boolean;
    pinned: boolean;
  } | null>(null);

  // measured card size, so we can clamp it inside the host bounds
  let cardEl = $state<HTMLDivElement | null>(null);
  let cardW = $state(220);
  let cardH = $state(96);

  $effect(() => {
    if (!cardEl) return;
    cardW = cardEl.offsetWidth || cardW;
    cardH = cardEl.offsetHeight || cardH;
  });

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

  // The state key a glyph/row reads from (restState wins, else status). Used for
  // the dot class, the shape glyph and the human label — never hue alone.
  function stateKey(l: LoopSummary): string {
    return l.restState ?? l.status;
  }

  // A non-hue separator: a small geometric glyph paired with the dot so status
  // survives greyscale / colour-blindness (design rule: status ≠ hue alone).
  function stateGlyph(key: string): string {
    switch (key) {
      case 'running':
        return '◆'; // live diamond
      case 'certified-done':
        return '✓'; // sealed
      case 'stopped-ember':
        return '▬'; // banked
      case 'quota-frost':
      case 'quota-wait':
        return '❄'; // frost
      case 'handoff-beacon':
      case 'handoff':
        return '!'; // needs you
      case 'error':
        return '×'; // crashed
      case 'stopping':
        return '◇'; // winding down
      default:
        return '·'; // idle ember
    }
  }

  // Plain-language status for the label + aria (so "does it need me?" reads
  // without a mouse hover).
  function stateLabel(key: string): string {
    switch (key) {
      case 'running':
        return 'running';
      case 'certified-done':
        return 'certified done';
      case 'stopped-ember':
        return 'stopped';
      case 'quota-frost':
      case 'quota-wait':
        return 'quota wait';
      case 'handoff-beacon':
      case 'handoff':
        return 'needs you';
      case 'error':
        return 'error';
      case 'stopping':
        return 'stopping';
      default:
        return 'idle';
    }
  }

  // A full glanceable aria-label for a roster row / hover card.
  function loopAria(l: LoopSummary): string {
    const parts = [
      `Loop ${l.id}`,
      stateLabel(stateKey(l)),
      `spend ${fmtUsd(l.cumUsd)}`,
    ];
    if (l.currentItem) parts.push(`on ${l.currentItem}`);
    return parts.join(', ');
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

  // Position + flip + clamp a card for a glyph hit at (px,py) with radius r.
  function placeCard(px: number, py: number, r: number, loop: LoopSummary, pinned: boolean) {
    const w = host?.clientWidth || 800;
    const h = host?.clientHeight || 600;
    const pad = 8;
    // default: card sits ABOVE the glyph; flip BELOW if it would clip the top.
    const wantBelow = py - r - cardH - pad < 0;
    let x = px;
    // clamp horizontally so the (centre-anchored) card stays inside the host
    const half = cardW / 2;
    x = Math.max(half + pad, Math.min(w - half - pad, x));
    const y = wantBelow ? py + r + 14 : py - r - 14;
    hover = { x, y, r, loop, below: wantBelow, pinned };
  }

  // Row ENTER from the DOM roster.
  function enterFromRow(id: string) {
    hover = null;
    onEnter(id);
  }

  onMount(() => {
    if (!browser) return;
    let destroyed = false;
    let app: any = null;
    let raf = 0;
    let cleanup: (() => void) | null = null;

    // reduced motion is read live from the single source of truth (uiStore);
    // a small reactive bridge keeps the rAF's `reduced` flag in sync.
    let reduced = uiStore.reducedMotion;
    const stopReduced = $effect.root(() => {
      $effect(() => {
        reduced = uiStore.reducedMotion;
      });
    });

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
        // a touch-pinned card stays put until tapped away / entered
        if (hover?.pinned) return;
        const rect = app.canvas.getBoundingClientRect();
        const mx = e.clientX - rect.left;
        const my = e.clientY - rect.top;
        const id = pick(mx, my);
        const loop = id ? cosmosStore.get(id) : null;
        const p = id ? hits.get(id) : null;
        if (loop && p) {
          placeCard(p.x, p.y, p.r, loop, false);
          app.canvas.style.cursor = 'pointer';
        } else {
          hover = null;
          app.canvas.style.cursor = 'default';
        }
      };
      const onLeave = () => {
        if (hover?.pinned) return;
        hover = null;
      };
      const onClick = (e: MouseEvent) => {
        const rect = app.canvas.getBoundingClientRect();
        const id = pick(e.clientX - rect.left, e.clientY - rect.top);
        if (id) onEnter(id);
      };
      // Touch: first tap reveals the card (pinned), a second tap on the SAME
      // glyph enters; tapping empty space / another glyph re-targets. We never
      // dive straight in on the first touch (plan §7 hover→tap).
      const onTouchStart = (e: TouchEvent) => {
        const t = e.touches[0] ?? e.changedTouches[0];
        if (!t) return;
        const rect = app.canvas.getBoundingClientRect();
        const mx = t.clientX - rect.left;
        const my = t.clientY - rect.top;
        const id = pick(mx, my);
        if (!id) {
          hover = null;
          return;
        }
        const loop = cosmosStore.get(id);
        const p = hits.get(id);
        if (!loop || !p) return;
        if (hover?.pinned && hover.loop.id === id) {
          // second tap on the already-revealed glyph → enter
          e.preventDefault();
          enterFromRow(id);
          return;
        }
        // first tap → reveal a pinned card with explicit enter/edit
        e.preventDefault();
        placeCard(p.x, p.y, p.r, loop, true);
      };
      app.canvas.style.pointerEvents = 'auto';
      app.canvas.addEventListener('mousemove', onMove);
      app.canvas.addEventListener('mouseleave', onLeave);
      app.canvas.addEventListener('click', onClick);
      app.canvas.addEventListener('touchstart', onTouchStart, { passive: false });
      cleanup = () => {
        ro.disconnect();
        app.canvas.removeEventListener('mousemove', onMove);
        app.canvas.removeEventListener('mouseleave', onLeave);
        app.canvas.removeEventListener('click', onClick);
        app.canvas.removeEventListener('touchstart', onTouchStart);
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
          // DOM roster + hover card, not Pixi text (which would re-alloc/frame).
          for (let s = 0; s < 3; s++) {
            const ang = Math.PI * 0.5 + (s - 1) * 0.5;
            g.moveTo(cx + Math.cos(ang) * (r + 2), cy + Math.sin(ang) * (r + 2))
              .lineTo(cx + Math.cos(ang) * (r + 5), cy + Math.sin(ang) * (r + 5))
              .stroke({ width: 1, color: col, alpha: 0.25 });
          }
        });

        // keep a pinned/hover card glued to its glyph as the field reflows
        if (hover) {
          const p = hits.get(hover.loop.id);
          const loop = cosmosStore.get(hover.loop.id);
          if (p && loop) placeCard(p.x, p.y, p.r, loop, hover.pinned);
          else hover = null;
        }

        raf = requestAnimationFrame(tick);
      };
      raf = requestAnimationFrame(tick);
    })();

    return () => {
      destroyed = true;
      if (raf) cancelAnimationFrame(raf);
      cleanup?.();
      stopReduced();
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

  <!-- rich hover / touch-detail card, clamped to the host (flips below near the top) -->
  {#if hover}
    {@const key = stateKey(hover.loop)}
    <div
      bind:this={cardEl}
      class="card"
      class:below={hover.below}
      class:pinned={hover.pinned}
      style="left:{hover.x}px; top:{hover.y}px;"
      role={hover.pinned ? 'dialog' : undefined}
      aria-label={hover.pinned ? loopAria(hover.loop) : undefined}
    >
      <div class="cname">{hover.loop.name}</div>
      <div class="crow">
        <span class="ccost num">{fmtUsd(hover.loop.cumUsd)}</span>
        <span class="cstat {key}">
          <span class="cglyph" aria-hidden="true">{stateGlyph(key)}</span>
          {stateLabel(key)}
        </span>
      </div>
      {#if hover.loop.currentItem}
        <div class="cur mono">→ {hover.loop.currentItem}</div>
      {/if}
      {#if hover.pinned}
        <div class="cactions">
          <button class="cbtn enter" onclick={() => hover && enterFromRow(hover.loop.id)}>
            enter system →
          </button>
          <button
            class="cbtn edit"
            onclick={() => hover && cosmosStore.editLoop(hover.loop.id)}
            aria-label="Edit loop {hover.loop.id}"
          >
            ✎ edit
          </button>
        </div>
      {:else}
        <div class="hint">click to enter system</div>
      {/if}
    </div>
  {/if}

  <!-- ── the unified loop roster: the single home for ENTER + EDIT ──────────
       desktop = compact bottom bar · phone = scrollable bottom sheet -->
  {#if !cosmosStore.loading && cosmosStore.loops.length}
    <section
      class="roster"
      class:sheet={uiStore.isPhone}
      aria-label="loop roster — enter or edit a loop"
    >
      <ul>
        {#each cosmosStore.loops as l (l.id)}
          {@const key = stateKey(l)}
          <li class="row">
            <button
              class="enter"
              onclick={() => enterFromRow(l.id)}
              onmouseenter={() => (hover = hover?.pinned ? hover : null)}
              aria-label="Enter {loopAria(l)}"
            >
              <span class="dot {key}" aria-hidden="true">
                <span class="dglyph">{stateGlyph(key)}</span>
              </span>
              <span class="meta">
                <span class="lid mono">{l.id}</span>
                <span class="lstate">{stateLabel(key)}</span>
              </span>
              <span class="lcost num">{fmtUsd(l.cumUsd)}</span>
            </button>
            <button
              class="edit"
              onclick={() => cosmosStore.editLoop(l.id)}
              aria-label="Edit loop {l.id}"
              title="Edit {l.name}"
            >
              <span aria-hidden="true">✎</span>
            </button>
          </li>
        {/each}
      </ul>
    </section>
  {/if}

  <!-- ── overlays (HTML, so they survive any canvas suppression) ──────────── -->
  {#if cosmosStore.loading}
    <div class="loading mono" role="status">igniting the cosmos…</div>
  {:else if cosmosStore.loops.length === 0}
    <div class="empty" role="status">
      <p class="etitle">No loops yet</p>
      <p class="esub">Ignite your first loop to populate the cosmos.</p>
    </div>
  {/if}

  {#if cosmosStore.error}
    <div class="errline" role="alert">
      <span class="emsg mono">couldn't load the cosmos</span>
      <button class="retry" onclick={() => cosmosStore.load()}>retry</button>
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

  /* ── unified roster ───────────────────────────────────────────────────── */
  .roster {
    position: absolute;
    bottom: var(--chrome-inset);
    left: 50%;
    transform: translateX(-50%);
    max-width: min(92vw, 920px);
    background: var(--panel);
    border: 1px solid var(--panel-edge);
    border-radius: var(--radius-pill);
    backdrop-filter: blur(8px);
    z-index: 11;
    padding: var(--space-1) var(--space-2);
  }
  .roster ul {
    margin: 0;
    padding: 0;
    list-style: none;
    display: flex;
    flex-wrap: wrap;
    justify-content: center;
    gap: var(--space-1);
  }
  /* phone: a scrollable bottom SHEET (clears the cost strip) */
  .roster.sheet {
    left: 0;
    right: 0;
    bottom: 0;
    transform: none;
    max-width: none;
    border-radius: var(--radius) var(--radius) 0 0;
    border-bottom: none;
    padding: var(--space-2) var(--space-2) calc(var(--space-2) + env(safe-area-inset-bottom, 0px));
    max-height: 42vh;
    overflow-y: auto;
    -webkit-overflow-scrolling: touch;
  }
  .roster.sheet ul {
    flex-direction: column;
    flex-wrap: nowrap;
    gap: var(--space-2);
  }

  .row {
    display: flex;
    align-items: stretch;
    gap: var(--space-1);
  }
  .roster.sheet .row {
    width: 100%;
  }

  .row .enter,
  .row .edit {
    border: 1px solid var(--hairline);
    background: transparent;
    color: var(--starlight);
    cursor: pointer;
    transition: border-color var(--dur-fast) var(--ease-standard),
      background var(--dur-fast) var(--ease-standard),
      color var(--dur-fast) var(--ease-standard);
  }
  .row .enter {
    display: inline-flex;
    align-items: center;
    gap: var(--space-2);
    padding: var(--space-1) var(--space-3);
    border-radius: var(--radius-pill);
  }
  .roster.sheet .row .enter {
    flex: 1 1 auto;
    border-radius: var(--radius);
    justify-content: flex-start;
  }
  .row .enter:hover {
    border-color: color-mix(in srgb, var(--brass) 45%, transparent);
    background: color-mix(in srgb, var(--brass) 8%, transparent);
  }
  .row .edit {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    width: 32px;
    border-radius: var(--radius-pill);
    color: var(--text-meta);
    font-size: var(--text-sm);
  }
  .roster.sheet .row .edit {
    border-radius: var(--radius);
    width: 44px;
  }
  .row .edit:hover {
    border-color: var(--brass);
    color: var(--brass);
    background: color-mix(in srgb, var(--brass) 8%, transparent);
  }

  .meta {
    display: flex;
    flex-direction: column;
    align-items: flex-start;
    gap: 1px;
    line-height: 1.1;
  }
  .lid {
    font-size: var(--text-xs);
    letter-spacing: 0.08em;
    text-transform: uppercase;
    color: var(--brass);
  }
  .lstate {
    font-size: var(--text-2xs);
    letter-spacing: 0.06em;
    color: var(--text-meta);
  }
  .lcost {
    font-size: var(--text-xs);
    color: var(--text-meta);
    margin-left: auto;
  }

  /* status dot carries a SHAPE glyph too — never hue alone */
  .dot {
    width: 16px;
    height: 16px;
    border-radius: 50%;
    background: var(--text-faint);
    flex: none;
    display: inline-flex;
    align-items: center;
    justify-content: center;
  }
  .dot .dglyph {
    font-size: 9px;
    line-height: 1;
    color: var(--void);
    font-weight: 700;
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
  .dot.handoff,
  .dot.error {
    background: var(--crimson);
  }

  /* ── hover / detail card ──────────────────────────────────────────────── */
  .card {
    position: absolute;
    transform: translate(-50%, -100%);
    min-width: 160px;
    max-width: 240px;
    padding: var(--space-2) var(--space-3);
    background: var(--panel);
    border: 1px solid var(--panel-edge);
    border-radius: var(--radius);
    backdrop-filter: blur(8px);
    pointer-events: none;
    z-index: 12;
  }
  /* flipped below the glyph (anchor at the card's TOP) */
  .card.below {
    transform: translate(-50%, 0);
  }
  /* a pinned (touch-revealed) card is interactive */
  .card.pinned {
    pointer-events: auto;
  }
  .cname {
    font-size: var(--text-sm);
    color: var(--starlight);
    margin-bottom: var(--space-1);
    line-height: 1.3;
  }
  .crow {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: var(--space-2);
    font-size: var(--text-xs);
  }
  .ccost {
    color: var(--brass);
  }
  .cstat {
    display: inline-flex;
    align-items: center;
    gap: var(--space-1);
    text-transform: uppercase;
    letter-spacing: 0.08em;
    font-size: var(--text-2xs);
    color: var(--text-meta);
  }
  .cglyph {
    font-size: var(--text-xs);
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
  .cstat.handoff,
  .cstat.error {
    color: var(--crimson);
  }
  .cur {
    margin-top: var(--space-1);
    font-size: var(--text-2xs);
    color: var(--text-meta);
  }
  .hint {
    margin-top: var(--space-2);
    font-size: var(--text-2xs);
    text-transform: uppercase;
    letter-spacing: 0.14em;
    color: var(--text-faint);
  }
  .cactions {
    display: flex;
    gap: var(--space-1);
    margin-top: var(--space-2);
  }
  .cbtn {
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
  .cbtn.enter {
    flex: 1 1 auto;
    border-color: color-mix(in srgb, var(--brass) 45%, transparent);
    color: var(--brass);
  }
  .cbtn.edit {
    color: var(--text-meta);
  }
  .cbtn:hover {
    border-color: var(--brass);
  }

  /* ── overlays ─────────────────────────────────────────────────────────── */
  .loading {
    position: absolute;
    top: 50%;
    left: 50%;
    transform: translate(-50%, -50%);
    color: var(--text-dim);
    font-size: var(--text-sm);
    letter-spacing: 0.1em;
  }
  .empty {
    position: absolute;
    top: 50%;
    left: 50%;
    transform: translate(-50%, -50%);
    text-align: center;
    max-width: 360px;
    padding: var(--space-5) var(--space-6);
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
  .errline {
    position: absolute;
    top: var(--chrome-inset);
    left: 50%;
    transform: translateX(-50%);
    display: flex;
    align-items: center;
    gap: var(--space-3);
    padding: var(--space-1) var(--space-3);
    background: var(--panel);
    border: 1px solid color-mix(in srgb, var(--crimson) 40%, var(--panel-edge));
    border-radius: var(--radius-pill);
    backdrop-filter: blur(8px);
    z-index: 13;
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
</style>
