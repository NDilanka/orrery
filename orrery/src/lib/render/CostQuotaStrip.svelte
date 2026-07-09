<script lang="ts">
  // Cost / Quota strip — a uPlot time-series along the bottom edge: the cumulative
  // spend as a filled area (the multi-run sawtooth survives `start` resets), a
  // derived rate-per-sample line on a second axis, and quota-window shading where
  // the run was waiting out the night. CLIENT-ONLY: uPlot is dynamically imported
  // and guarded with `browser`, so it never runs at build/SSR/prerender time.
  //
  // The cost `series` carries synthetic timestamps (line-index × 1000 in replay);
  // we plot against the sample index so the curve is stable and legible. Quota
  // bands are inferred from the active quota window in the reduced state.

  import { onMount } from 'svelte';
  import { browser } from '$app/environment';
  import { runStore } from '../stores/run.svelte';
  import { uiStore } from '../stores/ui.svelte';
  import { resolveVarRgbTriple } from '../theme';

  let host: HTMLDivElement;

  // A run that is clearly active but reports zero dollar cost is almost always a Claude
  // SUBSCRIPTION run (the agent CLI emits no per-call USD), so the $ curve sits flat at $0 and
  // reads as "broken". Surface an honest note instead — the real usage is the subscription quota.
  const s = $derived(runStore.state);
  const hasCost = $derived(s.run.cumUsd > 0 || s.cost.series.some((p) => p.cum > 0));
  // Empty-state PRECEDENCE (the two never both show):
  //   1. awaiting-first-spend — very early, no cost yet (cold start, ≤3 events).
  //   2. subscription note    — >3 events and STILL no cost ⇒ a subscription run.
  //   3. the real curve.
  const awaitingFirstSpend = $derived(!hasCost && s.events <= 3);
  // Require a few events first so the note doesn't flash on a metered run's cold start (which is
  // legitimately $0 until the first cost-emitting milestone). A subscription run never crosses it.
  const noCostYet = $derived(!hasCost && s.events > 3);

  // a live status dot for the header, mirroring the LIVE LOG / RUN QUALITY siblings.
  const running = $derived(s.run.status === 'running');

  // arm()'d by the top-level $effect below so live cost keeps updating after a
  // quota-wait/resume even though the polling interval pauses while idle.
  let arm: (() => void) | null = null;

  onMount(() => {
    if (!browser) return;
    let u: any = null;
    let destroyed = false;
    let cleanup: (() => void) | null = null;

    (async () => {
      const uPlot = (await import('uplot')).default;
      // uPlot ships its own CSS; import it so axes/grid render.
      await import('uplot/dist/uPlot.min.css');
      if (destroyed) return;

      const cssVar = (name: string, fallback: string) => {
        const v = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
        return v || fallback;
      };
      // M4.5 monochrome sweep: the cum-$ curve and its axis go light-gray
      // (em-hi), the $/min rate curve/axis go mid-gray (em-mid) — the only
      // chromatic pixels left on this strip are the quota-wait window (warn).
      const emHi = cssVar('--em-hi', '#eaf0ff');
      const emMid = cssVar('--em-mid', 'rgba(234,240,255,0.7)');
      const warn = cssVar('--status-warn-core', '#e8b64a');
      // state-bearing axis labels live at --text-meta (AA on the void), ≥11px.
      const meta = cssVar('--text-meta', 'rgba(234,240,255,0.66)');
      const axisFont = '11px JetBrains Mono, monospace';

      // Grid/tick/quota-wash colors used to be raw rgba literals that silently drifted from
      // tokens.css on any token change (audit #11) — resolve the same "r, g, b" triples the
      // literals were hand-copied from (theme.ts's probe-based resolver, shared with the Pixi
      // color bridge) and re-apply the SAME alphas, so a token edit propagates here too.
      const gridRgb = resolveVarRgbTriple('--starlight') ?? '234, 240, 255';
      const emHiRgb = resolveVarRgbTriple('--em-hi') ?? '234, 240, 255';
      const warnRgb = resolveVarRgbTriple('--status-warn-core') ?? '232, 182, 74';

      // quota-band plugin: paint a warn-amber wash over x-ranges that were
      // waiting — a quota night IS a genuine attention window, so it's the one
      // chromatic band on an otherwise monochrome strip — with a top hairline
      // so it's clearly legible (not a faint tint).
      let quotaBands: [number, number][] = [];
      const bandPlugin = {
        hooks: {
          draw: (self: any) => {
            if (!quotaBands.length) return;
            const ctx = self.ctx;
            ctx.save();
            for (const [x0, x1] of quotaBands) {
              const cx0 = self.valToPos(x0, 'x', true);
              const cx1 = self.valToPos(x1, 'x', true);
              const w = Math.max(1, cx1 - cx0);
              // raised wash so the waiting window reads at a glance
              ctx.fillStyle = `rgba(${warnRgb}, 0.18)`;
              ctx.fillRect(cx0, self.bbox.top, w, self.bbox.height);
              // top hairline in warn-amber to crown the band
              ctx.fillStyle = warn;
              ctx.globalAlpha = 0.6;
              ctx.fillRect(cx0, self.bbox.top, w, 1);
              ctx.globalAlpha = 1;
            }
            ctx.restore();
          },
        },
      };

      const opts = {
        width: host.clientWidth || 600,
        height: host.clientHeight || 96,
        padding: [6, 8, 0, 8] as [number, number, number, number],
        cursor: { show: true, y: false },
        legend: { show: false },
        scales: {
          x: { time: false },
          $: { auto: true },
          rate: { auto: true },
        },
        axes: [
          {
            stroke: meta,
            grid: { stroke: `rgba(${gridRgb}, 0.05)`, width: 1 },
            ticks: { stroke: `rgba(${gridRgb}, 0.08)` },
            font: axisFont,
            // x is the sample INDEX, so ticks MUST be unique integers. uPlot's
            // default chooses fractional increments over a small range (giving
            // 0,1,1,2,2,3 once Math.round() collapsed them) — so pin the tick
            // increment to a whole-number ladder. uPlot walks `incrs` to find the
            // smallest step whose pixel spacing clears its min-tick gap, so every
            // candidate (1,2,5,…) is an integer ⇒ gridlines land only on integers.
            incrs: [1, 2, 5, 10, 25, 50, 100, 250, 500, 1000],
            // Guard the degenerate axis: a 0- or 1-sample series spans [0,0] with
            // no real increment, so uPlot can emit a NaN/duplicate split. Pin a
            // single tick at 0 in that case; otherwise let `incrs` drive splits.
            splits: (_self: any, _axisIdx: number, scaleMin: number, scaleMax: number, foundIncr: number, foundSpace: number) => {
              if (!(scaleMax > scaleMin) || !isFinite(foundIncr) || foundIncr <= 0) {
                return [Math.round(scaleMin) || 0];
              }
              const start = Math.ceil(scaleMin / foundIncr) * foundIncr;
              const out: number[] = [];
              for (let v = start; v <= scaleMax + 1e-9; v += foundIncr) out.push(Math.round(v));
              return out.length ? out : [Math.round(scaleMin) || 0];
            },
            // Integer formatter — values are already whole, so print with no
            // decimals (Math.round() also strips any -0 / float drift to a clean int).
            values: (_self: any, ticks: number[]) => ticks.map((v) => '' + Math.round(v)),
          },
          {
            scale: '$',
            stroke: emHi,
            grid: { stroke: `rgba(${gridRgb}, 0.05)`, width: 1 },
            font: axisFont,
            size: 42,
            values: (_self: any, ticks: number[]) => ticks.map((v) => '$' + v.toFixed(0)),
          },
          {
            scale: 'rate',
            side: 1,
            stroke: emMid,
            grid: { show: false },
            font: axisFont,
            size: 40,
            values: (_self: any, ticks: number[]) => ticks.map((v) => v.toFixed(1)),
          },
        ],
        series: [
          {},
          {
            label: 'cum $',
            scale: '$',
            stroke: emHi,
            width: 1.5,
            fill: `rgba(${emHiRgb}, 0.1)`,
            points: { show: false },
          },
          {
            label: '$/sample',
            scale: 'rate',
            stroke: emMid,
            width: 1,
            dash: [4, 3],
            points: { show: false },
          },
        ],
        plugins: [bandPlugin],
      };

      u = new uPlot(opts, [[0], [0], [0]], host);

      function rebuild() {
        if (!u || destroyed) return;
        const series = runStore.state.cost.series;
        const xs: number[] = [];
        const cum: number[] = [];
        const rate: number[] = [];
        for (let i = 0; i < series.length; i++) {
          xs.push(i);
          cum.push(series[i].cum);
          // per-sample delta (a legible proxy for spend velocity)
          rate.push(i === 0 ? 0 : Math.max(0, series[i].cum - series[i - 1].cum));
        }
        if (!xs.length) {
          xs.push(0);
          cum.push(0);
          rate.push(0);
        }

        // quota bands: if currently waiting, shade the full active window (the
        // tail samples since the wait began) so the wait reads clearly.
        quotaBands = runStore.state.quota.active
          ? [[Math.max(0, xs.length - 3), xs.length - 1]]
          : [];

        u.setData([xs, cum, rate]);
      }

      // Live re-sync. We poll the runes store on a light interval while the run is
      // ACTIVE (data rate is tiny), but PAUSE the interval when the run is stopped/
      // banked or under reduced-motion so we don't busy-poll forever. The top-level
      // $effect re-arms us (one-shot rebuild + restart the interval if live again)
      // when s.cost.series.length or s.run.status changes — so live cost STILL
      // updates after a quota-wait/resume even though the interval was parked.
      let iv: ReturnType<typeof setInterval> | null = null;
      const stopPolling = () => {
        if (iv) {
          clearInterval(iv);
          iv = null;
        }
      };
      arm = () => {
        if (destroyed || !u) return;
        rebuild(); // one-shot so the latest sample paints immediately
        const live = runStore.state.run.status === 'running' && !uiStore.reducedMotion;
        if (live) {
          if (!iv) iv = setInterval(rebuild, 200);
        } else {
          stopPolling();
        }
      };
      arm();

      const onResize = () => {
        if (u) u.setSize({ width: host.clientWidth || 600, height: host.clientHeight || 96 });
      };
      const ro = new ResizeObserver(onResize);
      ro.observe(host);

      cleanup = () => {
        stopPolling();
        arm = null;
        ro.disconnect();
      };
    })();

    return () => {
      destroyed = true;
      cleanup?.();
      if (u) {
        try {
          u.destroy();
        } catch {
          /* ignore */
        }
        u = null;
      }
    };
  });

  // Re-arm the polling loop whenever new cost samples arrive or the run status
  // flips. This keeps live cost flowing after a quota-wait/resume (when the idle
  // pause had stopped the interval) without ever busy-spinning while stopped.
  $effect(() => {
    void s.cost.series.length;
    void s.run.status;
    void uiStore.reducedMotion;
    arm?.();
  });
