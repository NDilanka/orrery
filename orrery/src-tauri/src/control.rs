//! Tauri command surface (PROTOCOL.md §6).
//!
//! - `load_run`  — one-shot reduce of existing files.
//! - `watch_run` — emit a Snapshot delta immediately, then a fresh State delta per new event.
//! - `list_loops` — read `loops/<id>/loop.json`.

use std::collections::HashMap;
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex};

use serde_json::Value;

use crate::model::{
    Activity, Checkpoint, Delta, GuardStatus, LoopDef, RunState, StartResult, StartSpec,
};
use crate::reducer::Reducer;
use crate::sprint;
use crate::watcher;

/// App state managed by Tauri: loopId → most-recently-spawned child PID.
#[derive(Default)]
pub struct LoopPids(pub Mutex<HashMap<String, u32>>);

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

/// Public wrapper so the LAN server (`lan.rs`) can run the SAME tail→reduce pipeline `watch_run`
/// uses for its `/ws` snapshot + state deltas. Pure read of the loop's files → `RunState`.
pub fn reduce_all_pub(state_dir: &Path, adapter: &str, log_file: Option<&str>) -> RunState {
    reduce_all(state_dir, adapter, log_file)
}

/// Overlay value: read the STOP brake flag at `<state_dir>/STOP` → a `StopPending`.
/// `reduce_all` replays only the event log + checkpoint + sprint-status, never the STOP file,
/// so `run.stopPending` would be `null` on a fresh snapshot even when a brake is banked on
/// disk — making a UI reload silently drop the pending brake (the engine still honors the file,
/// which is why a second Brake click "works"). The snapshot/state builders overlay this so a
/// banked brake is DURABLE across a reload. Kept OUT of the pure reducer / `reduce_all` so the
/// cross-language parity goldens (built from the event stream only) stay untouched.
fn stop_pending_at(state_dir: &Path) -> Option<crate::model::StopPending> {
    use crate::model::StopPending;
    let text = std::fs::read_to_string(state_dir.join("STOP")).ok()?;
    Some(match text.trim().to_lowercase().as_str() {
        "story" => StopPending::Story,
        "now" => StopPending::Now,
        // "phase", empty, or anything else → phase (parity with read_stop_flag's default)
        _ => StopPending::Phase,
    })
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
    let mut state = reduce_all(&dir, &adapter, log_file.as_deref());
    state.run.stop_pending = stop_pending_at(&dir); // durable brake across reload
    Ok(state)
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

    // Emit the initial full snapshot synchronously. Overlay the on-disk STOP brake so a banked
    // brake survives a UI reload/reconnect (reduce_all never reads the STOP file).
    let mut initial = reduce_all(&dir, &adapter, log_file.as_deref());
    initial.run.stop_pending = stop_pending_at(&dir);
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
        // The activity heartbeat rides a SEPARATE Delta on its own channel clone (Tauri Channels
        // are cheap to clone) so a beat surfaces even on a poll with no new log line.
        let activity_channel = channel.clone();
        let on_activity = move |raw: Option<Value>| {
            // Validate/canonicalize into the typed Activity (drops unknown keys, defaults missing);
            // a malformed activity.json → `None` rather than a wire error.
            let activity = raw.and_then(|v| serde_json::from_value::<Activity>(v).ok());
            let _ = activity_channel.send(Delta::Activity { activity });
        };

        // We re-reduce the whole file on each notification; this keeps the wire model a full
        // RunState (frontend always has complete state) and is robust against partial/keyed events.
        let on_events = move |evs: &[Value]| {
            // Live LOG feed: stream the raw events, but cap to the last 300 of a batch. The
            // watcher's first drain replays the WHOLE existing log, which on a long-running loop
            // could be thousands of lines — without the cap that would flood the Channel on every
            // System mount (the frontend logStore caps at 300 too). Live batches are tiny, so this
            // never trims them.
            let start = evs.len().saturating_sub(300);
            for ev in &evs[start..] {
                let _ = channel.send(Delta::Event { event: ev.clone() });
            }
            // Reduce ONCE per batch (not per line) → O(N) on mount instead of O(N^2), and one
            // authoritative State delta. ignore send errors (channel closed => navigated away).
            let mut state = reduce_all(&dir, &adapter2, log_file2.as_deref());
            state.run.stop_pending = stop_pending_at(&dir); // keep the banked brake live
            let _ = channel.send(Delta::State { state });
        };

        let should_stop = move || stop_thread.load(Ordering::Relaxed);

        // state_dir for watching == parent dir of the log file
        let watch_dir = lp.parent().map(PathBuf::from).unwrap_or_else(|| PathBuf::from("."));
        let _ = watcher::watch_loop(&watch_dir, &lp, on_events, on_activity, should_stop);
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
                Ok(mut def) => {
                    // Resolve a relative stateDir against the loop's own dir so the frontend
                    // (watch_run / load_run) and the engine agree on ONE absolute path. The
                    // Tuning Console's default stateDir is the relative ".loop"; without this it
                    // would be watched relative to the app's cwd while the engine writes it
                    // relative to the loop dir → the live UI would never update for that loop.
                    def.state_dir = resolve_under(&path, &def.state_dir)
                        .to_string_lossy()
                        .into_owned();
                    out.push(def);
                }
                Err(e) => return Err(format!("parse {:?}: {}", loop_json, e)),
            }
        }
    }
    // stable order by id
    out.sort_by(|a, b| a.id.cmp(&b.id));
    Ok(out)
}

// ===========================================================================
// A5 — LOOP CRUD (create / update / clone / delete)
// ===========================================================================
//
// The Tuning Console authors a loop.json and persists it under `loops/<id>/`.
// These commands are the write-side mirror of `list_loops`: they take the same
// `loops_dir` and produce/replace `loops/<id>/loop.json` (camelCase on the wire,
// guaranteed by `LoopDef`'s serde rename_all). All writes validate the id is a
// filesystem-safe single path component and (for create/clone) unique.
//
// SECURITY: the id becomes a directory name. We refuse anything that is not a
// plain `[a-z0-9_-]`-ish token so a console value can never traverse the tree
// or smuggle a path separator. The console may send an arbitrary `engine`
// object — that's data the engine config reads, never a command we execute.

/// Is `id` a safe single path component usable as a loop directory name?
/// Allowed: ASCII letters, digits, `-`, `_`. Length 1..=64. No `.`/`/`/`\\`.
fn is_safe_loop_id(id: &str) -> bool {
    if id.is_empty() || id.len() > 64 {
        return false;
    }
    id.chars()
        .all(|c| c.is_ascii_alphanumeric() || c == '-' || c == '_')
}

/// Path to a loop's `loop.json` under `loops_dir`. Caller must have validated id.
fn loop_json_path(loops_dir: &Path, id: &str) -> PathBuf {
    loops_dir.join(id).join("loop.json")
}

