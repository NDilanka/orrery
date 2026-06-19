<script lang="ts">
  // BODY view (A4, Tier-2/3) — one work-item up close. The deepest zoom level
  // (Cosmos → System → Body). Reads the active run store for a single item and
  // shows its gate, gate stages (the airlock chambers), verifier verdict, strike
  // history (rollback lives), smoke result and PR — the item's whole story on one
  // card. Reuses VerdictPanel's vocabulary (claimed-green vs certified seal).
  //
  // It is deliberately simple: no Pixi, no orbit. The System view's Observatory
  // is what you fly out to; this is the work-item dossier.

  import { runStore } from '../stores/run.svelte';
  import type { WorkItem } from '../types';

  let { itemKey }: { itemKey: string } = $props();

  const s = $derived(runStore.state);
  const item = $derived<WorkItem | null>(s.items[itemKey] ?? null);
  const verdict = $derived(s.verdicts[itemKey] ?? null);

  function fmtUsd(n: number): string {
    return '$' + n.toFixed(2);
  }
</script>

<div class="body">
  {#if !item}
    <div class="empty mono">no such body · {itemKey}</div>
  {:else}
    <header class="bhead">
      <span class="bkey mono">{itemKey}</span>
      <span class="bstatus {item.status}">{item.status}</span>
      {#if item.certified}
        <span class="seal">✦ certified · brass seal</span>
      {:else if item.gate?.green}
        <span class="claimed">claimed green · awaiting audit</span>
      {/if}
    </header>

    <!-- ghost / acceptance criteria (the frozen done-contract) -->
    {#if item.ghost && item.ghost.criteria.length}
      <section class="block">
        <div class="blabel">acceptance criteria · ghost target</div>
        <ul class="crit">
          {#each item.ghost.criteria as c (c.text)}
            <li class={c.met ? 'met' : 'unmet'}>
              <span class="mark">{c.met ? '✦' : '◌'}</span>{c.text}
            </li>
          {/each}
        </ul>
      </section>
    {/if}

    <!-- gate + airlock chambers -->
    {#if item.gate}
      {@const g = item.gate}
      <section class="block">
        <div class="blabel">gate · {g.green ? 'green' : 'red'}</div>
        <div class="gate mono {g.green ? 'g' : 'r'}">
          {g.pass}/{g.total} pass{g.fail ? ` · ${g.fail} fail` : ''}
          {#if g.baselinePass}<span class="dim"> · baseline {g.baselinePass}</span>{/if}
        </div>
        {#if g.stages && g.stages.length}
          <div class="airlock">
            {#each g.stages as st (st.name)}
              <span class="chamber {st.ok ? 'ok' : 'fail'}">{st.name}</span>
            {/each}
          </div>
        {/if}
      </section>
    {/if}

    <!-- verifier verdict (the auditor's report card) -->
    {#if verdict}
      <section class="block">
        <div class="blabel">verifier verdict{verdict.model ? ` · ${verdict.model}` : ''}</div>
        <div class="vbadge {verdict.pass ? 'pass' : 'fail'}">
          {verdict.pass ? '✦ certified' : '✖ refuted · false green'}
        </div>
        {#if verdict.failingCriteria.length}
          <ul class="crit">
            {#each verdict.failingCriteria as c (c)}
              <li class="unmet"><span class="mark">✖</span>{c}</li>
            {/each}
          </ul>
        {/if}
        {#if verdict.evidence}
          <div class="evidence mono">{verdict.evidence}</div>
        {/if}
        {#if verdict.nextAction}
          <div class="next">→ {verdict.nextAction}</div>
        {/if}
      </section>
    {/if}

    <!-- strikes (rollback lives) -->
    {#if item.strikes > 0}
      <section class="block">
        <div class="blabel">strikes · rollback lives</div>
        <div class="strikes">
          {#each Array(Math.max(item.strikeBudget, item.strikes)) as _, i (i)}
            <span class="notch {i < item.strikes ? 'used' : ''}"></span>
          {/each}
          <span class="mono dim">{item.strikes}/{item.strikeBudget}</span>
        </div>
      </section>
    {/if}

    <!-- smoke -->
    {#if item.smoke}
      <section class="block">
        <div class="blabel">browser smoke · iter {item.smoke.iter}</div>
        <div class="mono {item.smoke.passed ? 'g' : 'r'}">
          {item.smoke.passed ? 'passed' : 'failed'}{item.smoke.timedOut ? ' · timed out' : ''}
        </div>
        {#if item.smoke.verdict}
          <div class="evidence mono">{item.smoke.verdict}</div>
        {/if}
      </section>
    {/if}

    <!-- PR (the persist dock) -->
    {#if item.pr}
      <section class="block">
        <div class="blabel">pull request{item.pr.merged ? ' · merged' : ''}</div>
        <div class="pr mono {item.pr.merged ? 'g' : ''}">
          {item.pr.merged ? '✓ merged' : 'open'} → {item.pr.base}
          {#if item.pr.url}
            <a class="prlink" href={item.pr.url} target="_blank" rel="noreferrer">{item.pr.url}</a>
          {/if}
        </div>
      </section>
    {/if}

    <footer class="bfoot mono">
      cost attributed {fmtUsd(item.costAttributed)}
      {#if item.group}· epic {item.group}{/if}
    </footer>
  {/if}
</div>

<style>
  .body {
    position: absolute;
    top: 50%;
    left: 50%;
    transform: translate(-50%, -50%);
    width: min(560px, 92vw);
    max-height: 78vh;
    overflow-y: auto;
    display: flex;
    flex-direction: column;
    gap: 14px;
    padding: 22px 26px;
    background: var(--panel);
    border: 1px solid var(--panel-edge);
    border-radius: var(--radius);
    backdrop-filter: blur(10px);
    z-index: 9;
  }
  .empty {
    color: var(--text-dim);
    text-align: center;
  }
  .bhead {
    display: flex;
    align-items: center;
    gap: 10px;
    flex-wrap: wrap;
    border-bottom: 1px solid var(--hairline);
    padding-bottom: 12px;
  }
  .bkey {
    font-size: 15px;
    letter-spacing: 0.04em;
    color: var(--starlight);
  }
  .bstatus {
    font-size: 10px;
    text-transform: uppercase;
    letter-spacing: 0.12em;
    padding: 3px 9px;
    border-radius: var(--radius-pill);
    color: var(--text-dim);
    border: 1px solid var(--hairline);
  }
  .bstatus.done {
    color: var(--plasma-green);
    border-color: color-mix(in srgb, var(--plasma-green) 40%, transparent);
  }
  .bstatus.in-progress {
    color: var(--plasma-cyan);
  }
  .bstatus.review {
    color: var(--amber);
  }
  .bstatus.blocked,
  .bstatus.failed {
    color: var(--crimson);
  }
  .seal {
    color: var(--brass);
    font-size: 11px;
    font-weight: 600;
    letter-spacing: 0.04em;
  }
  .claimed {
    color: var(--amber);
    font-size: 11px;
    font-weight: 600;
  }
  .block {
    display: flex;
    flex-direction: column;
    gap: 7px;
  }
  .blabel {
    font-size: 9px;
    text-transform: uppercase;
    letter-spacing: 0.16em;
    color: var(--text-faint);
  }
  .crit {
    margin: 0;
    padding: 0;
    list-style: none;
    display: flex;
    flex-direction: column;
    gap: 5px;
  }
  .crit li {
    display: flex;
    gap: 8px;
    font-size: 12.5px;
    line-height: 1.4;
  }
  .crit li .mark {
    flex: none;
  }
  .crit li.met {
    color: var(--plasma-green);
  }
  .crit li.unmet {
    color: var(--crimson);
  }
  .gate {
    font-size: 13px;
  }
  .gate.g,
  .g {
    color: var(--plasma-green);
  }
  .gate.r,
  .r {
    color: var(--crimson);
  }
  .dim {
    color: var(--text-faint);
  }
  .airlock {
    display: flex;
    gap: 6px;
    flex-wrap: wrap;
  }
  .chamber {
    font-size: 10px;
    text-transform: uppercase;
    letter-spacing: 0.08em;
    padding: 3px 9px;
    border-radius: 6px;
    border: 1px solid var(--hairline);
    font-family: var(--font-mono);
  }
  .chamber.ok {
    color: var(--plasma-green);
    border-color: color-mix(in srgb, var(--plasma-green) 35%, transparent);
  }
  .chamber.fail {
    color: var(--crimson);
    border-color: color-mix(in srgb, var(--crimson) 45%, transparent);
    background: color-mix(in srgb, var(--crimson) 8%, transparent);
  }
  .vbadge {
    font-size: 12px;
    font-weight: 600;
    letter-spacing: 0.04em;
  }
  .vbadge.pass {
    color: var(--brass);
  }
  .vbadge.fail {
    color: var(--crimson);
  }
  .evidence {
    color: var(--text-dim);
    font-size: 11px;
    line-height: 1.45;
    white-space: pre-wrap;
    word-break: break-word;
  }
  .next {
    color: var(--starlight);
    font-size: 12px;
  }
  .strikes {
    display: flex;
    align-items: center;
    gap: 6px;
  }
  .notch {
    width: 16px;
    height: 4px;
    border-radius: 2px;
    background: var(--hairline);
  }
  .notch.used {
    background: var(--crimson);
  }
  .pr {
    font-size: 12px;
    color: var(--text-dim);
    display: flex;
    flex-direction: column;
    gap: 4px;
  }
  .prlink {
    color: var(--plasma-cyan);
    text-decoration: none;
    word-break: break-all;
    font-size: 11px;
  }
  .prlink:hover {
    text-decoration: underline;
  }
  .bfoot {
    font-size: 10.5px;
    color: var(--text-faint);
    border-top: 1px solid var(--hairline);
    padding-top: 10px;
  }
</style>
