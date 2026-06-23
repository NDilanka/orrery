<script lang="ts">
  // Live event log — a textual tail of the raw protocol events so you can verify the loop is
  // actually working even when the orbital viz is visually still (long silent phases). Collapsible
  // so it never has to crowd the instrument. Reads the shared logStore the transport feeds.

  import { logStore } from '../stores/log.svelte';

  let open = $state(true);
  const entries = $derived(logStore.entries);

  // colour the event-type chip by family (start/done = green, stop/halt = ember, ask = cyan…)
  function tone(ev: string): string {
    if (/(done|merged|complete|green|pass|start)/.test(ev)) return 'good';
    if (/(stop|halt|error|fail|rollback|strike|refute|timeout)/.test(ev)) return 'bad';
    if (/(question|await|quota|wait)/.test(ev)) return 'wait';
    return 'neutral';
  }

  // keep the newest line in view as events stream in — but only while the user is already at the
  // bottom, so we never yank them out of scrollback they're reading.
  let box = $state<HTMLDivElement | null>(null);
  let stick = true;
  function onScroll() {
    if (box) stick = box.scrollHeight - box.scrollTop - box.clientHeight < 40;
  }
  $effect(() => {
    entries.length; // track
    if (box && stick) box.scrollTop = box.scrollHeight;
  });
</script>

<div class="log" class:closed={!open}>
  <button class="loghdr mono" onclick={() => (open = !open)} aria-expanded={open}>
    <span class="dot" class:live={entries.length > 0}></span>
    LIVE LOG
    <span class="count">{entries.length}</span>
    <span class="chev">{open ? '▾' : '▸'}</span>
  </button>
  {#if open}
    <div class="logbox" bind:this={box} onscroll={onScroll}>
      {#if entries.length === 0}
        <div class="empty mono">waiting for the engine's first event…</div>
      {:else}
        {#each entries as e (e.seq)}
          <div class="row mono">
            <span class="ev {tone(e.event)}">{e.event}</span>
            {#if e.detail}<span class="detail">{e.detail}</span>{/if}
          </div>
        {/each}
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
    gap: 8px;
    width: 100%;
    padding: 7px 11px;
    background: transparent;
    border: none;
    color: var(--text-dim);
    font-size: 9.5px;
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
  @keyframes pulse {
    0%, 100% { opacity: 1; }
    50% { opacity: 0.4; }
  }
  @media (prefers-reduced-motion: reduce) {
    .dot.live { animation: none; }
  }
  .count {
    color: var(--text-faint);
  }
  .chev {
    margin-left: auto;
    color: var(--text-faint);
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
    font-size: 11px;
    color: var(--text-faint);
    font-style: italic;
    padding: 4px 0;
  }
  .row {
    display: flex;
    align-items: baseline;
    gap: 8px;
    font-size: 11px;
    line-height: 1.4;
  }
  .ev {
    flex: 0 0 auto;
    color: var(--text-dim);
    border: 1px solid var(--hairline);
    border-radius: 4px;
    padding: 0 5px;
    font-size: 10px;
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
    color: var(--text-dim);
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .log.closed {
    width: auto;
  }
</style>
