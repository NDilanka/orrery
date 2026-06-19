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
        .invoke_handler(tauri::generate_handler![
            control::load_run,
            control::watch_run,
            control::list_loops,
        ])
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}
