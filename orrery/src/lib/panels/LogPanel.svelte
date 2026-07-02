<script lang="ts">
  // Live event log — a textual tail of the raw protocol events so you can verify the loop is
  // actually working even when the orbital viz is visually still (long silent phases). Collapsible
  // so it never has to crowd the instrument. Reads the shared logStore the transport feeds.

  import { logStore } from '../stores/log.svelte';
  import { activityStore, computeLiveness } from '../stores/activity.svelte';
  import { runStore } from '../stores/run.svelte';
  import { uiStore } from '../stores/ui.svelte';

  let open = $state(true);
  const entries = $derived(logStore.entries);

  // ── liveness ────────────────────────────────────────────────────────────────
  // log.jsonl only gains a line at phase boundaries, so a 30-min dev-story looks frozen. The
  // engine's activity.json heartbeat (every ~12s during an agent step) fills the gap. We turn it
  // into a real dot: green = a fresh beat (actively working), amber = running but the beat has gone
  // quiet (a gate run / between steps — still alive), faint = not running / no beat. A local 1s
  // clock makes the dot go stale + the elapsed tick up WITHOUT needing a new delta. The derivation
  // itself is the pure (unit-tested) computeLiveness.
  let now = $state(Date.now());
  $effect(() => {
    const id = setInterval(() => (now = Date.now()), 1000);
    return () => clearInterval(id);
  });

  const activity = $derived(activityStore.current);
  const live = $derived(
    computeLiveness(
      activity,
      runStore.state.run.status === 'running',
      now,
      activityStore.receivedAt,
    ),
  );
  const liveness = $derived(live.state);

  function fmtDur(sec: number): string {
    const s = Math.max(0, Math.floor(sec));
    if (s < 60) return `${s}s`;
    const m = Math.floor(s / 60);
    if (m < 60) return `${m}m${String(s % 60).padStart(2, '0')}s`;
    return `${Math.floor(m / 60)}h${String(m % 60).padStart(2, '0')}m`;
  }

  // colour the event-type chip by family (start/done = green, stop/halt = ember, ask = cyan…)
  function tone(ev: string): string {
    if (/(done|merged|complete|green|pass|start)/.test(ev)) return 'good';
    if (/(stop|halt|error|fail|rollback|strike|refute|timeout)/.test(ev)) return 'bad';
    if (/(question|await|quota|wait)/.test(ev)) return 'wait';
    return 'neutral';
  }

  // Rows are a compact one line by default; clicking a row toggles the full multi-line detail
  // (the native title= tooltip is always there too for hover-peek without committing a click).
  let expanded = $state<Set<number>>(new Set());
  function toggleRow(seq: number) {
    const next = new Set(expanded);
    if (next.has(seq)) next.delete(seq);
    else next.add(seq);
    expanded = next;
  }

  // ── streaming discipline ────────────────────────────────────────────────────
  // Keep the newest line in view as events stream in — but ONLY while the user is already near the
  // bottom, so we never yank them out of scrollback they're reading. If they've scrolled up, we
  // surface a small "new" affordance instead. DOM/scroll updates are coalesced to one rAF flush.
  const FOLLOW_PX = 60;
  let box = $state<HTMLDivElement | null>(null);
  let stick = $state(true);
  let unread = $state(0);
  let raf = 0;

  function nearBottom(el: HTMLDivElement): boolean {
    return el.scrollHeight - el.scrollTop - el.clientHeight < FOLLOW_PX;
  }

  function onScroll() {
    if (!box) return;
    stick = nearBottom(box);
    if (stick) unread = 0;
  }

  function jumpToNewest() {
    if (!box) return;
    box.scrollTop = box.scrollHeight;
    stick = true;
    unread = 0;
  }

  // One rAF-coalesced flush per render: either follow to the bottom, or count the new rows the
  // user hasn't seen yet (so a burst of events is a single scroll write, not one-per-event).
  let lastSeen = 0;
  $effect(() => {
    const n = entries.length; // track
    if (raf) cancelAnimationFrame(raf);
    raf = requestAnimationFrame(() => {
      raf = 0;
      if (!box) return;
      if (stick) {
        box.scrollTop = box.scrollHeight;
        unread = 0;
      } else if (n > lastSeen) {
        unread += n - lastSeen;
      }
      lastSeen = n;
    });
  });

  $effect(() => () => {
    if (raf) cancelAnimationFrame(raf);
  });