/// Force a serialized `LoopDef`'s `id` field, parse it, and write it to disk as
/// pretty camelCase JSON. Returns the parsed `LoopDef`. Shared by create/clone.
fn write_loop_def(loops_dir: &Path, id: &str, mut def: Value) -> Result<LoopDef, String> {
    // The on-disk id is authoritative — overwrite whatever the payload claimed so
    // the directory name and `def.id` can never disagree.
    if let Value::Object(map) = &mut def {
        map.insert("id".to_string(), Value::String(id.to_string()));
    } else {
        return Err("loop definition must be a JSON object".to_string());
    }

    // Validate the shape NOW (before touching the disk) by parsing into LoopDef.
    let parsed: LoopDef = serde_json::from_value(def.clone())
        .map_err(|e| format!("invalid loop definition: {e}"))?;

    let dir = loops_dir.join(id);
    std::fs::create_dir_all(&dir).map_err(|e| format!("create {dir:?}: {e}"))?;
    let path = loop_json_path(loops_dir, id);
    // Re-serialize from the validated LoopDef so the file is canonical camelCase
    // (drops unknown keys, applies skip_serializing_if). Pretty-printed for humans.
    let json = serde_json::to_string_pretty(&parsed)
        .map_err(|e| format!("serialize {id}: {e}"))?;
    std::fs::write(&path, json).map_err(|e| format!("write {path:?}: {e}"))?;
    Ok(parsed)
}

/// `create_loop(loopsDir, def)` — validate the id is filesystem-safe + unique,
/// then write `loops/<id>/loop.json`. Returns the canonicalized `LoopDef`.
#[tauri::command]
pub fn create_loop(loops_dir: String, def: Value) -> Result<LoopDef, String> {
    let loops = PathBuf::from(&loops_dir);
    // pull the id out of the payload to validate it
    let id = def
        .get("id")
        .and_then(|v| v.as_str())
        .map(|s| s.to_string())
        .ok_or_else(|| "loop definition is missing a string `id`".to_string())?;
    if !is_safe_loop_id(&id) {
        return Err(format!(
            "invalid loop id {id:?}: use letters, digits, '-' or '_' (1-64 chars)"
        ));
    }
    if loop_json_path(&loops, &id).exists() {
        return Err(format!("a loop with id {id:?} already exists"));
    }
    write_loop_def(&loops, &id, def)
}

/// `update_loop(loopsDir, id, def)` — overwrite an existing `loops/<id>/loop.json`.
/// The loop must already exist (use create_loop for new ones).
#[tauri::command]
pub fn update_loop(loops_dir: String, id: String, def: Value) -> Result<(), String> {
    if !is_safe_loop_id(&id) {
        return Err(format!("invalid loop id {id:?}"));
    }
    let loops = PathBuf::from(&loops_dir);
    if !loop_json_path(&loops, &id).exists() {
        return Err(format!("loop {id:?} does not exist (use create_loop)"));
    }
    write_loop_def(&loops, &id, def)?;
    Ok(())
}

/// `clone_loop(loopsDir, id, newId)` — read an existing loop.json, re-id it, and
/// write it as a brand-new loop. `newId` must be safe + unique.
#[tauri::command]
pub fn clone_loop(loops_dir: String, id: String, new_id: String) -> Result<LoopDef, String> {
    if !is_safe_loop_id(&id) {
        return Err(format!("invalid source loop id {id:?}"));
    }
    if !is_safe_loop_id(&new_id) {
        return Err(format!("invalid new loop id {new_id:?}"));
    }
    let loops = PathBuf::from(&loops_dir);
    let src = loop_json_path(&loops, &id);
    let text = std::fs::read_to_string(&src).map_err(|e| format!("read {src:?}: {e}"))?;
    let def: Value =
        serde_json::from_str(&text).map_err(|e| format!("parse {src:?}: {e}"))?;
    if loop_json_path(&loops, &new_id).exists() {
        return Err(format!("a loop with id {new_id:?} already exists"));
    }
    write_loop_def(&loops, &new_id, def)
}

/// `delete_loop(loopsDir, id)` — remove `loops/<id>/` and its loop.json. Idempotent
/// on a missing directory. Only ever removes a single validated loop directory.
#[tauri::command]
pub fn delete_loop(loops_dir: String, id: String) -> Result<(), String> {
    if !is_safe_loop_id(&id) {
        return Err(format!("invalid loop id {id:?}"));
    }
    let dir = PathBuf::from(&loops_dir).join(&id);
    match std::fs::remove_dir_all(&dir) {
        Ok(()) => Ok(()),
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => Ok(()),
        Err(e) => Err(format!("remove {dir:?}: {e}")),
    }
}

// ===========================================================================
// A6 — LIVE CONTROL (start / stop / cancel / resume / guard_status)
// ===========================================================================
//
// Conventions mirrored from the engine (bmad-loop.ps1 / stop-loop.ps1):
//   * STOP flag       — a text file `<stateDir>/STOP` containing "phase"|"story"|"now".
//                       The loop reads it at safe checkpoints and consumes it. We never
//                       kill the process — cooperative stop only (stop-loop.ps1 parity).
//   * checkpoint.json — `<stateDir>/checkpoint.json`; `resume` is a shell command string.
//   * concurrency     — the engine refuses (`exit 2`) if another process' command line
//                       references its start script. We mirror that: refuse with
//                       `Err("AlreadyRunning")` when a live process matches this loop.
//
// SECURITY: we only ever spawn the program/args declared in the loop's own loop.json (or
// the checkpoint `resume` string the engine itself wrote). The frontend cannot inject a
// command. `mode` is validated against {phase,story,now}.

/// Load `loops/<id>/loop.json` into a `LoopDef`.
fn load_loop_def(loops_dir: &Path, loop_id: &str) -> Result<LoopDef, String> {
    if loop_id.is_empty() || loop_id.contains(['/', '\\', '.']) {
        // basic path-traversal guard: ids are plain directory names.
        return Err(format!("invalid loopId: {loop_id:?}"));
    }
    let p = loops_dir.join(loop_id).join("loop.json");
    let text = std::fs::read_to_string(&p).map_err(|e| format!("read {p:?}: {e}"))?;
    serde_json::from_str::<LoopDef>(&text).map_err(|e| format!("parse {p:?}: {e}"))
}

/// The loop's own base dir is `loops_dir/<loop_id>` (where its loop.json lives).
/// A RELATIVE path in loop.json (stateDir / stopFlag / checkpoint) resolves against
/// it; an ABSOLUTE path is left untouched. This is what makes a self-contained
/// `stateDir: ".loop"` resolve under the loop's own dir rather than the app's cwd.
fn loop_base_dir(loops_dir: &Path, loop_id: &str) -> PathBuf {
    loops_dir.join(loop_id)
}

