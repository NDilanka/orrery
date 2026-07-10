// Settings store (WS-A) — the reactive singleton implementing `SettingsStoreApi`.
//
// Instant-apply, no global "Save": every `set()` mutates the live tree, runs any
// side-effect (theme → data-theme), and persists through the injected backend.
// A Tauri file-watcher hot-reloads the tree when settings.json is edited out of
// band, with a short self-write debounce so our own writes don't echo back.
//
// Follows the house `class XStore { field = $state(...) } export const xStore`
// pattern (ui/cosmos stores), and their graceful "no Tauri → degrade, never
// throw" idiom: the store works fully in `vite dev` on a localStorage backend.

import { browser } from '$app/environment';
import {
  DEFAULTS,
  defaultFor,
  providerRule,
  type Settings,
  type ProviderInstance,
} from '../settings/schema';
import type { SettingsStoreApi, ByokAuthStatus } from '../settings/contract';
import { CMD } from '../settings/contract';
import { makeBackend, hasTauri, type SettingsBackend } from '../settings/backend';
import { accountFor, keychainDelete } from '../settings/keychain';

/** How long after our own write to ignore watcher echoes (ms). */
const SELF_WRITE_DEBOUNCE_MS = 400;

// ─── pure helpers (exported for settingsIo reuse) ───────────────────────────

function isPlainObject(v: unknown): v is Record<string, unknown> {
  return typeof v === 'object' && v !== null && !Array.isArray(v);
}

/**
 * Deep-merge a (possibly partial / stale) persisted tree OVER DEFAULTS so that
 * missing or newly-added keys always fall back to their default. Arrays and
 * scalars from the source REPLACE the default wholesale (never element-merged).
 * Unknown keys not present in DEFAULTS are DROPPED, and a value whose runtime
 * type disagrees with its default's is dropped too — settings.json is openly
 * hand-editable (openSettingsFile + the hot-reload watcher), and a stray
 * `"appearance": "dark"` must not replace a whole section for every consumer.
 * `version` is forced to 1.
 */
export function mergeSettings(persisted: unknown): Settings {
  const out = structuredClone(DEFAULTS);
  const merge = (target: Record<string, unknown>, src: Record<string, unknown>) => {
    for (const key of Object.keys(target)) {
      if (!(key in src)) continue;
      const t = target[key];
      const s = src[key];
      if (isPlainObject(t)) {
        if (isPlainObject(s)) merge(t, s);
      } else if (Array.isArray(t)) {
        if (Array.isArray(s)) target[key] = s;
      } else if (t === null) {
        // the null defaults are all nullable-string fields (loopsDir, instance pointers)
        if (s === null || typeof s === 'string') target[key] = s;
      } else if (typeof s === typeof t) {
        target[key] = s;
      }
    }
  };
  if (isPlainObject(persisted)) merge(out as unknown as Record<string, unknown>, persisted);
  out.version = 1;
  return out;
}

/** Structural equality by canonical JSON — sufficient for settings scalars/arrays/objects. */
export function settingsEqual(a: unknown, b: unknown): boolean {
  return JSON.stringify(a) === JSON.stringify(b);
}

// ─── the store ──────────────────────────────────────────────────────────────

class SettingsStore implements SettingsStoreApi {
  data = $state<Settings>(structuredClone(DEFAULTS));
  loaded = $state(false);
  scope = $state<'user' | 'workspace'>('user');
  keychain = $state<Record<string, 'set' | 'unset'>>({});
  authStatus = $state<Record<string, ByokAuthStatus>>({});

  // reactive mirrors of the OS media queries (matchMedia.matches is NOT reactive
  // on its own, so we mirror it into $state and update it from the change events).
  private schemeDark = $state(false);
  private prefersReduced = $state(false);

  private backend: SettingsBackend | null = null;
  private schemeMql: MediaQueryList | null = null;
  private motionMql: MediaQueryList | null = null;
  /** ignore watcher echoes until this timestamp (self-write debounce). */
  private selfWriteUntil = 0;

  // ── lifecycle ──────────────────────────────────────────────────────────────

