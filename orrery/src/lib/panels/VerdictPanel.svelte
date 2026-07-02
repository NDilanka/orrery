<script lang="ts">
  // VerdictPanel — the auditor's report card for one item (A3 / design B1).
  // Opens (a) when you click a planet, or (b) automatically when the verifier
  // REFUTES a claimed green (a `verdict{pass:false}` for the current item) — the
  // signature moment E2. Shows the structured verdict: pass/fail, the failing
  // criteria, evidence, next action, and which (cheap) model judged it.
  //
  // It reads the runes store directly. Selection is held in the store
  // (`selectedItem`); a refute auto-selects the refuted item so the card pops.

  import { runStore } from '../stores/run.svelte';
  import type { Verdict } from '../types';

  const s = $derived(runStore.state);

  // which item's verdict to show: explicit selection wins, else the latest refute
  let autoKey = $state<string | null>(null);
  let lastRefuted = $state<string | null>(null);

  // detect a fresh refute → auto-open that item's card
  $effect(() => {
    const lv = runStore.latestVerdict;
    if (lv && lv.verdict.pass === false && lv.key !== lastRefuted) {
      lastRefuted = lv.key;
      autoKey = lv.key;
    }
  });

  const activeKey = $derived(runStore.selectedItem ?? autoKey);
  const verdict = $derived<Verdict | null>(activeKey ? s.verdicts[activeKey] ?? null : null);
  const item = $derived(activeKey ? s.items[activeKey] ?? null : null);
  // show the panel only when there is something to report for the active item
  const open = $derived(!!activeKey && (!!verdict || !!item?.gate));

  function close() {
    runStore.selectItem(null);
    autoKey = null;
  }
</script>

