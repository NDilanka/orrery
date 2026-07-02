<script lang="ts">
  // DecisionSheet — the answer-from-ambient surface. When a loop BLOCKS on a
  // review/retro question while the phone is in the Planetarium (ambient) view,
  // the operator must be able to answer WITHOUT switching back to the full
  // instrument. This is a compact bottom-sheet modal that shows the first
  // pending question and a textarea; Send answers it and closes, and we STAY in
  // Planetarium (the parent owns the mode, this sheet never touches it).
  //
  // It shares QAConsole's send path (`../actions/answerFlow.ts` — threaded
  // answer() + optimistic answerLocally) and TuningConsole's modal contract
  // (scrim, Esc, focus move-in / trap / restore — `../actions/focusTrap.ts`).

  import { runStore } from '../stores/run.svelte';
  import { uiStore } from '../stores/ui.svelte';
  import { sessionStore } from '../stores/session.svelte';
  import { focusTrap } from '../actions/focusTrap';
  import { sendAnswer, onSubmitKey } from '../actions/answerFlow';
  import type { Qa } from '../types';

  let { onClose }: { onClose: () => void } = $props();

  const s = $derived(runStore.state);
  // the first unanswered question — the one blocking the loop right now.
  const q = $derived<Qa | null>(s.questions.find((x) => x.a == null) ?? null);
  const remaining = $derived(s.questions.filter((x) => x.a == null).length);
  const observeOnly = $derived(sessionStore.observeOnly);

  let text = $state('');
  let busy = $state(false);
  let textareaEl = $state<HTMLTextAreaElement | null>(null);

  async function send() {
    if (busy || observeOnly || !q) return;
    busy = true;
    try {
      const sent = await sendAnswer(sessionStore.answer.bind(sessionStore), q.id, text);
      if (sent) onClose();
    } finally {
      busy = false;
    }
  }

  function fmtKind(k: Qa['kind']): string {
    return k === 'retro' ? 'RETRO' : 'REVIEW';
  }
</script>

<!-- scrim + bottom-sheet -->
<div class="scrim" role="presentation" onclick={onClose}>
  <div
    class="sheet floating-card"
    class:reduced={uiStore.reducedMotion}
    role="dialog"
    aria-modal="true"
    aria-label="Answer the loop's pending decision"
    tabindex="-1"
    use:focusTrap={{ onClose, initialFocus: () => textareaEl }}
    onclick={(e) => e.stopPropagation()}
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
          onkeydown={(e) => onSubmitKey(e, () => void send())}
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
    z-index: var(--z-sheet); /* above the planetarium overlay (--z-scene-overlay) */
    padding: var(--space-4);
  }
  .sheet {
    width: min(560px, 100%);
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
    /* unified header pattern (M1.2): 11px caps-spaced --text-xs label, matching
       HelpOverlay's section labels and ShareButton's popover title. */
    font-size: var(--text-xs);
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
    font-size: var(--text-xs);
    flex: none;
    transition: border-color var(--dur-feedback) var(--ease-standard),
      color var(--dur-feedback) var(--ease-standard);
  }
  .x:hover {
    border-color: var(--crimson);
    color: var(--crimson);
  }

  .qtext {
    margin: 0;
    font-family: var(--font-grotesk);
    /* 14px sat exactly between --text-md(13)/--text-lg(15); rounded up — this is the
       question prompt itself, the most important line in the sheet. */
    font-size: var(--text-lg);
    line-height: 1.5;
    color: var(--starlight);
  }

  .answer {
    width: 100%;
    background: var(--void-3);
    border: 1px solid var(--hairline);
    border-radius: var(--radius-sm);
    color: var(--starlight);
    font-family: var(--font-grotesk);
    font-size: var(--text-md);
    line-height: 1.5;
    padding: 9px 11px;
    resize: vertical;
    transition: border-color var(--dur-fast) var(--ease-standard);
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
    border-radius: var(--radius-sm);
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
    font-size: var(--text-xs);
    cursor: pointer;
    transition: color var(--dur-feedback) var(--ease-standard);
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
    font-size: var(--text-xs);
    font-weight: 600;
    letter-spacing: 0.08em;
    cursor: pointer;
    transition: all var(--dur-fast) var(--ease-standard);
  }
  .send:hover:not(:disabled) {
    background: color-mix(in srgb, var(--brass) 24%, transparent);
    transform: translateY(-1px);
  }
  .send:disabled {
    opacity: 0.4;
    cursor: not-allowed;
  }
</style>
