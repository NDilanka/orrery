<script lang="ts">
  // PlanetariumOverlay (A7) — the AMBIENT view's only chrome. Planetarium is
  // Tier-1 and calm: the Observatory canvas carries the whole picture (star,
  // cost-horizon, the four rest-states, beacon, quota-night). This overlay adds
  // TEXT ONLY ON A THRESHOLD (plan §3 "Planetarium = Tier-1, text only on
  // threshold; only beacon + weekly-crystal may be 'loud'"):
  //   - a rest-state reached (certified / banked ember / polar night / beacon)
  //   - the cost-horizon crossing 50/80/100%
  //   - a quota night with its countdown
  // When the run is simply healthy-and-running, the overlay shows the bare
  // minimum (loop id + spend) low-contrast — nothing loud. The exit affordance
  // (back to Observatory) is always available but recessed.

  import { runStore } from '../stores/run.svelte';
  import { uiStore } from '../stores/ui.svelte';
  import DecisionSheet from './DecisionSheet.svelte';

  const s = $derived(runStore.state);

  // pending = questions the engine surfaced that nobody has answered yet. When
  // any exist the loop is blocked on a human; the ambient view must say so LOUD.
  const pending = $derived(s.questions.filter((q) => q.a == null));

  let showSheet = $state(false);

  function fmtUsd(n: number): string {
    return '$' + n.toFixed(2);
  }
  function fmtCountdown(sec: number | null): string {
    if (sec == null) return '';
    if (sec <= 0) return 'probing…';
    const h = Math.floor(sec / 3600);
    const m = Math.floor((sec % 3600) / 60);
    if (h > 0) return `${h}h ${String(m).padStart(2, '0')}m`;
    return `${m}m`;
  }

  // the single most-important thing to SAY right now (or null = stay silent)
  type Threshold = { label: string; cls: string; loud: boolean } | null;
  const threshold = $derived.by<Threshold>((): Threshold => {
    const rest = s.run.restState;
    // a genuine crash outranks everything — as loud as NEEDS YOU (plan §3
    // "only beacon + weekly-crystal may be loud"; a failure earns the same voice).
    if (rest === 'failed-dark') return { label: 'FAILED', cls: 'beacon', loud: true };
    if (rest === 'handoff-beacon') return { label: 'NEEDS YOU', cls: 'beacon', loud: true };
    if (rest === 'quota-frost' || s.run.status === 'quota-wait') {
      const left = runStore.quotaSecondsLeft;
      const weekly = s.quota.resetType === 'weekly';
      return {
        label: `QUOTA PAUSE${weekly ? ' (weekly)' : ''} · resumes ${fmtCountdown(left) || 'soon'}`,
        cls: 'frost',
        loud: weekly, // only the weekly crystal may be loud
      };
    }
    if (rest === 'certified-done') return { label: 'DONE · VERIFIED', cls: 'done', loud: false };
    if (rest === 'stopped-ember') return { label: 'PAUSED', cls: 'ember', loud: false };
    // defensive fallback: status flipped to error but restState hasn't (shouldn't
    // happen — the reducer derives both together — but never silently stay quiet).
    if (s.run.status === 'error') return { label: 'FAILED', cls: 'beacon', loud: true };
    const hf = runStore.horizonFrac;
    if (hf >= 1) return { label: 'COST HORIZON · 100% — frozen', cls: 'crit', loud: false };
    if (hf >= 0.8) return { label: `COST HORIZON · ${Math.round(hf * 100)}%`, cls: 'warn', loud: false };
    if (hf >= 0.5) return { label: `cost horizon · ${Math.round(hf * 100)}%`, cls: 'note', loud: false };
    return null;
  });
</script>

