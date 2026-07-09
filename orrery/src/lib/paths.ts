// Absolute path to the loops/ registry (loops/<id>/loop.json). Tauri commands (list_loops,
// load_run, watch_run, start_loop, create_loop, ...) all need to agree on ONE location
// regardless of the app's working directory (the launcher runs from orrery/, cargo builds from
// orrery/src-tauri/ — a relative 'orrery/loops' resolves against neither). This used to be
// copy-pasted separately in transport/tauri.ts and stores/cosmos.svelte.ts (and baked implicitly
// into transport/index.ts's bmad seed stateDir) — one source of truth now.
//
// Resolved at build time by vite.config.js: VITE_LOOPS_DIR (env / orrery/.env) when set,
// otherwise `<repo>/orrery/loops` of the checkout being built — portable across machines.
// That build-time value is the DEFAULT. At runtime (desktop), the user can OVERRIDE the loops
// dir via settings.general.loopsDir; the Rust `resolve_loops_dir` command reads settings.json
// off disk (boot-safe, no JS-store dependency) and returns the effective dir. `resolveLoopsDir()`
// seeds `getLoopsDir()` from it ONCE at boot; every consumer reads `getLoopsDir()` AT USE TIME.
declare const __ORRERY_LOOPS_DIR__: string;

/** The build-time default loops dir. Runtime override (if any) is applied on top by resolveLoopsDir(). */
export const DEFAULT_LOOPS_DIR: string = __ORRERY_LOOPS_DIR__;

// The loops dir in effect for this session. Starts at the build-time default; resolveLoopsDir()
// may replace it once with the backend-resolved (settings-honoring) value.
let currentLoopsDir: string = DEFAULT_LOOPS_DIR;

/**
 * The loops dir in effect NOW. ALWAYS call this at use-time (never capture the value at module
 * import): before boot / in dev / no-Tauri it returns DEFAULT_LOOPS_DIR; after resolveLoopsDir()
 * has run under Tauri it may return the user's settings.general.loopsDir override.
 */
export function getLoopsDir(): string {
  return currentLoopsDir;
}

function hasTauriRuntime(): boolean {
  // Mirror transport/index.ts hasTauri() — inlined here to avoid a paths↔transport import cycle.
  if (typeof window === 'undefined') return false;
  return '__TAURI_INTERNALS__' in window || '__TAURI__' in window;
}

// Cached so the backend is hit at most once and concurrent awaiters share one resolution.
let resolvePromise: Promise<string> | null = null;

/**
 * Seed the runtime loops dir ONCE from the Rust `resolve_loops_dir` command (which honors
 * settings.general.loopsDir, falling back to the built-in default). Idempotent and safe to
 * await any number of times — only the first call reaches the backend; later calls resolve to
 * the same cached value. In dev / no-Tauri (or on any invoke error) it leaves DEFAULT_LOOPS_DIR
 * untouched. The orchestrator awaits this in +page.svelte onMount BEFORE cosmos.load().
 */
export function resolveLoopsDir(): Promise<string> {
  if (resolvePromise) return resolvePromise;
  resolvePromise = (async () => {
    if (!hasTauriRuntime()) return currentLoopsDir;
    try {
      const { invoke } = await import('@tauri-apps/api/core');
      const dir = (await invoke('resolve_loops_dir')) as string;
      if (typeof dir === 'string' && dir.trim().length > 0) {
        currentLoopsDir = dir;
      }
    } catch {
      // keep DEFAULT_LOOPS_DIR — dev/no-Tauri or a backend without the command
    }
    return currentLoopsDir;
  })();
  return resolvePromise;
}
