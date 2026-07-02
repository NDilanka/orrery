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
  // Each glyph publishes its on-canvas screen position here. Set from layout(), so it
  // changes only on resize / loop-count change (NOT every frame) — the always-visible
  // "station" labels read these to sit directly under their glyph.
  let positions = $state<{ id: string; x: number; y: number; r: number }[]>([]);

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
      case 'failed-dark':
        return '⚠'; // crashed — dim cracked disc (Observatory mirror)
      case 'error':
        return '×'; // crashed (no restState yet — defensive fallback)
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
      case 'failed-dark':
        return 'failed';
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
    if (l.restState === 'failed-dark') return C.crimson;
    if (l.restState === 'quota-frost') return C.frost;
    if (l.restState === 'handoff-beacon') return C.crimson;
    if (l.restState === 'certified-done') return C.green;
    if (l.restState === 'stopped-ember') return C.ember;
    if (l.status === 'error') return C.crimson;
    if (l.status === 'running') return C.amber;
    if (l.status === 'quota-wait') return C.frost;
    return 0x6c779e; // idle ember (dim)
  }

  // Enter a loop's system.
  function enterFromRow(id: string) {
    onEnter(id);
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
        app.canvas.removeEventListener('mousemove', onMove);
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
          e.col = lerpColor(e.col, target, 0.08);
          // running = warm live glow; idle = dim ember; rest-states sit steady.
          // failed-dark gets NO glow (it should read dead, not radiant).
          const wantGlow = running ? 1 : l.restState === 'failed-dark' ? 0.08 : l.restState ? 0.5 : 0.18;
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

          // ── the FIVE rest-state silhouettes (greyscale-separable) ──────────
          if (l.restState === 'failed-dark') {
            // crashed: a dim crimson disc, no glow (see wantGlow above), cut by a
            // fixed jagged fracture — mirrors the Observatory's star silhouette.
            const flicker = reduced ? 1 : 0.82 + Math.sin(t * 0.55 + i * 0.4) * 0.18;
            g.circle(cx, cy, r).fill({ color: col, alpha: 0.4 * flicker });
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

  <!-- always-visible STATION labels: each glyph names itself (id · status · cost)
       and IS the accessible enter/edit affordance, sitting just under its glyph.
       This replaces the old anonymous-dots + disconnected bottom roster. -->
  <div class="stations" aria-label="loops">
    {#each cosmosStore.loops as l (l.id)}
      {@const p = positions.find((q) => q.id === l.id)}
      {#if p}
        {@const key = stateKey(l)}
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
            </span>
            {#if l.currentItem}
              <span class="s-cur mono" title={l.currentItem}>→ {l.currentItem}</span>
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
      <button class="pill" class:on={filter === 'all'} aria-pressed={filter === 'all'} onclick={() => (filter = 'all')}>
        All<span class="pcount num">{cosmosStore.loops.length}</span>
      </button>
      <button
        class="pill"
        class:on={filter === 'needs'}
        class:has={needsYouCount > 0}
        aria-pressed={filter === 'needs'}
        onclick={() => (filter = 'needs')}
      >
        Needs you<span class="pcount num">{needsYouCount}</span>
      </button>
      <button
        class="pill"
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

  /* ── station labels (one per glyph) — each loop names itself under its star ── */
  .stations {
    position: absolute;
    inset: 0;
    pointer-events: none; /* gaps pass clicks through to the canvas glyphs */
    z-index: 11;
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
  .station .edit {
    pointer-events: auto;
    border: 1px solid var(--hairline);
    background: color-mix(in srgb, var(--panel) 88%, transparent);
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
    background: color-mix(in srgb, var(--brass) 12%, var(--panel));
  }
  .station.needs .enter {
    border-color: color-mix(in srgb, var(--crimson) 45%, var(--hairline));
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
  .s-stat.running {
    color: var(--amber);
  }
  .s-stat.certified-done {
    color: var(--plasma-green);
  }
  .s-stat.stopped-ember {
    color: var(--ember);
  }
  .s-stat.quota-frost,
  .s-stat.quota-wait {
    color: var(--frost);
  }
  .s-stat.handoff-beacon,
  .s-stat.handoff,
  .s-stat.failed-dark,
  .s-stat.error {
    color: var(--crimson);
  }
  .s-cost {
    font-size: var(--text-2xs);
    color: var(--text-meta);
  }
  .s-cur {
    max-width: 180px;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    font-size: var(--text-2xs);
    color: var(--text-faint);
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
  .s-retro.pending {
    color: var(--amber);
  }
  /* the per-station edit affordance — quiet until hover / keyboard focus */
  .station .edit {
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
  .station:focus-within .edit {
    opacity: 1;
  }
  .station .edit:hover {
    border-color: var(--brass);
    color: var(--brass);
  }

  /* ── N-needs-you badge — the one element allowed to be loud ──────────────── */
  .needsbadge {
    position: absolute;
    top: var(--chrome-inset);
    left: 50%;
    transform: translateX(-50%);
    z-index: 12;
    display: inline-flex;
    align-items: center;
    gap: var(--space-2);
    padding: var(--space-1) var(--space-3);
    border-radius: var(--radius-pill);
    border: 1px solid color-mix(in srgb, var(--crimson) 55%, var(--panel-edge));
    background: color-mix(in srgb, var(--crimson) 16%, var(--panel));
    color: var(--starlight);
    backdrop-filter: blur(8px);
    cursor: pointer;
    font-family: var(--font-grotesk);
    transition: border-color var(--dur-fast) var(--ease-standard),
      background var(--dur-fast) var(--ease-standard);
  }
  .needsbadge:hover,
  .needsbadge[aria-pressed='true'] {
    border-color: var(--crimson);
    background: color-mix(in srgb, var(--crimson) 24%, var(--panel));
  }
  .needsbadge .nbglyph {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    width: 16px;
    height: 16px;
    border-radius: 50%;
    background: var(--crimson);
    color: var(--void);
    font-weight: 700;
    font-size: 11px;
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
    color: color-mix(in srgb, var(--crimson) 30%, var(--starlight));
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
    z-index: 11;
  }
  .pill {
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
  .pill:hover {
    border-color: color-mix(in srgb, var(--brass) 45%, transparent);
    color: var(--starlight);
  }
  .pill.on {
    border-color: var(--brass);
    color: var(--brass);
    background: color-mix(in srgb, var(--brass) 8%, transparent);
  }
  .pill .pcount {
    font-size: var(--text-2xs);
    padding: 0 5px;
    border-radius: var(--radius-pill);
    background: var(--surface-3);
    color: var(--text-meta);
  }
  .pill.on .pcount {
    background: color-mix(in srgb, var(--brass) 22%, var(--surface-3));
    color: var(--brass);
  }
  /* the Needs-you pill earns a crimson edge when the count is nonzero */
  .pill.has {
    border-color: color-mix(in srgb, var(--crimson) 40%, var(--hairline));
    color: var(--starlight);
  }
  .pill.has .pcount {
    background: color-mix(in srgb, var(--crimson) 30%, var(--surface-3));
    color: var(--starlight);
  }

  /* (the old roster-row, status-dot and hover-card styles were removed —
     the per-glyph stations above replace them entirely) */

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