</script>

<div class="log" class:closed={!open}>
  <button class="loghdr mono" onclick={() => (open = !open)} aria-expanded={open}>
    <span
      class="dot"
      class:live={liveness === 'live'}
      class:idle={liveness === 'idle'}
      class:still={uiStore.reducedMotion}
      aria-hidden="true"
    ></span>
    LIVE LOG
    <span class="count num">{entries.length}</span>
    <span class="chev" aria-hidden="true">{open ? '▾' : '▸'}</span>
  </button>
  {#if open}
    <!-- liveness sub-header: what the engine is doing RIGHT NOW (between coarse log events) -->
    {#if liveness !== 'off' && activity}
      <div class="activity mono" class:idle={liveness === 'idle'} title={activity.ts ? `last beat ${activity.ts}` : ''}>
        <span class="aphase">{activity.phase ?? 'working'}</span>
        {#if activity.story}<span class="asep">·</span><span class="astory">{activity.story}</span>{/if}
        <span class="asep">·</span><span class="adur num">{fmtDur(live.elapsedSec)}</span>
        {#if activity.dirty > 0}
          <span class="asep">·</span><span class="adirty num">{activity.dirty} file{activity.dirty === 1 ? '' : 's'}</span>
        {/if}
        {#if liveness === 'idle'}<span class="aquiet">quiet {fmtDur(live.ageMs / 1000)}</span>{/if}
      </div>
    {/if}
    <div class="logwrap">
      <div
        class="logbox"
        bind:this={box}
        onscroll={onScroll}
        role="log"
        aria-live="polite"
        aria-relevant="additions"
        aria-label="Live event log"
      >
        {#if entries.length === 0}
          <div class="empty mono">waiting for the engine's first event…</div>
        {:else}
          {#each entries as e (e.seq)}
            <div class="row mono" class:open={expanded.has(e.seq)}>
              <span class="ev {tone(e.event)}">{e.event}</span>
              {#if e.detail}
                <button
                  class="detail"
                  class:open={expanded.has(e.seq)}
                  title={e.detail}
                  aria-expanded={expanded.has(e.seq)}
                  onclick={() => toggleRow(e.seq)}>{e.detail}</button
                >
              {/if}
            </div>
          {/each}
        {/if}
      </div>
      {#if !stick && unread > 0}
        <button class="jump mono num" onclick={jumpToNewest}>
          ↓ {unread} new
        </button>
      {/if}
    </div>
  {/if}
</div>

<style>
  .log {
    /* left side, BELOW the HUD (top-left) and ABOVE the bottom control/transport band — a free
       zone in the System view, so the log never overlaps the scrubber or control bar. */
    position: absolute;
    top: 280px;
    left: 14px;
    width: min(340px, 36vw);
    background: var(--panel);
    border: 1px solid var(--panel-edge);
    border-radius: var(--radius);
    backdrop-filter: blur(8px);
    z-index: 15;
    overflow: hidden;
  }
  .loghdr {
    display: flex;
    align-items: center;
    gap: var(--space-2);
    width: 100%;
    padding: 7px 11px;
    background: transparent;
    border: none;
    color: var(--text-meta);
    font-size: 10.5px;
    letter-spacing: 0.16em;
    cursor: pointer;
    text-align: left;
  }
  .loghdr:hover {
    color: var(--starlight);
  }
  .dot {
    width: 7px;
    height: 7px;
    border-radius: 50%;
    background: var(--text-faint);
  }
  .dot.live {
    background: var(--plasma-green);
    box-shadow: 0 0 6px var(--plasma-green);
    animation: pulse 1.8s ease-in-out infinite;
  }
  /* running but the heartbeat has gone quiet (gate run / between steps) — alive, not working */
  .dot.idle {
    background: var(--amber);
    box-shadow: 0 0 5px var(--amber);
    animation: pulse 3.2s ease-in-out infinite;
  }
  .dot.live.still,
  .dot.idle.still {
    animation: none;
  }
  @keyframes pulse {
    0%, 100% { opacity: 1; }
    50% { opacity: 0.4; }
  }
  .count {
    color: var(--text-meta);
    font-size: 10.5px;
  }
  .chev {
    margin-left: auto;
    color: var(--text-faint);
  }
  /* liveness sub-header: phase · story · elapsed · files-changed (one ellipsized line) */
  .activity {
    display: flex;
    align-items: baseline;
    gap: 5px;
    padding: 0 11px 7px;
    font-size: 10.5px;
    line-height: 1.3;
    color: var(--plasma-green);
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }
  .activity.idle {
    color: var(--amber);
  }
  .activity .aphase {
    flex: 0 0 auto;
    letter-spacing: 0.04em;
  }
  .activity .astory {
    flex: 0 1 auto;
    min-width: 0;
    overflow: hidden;
    text-overflow: ellipsis;
    color: var(--text-meta);
  }
  .activity .asep {
    flex: 0 0 auto;
    color: var(--text-faint);
  }
  .activity .adur,
  .activity .adirty {
    flex: 0 0 auto;
    color: var(--text-dim);
  }
  .activity .aquiet {
    flex: 0 0 auto;
    margin-left: auto;
    color: var(--text-faint);
    font-style: italic;
  }
  .logwrap {
    position: relative;
  }
  .logbox {
    max-height: 26vh;
    overflow-y: auto;
    padding: 0 11px 9px;
    display: flex;
    flex-direction: column;
    gap: 3px;
  }
  .empty {
    font-size: var(--text-xs);
    color: var(--text-meta);
    font-style: italic;
    padding: 4px 0;
  }
  .row {
    display: flex;
    align-items: baseline;
    gap: var(--space-2);
    font-size: var(--text-xs);
    line-height: 1.4;
  }
  .ev {
    flex: 0 0 auto;
    color: var(--text-meta);
    border: 1px solid var(--hairline);
    border-radius: 4px;
    padding: 0 5px;
    font-size: 10.5px;
  }
  .ev.good {
    color: var(--plasma-green);
    border-color: color-mix(in srgb, var(--plasma-green) 35%, transparent);
  }
  .ev.bad {
    color: var(--ember);
    border-color: color-mix(in srgb, var(--ember) 35%, transparent);
  }
  .ev.wait {
    color: var(--plasma-cyan);
    border-color: color-mix(in srgb, var(--plasma-cyan) 35%, transparent);
  }
  .detail {
    flex: 1;
    min-width: 0; /* allow the flex child to shrink below content width so ellipsis actually kicks in */
    margin: 0;
    padding: 0;
    background: transparent;
    border: none;
    text-align: left;
    font: inherit;
    font-size: 10.5px;
    color: var(--text-meta);
    cursor: pointer;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    transition: color var(--dur-fast) var(--ease-standard);
  }
  .detail:hover {
    color: var(--starlight);
  }
  /* expanded: drop the one-line clamp so the full detail wraps across lines */
  .detail.open {
    overflow: visible;
    text-overflow: clip;
    white-space: normal;
    word-break: break-word;
    color: var(--text-dim);
  }
  .jump {
    position: absolute;
    right: 11px;
    bottom: 11px;
    padding: 3px var(--space-2);
    font-size: 10.5px;
    letter-spacing: 0.04em;
    color: var(--void);
    background: var(--plasma-cyan);
    border: none;
    border-radius: 10px;
    cursor: pointer;
    box-shadow: 0 2px 8px rgba(0, 0, 0, 0.4);
  }
  .log.closed {
    width: auto;
  }
</style>
