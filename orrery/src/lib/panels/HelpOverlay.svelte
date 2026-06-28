<script lang="ts">
  // HelpOverlay — the keyboard-shortcut LEGEND modal (Wave 3, H-polish-help).
  // A glance card listing every key the instrument answers to, in the same
  // boxed-mono / dot-header vocabulary as the other panels. The orchestrator
  // renders it from +page when showHelp is true; ? toggles it, Esc closes.
  //
  // Modal contract mirrors TuningConsole: --scrim backdrop, role=dialog +
  // aria-modal, focus moves in on open + restores to the trigger on close, and
  // Tab/Shift+Tab are trapped inside the card (WCAG 2.4.3 + 2.1.2).

  import { onMount } from 'svelte';

  let { onClose }: { onClose: () => void } = $props();

  // ── the legend. `keys` render as boxed-mono chips; a verb is paired with each
  //    so the shortcut survives a glance (and a screen reader) without colour. ──
  const SHORTCUTS: { keys: string[]; verb: string }[] = [
    { keys: ['?'], verb: 'toggle this help' },
    { keys: ['Esc'], verb: 'close · leave a Body' },
    { keys: ['i'], verb: 'Ignite the loop' },
    { keys: ['b'], verb: 'Brake at the next phase' },
    { keys: ['r'], verb: 'Reignite a banked ember' },
    { keys: ['click'], verb: 'inspect a planet' },
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
      <span id="help-title" class="title mono">KEYBOARD</span>
      <span class="sub mono">the keys the instrument answers to</span>
      <button class="x" aria-label="close" onclick={onClose}>✕</button>
    </header>

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

    <footer class="ftr mono">
      the run-controls (Ignite · Brake · Reignite) act inside a System.
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
    width: min(380px, 94vw);
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
    min-width: 84px;
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

  .ftr {
    font-size: var(--text-2xs);
    color: var(--text-meta);
    letter-spacing: 0.04em;
    border-top: 1px solid var(--hairline);
    padding-top: var(--space-3);
  }
</style>
