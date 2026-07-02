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
  // local answer so the UI feels immediate even before the event round-trips
  // (`../actions/answerFlow.ts` — shared with DecisionSheet's single-question
  // send path).
  //
  // Reads the runes stores directly: the pending questions from runStore, and
  // the transport's answer()/observe-only flag from sessionStore (A4 — no more
  // prop-drilling `answer`/`observeOnly` down from +page.svelte). When
  // observe-only (web, no token) the input is disabled with a clear note.

  import { runStore } from '../stores/run.svelte';
  import { sessionStore } from '../stores/session.svelte';
  import { sendAnswer, onSubmitKey } from '../actions/answerFlow';
  import type { Qa } from '../types';

  const s = $derived(runStore.state);
  const observeOnly = $derived(sessionStore.observeOnly);

  // pending = a question the engine surfaced that nobody has answered yet
  const pending = $derived<Qa[]>(s.questions.filter((q) => q.a == null));

  // local draft per question id + a sending/answered flag for optimistic UX
  let drafts = $state<Record<string, string>>({});
  let sending = $state<Record<string, boolean>>({});
  let sentLocally = $state<Record<string, string>>({}); // qid → text we sent

  async function send(q: Qa) {
    if (observeOnly) return;
    const text = drafts[q.id] ?? '';
    sending = { ...sending, [q.id]: true };
    try {
      const sent = await sendAnswer(sessionStore.answer.bind(sessionStore), q.id, text);
      if (sent) {
        sentLocally = { ...sentLocally, [q.id]: text.trim() };
        drafts = { ...drafts, [q.id]: '' };
      }
    } finally {
      sending = { ...sending, [q.id]: false };
    }
  }

  function onKey(e: KeyboardEvent, q: Qa) {
    onSubmitKey(e, () => void send(q));
    // Escape blurs the textarea (a side panel must let focus leave — no trap)
    if (e.key === 'Escape') {
      (e.currentTarget as HTMLTextAreaElement).blur();
    }
  }

  // also surface the most-recently answered question briefly as a confirmation
  const lastAnswered = $derived.by<Qa | null>(() => {
    const answered = s.questions.filter((q) => q.a != null && sentLocally[q.id]);
    return answered.length ? answered[answered.length - 1] : null;
  });
</script>

{#if pending.length}
  <div
    class="qa"
    role="region"
    aria-label="Pending decision"
    aria-live="assertive"
  >
    <div class="qhead">
      <span class="qmote" aria-hidden="true">?</span>
      <span class="qtitle mono">DECISION CHAMBER</span>
      <span class="qcount mono num">{pending.length} pending</span>
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
    /* wave U2 Task 1: docked in the right rail (below Metrics/Verdict) instead of
       floating independently at the vertical center of the viewport — the grid
       + the rail's own scroll now own placement; this is internal styling only. */
    width: 100%;
    flex: none;
    box-sizing: border-box;
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
    font-size: var(--text-2xs);
    letter-spacing: 0.16em;
    color: var(--plasma-cyan);
    flex: 1;
  }
  .qcount {
    font-size: var(--text-2xs);
    color: var(--text-meta);
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
    color: var(--text-meta);
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
    /* keep the global :focus-visible ring — only accent the border */
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
    font-size: var(--text-2xs);
    color: var(--text-meta);
    font-style: italic;
    padding: 6px 0;
  }
</style>
