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
{/if}

<style>
  .verdict {
    /* wave U2 Task 1: docked in the right rail below MetricsPanel — normal flow in
       a flex column, not a hand-computed offset off MetricsPanel's height. */
    width: 100%;
    flex: none;
    box-sizing: border-box;
    display: flex;
    flex-direction: column;
    gap: var(--space-3);
    padding: var(--space-4);
    background: var(--panel);
    border: 1px solid var(--panel-edge);
    border-left-width: 3px;
    border-radius: var(--radius);
    backdrop-filter: blur(8px);
    font-size: var(--text-sm);
  }
  .verdict.pass {
    border-left-color: var(--plasma-green);
  }
  .verdict.fail {
    border-left-color: var(--crimson);
  }
  .verdict.pending {
    border-left-color: var(--amber);
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
    color: var(--text-faint);
    cursor: pointer;
    font-size: var(--text-md);
    line-height: 1;
    padding: 2px 4px;
  }
  .vclose:hover {
    color: var(--starlight);
  }
  .vbadge {
    display: flex;
    align-items: center;
    gap: var(--space-2);
    flex-wrap: wrap;
  }
  .seal {
    color: var(--brass);
    font-weight: 600;
    letter-spacing: 0.04em;
  }
  .refuted {
    color: var(--crimson);
    font-weight: 600;
    letter-spacing: 0.04em;
  }
  .claimed {
    color: var(--amber);
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
    color: var(--crimson);
    font-size: var(--text-sm);
    line-height: 1.35;
  }
  .ok {
    color: var(--plasma-green);
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
    color: var(--plasma-green);
  }
  .gate.r {
    color: var(--crimson);
  }
  .strikes {
    color: var(--crimson);
  }
</style>
