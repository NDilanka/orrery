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

  let host: HTMLDivElement;

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
      const brass = cssVar('--brass', '#c9a24b');
      const cyan = cssVar('--plasma-cyan', '#46e0ff');
      const frost = cssVar('--frost', '#9fb6ff');
      const dim = cssVar('--text-faint', 'rgba(234,240,255,0.34)');

      // quota-band plugin: paint a frost wash over x-ranges that were waiting.
      let quotaBands: [number, number][] = [];
      const bandPlugin = {
        hooks: {
          draw: (self: any) => {
            if (!quotaBands.length) return;
            const ctx = self.ctx;
            ctx.save();
            ctx.fillStyle = 'rgba(159,182,255,0.10)';
            for (const [x0, x1] of quotaBands) {
              const cx0 = self.valToPos(x0, 'x', true);
              const cx1 = self.valToPos(x1, 'x', true);
              ctx.fillRect(cx0, self.bbox.top, Math.max(1, cx1 - cx0), self.bbox.height);
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
            stroke: dim,
            grid: { stroke: 'rgba(234,240,255,0.05)', width: 1 },
            ticks: { stroke: 'rgba(234,240,255,0.08)' },
            font: '10px JetBrains Mono, monospace',
          },
          {
            scale: '$',
            stroke: brass,
            grid: { stroke: 'rgba(234,240,255,0.05)', width: 1 },
            font: '10px JetBrains Mono, monospace',
            size: 42,
            values: (_self: any, ticks: number[]) => ticks.map((v) => '$' + v.toFixed(0)),
          },
          {
            scale: 'rate',
            side: 1,
            stroke: cyan,
            grid: { show: false },
            font: '10px JetBrains Mono, monospace',
            size: 40,
            values: (_self: any, ticks: number[]) => ticks.map((v) => v.toFixed(1)),
          },
        ],
        series: [
          {},
          {
            label: 'cum $',
            scale: '$',
            stroke: brass,
            width: 1.5,
            fill: 'rgba(201,162,75,0.16)',
            points: { show: false },
          },
          {
            label: '$/sample',
            scale: 'rate',
            stroke: cyan,
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

        // quota bands: if currently waiting, shade the tail of the window.
        quotaBands = runStore.state.quota.active
          ? [[Math.max(0, xs.length - 2), xs.length - 1]]
          : [];

        u.setData([xs, cum, rate]);
      }

      // poll the runes store (cheap; data rate is tiny). $effect can't reach
      // into a non-component module cleanly here, so a light interval re-syncs.
      rebuild();
      const iv = setInterval(rebuild, 200);

      const onResize = () => {
        if (u) u.setSize({ width: host.clientWidth || 600, height: host.clientHeight || 96 });
      };
      const ro = new ResizeObserver(onResize);
      ro.observe(host);

      cleanup = () => {
        clearInterval(iv);
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
</script>

<div class="strip">
  <div class="hdr mono">COST · QUOTA</div>
  <div bind:this={host} class="plot"></div>
</div>

<style>
  .strip {
    position: absolute;
    bottom: 0;
    left: 0;
    right: 0;
    height: 120px;
    padding: 6px 10px 8px;
    background: linear-gradient(to top, var(--void-2), transparent);
    border-top: 1px solid var(--hairline);
    pointer-events: none;
  }
  .hdr {
    font-size: 9px;
    letter-spacing: 0.18em;
    color: var(--text-faint);
    margin-bottom: 2px;
  }
  .plot {
    width: 100%;
    height: 92px;
    pointer-events: auto;
  }
  /* uPlot dark theme tweaks */
  .plot :global(.u-legend) {
    display: none;
  }
</style>
