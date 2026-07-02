<script lang="ts">
  // HUD overlay — glance-first instrument readout. Status pill (the four
  // not-running rest-states + running), cumUsd (tabular), current item, quota
  // countdown. Reads the runes store; numbers are tabular for honesty.

  import { runStore } from '../stores/run.svelte';
  import { fmtClock, fmtDuration, fmtRelative } from '../timefmt';

  const s = $derived(runStore.state);

  type Pill = { label: string; cls: string; sub?: string };
  // quota resume sub-text, shared by the quota-frost restState and the transient quota-wait
  // status (before restState settles) — "+ reset time if available".
  function quotaSub(): string | undefined {
    const left = runStore.quotaSecondsLeft;
    return left != null ? `resumes in ${fmtCountdown(left)}` : undefined;
  }
  const pill = $derived.by<Pill>((): Pill => {
    const rest = s.run.restState;
    if (rest === 'failed-dark') return { label: 'FAILED', cls: 'failed' };
    if (rest === 'certified-done') return { label: 'DONE · VERIFIED', cls: 'done' };
    if (rest === 'stopped-ember')
      return { label: 'PAUSED', cls: 'ember', sub: 'resumable from checkpoint' };
    if (rest === 'quota-frost') return { label: 'QUOTA PAUSE', cls: 'frost', sub: quotaSub() };
    if (rest === 'handoff-beacon') return { label: 'NEEDS YOU', cls: 'beacon' };
    if (s.run.status === 'running') return { label: 'RUNNING', cls: 'running' };
    if (s.run.status === 'error') return { label: 'FAILED', cls: 'failed' };
    if (s.run.status === 'quota-wait') return { label: 'QUOTA PAUSE', cls: 'frost', sub: quotaSub() };
    return { label: 'IDLE', cls: 'idle' };
  });

  function fmtUsd(n: number): string {
    return '$' + n.toFixed(2);
  }
  function fmtCountdown(sec: number | null): string {
    if (sec == null) return '';
    if (sec <= 0) return 'probing…';
    const h = Math.floor(sec / 3600);
    const m = Math.floor((sec % 3600) / 60);
    const ss = sec % 60;
    if (h > 0) return `${h}h ${String(m).padStart(2, '0')}m`;
    if (m > 0) return `${m}m ${String(ss).padStart(2, '0')}s`;
    return `${ss}s`;
  }

  function fmtMins(n: number): string {
    // ~Ym to ceiling; switch to h:mm once it's long enough to be unwieldy in minutes.
    const m = Math.round(n);
    if (m >= 100) return `${Math.floor(m / 60)}h${String(m % 60).padStart(2, '0')}`;
    return `${m}m`;
  }

  const quotaLeft = $derived(runStore.quotaSecondsLeft);
  const horizonPct = $derived(Math.round(runStore.horizonFrac * 100));

  // scrub-safe forecast: only when actively burning toward a meaningful ceiling
  const minsToCeiling = $derived(runStore.minsToCeiling);
  const showForecast = $derived(minsToCeiling != null && runStore.ceilingUsd > 0);

  // model spectral chip (always paired with the text label, never hue-only)
  const model = $derived(s.phase.model);

  // cache activity (hide entirely when cold + no hits)
  const cacheActive = $derived(s.cache.hitRatio > 0 || s.cache.warm);
  const cachePct = $derived(Math.round(s.cache.hitRatio * 100));

  // "N ring"/"N rings" — proper pluralization (irregular nouns need an explicit plural form).
  function pl(n: number, singular: string, plural: string = singular + 's'): string {
    return `${n} ${n === 1 ? singular : plural}`;
  }

  // ── trust chip (Task 2): the product's core trust signal — an agent CLAIMING a pass vs an
  // independent verifier CONFIRMING it — promoted from a tiny dashed/solid planet ring to a
  // compact text chip next to the current item. null when there's nothing to report yet
  // (no gate/verdict seen for the current item).
  type TrustChip = { label: string; glyph: string; cls: 'verified' | 'unverified' } | null;
  const trustChip = $derived.by<TrustChip>((): TrustChip => {
    const cur = runStore.current;
    if (!cur) return null;
    if (cur.certified) return { label: 'VERIFIED', glyph: '✓', cls: 'verified' };
    if (cur.gate?.green || cur.status === 'review')
      return { label: 'UNVERIFIED', glyph: '◌', cls: 'unverified' };
    return null;
  });

  // ── wall-clock anchors (Task 4): run.startedAt / run.lastEventAt. A coarse 30s ticker (not a
  // 60fps one — this is a text readout, not motion) so "running 2h 12m" advances live.
  let nowTick = $state(Date.now());
  $effect(() => {
    const id = setInterval(() => {
      nowTick = Date.now();
    }, 30000);
    return () => clearInterval(id);
  });
  const timeline = $derived.by<string | null>((): string | null => {
    if (s.run.status === 'running') {
      const clock = fmtClock(s.run.startedAt);
      if (!clock) return null;
      const dur = fmtDuration(nowTick - Date.parse(s.run.startedAt!));
      return `started ${clock} · running ${dur}`;
    }
    const rel = fmtRelative(s.run.lastEventAt, nowTick);
    return rel ? `ended ${rel}` : null;
  });
