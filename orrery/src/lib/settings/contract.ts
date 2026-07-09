// settings/contract.ts — the PUBLIC SURFACE between the Settings store (WS-A),
// the panels that consume it (WS-B/E), and the Rust backend (WS-E).
//
// This file is types + string constants ONLY. There is NO implementation here:
// WS-A implements `SettingsStoreApi`; every workstream imports the same `CMD`
// command names so TS `invoke()` and the Rust handlers can never disagree.

import type { Settings, ProviderInstance, RunnerId, ProviderId, AuthMode } from './schema';

/**
 * The reactive public API of the settings store. `data` is a live (reactive)
 * `Settings`; getters/methods are the only sanctioned way to read or mutate it.
 * All paths are dotted keys into `Settings` (e.g. 'general.lanPort'), matching
 * `SettingMeta.key` and `defaultFor()`.
 */
export interface SettingsStoreApi {
  /** the live settings tree (reactive). */
  readonly data: Settings;
  /** true once the persisted file (or dev localStorage) has been read. */
  readonly loaded: boolean;
  /** which layer edits currently target. */
  readonly scope: 'user' | 'workspace';

  /** Load persistence + start the file watcher; resolves to a teardown fn. */
  init(): Promise<() => void>;

  /** Read a value by dotted path. */
  get<T>(path: string): T;
  /** Write a value by dotted path; persists + applies (may hit the backend). */
  set(path: string, value: unknown): Promise<void>;
  /** Has this path diverged from its default? */
  isChanged(path: string): boolean;

  /** Reset one key to its default. */
  resetKey(path: string): void;
  /** Reset every key in a category to its defaults. */
  resetSection(cat: keyof Settings): void;
  /** Reset the entire settings tree to DEFAULTS. */
  resetAll(): void;

  /** the theme actually in force after resolving 'system'. */
  readonly resolvedTheme: 'light' | 'dark';
  /** whether motion should currently be reduced (resolves 'system' + OS pref). */
  readonly reducedMotion: boolean;

  // ── BYOK helpers ──────────────────────────────────────────────────────────
  /** Add a provider instance; returns its generated id. */
  addInstance(instance: Omit<ProviderInstance, 'id'>): Promise<string>;
  /** Patch an existing provider instance. */
  updateInstance(id: string, patch: Partial<ProviderInstance>): Promise<void>;
  /** Remove a provider instance (and its keychain secret, if any). */
  removeInstance(id: string): Promise<void>;

  /** per-instance-account keychain presence, mirrored from the backend. */
  readonly keychain: Record<string, 'set' | 'unset'>;
  /** per-instance BYOK auth reachability/status, keyed by instance id. */
  readonly authStatus: Record<string, ByokAuthStatus>;
}

/** Result of a BYOK auth probe (byok_auth_status) for one instance. */
export interface ByokAuthStatus {
  runner: RunnerId;
  provider: ProviderId;
  mode: AuthMode;
  ok: boolean;
  detail?: string;
}

/**
 * Every Rust command the Settings feature invokes. TS `invoke(CMD.x)` and the
 * `#[tauri::command]` handlers MUST agree on these exact names.
 */
export const CMD = {
  /** resolve the on-disk path of the settings.json file. */
  settingsConfigPath: 'settings_config_path',
  /** open settings.json in the OS default editor. */
  openSettingsFile: 'open_settings_file',
  /** resolve the effective loops dir (override or built-in default). */
  resolveLoopsDir: 'resolve_loops_dir',
  /** store a secret in the OS keychain under an account. */
  keychainSet: 'keychain_set',
  /** does the keychain hold a secret for this account? */
  keychainHas: 'keychain_has',
  /** delete a keychain secret. */
  keychainDelete: 'keychain_delete',
  /** probe BYOK auth for an instance. */
  byokAuthStatus: 'byok_auth_status',
  /** export settings (minus secrets) to a file. */
  exportSettings: 'export_settings',
  /** import settings from a file. */
  importSettings: 'import_settings',
  /** start watching settings.json for external edits. */
  watchSettings: 'watch_settings',
  /** stop watching settings.json. */
  unwatchSettings: 'unwatch_settings',
} as const;

/** Union of the raw command-name strings, for typing `invoke` call sites. */
export type CommandName = (typeof CMD)[keyof typeof CMD];

/** The persisted settings filename (under the app config dir). */
export const STORE_FILE = 'settings.json';

/** localStorage key used in dev, where the Tauri backend is a no-op. */
export const DEV_LS_KEY = 'orrery.settings';
