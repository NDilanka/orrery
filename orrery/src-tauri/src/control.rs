//! Tauri command surface (PROTOCOL.md §6).
//!
//! - `load_run`  — one-shot reduce of existing files.
//! - `watch_run` — emit a Snapshot delta immediately, then a fresh State delta per new event.
//! - `list_loops` — read `loops/<id>/loop.json`.

use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Arc;

use serde_json::Value;

use crate::model::{Delta, LoopDef, RunState};
use crate::reducer::Reducer;
use crate::sprint;
use crate::watcher;

/// Resolve the log path the same way the watcher does.
fn log_path(state_dir: &Path, adapter: &str, log_file: Option<&str>) -> PathBuf {
    watcher::resolve_log_path(state_dir, adapter, log_file)
}

/// Try to find a project dir to look up sprint-status.yaml for the bmad adapter.
/// We probe the state dir, its parent, and any `resume`/checkpoint-derived path. If nothing
/// resolves, we skip (sprint-status is optional).
fn find_sprint_status(state_dir: &Path) -> Option<PathBuf> {
    // 1) alongside the log (e.g. .loop/sprint-status.yaml)
    let candidates = [
        state_dir.join("sprint-status.yaml"),
        // 2) one level up (project root)
        state_dir
            .parent()
            .map(|p| p.join("sprint-status.yaml"))
            .unwrap_or_default(),
        // 3) common BMAD location
        state_dir
            .parent()
            .map(|p| p.join("_bmad-output").join("sprint-status.yaml"))
            .unwrap_or_default(),
    ];
    candidates.into_iter().find(|p| p.exists())
}

fn checkpoint_path(state_dir: &Path) -> PathBuf {
    state_dir.join("checkpoint.json")
}

/// Loop id from the state dir's last path component (fallback "loop").
fn loop_id_from(state_dir: &Path, log_file: Option<&str>) -> String {
    let _ = log_file;
    state_dir
        .file_name()
        .and_then(|s| s.to_str())
        .map(|s| s.to_string())
        .unwrap_or_else(|| "loop".to_string())
}

/// Read & parse a JSONL file into events. Missing file → empty vec.
fn read_events(path: &Path) -> Vec<Value> {
    match std::fs::read_to_string(path) {
        Ok(text) => text
            .lines()
            .filter(|l| !l.trim().is_empty())
            .filter_map(|l| serde_json::from_str::<Value>(l).ok())
            .collect(),
        Err(_) => Vec::new(),
    }
}

/// Core reduce shared by load_run and watch_run's initial snapshot. Applies events, then
/// checkpoint, then (bmad) sprint-status overlays.
fn reduce_all(state_dir: &Path, adapter: &str, log_file: Option<&str>) -> RunState {
    let loop_id = loop_id_from(state_dir, log_file);
    let mut reducer = Reducer::new(loop_id, adapter);

    let lp = log_path(state_dir, adapter, log_file);
    let events = read_events(&lp);
    for (i, ev) in events.iter().enumerate() {
        reducer.apply(ev, (i as f64) * 1000.0);
    }

    // checkpoint.json
    let cp_path = checkpoint_path(state_dir);
    if let Ok(text) = std::fs::read_to_string(&cp_path) {
        if let Ok(cp) = serde_json::from_str::<Value>(&text) {
            reducer.apply_checkpoint(&cp);
        }
    }

    // sprint-status.yaml (bmad only, authoritative for not-in-flight items)
    if adapter == "bmad" {
        if let Some(sp) = find_sprint_status(state_dir) {
            let s = sprint::parse_file(&sp);
            reducer.apply_sprint(&s.items, &s.groups);
        }
    }

    reducer.state
}

// ---------------------------------------------------------------------------
// Tauri commands
// ---------------------------------------------------------------------------

#[tauri::command]
pub fn load_run(
    state_dir: String,
    adapter: String,
    log_file: Option<String>,
) -> Result<RunState, String> {
    let dir = PathBuf::from(&state_dir);
    Ok(reduce_all(&dir, &adapter, log_file.as_deref()))
}

