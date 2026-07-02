//! File watcher (PROTOCOL.md §6 `watch_run`).
//!
//! A debounced (`notify-debouncer-mini`, ~50ms) recommended watcher over a `stateDir`. On each
//! change it tails new lines from the target log file and hands each complete line (as a parsed
//! JSON `Value`) to a callback, which the control layer wires into the reducer.

use notify::RecursiveMode;
use notify_debouncer_mini::{new_debouncer, DebounceEventResult};
use serde_json::Value;
use std::path::{Path, PathBuf};
use std::sync::mpsc;
use std::time::{Duration, SystemTime};

use crate::tailer::Tailer;

/// Polls `<stateDir>/activity.json` (the liveness heartbeat) by modified-time, re-reading only when
/// it changes. `poll()` returns `Some(activity)` when changed since the last poll (the inner
/// `None` = file absent or unparseable), or `None` when unchanged — so the watcher only emits an
/// activity Delta on an actual beat, not on every 50ms tick. The engine writes it atomically
/// (temp + rename), so a read here never sees a partial file.
struct ActivityReader {
    path: PathBuf,
    last_mtime: Option<SystemTime>,
    started: bool,
}

impl ActivityReader {
    fn new(state_dir: &Path) -> Self {
        ActivityReader {
            path: state_dir.join("activity.json"),
            last_mtime: None,
            started: false,
        }
    }

    fn poll(&mut self) -> Option<Option<Value>> {
        let mtime = std::fs::metadata(&self.path)
            .ok()
            .and_then(|m| m.modified().ok());
        // Unchanged since the last poll (and we've reported once) → nothing to emit.
        if self.started && mtime == self.last_mtime {
            return None;
        }
        self.started = true;
        self.last_mtime = mtime;
        match std::fs::read_to_string(&self.path) {
            Ok(text) => Some(serde_json::from_str::<Value>(&text).ok()),
            Err(_) => Some(None), // file absent/unreadable → emit a `null` activity
        }
    }
}

/// Blocking watch loop. `state_dir` is the directory to watch; `log_path` is the file to tail.
/// `on_events` is called once per drain with the BATCH of newly-completed JSON lines (empty drains
/// are skipped). Batching matters: the first drain replays the whole existing log, so a per-line
/// callback there would do O(N^2) work + flood the sink — callers reduce once per batch instead.
/// `should_stop` lets the caller break the loop. Runs until `should_stop()` or the channel closes.
pub fn watch_loop<F, A, S>(
    state_dir: &Path,
    log_path: &Path,
    mut on_events: F,
    mut on_activity: A,
    should_stop: S,
) -> notify::Result<()>
where
    F: FnMut(&[Value]),
    // Called with the latest `activity.json` whenever it changes (inner `None` = absent/cleared).
    A: FnMut(Option<Value>),
    S: Fn() -> bool,
{
    let (tx, rx) = mpsc::channel();
    // A short debounce keeps the live log near-continuous: new log.jsonl lines surface within
    // ~50ms of being appended (was 150ms) — the per-line Event deltas stream as fast as the
    // engine writes them, instead of in visibly chunky batches.
    let mut debouncer = new_debouncer(
        Duration::from_millis(50),
        move |res: DebounceEventResult| {
            // Forward; ignore send errors (receiver gone => loop exiting).
            let _ = tx.send(res);
        },
    )?;

    // Ensure the dir exists BEFORE arming the watch. `watch_run` is called when the System
    // view mounts, which can happen before the loop is ever started — so the state dir (a
    // gitignored `.loop/`) often does not exist yet. notify errors on a missing path; that
    // error rides `?` out of the thread and the watch never re-arms, so the live UI stays
    // empty even after the engine later creates the dir + log. Creating it here is benign
    // (start_loop also create_dir_all's it) and lets the dir-watch catch log.jsonl on creation.
    let _ = std::fs::create_dir_all(state_dir);

    // Watch the directory (the log file may be created/rotated within it).
    debouncer
        .watcher()
        .watch(state_dir, RecursiveMode::NonRecursive)?;

    let mut tailer = Tailer::new();
    let mut activity = ActivityReader::new(state_dir);

    // Process anything already present once before blocking (log + the current activity beat).
    drain_new(&mut tailer, log_path, &mut on_events);
    if let Some(a) = activity.poll() {
        on_activity(a);
    }

    loop {
        if should_stop() {
            break;
        }
        match rx.recv_timeout(Duration::from_millis(50)) {
            Ok(Ok(_events)) => {
                drain_new(&mut tailer, log_path, &mut on_events);
            }
            Ok(Err(_e)) => {
                // watch error; keep going (file may be transiently locked on Windows)
            }
            Err(mpsc::RecvTimeoutError::Timeout) => {
                // periodic poll fallback (~50ms): on Windows, notify frequently MISSES in-place
                // appends to log.jsonl, so this poll — not the fs-event — is what actually keeps
                // the live log advancing. A no-new-bytes drain is cheap (a stat + seek).
                drain_new(&mut tailer, log_path, &mut on_events);
            }
            Err(mpsc::RecvTimeoutError::Disconnected) => break,
        }
        // After handling log changes, surface a fresh activity beat if activity.json moved. The
        // engine rewrites it on its own (~12s) cadence — independent of log lines — so this poll,
        // not a log event, is what keeps the liveness indicator alive through a silent dev-story.
        if let Some(a) = activity.poll() {
            on_activity(a);
        }
    }
    Ok(())
}

