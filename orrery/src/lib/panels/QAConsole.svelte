<script lang="ts">
  // QAConsole (A8) — the answer-from-UI surface. Renders any PENDING review/retro
  // question (RunState.questions whose `a == null`) as an Oracle card with a text
  // input + Send. Send calls the transport's answer() method:
  //   - desktop → invoke('answer_question', { loopId, loopsDir, qid, text })
  //   - web     → POST /api/control { action:'answer', qid, text } (token-gated)
  //   - dev     → no-ops gracefully (no live engine inbox)
  // The resulting `review-answer` event flows back through the reducer and the
  // question drops out of the pending set (its `a` becomes non-null) — so the
  // card reflects the answer and disappears. Optimistically we also stamp the
  // local answer so the UI feels immediate even before the event round-trips.
  //
  // Reads the runes store directly for pending questions; `answer` + an
  // observe-only flag are passed down from the shell (the shell owns the
  // transport). When observe-only (web, no token) the input is disabled with a
  // clear note.

  import { runStore } from '../stores/run.svelte';
  import type { Qa } from '../types';

  let {
    answer,
    observeOnly = false,
  }: {
    answer?: (qid: string, text: string) => void | Promise<void>;
    observeOnly?: boolean;
  } = $props();

  const s = $derived(runStore.state);

  // pending = a question the engine surfaced that nobody has answered yet
  const pending = $derived<Qa[]>(s.questions.filter((q) => q.a == null));

  // local draft per question id + a sending/answered flag for optimistic UX
  let drafts = $state<Record<string, string>>({});
  let sending = $state<Record<string, boolean>>({});
  let sentLocally = $state<Record<string, string>>({}); // qid → text we sent

  async function send(q: Qa) {
    const text = (drafts[q.id] ?? '').trim();
    if (!text || observeOnly) return;
    sending = { ...sending, [q.id]: true };
    try {
      await answer?.(q.id, text);
      // optimistic: reflect our answer locally so the card updates immediately
      // even before the engine writes a `review-answer` back through the reducer.
      sentLocally = { ...sentLocally, [q.id]: text };
      runStore.answerLocally(q.id, text);
      drafts = { ...drafts, [q.id]: '' };
    } finally {
      sending = { ...sending, [q.id]: false };
    }
  }

  function onKey(e: KeyboardEvent, q: Qa) {
    // Cmd/Ctrl+Enter sends (Enter alone keeps newlines for long answers)
    if ((e.metaKey || e.ctrlKey) && e.key === 'Enter') {
      e.preventDefault();
      void send(q);
    }
  }

  // also surface the most-recently answered question briefly as a confirmation
  const lastAnswered = $derived.by<Qa | null>(() => {
    const answered = s.questions.filter((q) => q.a != null && sentLocally[q.id]);
    return answered.length ? answered[answered.length - 1] : null;
  });
</script>

