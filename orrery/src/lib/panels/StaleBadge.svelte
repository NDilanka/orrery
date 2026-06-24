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
    /* clear the 120px cost/quota strip pinned to the bottom edge */
    bottom: calc(var(--strip-h) + var(--space-3));
    left: var(--chrome-inset);
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
  /* connecting/stale are STEADY beacons — no opacity blink. Motion is reserved
     for true urgency; freshness state is carried by hue + the text label
     (connecting… / stale / closed / live), so it survives grayscale. */
  .badge.connecting .dot {
    background: var(--amber);
  }
  .badge.stale .dot {
    background: var(--horizon-rose);
  }
  .badge.closed .dot {
    background: var(--text-faint);
  }
  .txt {
    font-size: var(--text-2xs);
    color: var(--text-meta);
    letter-spacing: 0.04em;
  }
  .badge.live .txt {
    color: var(--plasma-green);
  }
  .badge.stale .txt {
    color: var(--horizon-rose);
  }
  .att {
    font-size: var(--text-2xs);
    color: var(--text-meta);
  }
  .obs {
    font-size: var(--text-2xs);
    color: var(--text-meta);
    padding-left: 6px;
    border-left: 1px solid var(--hairline);
    text-transform: uppercase;
    letter-spacing: 0.1em;
  }
  /* Tier-1 / phone: shrink the badge so it doesn't crowd a 360px viewport */
  @media (max-width: 640px) {
    .badge {
      gap: var(--space-1);
      padding: 4px var(--space-2);
    }
    .txt,
    .att,
    .obs {
      font-size: 9.5px;
    }
    .obs {
      padding-left: var(--space-1);
    }
  }
</style>
