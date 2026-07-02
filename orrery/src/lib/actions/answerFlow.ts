// Shared answer-from-UI flow (A8) — previously duplicated between QAConsole (multi-question) and
// DecisionSheet (single-question): trim + guard, call the transport's answer(), then optimistically
// stamp the answer locally (`runStore.answerLocally`) so the card reflects the send immediately,
// before the engine's `review-answer` round-trips through the reducer. Also the shared
// Cmd/Ctrl+Enter submit key handler (Enter alone keeps newlines for a multi-line answer).

import { runStore } from '../stores/run.svelte';

export type AnswerFn = ((qid: string, text: string) => void | Promise<void>) | undefined;

/** Cmd/Ctrl+Enter → submit. Call from a textarea's `onkeydown`. */
export function onSubmitKey(e: KeyboardEvent, submit: () => void): void {
  if ((e.metaKey || e.ctrlKey) && e.key === 'Enter') {
    e.preventDefault();
    submit();
  }
}

/**
 * Send an answer through the transport, then optimistically stamp it into `runStore` so the UI
 * reflects the send before the reducer sees the resulting `review-answer` event. No-ops on an
 * empty (trimmed) body. Returns `true` when it actually sent, `false` when it was a no-op.
 */
export async function sendAnswer(answer: AnswerFn, qid: string, text: string): Promise<boolean> {
  const body = text.trim();
  if (!body) return false;
  await answer?.(qid, body);
  runStore.answerLocally(qid, body);
  return true;
}