  /**
   * Load persistence, wire the theme/motion media listeners, start the Tauri
   * hot-reload watcher, and apply the initial theme. Returns a teardown fn.
   * `injected` lets tests supply an in-memory backend (and skips Tauri wiring,
   * which the environment guards also skip in a non-browser test runner).
   */
  async init(injected?: SettingsBackend): Promise<() => void> {
    this.backend = injected ?? makeBackend();

    const teardowns: Array<() => void> = [];

    // Wire the OS media mirrors BEFORE the async backend load: consumers like the Pixi
    // canvases read `reducedMotion` from the first frame, and an OS reduced-motion user
    // must not get full animation while the (possibly slow) store load is in flight.
    if (browser && typeof window !== 'undefined' && typeof window.matchMedia === 'function') {
      this.schemeMql = window.matchMedia('(prefers-color-scheme: dark)');
      this.schemeDark = this.schemeMql.matches;
      const onScheme = () => {
        this.schemeDark = this.schemeMql?.matches ?? false;
        this.applyTheme();
      };
      this.schemeMql.addEventListener?.('change', onScheme);
      teardowns.push(() => this.schemeMql?.removeEventListener?.('change', onScheme));

      this.motionMql = window.matchMedia('(prefers-reduced-motion: reduce)');
      this.prefersReduced = this.motionMql.matches;
      const onMotion = () => (this.prefersReduced = this.motionMql?.matches ?? false);
      this.motionMql.addEventListener?.('change', onMotion);
      teardowns.push(() => this.motionMql?.removeEventListener?.('change', onMotion));
    }

    try {
      const persisted = await this.backend.load();
      if (persisted) this.data = mergeSettings(persisted);
    } catch {
      /* first run / unreadable → keep DEFAULTS */
    }
    this.loaded = true;

    // hot-reload: react to external edits of settings.json (Tauri only).
    if (browser && hasTauri()) {
      try {
        const { invoke, Channel } = await import('@tauri-apps/api/core');
        const channel = new Channel<unknown>();
        channel.onmessage = () => void this.onExternalChange();
        await invoke(CMD.watchSettings, { channel });
        teardowns.push(() => {
          channel.onmessage = () => {};
          void invoke(CMD.unwatchSettings, {}).catch(() => {});
        });
      } catch {
        /* watcher unavailable — settings still work, just no live external reload */
      }
    }

    this.applyTheme();
    this.applyMotion();
    this.applyDensity();
    return () => {
      for (const t of teardowns) t();
    };
  }

  /** Re-read from the backend when the file changed underneath us (debounced). */
  private async onExternalChange(): Promise<void> {
    if (Date.now() < this.selfWriteUntil) return; // our own write echoing back
    if (!this.backend) return;
    try {
      const fresh = await this.backend.reload();
      if (fresh) {
        this.data = mergeSettings(fresh);
        this.applyTheme();
        this.applyMotion();
        this.applyDensity();
      }
    } catch {
      /* transient read error — keep the current tree */
    }
  }

  // ── read / write ─────────────────────────────────────────────────────────

  get<T>(path: string): T {
    return path.split('.').reduce<unknown>((acc, part) => {
      if (acc && typeof acc === 'object' && part in (acc as Record<string, unknown>)) {
        return (acc as Record<string, unknown>)[part];
      }
      return undefined;
    }, this.data as unknown) as T;
  }

  async set(path: string, value: unknown): Promise<void> {
    this.writePath(path, value);
    this.applySideEffects(path);
    await this.persist();
  }

  /** Write into `data` by dotted path, reassigning the top-level branch so the runes tree reacts. */
  private writePath(path: string, value: unknown): void {
    const parts = path.split('.');
    const head = parts[0];
    const rest = parts.slice(1);
    const record = this.data as unknown as Record<string, unknown>;
    if (rest.length === 0) {
      record[head] = value;
      return;
    }
    // $state.snapshot() unwraps the reactive proxy first — structuredClone throws on a
    // raw $state proxy ("could not be cloned"); the plain snapshot is safe to clone + mutate.
    const branch = structuredClone($state.snapshot(record[head])) as Record<string, unknown>;
    let cursor = branch;
    for (let i = 0; i < rest.length - 1; i++) {
      cursor = cursor[rest[i]] as Record<string, unknown>;
    }
    cursor[rest[rest.length - 1]] = value;
    record[head] = branch;
  }

  private applySideEffects(path: string): void {
    // live DOM side-effects; a section reset ('appearance') re-applies all three.
    if (path === 'appearance.theme' || path === 'appearance') this.applyTheme();
    if (path === 'appearance.motion' || path === 'appearance') this.applyMotion();
    if (path === 'appearance.density' || path === 'appearance') this.applyDensity();
  }

  private applyTheme(): void {
    if (typeof document !== 'undefined') {
      document.documentElement.dataset.theme = this.resolvedTheme;
    }
  }