/// Resolve `p` against `base` when relative; return it as-is when absolute.
fn resolve_under(base: &Path, p: &str) -> PathBuf {
    let pb = PathBuf::from(p);
    if pb.is_absolute() {
        pb
    } else {
        base.join(pb)
    }
}

fn state_dir_of(def: &LoopDef, base_dir: &Path) -> PathBuf {
    resolve_under(base_dir, &def.state_dir)
}

fn stop_flag_path(def: &LoopDef, base_dir: &Path) -> PathBuf {
    // Honor an explicit stopFlag if the loop.json sets one; else `<stateDir>/STOP`.
    match &def.stop_flag {
        Some(p) => resolve_under(base_dir, p),
        None => state_dir_of(def, base_dir).join("STOP"),
    }
}

fn checkpoint_file_path(def: &LoopDef, base_dir: &Path) -> PathBuf {
    match &def.checkpoint {
        Some(p) => resolve_under(base_dir, p),
        None => state_dir_of(def, base_dir).join("checkpoint.json"),
    }
}

/// Distinctive lowercase tokens that identify *this* loop's run on a command line.
/// All loops here share one stateDir, so stateDir alone can't disambiguate — we key on
/// the start script's basename plus any `-TaskFile`/`-File` discriminators in its args.
/// Returns the tokens that MUST all be present for a command line to count as this loop.
fn guard_tokens(def: &LoopDef) -> Vec<String> {
    let mut tokens: Vec<String> = Vec::new();
    if let Some(start) = &def.start {
        // The orchestrator's PROGRAM basename is the key discriminator: it appears in the REAL loop
        // process's command line but NOT in a bystander that merely references the state dir (a
        // `tail -f .loop/log.jsonl`, an editor with the log open, a file manager). Without it, a
        // loop whose args carry no -File/-TaskFile — e.g. the bmad loop's `loop-bmad --state-dir
        // <path> ...` — fell through to the bare state-dir fallback below, so ANY such bystander
        // tripped the "AlreadyRunning" guard and silently blocked start/reignite.
        if let Some(stem) = Path::new(&start.program).file_stem().and_then(|s| s.to_str()) {
            let s = stem.to_lowercase();
            if !s.is_empty() {
                tokens.push(s);
            }
        }
        let args = &start.args;
        for (i, a) in args.iter().enumerate() {
            let lower = a.to_lowercase();
            // The script file passed to -File: take just the basename (e.g. bmad-loop.ps1).
            if lower == "-file" || lower == "-c" || lower == "-command" {
                if let Some(next) = args.get(i + 1) {
                    if let Some(base) = Path::new(next).file_name().and_then(|s| s.to_str()) {
                        tokens.push(base.to_lowercase());
                    }
                }
            }
            // The task file disambiguates roman (TASK.md) from calc (TASK.calc.md).
            if lower == "-taskfile" {
                if let Some(next) = args.get(i + 1) {
                    if let Some(base) = Path::new(next).file_name().and_then(|s| s.to_str()) {
                        tokens.push(base.to_lowercase());
                    } else {
                        tokens.push(next.to_lowercase());
                    }
                }
            }
        }
    }
    // Fallback: if a loop.json had no usable script token, key on the stateDir so we at
    // least don't treat *everything* as a match (mirrors the engine's stateDir intent).
    if tokens.is_empty() {
        tokens.push(def.state_dir.to_lowercase());
    }
    tokens
}

/// Does a candidate process command line belong to this loop? True iff every guard token
/// appears in the (lowercased, path-normalized) command line. Tested in isolation.
fn cmdline_matches(tokens: &[String], cmdline: &str) -> bool {
    if tokens.is_empty() {
        return false;
    }
    // Normalize backslashes so a token basename matches regardless of path separators.
    let hay = cmdline.to_lowercase().replace('\\', "/");
    tokens.iter().all(|t| {
        let needle = t.replace('\\', "/");
        hay.contains(&needle)
    })
}

/// Scan live processes (sysinfo) for one whose command line matches this loop. Returns its
/// PID if found. Mirrors the engine's "another orchestrator is already running" guard.
fn find_running_pid(tokens: &[String]) -> Option<u32> {
    use sysinfo::{ProcessRefreshKind, RefreshKind, System};
    // new_with_specifics performs the initial process refresh for us.
    let sys = System::new_with_specifics(
        RefreshKind::new().with_processes(ProcessRefreshKind::everything()),
    );
    for proc in sys.processes().values() {
        let cmd: String = proc
            .cmd()
            .iter()
            .map(|s| s.to_string_lossy())
            .collect::<Vec<_>>()
            .join(" ");
        if cmd.is_empty() {
            continue;
        }
        if cmdline_matches(tokens, &cmd) {
            return Some(proc.pid().as_u32());
        }
    }
    None
}

/// Resolve a loop's start `program` to an absolute path when it is a bare command name (e.g.
/// `loop-bmad`) that lives in the repo's bundled virtualenv. The app may be launched without
/// `.venv/Scripts` (Windows) / `.venv/bin` (POSIX) on PATH, in which case a bare `Command::new`
/// fails with "program not found". We walk up from the loop's base dir looking for a `.venv`
/// that contains the program, and use that absolute path. Falls back to the bare name (ambient
/// PATH lookup) when nothing is found, and passes through anything already path-qualified.
fn resolve_program(program: &str, base_dir: &Path) -> String {
    let p = Path::new(program);
    if p.is_absolute() || program.contains('/') || program.contains('\\') {
        return program.to_string();
    }
    #[cfg(windows)]
    let rel = format!("Scripts/{program}.exe");
    #[cfg(not(windows))]
    let rel = format!("bin/{program}");
    for ancestor in base_dir.ancestors() {
        let cand = ancestor.join(".venv").join(&rel);
        if cand.is_file() {
            return cand.to_string_lossy().into_owned();
        }
    }
    program.to_string()
}

/// Spawn `program` + `args` as a DETACHED child, redirecting stdout+stderr to
/// `<stateDir>/run.out` so the existing tailer can read the transcript. We do NOT wait on
/// the child. Returns its PID.
fn spawn_detached(
    state_dir: &Path,
    program: &str,
    args: &[String],
) -> Result<u32, String> {
    std::fs::create_dir_all(state_dir)
        .map_err(|e| format!("create stateDir {state_dir:?}: {e}"))?;
    let out_path = state_dir.join("run.out");
    let stdout = std::fs::File::create(&out_path)
        .map_err(|e| format!("create {out_path:?}: {e}"))?;
    let stderr = stdout
        .try_clone()
        .map_err(|e| format!("clone run.out handle: {e}"))?;

    let mut cmd = std::process::Command::new(program);
    cmd.args(args)
        .current_dir(state_dir.parent().unwrap_or(state_dir))
        .stdin(std::process::Stdio::null())
        .stdout(std::process::Stdio::from(stdout))
        .stderr(std::process::Stdio::from(stderr));

    // Windows: detach into a new process group so a parent Ctrl-C / app exit doesn't
    // signal the loop; this keeps the orchestrator alive independently.
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        const CREATE_NEW_PROCESS_GROUP: u32 = 0x0000_0200;
        cmd.creation_flags(CREATE_NEW_PROCESS_GROUP);
    }

    let child = cmd
        .spawn()
        .map_err(|e| format!("spawn {program} {args:?}: {e}"))?;
    Ok(child.id())
}

