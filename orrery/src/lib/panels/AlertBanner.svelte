<script lang="ts">
  // AlertBanner (wave U4 Task 3) — dismissible top-of-viewport strip for unattended-run
  // events (stores/alerts.svelte.ts does the edge-detection; this just renders the result).
  // Glyph + plain one-liner, never hue alone. No sound, no OS notification (native
  // notifications need a Tauri plugin — deferred, see the U4 report), no flashing (reduced
  // motion drops even the slide-in entrance).

  import { alertStore, type RunAlert } from '../stores/alerts.svelte';
  import { uiStore } from '../stores/ui.svelte';
  import { settingsStore } from '../stores/settings.svelte';

  let { onJump }: { onJump: (loopId: string) => void } = $props();

  const GLYPH: Record<RunAlert['kind'], string> = {
    failed: '⚠',
    handoff: '◈',
    quota: '❄',
    done: '✓',
    stopped: '⏸',
  };

  // Cap the visible stack — a multi-loop bad night (several failures + handoffs) shouldn't
  // fill the viewport. The store has no ordering concept of its own (plain insertion order,
  // see alerts.svelte.ts), so severity ordering lives here: failures sort ahead of the amber
  // kinds (handoff/quota), stable otherwise.
  const MAX_VISIBLE = 3;
  // failures lead, then the amber needs-you kinds, then the monochrome informational
  // kinds (done/stopped) — neither is urgent, so they sink to the bottom of the stack.
  const SEVERITY_RANK: Record<RunAlert['kind'], number> = {
    failed: 0,
    handoff: 1,
    quota: 1,
    done: 2,
    stopped: 2,
  };

  let expanded = $state(false);

  // Alert sound (settings.notifications.sound): a short, quiet 2-tone sine blip synthesized
  // via the Web Audio API — no asset fetch (offline Tauri). Edge-detected on alert ids so it
  // fires once per NEW alert, never on a re-render or when an alert is dismissed. Primed on
  // first run so pre-existing alerts at mount don't blip.
  let knownIds = new Set<string>();
  let primed = false;
  $effect(() => {
    const ids = new Set(alertStore.alerts.map((a) => a.id));
    if (!primed) {
      knownIds = ids;
      primed = true;
      return;
    }
    let hasNew = false;
    for (const id of ids) if (!knownIds.has(id)) hasNew = true;
    knownIds = ids;
    // short-circuit keeps the effect from subscribing to `sound` unless something new fired
    if (hasNew && settingsStore.data.notifications.sound) playChime();
  });

  function playChime(): void {
    try {
      const Ctx =
        typeof window !== 'undefined'
          ? (window.AudioContext ?? (window as unknown as { webkitAudioContext?: typeof AudioContext }).webkitAudioContext)
          : undefined;
      if (!Ctx) return;
      const ctx = new Ctx();
      // a context created outside a user gesture can start 'suspended' (autoplay policy)
      // and silently swallow the blip — nudge it awake; a refusal just means no chime.
      if (ctx.state === 'suspended') void ctx.resume().catch(() => {});
      const now = ctx.currentTime;
      [660, 880].forEach((freq, i) => {
        const osc = ctx.createOscillator();
        const gain = ctx.createGain();
        osc.type = 'sine';
        osc.frequency.value = freq;
        const t0 = now + i * 0.11;
        gain.gain.setValueAtTime(0, t0);
        gain.gain.linearRampToValueAtTime(0.1, t0 + 0.02);
        gain.gain.exponentialRampToValueAtTime(0.0001, t0 + 0.11);
        osc.connect(gain).connect(ctx.destination);
        osc.start(t0);
        osc.stop(t0 + 0.12);
      });
      // free the context once the blip (~230ms) has played out
      setTimeout(() => void ctx.close().catch(() => {}), 400);
    } catch {
      /* audio unavailable / blocked — a missing chime is never fatal */
    }
  }

  let sorted = $derived(
    [...alertStore.alerts].sort((a, b) => SEVERITY_RANK[a.kind] - SEVERITY_RANK[b.kind]),
  );
  let overflow = $derived(sorted.length > MAX_VISIBLE);
  let visible = $derived(expanded ? sorted : sorted.slice(0, MAX_VISIBLE));
  let hidden = $derived(sorted.slice(MAX_VISIBLE));
  // never hue-alone: the summary's tint is a shorthand for the WORST kind in the hidden
  // tail — red if it holds a failure, amber if it holds a needs-you (handoff/quota), and
  // MONOCHROME when it's only the informational done/stopped kinds (design law: those are
  // never red/amber). The "+N more" text carries the actual information either way.
  let hiddenHasFailure = $derived(hidden.some((a) => a.kind === 'failed'));
  let hiddenHasNeedsYou = $derived(hidden.some((a) => SEVERITY_RANK[a.kind] === 1));
</script>

