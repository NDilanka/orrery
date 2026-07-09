// settings/backend.ts — the persistence seam under the settings store (WS-A).
//
// Two interchangeable implementations of one tiny `SettingsBackend` contract:
//   • TauriBackend        — the real desktop path, on @tauri-apps/plugin-store.
//   • LocalStorageBackend — the `vite dev` / plain-browser fallback, on
//                           localStorage[DEV_LS_KEY].
//
// Storage layout choice: the WHOLE `Settings` object round-trips under a SINGLE
// key (`'settings'`) in the plugin-store, and as one JSON blob under
// localStorage[DEV_LS_KEY]. This is the simplest shape that round-trips (no
// per-category fan-out / re-assembly) and it keeps the dev localStorage value
// byte-for-byte the JSON `Settings` the FOUC bootstrap script reads synchronously.
//
// The store injects a backend for tests; in the app `makeBackend()` picks one by
// Tauri presence. Both impls ALSO leave localStorage[DEV_LS_KEY] populated so the
// FOUC script has a synchronous theme mirror in every mode.

import { STORE_FILE, DEV_LS_KEY } from './contract';
import type { Settings } from './schema';

/** The single persisted-tree key inside the plugin-store file. */
const ROOT_KEY = 'settings';

/**
 * Tauri detection, kept LOCAL (not imported from ../transport) so the settings
 * feature stays a self-contained, unit-testable island rather than dragging the
 * whole transport graph into every importer. Mirrors `hasTauri()` in
 * transport/index.ts verbatim — `__TAURI_INTERNALS__` is the IPC bridge always
 * present inside a Tauri webview; `__TAURI__` covers `withGlobalTauri`.
 */
export function hasTauri(): boolean {
  if (typeof window === 'undefined') return false;
  return '__TAURI_INTERNALS__' in window || '__TAURI__' in window;
}

/** Persistence seam. `null` from load/reload means "nothing persisted yet". */
export interface SettingsBackend {
  /** Read the persisted tree (or null when none has been written). */
  load(): Promise<Settings | null>;
  /** Re-read from the source of truth, bypassing any in-memory cache. */
  reload(): Promise<Settings | null>;
  /** Persist the whole tree. */
  save(settings: Settings): Promise<void>;
}

/** Write the synchronous FOUC mirror. Safe in any environment. */
function writeSyncMirror(settings: Settings): void {
  try {
    if (typeof localStorage !== 'undefined') {
      localStorage.setItem(DEV_LS_KEY, JSON.stringify(settings));
    }
  } catch {
    /* private-mode / quota / SSR — the mirror is best-effort. */
  }
}

// ─── Tauri: @tauri-apps/plugin-store ────────────────────────────────────────

type PluginStore = {
  get<T>(key: string): Promise<T | undefined>;
  set(key: string, value: unknown): Promise<void>;
  save(): Promise<void>;
  reload(): Promise<void>;
};

class TauriBackend implements SettingsBackend {
  private store: PluginStore | null = null;

  private async ensure(): Promise<PluginStore> {
    if (this.store) return this.store;
    const { load } = await import('@tauri-apps/plugin-store');
    // autoSave keeps the file current even if a caller forgets an explicit save();
    // `defaults: {}` is required by StoreOptions — the tree lives under ROOT_KEY, so
    // an empty default map is correct (a missing ROOT_KEY reads back as undefined).
    this.store = (await load(STORE_FILE, {
      autoSave: true,
      defaults: {},
    })) as unknown as PluginStore;
    return this.store;
  }

  async load(): Promise<Settings | null> {
    const store = await this.ensure();
    return (await store.get<Settings>(ROOT_KEY)) ?? null;
  }

  async reload(): Promise<Settings | null> {
    const store = await this.ensure();
    await store.reload();
    return (await store.get<Settings>(ROOT_KEY)) ?? null;
  }

  async save(settings: Settings): Promise<void> {
    const store = await this.ensure();
    await store.set(ROOT_KEY, settings);
    await store.save();
    writeSyncMirror(settings); // keep the FOUC mirror current under Tauri too
  }
}

// ─── Dev / browser: localStorage[DEV_LS_KEY] ────────────────────────────────

class LocalStorageBackend implements SettingsBackend {
  private read(): Settings | null {
    if (typeof localStorage === 'undefined') return null;
    const raw = localStorage.getItem(DEV_LS_KEY);
    if (!raw) return null;
    try {
      return JSON.parse(raw) as Settings;
    } catch {
      return null; // corrupt mirror → fall back to DEFAULTS in the store
    }
  }

  async load(): Promise<Settings | null> {
    return this.read();
  }

  async reload(): Promise<Settings | null> {
    return this.read();
  }

  async save(settings: Settings): Promise<void> {
    writeSyncMirror(settings);
  }
}

/** Choose the backend for the current environment. */
export function makeBackend(): SettingsBackend {
  return hasTauri() ? new TauriBackend() : new LocalStorageBackend();
}