/// Shared start path: run the guard, spawn, record PID. `spec` is taken from loop.json (or
/// a resume command); never from arbitrary frontend input. `pids` is optional so the LAN route
/// (which has no desktop `LoopPids` state) can reuse the same guard+spawn path.
fn start_with_spec(
    pids: Option<&LoopPids>,
    loop_id: &str,
    def: &LoopDef,
    base_dir: &Path,
    spec: &StartSpec,
) -> Result<StartResult, String> {
    let tokens = guard_tokens(def);
    if let Some(_existing) = find_running_pid(&tokens) {
        return Err("AlreadyRunning".to_string());
    }
    let state_dir = state_dir_of(def, base_dir);
    // Resolve the engine to the bundled venv when invoked by bare name, so the spawn works
    // regardless of whether the app was launched with `.venv/Scripts` on PATH.
    let program = resolve_program(&spec.program, base_dir);
    let pid = spawn_detached(&state_dir, &program, &spec.args)?;
    // The spawn SUCCEEDED — only now clear any leftover brake. A fresh start/resume must not
    // inherit a STOP flag from a previous run (it would make the engine cooperative-stop at its
    // first checkpoint, reading as "the loop won't start"), but a FAILED spawn (above, via `?`)
    // must leave the user's banked brake intact rather than silently discarding it.
    let _ = std::fs::remove_file(stop_flag_path(def, base_dir));
    if let Some(pids) = pids {
        if let Ok(mut map) = pids.0.lock() {
            map.insert(loop_id.to_string(), pid);
        }
    }
    Ok(StartResult { pid })
}

/// Core of `start_loop` without Tauri `LoopPids` state — used by the LAN `/api/control` route.
/// Runs the concurrency guard and spawns the loop's own declared start command. Returns the PID.
pub fn start_loop_core(loop_id: &str, loops_dir: &str) -> Result<u32, String> {
    let loops = PathBuf::from(loops_dir);
    let def = load_loop_def(&loops, loop_id)?;
    let base_dir = loop_base_dir(&loops, loop_id);
    let spec = def
        .start
        .clone()
        .ok_or_else(|| format!("loop {loop_id} has no start command"))?;
    start_with_spec(None, loop_id, &def, &base_dir, &spec).map(|r| r.pid)
}

/// Core of `resume_loop` without Tauri state — prefers the checkpoint resume command, else start.
pub fn resume_loop_core(loop_id: &str, loops_dir: &str) -> Result<u32, String> {
    let loops = PathBuf::from(loops_dir);
    let def = load_loop_def(&loops, loop_id)?;
    let base_dir = loop_base_dir(&loops, loop_id);
    let spec = match read_checkpoint(&def, &base_dir).and_then(|c| c.resume) {
        Some(resume) if !resume.trim().is_empty() => parse_command_string(&resume)?,
        _ => def
            .start
            .clone()
            .ok_or_else(|| format!("loop {loop_id} has neither resume nor start command"))?,
    };
    start_with_spec(None, loop_id, &def, &base_dir, &spec).map(|r| r.pid)
}

/// A8 — core of `answer_question`. Resolve the loop's stateDir and write the PROTOCOL §1 answer
/// inbox `<stateDir>/answer.json = { "qid": qid, "kind": "review", "a": text }`. The engine (E5,
/// `loopcore.ps1` Read-AnswerInbox) reads + consumes this. `kind` is fixed to "review" per the
/// task; the engine matches on `qid` (or an untargeted answer) regardless.
pub fn answer_question_core(
    loop_id: &str,
    loops_dir: &str,
    qid: &str,
    text: &str,
) -> Result<(), String> {
    let loops = PathBuf::from(loops_dir);
    let def = load_loop_def(&loops, loop_id)?;
    let base_dir = loop_base_dir(&loops, loop_id);
    let state_dir = state_dir_of(&def, &base_dir);
    std::fs::create_dir_all(&state_dir)
        .map_err(|e| format!("create stateDir {state_dir:?}: {e}"))?;
    let path = state_dir.join("answer.json");
    // Exact §1 shape; serde_json escapes the strings safely.
    let body = serde_json::json!({
        "qid": qid,
        "kind": "review",
        "a": text,
    });
    let json = serde_json::to_string_pretty(&body)
        .map_err(|e| format!("serialize answer.json: {e}"))?;
    std::fs::write(&path, json).map_err(|e| format!("write {path:?}: {e}"))?;
    Ok(())
}

/// Parse a shell command string (the checkpoint `resume`) into program + args. Handles
/// double-quoted segments so a path with spaces stays one arg. This is NOT a general shell
/// parser — it only needs to round-trip the engine's own `pwsh -File "..."` strings.
fn parse_command_string(s: &str) -> Result<StartSpec, String> {
    let mut parts: Vec<String> = Vec::new();
    let mut cur = String::new();
    let mut in_quote = false;
    let mut any = false;
    for ch in s.chars() {
        match ch {
            '"' => {
                in_quote = !in_quote;
                any = true;
            }
            c if c.is_whitespace() && !in_quote => {
                if any {
                    parts.push(std::mem::take(&mut cur));
                    any = false;
                }
            }
            c => {
                cur.push(c);
                any = true;
            }
        }
    }
    if any {
        parts.push(cur);
    }
    if in_quote {
        return Err(format!("unbalanced quote in resume command: {s:?}"));
    }
    let mut it = parts.into_iter();
    let program = it.next().ok_or_else(|| "empty resume command".to_string())?;
    let args: Vec<String> = it.collect();
    Ok(StartSpec { program, args })
}

/// Read & parse `checkpoint.json` for a loop, if present.
fn read_checkpoint(def: &LoopDef, base_dir: &Path) -> Option<Checkpoint> {
    let p = checkpoint_file_path(def, base_dir);
    let text = std::fs::read_to_string(&p).ok()?;
    serde_json::from_str::<Checkpoint>(&text).ok()
}

/// Read the STOP file's trimmed contents, if present.
fn read_stop_flag(def: &LoopDef, base_dir: &Path) -> Option<String> {
    let p = stop_flag_path(def, base_dir);
    let text = std::fs::read_to_string(&p).ok()?;
    let mode = text.trim().to_lowercase();
    if mode.is_empty() {
        Some("phase".to_string())
    } else {
        Some(mode)
    }
}

// ---------------------------------------------------------------------------
// Tauri commands (A6)
// ---------------------------------------------------------------------------

