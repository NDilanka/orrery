//! Orrery Tauri core — tails autonomous-loop logs and reduces them into a universal RunState.

pub mod control;
pub mod lan;
pub mod live;
pub mod model;
pub mod reducer;
pub mod settings;
pub mod sprint;
pub mod tailer;
pub mod watcher;

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_opener::init())
        // WS-D: settings persistence (store) + native file dialogs (import/export).
        .plugin(tauri_plugin_store::Builder::new().build())
        .plugin(tauri_plugin_dialog::init())
        // A6: loopId → spawned PID, shared across control commands.
        .manage(control::LoopPids::default())
        // A7: the opt-in LAN server handle (None until start_lan_server runs).
        .manage(lan::LanServer::default())
        // WS-D: record the app config dir once (where tauri-plugin-store writes settings.json), so
        // `settings::byok_env()` / `settings_config_path()` resolve WITHOUT an AppHandle later —
        // needed by the LAN control routes, which have none.
        .setup(|app| {
            use tauri::Manager;
            match app.path().app_config_dir() {
                Ok(dir) => {
                    let _ = std::fs::create_dir_all(&dir);
                    settings::init_config_dir(dir);
                }
                Err(e) => eprintln!("[orrery] could not resolve app config dir: {e}"),
            }
            Ok(())
        })
        .invoke_handler(tauri::generate_handler![
            control::load_run,
            control::watch_run,
            control::unwatch_run,
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
            // WS-D — settings + multi-provider BYOK
            settings::settings_config_path,
            settings::open_settings_file,
            settings::resolve_loops_dir,
            settings::keychain_set,
            settings::keychain_has,
            settings::keychain_delete,
            settings::byok_auth_status,
            settings::export_settings,
            settings::import_settings,
            settings::watch_settings,
            settings::unwatch_settings,
        ])
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}
