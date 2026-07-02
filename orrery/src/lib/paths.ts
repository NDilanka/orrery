// Absolute path to the loops/ registry (loops/<id>/loop.json). Tauri commands (list_loops,
// load_run, watch_run, start_loop, create_loop, ...) all need to agree on ONE location
// regardless of the app's working directory (the launcher runs from orrery/, cargo builds from
// orrery/src-tauri/ — a relative 'orrery/loops' resolves against neither). This used to be
// copy-pasted separately in transport/tauri.ts and stores/cosmos.svelte.ts (and baked implicitly
// into transport/index.ts's bmad seed stateDir) — one source of truth now.
//
// LOCAL override for dev; a packaged build should resolve this via a Tauri path API instead
// (out of scope here — the constant stays, runtime resolution doesn't).
export const DEFAULT_LOOPS_DIR = 'D:/dev/loop/orrery/loops';
