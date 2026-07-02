<script lang="ts">
  // StaleBadge (A7) — the connection freshness indicator, bottom-left of the System
  // altitude. Two mutually-exclusive readings, same slot:
  //   - ws transport (web/mobile client): a calm "live" dot while connected; on
  //     drop it flips to "stale (last seen HH:MM)" + the reconnect attempt count.
  //   - tauri transport (desktop): the Channel itself never "disconnects" — it
  //     just goes quiet if the engine hangs. There's no socket to watch, so we
  //     reuse the activity.json heartbeat (PROTOCOL §1, the same beat LogPanel's
  //     live dot reads) to answer "is the desktop engine still actually there?"
  // Replay never renders anything here — there's no live connection to speak of.

  import type { WsStatus } from '../transport/ws';
  import { activityStore, computeLiveness } from '../stores/activity.svelte';
  import { runStore } from '../stores/run.svelte';

  let {
    status,
    transportKind = null,
  }: { status: WsStatus | null; transportKind?: 'tauri' | 'ws' | 'replay' | null } = $props();

  function hhmm(ms: number | null): string {
    if (!ms) return '—';
    const d = new Date(ms);
    return `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`;
  }

  const cls = $derived(status?.state ?? 'connecting');

  // ── desktop (Tauri) heartbeat staleness ────────────────────────────────
  // A beat older than this (or no beat at all) while the run is 'running' reads
  // as "the engine's gone quiet" — a real warning, not just "between phases"
  // (LogPanel's own live/idle dot uses a tighter 30s window for that finer read).
  const DESKTOP_STALE_MS = 45_000;
  let now = $state(Date.now());
  $effect(() => {
    if (transportKind !== 'tauri') return;
    const id = setInterval(() => (now = Date.now()), 1000);
    return () => clearInterval(id);
  });
  const running = $derived(runStore.state.run.status === 'running');
  const liveness = $derived(
    computeLiveness(activityStore.current, running, now, activityStore.receivedAt, DESKTOP_STALE_MS),
  );
  const desktopStale = $derived(
    transportKind === 'tauri' && running && liveness.state !== 'live',
  );
  function fmtAge(ms: number): string {
    if (!Number.isFinite(ms)) return '';
    const s = Math.max(0, Math.floor(ms / 1000));
    if (s < 60) return `${s}s`;
    return `${Math.floor(s / 60)}m`;
  }
  const desktopAgeTxt = $derived(fmtAge(liveness.ageMs));
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
{:else if desktopStale}
  <div class="badge stale" role="status" title="No activity.json heartbeat from the engine recently — it may be hung.">
    <span class="dot"></span>
    <span class="txt mono">engine silent{desktopAgeTxt ? ` ${desktopAgeTxt}` : ''}</span>
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
