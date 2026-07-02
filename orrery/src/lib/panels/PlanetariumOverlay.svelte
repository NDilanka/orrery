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
    z-index: var(--z-scene-overlay);
  }
  .ident {
    position: absolute;
    /* was 22px — nearest space step is --space-5 (24px, a 2px round per plan §M1.1) */
    top: var(--space-5);
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
  /* what the loop is working on right now — the other load-bearing fact.
     top:44px is `.ident`'s offset + its own line height, not a standalone design
     position — no --space-* step fits it without breaking that relationship, so it's
     kept literal. */
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
    font-size: var(--text-lg);
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
  /* "loud" thresholds (a genuine crash + the weekly quota-night) get the SAME glowing-chip
     treatment as the NEEDS-A-DECISION pill below (`--glow` + the shared `breathe` keyframe,
     primitives.css) — a failure earns the same voice, not just bigger pulsing text.
     Non-interactive (a div, not a button): there's nothing to DO from inside Planetarium
     besides exit to the full instrument, so unlike `.needs` this never gets
     `cursor:pointer` or a hover state. */
  .threshold.loud {
    --glow: var(--crimson);
    left: 50%;
    right: auto;
    bottom: 15%;
    transform: translateX(-50%);
    width: max-content;
    max-width: calc(100vw - 2 * var(--space-5));
    /* was 14px — pairs with `.needs` below (both "loud" chips share a size, one step
       down from the base --text-lg threshold) */
    font-size: var(--text-md);
    /* was 10px 20px — equidistant between --space-* steps either way; rounded down
       (--space-2 / --space-4) to keep the loud chip tight rather than growing it */
    padding: var(--space-2) var(--space-4);
    border-radius: var(--radius-pill);
    border: 1px solid var(--glow);
    background: color-mix(in srgb, var(--glow) 22%, var(--void-3));
    animation: breathe 2.6s var(--ease-standard) infinite;
  }
  .threshold.loud.frost {
    --glow: var(--frost);
  }
  .planetarium.reduced .threshold.loud {
    animation: none;
  }
  /* the BEACON-tier call to act. Loud is allowed here (per the plan only the
     beacon + weekly-crystal may be loud). Glyph + label + count so status never
     rides on hue alone. Urgency reads as a slow TIGHTENING breath, never a
     blink; reduced-motion drops the animation but keeps the loud styling. */
  .needs {
    --glow: var(--crimson);
    position: absolute;
    left: 50%;
    bottom: 22%;
    transform: translateX(-50%);
    pointer-events: auto;
    display: flex;
    align-items: center;
    gap: var(--space-2);
    font-family: var(--font-grotesk);
    font-size: var(--text-md);
    font-weight: 700;
    letter-spacing: 0.16em;
    text-transform: uppercase;
    color: var(--starlight);
    /* was 12px 22px — 12 is exact (--space-3); 22 rounds up 2px to --space-5 */
    padding: var(--space-3) var(--space-5);
    border-radius: var(--radius-pill);
    border: 1px solid var(--glow);
    background: color-mix(in srgb, var(--glow) 22%, var(--void-3));
    cursor: pointer;
    backdrop-filter: blur(6px);
    animation: breathe 2.6s var(--ease-standard) infinite;
  }
  .needs:hover {
    background: color-mix(in srgb, var(--glow) 32%, var(--void-3));
  }
  .needs-glyph {
    color: var(--crimson);
    font-size: var(--text-lg);
  }
  .needs-n {
    color: var(--crimson);
    font-size: var(--text-sm);
    font-weight: 700;
  }
  /* the shared `breathe` keyframe now lives in primitives.css (a slow tightening of the glow
     ring, attention without a blink) — `.needs` and `.threshold.loud` above just supply
     `--glow` and the per-consumer duration. */
  .needs.reduced {
    animation: none;
  }

  .exit {
    position: absolute;
    /* was 20px — nearest available step is --chrome-inset (18px, the canonical
       viewport gutter), closer than either --space-4/-5 */
    bottom: var(--chrome-inset);
    left: 50%;
    transform: translateX(-50%);
    pointer-events: auto;
    font-size: var(--text-2xs);
    letter-spacing: 0.14em;
    text-transform: uppercase;
    /* was 7px 16px — 16 is exact (--space-4); 7 rounds to --space-2 */
    padding: var(--space-2) var(--space-4);
    border-radius: var(--radius-pill);
    border: 1px solid var(--hairline);
    background: color-mix(in srgb, var(--void-3) 70%, transparent);
    color: var(--text-faint);
    cursor: pointer;
    backdrop-filter: blur(6px);
    transition: color var(--dur-feedback) var(--ease-standard),
      border-color var(--dur-feedback) var(--ease-standard);
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
