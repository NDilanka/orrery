<script lang="ts">
  // DecisionSheet — the answer-from-ambient surface. When a loop BLOCKS on a
  // review/retro question while the phone is in the Planetarium (ambient) view,
  // the operator must be able to answer WITHOUT switching back to the full
  // instrument. This is a compact bottom-sheet modal that shows the first
  // pending question and a textarea; Send answers it and closes, and we STAY in
  // Planetarium (the parent owns the mode, this sheet never touches it).
  //
  // It mirrors QAConsole's send path (threaded answer() + optimistic
  // answerLocally) and TuningConsole's modal contract (scrim, Esc, focus
  // move-in / trap / restore).

  import { onMount } from 'svelte';
  import { runStore } from '../stores/run.svelte';
  import { uiStore } from '../stores/ui.svelte';
  import type { Qa } from '../types';

  let {
    answer,
    observeOnly = false,
    onClose,
  }: {
    answer?: (qid: string, text: string) => void | Promise<void>;
    observeOnly?: boolean;
    onClose: () => void;
  } = $props();

  const s = $derived(runStore.state);
  // the first unanswered question — the one blocking the loop right now.
  const q = $derived<Qa | null>(s.questions.find((x) => x.a == null) ?? null);
  const remaining = $derived(s.questions.filter((x) => x.a == null).length);

  let text = $state('');
  let busy = $state(false);

  async function send() {
    const body = text.trim();
    if (!body || busy || observeOnly || !q) return;
    busy = true;
    try {
      await answer?.(q.id, body);
      // optimistic: stamp locally so the card reflects the send immediately and
      // the parent's `pending` empties even before the engine round-trips.
      runStore.answerLocally(q.id, body);
      onClose();
    } finally {
      busy = false;
    }
  }

  function onKey(e: KeyboardEvent) {
    // Cmd/Ctrl+Enter sends (Enter alone keeps newlines for long answers).
    if ((e.metaKey || e.ctrlKey) && e.key === 'Enter') {
      e.preventDefault();
      void send();
    }
  }

  // ── modal contract: scrim, Esc, focus move-in / trap / restore ─────────────
  let dialogEl = $state<HTMLDivElement | null>(null);
  let textareaEl = $state<HTMLTextAreaElement | null>(null);
  let triggerEl: HTMLElement | null = null;

  function focusable(): HTMLElement[] {
    if (!dialogEl) return [];
    return Array.from(
      dialogEl.querySelectorAll<HTMLElement>(
        'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])',
      ),
    ).filter((el) => el.offsetParent !== null || el === document.activeElement);
  }

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
    triggerEl = document.activeElement as HTMLElement | null;
    // focus the textarea (or the dialog if observe-only disabled it) on open
    queueMicrotask(() => {
      (textareaEl ?? dialogEl)?.focus();
    });
    return () => triggerEl?.focus?.();
  });

  function fmtKind(k: Qa['kind']): string {
    return k === 'retro' ? 'RETRO' : 'REVIEW';
  }
</script>

