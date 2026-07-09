// settings/settingsIo.ts — import / export of the settings tree (WS-A).
//
// Export writes the tree MINUS any secret-shaped field (the `Settings` shape
// carries none — secrets live only in the OS keychain — but we strip defensively
// so a future/foreign field can never leak through an export file). Import
// validates `version === 1`, deep-merges over DEFAULTS (dropping unknown keys),
// then replaces + re-applies + persists through the store.
//
// Pure helpers (`redactSecrets`, `validateImport`) are exported for unit tests;
// the `exportSettings` / `importSettings` orchestrators do the dialog / IO and
// degrade gracefully in `vite dev` (Blob download / <input type=file>).

import { CMD } from './contract';
import type { Settings } from './schema';
import { hasTauri } from './backend';
import { settingsStore, mergeSettings } from '../stores/settings.svelte';

/** Exact (lowercased) field names never allowed into an export file. */
const SECRET_FIELDS = new Set([
  'key',
  'token',
  'secret',
  'apikey',
  'api_key',
  'password',
  'passwd',
  'auth',
  'authtoken',
  'auth_token',
  'credential',
  'credentials',
  'accesskey',
  'access_key',
  'privatekey',
  'private_key',
]);

/** Deep-clone `value`, dropping any object field whose name looks like a secret. */
export function redactSecrets<T>(value: T): T {
  if (Array.isArray(value)) {
    return value.map((v) => redactSecrets(v)) as unknown as T;
  }
  if (value && typeof value === 'object') {
    const out: Record<string, unknown> = {};
    for (const [k, v] of Object.entries(value as Record<string, unknown>)) {
      if (SECRET_FIELDS.has(k.toLowerCase())) continue; // strip, never serialize
      out[k] = redactSecrets(v);
    }
    return out as T;
  }
  return value;
}

/** The JSON-serializable, secret-free export payload for the current settings. */
export function exportPayload(): Settings {
  return redactSecrets(settingsStore.snapshot());
}

/**
 * Validate an arbitrary parsed object as importable settings. Throws on a shape
 * we won't accept; otherwise returns a clean tree merged over DEFAULTS (unknown
 * keys dropped, missing keys defaulted).
 */
export function validateImport(raw: unknown): Settings {
  if (!raw || typeof raw !== 'object' || Array.isArray(raw)) {
    throw new Error('Settings file is not a valid object.');
  }
  const version = (raw as { version?: unknown }).version;
  if (version !== 1) {
    throw new Error(`Unsupported settings version: ${String(version)} (expected 1).`);
  }
  return mergeSettings(raw);
}

// ─── orchestrators (dialog + IO; degrade in dev) ────────────────────────────

const EXPORT_NAME = 'orrery-settings.json';

/** Export the current settings to a file chosen by the user. Returns true on write. */
export async function exportSettings(): Promise<boolean> {
  const payload = exportPayload();
  const json = JSON.stringify(payload, null, 2);

  if (hasTauri()) {
    try {
      const { save } = await import('@tauri-apps/plugin-dialog');
      const path = await save({
        defaultPath: EXPORT_NAME,
        filters: [{ name: 'JSON', extensions: ['json'] }],
      });
      if (!path) return false; // user cancelled
      const { invoke } = await import('@tauri-apps/api/core');
      await invoke(CMD.exportSettings, { path, contents: json });
      return true;
    } catch {
      return false;
    }
  }

  // dev / browser: Blob download.
  if (typeof document === 'undefined') return false;
  try {
    const blob = new Blob([json], { type: 'application/json' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = EXPORT_NAME;
    a.click();
    URL.revokeObjectURL(url);
    return true;
  } catch {
    return false;
  }
}

/**
 * Import settings from a user-chosen file. Validates + applies through the store.
 * Returns the applied `Settings` on success, or `null` if cancelled / failed.
 */
export async function importSettings(): Promise<Settings | null> {
  if (hasTauri()) {
    try {
      const { open } = await import('@tauri-apps/plugin-dialog');
      const path = await open({
        multiple: false,
        directory: false,
        filters: [{ name: 'JSON', extensions: ['json'] }],
      });
      if (!path || typeof path !== 'string') return null; // cancelled
      const { invoke } = await import('@tauri-apps/api/core');
      // Rust reads + parses the chosen file and returns the raw object; we still
      // validate + merge locally so the store is the single arbiter of shape.
      const raw = await invoke<unknown>(CMD.importSettings, { path });
      const next = validateImport(raw);
      settingsStore.replaceAll(next);
      return next;
    } catch {
      return null;
    }
  }

  // dev / browser: <input type=file> + FileReader.
  const raw = await pickJsonFileInBrowser();
  if (raw === null) return null;
  const next = validateImport(raw);
  settingsStore.replaceAll(next);
  return next;
}

/** Dev-only file picker: resolves the parsed JSON, or null if cancelled / unreadable. */
function pickJsonFileInBrowser(): Promise<unknown | null> {
  if (typeof document === 'undefined') return Promise.resolve(null);
  return new Promise((resolve) => {
    const input = document.createElement('input');
    input.type = 'file';
    input.accept = 'application/json,.json';
    input.onchange = () => {
      const file = input.files?.[0];
      if (!file) return resolve(null);
      const reader = new FileReader();
      reader.onload = () => {
        try {
          resolve(JSON.parse(String(reader.result)));
        } catch {
          resolve(null);
        }
      };
      reader.onerror = () => resolve(null);
      reader.readAsText(file);
    };
    input.click();
  });
}
