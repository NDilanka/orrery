<script lang="ts">
  // HUD overlay — glance-first instrument readout. Status pill (the four
  // not-running rest-states + running), cumUsd (tabular), current item, quota
  // countdown. Reads the runes store; numbers are tabular for honesty.

  import { runStore } from '../stores/run.svelte';

  const s = $derived(runStore.state);

  type Pill = { label: string; cls: string };
  const pill = $derived.by<Pill>((): Pill => {
    const rest = s.run.restState;
    if (rest === 'certified-done') return { label: 'CERTIFIED DONE', cls: 'done' };
    if (rest === 'stopped-ember') return { label: 'BANKED EMBER', cls: 'ember' };
    if (rest === 'quota-frost') return { label: 'POLAR NIGHT', cls: 'frost' };
    if (rest === 'handoff-beacon') return { label: 'NEEDS A HUMAN', cls: 'beacon' };
    if (s.run.status === 'running') return { label: 'RUNNING', cls: 'running' };
    if (s.run.status === 'error') return { label: 'CRASHED', cls: 'beacon' };
    if (s.run.status === 'quota-wait') return { label: 'POLAR NIGHT', cls: 'frost' };
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

  const quotaLeft = $derived(runStore.quotaSecondsLeft);
  const horizonPct = $derived(Math.round(runStore.horizonFrac * 100));
</script>

<div class="hud">
  <div class="row top">
    <span class="pill {pill.cls}">
      <span class="dot"></span>{pill.label}
    </span>
    {#if s.phase.sixPhase}
      <span class="phase mono">{s.phase.sixPhase}{s.phase.label ? ' · ' + s.phase.label : ''}</span>
    {/if}
  </div>

  <div class="cost">
    <span class="num big">{fmtUsd(s.run.cumUsd)}</span>
    <span class="label">cum spend</span>
    {#if runStore.horizonVisible}
      <span class="horizon {horizonPct >= 100 ? 'crit' : horizonPct >= 80 ? 'warn' : ''}">
        horizon {horizonPct}%
      </span>
    {/if}
  </div>

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
      {#if runStore.current?.certified}
        <span class="seal">✦ sealed</span>
      {/if}
    </div>
  {/if}

  {#if quotaLeft != null}
    <div class="quota">
      <span class="label">{s.quota.resetType === 'weekly' ? 'polar night' : 'dusk'} · resumes in</span>
      <span class="num">{fmtCountdown(quotaLeft)}</span>
    </div>
  {/if}

  <div class="meta mono">
    <span>{Object.keys(s.items).length} bodies</span>
    <span>·</span>
    <span>{Object.keys(s.groups).length} rings</span>
    <span>·</span>
    <span>{s.events} events</span>
    {#if s.cost.ratePerMin > 0}
      <span>·</span>
      <span>{fmtUsd(s.cost.ratePerMin)}/min</span>
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
    border-color: color-mix(in srgb, var(--crimson) 50%, transparent);
    background: color-mix(in srgb, var(--crimson) 12%, transparent);
    animation: beaconPulse 1.6s ease-in-out infinite;
  }
  .pill.idle {
    color: var(--text-dim);
    border-color: var(--hairline);
  }
  @keyframes beaconPulse {
    0%, 100% { opacity: 1; }
    50% { opacity: 0.55; }
  }
  .phase {
    font-size: 11px;
    color: var(--text-dim);
    letter-spacing: 0.04em;
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
    font-size: 10px;
    text-transform: uppercase;
    letter-spacing: 0.14em;
    color: var(--text-faint);
  }
  .horizon {
    font-size: 11px;
    color: var(--amber);
    font-family: var(--num);
  }
  .horizon.warn { color: var(--horizon-rose); }
  .horizon.crit { color: var(--crimson); }
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
  .seal { color: var(--brass); font-size: 11px; }
  .quota .num { color: var(--frost); }
  .meta {
    display: flex;
    gap: 6px;
    font-size: 10.5px;
    color: var(--text-faint);
    flex-wrap: wrap;
  }
</style>
