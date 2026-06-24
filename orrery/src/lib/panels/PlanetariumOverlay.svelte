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

  const s = $derived(runStore.state);

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
    if (rest === 'handoff-beacon') return { label: 'NEEDS A HUMAN', cls: 'beacon', loud: true };
    if (rest === 'quota-frost' || s.run.status === 'quota-wait') {
      const left = runStore.quotaSecondsLeft;
      const weekly = s.quota.resetType === 'weekly';
      return {
        label: `${weekly ? 'POLAR NIGHT' : 'DUSK'} · resumes ${fmtCountdown(left) || 'soon'}`,
        cls: 'frost',
        loud: weekly, // only the weekly crystal may be loud
      };
    }
    if (rest === 'certified-done') return { label: 'CERTIFIED DONE', cls: 'done', loud: false };
    if (rest === 'stopped-ember') return { label: 'BANKED EMBER', cls: 'ember', loud: false };
    if (s.run.status === 'error') return { label: 'CRASHED', cls: 'beacon', loud: true };
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

  <!-- threshold text — appears only when there's something worth saying -->
  {#if threshold}
    <div class="threshold {threshold.cls}" class:loud={threshold.loud}>
      {threshold.label}
    </div>
  {/if}

  <!-- recessed exit back to the full instrument -->
  <button class="exit mono" onclick={() => uiStore.setMode('observatory')}>
    ✦ exit planetarium
  </button>
</div>

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
