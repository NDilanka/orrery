//! Orrery Tauri core — tails autonomous-loop logs and reduces them into a universal RunState.

pub mod control;
pub mod lan;
pub mod live;
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
        // A7: the opt-in LAN server handle (None until start_lan_server runs).
        .manage(lan::LanServer::default())
        .invoke_handler(tauri::generate_handler![
            control::load_run,
            control::watch_run,
            control::list_loops,
            // A5 — loop CRUD (Tuning Console write-side)
            control::create_loop,
            control::update_loop,
            control::clone_loop,
            control::delete_loop,
            // A6 — live control
            control::start_loop,
            control::stop_loop,
            control::cancel_stop,
            control::resume_loop,
            control::guard_status,
            // A8 — answer-from-UI (writes answer.json inbox)
            control::answer_question,
            // U3 — creation & onboarding (scaffold a TASK.md, probe a gate command)
            control::write_loop_file,
            control::probe_command,
            // A7 — opt-in LAN server (phone/browser observe + token-gated control)
            lan::start_lan_server,
            lan::stop_lan_server,
        ])
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}
