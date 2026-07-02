//! live.rs (R2/R4) — an incrementally-fed `Reducer` shared by `control::watch_run` and
//! `lan::stream_loop`. Both used to re-reduce the ENTIRE on-disk log from scratch on every tail
//! drain (`reduce_all`) — cheap for a short demo log, but O(total log) per ~50ms tick on a
//! multi-day run, plus 2-3 full passes on every mount. `LiveReducer` instead persists across
//! drains and is fed ONLY the newly-tailed lines; a full rebuild happens exactly when a loop view
//! first mounts (the initial snapshot) and whenever the tailer detects the log file was rotated
//! out from under it (`tailer::Tailer`'s `(lines, rotated)` — see R3). The reducer itself
//! (`reducer::Reducer`) is unchanged — this module only owns WHICH lines get fed to it and WHEN,
//! plus the per-line timestamp policy below.
//!
//! Real timestamps (R4): every caller used to stamp a SYNTHETIC `index * 1000` ms for every
//! event, which is why `run.startedAt`/`run.lastEventAt` always read as 1970 (suppressed behind
//! `timefmt.ts`'s `SANE_EPOCH_MS` guard on the frontend). `next_ts` now prefers a REAL timestamp
//! when the event line actually carries one: a numeric `_t` (ms since epoch — the same test-only
//! override `reduce.ts`'s production `reduce()` already honors on the TypeScript side, so a
//! fixture can carry real times without inventing a second wire convention the two reducers would
//! have to agree on separately) or a string `ts` (ISO-8601 — no engine emits this YET as of this
//! wave, but chrono parsing is wired up here so it lights up the moment one does, per PROTOCOL.md
//! rule 8). Falling back, in order: the LAST real timestamp seen (bridges a gap where only some
//! lines carry one), then the synthetic index scheme (today's default for every real fixture —
//! byte-identical to the old per-caller `(i as f64) * 1000.0` when a log carries no timestamps at
//! all, so this is a pure extension, not a behavior change, for every log in the repo today).

use serde_json::Value;

use crate::model::{GroupStatus, ItemStatus, RetroStatus, RunState};
use crate::reducer::Reducer;

/// Parse a real ms-since-epoch timestamp off one event line, when present. Checked in order:
/// `_t` (a bare number), then `ts` (an ISO-8601 string, RFC3339-parseable). Neither present or
/// parseable → `None` (the caller falls back to its own ts policy).
fn parse_event_ts(ev: &Value) -> Option<f64> {
    if let Some(t) = ev.get("_t").and_then(Value::as_f64) {
        return Some(t);
    }
    let s = ev.get("ts").and_then(Value::as_str)?;
    chrono::DateTime::parse_from_rfc3339(s)
        .ok()
        .map(|dt| dt.timestamp_millis() as f64)
}

/// A `Reducer` plus the incremental-feed + timestamp bookkeeping described above.
pub struct LiveReducer {
    reducer: Reducer,
    next_index: usize,
    last_real_ts: Option<f64>,
}

impl LiveReducer {
    pub fn new(loop_id: impl Into<String>, adapter: impl Into<String>) -> Self {
        LiveReducer {
            reducer: Reducer::new(loop_id, adapter),
            next_index: 0,
            last_real_ts: None,
        }
    }

    fn next_ts(&mut self, ev: &Value) -> f64 {
        let ts = match parse_event_ts(ev) {
            Some(t) => {
                self.last_real_ts = Some(t);
                t
            }
            None => self
                .last_real_ts
                .unwrap_or((self.next_index as f64) * 1000.0),
        };
        self.next_index += 1;
        ts
    }

    /// Feed one batch of already-parsed NEW event lines (in order) through the reducer.
    pub fn apply_batch(&mut self, events: &[Value]) {
        for ev in events {
            let ts = self.next_ts(ev);
            self.reducer.apply(ev, ts);
        }
    }

    pub fn apply_checkpoint(&mut self, cp: &Value) {
        self.reducer.apply_checkpoint(cp);
    }

    pub fn apply_sprint(
        &mut self,
        item_statuses: &[(String, ItemStatus, Option<String>)],
        group_statuses: &[(String, GroupStatus, Option<RetroStatus>)],
    ) {
        self.reducer.apply_sprint(item_statuses, group_statuses);
    }

    /// A snapshot of the reduced state so far (cheap-ish clone; `RunState` has no huge fields).
    pub fn state(&self) -> RunState {
        self.reducer.state.clone()
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn synthetic_index_fallback_matches_old_reduce_all_when_no_real_ts() {
        let mut lr = LiveReducer::new("x", "generic");
        let events = vec![
            json!({"event":"iter","cum":1.0,"pass":1,"total":2}),
            json!({"event":"iter","cum":2.0,"pass":2,"total":2}),
        ];
        lr.apply_batch(&events);
        // event 0 -> t=0 ; event 1 -> t=1000 (byte-identical to the old `(i as f64) * 1000.0`)
        assert_eq!(
            lr.state().run.last_event_at.as_deref(),
            Some("1970-01-01T00:00:01.000Z")
        );
    }

    #[test]
    fn numeric_t_override_is_honored_and_carried_forward() {
        let mut lr = LiveReducer::new("x", "generic");
        let real = 1_750_000_000_000.0_f64; // a real-looking ms epoch (~mid-2025)
        let events = vec![
            json!({"event":"iter","cum":1.0,"pass":1,"total":2,"_t":real}),
            // no _t here: must inherit the PREVIOUS real ts, not fall back to a synthetic 1970 one
            json!({"event":"iter","cum":2.0,"pass":2,"total":2}),
        ];
        lr.apply_batch(&events);
        let iso = lr.state().run.last_event_at.unwrap();
        assert!(iso.starts_with("2025") || iso.starts_with("2026"), "{iso}");
    }

    #[test]
    fn iso_ts_string_is_parsed() {
        let mut lr = LiveReducer::new("x", "generic");
        let events = vec![
            json!({"event":"iter","cum":1.0,"pass":1,"total":2,"ts":"2026-06-24T17:15:00.000Z"}),
        ];
        lr.apply_batch(&events);
        assert_eq!(
            lr.state().run.last_event_at.as_deref(),
            Some("2026-06-24T17:15:00.000Z")
        );
    }

    #[test]
    fn feeding_two_batches_matches_feeding_one_batch() {
        // The whole point of R2: an incremental "new lines only" feed must land on the SAME state
        // as a from-scratch full rebuild of the same events, not just a re-apply of the SAME lines
        // twice (which the reducer's own idempotency rule already covers).
        let events = vec![
            json!({"event":"iter","cum":1.0,"pass":1,"total":2}),
            json!({"event":"iter","cum":2.0,"pass":2,"total":2}),
            json!({"event":"stop","reason":"green","green":true,"cum":2.0}),
        ];
        let mut whole = LiveReducer::new("x", "generic");
        whole.apply_batch(&events);

        let mut split = LiveReducer::new("x", "generic");
        split.apply_batch(&events[..1]);
        split.apply_batch(&events[1..]);

        assert_eq!(
            serde_json::to_value(whole.state()).unwrap(),
            serde_json::to_value(split.state()).unwrap()
        );
    }
}
