<script lang="ts">
  // BODY view (A4, Tier-2/3) — one work-item up close. The deepest zoom level
  // (Cosmos → System → Body). Reads the active run store for a single item and
  // shows its gate, gate stages (the airlock chambers), verifier verdict, strike
  // history (rollback lives), smoke result and PR — the item's whole story on one
  // card. Reuses VerdictPanel's vocabulary (claimed-green vs certified seal).
  //
  // It is deliberately simple: no Pixi, no orbit. The System view's Observatory
  // is what you fly out to; this is the work-item dossier.
  //
  // wave U2 Task 5: rendered as a right-side drawer over the (already dimmed —
  // see +page.svelte .layer.out-near) System canvas on desktop, a full-screen
  // sheet on phone. Escape and the breadcrumb both already route back through
  // +page.svelte's existing nav (onKeydown / the crumb button) — unchanged here.
  // The slide-in animation + reduced-motion gate mirror DecisionSheet's contract.

  import { onMount } from 'svelte';
  import { runStore } from '../stores/run.svelte';
  import { uiStore } from '../stores/ui.svelte';
  import type { WorkItem } from '../types';

  let { itemKey, onBack }: { itemKey: string; onBack?: () => void } = $props();

  let dialogEl = $state<HTMLDivElement | null>(null);
  onMount(() => {
    // land keyboard focus inside the drawer on open (a minimal a11y contract —
    // the back button/breadcrumb/Escape all already close it).
    dialogEl?.focus();
  });

  const s = $derived(runStore.state);
  const item = $derived<WorkItem | null>(s.items[itemKey] ?? null);
  const verdict = $derived(s.verdicts[itemKey] ?? null);

  function fmtUsd(n: number): string {
    return '$' + n.toFixed(2);
  }
</script>

<div
  class="body"
  class:reduced={uiStore.reducedMotion}
  role="dialog"
  aria-modal="true"
  aria-label="Body dossier — {itemKey}"
  tabindex="-1"
  bind:this={dialogEl}