#[tauri::command]
pub fn start_loop(
    loop_id: String,
    loops_dir: String,
    overrides: Option<Value>,
    pids: tauri::State<'_, LoopPids>,
) -> Result<StartResult, String> {
    let _ = overrides; // accepted per §6; engine-side overrides are not wired yet.
    let loops = PathBuf::from(&loops_dir);
    let def = load_loop_def(&loops, &loop_id)?;
    let base_dir = loop_base_dir(&loops, &loop_id);
    let spec = def
        .start
        .clone()
        .ok_or_else(|| format!("loop {loop_id} has no start command"))?;
    start_with_spec(Some(&pids), &loop_id, &def, &base_dir, &spec)
}

/// Core of `stop_loop`, shared by the Tauri command and the LAN `/api/control` route. Validates
/// `mode ∈ {phase,story,now}` and writes the STOP flag (stop-loop.ps1 parity). No Tauri state.
pub fn stop_loop_core(loop_id: &str, loops_dir: &str, mode: &str) -> Result<(), String> {
    // SECURITY: validate mode ∈ {phase,story,now}.
    let mode = mode.trim().to_lowercase();
    if !matches!(mode.as_str(), "phase" | "story" | "now") {
        return Err(format!("invalid stop mode: {mode:?} (expected phase|story|now)"));
    }
    let loops = PathBuf::from(loops_dir);
    let def = load_loop_def(&loops, loop_id)?;
    let base_dir = loop_base_dir(&loops, loop_id);
    let flag = stop_flag_path(&def, &base_dir);
    if let Some(parent) = flag.parent() {
        std::fs::create_dir_all(parent).map_err(|e| format!("create {parent:?}: {e}"))?;
    }
    // Exactly what stop-loop.ps1 does: write the mode word as the STOP file body.
    std::fs::write(&flag, &mode).map_err(|e| format!("write {flag:?}: {e}"))?;
    Ok(())
}

/// Core of `cancel_stop`: delete the STOP flag (idempotent on absence).
pub fn cancel_stop_core(loop_id: &str, loops_dir: &str) -> Result<(), String> {
    let loops = PathBuf::from(loops_dir);
    let def = load_loop_def(&loops, loop_id)?;
    let base_dir = loop_base_dir(&loops, loop_id);
    let flag = stop_flag_path(&def, &base_dir);
    match std::fs::remove_file(&flag) {
        Ok(()) => Ok(()),
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => Ok(()), // already clear
        Err(e) => Err(format!("remove {flag:?}: {e}")),
    }
}

#[tauri::command]
pub fn stop_loop(
    loop_id: String,
    loops_dir: String,
    mode: String,
    _pids: tauri::State<'_, LoopPids>,
) -> Result<(), String> {
    stop_loop_core(&loop_id, &loops_dir, &mode)
}

#[tauri::command]
pub fn cancel_stop(
    loop_id: String,
    loops_dir: String,
    _pids: tauri::State<'_, LoopPids>,
) -> Result<(), String> {
    cancel_stop_core(&loop_id, &loops_dir)
}

#[tauri::command]
pub fn resume_loop(
    loop_id: String,
    loops_dir: String,
    pids: tauri::State<'_, LoopPids>,
) -> Result<StartResult, String> {
    let loops = PathBuf::from(&loops_dir);
    let def = load_loop_def(&loops, &loop_id)?;
    let base_dir = loop_base_dir(&loops, &loop_id);
    // Prefer the checkpoint's resume command; fall back to start.
    let spec = match read_checkpoint(&def, &base_dir).and_then(|c| c.resume) {
        Some(resume) if !resume.trim().is_empty() => parse_command_string(&resume)?,
        _ => def
            .start
            .clone()
            .ok_or_else(|| format!("loop {loop_id} has neither resume nor start command"))?,
    };
    start_with_spec(Some(&pids), &loop_id, &def, &base_dir, &spec)
}

/// A8 — `answer_question(loopId, loopsDir, qid, text)` (PROTOCOL §6, stretch). Writes the
/// `<stateDir>/answer.json` inbox `{ qid, kind:"review", a:text }` that the engine consumes.
#[tauri::command]
pub fn answer_question(
    loop_id: String,
    loops_dir: String,
    qid: String,
    text: String,
) -> Result<(), String> {
    answer_question_core(&loop_id, &loops_dir, &qid, &text)
}

#[tauri::command]
pub fn guard_status(
    loop_id: String,
    loops_dir: String,
    pids: tauri::State<'_, LoopPids>,
) -> Result<GuardStatus, String> {
    let loops = PathBuf::from(&loops_dir);
    let def = load_loop_def(&loops, &loop_id)?;
    let base_dir = loop_base_dir(&loops, &loop_id);
    let tokens = guard_tokens(&def);

    // running via sysinfo match OR a live tracked PID.
    let scanned = find_running_pid(&tokens);
    let tracked = pids
        .0
        .lock()
        .ok()
        .and_then(|m| m.get(&loop_id).copied());
    let pid = scanned.or_else(|| tracked.filter(|&p| pid_is_alive(p)));
    let running = pid.is_some();

    Ok(GuardStatus {
        running,
        pid,
        stop_pending: read_stop_flag(&def, &base_dir),
        checkpoint: read_checkpoint(&def, &base_dir),
    })
}