{#if alertStore.alerts.length}
  <div class="alerts" role="alert">
    {#each visible as a (a.id)}
      <div class="bar {a.kind}" class:reduced={uiStore.reducedMotion}>
        <span class="glyph" aria-hidden="true">{GLYPH[a.kind]}</span>
        <span class="msg">{a.message}</span>
        {#if a.source === 'cosmos'}
          <button class="jump btn btn-ghost btn-sm" onclick={() => onJump(a.loopId)}>enter loop →</button>
        {/if}
        <button
          class="dismiss btn btn-ghost btn-icon"
          aria-label="dismiss alert"
          onclick={() => alertStore.dismiss(a.id)}>✕</button
        >
      </div>
    {/each}
    {#if overflow}
      <button
        type="button"
        class="bar summary"
        class:sev-amber={!hiddenHasFailure && hiddenHasNeedsYou}
        class:sev-mono={!hiddenHasFailure && !hiddenHasNeedsYou}
        class:reduced={uiStore.reducedMotion}
        onclick={() => (expanded = !expanded)}
      >
        <span class="glyph" aria-hidden="true">
          {hiddenHasFailure ? GLYPH.failed : hiddenHasNeedsYou ? GLYPH.handoff : GLYPH.done}
        </span>
        <span class="msg">{expanded ? 'show less' : `+${hidden.length} more`}</span>
      </button>
    {/if}
  </div>
{/if}

<style>
  .alerts {
    /* a REAL flex item (routes/+page.svelte's `.stage` is a column flexbox) — takes its
       natural height at the very top and pushes `.stage-body` (navbar / ModeBar /
       ignite-fab, all position:absolute within it) down below it, rather than floating
       over them. */
    flex: none;
    display: flex;
    flex-direction: column;
  }
  .bar {
    display: flex;
    align-items: center;
    gap: var(--space-3);
    padding: 9px var(--chrome-inset);
    background: color-mix(in srgb, var(--crimson) 14%, var(--void-2));
    border-bottom: 1px solid color-mix(in srgb, var(--crimson) 35%, transparent);
    backdrop-filter: blur(8px);
    animation: slideDown var(--dur-mid) var(--ease-out);
  }
  .bar.reduced {
    animation: none;
  }
  @keyframes slideDown {
    from {
      transform: translateY(-100%);
    }
    to {
      transform: translateY(0);
    }
  }
  /* M4.5: handoff and quota are both "needs-you" alerts (docs/ui-modernization-plan.md §5;
     matches Hud.svelte's status-pill.frost/.beacon reclassification) — only a genuine
     failure stays on the base crimson tint above; these two share the amber family. */
  .bar.quota,
  .bar.handoff {
    background: color-mix(in srgb, var(--amber) 14%, var(--void-2));
    border-bottom-color: color-mix(in srgb, var(--amber) 35%, transparent);
  }
  /* DESIGN LAW: a done or stopped loop is NOT a failure and NOT a needs-you — it stays
     monochrome, never red/amber. `done` reads as a bright/positive resolution (em-hi white
     emphasis); `stopped` is a neutral, quiet parked state (dim). */
  .bar.done,
  .bar.stopped {
    background: color-mix(in srgb, var(--em-hi) 8%, var(--void-2));
    border-bottom-color: var(--hairline);
  }
  /* summary row (overflow cap) — a real <button> so it's keyboard-reachable; reset the
     native button chrome back to the plain `.bar` look. `.sev-amber` is a severity
     shorthand (not a real alert kind) for "no failure in the hidden tail"; unset it falls
     through to the base crimson tint above, same family as a failed alert. */
  .bar.summary {
    appearance: none;
    width: 100%;
    margin: 0;
    border: none;
    border-bottom: 1px solid color-mix(in srgb, var(--crimson) 35%, transparent);
    font: inherit;
    text-align: left;
    cursor: pointer;
  }
  .bar.summary.sev-amber {
    background: color-mix(in srgb, var(--amber) 14%, var(--void-2));
    border-bottom-color: color-mix(in srgb, var(--amber) 35%, transparent);
  }
  .bar.summary.sev-amber .glyph {
    color: var(--amber);
  }
  /* a hidden tail of ONLY done/stopped alerts is informational, never an alert — it takes
     the same monochrome treatment as the .bar.done/.bar.stopped rows above (design law). */
  .bar.summary.sev-mono {
    background: color-mix(in srgb, var(--em-hi) 8%, var(--void-2));
    border-bottom-color: var(--hairline);
  }
  .bar.summary.sev-mono .glyph {
    color: var(--em-mid);
  }
  .glyph {
    flex: none;
    font-size: var(--text-lg);
    color: var(--crimson);
  }
  .bar.quota .glyph,
  .bar.handoff .glyph {
    color: var(--amber);
  }
  .bar.done .glyph {
    color: var(--em-hi);
  }
  .bar.stopped .glyph {
    color: var(--em-mid);
  }
  .msg {
    flex: 1;
    min-width: 0;
    font-family: var(--font-grotesk);
    font-size: var(--text-sm);
    font-weight: 600;
    letter-spacing: 0.01em;
    color: var(--em-hi);
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  /* .btn/.btn-ghost/.btn-sm (primitives.css) supply the shape/border/hover — matches
     LogPanel's "jump to newest" chip, the app's one ghost-button convention. */
  .jump {
    flex: none;
  }
  /* .btn-icon squares the padding for the ✕ glyph (matches TransportBar's icon buttons). */
  .dismiss {
    flex: none;
  }

  @media (max-width: 640px) {
    .bar {
      padding: 8px var(--space-2);
      gap: var(--space-2);
    }
    .msg {
      font-size: var(--text-xs);
      white-space: normal;
    }
  }
</style>
