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
  // engine-v3 per-story trust signals (additive maps; absent until their event fires).
  const verify = $derived(activeKey ? s.verifies?.[activeKey] ?? null : null);
  const integrity = $derived(activeKey ? s.testIntegrity?.[activeKey] ?? null : null);
  const planCheck = $derived(activeKey ? s.planChecks?.[activeKey] ?? null : null);
  // test-integrity is only worth surfacing when it's a red tamper (a deletion / not-ok) or a
  // neutral "tests were modified" note; an all-clear ok+no-modifications result stays silent.
  const integrityAlert = $derived(!!integrity && ((integrity.deleted?.length ?? 0) > 0 || !integrity.ok));
  const integrityNote = $derived(!!integrity && !integrityAlert && (integrity.modified?.length ?? 0) > 0);
  // plan-check is only loud when it BLOCKED (the run will have halted); ok/inconclusive is quiet
  // (it still shows in the LOG panel), so it doesn't render a chip here.
  const planBlocked = $derived(!!planCheck && (planCheck.verdict === 'blocked' || !planCheck.ok));
  const hasChecks = $derived(!!verify || integrityAlert || integrityNote || planBlocked);
  // show the panel when there is something to report for the active item
  const open = $derived(!!activeKey && (!!verdict || !!item?.gate || hasChecks));

  function close() {
    runStore.selectItem(null);
    autoKey = null;
  }
</script>

{#if open && activeKey}
  <div class="verdict panel {verdict ? (verdict.pass ? 'pass' : 'fail') : 'pending'}">
    <div class="vhead panel-hd">
      <span class="vtitle mono">{activeKey}</span>
      <button class="vclose" aria-label="close" onclick={close}>✕</button>
    </div>

    {#if verdict}
      <div class="vbadge">
        {#if verdict.pass}
          <span class="seal">✦ VERIFIED · sealed</span>
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

    {#if hasChecks}
      <div class="checks">
        {#if verify}
          <div
            class="chk {verify.verdict === 'refute'
              ? 'alert-red'
              : verify.verdict === 'pass'
                ? 'ok'
                : 'neutral'}"
          >
            {#if verify.verdict === 'pass'}
              Adversarial check: passed
            {:else if verify.verdict === 'refute'}
              REFUTED{verify.reason ? `: ${verify.reason}` : ''}
            {:else}
              Adversarial check: {verify.verdict}
            {/if}
          </div>
        {/if}

        {#if integrityAlert && integrity}
          <div class="chk alert-red">
            Test tamper — {integrity.deleted.length} pre-existing test file{integrity.deleted
              .length === 1
              ? ''
              : 's'} deleted
            {#if integrity.deleted.length}
              <ul class="ck-list mono">
                {#each integrity.deleted as f (f)}
                  <li>{f}</li>
                {/each}
              </ul>
            {/if}
          </div>
        {:else if integrityNote && integrity}
          <div class="chk neutral">
            {integrity.modified.length} pre-existing test{integrity.modified.length === 1
              ? ''
              : 's'} modified
          </div>
        {/if}

        {#if planBlocked && planCheck}
          <div class="chk alert-amber">
            Plan gate blocked{planCheck.reason ? `: ${planCheck.reason}` : ''}
          </div>
        {/if}
      </div>
    {/if}
  </div>
{:else}
  <!-- M1.5: subtle skeleton-shimmer placeholder instead of rendering nothing, so the
       right rail reads as one continuous instrument even before a body is selected.
       Decorative only — the sr-only text below carries the actual state to a11y tools. -->
  <div class="verdict panel empty" aria-hidden="true">
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
       M4.5: card chrome (padding/border/radius/background) now comes from the
       shared `.panel` primitive — this class keeps the left-border accent (a
       one-off shape `.panel` doesn't have) and the flex-column layout. */
    width: 100%;
    flex: none;
    box-sizing: border-box;
    display: flex;
    flex-direction: column;
    gap: var(--space-3);
    border-left-width: var(--accent-border-w);
    font-size: var(--text-sm);
  }
  /* left-border accent is a small element → the two-tier system's -core.
     M4.5 monochrome sweep: pass/fail keep the pass→grayscale / fail→err
     mapping (status-ok-core already resolves to --em-hi post-M4.1). `pending`
     here means "no verdict yet" — nothing for the user to act on (verification
     runs automatically), so it is NOT a genuine "needs you" state and stays
     grayscale rather than warn-amber; contrast QAConsole, where a pending
     question genuinely blocks on the user and correctly stays amber. */
  .verdict.pass {
    border-left-color: var(--status-ok-core);
  }
  .verdict.fail {
    border-left-color: var(--status-err-core);
  }
  .verdict.pending {
    border-left-color: var(--em-low);
  }
  .verdict.empty {
    border-left-color: var(--hairline);
    gap: var(--space-2);
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
    /* M4.5: verified = pure white + the seal glyph (plan §5), not brass — brass is
       retired for success/verified (BodyView's .seal is the reference implementation). */
    color: var(--em-hi);
    font-weight: 600;
    letter-spacing: 0.04em;
  }
  .refuted {
    color: var(--status-err-core);
    font-weight: 600;
    letter-spacing: 0.04em;
  }
  .claimed {
    /* an unverified claim, not a thing the user must act on → content tier,
       not warn-amber (see the .verdict.pending comment above) */
    color: var(--em-mid);
    font-weight: 600;
  }
  .model {
    /* meta aside → em-faint */
    font-size: var(--text-2xs);
    color: var(--text-faint);
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
    /* body sentence, not a headline value → content tier */
    color: var(--text-dim);
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

  /* engine-v3 per-story trust chips (verify / test-integrity / plan-check). Chrome stays
     monochrome (M5 law); the red/amber families are reserved for genuine alerts — a REFUTED
     verify and a deleted-test tamper are reds, a blocked plan gate is amber, everything else
     (passed/skipped/inconclusive verify, tests-modified note) stays grayscale. */
  .checks {
    display: flex;
    flex-direction: column;
    gap: var(--space-2);
    border-top: 1px solid var(--hairline);
    padding-top: var(--space-2);
  }
  .chk {
    font-size: var(--text-xs);
    line-height: 1.4;
  }
  .chk.ok {
    color: var(--em-hi);
    font-weight: 600;
  }
  .chk.neutral {
    color: var(--text-faint);
  }
  .chk.alert-red {
    color: var(--status-err-core);
    font-weight: 600;
  }
  .chk.alert-amber {
    color: var(--status-warn-core);
    font-weight: 600;
  }
  .ck-list {
    margin: 4px 0 0;
    padding-left: 1.2em;
    list-style: disc;
  }
  .ck-list li {
    font-size: var(--text-2xs);
    font-weight: 400;
    color: var(--text-dim);
    line-height: 1.35;
    word-break: break-word;
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