</script>

<div class="strip">
  <div class="hdr-row">
    <div class="hdr mono" role="heading" aria-level="2">
      <span class="dot" class:live={running} aria-hidden="true"></span>
      COST · QUOTA
    </div>
    <!-- compact inline key for the two unlabeled series -->
    <div class="key mono" aria-hidden="true">
      <span class="ks"><span class="sw sw-cum"></span>$ cum</span>
      <span class="ks"><span class="sw sw-rate"></span>$/min</span>
    </div>
  </div>
  <div bind:this={host} class="plot"></div>
  {#if awaitingFirstSpend}
    <div class="nocost mono" role="status">awaiting first spend…</div>
  {:else if noCostYet}
    <div class="nocost mono" role="status">
      no $ metering — this run reports no per-call cost (e.g. a Claude subscription); usage shows in
      your plan's quota, not here
    </div>
  {/if}
</div>

<style>
  .strip {
    /* wave U2 Task 1: the System dock's bottom-most full-width row (grid-area:
       strip) instead of a viewport-pinned absolute bar — this is internal
       styling only now. */
    position: relative;
    width: 100%;
    height: var(--strip-h);
    box-sizing: border-box;
    padding: var(--space-2) var(--space-3) var(--space-2);
    background: linear-gradient(to top, var(--void-2), transparent);
    border-top: 1px solid var(--hairline);
  }
  .hdr-row {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: var(--space-3);
    margin-bottom: var(--space-1);
  }
  /* boxed-mono header to match the LIVE LOG / RUN QUALITY siblings */
  .hdr {
    display: inline-flex;
    align-items: center;
    gap: var(--space-2);
    font-size: var(--text-xs); /* ≥11px */
    letter-spacing: 0.16em;
    color: var(--text-meta);
  }
  /* leading status dot — steady when idle, soft pulse only while running */
  .dot {
    width: 7px;
    height: 7px;
    border-radius: 50%;
    background: var(--text-faint);
    flex: none;
  }
  .dot.live {
    /* M4.5: the live dot is white/gray, not an identity hue */
    background: var(--em-hi);
    box-shadow: 0 0 6px var(--em-hi);
    animation: dotpulse 1.8s ease-in-out infinite;
  }
  @keyframes dotpulse {
    0%, 100% { opacity: 1; }
    50% { opacity: 0.4; }
  }
  @media (prefers-reduced-motion: reduce) {
    :global(:root:not([data-motion='full'])) .dot.live { animation: none; }
  }
  /* mirrors the media block above, for the user-forced reduced-motion setting */
  :global(:root[data-motion='reduced']) .dot.live { animation: none; }
  /* inline legend key */
  .key {
    display: inline-flex;
    align-items: center;
    gap: var(--space-3);
    font-size: var(--text-2xs);
    color: var(--text-meta);
  }
  .ks {
    display: inline-flex;
    align-items: center;
    gap: var(--space-1);
  }
  .sw {
    width: 14px;
    height: 0;
    flex: none;
  }
  /* light-gray swatch = cumulative $ area (was brass-tinted) */
  .sw-cum {
    height: 8px;
    border-radius: 1px;
    background: color-mix(in srgb, var(--em-hi) 14%, transparent);
    border-top: 1.5px solid var(--em-hi);
  }
  /* mid-gray dashed swatch = $/min rate (was cyan-tinted) */
  .sw-rate {
    height: 0;
    border-top: 1.5px dashed var(--em-mid);
  }
  .plot {
    width: 100%;
    height: 84px;
    pointer-events: auto;
  }
  /* empty-state overlay — centered over the flat plot. Precedence is enforced in
     markup: awaiting-first-spend (cold start) → subscription note → real curve. */
  .nocost {
    position: absolute;
    left: 0;
    right: 0;
    bottom: 38px;
    text-align: center;
    font-size: var(--text-xs);
    line-height: 1.4;
    color: var(--text-meta);
    padding: 0 16%;
    pointer-events: none;
  }
  /* uPlot dark theme tweaks */
  .plot :global(.u-legend) {
    display: none;
  }
</style>
