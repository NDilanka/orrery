<script lang="ts">
  // MetricsPanel — the run-quality report card (engine-v3 `metrics` event, §2).
  // A calm, glance-first readout of how WELL the loop ran, not just whether it
  // finished: first-try-green, iterations + dollars to green, rollbacks and the
  // regression rate. These replace pass@k for an iterative agent loop (a single
  // learning trajectory, not k independent draws).
  //
  // Reads runStore.state.metrics; renders a muted placeholder until the engine
  // emits its first `metrics` event. The engine (when metrics are enabled) now
  // emits one after EVERY iteration, not just at stop — the reducer is
  // last-write-wins, so this panel simply reflects the latest partial fold
  // live and settles on the final report at stop. camelCase-on-the-wire shape.

  import { runStore } from '../stores/run.svelte';

  const m = $derived(runStore.state.metrics);
  // BMAD flavor of the `metrics` event — a DIFFERENT field set (pipeline counters) than the
  // generic-loop `metrics` above. When it arrived we render its shape; a run only ever emits one
  // flavor, so both are never present at once (BMAD wins the {#if} if it somehow is).
  const bm = $derived(runStore.state.bmadMetrics);

  function fmtUsd(n: number | null): string {
    return n == null ? '—' : '$' + n.toFixed(2);
  }
  function fmtPct(n: number): string {
    return Math.round(n * 100) + '%';
  }
  function fmtInt(n: number | null): string {
    return n == null ? '—' : String(n);
  }
  function fmtDur(sec: number): string {
    const s = Math.max(0, Math.round(sec));
    if (s < 60) return `${s}s`;
    const min = Math.floor(s / 60);
    if (min < 60) return `${min}m`;
    return `${Math.floor(min / 60)}h${String(min % 60).padStart(2, '0')}m`;
  }
</script>

<section class="metrics panel" aria-labelledby="metrics-heading">
  <h2 id="metrics-heading" class="panel-hd">RUN QUALITY</h2>

  {#if bm}
    <!-- BMAD-flavored run summary (pipeline counters). Only the fields the event carries are
         shown; nothing is fabricated for a flavor that didn't arrive. -->
    <div class="grid">
      <div class="cell good">
        <span class="mlabel">stories done</span>
        <span class="mval num">{bm.storiesCompleted}</span>
      </div>
      <div class="cell {bm.storiesHalted > 0 ? 'miss' : ''}">
        <span class="mlabel">halted</span>
        <span class="mval num">{bm.storiesHalted}</span>
      </div>
      <div class="cell">
        <span class="mlabel">PRs merged</span>
        <span class="mval num">{bm.prsMerged}/{bm.prsCreated}</span>
      </div>
      <div class="cell">
        <span class="mlabel">verifies</span>
        <span class="mval num">{bm.verifies}</span>
      </div>
      <div class="cell">
        <span class="mlabel">plan checks</span>
        <span class="mval num">{bm.planChecks}</span>
      </div>
      <div class="cell">
        <span class="mlabel">gate reds</span>
        <span class="mval num {bm.gateReds > 0 ? 'warn' : ''}">{bm.gateReds}</span>
      </div>
    </div>
    <div class="foot mono">
      {bm.devGates} dev gates · {fmtUsd(bm.cumUsd)} · {fmtDur(bm.durationSec)} · cache {fmtPct(
        bm.hitRatio,
      )}{#if bm.flakyRetries > 0} · {bm.flakyRetries} flaky{/if}{#if bm.quotaWaits > 0} · {bm.quotaWaits}
        quota wait{bm.quotaWaits === 1 ? '' : 's'}{/if}
    </div>
  {:else if m}
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
    <!-- M1.5: a subtle skeleton-shimmer placeholder alongside the explanatory text, so the
         panel reads as "waiting for data" rather than blank while the loop spins up. -->
    <div class="grid" aria-hidden="true">
      {#each Array(6) as _, i (i)}
        <div class="cell">
          <span class="skel-bar skel-label"></span>
          <span class="skel-bar skel-val"></span>
        </div>
      {/each}
    </div>
    <p class="placeholder mono">
      no run-quality data yet — updates each iteration when metrics are enabled; final report at
      stop.
    </p>
  {/if}
</section>

<style>
  .metrics {
    /* wave U2 Task 1: docked in the right rail (a scrollable flex column with
       VerdictPanel/QAConsole) — the grid places it, this is internal styling only.
       M4.5: card chrome (padding/border/radius/background) now comes from the
       shared `.panel` primitive (primitives.css) — this class only carries the
       flex-column layout on top of it. */
    width: 100%;
    flex: none;
    box-sizing: border-box;
    display: flex;
    flex-direction: column;
    gap: var(--space-3);
    font-size: var(--text-sm);
  }
  .grid {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: var(--space-2) var(--space-3);
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
    font-size: var(--text-lg);
    font-weight: 600;
    color: var(--em-hi);
    line-height: 1;
  }
  .mval.num {
    font-family: var(--num, var(--font-mono));
  }
  .mval.warn {
    /* warning is ONE hue across System view — now enforced by the two-tier status
       system itself (--status-warn-core) rather than a hand-picked literal; --amber
       stays reserved for the identity/override uses elsewhere. */
    color: var(--status-warn-core);
  }
  .cell.good .mval {
    color: var(--status-ok-core);
  }
  .cell.miss .mval {
    color: var(--status-err-core);
  }
  .foot {
    /* meta aside (a running total, not a value) → em-faint */
    font-size: var(--text-2xs);
    color: var(--text-faint);
    border-top: 1px solid var(--hairline);
    padding-top: var(--space-2);
  }
  .placeholder {
    /* explanatory body sentence → em-mid */
    font-size: var(--text-xs);
    color: var(--text-dim);
    line-height: 1.4;
    margin: 0;
  }

  /* ── M1.5 empty-state skeleton shimmer ── reduced motion is handled globally
     (tokens.css freezes all animation-duration to ~0, leaving a static bar). */
  .skel-bar {
    display: block;
    border-radius: var(--radius-sm);
    background: linear-gradient(90deg, var(--n3) 25%, var(--n4) 50%, var(--n3) 75%);
    background-size: 200% 100%;
    animation: shimmer 1.6s ease-in-out infinite;
  }
  .skel-label {
    width: 55%;
    height: 8px;
  }
  .skel-val {
    width: 35%;
    height: 16px;
    margin-top: 2px;
  }
  @keyframes shimmer {
    0% {
      background-position: 200% 0;
    }
    100% {
      background-position: -200% 0;
    }
  }
</style>