fn drain_new<F: FnMut(&[Value])>(tailer: &mut Tailer, log_path: &Path, on_events: &mut F) {
    if let Ok(lines) = tailer.read_new(log_path) {
        // Parse the whole drain, then hand the callback ONE batch (non-JSON lines skipped; the log
        // is JSONL). Empty drains (the common 300ms poll with no new bytes) call nothing.
        let vals: Vec<Value> = lines
            .iter()
            .filter_map(|line| serde_json::from_str::<Value>(line).ok())
            .collect();
        if !vals.is_empty() {
            on_events(&vals);
        }
    }
}

/// Resolve the log file path inside a state dir, honouring an explicit override. Both adapters
/// default to `log.jsonl` — what the engine actually writes (`loop` and `loop-bmad` both emit
/// `<stateDir>/log.jsonl`). A loop.json may still override via `logFile`. (Replay fixtures use
/// the `bmad-log.jsonl` name, but those are loaded by the frontend, not this resolver.)
pub fn resolve_log_path(state_dir: &Path, adapter: &str, log_file: Option<&str>) -> PathBuf {
    let _ = adapter;
    match log_file {
        Some(f) if !f.trim().is_empty() => state_dir.join(f),
        _ => state_dir.join("log.jsonl"),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn tmp_state_dir(tag: &str) -> PathBuf {
        let mut p = std::env::temp_dir();
        p.push(format!(
            "orrery_activity_{}_{}_{:?}",
            std::process::id(),
            tag,
            std::thread::current().id()
        ));
        let _ = std::fs::remove_dir_all(&p);
        std::fs::create_dir_all(&p).unwrap();
        p
    }

    #[test]
    fn activity_reader_emits_on_appearance_and_change_only() {
        let dir = tmp_state_dir("reader");
        let mut r = ActivityReader::new(&dir);

        // Absent first poll → Some(None) (report the null once); then unchanged → None.
        assert_eq!(r.poll(), Some(None));
        assert!(r.poll().is_none(), "absent + unchanged emits nothing");

        // A beat appears → poll reads + parses it.
        let ap = dir.join("activity.json");
        std::fs::write(&ap, r#"{"phase":"dev-story","story":"5-2","dirty":3}"#).unwrap();
        match r.poll() {
            Some(Some(v)) => {
                assert_eq!(v["phase"], "dev-story");
                assert_eq!(v["dirty"], 3);
            }
            other => panic!("expected a parsed beat, got {other:?}"),
        }
        // Unchanged mtime → no re-emit (so a 50ms tick with no beat is silent).
        assert!(r.poll().is_none(), "unchanged file emits nothing");

        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn activity_reader_emits_null_for_unparseable_beat() {
        let dir = tmp_state_dir("bad");
        let ap = dir.join("activity.json");
        std::fs::write(&ap, "{not json").unwrap();
        let mut r = ActivityReader::new(&dir);
        // Present but unparseable → Some(None): the wire carries a `null` activity, never an error.
        assert_eq!(r.poll(), Some(None));
        let _ = std::fs::remove_dir_all(&dir);
    }
}
