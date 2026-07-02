<script lang="ts">
  // Live event log — a textual tail of the raw protocol events so you can verify the loop is
  // actually working even when the orbital viz is visually still (long silent phases). Collapsible
  // so it never has to crowd the instrument. Reads the shared logStore the transport feeds.

  import { logStore } from '../stores/log.svelte';
  import { activityStore, computeLiveness } from '../stores/activity.svelte';
  import { runStore } from '../stores/run.svelte';
  import { uiStore } from '../stores/ui.svelte';

  // wave U2 Task 4: on a phone the panel starts collapsed to a 2-line peek (see
  // `peekEntries` + the `.peek` markup below) instead of the full desktop log —
  // it still expands to the same full view on tap, nothing is lost.
  let open = $state(!uiStore.isPhone);
  const entries = $derived(logStore.entries);
  const peekEntries = $derived(entries.slice(-2));

  // ── liveness ────────────────────────────────────────────────────────────────
  // log.jsonl only gains a line at phase boundaries, so a 30-min dev-story looks frozen. The
  // engine's activity.json heartbeat (every ~12s during an agent step) fills the gap. We turn it
  // into a real dot: a breathing white dot = a fresh beat (actively working); static faint = not
  // running, OR running but the beat has gone quiet (a gate run / between steps — still alive, but
  // M4.5 "monochrome, color = alert only" — a quiet beat isn't a genuine alert, so it no longer
  // gets its own amber tint; the .activity sub-header's "quiet Ns" text already says so in words).
  // A local 1s clock makes the dot go stale + the elapsed tick up WITHOUT needing a new delta. The
  // derivation itself is the pure (unit-tested) computeLiveness.
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

  // ── per-row relative time ───────────────────────────────────────────────────
  // Each entry is stamped with the wall-clock ms epoch at which the UI received it (see
  // log.svelte.ts). A live log benefits from that label creeping forward, but a per-second (or
  // finer) ticker is overkill for a coarse "Xm"/"Xh" bucket — a 20s nudge is plenty.
  let tsTick = $state(0);
  $effect(() => {
    const id = setInterval(() => (tsTick += 1), 20_000);
    return () => clearInterval(id);
  });

  function fmtRelative(ts: number): string {
    void tsTick; // read so the template re-evaluates this label on each tick
    if (typeof ts !== 'number' || !Number.isFinite(ts)) return '';
    const diffSec = Math.max(0, Math.floor((Date.now() - ts) / 1000));
    if (diffSec < 5) return 'just now';
    if (diffSec < 60) return `${diffSec}s`;
    const m = Math.floor(diffSec / 60);
    if (m < 60) return `${m}m`;
    const h = Math.floor(m / 60);
    if (h < 24) return `${h}h`;
    return `${Math.floor(h / 24)}d`;
  }

  // M4.5 LogPanel sweep (owner: "monochrome, color = alert only") — event-kind chips are
  // plain --em-low mono text by default; only the two families that are genuine alerts
  // elsewhere in the app (plan §5 M4: "red failed/crashed, amber needs-you/handoff/quota")
  // keep chroma. Judgment calls, since the raw event vocabulary (types.ts EventName + the
  // open string escape hatch for other adapters) isn't 1:1 with that short list:
  //   - err: parse_error, plus any literal error/fail/crash text a generic adapter emits.
  //   - warn: handoff, quota-hit/-wait/-resume, phase-timeout (a stalled phase is the same
  //     "needs a human" family as quota), cost-alert (an "-alert" kind, same family), and
  //     review-question/retro-question (the loop is asking the user something — a needs-you
  //     moment even though "question" isn't in the owner's literal list).
  //   - left monochrome on purpose: rollback/plateau (the row's own detail text already says
  //     what happened — never-hue-alone holds without a red chip too) and every start/stop/
  //     review/pr/merge/smoke/gate/verdict kind, per the owner's own examples.
  function tone(ev: string): 'err' | 'warn' | 'plain' {
    if (/error|fail|crash/i.test(ev)) return 'err';
    if (/handoff|needs.?you|quota|timeout|alert|question|await/i.test(ev)) return 'warn';
    return 'plain';
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

<div class="log panel" class:closed={!open}>
  <button class="loghdr panel-hd mono" onclick={() => (open = !open)} aria-expanded={open}>
    <span
      class="dot"
      class:live={liveness === 'live'}
      class:idle={liveness === 'idle'}
      class:still={uiStore.reducedMotion}
      aria-hidden="true"
    ></span>
    LIVE LOG
    <span class="count panel-meta num">{entries.length}</span>
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
              {#if typeof e.ts === 'number'}
                <span class="ts num">{fmtRelative(e.ts)}</span>
              {/if}
            </div>
          {/each}
        {/if}
      </div>
      {#if !stick && unread > 0}
        <button class="jump btn btn-ghost btn-sm mono num" onclick={jumpToNewest}>
          ↓ {unread} new
        </button>
      {/if}
    </div>
  {:else if uiStore.isPhone && entries.length}
    <!-- phone peek (wave U2 Task 4): a 2-line preview of the latest activity so
         the log reads at a glance without spending a tap to open it. -->
    <button class="peek" onclick={() => (open = true)} aria-label="expand the live log">
      {#each peekEntries as e (e.seq)}
        <div class="peekrow mono">
          <span class="ev {tone(e.event)}">{e.event}</span>
          {#if e.detail}<span class="peektext">{e.detail}</span>{/if}
        </div>
      {/each}
    </button>
  {/if}
</div>

<style>
  .log {
    /* wave U2 Task 1: docked in the left rail (below the HUD), not floated over the
       canvas — the grid places it, this is purely internal styling now. Flexes to
       fill the rail's remaining height (see .logwrap/.logbox below); when collapsed
       it shrinks back to just the header row. M4.4: Tier B (working) — plain `.panel`,
       no elevation (Tier A is the HUD alone). */
    width: 100%;
    height: 100%;
    display: flex;
    flex-direction: column;
    min-height: 0;
    /* .panel (primitives.css) supplies the surface-panel background + hairline +
       radius; padding: 0 overrides its default --space-4 — the header/body below
       already manage their own edge padding so the scrollbar can ride the true
       panel edge. */
    padding: 0;
    overflow: hidden;
  }
  .log.closed {
    height: auto;
  }
  /* .panel-hd (primitives.css) supplies the caps/weight/letter-spacing/color +
     flex/justify-content convention; this rule keeps only what's specific to LogPanel's
     header being a clickable collapse toggle (padding, cursor, hover/active feedback). */
  .loghdr {
    width: 100%;
    padding: var(--space-2) var(--space-3);
    background: transparent;
    border: none;
    cursor: pointer;
    text-align: left;
    transition:
      color var(--dur-feedback) var(--ease-standard),
      background var(--dur-feedback) var(--ease-standard);
  }
  .loghdr:hover {
    color: var(--em-hi);
    background: var(--surface-hover);
  }
  .loghdr:active {
    background: color-mix(in srgb, var(--surface-hover) 80%, white 10%);
  }
  .dot {
    width: 7px;
    height: 7px;
    border-radius: 50%;
    background: var(--em-faint);
  }
  /* live = a fresh heartbeat, the one state that earns motion: a white breathing glow
     (shared `breathe` keyframe, primitives.css — was an opacity-blink, retired per
     plan §1). M4.5: "idle" (heartbeat gone quiet, still running) is NOT an alert — it's
     just a lull — so it drops the old amber tint and reads identically to the static
     off/idle dot below (owner: "monochrome, color = alert only"; the elapsed/quiet
     timers in .activity already say what's happening in words). */
  .dot.live {
    background: var(--em-hi);
    --glow: var(--em-hi);
    --breathe-r: 7px;
    animation: breathe 1.8s ease-in-out infinite;
  }
  .dot.idle {
    background: var(--em-faint);
  }
  .dot.live.still {
    animation: none;
  }
  .count {
    font-size: var(--text-2xs);
  }
  .chev {
    margin-left: auto;
    color: var(--em-faint);
  }
  /* liveness sub-header: phase · story · elapsed · files-changed (one ellipsized line) —
     M4.5: same reasoning as the dot above — "idle" (quiet heartbeat) isn't an alert, so
     it dims to --em-low instead of the old amber; --em-mid while live. */
  .activity {
    display: flex;
    align-items: baseline;
    gap: var(--space-1);
    padding: 0 var(--space-3) var(--space-2);
    font-size: var(--text-2xs);
    line-height: 1.3;
    color: var(--em-mid);
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }
  .activity.idle {
    color: var(--em-low);
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
    flex: 1 1 auto;
    min-height: 0;
    display: flex;
    flex-direction: column;
  }
  .logbox {
    flex: 1 1 auto;
    min-height: 0;
    overflow-y: auto;
    padding: 0 var(--space-3) var(--space-2);
    display: flex;
    flex-direction: column;
    gap: var(--space-1);
  }
  .empty {
    font-size: var(--text-xs);
    color: var(--text-meta);
    font-style: italic;
    padding: var(--space-1) 0;
  }
  .row {
    display: flex;
    align-items: flex-start;
    gap: var(--space-2);
    font-size: var(--text-xs);
    line-height: 1.4;
  }
  /* M4.5: plain hairline mono chip, no fill — the owner's "monochrome, color = alert
     only" default. Only .ev.err / .ev.warn (below) keep chroma. */
  .ev {
    flex: 0 0 auto;
    color: var(--em-low);
    border: 1px solid var(--hairline);
    /* no --radius token below --radius-sm (6px) exists for a chip this small;
       nearest used (was a literal 4px) */
    border-radius: var(--radius-sm);
    padding: 0 var(--space-1);
    font-size: var(--text-2xs);
    background: transparent;
  }
  .ev.err {
    color: var(--status-err-core);
    border-color: color-mix(in srgb, var(--status-err-core) 35%, transparent);
  }
  .ev.warn {
    color: var(--status-warn-core);
    border-color: color-mix(in srgb, var(--status-warn-core) 35%, transparent);
  }
  .detail {
    flex: 1;
    min-width: 0; /* allow the flex child to shrink below content width so wrapping actually kicks in */
    margin: 0;
    padding: 0;
    background: transparent;
    border: none;
    text-align: left;
    font: inherit;
    font-size: var(--text-2xs);
    color: var(--em-mid);
    cursor: pointer;
    /* collapsed: up to 3 wrapped lines, then clamp — long identifiers/URLs still can't blow out
       the row width, but normal text no longer breaks mid-word on the way there. */
    display: -webkit-box;
    -webkit-box-orient: vertical;
    -webkit-line-clamp: 3;
    line-clamp: 3;
    overflow: hidden;
    overflow-wrap: break-word;
    transition: color var(--dur-feedback) var(--ease-standard);
  }
  .detail:hover {
    color: var(--em-hi);
  }
  /* expanded: drop the 3-line clamp so the full detail wraps across as many lines as it needs */
  .detail.open {
    display: block;
    -webkit-line-clamp: unset;
    line-clamp: unset;
    overflow: visible;
    overflow-wrap: break-word;
    color: var(--em-mid);
  }
  /* right-aligned relative-time label — sits at the end of the row (after .detail's flex:1, or
     pushed there itself via margin-left:auto when a row has no detail text at all). */
  .ts {
    flex: 0 0 auto;
    margin-left: auto;
    padding-left: var(--space-1);
    font-size: var(--text-2xs);
    color: var(--em-faint);
    white-space: nowrap;
  }
  /* M4.5: the shared .btn system (primitives.css) — a plain ghost pill, not its own
     bespoke cyan-filled button. Only positioning/elevation stay local. */
  .jump {
    position: absolute;
    right: var(--space-3);
    bottom: var(--space-3);
    /* .btn defaults to --font-grotesk, which would otherwise win the cascade over the
       global .mono/.num utility classes (primitives.css loads after tokens.css, so an
       equal-specificity .btn rule wins by source order) — force mono back explicitly. */
    font-family: var(--font-mono);
    letter-spacing: 0.04em;
    box-shadow: 0 2px 8px rgba(0, 0, 0, 0.4);
  }
  /* phone peek (wave U2 Task 4): a tappable 2-line preview shown while collapsed,
     instead of nothing — a glance at the latest activity without an extra tap. */
  .peek {
    display: flex;
    flex-direction: column;
    gap: var(--space-1);
    width: 100%;
    padding: 0 var(--space-3) var(--space-2);
    background: transparent;
    border: none;
    text-align: left;
    cursor: pointer;
    transition: background var(--dur-feedback) var(--ease-standard);
  }
  .peek:hover {
    background: var(--surface-hover);
  }
  .peek:active {
    background: color-mix(in srgb, var(--surface-hover) 80%, white 10%);
  }
  .peekrow {
    display: flex;
    align-items: baseline;
    gap: var(--space-2);
    font-size: var(--text-xs);
    line-height: 1.4;
  }
  .peektext {
    flex: 1;
    min-width: 0;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    color: var(--em-mid);
    font-size: var(--text-2xs);
  }
  /* phone (wave U2 Task 4): the dock row is content-sized (not a 1fr rail), so
     height:100% would collapse — size to content, and bound the expanded list
     so it scrolls internally instead of swallowing the page. */
  @media (max-width: 640px) {
    .log {
      height: auto;
    }
    .logbox {
      max-height: 32vh;
      flex: none;
    }
  }
</style>
