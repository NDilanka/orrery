<script lang="ts">
  // MetricsPanel — the run-quality report card (engine-v3 `metrics` event, §2).
  // A calm, glance-first readout of how WELL the loop ran, not just whether it
  // finished: first-try-green, iterations + dollars to green, rollbacks and the
  // regression rate. These replace pass@k for an iterative agent loop (a single
  // learning trajectory, not k independent draws).
  //
  // Reads runStore.state.metrics; renders a muted placeholder until the engine
  // emits the summary (it lands once, at stop). camelCase-on-the-wire shape.

  import { runStore } from '../stores/run.svelte';

  const m = $derived(runStore.state.metrics);

  function fmtUsd(n: number | null): string {
    return n == null ? '—' : '$' + n.toFixed(2);
  }
  function fmtPct(n: number): string {
    return Math.round(n * 100) + '%';
  }
  function fmtInt(n: number | null): string {
    return n == null ? '—' : String(n);
  }
</script>

<section class="metrics" aria-labelledby="metrics-heading">
  <h2 id="metrics-heading" class="mhead">RUN QUALITY</h2>

  {#if m}
    <div class="grid">
      <div class="cell first {m.firstTryGreen ? 'good' : 'miss'}">
        <span class="mlabel">first-try green</span>
        <span class="mval" aria-label={m.firstTryGreen ? 'yes' : 'no'}>
          {m.firstTryGreen ? '✓' : '✗'}
        </span>
      </div>
      <div class="cell">
        <span class="mlabel">iters → green</span>
        <span class="mval num">{fmtInt(m.itersToGreen)}</span>
      </div>
      <div class="cell">
        <span class="mlabel">cost → green</span>
        <span class="mval num">{fmtUsd(m.costToGreen)}</span>
      </div>
      <div class="cell">
        <span class="mlabel">rollbacks</span>
        <span class="mval num {m.rollbacks > 0 ? 'warn' : ''}">{m.rollbacks}</span>
      </div>
      <div class="cell">
        <span class="mlabel">regression rate</span>
        <span class="mval num {m.regressionRate > 0 ? 'warn' : ''}">{fmtPct(m.regressionRate)}</span>
      </div>
      <div class="cell {m.finalGreen ? 'good' : 'miss'}">
        <span class="mlabel">final</span>
        <span class="mval num">{m.finalGreen ? 'green' : 'red'}</span>
      </div>
    </div>
    <div class="foot mono">
      {m.totalIters} iters · {fmtUsd(m.totalCost)} total
    </div>

    <!-- TODO(lessons): the engine's cross-run lessons live in a side memory.jsonl,
         not the event stream — surfacing them needs a new protocol event. A lessons
         panel would mount here once that event exists (out of scope; see report). -->
  {:else}
    <p class="placeholder mono">no run-quality summary yet — emitted once at stop.</p>
  {/if}
</section>

<style>
  .metrics {
    position: absolute;
    /* clear the 120px cost/quota strip pinned to the bottom edge */
    bottom: calc(var(--strip-h) + var(--space-4));
    right: var(--chrome-inset);
    width: 248px;
    display: flex;
    flex-direction: column;
    gap: var(--space-3);
    padding: var(--space-4);
    background: var(--panel);
    border: 1px solid var(--panel-edge);
    border-radius: var(--radius);
    backdrop-filter: blur(8px);
    z-index: 10;
    font-size: var(--text-sm);
  }
  .mhead {
    margin: 0;
    font-size: var(--text-2xs);
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.14em;
    color: var(--text-meta);
  }
  .grid {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 9px 12px;
  }
  .cell {
    display: flex;
    flex-direction: column;
    gap: 3px;
  }
  .mlabel {
    font-size: var(--text-2xs);
    text-transform: uppercase;
    letter-spacing: 0.1em;
    color: var(--text-meta);
  }
  .mval {
    font-size: 15px;
    font-weight: 600;
    color: var(--starlight);
    line-height: 1;
  }
  .mval.num {
    font-family: var(--num, var(--font-mono));
  }
  .mval.warn {
    /* warning is ONE hue across System view; --amber is reserved elsewhere */
    color: var(--horizon-rose);
  }
  .cell.good .mval {
    color: var(--plasma-green);
  }
  .cell.miss .mval {
    color: var(--crimson);
  }
  .foot {
    font-size: var(--text-2xs);
    color: var(--text-meta);
    border-top: 1px solid var(--hairline);
    padding-top: var(--space-2);
  }
  .placeholder {
    font-size: var(--text-xs);
    color: var(--text-meta);
    line-height: 1.4;
    margin: 0;
  }
  /* the panel is static; no animation to respect, but keep the contract explicit */
  @media (prefers-reduced-motion: reduce) {
    .metrics {
      backdrop-filter: none;
    }
  }
  /* Tier-1 / phone: become a full-width bottom sheet instead of a fixed card */
  @media (max-width: 640px) {
    .metrics {
      left: var(--space-2);
      right: var(--space-2);
      width: auto;
    }
  }
</style>