</script>

<div class="hud">
  <div class="row top">
    <span class="pill {pill.cls}">
      <span class="dot"></span>{pill.label}
    </span>
    {#if s.phase.sixPhase}
      <span class="phase mono">{s.phase.sixPhase}{s.phase.label ? ' · ' + s.phase.label : ''}</span>
    {/if}
    {#if model}
      <span class="model-chip {model}">{model}</span>
    {/if}
  </div>
  {#if pill.sub}
    <div class="pill-sub">{pill.sub}</div>
  {/if}
  {#if timeline}
    <div class="timeline mono">{timeline}</div>
  {/if}

  <div class="cost">
    <span class="num big">{fmtUsd(s.run.cumUsd)}</span>
    <span class="label">cum spend</span>
    {#if runStore.horizonVisible}
      <span class="horizon {horizonPct >= 100 ? 'crit' : horizonPct >= 80 ? 'warn' : ''}">
        horizon {horizonPct}%
      </span>
    {/if}
  </div>

  {#if showForecast}
    <div class="forecast">
      <span class="num">{fmtUsd(runStore.usdRemaining)}</span>
      <span>left · ~</span>
      <span class="num">{fmtMins(minsToCeiling!)}</span>
      <span>to ceiling</span>
    </div>
  {/if}

  {#if s.currentItem}
    <div class="current">
      <span class="label">current</span>
      <span class="key mono">{s.currentItem}</span>
      {#if runStore.current?.gate}
        {@const g = runStore.current.gate}
        <span class="gate num {g.green ? 'green' : 'red'}">
          {g.pass}/{g.total} {g.green ? 'green' : 'red'}
        </span>
      {/if}
      {#if trustChip}
        <span class="trust {trustChip.cls}"
          ><span aria-hidden="true">{trustChip.glyph}</span> {trustChip.label}</span
        >
      {/if}
    </div>
  {/if}

  {#if quotaLeft != null}
    <div class="quota">
      <span class="label"
        >{s.quota.resetType === 'weekly' ? 'quota pause (weekly)' : 'quota pause'} · resumes in</span
      >
      <span class="num">{fmtCountdown(quotaLeft)}</span>
      {#if s.quota.probe > 0}
        <span class="probe num">probe #{s.quota.probe}</span>
      {/if}
    </div>
  {/if}

  <div class="meta mono">
    <span>{pl(Object.keys(s.items).length, 'body', 'bodies')}</span>
    <span>·</span>
    <span>{pl(Object.keys(s.groups).length, 'ring')}</span>
    <span>·</span>
    <span>{pl(s.events, 'event')}</span>
    {#if s.cost.ratePerMin > 0}
      <span>·</span>
      <span>{fmtUsd(s.cost.ratePerMin)}/min</span>
    {/if}
    {#if cacheActive}
      <span>·</span>
      <span class="cache">↻ cache <span class="num">{cachePct}%</span>{#if s.cache.warm}<span class="warm" title="warm">•</span>{/if}</span>
    {/if}
  </div>
</div>

<style>
  .hud {
    position: absolute;
    top: 18px;
    left: 18px;
    display: flex;
    flex-direction: column;
    gap: 10px;
    padding: 16px 18px;
    min-width: 240px;
    background: var(--panel);
    border: 1px solid var(--panel-edge);
    border-radius: var(--radius);
    backdrop-filter: blur(8px);
    pointer-events: none;
    user-select: none;
  }
  .row.top {
    display: flex;
    align-items: center;
    gap: 10px;
    flex-wrap: wrap;
  }
  .pill {
    display: inline-flex;
    align-items: center;
    gap: 7px;
    padding: 4px 11px;
    border-radius: var(--radius-pill);
    font-size: 11px;
    font-weight: 600;
    letter-spacing: 0.12em;
    border: 1px solid transparent;
  }
  .pill .dot {
    width: 7px;
    height: 7px;
    border-radius: 50%;
    background: currentColor;
  }
  .pill.running {
    color: var(--amber);
    border-color: color-mix(in srgb, var(--amber) 40%, transparent);
    background: color-mix(in srgb, var(--amber) 10%, transparent);
  }
  .pill.done {
    color: var(--plasma-green);
    border-color: color-mix(in srgb, var(--plasma-green) 45%, transparent);
    background: color-mix(in srgb, var(--plasma-green) 10%, transparent);
  }
  .pill.ember {
    color: var(--ember);
    border-color: color-mix(in srgb, var(--ember) 45%, transparent);
    background: color-mix(in srgb, var(--ember) 10%, transparent);
  }
  .pill.frost {
    color: var(--frost);
    border-color: color-mix(in srgb, var(--frost) 45%, transparent);
    background: color-mix(in srgb, var(--frost) 12%, transparent);
  }
  .pill.beacon {
    color: var(--crimson);
    /* the loudest state. Urgency reads as a slow breathing GLOW, never an opacity
       blink; the static border+shadow stays high-contrast so reduced-motion
       (which freezes the animation) never makes the state disappear. */
    border-color: var(--crimson);
    background: color-mix(in srgb, var(--crimson) 16%, transparent);
    box-shadow: 0 0 0 1px color-mix(in srgb, var(--crimson) 45%, transparent);
    animation: beaconBreathe 2.2s ease-in-out infinite;
  }
  /* the crashed state — crimson like beacon, but STEADY (no breathing). failed-dark
     reads as dim/dead (no glow), not an urgent call to act right now. */
  .pill.failed {
    color: var(--crimson);
    border-color: var(--crimson);
    background: color-mix(in srgb, var(--crimson) 14%, transparent);
  }
  .pill.idle {
    color: var(--text-dim);
    border-color: var(--hairline);
  }
  @keyframes beaconBreathe {
    0%,
    100% {
      box-shadow: 0 0 0 1px color-mix(in srgb, var(--crimson) 35%, transparent),
        0 0 6px color-mix(in srgb, var(--crimson) 18%, transparent);
    }
    50% {
      box-shadow: 0 0 0 1px color-mix(in srgb, var(--crimson) 65%, transparent),
        0 0 14px color-mix(in srgb, var(--crimson) 45%, transparent);
    }
  }
  .phase {
    font-size: 11px;
    color: var(--text-dim);
    letter-spacing: 0.04em;
  }
  /* rest-state sub-text (e.g. "resumable from checkpoint") — quiet, secondary to the pill */
  .pill-sub {
    font-size: var(--text-2xs);
    color: var(--text-meta);
    letter-spacing: 0.02em;
    margin-top: -4px;
  }
  /* wall-clock anchors (Task 4): "started HH:MM · running 2h 12m" / "ended 5m ago" */
  .timeline {
    font-size: var(--text-2xs);
    color: var(--text-meta);
    letter-spacing: 0.02em;
    margin-top: -4px;
  }
  /* model spectral chip — heat color is decorative; the text label always carries
     the meaning (status never by hue alone). */
  .model-chip {
    font-size: var(--text-2xs);
    text-transform: lowercase;
    letter-spacing: 0.06em;
    padding: 1px 6px;
    border-radius: var(--radius-pill);
    border: 1px solid currentColor;
    line-height: 1.4;
  }
  .model-chip.haiku {
    color: var(--spectral-haiku);
  }
  .model-chip.sonnet {
    color: var(--spectral-sonnet);
  }
  .model-chip.opus {
    color: var(--spectral-opus);
  }
  .cost {
    display: flex;
    align-items: baseline;
    gap: 9px;
    flex-wrap: wrap;
  }
  .big {
    font-size: 30px;
    font-weight: 600;
    color: var(--starlight);
    line-height: 1;
  }
  .label {
    font-size: var(--text-2xs);
    text-transform: uppercase;
    letter-spacing: 0.14em;
    color: var(--text-meta);
  }
  .horizon {
    font-size: 11px;
    color: var(--amber);
    font-family: var(--num);
  }
  .horizon.warn { color: var(--horizon-rose); }
  .horizon.crit { color: var(--crimson); }
  /* calm cost forecast — a quiet readout, not an alarm */
  .forecast {
    display: flex;
    align-items: baseline;
    gap: 5px;
    font-size: var(--text-2xs);
    letter-spacing: 0.04em;
    color: var(--text-meta);
    margin-top: -4px;
  }
  .forecast .num {
    color: var(--text-dim);
    font-size: var(--text-xs);
  }
  .current,
  .quota {
    display: flex;
    align-items: center;
    gap: 8px;
    flex-wrap: wrap;
    font-size: 12px;
  }
  .key {
    color: var(--starlight);
    font-size: 12px;
  }
  .gate.green { color: var(--plasma-green); }
  .gate.red { color: var(--crimson); }
  /* trust chip (Task 2) — the claimed-vs-verified signal, promoted to text. Amber outline =
     asserted-but-not-yet-audited; green (filled) = an independent verifier confirmed it. Never
     hue-alone: the glyph (◌ / ✓) and the label text both carry the meaning too. */
  .trust {
    display: inline-flex;
    align-items: center;
    gap: 4px;
    padding: 1px 8px;
    border-radius: var(--radius-pill);
    font-size: var(--text-2xs);
    letter-spacing: 0.08em;
    border: 1px solid currentColor;
  }
  .trust.unverified { color: var(--amber); }
  .trust.verified {
    color: var(--plasma-green);
    background: color-mix(in srgb, var(--plasma-green) 12%, transparent);
  }
  .quota .num { color: var(--frost); }
  .probe.num {
    color: var(--text-meta);
    font-size: var(--text-2xs);
  }
  .cache {
    display: inline-flex;
    align-items: baseline;
    gap: 3px;
    color: var(--cache-teal);
  }
  .cache .num {
    color: var(--cache-teal);
    font-size: var(--text-2xs);
  }
  .cache .warm {
    color: var(--cache-teal);
    margin-left: 1px;
  }
  .meta {
    display: flex;
    gap: var(--space-1);
    font-size: var(--text-2xs);
    color: var(--text-meta);
    flex-wrap: wrap;
  }

  /* phone / tier-1: a compact HUD that fits a 360px width and doesn't crowd the
     canvas — drop the meta row, shrink the headline, span the top edge. */
  @media (max-width: 640px) {
    .hud {
      left: var(--space-2);
      right: var(--space-2);
      min-width: 0;
      padding: var(--space-3);
      gap: var(--space-2);
    }
    .big {
      font-size: var(--text-xl);
    }
    .meta {
      display: none;
    }
    /* the forecast is an at-a-glance desktop nicety; drop it so the compact
       phone HUD never overflows (cache badge already rides in the hidden meta). */
    .forecast {
      display: none;
    }
    /* wall-clock line is a nicety too — drop it on the compact phone HUD */
    .timeline {
      display: none;
    }
  }
</style>