<div class="planetarium" class:reduced={uiStore.reducedMotion}>
  <!-- recessed identity + the load-bearing numbers (legible without the HUD) -->
  <div class="ident mono">
    <span class="lid">{s.loopId}</span>
    <span class="dot" aria-hidden="true">·</span>
    <span class="cost num">{fmtUsd(s.run.cumUsd)}</span>
    {#if s.cost.ratePerMin > 0}
      <span class="dot" aria-hidden="true">·</span>
      <span class="rate num">{fmtUsd(s.cost.ratePerMin)}/min</span>
    {/if}
  </div>
  {#if s.currentItem}
    <div class="curitem mono" title={s.currentItem}>→ {s.currentItem}</div>
  {/if}

  <!-- BLOCKED ON A HUMAN — a loud, real (keyboard/touch) affordance. This is the
       one place the ambient view raises its voice: a beacon-tier call to act,
       paired with a glyph + count so it never reads by hue alone. Opens the
       DecisionSheet in place; we never leave Planetarium. -->
  {#if pending.length}
    <button
      class="needs"
      class:reduced={uiStore.reducedMotion}
      onclick={() => (showSheet = true)}
      aria-haspopup="dialog"
    >
      <span class="needs-glyph" aria-hidden="true">◈</span>
      <span class="needs-text">NEEDS A DECISION</span>
      {#if pending.length > 1}
        <span class="needs-n num" aria-label="{pending.length} pending">×{pending.length}</span>
      {/if}
    </button>
  {/if}

  <!-- threshold text — appears only when there's something worth saying -->
  {#if threshold}
    <div class="threshold {threshold.cls}" class:loud={threshold.loud}>
      {threshold.label}
    </div>
  {/if}

  <!-- recessed exit back to the full instrument -->
  <button class="exit mono" onclick={() => uiStore.setMode('observatory')}>
    ✦ EXIT AMBIENT
  </button>
</div>

{#if showSheet && pending.length}
  <DecisionSheet onClose={() => (showSheet = false)} />
{/if}

<style>
  .planetarium {
    position: absolute;
    inset: 0;
    pointer-events: none;
    z-index: 13;
  }
  .ident {
    position: absolute;
    top: 22px;
    left: 0;
    right: 0;
    text-align: center;
    font-size: var(--text-xs);
    letter-spacing: 0.16em;
    color: var(--text-meta);
    text-transform: uppercase;
  }
  .ident .lid {
    color: var(--text-dim);
  }
  .ident .cost {
    color: var(--brass);
  }
  .ident .rate {
    color: var(--text-meta);
  }
  /* what the loop is working on right now — the other load-bearing fact */
  .curitem {
    position: absolute;
    top: 44px;
    left: 0;
    right: 0;
    text-align: center;
    font-size: var(--text-2xs);
    letter-spacing: 0.1em;
    color: var(--text-meta);
    padding: 0 var(--space-4);
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  /* the threshold line sits just below the star (centre), big + readable */
  .threshold {
    position: absolute;
    left: 0;
    right: 0;
    bottom: 16%;
    text-align: center;
    font-family: var(--font-grotesk);
    font-size: 15px;
    font-weight: 600;
    letter-spacing: 0.18em;
    text-transform: uppercase;
    color: var(--text-dim);
  }
  .threshold.note { color: var(--amber); }
  .threshold.warn { color: var(--horizon-rose); }
  .threshold.crit { color: var(--crimson); }
  .threshold.done { color: var(--plasma-green); }
  .threshold.ember { color: var(--ember); }
  .threshold.frost { color: var(--frost); }
  .threshold.beacon { color: var(--crimson); }
  /* only "loud" thresholds (beacon, weekly night) get a slow attention pulse */
  .threshold.loud {
    font-size: 18px;
    animation: loudPulse 2.4s ease-in-out infinite;
  }
  @keyframes loudPulse {
    0%, 100% { opacity: 1; }
    50% { opacity: 0.55; }
  }
  .planetarium.reduced .threshold.loud {
    animation: none;
  }
  /* the BEACON-tier call to act. Loud is allowed here (per the plan only the
     beacon + weekly-crystal may be loud). Glyph + label + count so status never
     rides on hue alone. Urgency reads as a slow TIGHTENING breath, never a
     blink; reduced-motion drops the animation but keeps the loud styling. */
  .needs {
    position: absolute;
    left: 50%;
    bottom: 22%;
    transform: translateX(-50%);
    pointer-events: auto;
    display: flex;
    align-items: center;
    gap: var(--space-2);
    font-family: var(--font-grotesk);
    font-size: 14px;
    font-weight: 700;
    letter-spacing: 0.16em;
    text-transform: uppercase;
    color: var(--starlight);
    padding: 12px 22px;
    border-radius: var(--radius-pill);
    border: 1px solid var(--crimson);
    background: color-mix(in srgb, var(--crimson) 22%, var(--void-3));
    box-shadow:
      0 0 0 1px color-mix(in srgb, var(--crimson) 40%, transparent),
      0 0 24px color-mix(in srgb, var(--crimson) 45%, transparent);
    cursor: pointer;
    backdrop-filter: blur(6px);
    animation: needsBreath 2.6s var(--ease-standard) infinite;
  }
  .needs:hover {
    background: color-mix(in srgb, var(--crimson) 32%, var(--void-3));
  }
  .needs-glyph {
    color: var(--crimson);
    font-size: 16px;
  }
  .needs-n {
    color: var(--crimson);
    font-size: 12px;
    font-weight: 700;
  }
  /* a slow tightening of the glow ring — attention without a blink */
  @keyframes needsBreath {
    0%,
    100% {
      box-shadow:
        0 0 0 1px color-mix(in srgb, var(--crimson) 40%, transparent),
        0 0 24px color-mix(in srgb, var(--crimson) 45%, transparent);
    }
    50% {
      box-shadow:
        0 0 0 1px color-mix(in srgb, var(--crimson) 70%, transparent),
        0 0 12px color-mix(in srgb, var(--crimson) 60%, transparent);
    }
  }
  .needs.reduced {
    animation: none;
  }

  .exit {
    position: absolute;
    bottom: 20px;
    left: 50%;
    transform: translateX(-50%);
    pointer-events: auto;
    font-size: 10px;
    letter-spacing: 0.14em;
    text-transform: uppercase;
    padding: 7px 16px;
    border-radius: var(--radius-pill);
    border: 1px solid var(--hairline);
    background: color-mix(in srgb, var(--void-3) 70%, transparent);
    color: var(--text-faint);
    cursor: pointer;
    backdrop-filter: blur(6px);
    transition: color 0.2s, border-color 0.2s;
  }
  .exit:hover {
    color: var(--starlight);
    border-color: var(--brass);
  }

  /* phone: the top edge is taken by the (wrapping) navbar + mode toggle, so the
     load-bearing identity numbers move BELOW the star where they stay readable. */
  @media (max-width: 640px) {
    .ident {
      top: auto;
      bottom: 32%;
    }
    .curitem {
      top: auto;
      bottom: calc(32% - 20px);
    }
  }
</style>