#[tauri::command]
pub fn watch_run(
    state_dir: String,
    adapter: String,
    log_file: Option<String>,
    channel: tauri::ipc::Channel<Delta>,
) -> Result<(), String> {
    let dir = PathBuf::from(&state_dir);
    let lp = log_path(&dir, &adapter, log_file.as_deref());

    // Emit the initial full snapshot synchronously.
    let initial = reduce_all(&dir, &adapter, log_file.as_deref());
    channel
        .send(Delta::Snapshot { state: initial })
        .map_err(|e| e.to_string())?;

    // Spawn the watcher on a thread. It re-reduces from scratch on each new event (cheap for
    // these logs and trivially consistent with idempotency) and emits a full State delta.
    let stop = Arc::new(AtomicBool::new(false));
    let stop_thread = stop.clone();
    let adapter2 = adapter.clone();
    let log_file2 = log_file.clone();

    std::thread::spawn(move || {
        // We re-reduce the whole file on each notification; this keeps the wire model a full
        // RunState (frontend always has complete state) and is robust against partial/keyed events.
        let on_event = move |_ev: &Value| {
            let state = reduce_all(&dir, &adapter2, log_file2.as_deref());
            // ignore send errors (channel closed => frontend navigated away)
            let _ = channel.send(Delta::State { state });
        };

        let should_stop = move || stop_thread.load(Ordering::Relaxed);

        // state_dir for watching == parent dir of the log file
        let watch_dir = lp.parent().map(PathBuf::from).unwrap_or_else(|| PathBuf::from("."));
        let _ = watcher::watch_loop(&watch_dir, &lp, on_event, should_stop);
    });

    // NOTE: the thread runs until the process exits; the stop flag is reserved for a future
    // unwatch command. We intentionally leak the handle (daemon thread).
    let _ = stop;
    Ok(())
}

#[tauri::command]
pub fn list_loops(loops_dir: String) -> Result<Vec<LoopDef>, String> {
    let dir = PathBuf::from(&loops_dir);
    let mut out = Vec::new();
    let entries = match std::fs::read_dir(&dir) {
        Ok(e) => e,
        Err(e) => return Err(format!("read_dir {:?}: {}", dir, e)),
    };
    for entry in entries.flatten() {
        let path = entry.path();
        if !path.is_dir() {
            continue;
        }
        let loop_json = path.join("loop.json");
        if let Ok(text) = std::fs::read_to_string(&loop_json) {
            match serde_json::from_str::<LoopDef>(&text) {
                Ok(def) => out.push(def),
                Err(e) => return Err(format!("parse {:?}: {}", loop_json, e)),
            }
        }
    }
    // stable order by id
    out.sort_by(|a, b| a.id.cmp(&b.id));
    Ok(out)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::model::ItemStatus;

    fn fixtures_dir() -> PathBuf {
        PathBuf::from(concat!(env!("CARGO_MANIFEST_DIR"), "/../fixtures"))
    }

    #[test]
    fn load_run_bmad_overlays_checkpoint_and_sprint() {
        // fixtures dir holds bmad-log.jsonl, checkpoint.json, sprint-status.yaml
        let dir = fixtures_dir();
        let state = reduce_all(&dir, "bmad", Some("bmad-log.jsonl"));

        // cumUsd ≈ 26.75 (running-max of last run, matches checkpoint too)
        assert!((state.run.cum_usd - 26.7472).abs() < 0.01, "cumUsd {}", state.run.cum_usd);

        // checkpoint overlaid: stage + resume + branch
        assert_eq!(state.run.stage.as_deref(), Some("between-stories (clean)"));
        assert!(state.run.resume_cmd.is_some());
        assert_eq!(state.run.merge_base, "develop");

        // sprint-status overlaid a not-in-flight backlog item (3-5)
        let s35 = state.items.get("3-5-re-embedding-batch-utility");
        assert_eq!(s35.map(|i| i.status), Some(ItemStatus::Backlog));

        // a done item exists from the log
        assert!(state.items.values().any(|i| i.status == ItemStatus::Done));
    }

    #[test]
    fn list_loops_reads_seed_defs() {
        let loops_dir = PathBuf::from(concat!(env!("CARGO_MANIFEST_DIR"), "/../loops"));
        let defs = list_loops(loops_dir.to_string_lossy().to_string()).unwrap();
        let ids: Vec<&str> = defs.iter().map(|d| d.id.as_str()).collect();
        assert!(ids.contains(&"bmad"));
        assert!(ids.contains(&"roman"));
        assert!(ids.contains(&"calc"));
        // bmad def carries logFile + adapter
        let bmad = defs.iter().find(|d| d.id == "bmad").unwrap();
        assert_eq!(bmad.adapter, "bmad");
        assert_eq!(bmad.log_file.as_deref(), Some("bmad-log.jsonl"));
    }
}