  /**
   * Stamp data-motion on <html> so a user 'full' choice can override the OS
   * prefers-reduced-motion (tokens.css scopes the OS freeze to
   * :root:not([data-motion='full'])) and a 'reduced' choice freezes motion
   * unconditionally. 'system' leaves the attribute off so only the OS pref speaks.
   */
  private applyMotion(): void {
    if (typeof document === 'undefined') return;
    const m = this.data.appearance.motion;
    if (m === 'full' || m === 'reduced') {
      document.documentElement.dataset.motion = m;
    } else {
      delete document.documentElement.dataset.motion;
    }
  }

  /**
   * Stamp data-density on <html> so tokens.css can tighten chrome spacing under
   * [data-density='compact']. 'comfortable' (the default) leaves the attribute off.
   */
  private applyDensity(): void {
    if (typeof document === 'undefined') return;
    if (this.data.appearance.density === 'compact') {
      document.documentElement.dataset.density = 'compact';
    } else {
      delete document.documentElement.dataset.density;
    }
  }

  private async persist(): Promise<void> {
    if (!this.backend) return;
    this.selfWriteUntil = Date.now() + SELF_WRITE_DEBOUNCE_MS;
    try {
      await this.backend.save(this.snapshot());
    } catch {
      /* persistence failure is non-fatal to the in-memory session */
    }
  }

  /** A plain (proxy-free) deep clone of the live tree — safe to serialize / persist. */
  snapshot(): Settings {
    return structuredClone($state.snapshot(this.data)) as Settings;
  }

  // ── changed / reset ────────────────────────────────────────────────────────

  isChanged(path: string): boolean {
    return !settingsEqual(this.get(path), defaultFor(path));
  }

  resetKey(path: string): void {
    void this.set(path, structuredClone(defaultFor(path)));
  }

  resetSection(cat: keyof Settings): void {
    void this.set(cat as string, structuredClone(DEFAULTS[cat]));
  }

  resetAll(): void {
    for (const cat of Object.keys(DEFAULTS) as Array<keyof Settings>) {
      if (cat === 'version') continue;
      this.resetSection(cat);
    }
  }

  /** Replace the entire tree (import path): merge over DEFAULTS, re-apply, persist. */
  replaceAll(next: unknown): void {
    this.data = mergeSettings(next);
    this.applyTheme();
    this.applyMotion();
    this.applyDensity();
    void this.persist();
  }

  // ── resolved getters ───────────────────────────────────────────────────────

  get resolvedTheme(): 'light' | 'dark' {
    const t = this.data.appearance.theme;
    return t === 'system' ? (this.schemeDark ? 'dark' : 'light') : t;
  }

  get reducedMotion(): boolean {
    const m = this.data.appearance.motion;
    return m === 'system' ? this.prefersReduced : m === 'reduced';
  }

  // ── BYOK provider instances ────────────────────────────────────────────────

  async addInstance(instance: Omit<ProviderInstance, 'id'>): Promise<string> {
    const id = crypto.randomUUID();
    const next = [...this.data.ai.instances, { ...instance, id }];
    await this.set('ai.instances', next);
    return id;
  }

  async updateInstance(id: string, patch: Partial<ProviderInstance>): Promise<void> {
    const next = this.data.ai.instances.map((i) =>
      i.id === id ? { ...i, ...patch, id } : i,
    );
    await this.set('ai.instances', next);
  }

  async removeInstance(id: string): Promise<void> {
    const inst = this.data.ai.instances.find((i) => i.id === id);
    const next = this.data.ai.instances.filter((i) => i.id !== id);
    await this.set('ai.instances', next);
    // orphaned default/fallback pointers must not dangle
    if (this.data.ai.defaultInstanceId === id) await this.set('ai.defaultInstanceId', null);
    if (this.data.ai.fallbackInstanceId === id) await this.set('ai.fallbackInstanceId', null);
    // drop its keychain secret + presence mirror — but ONLY when no surviving instance
    // shares the account (accounts are deliberately shared per provider/mode, e.g.
    // anthropic claude+aider both live under orrery/anthropic/api-key; deleting one
    // instance must not destroy the sibling's credential).
    if (inst) {
      const account = accountFor(inst);
      const stillUsed = account !== null && next.some((i) => accountFor(i) === account);
      if (account && !stillUsed) {
        await keychainDelete(account);
        const mirror = { ...this.keychain };
        delete mirror[account];
        this.keychain = mirror;
      }
      const status = { ...this.authStatus };
      delete status[id];
      this.authStatus = status;
    }
  }
}

export const settingsStore = new SettingsStore();
export type { SettingsStore };
