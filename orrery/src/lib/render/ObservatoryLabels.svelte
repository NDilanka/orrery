<script lang="ts">
  // ObservatoryLabels — an absolutely-positioned, pointer-events:none HTML overlay
  // that sits OVER the Pixi canvas (in Observatory's .ofield) and makes the scene
  // legible in isolation. It is deliberately restrained / low-contrast so it never
  // fights the rendered instrument, and it works in Planetarium (Tier-1) too.
  //
  // It draws:
  //   · a small caption just under the star — cumulative spend + the per-minute
  //     rate (tabular figures, --text-meta);
  //   · a small label (story key + status glyph) near EVERY orbit body (Task 3), decluttered —
  //     faded/shrunk when not current, and skipped in a dense ring (>12 bodies) unless the body
  //     is current or needs attention (failed / claimed-but-unverified);
  //   · the CURRENT body's label gets the full treatment plus a ◌/✓ claimed-vs-verified trust
  //     glyph prefix (Task 2 — the product's core trust signal, promoted off the tiny planet ring);
  //   · an optional tiny 50/80/100% tick label on the cost-horizon ring.
  //
  // Positions arrive pre-computed/THROTTLED from the rAF (so we don't thrash
  // reactivity). Under reduced motion we place statically — no transitions.

  type BodyLabel = {
    key: string;
    x: number;
    y: number;
    status: string;
    trust: 'verified' | 'unverified' | null;
    current: boolean;
    // wave U2 Task 3: this is runStore.auditTargetKey — the retired Observatory
    // "lighthouse" (a tower + sweeping beam) used to be the only signal that this
    // claimed-green body was being audited; a pulsing "verifying…" label replaces it.
    auditing: boolean;
  };
  type Labels = {
    cumUsd: number;
    ratePerMin: number;
    star: { x: number; y: number };
    bodies: BodyLabel[];
    horizonPct: number | null;
  };

  let { labels, reduced }: { labels: Labels; reduced: boolean } = $props();

  function fmtUsd(n: number): string {
    return '$' + (n ?? 0).toFixed(2);
  }

  // a small, non-hue-alone status glyph per work-item lifecycle status (distinct from the trust
  // glyph ◌/✓, which is about claimed-vs-verified, not lifecycle stage).
  function statusGlyph(status: string): string {
    switch (status) {
      case 'done':
        return '●';
      case 'review':
        return '◑';
      case 'in-progress':
        return '◐';
      case 'blocked':
        return '⊘';
      case 'failed':
        return '✕';
      case 'ready':
        return '○';
      default:
        return '·'; // backlog
    }
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

  <!-- every orbit body, named at its planet — current gets the full/undimmed treatment
       (+ the claimed-vs-verified trust glyph), others are small/faded (Task 3 declutter) -->
  {#each labels.bodies as b (b.key)}
    <div
      class="body mono"
      class:current={b.current}
      class:other={!b.current}
      style="left:{b.x}px; top:{b.y}px;"
      title={b.auditing ? `${b.key} — verifying…` : b.key}
    >
      {#if b.current && b.trust}
        <span class="trust {b.trust}" aria-hidden="true">{b.trust === 'verified' ? '✓' : '◌'}</span>
      {/if}
      <span class="bkey">{b.key}</span>
      {#if b.auditing}
        <span class="verifying">verifying…</span>
      {/if}
      <span class="bglyph" aria-hidden="true">{statusGlyph(b.status)}</span>
    </div>
  {/each}

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
    z-index: var(--z-labels);
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

  /* every orbit body's key, floated just above-right of its planet (Task 3).
     The KEY is the child that ellipsizes (min-width:0 + hidden overflow) so the
     trailing "verifying…" pulse / status glyph always survive a long story key. */
  .body {
    position: absolute;
    transform: translate(-50%, calc(-100% - 12px));
    display: inline-flex;
    align-items: baseline;
    gap: 3px;
    max-width: 168px;
    white-space: nowrap;
    letter-spacing: 0.08em;
    text-shadow: 0 1px 3px var(--void);
    transition: left var(--dur-mid) var(--ease-standard),
      top var(--dur-mid) var(--ease-standard),
      opacity var(--dur-mid) var(--ease-standard);
  }
  .body .bkey {
    flex: 0 1 auto;
    min-width: 0;
    overflow: hidden;
    text-overflow: ellipsis;
  }
  .body .trust,
  .body .verifying,
  .body .bglyph {
    flex: none;
  }
  /* current body: full-size, undimmed — the one label that matters most right now */
  .body.current {
    font-size: var(--text-2xs);
    color: var(--text-meta);
    opacity: 1;
  }
  /* every other body: small + faded (declutter) — a quiet map pin, not a competing label */
  .body.other {
    font-size: 9px;
    color: var(--text-faint);
    opacity: 0.55;
    max-width: 96px;
  }
  .body .bglyph {
    opacity: 0.85;
  }
  /* claimed-vs-verified trust glyph (Task 2), current body only — paired with the VERIFIED/
     UNVERIFIED word elsewhere (Hud/Cosmos); here it's a compact glyph-only prefix. */
  .body .trust {
    font-size: var(--text-xs);
  }
  .body .trust.unverified {
    color: var(--amber);
  }
  .body .trust.verified {
    color: var(--plasma-green);
  }
  /* the audit-in-flight signal (wave U2 Task 3, replaces the Observatory lighthouse) —
     a cold auditor-white pulse, subtle so it never competes with the trust glyph. */
  .body .verifying {
    color: var(--auditor-white);
    font-size: 9px;
    opacity: 0.85;
    animation: verifyPulse 2.2s ease-in-out infinite;
  }
  @keyframes verifyPulse {
    0%, 100% { opacity: 0.85; }
    50% { opacity: 0.35; }
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
  .olabels.reduced .body,
  .olabels.reduced .horizon {
    transition: none;
  }
</style>