{#if pending.length}
  <div class="qa">
    <div class="qhead">
      <span class="qmote">?</span>
      <span class="qtitle mono">DECISION CHAMBER</span>
      <span class="qcount mono">{pending.length} pending</span>
    </div>

    {#each pending as q (q.id)}
      <div class="qcard">
        <div class="qmeta mono">
          <span class="kind {q.kind}">{q.kind}</span>
          <span class="turn">turn {q.turn}</span>
          {#if q.epic}<span class="epic">epic {q.epic}</span>{/if}
        </div>
        <div class="qbody">{q.q || '(awaiting question text…)'}</div>

        {#if observeOnly}
          <div class="observe mono">observe-only · open on the host (or with a token) to answer</div>
        {:else}
          <textarea
            class="qinput mono"
            placeholder="your decision… (⌘/Ctrl+Enter to send)"
            rows="3"
            bind:value={drafts[q.id]}
            onkeydown={(e) => onKey(e, q)}
            disabled={sending[q.id]}
          ></textarea>
          <div class="qactions">
            <button
              class="send"
              onclick={() => send(q)}
              disabled={sending[q.id] || !(drafts[q.id] ?? '').trim()}
            >
              {sending[q.id] ? 'sending…' : 'Send ▷'}
            </button>
          </div>
        {/if}
      </div>
    {/each}
  </div>
{:else if lastAnswered}
  <div class="qa answered">
    <div class="qhead">
      <span class="qmote done">✓</span>
      <span class="qtitle mono">ANSWERED · turn {lastAnswered.turn}</span>
    </div>
    <div class="qbody small">{lastAnswered.a}</div>
  </div>
{/if}

<style>
  .qa {
    position: absolute;
    top: 50%;
    right: 18px;
    transform: translateY(-50%);
    width: min(360px, 92vw);
    max-height: 76vh;
    overflow-y: auto;
    display: flex;
    flex-direction: column;
    gap: 12px;
    padding: 16px 18px;
    background: var(--panel);
    border: 1px solid var(--panel-edge);
    border-left: 3px solid var(--plasma-cyan);
    border-radius: var(--radius);
    backdrop-filter: blur(10px);
    z-index: 14;
  }
  .qa.answered {
    border-left-color: var(--plasma-green);
  }
  .qhead {
    display: flex;
    align-items: center;
    gap: 9px;
  }
  .qmote {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    width: 22px;
    height: 22px;
    border-radius: 50%;
    background: color-mix(in srgb, var(--plasma-cyan) 18%, transparent);
    color: var(--plasma-cyan);
    font-weight: 700;
    font-size: 13px;
    animation: breathe 2.4s ease-in-out infinite;
  }
  .qmote.done {
    background: color-mix(in srgb, var(--plasma-green) 18%, transparent);
    color: var(--plasma-green);
    animation: none;
  }
  @keyframes breathe {
    0%, 100% { opacity: 1; }
    50% { opacity: 0.5; }
  }
  @media (prefers-reduced-motion: reduce) {
    .qmote { animation: none; }
  }
  .qtitle {
    font-size: 10px;
    letter-spacing: 0.16em;
    color: var(--plasma-cyan);
    flex: 1;
  }
  .qcount {
    font-size: 10px;
    color: var(--text-faint);
  }
  .qcard {
    display: flex;
    flex-direction: column;
    gap: 9px;
    padding-top: 10px;
    border-top: 1px solid var(--hairline);
  }
  .qcard:first-of-type {
    border-top: none;
    padding-top: 0;
  }
  .qmeta {
    display: flex;
    align-items: center;
    gap: 8px;
    font-size: 9.5px;
    text-transform: uppercase;
    letter-spacing: 0.08em;
    color: var(--text-faint);
  }
  .kind {
    padding: 2px 7px;
    border-radius: var(--radius-pill);
    border: 1px solid var(--hairline);
    color: var(--text-dim);
  }
  .kind.review {
    color: var(--amber);
    border-color: color-mix(in srgb, var(--amber) 35%, transparent);
  }
  .kind.retro {
    color: var(--plasma-cyan);
    border-color: color-mix(in srgb, var(--plasma-cyan) 35%, transparent);
  }
  .qbody {
    font-size: 12.5px;
    line-height: 1.5;
    color: var(--starlight);
    white-space: pre-wrap;
    word-break: break-word;
    max-height: 30vh;
    overflow-y: auto;
  }
  .qbody.small {
    font-size: 11.5px;
    color: var(--text-dim);
  }
  .qinput {
    width: 100%;
    resize: vertical;
    background: var(--void-3);
    border: 1px solid var(--hairline);
    border-radius: 8px;
    color: var(--starlight);
    font-size: 12px;
    line-height: 1.45;
    padding: 9px 11px;
  }
  .qinput:focus {
    outline: none;
    border-color: color-mix(in srgb, var(--plasma-cyan) 50%, transparent);
  }
  .qinput:disabled {
    opacity: 0.5;
  }
  .qactions {
    display: flex;
    justify-content: flex-end;
  }
  .send {
    font-family: var(--font-grotesk);
    font-size: 12px;
    font-weight: 600;
    letter-spacing: 0.04em;
    padding: 7px 16px;
    border-radius: var(--radius-pill);
    border: 1px solid color-mix(in srgb, var(--plasma-cyan) 45%, transparent);
    background: color-mix(in srgb, var(--plasma-cyan) 10%, transparent);
    color: var(--plasma-cyan);
    cursor: pointer;
    transition: background 0.18s, transform 0.1s;
  }
  .send:hover:not(:disabled) {
    background: color-mix(in srgb, var(--plasma-cyan) 18%, transparent);
    transform: translateY(-1px);
  }
  .send:disabled {
    opacity: 0.4;
    cursor: default;
  }
  .observe {
    font-size: 10.5px;
    color: var(--text-faint);
    font-style: italic;
    padding: 6px 0;
  }
</style>
