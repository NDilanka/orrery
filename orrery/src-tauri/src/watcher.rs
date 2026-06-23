//! File watcher (PROTOCOL.md §6 `watch_run`).
//!
//! A debounced (`notify-debouncer-mini`, ~150ms) recommended watcher over a `stateDir`. On each
//! change it tails new lines from the target log file and hands each complete line (as a parsed
//! JSON `Value`) to a callback, which the control layer wires into the reducer.

use notify::RecursiveMode;
use notify_debouncer_mini::{new_debouncer, DebounceEventResult};
use serde_json::Value;
use std::path::{Path, PathBuf};
use std::sync::mpsc;
use std::time::Duration;

use crate::tailer::Tailer;

/// Blocking watch loop. `state_dir` is the directory to watch; `log_path` is the file to tail.
/// `on_events` is called once per drain with the BATCH of newly-completed JSON lines (empty drains
/// are skipped). Batching matters: the first drain replays the whole existing log, so a per-line
/// callback there would do O(N^2) work + flood the sink — callers reduce once per batch instead.
/// `should_stop` lets the caller break the loop. Runs until `should_stop()` or the channel closes.
pub fn watch_loop<F, S>(
    state_dir: &Path,
    log_path: &Path,
    mut on_events: F,
    should_stop: S,
) -> notify::Result<()>
where
    F: FnMut(&[Value]),
    S: Fn() -> bool,
{
    let (tx, rx) = mpsc::channel();
    let mut debouncer = new_debouncer(
        Duration::from_millis(150),
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

    // Process anything already present once before blocking.
    drain_new(&mut tailer, log_path, &mut on_events);

    loop {
        if should_stop() {
            break;
        }
        match rx.recv_timeout(Duration::from_millis(300)) {
            Ok(Ok(_events)) => {
                drain_new(&mut tailer, log_path, &mut on_events);
            }
            Ok(Err(_e)) => {
                // watch error; keep going (file may be transiently locked on Windows)
            }
            Err(mpsc::RecvTimeoutError::Timeout) => {
                // periodic poll fallback (also catches missed events on some platforms)
                drain_new(&mut tailer, log_path, &mut on_events);
            }
            Err(mpsc::RecvTimeoutError::Disconnected) => break,
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
