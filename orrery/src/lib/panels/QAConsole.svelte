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
    class="qa panel"
    role="region"
    aria-label="Pending decision"
    aria-live="assertive"
  >
    <div class="qhead panel-hd">
      <span class="qmote" aria-hidden="true">?</span>
      <span class="qtitle mono">DECISION CHAMBER</span>
      <span class="qcount panel-meta num">{pending.length} pending</span>
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
              class="btn btn-primary btn-sm"
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
  <div class="qa answered panel">
    <div class="qhead panel-hd">
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
       + the rail's own scroll now own placement; this is internal styling only.
       M4.5: card chrome (padding/border/radius/background) now comes from the
       shared `.panel` primitive — this class keeps the left-border accent (a
       one-off shape `.panel` doesn't have), the scroll clamp, and the layout.
       Border-left stays warn-amber: a pending question genuinely blocks on the
       user (the one Tier-B "needs you" case the monochrome sweep keeps
       chromatic) — contrast VerdictPanel's "pending" state, which is just
       system status with nothing for the user to do, and went grayscale. */
    width: 100%;
    flex: none;
    box-sizing: border-box;
    max-height: 76vh;
    overflow-y: auto;
    display: flex;
    flex-direction: column;
    gap: var(--space-3);
    border-left: var(--accent-border-w) solid var(--status-warn-core);
  }
  .qa.answered {
    /* pass state on a small element (border accent) → the two-tier system's -core */
    border-left-color: var(--status-ok-core);
  }
  .qmote {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    width: 22px;
    height: 22px;
    border-radius: 50%;
    background: color-mix(in srgb, var(--status-warn-core) 18%, transparent);
    color: var(--status-warn-core);
    font-weight: 700;
    font-size: var(--text-md);
    --glow: var(--status-warn-core);
    --breathe-r: 14px;
    /* was an opacity-blink; the shared `breathe` keyframe (primitives.css) instead —
       one attention grammar app-wide (plan §1: "never blinking"). Reduced motion is
       handled globally in tokens.css. */
    animation: breathe 2.4s ease-in-out infinite;
  }
  .qmote.done {
    background: color-mix(in srgb, var(--status-ok-core) 18%, transparent);
    color: var(--status-ok-core);
    --glow: var(--status-ok-core);
    animation: none;
  }
  .qtitle {
    /* header text stays the quiet em-low caps `.panel-hd` sets — only the glyph
       and border accent carry the warn signal, so the right rail still reads
       as one quiet Tier-B family (plan §M4.4/M4.5). */
    flex: 1;
  }
  .qcount {
    font-size: var(--text-2xs);
  }
  .qcard {
    display: flex;
    flex-direction: column;
    gap: var(--space-2);
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
    gap: var(--space-2);
    font-size: var(--text-2xs);
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
  .kind.review,
  .kind.retro {
    /* M4.5: both review and retro are pending-question metadata, same amber
       "needs-you" family — matches DecisionSheet's `.meta .kind` treatment
       exactly (no separate cyan taxonomy for retro). */
    color: var(--amber);
    border-color: color-mix(in srgb, var(--amber) 35%, transparent);
  }
  .qbody {
    /* body sentence, not a headline value → content tier */
    font-size: var(--text-sm);
    line-height: 1.5;
    color: var(--text-dim);
    white-space: pre-wrap;
    word-break: break-word;
    max-height: 30vh;
    overflow-y: auto;
  }
  .qbody.small {
    font-size: var(--text-xs);
    color: var(--text-dim);
  }
  .qinput {
    width: 100%;
    resize: vertical;
    background: var(--n1);
    border: 1px solid var(--hairline);
    border-radius: var(--radius-sm);
    color: var(--em-hi);
    font-size: var(--text-sm);
    line-height: 1.45;
    padding: 9px 11px;
    transition:
      background var(--dur-feedback) var(--ease-standard),
      border-color var(--dur-feedback) var(--ease-standard);
  }
  .qinput:hover:not(:disabled) {
    /* +1 surface step on hover (plan §M1.4) */
    background: var(--n2);
  }
  .qinput:focus {
    /* keep the global :focus-visible ring — only accent the border, grayscale now */
    border-color: color-mix(in srgb, var(--em-mid) 50%, transparent);
  }
  .qinput:disabled {
    opacity: 0.4;
    cursor: not-allowed;
  }
  .qactions {
    display: flex;
    justify-content: flex-end;
  }
  /* Send is a "go forward" action → .btn-primary (primitives.css), same family
     as Start/Resume on RunControlBar. Replaces the bespoke cyan pill button. */
  .observe {
    /* load-bearing copy (tells the user why they can't answer), not decorative —
       matches DecisionSheet's `.observe` tier: --text-meta, not the faint/decorative
       --text-faint this used to sit at. */
    font-size: var(--text-2xs);
    color: var(--text-meta);
    font-style: italic;
    padding: 6px 0;
  }
</style>