{#if open && activeKey}
  <div class="verdict {verdict ? (verdict.pass ? 'pass' : 'fail') : 'pending'}">
    <div class="vhead">
      <span class="vtitle mono">{activeKey}</span>
      <button class="vclose" aria-label="close" onclick={close}>✕</button>
    </div>

    {#if verdict}
      <div class="vbadge">
        {#if verdict.pass}
          <span class="seal">✦ VERIFIED · brass seal</span>
        {:else}
          <span class="refuted">✖ REFUTED · false green</span>
        {/if}
        {#if verdict.model}
          <span class="model mono">judge · {verdict.model}</span>
        {/if}
      </div>

      {#if verdict.failingCriteria.length}
        <div class="block">
          <div class="blabel">failing criteria</div>
          <ul class="crit">
            {#each verdict.failingCriteria as c (c)}
              <li>✖ {c}</li>
            {/each}
          </ul>
        </div>
      {:else if verdict.pass}
        <div class="block ok mono">all acceptance criteria met</div>
      {/if}

      {#if verdict.evidence}
        <div class="block">
          <div class="blabel">evidence</div>
          <div class="evidence mono">{verdict.evidence}</div>
        </div>
      {/if}

      {#if verdict.nextAction}
        <div class="block">
          <div class="blabel">next action</div>
          <div class="next">{verdict.nextAction}</div>
        </div>
      {/if}
    {:else}
      <div class="vbadge">
        <span class="claimed">agent claims pass — not yet verified</span>
      </div>
    {/if}

    {#if item?.gate}
      <div class="gate mono {item.gate.green ? 'g' : 'r'}">
        gate {item.gate.pass}/{item.gate.total} · {item.gate.green ? 'green' : 'red'}
        {#if item.strikes > 0}
          <span class="strikes">· {item.strikes}/{item.strikeBudget} strikes (rollback lives)</span>
        {/if}
      </div>
    {/if}
  </div>
{:else}
  <!-- M1.5: subtle skeleton-shimmer placeholder instead of rendering nothing, so the
       right rail reads as one continuous instrument even before a body is selected.
       Decorative only — the sr-only text below carries the actual state to a11y tools. -->
  <div class="verdict empty" aria-hidden="true">
    <div class="skel-bar skel-title"></div>
    <div class="skel-bar skel-line"></div>
    <div class="skel-bar skel-line short"></div>
  </div>
  <span class="sr-only">no body selected — click one to see its verdict</span>
{/if}

<style>
  .verdict {
    /* wave U2 Task 1: docked in the right rail below MetricsPanel — normal flow in
       a flex column, not a hand-computed offset off MetricsPanel's height.
       M1.2: one shared right-rail card treatment — --surface-panel + a hairline
       border (no shadow, no glass on docked rails per plan §1.6). */
    width: 100%;
    flex: none;
    box-sizing: border-box;
    display: flex;
    flex-direction: column;
    gap: var(--space-3);
    padding: var(--space-4);
    background: var(--surface-panel);
    border: 1px solid var(--hairline);
    border-left-width: var(--accent-border-w);
    border-radius: var(--radius);
    font-size: var(--text-sm);
  }
  /* left-border accent is a small element → the two-tier system's -core */
  .verdict.pass {
    border-left-color: var(--status-ok-core);
  }
  .verdict.fail {
    border-left-color: var(--status-err-core);
  }
  .verdict.pending {
    border-left-color: var(--status-warn-core);
  }
  .verdict.empty {
    border-left-color: var(--hairline);
    gap: var(--space-2);
  }
  .vhead {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: var(--space-2);
  }
  .vtitle {
    font-size: var(--text-xs);
    letter-spacing: 0.08em;
    color: var(--starlight);
  }
  .vclose {
    background: transparent;
    border: none;
    border-radius: var(--radius-sm);
    color: var(--text-faint);
    cursor: pointer;
    font-size: var(--text-md);
    line-height: 1;
    padding: 2px 4px;
    transition:
      background var(--dur-feedback) var(--ease-standard),
      color var(--dur-feedback) var(--ease-standard);
  }
  .vclose:hover {
    /* +1 surface step on hover (plan §M1.4) */
    color: var(--starlight);
    background: var(--n3);
  }
  .vclose:active {
    background: var(--n4);
  }
  .vbadge {
    display: flex;
    align-items: center;
    gap: var(--space-2);
    flex-wrap: wrap;
  }
  .seal {
    /* brass = identity/certification accent (plan §1), not a status hue — kept as-is */
    color: var(--brass);
    font-weight: 600;
    letter-spacing: 0.04em;
  }
  .refuted {
    color: var(--status-err-core);
    font-weight: 600;
    letter-spacing: 0.04em;
  }
  .claimed {
    color: var(--status-warn-core);
    font-weight: 600;
  }
  .model {
    font-size: var(--text-2xs);
    color: var(--text-dim);
    padding: 2px 7px;
    border-radius: var(--radius-pill);
    background: var(--void-3);
  }
  .block {
    display: flex;
    flex-direction: column;
    gap: 5px;
  }
  .blabel {
    font-size: var(--text-2xs);
    text-transform: uppercase;
    letter-spacing: 0.14em;
    color: var(--text-meta);
  }
  .crit {
    margin: 0;
    padding: 0;
    list-style: none;
    display: flex;
    flex-direction: column;
    gap: 4px;
  }
  .crit li {
    color: var(--status-err-core);
    font-size: var(--text-sm);
    line-height: 1.35;
  }
  .ok {
    color: var(--status-ok-core);
    font-size: var(--text-xs);
  }
  .evidence {
    color: var(--text-dim);
    font-size: var(--text-xs);
    line-height: 1.4;
    white-space: pre-wrap;
    word-break: break-word;
  }
  .next {
    color: var(--starlight);
    font-size: var(--text-sm);
    line-height: 1.4;
  }
  .gate {
    font-size: var(--text-xs);
    color: var(--text-dim);
    border-top: 1px solid var(--hairline);
    padding-top: var(--space-2);
  }
  .gate.g {
    color: var(--status-ok-core);
  }
  .gate.r {
    color: var(--status-err-core);
  }
  .strikes {
    color: var(--status-err-core);
  }

  /* ── M1.5 empty-state skeleton shimmer ── reduced motion is handled globally
     (tokens.css freezes all animation-duration to ~0, leaving a static bar). */
  .skel-bar {
    border-radius: var(--radius-sm);
    background: linear-gradient(90deg, var(--n3) 25%, var(--n4) 50%, var(--n3) 75%);
    background-size: 200% 100%;
    animation: shimmer 1.6s ease-in-out infinite;
  }
  .skel-title {
    width: 40%;
    height: 12px;
  }
  .skel-line {
    width: 90%;
    height: 10px;
  }
  .skel-line.short {
    width: 60%;
  }
  @keyframes shimmer {
    0% {
      background-position: 200% 0;
    }
    100% {
      background-position: -200% 0;
    }
  }

  .sr-only {
    position: absolute;
    width: 1px;
    height: 1px;
    padding: 0;
    margin: -1px;
    overflow: hidden;
    clip: rect(0, 0, 0, 0);
    white-space: nowrap;
    border: 0;
  }
</style>
