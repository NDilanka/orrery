//! Orrery Tauri core — tails autonomous-loop logs and reduces them into a universal RunState.

pub mod control;
pub mod model;
pub mod reducer;
pub mod sprint;
pub mod tailer;
pub mod watcher;

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_opener::init())
        // A6: loopId → spawned PID, shared across control commands.
        .manage(control::LoopPids::default())
        .invoke_handler(tauri::generate_handler![
            control::load_run,
            control::watch_run,
            control::list_loops,
            // A6 — live control
            control::start_loop,
            control::stop_loop,
            control::cancel_stop,
            control::resume_loop,
            control::guard_status,
        ])
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}