>
  {#if onBack}
    <button class="back" type="button" onclick={() => onBack?.()}>
      <span class="back-glyph" aria-hidden="true">←</span> system
    </button>
  {/if}
  {#if !item}
    <div class="empty mono">no such body · {itemKey}</div>
  {:else}
    <header class="bhead">
      <span class="bkey mono">{itemKey}</span>
      <span class="bstatus {item.status}">{item.status}</span>
      {#if item.certified}
        <span class="seal">✦ verified · brass seal</span>
      {:else if item.gate?.green}
        <span class="claimed">agent claims pass — not yet verified</span>
      {/if}
    </header>

    <!-- ghost / acceptance criteria (the frozen done-contract) -->
    {#if item.ghost && item.ghost.criteria.length}
      <section class="block">
        <div class="blabel">acceptance criteria (frozen)</div>
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
          {verdict.pass ? '✦ verified' : '✖ refuted · false green'}
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
        <div class="blabel">strikes (rollback lives)</div>
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
  /* wave U2 Task 5: a right-side drawer (desktop) — full height, slides in from
     the edge over the dimmed System canvas (see +page.svelte .layer.out-near). */
  .body {
    position: absolute;
    inset: 0 0 0 auto;
    width: min(420px, 100vw);
    max-height: none;
    overflow-y: auto;
    display: flex;
    flex-direction: column;
    gap: var(--space-4);
    padding: var(--space-5);
    /* clear the floating breadcrumb pill (navbar: top 18px, ~42px tall) which
       overlays the drawer's top-right and doubles as its Cosmos > loop > item header */
    padding-top: 76px;
    background: linear-gradient(180deg, var(--surface-2), var(--surface-1));
    border-left: 1px solid var(--panel-edge);
    border-radius: 0;
    box-shadow: -24px 0 60px rgba(0, 0, 0, 0.5);
    z-index: 9;
    animation: drawerIn var(--dur-zoom) var(--ease-out);
  }
  @keyframes drawerIn {
    from {
      transform: translateX(28px);
      opacity: 0;
    }
    to {
      transform: translateX(0);
      opacity: 1;
    }
  }
  .body.reduced {
    animation: none;
  }
  .body:focus-visible {
    outline: none; /* a dialog landing-focus, not a clickable control */
  }
  /* full-screen sheet on phone — same slide, just edge-to-edge. The navbar pill
     wraps to two rows on a 390px width, so the sheet clears a taller header. */
  @media (max-width: 640px) {
    .body {
      inset: 0;
      width: auto;
      border-left: none;
      padding-top: 100px;
    }
  }
  .empty {
    color: var(--text-dim);
    text-align: center;
  }
  .back {
    align-self: flex-start;
    display: inline-flex;
    align-items: center;
    gap: var(--space-1);
    padding: var(--space-1) var(--space-3);
    font-family: var(--font-mono);
    font-size: var(--text-xs);
    text-transform: uppercase;
    letter-spacing: 0.12em;
    color: var(--text-meta);
    background: var(--surface-2);
    border: 1px solid var(--hairline);
    border-radius: var(--radius-pill);
    cursor: pointer;
    transition: color var(--dur-fast) var(--ease-standard),
      border-color var(--dur-fast) var(--ease-standard);
  }
  .back:hover {
    color: var(--starlight);
    border-color: var(--panel-edge);
  }
  .back-glyph {
    font-size: var(--text-md);
    line-height: 1;
  }
  .bhead {
    display: flex;
    align-items: center;
    gap: var(--space-2);
    flex-wrap: wrap;
    border-bottom: 1px solid var(--hairline);
    padding-bottom: var(--space-3);
  }
  .bkey {
    font-size: var(--text-lg);
    letter-spacing: 0.04em;
    color: var(--starlight);
  }
  .bstatus {
    font-size: var(--text-2xs);
    text-transform: uppercase;
    letter-spacing: 0.12em;
    padding: var(--space-1) var(--space-2);
    border-radius: var(--radius-pill);
    color: var(--text-meta);
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
    font-size: var(--text-xs);
    font-weight: 600;
    letter-spacing: 0.04em;
  }
  .claimed {
    color: var(--amber);
    font-size: var(--text-xs);
    font-weight: 600;
  }
  .block {
    display: flex;
    flex-direction: column;
    gap: var(--space-2);
  }
  .blabel {
    font-size: var(--text-2xs);
    text-transform: uppercase;
    letter-spacing: 0.16em;
    color: var(--text-meta);
  }
  .crit {
    margin: 0;
    padding: 0;
    list-style: none;
    display: flex;
    flex-direction: column;
    gap: var(--space-1);
  }
  .crit li {
    display: flex;
    gap: var(--space-2);
    font-size: var(--text-sm);
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
    font-size: var(--text-md);
    font-variant-numeric: tabular-nums;
    font-feature-settings: "tnum" 1;
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
    color: var(--text-meta);
  }
  .airlock {
    display: flex;
    gap: var(--space-2);
    flex-wrap: wrap;
  }
  .chamber {
    font-size: var(--text-2xs);
    text-transform: uppercase;
    letter-spacing: 0.08em;
    padding: var(--space-1) var(--space-2);
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
    font-size: var(--text-sm);
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
    font-size: var(--text-xs);
    line-height: 1.45;
    white-space: pre-wrap;
    word-break: break-word;
  }
  .next {
    color: var(--starlight);
    font-size: var(--text-sm);
  }
  .strikes {
    display: flex;
    align-items: center;
    gap: var(--space-2);
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
    font-size: var(--text-sm);
    color: var(--text-dim);
    display: flex;
    flex-direction: column;
    gap: var(--space-1);
  }
  .prlink {
    color: var(--plasma-cyan);
    text-decoration: none;
    word-break: break-all;
    font-size: var(--text-xs);
  }
  .prlink:hover {
    text-decoration: underline;
  }
  .bfoot {
    font-size: var(--text-2xs);
    color: var(--text-meta);
    border-top: 1px solid var(--hairline);
    padding-top: var(--space-3);
    font-variant-numeric: tabular-nums;
    font-feature-settings: "tnum" 1;
  }
</style>
