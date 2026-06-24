<script lang="ts">
  // ObservatoryLabels — an absolutely-positioned, pointer-events:none HTML overlay
  // that sits OVER the Pixi canvas (in Observatory's .ofield) and makes the scene
  // legible in isolation. It is deliberately restrained / low-contrast so it never
  // fights the rendered instrument, and it works in Planetarium (Tier-1) too.
  //
  // It draws three things, all in host CSS px (the same coordinate space the rAF
  // resolves):
  //   · a small caption just under the star — cumulative spend + the per-minute
  //     rate (tabular figures, --text-meta);
  //   · the active work item's key near its planet (clamped in-bounds, ellipsized);
  //   · an optional tiny 50/80/100% tick label on the cost-horizon ring.
  //
  // Positions arrive pre-computed/THROTTLED from the rAF (so we don't thrash
  // reactivity). Under reduced motion we place statically — no transitions.

  type Labels = {
    cumUsd: number;
    ratePerMin: number;
    star: { x: number; y: number };
    active: { name: string; x: number; y: number } | null;
    horizonPct: number | null;
  };

  let { labels, reduced }: { labels: Labels; reduced: boolean } = $props();

  function fmtUsd(n: number): string {
    return '$' + (n ?? 0).toFixed(2);
  }

  // the horizon tick reads at its nearest milestone band (50 / 80 / 100%)
  const horizonBand = $derived.by<{ pct: number; cls: string } | null>(() => {
    const p = labels.horizonPct;
    if (p == null || p < 50) return null;
    if (p >= 100) return { pct: 100, cls: 'crit' };
    if (p >= 80) return { pct: 80, cls: 'warn' };
    return { pct: 50, cls: 'note' };
  });
</script>

<div class="olabels" class:reduced aria-hidden="true">
  <!-- spend + rate caption, pinned just under the star core -->
  <div
    class="cap num"
    style="left:{labels.star.x}px; top:{labels.star.y}px;"
  >
    <span class="usd">{fmtUsd(labels.cumUsd)}</span>
    {#if labels.ratePerMin > 0}
      <span class="rate">{fmtUsd(labels.ratePerMin)}/min</span>
    {/if}
  </div>

  <!-- the active work item, named at its planet -->
  {#if labels.active}
    <div
      class="active mono"
      style="left:{labels.active.x}px; top:{labels.active.y}px;"
      title={labels.active.name}
    >
      {labels.active.name}
    </div>
  {/if}

  <!-- a tiny horizon milestone tick (50/80/100%) -->
  {#if horizonBand}
    <div
      class="horizon num {horizonBand.cls}"
      style="left:{labels.star.x}px; top:{labels.star.y}px;"
    >
      {horizonBand.pct}%
    </div>
  {/if}
</div>

<style>
  .olabels {
    position: absolute;
    inset: 0;
    pointer-events: none;
    z-index: 2;
    /* low contrast so the labels read as instrument annotation, not chrome */
    font-family: var(--font-grotesk);
  }

  /* spend + rate, centred under the star core */
  .cap {
    position: absolute;
    transform: translate(-50%, 18px);
    display: flex;
    align-items: baseline;
    gap: var(--space-2);
    white-space: nowrap;
    font-size: var(--text-meta, 11px);
    transition: left var(--dur-mid) var(--ease-standard),
      top var(--dur-mid) var(--ease-standard);
  }
  .cap .usd {
    color: var(--brass);
    font-size: var(--text-sm);
    letter-spacing: 0.02em;
  }
  .cap .rate {
    color: var(--text-meta);
    font-size: var(--text-2xs);
  }

  /* the active item's key, floated just above-right of its planet */
  .active {
    position: absolute;
    transform: translate(-50%, calc(-100% - 12px));
    max-width: 168px;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    font-size: var(--text-2xs);
    letter-spacing: 0.08em;
    color: var(--text-meta);
    text-shadow: 0 1px 3px var(--void);
    transition: left var(--dur-mid) var(--ease-standard),
      top var(--dur-mid) var(--ease-standard);
  }

  /* horizon milestone tick — placed top-of-star, recessed */
  .horizon {
    position: absolute;
    transform: translate(-50%, -34px);
    font-size: var(--text-2xs);
    letter-spacing: 0.1em;
    color: var(--text-faint);
    transition: left var(--dur-mid) var(--ease-standard),
      top var(--dur-mid) var(--ease-standard);
  }
  .horizon.note { color: var(--amber); }
  .horizon.warn { color: var(--horizon-rose); }
  .horizon.crit { color: var(--crimson); }

  /* reduced motion: place statically, no easing of position */
  .olabels.reduced .cap,
  .olabels.reduced .active,
  .olabels.reduced .horizon {
    transition: none;
  }
</style>