/// Is a specific PID currently a live process? Used to validate a tracked PID.
fn pid_is_alive(pid: u32) -> bool {
    use sysinfo::{Pid, ProcessRefreshKind, ProcessesToUpdate, System};
    let mut sys = System::new();
    sys.refresh_processes_specifics(
        ProcessesToUpdate::Some(&[Pid::from_u32(pid)]),
        true,
        ProcessRefreshKind::new(),
    );
    sys.process(Pid::from_u32(pid)).is_some()
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

    // ----- A5 loop-CRUD tests -----

    #[test]
    fn safe_loop_id_accepts_and_rejects() {
        for ok in ["roman", "calc", "my-loop_2", "A1", "loop123"] {
            assert!(is_safe_loop_id(ok), "should accept {ok:?}");
        }
        for bad in ["", "../evil", "a/b", "a\\b", "a.b", "with space", &"x".repeat(65)] {
            assert!(!is_safe_loop_id(bad), "should reject {bad:?}");
        }
    }

    #[test]
    fn create_loop_round_trips_and_list_reads_it_back() {
        let tmp = TmpDir::new("crud");
        let loops_dir = tmp.path().to_string_lossy().to_string();

        // A console-authored generic loop def (camelCase wire shape, opaque engine).
        let def = serde_json::json!({
            "id": "mytest",
            "name": "My test loop — fix until green",
            "theme": "ember",
            "kind": "generic",
            "adapter": "generic",
            "stateDir": ".loop",
            "logFile": "log.jsonl",
            "start": { "program": "pwsh", "args": ["-NoProfile", "-File", "loop.ps1"] },
            "engine": {
                "task": "TASK.md",
                "models": { "discover": "haiku", "execute": "sonnet", "judge": "haiku", "hard": "opus" },
                "maxTurns": 30,
                "gate": { "stages": [ { "name": "test", "command": "bun test" } ] },
                "cost": { "ceilingUsd": 3.0, "alertPct": [50, 80, 100] }
            }
        });

        // create writes loops/<id>/loop.json and returns the canonical LoopDef
        let created = create_loop(loops_dir.clone(), def).expect("create_loop ok");
        assert_eq!(created.id, "mytest");
        assert_eq!(created.adapter, "generic");
        assert_eq!(created.log_file.as_deref(), Some("log.jsonl"));
        assert!(created.engine.is_some(), "engine block preserved");

        // the file exists at the expected path and is camelCase on disk
        let p = loop_json_path(tmp.path(), "mytest");
        assert!(p.exists(), "loop.json written at {p:?}");
        let on_disk = std::fs::read_to_string(&p).unwrap();
        assert!(on_disk.contains("\"stateDir\""), "camelCase key on disk");
        assert!(on_disk.contains("\"ceilingUsd\""), "nested engine key preserved");

        // list_loops reads it back as a LoopDef
        let defs = list_loops(loops_dir.clone()).expect("list_loops ok");
        let found = defs.iter().find(|d| d.id == "mytest").expect("listed");
        assert_eq!(found.name, "My test loop — fix until green");
        // list_loops now resolves a relative stateDir to an absolute path (so the watcher and
        // the engine agree on one location); the on-disk loop.json keeps the relative form.
        let want_state = tmp.path().join("mytest").join(".loop");
        assert_eq!(found.state_dir, want_state.to_string_lossy());

        // duplicate create is refused
        let dup = serde_json::json!({ "id": "mytest", "name": "x", "adapter": "generic", "stateDir": "." });
        assert!(create_loop(loops_dir.clone(), dup).is_err(), "dup id refused");

        // an unsafe id is refused before any disk write
        let evil = serde_json::json!({ "id": "../evil", "name": "x", "adapter": "generic", "stateDir": "." });
        assert!(create_loop(loops_dir.clone(), evil).is_err(), "unsafe id refused");
    }

    #[test]
    fn update_clone_delete_loop_lifecycle() {
        let tmp = TmpDir::new("crud2");
        let loops_dir = tmp.path().to_string_lossy().to_string();
        let base = serde_json::json!({
            "id": "base", "name": "base", "kind": "generic",
            "adapter": "generic", "stateDir": ".loop"
        });
        create_loop(loops_dir.clone(), base).unwrap();

        // update overwrites; a missing loop cannot be updated
        let edited = serde_json::json!({
            "id": "base", "name": "base — edited", "kind": "generic",
            "adapter": "generic", "stateDir": ".loop"
        });
        update_loop(loops_dir.clone(), "base".into(), edited).unwrap();
        let defs = list_loops(loops_dir.clone()).unwrap();
        assert_eq!(defs.iter().find(|d| d.id == "base").unwrap().name, "base — edited");
        let missing = serde_json::json!({ "id": "ghost", "name": "x", "adapter": "generic", "stateDir": "." });
        assert!(update_loop(loops_dir.clone(), "ghost".into(), missing).is_err());

        // clone duplicates under a new id; the new id must be unique
        let cloned = clone_loop(loops_dir.clone(), "base".into(), "copy".into()).unwrap();
        assert_eq!(cloned.id, "copy");
        assert_eq!(cloned.name, "base — edited", "clone copies content");
        assert!(clone_loop(loops_dir.clone(), "base".into(), "copy".into()).is_err(), "dup clone refused");
        assert_eq!(list_loops(loops_dir.clone()).unwrap().len(), 2);

        // delete removes a loop; deleting again is a no-op (idempotent)
        delete_loop(loops_dir.clone(), "copy".into()).unwrap();
        assert_eq!(list_loops(loops_dir.clone()).unwrap().len(), 1);
        delete_loop(loops_dir.clone(), "copy".into()).unwrap();
    }

    #[test]
    fn list_loops_reads_seed_defs() {
        let loops_dir = PathBuf::from(concat!(env!("CARGO_MANIFEST_DIR"), "/../loops"));
        let defs = list_loops(loops_dir.to_string_lossy().to_string()).unwrap();
        let ids: Vec<&str> = defs.iter().map(|d| d.id.as_str()).collect();
        assert!(ids.contains(&"hello"));
        // the hello seed is a self-contained generic loop carrying logFile + adapter
        let hello = defs.iter().find(|d| d.id == "hello").unwrap();
        assert_eq!(hello.adapter, "generic");
        assert_eq!(hello.log_file.as_deref(), Some("log.jsonl"));
    }

    // ----- A6 live-control tests (no real claude loop is ever spawned) -----

    /// Unique temp dir per test; cleaned on drop.
    struct TmpDir(PathBuf);
    impl TmpDir {
        fn new(tag: &str) -> Self {
            let mut p = std::env::temp_dir();
            let nanos = std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .unwrap()
                .as_nanos();
            p.push(format!("orrery-test-{tag}-{nanos}-{:?}", std::thread::current().id()));
            std::fs::create_dir_all(&p).unwrap();
            TmpDir(p)
        }
        fn path(&self) -> &Path {
            &self.0
        }
    }
    impl Drop for TmpDir {
        fn drop(&mut self) {
            let _ = std::fs::remove_dir_all(&self.0);
        }
    }

    fn def_with_state_dir(id: &str, state_dir: &Path, args: Vec<&str>) -> LoopDef {
        LoopDef {
            id: id.to_string(),
            name: id.to_string(),
            theme: None,
            kind: Some("generic".to_string()),
            state_dir: state_dir.to_string_lossy().to_string(),
            adapter: "generic".to_string(),
            log_file: Some("log.jsonl".to_string()),
            stop_flag: None,
            checkpoint: None,
            start: Some(StartSpec {
                program: "pwsh".to_string(),
                args: args.into_iter().map(|s| s.to_string()).collect(),
            }),
            engine: None,
        }
    }

    #[test]
    fn relative_state_dir_resolves_under_loop_base_dir() {
        // A self-contained seed (stateDir ".loop") must resolve under the loop's own dir
        // (loops/<id>), NOT the app's cwd — otherwise spawn_detached's current_dir is wrong.
        let base = Path::new("/loops/hello");
        let def = LoopDef {
            id: "hello".into(),
            name: "hello".into(),
            theme: None,
            kind: Some("generic".into()),
            state_dir: ".loop".into(),
            adapter: "generic".into(),
            log_file: Some("log.jsonl".into()),
            stop_flag: Some(".loop/STOP".into()),
            checkpoint: Some(".loop/checkpoint.json".into()),
            start: None,
            engine: None,
        };
        // relative stateDir + STOP + checkpoint all resolve under base.
        assert_eq!(state_dir_of(&def, base), Path::new("/loops/hello/.loop"));
        assert_eq!(stop_flag_path(&def, base), Path::new("/loops/hello/.loop/STOP"));
        assert_eq!(
            checkpoint_file_path(&def, base),
            Path::new("/loops/hello/.loop/checkpoint.json")
        );
        // and the spawn cwd is the loop's own dir (parent of the resolved stateDir).
        assert_eq!(state_dir_of(&def, base).parent(), Some(base));
    }

    #[test]
    fn absolute_state_dir_is_left_untouched() {
        // ABSOLUTE stateDir must be used verbatim regardless of base dir (parity with before).
        let base = Path::new("/somewhere/else");
        let abs = if cfg!(windows) { "C:/abs/state" } else { "/abs/state" };
        let def = LoopDef {
            id: "ext".into(),
            name: "ext".into(),
            theme: None,
            kind: Some("generic".into()),
            state_dir: abs.into(),
            adapter: "generic".into(),
            log_file: Some("log.jsonl".into()),
            stop_flag: None,
            checkpoint: None,
            start: None,
            engine: None,
        };
        assert_eq!(state_dir_of(&def, base), PathBuf::from(abs));
        assert_eq!(stop_flag_path(&def, base), PathBuf::from(abs).join("STOP"));
        assert_eq!(
            checkpoint_file_path(&def, base),
            PathBuf::from(abs).join("checkpoint.json")
        );
    }

    #[test]
    fn stop_loop_writes_mode_and_cancel_removes_it() {
        let tmp = TmpDir::new("stop");
        let def = def_with_state_dir("hello", tmp.path(), vec![
            "--loop-json", "loop.json", "--cwd", ".", "--state-dir", ".loop",
        ]);
        // stateDir is absolute (the temp dir) so the base dir is irrelevant here.
        let base = tmp.path();
        let flag = stop_flag_path(&def, base);

        // Replicate the command body's write (the command itself needs tauri::State).
        std::fs::write(&flag, "phase").unwrap();
        assert_eq!(std::fs::read_to_string(&flag).unwrap(), "phase");
        assert_eq!(read_stop_flag(&def, base).as_deref(), Some("phase"));

        std::fs::write(&flag, "story").unwrap();
        assert_eq!(read_stop_flag(&def, base).as_deref(), Some("story"));

        // cancel removes it; second cancel is a no-op (NotFound is Ok).
        std::fs::remove_file(&flag).unwrap();
        assert!(!flag.exists());
        assert_eq!(read_stop_flag(&def, base), None);
    }

    #[test]
    fn stop_mode_validation_rejects_bad_input() {
        for bad in ["kill", "", "PHASE; rm -rf /", "now ", "halt"] {
            let m = bad.trim().to_lowercase();
            let ok = matches!(m.as_str(), "phase" | "story" | "now");
            // only the exact words are accepted
            assert_eq!(ok, ["phase", "story", "now"].contains(&m.as_str()), "{bad:?}");
        }
        // sanity: the three valid words pass
        for good in ["phase", "story", "now"] {
            assert!(matches!(good, "phase" | "story" | "now"));
        }
    }

    #[test]
    fn guard_status_reads_checkpoint_and_stop_file() {
        let tmp = TmpDir::new("guard");
        let def = def_with_state_dir("bmad", tmp.path(), vec![
            "-NoProfile", "-File", "bmad-loop.ps1",
        ]);

        // Drop a checkpoint.json fixture and a STOP file into the temp stateDir.
        let cp = r#"{
            "updatedAt": "2026-06-19T10:22:06.1447403+05:30",
            "stage": "between-stories (clean)",
            "story": null,
            "branch": "develop",
            "mergeBase": "develop",
            "cumUsd": 26.7472,
            "resume": "pwsh -File \"bmad-loop.ps1\""
        }"#;
        let base = tmp.path();
        std::fs::write(checkpoint_file_path(&def, base), cp).unwrap();
        std::fs::write(stop_flag_path(&def, base), "story").unwrap();

        let parsed = read_checkpoint(&def, base).expect("checkpoint parses");
        assert_eq!(parsed.stage.as_deref(), Some("between-stories (clean)"));
        assert_eq!(parsed.branch.as_deref(), Some("develop"));
        assert!((parsed.cum_usd.unwrap() - 26.7472).abs() < 1e-6);
        assert_eq!(
            parsed.resume.as_deref(),
            Some("pwsh -File \"bmad-loop.ps1\"")
        );

        assert_eq!(read_stop_flag(&def, base).as_deref(), Some("story"));
    }

    #[test]
    fn guard_tokens_disambiguate_shared_state_dir() {
        // All three seed loops share one stateDir; tokens must differ by script + task file.
        let sd = Path::new(".loop");
        let bmad = def_with_state_dir("bmad", sd, vec!["-NoProfile", "-File", "bmad-loop.ps1"]);
        let roman = def_with_state_dir("roman", sd, vec!["-NoProfile", "-File", "loop.ps1", "-TaskFile", "TASK.md"]);
        let calc = def_with_state_dir("calc", sd, vec!["-NoProfile", "-File", "loop.ps1", "-TaskFile", "TASK.calc.md"]);

        let t_bmad = guard_tokens(&bmad);
        let t_roman = guard_tokens(&roman);
        let t_calc = guard_tokens(&calc);

        assert!(t_bmad.contains(&"bmad-loop.ps1".to_string()), "{t_bmad:?}");
        assert!(t_roman.contains(&"loop.ps1".to_string()) && t_roman.contains(&"task.md".to_string()), "{t_roman:?}");
        assert!(t_calc.contains(&"loop.ps1".to_string()) && t_calc.contains(&"task.calc.md".to_string()), "{t_calc:?}");

        // A real bmad command line matches bmad, not roman/calc.
        let bmad_cmd = "pwsh -NoProfile -File bmad-loop.ps1";
        assert!(cmdline_matches(&t_bmad, bmad_cmd));
        assert!(!cmdline_matches(&t_roman, bmad_cmd));
        assert!(!cmdline_matches(&t_calc, bmad_cmd));

        // The roman command line matches roman but NOT calc (TASK.md vs TASK.calc.md),
        // even though both reference loop.ps1 — the task file is the discriminator.
        let roman_cmd = "pwsh -NoProfile -File loop.ps1 -TaskFile TASK.md";
        assert!(cmdline_matches(&t_roman, roman_cmd));
        assert!(!cmdline_matches(&t_calc, roman_cmd), "calc must not match roman's cmd");
        assert!(!cmdline_matches(&t_bmad, roman_cmd));

        // calc's own command line matches calc (TASK.calc.md contains 'task.md'? no — 'task.calc.md').
        let calc_cmd = "pwsh -NoProfile -File loop.ps1 -TaskFile TASK.calc.md";
        assert!(cmdline_matches(&t_calc, calc_cmd));
        // NOTE: roman's tokens (loop.ps1 + task.md) would substring-match 'task.calc.md'
        // because "task.calc.md" contains "task" then ".md" but not the contiguous "task.md".
        assert!(!cmdline_matches(&t_roman, calc_cmd), "roman must not match calc's cmd");
    }

    #[test]
    fn guard_tokens_key_on_program_so_log_readers_dont_block_start() {
        // The bmad loop's start carries NO -File/-TaskFile (it uses `loop-bmad --state-dir <path>`),
        // so the guard must key on the PROGRAM name — NOT the bare state-dir path. Otherwise a
        // bystander that merely references the state dir (a log tailer, an editor) trips the
        // AlreadyRunning guard and silently blocks Reignite (the exact bug this fixes).
        let def = LoopDef {
            id: "bmad".into(),
            name: "bmad".into(),
            theme: None,
            kind: Some("external".into()),
            state_dir: "D:/dev/loop/orrery/loops/bmad/.loop".into(),
            adapter: "bmad".into(),
            log_file: Some("log.jsonl".into()),
            stop_flag: None,
            checkpoint: None,
            start: Some(StartSpec {
                program: "loop-bmad".into(),
                args: ["--project-root", "D:/dev/brain2", "--state-dir",
                       "D:/dev/loop/orrery/loops/bmad/.loop", "--no-smoke"]
                    .into_iter().map(|s| s.to_string()).collect(),
            }),
            engine: None,
        };
        let tokens = guard_tokens(&def);
        assert!(tokens.contains(&"loop-bmad".to_string()), "must key on program: {tokens:?}");
        // The state-dir path is NOT a standalone token (it would over-match); the program is.
        assert!(!tokens.contains(&"d:/dev/loop/orrery/loops/bmad/.loop".to_string()), "{tokens:?}");

        // the REAL orchestrator (resolved exe + state-dir on its cmdline) matches → guard works
        let real = "d:/dev/loop/.venv/scripts/loop-bmad.exe --project-root d:/dev/brain2 \
                    --state-dir d:/dev/loop/orrery/loops/bmad/.loop --no-smoke";
        assert!(cmdline_matches(&tokens, real), "real loop-bmad must still match");

        // bystanders that only reference the state dir must NOT match (the fix)
        let tail = "c:/program files/git/usr/bin/tail.exe -n0 -f \
                    d:/dev/loop/orrery/loops/bmad/.loop/log.jsonl";
        assert!(!cmdline_matches(&tokens, tail), "a log tail must NOT trip the guard");
        let editor = "code.exe d:/dev/loop/orrery/loops/bmad/.loop/log.jsonl";
        assert!(!cmdline_matches(&tokens, editor), "an editor must NOT trip the guard");
    }

    #[test]
    fn answer_question_writes_exact_inbox_shape() {
        // A8: resolve the loop's stateDir from loop.json and write the PROTOCOL §1 answer.json
        // `{ qid, kind:"review", a }`. Build a throwaway loops/<id>/loop.json whose stateDir is a
        // temp dir, then assert the exact file contents.
        let tmp = TmpDir::new("answer");
        let loops_dir = tmp.path().join("loops");
        let state_dir = tmp.path().join("state");
        std::fs::create_dir_all(loops_dir.join("roman")).unwrap();
        std::fs::create_dir_all(&state_dir).unwrap();

        let def = serde_json::json!({
            "id": "roman",
            "name": "roman",
            "kind": "generic",
            "adapter": "generic",
            "stateDir": state_dir.to_string_lossy(),
            "logFile": "log.jsonl"
        });
        std::fs::write(
            loops_dir.join("roman").join("loop.json"),
            serde_json::to_string_pretty(&def).unwrap(),
        )
        .unwrap();

        answer_question_core(
            "roman",
            &loops_dir.to_string_lossy(),
            "4",
            "Yes — accept 0x hex literals.",
        )
        .expect("answer_question_core ok");

        // exact shape on disk: { qid, kind:"review", a }
        let written = std::fs::read_to_string(state_dir.join("answer.json")).unwrap();
        let v: serde_json::Value = serde_json::from_str(&written).unwrap();
        assert_eq!(v["qid"], "4");
        assert_eq!(v["kind"], "review");
        assert_eq!(v["a"], "Yes — accept 0x hex literals.");
        // exactly these three keys (no stray fields)
        assert_eq!(v.as_object().unwrap().len(), 3, "only qid/kind/a: {v}");

        // a traversal loop id is refused before any write
        assert!(answer_question_core("../evil", &loops_dir.to_string_lossy(), "1", "x").is_err());
    }

    #[test]
    fn parse_command_string_handles_quoted_paths() {
        let spec = parse_command_string("pwsh -File \"bmad-loop.ps1\"").unwrap();
        assert_eq!(spec.program, "pwsh");
        assert_eq!(spec.args, vec!["-File", "bmad-loop.ps1"]);

        let spec2 = parse_command_string("pwsh -NoProfile -File \"C:/has space/loop.ps1\" -TaskFile TASK.md").unwrap();
        assert_eq!(spec2.program, "pwsh");
        assert_eq!(
            spec2.args,
            vec!["-NoProfile", "-File", "C:/has space/loop.ps1", "-TaskFile", "TASK.md"]
        );

        // unbalanced quote is rejected
        assert!(parse_command_string("pwsh -File \"oops").is_err());
    }

    #[test]
    fn spawn_detached_captures_pid_and_redirects_output() {
        // Harmless no-op: pwsh prints a marker and exits 0. Proves spawn + PID capture +
        // run.out redirection WITHOUT touching claude. Skips gracefully if pwsh is absent.
        if std::process::Command::new("pwsh").arg("-Version").output().is_err() {
            eprintln!("pwsh not available — skipping spawn smoke test");
            return;
        }
        let tmp = TmpDir::new("spawn");
        // stateDir is a child so its parent (cwd) exists.
        let state_dir = tmp.path().join(".loop");
        let pid = spawn_detached(
            &state_dir,
            "pwsh",
            &[
                "-NoProfile".to_string(),
                "-Command".to_string(),
                "Write-Output 'ORRERY_SPAWN_OK'; exit 0".to_string(),
            ],
        )
        .expect("spawn no-op pwsh");
        assert!(pid > 0, "captured a real PID");

        // Wait briefly for the child to flush + exit, then confirm run.out got the marker.
        let out = state_dir.join("run.out");
        let mut content = String::new();
        for _ in 0..50 {
            if let Ok(s) = std::fs::read_to_string(&out) {
                if s.contains("ORRERY_SPAWN_OK") {
                    content = s;
                    break;
                }
            }
            std::thread::sleep(std::time::Duration::from_millis(100));
        }
        assert!(content.contains("ORRERY_SPAWN_OK"), "run.out captured stdout: {content:?}");
    }
}
