<script lang="ts">
  // HelpOverlay — the instrument's REFERENCE CARD (Wave U1, plain-language pass).
  // A terse cheat-sheet covering the views, the five rest-states, the trust
  // signal (claimed vs verified), the run controls + keyboard, and the canvas
  // grammar — all in the same boxed-mono / dot-header vocabulary as the other
  // panels. The orchestrator renders it from +page when showHelp is true; ?
  // toggles it, Esc closes.
  //
  // Modal contract mirrors TuningConsole: --scrim backdrop, role=dialog +
  // aria-modal, focus moves in on open + restores to the trigger on close, and
  // Tab/Shift+Tab are trapped inside the card (WCAG 2.4.3 + 2.1.2).

  import { onMount } from 'svelte';

  let { onClose }: { onClose: () => void } = $props();

  // ── 1. VIEWS — the three zoom levels ─────────────────────────────────────
  const VIEWS: { term: string; desc: string }[] = [
    { term: 'Cosmos', desc: 'all loops at a glance.' },
    {
      term: 'System',
      desc:
        'one loop — Observatory (live) · Ambient (full-screen overnight view) · Rewind (scrub a replay’s timeline).',
    },
    { term: 'Body', desc: 'one work item’s detail.' },
  ];

  // ── 2. STATUS — the five rest-states (not-running palettes). Glyphs match
  //    Cosmos.svelte's stateGlyph(); labels match the wave U1 copy sweep used
  //    everywhere else in the app, so this card never disagrees with the UI. ──
  const STATUS: { glyph: string; label: string; meaning: string }[] = [
    {
      glyph: '✓',
      label: 'DONE · VERIFIED',
      meaning: 'an independent verifier confirmed it — nothing to do.',
    },
    {
      glyph: '▬',
      label: 'PAUSED',
      meaning: 'stopped mid-work, resumable from checkpoint — press Resume to continue.',
    },
    {
      glyph: '❄',
      label: 'QUOTA PAUSE',
      meaning: 'waiting out a rate limit — resumes automatically, or Resume once the window opens.',
    },
    {
      glyph: '!',
      label: 'NEEDS YOU',
      meaning: 'the loop is waiting on a human answer or decision.',
    },
    {
      glyph: '⚠',
      label: 'FAILED',
      meaning: 'the run crashed — Resume from the last checkpoint, or Restart fresh.',
    },
  ];

  // ── 3. TRUST is a static two-sentence blurb, inlined directly in the markup
  //    below (no data array needed for a single paragraph). ──

  // ── 4. CONTROLS — keyboard legend. `keys` render as boxed-mono chips; a verb
  //    is paired with each so the shortcut survives a glance (and a screen
  //    reader) without colour. Exactly 6 entries, by design — verified against
  //    +page.svelte's onKeydown. ──
  const SHORTCUTS: { keys: string[]; verb: string }[] = [
    { keys: ['?'], verb: 'toggle this help' },
    { keys: ['Esc'], verb: 'close help · leave a Body' },
    { keys: ['i'], verb: 'Start the loop' },
    { keys: ['b'], verb: 'Brake at the next phase' },
    { keys: ['r'], verb: 'Resume from checkpoint' },
    { keys: ['click'], verb: 'inspect a body / planet' },
  ];

  // ── 5. READING THE CANVAS — the Observatory's visual grammar, terse ──────
  const CANVAS: string[] = [
    'the star = the loop itself — its size tracks cumulative spend.',
    'ring segments = groups / epics.',
    'the ring tightening around the star = the budget ceiling — closes in as spend nears it.',
    'body/planet colour = status, never colour alone — each also carries a distinct ring, dash or glyph (a brass seal ring = verified · a dashed pulsing green ring = claimed, not yet verified).',
  ];

  // ── modal contract: focus move-in / trap / restore ──────────────────────────
  let dialogEl = $state<HTMLDivElement | null>(null);
  let triggerEl: HTMLElement | null = null;

  function focusable(): HTMLElement[] {
    if (!dialogEl) return [];
    return Array.from(
      dialogEl.querySelectorAll<HTMLElement>(
        'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])',
      ),
    ).filter((el) => el.offsetParent !== null || el === document.activeElement);
  }

  // Escape closes, Tab/Shift+Tab wrap inside the card.
  function onDialogKeydown(e: KeyboardEvent) {
    if (e.key === 'Escape') {
      e.preventDefault();
      onClose();
      return;
    }
    if (e.key !== 'Tab') return;
    const items = focusable();
    if (items.length === 0) return;
    const first = items[0];
    const last = items[items.length - 1];
    const active = document.activeElement as HTMLElement | null;
    if (e.shiftKey && (active === first || !dialogEl?.contains(active))) {
      e.preventDefault();
      last.focus();
    } else if (!e.shiftKey && active === last) {
      e.preventDefault();
      first.focus();
    }
  }

  onMount(() => {
    // capture the trigger so focus can be restored on close (WCAG 2.4.3)
    triggerEl = document.activeElement as HTMLElement | null;
    // move focus into the dialog: first focusable control, else the container
    queueMicrotask(() => {
      const items = focusable();
      (items[0] ?? dialogEl)?.focus();
    });
    // restore focus to whatever opened the overlay on teardown
    return () => triggerEl?.focus?.();
  });
