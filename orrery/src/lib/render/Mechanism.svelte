<script lang="ts">
  // The central 6-gear Mechanism — the loop drawn as clockwork: the six blocks
  //   discover → assemble → execute → verify → persist → decide
  // arranged in a ring of gears. The active gear (from phase.sixPhase) is lit
  // and turns; its emitted color = the active model's spectral class (Haiku
  // ember-red · Sonnet brass-gold · Opus blue-white). A lightweight Canvas 2D
  // drawing on its own rAF, so it's cheap and never touches the Pixi scene.
  //
  // §F restraint: reduced-motion (one source: uiStore.reducedMotion) freezes
  // the rotation; the lit gear is still shown (state readable), it just doesn't
  // spin. When idle (stopped + eased values settled, or reduced-motion) the rAF
  // stops entirely — a top-level $effect re-arms it when the run state changes,
  // so we never busy-spin a clear+redraw every frame.

  import { onMount } from 'svelte';
  import { browser } from '$app/environment';
  import { runStore } from '../stores/run.svelte';
  import { uiStore } from '../stores/ui.svelte';
  import type { SixPhase } from '../types';

  let canvas: HTMLCanvasElement;

  const PHASES: { key: SixPhase; label: string }[] = [
    { key: 'discover', label: 'discover' },
    { key: 'assemble', label: 'assemble' },
    { key: 'execute', label: 'execute' },
    { key: 'verify', label: 'verify' },
    { key: 'persist', label: 'persist' },
    { key: 'decide', label: 'decide' },
  ];

  // model spectral class → emitted gear color (matches tokens.css)
  function modelHex(m: string): string {
    if (m === 'haiku') return '#ff6a4d';
    if (m === 'opus') return '#9fd0ff';
    return '#c9a24b'; // sonnet / default
  }

  // settle epsilon for the eased lit/emit values — once everything is within this
  // of its target we can stop drawing (one final frame) and park the rAF.
  const SETTLE_EPS = 0.004;

  // exposed so the top-level $effect can re-arm the loop when run state changes.
  let arm: (() => void) | null = null;

  onMount(() => {
    if (!browser) return;
    const ctx = canvas.getContext('2d');
    if (!ctx) return;
    let raf = 0;
    let destroyed = false;

    // eased lit-intensity per phase (so the active gear lights/dims smoothly)
    const litEased = new Array(PHASES.length).fill(0);
    let emitCol = { r: 201, g: 162, b: 75 };
    let t = 0;

    function hexToRgb(hex: string) {
      const n = parseInt(hex.slice(1), 16);
      return { r: (n >> 16) & 255, g: (n >> 8) & 255, b: n & 255 };
    }
    function lerp(a: number, b: number, k: number) {
      return a + (b - a) * k;
    }

    function gearPath(cx: number, cy: number, rOuter: number, rInner: number, teeth: number, rot: number) {
      ctx!.beginPath();
      const steps = teeth * 2;
      for (let i = 0; i <= steps; i++) {
        const ang = rot + (i / steps) * Math.PI * 2;
        const r = i % 2 === 0 ? rOuter : rInner;
        const x = cx + Math.cos(ang) * r;
        const y = cy + Math.sin(ang) * r;
        i === 0 ? ctx!.moveTo(x, y) : ctx!.lineTo(x, y);
      }
      ctx!.closePath();
    }

    function resize() {
      const dpr = Math.min(window.devicePixelRatio || 1, 2);
      const rect = canvas.getBoundingClientRect();
      canvas.width = Math.max(1, Math.round(rect.width * dpr));
      canvas.height = Math.max(1, Math.round(rect.height * dpr));
      ctx!.setTransform(dpr, 0, 0, dpr, 0, 0);
      // a static resize still needs a redraw — re-arm so the new size paints.
      arm?.();
    }
    resize();
    const ro = new ResizeObserver(resize);
    ro.observe(canvas);

    // one drawing pass; returns whether the scene has settled (no more motion).
    const drawFrame = (): boolean => {
      const s = runStore.state;
      const running = s.run.status === 'running';
      const reduced = uiStore.reducedMotion;
      if (!reduced && running) t += 0.016;

      const target = hexToRgb(modelHex(runStore.model));
      emitCol.r = lerp(emitCol.r, target.r, 0.06);
      emitCol.g = lerp(emitCol.g, target.g, 0.06);
      emitCol.b = lerp(emitCol.b, target.b, 0.06);
      const emit = `rgb(${emitCol.r | 0},${emitCol.g | 0},${emitCol.b | 0})`;

      const rect = canvas.getBoundingClientRect();
      const w = rect.width || 200;
      const h = rect.height || 200;
      ctx!.clearRect(0, 0, w, h);

      const cx = w / 2;
      const gearR = Math.min(w, h) * 0.12;
      // Reserve room for the bottom gear-row LABELS (drawn at gy + gearR + 9):
      // shrink the ring and bias the center upward so the lowest gear + its label
      // clear the canvas edge with margin instead of clipping.
      const labelPad = 14; // label baseline + descenders below the lowest gear
      const ringR = (Math.min(w, h) - gearR * 2 - labelPad * 2) / 2;
      const cy = h / 2 - labelPad / 2;

      const activeIdx = PHASES.findIndex((p) => p.key === s.phase.sixPhase);

      // connecting ring (the train the gears mesh along)
      ctx!.beginPath();
      ctx!.arc(cx, cy, ringR, 0, Math.PI * 2);
      ctx!.strokeStyle = 'rgba(201,162,75,0.18)';
      ctx!.lineWidth = 1;
      ctx!.stroke();

      // central hub gear (the whole mechanism), slowly counter-rotating
      gearPath(cx, cy, gearR * 0.7, gearR * 0.5, 10, reduced ? 0 : -t * 0.2);
      ctx!.strokeStyle = 'rgba(234,240,255,0.14)';
      ctx!.lineWidth = 1.2;
      ctx!.stroke();
      ctx!.beginPath();
      ctx!.arc(cx, cy, gearR * 0.22, 0, Math.PI * 2);
      ctx!.fillStyle = running ? emit : 'rgba(234,240,255,0.18)';
      ctx!.globalAlpha = running ? 0.5 : 0.3;
      ctx!.fill();
      ctx!.globalAlpha = 1;

      let settled = true; // becomes false if any eased value is still moving

      for (let i = 0; i < PHASES.length; i++) {
        const ang = (i / PHASES.length) * Math.PI * 2 - Math.PI / 2;
        const gx = cx + Math.cos(ang) * ringR;
        const gy = cy + Math.sin(ang) * ringR;
        const isActive = i === activeIdx;
        const litTarget = isActive && running ? 1 : 0;
        litEased[i] = lerp(litEased[i], litTarget, 0.1);
        if (Math.abs(litEased[i] - litTarget) > SETTLE_EPS) settled = false;
        const lit = litEased[i];

        // gear teeth — active gear meshes (rotates), others idle
        const rot = reduced ? 0 : (isActive ? t * 0.9 : t * 0.06 * (i % 2 ? 1 : -1));
        gearPath(gx, gy, gearR, gearR * 0.72, 9, rot);
        // emitted glow when lit
        if (lit > 0.02) {
          ctx!.shadowColor = emit;
          ctx!.shadowBlur = 14 * lit;
        }
        ctx!.strokeStyle = lit > 0.02
          ? `rgba(${emitCol.r | 0},${emitCol.g | 0},${emitCol.b | 0},${0.45 + lit * 0.5})`
          : 'rgba(234,240,255,0.16)';
        ctx!.lineWidth = 1.2 + lit * 1.4;
        ctx!.stroke();
        ctx!.shadowBlur = 0;

        // gear core
        ctx!.beginPath();
        ctx!.arc(gx, gy, gearR * 0.42, 0, Math.PI * 2);
        ctx!.fillStyle = lit > 0.02
          ? `rgba(${emitCol.r | 0},${emitCol.g | 0},${emitCol.b | 0},${0.18 + lit * 0.5})`
          : 'rgba(20,24,44,0.85)';
        ctx!.fill();

        // label
        ctx!.font = '9px "JetBrains Mono", ui-monospace, monospace';
        ctx!.textAlign = 'center';
        ctx!.textBaseline = 'middle';
        ctx!.fillStyle = lit > 0.2 ? 'rgba(234,240,255,0.95)' : 'rgba(234,240,255,0.4)';
        ctx!.fillText(PHASES[i].label, gx, gy + gearR + 9);
      }

      // emit color still drifting toward its target?
      const dr = Math.abs(emitCol.r - target.r);
      const dg = Math.abs(emitCol.g - target.g);
      const db = Math.abs(emitCol.b - target.b);
      if (dr + dg + db > 1) settled = false;

      return settled;
    };

    const tick = () => {
      if (destroyed) return;
      const settled = drawFrame();
      const running = runStore.state.run.status === 'running';
      // Spin only while there is genuine motion: a running, non-reduced loop, or
      // eased values still resolving. Otherwise draw the final frame and park.
      if ((running && !uiStore.reducedMotion) || !settled) {
        raf = requestAnimationFrame(tick);
      } else {
        raf = 0;
      }
    };

    // (re)start the loop if it isn't already running.
    arm = () => {
      if (destroyed || raf) return;
      raf = requestAnimationFrame(tick);
    };
    arm();

    return () => {
      destroyed = true;
      arm = null;
      if (raf) cancelAnimationFrame(raf);
      ro.disconnect();
    };
  });

  // Re-arm the rAF whenever the inputs that drive the drawing change — run status
  // (start/stop), active model, six-phase, and reduced-motion. When the loop has
  // parked itself this restarts it; while it's already spinning this is a no-op.
  $effect(() => {
    // touch the reactive deps so the effect re-runs on any change
    void runStore.state.run.status;
    void runStore.model;
    void runStore.state.phase.sixPhase;
    void uiStore.reducedMotion;
    arm?.();
  });
</script>

<div class="mechanism" class:phone={uiStore.isPhone}>
  <canvas bind:this={canvas} aria-hidden="true"></canvas>
</div>

<style>
  .mechanism {
    position: absolute;
    /* dock above the cost/quota strip with a clear gutter so the bottom gear
       row + its labels never tuck under the strip. */
    bottom: calc(var(--strip-h) + var(--space-4));
    right: var(--chrome-inset);
    width: 190px;
    height: 190px;
    pointer-events: none;
    opacity: 0.96;
  }
  /* phone (Tier-1): shrink so it doesn't crowd the ambient view */
  .mechanism.phone {
    width: 150px;
    height: 150px;
  }
  /* Short viewport: shrink so the stacked MetricsPanel above still fits without
     pushing off-screen (MetricsPanel mirrors this 150px in its own offset). */
  @media (max-height: 760px) {
    .mechanism {
      width: 150px;
      height: 150px;
    }
  }
  /* Very short viewport: drop the Mechanism entirely so RUN QUALITY stays
     readable (MetricsPanel re-docks above the cost strip at this height). */
  @media (max-height: 600px) {
    .mechanism {
      display: none;
    }
  }
  .mechanism canvas {
    width: 100%;
    height: 100%;
    display: block;
  }
</style>