<!-- scrim + bottom-sheet -->
<div class="scrim" role="presentation" onclick={onClose}>
  <div
    class="sheet"
    class:reduced={uiStore.reducedMotion}
    role="dialog"
    aria-modal="true"
    aria-label="Answer the loop's pending decision"
    tabindex="-1"
    bind:this={dialogEl}
    onclick={(e) => e.stopPropagation()}
    onkeydown={onDialogKeydown}
  >
    {#if q}
      <header class="hdr">
        <div class="meta mono">
          <span class="kind">◈ {fmtKind(q.kind)}</span>
          <span class="dot" aria-hidden="true">·</span>
          <span class="turn num">turn {q.turn}</span>
          {#if q.epic}
            <span class="dot" aria-hidden="true">·</span>
            <span class="epic">{q.epic}</span>
          {/if}
          {#if remaining > 1}
            <span class="dot" aria-hidden="true">·</span>
            <span class="more num">{remaining - 1} more after this</span>
          {/if}
        </div>
        <button class="x" aria-label="close" onclick={onClose}>✕</button>
      </header>

      <p class="qtext">{q.q}</p>

      {#if observeOnly}
        <div class="observe mono">
          observe-only · open on the host (or with a token) to answer
        </div>
      {:else}
        <textarea
          class="answer"
          bind:this={textareaEl}
          bind:value={text}
          onkeydown={onKey}
          placeholder="Type your decision… (⌘/Ctrl+Enter to send)"
          rows="3"
        ></textarea>
      {/if}

      <div class="actions">
        <button class="ghost mono" onclick={onClose}>cancel</button>
        <button
          class="send mono"
          disabled={observeOnly || busy || !text.trim()}
          onclick={send}
        >
          {busy ? 'sending…' : '↩ Send'}
        </button>
      </div>
    {/if}
  </div>
</div>

<style>
  .scrim {
    position: absolute;
    inset: 0;
    display: flex;
    align-items: flex-end;
    justify-content: center;
    background: var(--scrim);
    backdrop-filter: blur(4px);
    z-index: 41; /* above the planetarium overlay (z 13) */
    padding: var(--space-4);
  }
  .sheet {
    width: min(560px, 100%);
    background: linear-gradient(180deg, var(--surface-2), var(--surface-1));
    border: 1px solid var(--panel-edge);
    border-radius: var(--radius);
    box-shadow:
      0 24px 80px rgba(0, 0, 0, 0.6),
      inset 0 1px 0 rgba(201, 162, 75, 0.12);
    display: flex;
    flex-direction: column;
    gap: var(--space-3);
    padding: var(--space-4);
    animation: sheetIn var(--dur-mid) var(--ease-out);
  }
  @keyframes sheetIn {
    from {
      transform: translateY(16px);
      opacity: 0;
    }
    to {
      transform: translateY(0);
      opacity: 1;
    }
  }
  .sheet.reduced {
    animation: none;
  }

  .hdr {
    display: flex;
    align-items: baseline;
    gap: var(--space-2);
  }
  .meta {
    display: flex;
    align-items: baseline;
    flex-wrap: wrap;
    gap: 6px;
    font-size: var(--text-2xs);
    letter-spacing: 0.1em;
    color: var(--text-meta);
    text-transform: uppercase;
  }
  .meta .kind {
    color: var(--crimson);
    font-weight: 600;
  }
  .meta .epic {
    color: var(--brass);
  }
  .meta .dot {
    color: var(--text-faint);
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
    font-size: 11px;
    flex: none;
  }
  .x:hover {
    border-color: var(--crimson);
    color: var(--crimson);
  }

  .qtext {
    margin: 0;
    font-family: var(--font-grotesk);
    font-size: 14px;
    line-height: 1.5;
    color: var(--starlight);
  }

  .answer {
    width: 100%;
    background: var(--void-3);
    border: 1px solid var(--hairline);
    border-radius: 6px;
    color: var(--starlight);
    font-family: var(--font-grotesk);
    font-size: 13px;
    line-height: 1.5;
    padding: 9px 11px;
    resize: vertical;
    transition: border-color 0.18s;
    box-sizing: border-box;
  }
  .answer:focus {
    border-color: var(--brass);
  }

  .observe {
    font-size: var(--text-2xs);
    letter-spacing: 0.04em;
    color: var(--text-meta);
    padding: 9px 11px;
    border: 1px dashed var(--hairline);
    border-radius: 6px;
  }

  .actions {
    display: flex;
    justify-content: flex-end;
    gap: var(--space-2);
  }
  .ghost {
    background: transparent;
    border: 1px solid var(--hairline);
    color: var(--text-dim);
    border-radius: var(--radius-pill);
    padding: 8px 16px;
    font-size: 11px;
    cursor: pointer;
  }
  .ghost:hover {
    color: var(--starlight);
  }
  .send {
    background: color-mix(in srgb, var(--brass) 14%, transparent);
    border: 1px solid var(--brass);
    color: var(--brass);
    border-radius: var(--radius-pill);
    padding: 8px 20px;
    font-size: 11px;
    font-weight: 600;
    letter-spacing: 0.08em;
    cursor: pointer;
    transition: all 0.16s;
  }
  .send:hover:not(:disabled) {
    background: color-mix(in srgb, var(--brass) 24%, transparent);
    transform: translateY(-1px);
  }
  .send:disabled {
    opacity: 0.4;
    cursor: default;
  }
</style>
