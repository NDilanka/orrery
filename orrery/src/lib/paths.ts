// Absolute path to the loops/ registry (loops/<id>/loop.json). Tauri commands (list_loops,
// load_run, watch_run, start_loop, create_loop, ...) all need to agree on ONE location
// regardless of the app's working directory (the launcher runs from orrery/, cargo builds from
// orrery/src-tauri/ — a relative 'orrery/loops' resolves against neither). This used to be
// copy-pasted separately in transport/tauri.ts and stores/cosmos.svelte.ts (and baked implicitly
// into transport/index.ts's bmad seed stateDir) — one source of truth now.
//
// Resolved at build time by vite.config.js: VITE_LOOPS_DIR (env / orrery/.env) when set,
// otherwise `<repo>/orrery/loops` of the checkout being built — portable across machines.
// A packaged build should resolve this via a Tauri path API instead (out of scope here —
// the constant stays, runtime resolution doesn't).
declare const __ORRERY_LOOPS_DIR__: string;

export const DEFAULT_LOOPS_DIR: string = __ORRERY_LOOPS_DIR__;
