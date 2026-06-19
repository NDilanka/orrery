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
/// `on_event` is called once per newly-completed JSON line. `should_stop` lets the caller break
/// the loop. Runs until `should_stop()` returns true or the channel closes.
pub fn watch_loop<F, S>(
    state_dir: &Path,
    log_path: &Path,
    mut on_event: F,
    should_stop: S,
) -> notify::Result<()>
where
    F: FnMut(&Value),
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

    // Watch the directory (the log file may be created/rotated within it).
    debouncer
        .watcher()
        .watch(state_dir, RecursiveMode::NonRecursive)?;

    let mut tailer = Tailer::new();

    // Process anything already present once before blocking.
    drain_new(&mut tailer, log_path, &mut on_event);

    loop {
        if should_stop() {
            break;
        }
        match rx.recv_timeout(Duration::from_millis(300)) {
            Ok(Ok(_events)) => {
                drain_new(&mut tailer, log_path, &mut on_event);
            }
            Ok(Err(_e)) => {
                // watch error; keep going (file may be transiently locked on Windows)
            }
            Err(mpsc::RecvTimeoutError::Timeout) => {
                // periodic poll fallback (also catches missed events on some platforms)
                drain_new(&mut tailer, log_path, &mut on_event);
            }
            Err(mpsc::RecvTimeoutError::Disconnected) => break,
        }
    }
    Ok(())
}

fn drain_new<F: FnMut(&Value)>(tailer: &mut Tailer, log_path: &Path, on_event: &mut F) {
    if let Ok(lines) = tailer.read_new(log_path) {
        for line in lines {
            if let Ok(v) = serde_json::from_str::<Value>(&line) {
                on_event(&v);
            }
            // non-JSON lines are silently skipped (the log is JSONL)
        }
    }
}

/// Resolve the log file path inside a state dir, honouring an explicit override and the bmad
/// default (`bmad-log.jsonl`) vs generic default (`log.jsonl`).
pub fn resolve_log_path(state_dir: &Path, adapter: &str, log_file: Option<&str>) -> PathBuf {
    match log_file {
        Some(f) if !f.trim().is_empty() => state_dir.join(f),
        _ => {
            let name = if adapter == "bmad" {
                "bmad-log.jsonl"
            } else {
                "log.jsonl"
            };
            state_dir.join(name)
        }
    }
}