</script>

<!-- scrim + dialog -->
<div class="scrim" role="presentation" onclick={onClose}>
  <div
    class="card"
    role="dialog"
    aria-modal="true"
    aria-labelledby="help-title"
    tabindex="-1"
    bind:this={dialogEl}
    onclick={(e) => e.stopPropagation()}
    onkeydown={onDialogKeydown}
  >
    <header class="hdr">
      <span class="dot" aria-hidden="true"></span>
      <span id="help-title" class="title mono">REFERENCE</span>
      <span class="sub mono">how to read the instrument</span>
      <button class="x" aria-label="close" onclick={onClose}>✕</button>
    </header>

    <section class="sec">
      <h3 class="h mono">VIEWS</h3>
      <dl class="reflist">
        {#each VIEWS as v (v.term)}
          <div class="rrow">
            <dt class="term mono">{v.term}</dt>
            <dd>{v.desc}</dd>
          </div>
        {/each}
      </dl>
    </section>

    <section class="sec">
      <h3 class="h mono">STATUS <span class="hnote">— rest states</span></h3>
      <dl class="legend">
        {#each STATUS as st (st.label)}
          <div class="srow">
            <dt class="keys">
              <kbd class="kbd mono">{st.glyph}</kbd>
            </dt>
            <dd class="verb"><strong class="slabel">{st.label}</strong> — {st.meaning}</dd>
          </div>
        {/each}
      </dl>
    </section>

    <section class="sec">
      <h3 class="h mono">TRUST</h3>
      <p class="prose">
        <strong class="slabel">Claimed</strong> — the agent asserts its own work passed.
        <strong class="slabel">Verified</strong> — an independent auditor (a separate check, not
        the same agent grading itself) confirmed it. Only verified work is DONE · VERIFIED.
      </p>
    </section>

    <section class="sec">
      <h3 class="h mono">CONTROLS</h3>
      <p class="prose">
        Start · Brake · phase · Brake · story · Stop now (most urgent — cooperative, not a kill) ·
        Resume (from a checkpoint) · Restart fresh.
      </p>
      <dl class="legend">
        {#each SHORTCUTS as s (s.verb)}
          <div class="srow">
            <dt class="keys">
              {#each s.keys as k (k)}
                <kbd class="kbd mono">{k}</kbd>
              {/each}
            </dt>
            <dd class="verb">{s.verb}</dd>
          </div>
        {/each}
      </dl>
    </section>

    <section class="sec">
      <h3 class="h mono">READING THE CANVAS</h3>
      <ul class="bullets">
        {#each CANVAS as line (line)}
          <li>{line}</li>
        {/each}
      </ul>
    </section>

    <footer class="ftr mono">
      the run-controls (Start · Brake · Resume) act inside a System.
    </footer>
  </div>
</div>

<style>
  .scrim {
    position: absolute;
    inset: 0;
    display: flex;
    align-items: center;
    justify-content: center;
    background: var(--scrim);
    backdrop-filter: blur(4px);
    z-index: 40;
    padding: var(--chrome-inset);
  }
  .card {
    width: min(460px, 94vw);
    max-height: 90vh;
    overflow-y: auto;
    background: linear-gradient(180deg, rgba(11, 14, 28, 0.96), rgba(7, 9, 18, 0.98));
    border: 1px solid var(--panel-edge);
    border-radius: var(--radius);
    box-shadow: 0 24px 80px rgba(0, 0, 0, 0.6), inset 0 1px 0 rgba(201, 162, 75, 0.12);
    display: flex;
    flex-direction: column;
    gap: var(--space-4);
    padding: var(--space-4) var(--space-5) var(--space-4);
  }

  .hdr {
    display: flex;
    align-items: baseline;
    gap: var(--space-2);
    border-bottom: 1px solid var(--hairline);
    padding-bottom: var(--space-3);
  }
  .dot {
    width: 7px;
    height: 7px;
    border-radius: 50%;
    background: var(--brass);
    box-shadow: 0 0 6px color-mix(in srgb, var(--brass) 70%, transparent);
    align-self: center;
    flex: none;
  }
  .title {
    font-size: var(--text-md);
    letter-spacing: 0.2em;
    color: var(--brass);
  }
  .sub {
    font-size: var(--text-2xs);
    letter-spacing: 0.08em;
    color: var(--text-meta);
  }
  .x {
    margin-left: auto;
    background: transparent;
    border: 1px solid var(--hairline);
    color: var(--text-dim);
    border-radius: var(--radius-pill);
    width: 24px;
    height: 24px;
    cursor: pointer;
    font-size: var(--text-xs);
    align-self: center;
  }
  .x:hover {
    border-color: var(--crimson);
    color: var(--crimson);
  }

  /* ── section scaffold: a small mono uppercase heading + its body, reusing
     the .title/.sub letterspaced convention but scaled down for in-card use ── */
  .sec {
    display: flex;
    flex-direction: column;
    gap: var(--space-2);
  }
  .h {
    margin: 0;
    font-size: var(--text-2xs);
    letter-spacing: 0.16em;
    color: var(--brass);
    opacity: 0.85;
  }
  .hnote {
    font-family: var(--font-grotesk);
    letter-spacing: 0.02em;
    color: var(--text-meta);
    opacity: 0.9;
  }
  .prose {
    margin: 0;
    font-size: var(--text-sm);
    color: var(--text-dim);
    line-height: 1.5;
  }
  .slabel {
    color: var(--starlight);
    font-weight: 600;
    letter-spacing: 0.01em;
  }

  /* VIEWS — term/desc definition rows */
  .reflist {
    margin: 0;
    display: flex;
    flex-direction: column;
    gap: var(--space-2);
  }
  .reflist .rrow {
    display: flex;
    align-items: baseline;
    gap: var(--space-3);
  }
  .reflist .term {
    flex: none;
    width: 60px;
    font-size: var(--text-xs);
    color: var(--starlight);
  }
  .reflist dd {
    margin: 0;
    font-size: var(--text-sm);
    color: var(--text-dim);
    line-height: 1.4;
  }

  /* STATUS + CONTROLS — key/glyph chip rows (shared pattern) */
  .legend {
    margin: 0;
    display: flex;
    flex-direction: column;
    gap: var(--space-3);
  }
  .srow {
    display: flex;
    align-items: baseline;
    gap: var(--space-3);
  }
  .keys {
    flex: none;
    display: flex;
    gap: var(--space-1);
    min-width: 40px;
  }
  /* boxed-mono key chips — the same border/box vocabulary as the log's .ev chip */
  .kbd {
    display: inline-block;
    min-width: 18px;
    text-align: center;
    padding: 2px 6px;
    font-size: var(--text-xs);
    color: var(--starlight);
    background: var(--void-3);
    border: 1px solid var(--hairline);
    border-bottom-color: var(--panel-edge);
    border-radius: 4px;
    line-height: 1.3;
  }
  .verb {
    margin: 0;
    font-size: var(--text-sm);
    color: var(--text-dim);
    line-height: 1.4;
  }

  /* READING THE CANVAS — terse bullets */
  .bullets {
    margin: 0;
    padding: 0;
    list-style: none;
    display: flex;
    flex-direction: column;
    gap: var(--space-2);
  }
  .bullets li {
    position: relative;
    padding-left: var(--space-3);
    font-size: var(--text-sm);
    color: var(--text-dim);
    line-height: 1.4;
  }
  .bullets li::before {
    content: '\00b7';
    position: absolute;
    left: 0;
    color: var(--brass);
  }

  .ftr {
    font-size: var(--text-2xs);
    color: var(--text-meta);
    letter-spacing: 0.04em;
    border-top: 1px solid var(--hairline);
    padding-top: var(--space-3);
  }
</style>
