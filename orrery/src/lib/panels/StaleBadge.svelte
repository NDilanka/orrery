<script lang="ts">
  // StaleBadge (A7) — the WebSocket freshness indicator for the web/mobile client.
  // Shows a calm "live" dot while connected; on drop it flips to
  // "stale (last seen HH:MM)" and the reconnect attempt count, per plan §6/§7.
  // Only rendered when the ws transport is active (the shell passes its status).

  import type { WsStatus } from '../transport/ws';

  let { status }: { status: WsStatus | null } = $props();

  function hhmm(ms: number | null): string {
    if (!ms) return '—';
    const d = new Date(ms);
    return `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`;
  }

  const cls = $derived(status?.state ?? 'connecting');
</script>

{#if status}
  <div class="badge {cls}">
    <span class="dot"></span>
    {#if status.state === 'live'}
      <span class="txt mono">live</span>
    {:else if status.state === 'connecting'}
      <span class="txt mono">connecting…</span>
    {:else if status.state === 'closed'}
      <span class="txt mono">closed</span>
    {:else}
      <span class="txt mono">stale (last seen {hhmm(status.lastSeen)})</span>
      {#if status.attempt > 0}
        <span class="att mono">· retry {status.attempt}</span>
      {/if}
    {/if}
    {#if status.observeOnly}
      <span class="obs mono">observe-only</span>
    {/if}
  </div>
{/if}

<style>
  .badge {
    position: absolute;
    bottom: 134px;
    left: 18px;
    display: inline-flex;
    align-items: center;
    gap: 7px;
    padding: 5px 11px;
    background: var(--panel);
    border: 1px solid var(--panel-edge);
    border-radius: var(--radius-pill);
    backdrop-filter: blur(8px);
    z-index: 16;
  }
  .dot {
    width: 7px;
    height: 7px;
    border-radius: 50%;
    background: var(--text-faint);
    flex: none;
  }
  .badge.live .dot {
    background: var(--plasma-green);
    box-shadow: 0 0 6px color-mix(in srgb, var(--plasma-green) 70%, transparent);
  }
  .badge.connecting .dot {
    background: var(--amber);
    animation: blink 1.2s ease-in-out infinite;
  }
  .badge.stale .dot {
    background: var(--horizon-rose);
    animation: blink 1.6s ease-in-out infinite;
  }
  .badge.closed .dot {
    background: var(--text-faint);
  }
  @keyframes blink {
    0%, 100% { opacity: 1; }
    50% { opacity: 0.4; }
  }
  @media (prefers-reduced-motion: reduce) {
    .dot { animation: none !important; }
  }
  .txt {
    font-size: 10.5px;
    color: var(--text-dim);
    letter-spacing: 0.04em;
  }
  .badge.live .txt {
    color: var(--plasma-green);
  }
  .badge.stale .txt {
    color: var(--horizon-rose);
  }
  .att {
    font-size: 10px;
    color: var(--text-faint);
  }
  .obs {
    font-size: 9.5px;
    color: var(--text-faint);
    padding-left: 6px;
    border-left: 1px solid var(--hairline);
    text-transform: uppercase;
    letter-spacing: 0.1em;
  }
</style>
