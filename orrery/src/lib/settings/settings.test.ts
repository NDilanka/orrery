// Unit tests for the WS-A settings layer: fuzzy search over the registry,
// import validation + secret redaction, and the store's set / isChanged /
// resetKey against an injected in-memory backend (no Tauri, no DOM).

import { describe, it, expect, beforeEach, vi } from 'vitest';

// The store imports `browser` from SvelteKit's virtual module; in the node test
// runner it must be mocked (and browser=false keeps init() off the DOM path).
vi.mock('$app/environment', () => ({ browser: false }));

import { REGISTRY, DEFAULTS, defaultFor, type Settings } from './schema';
import type { SettingsBackend } from './backend';
import { searchSettings } from './search';
import { redactSecrets, validateImport } from './settingsIo';
import { settingsStore } from '../stores/settings.svelte';

// ── search ───────────────────────────────────────────────────────────────────

describe('searchSettings', () => {
  it('empty query returns every registry key', () => {
    const { keys, byCategory } = searchSettings('');
    expect(keys.size).toBe(REGISTRY.length);
    const summed = Object.values(byCategory).reduce((n, c) => n + c, 0);
    expect(summed).toBe(keys.size);
  });

  it('substring hits the expected key', () => {
    expect(searchSettings('ceiling').keys.has('loopDefaults.ceilingUsd')).toBe(true);
    expect(searchSettings('theme').keys.has('appearance.theme')).toBe(true);
    expect(searchSettings('keychain').keys.has('ai')).toBe(true);
  });

  it('gibberish matches nothing', () => {
    expect(searchSettings('zzzxqqwj').keys.size).toBe(0);
  });
});

// ── import validation + redaction ─────────────────────────────────────────────

describe('redactSecrets', () => {
  it('strips secret-shaped fields but keeps the hasSecret mirror + normal fields', () => {
    const clean = redactSecrets({
      version: 1,
      ai: { instances: [{ id: 'x', name: 'k', apiKey: 'sk-live', token: 'tok', hasSecret: true }] },
      nested: { secret: 's', password: 'p', keep: 42 },
    }) as Record<string, any>;

    const inst = clean.ai.instances[0];
    expect(inst.apiKey).toBeUndefined();
    expect(inst.token).toBeUndefined();
    expect(inst.hasSecret).toBe(true); // presence mirror is not a secret
    expect(inst.id).toBe('x');
    expect(clean.nested.secret).toBeUndefined();
    expect(clean.nested.password).toBeUndefined();
    expect(clean.nested.keep).toBe(42);
  });
});

describe('validateImport', () => {
  it('rejects a non-object / wrong version', () => {
    expect(() => validateImport(null)).toThrow();
    expect(() => validateImport([])).toThrow();
    expect(() => validateImport({ version: 2 })).toThrow(/version/i);
  });

  it('merges a partial tree over DEFAULTS and drops unknown keys', () => {
    const merged = validateImport({
      version: 1,
      general: { lanPort: 9999 },
      bogusTopLevel: 7,
    }) as Settings & Record<string, unknown>;

    expect(merged.general.lanPort).toBe(9999); // provided value wins
    expect(merged.general.cosmosPollMs).toBe(DEFAULTS.general.cosmosPollMs); // defaulted
    expect(merged.appearance.theme).toBe(DEFAULTS.appearance.theme); // whole section defaulted
    expect((merged as Record<string, unknown>).bogusTopLevel).toBeUndefined(); // unknown dropped
  });
});

// ── store: set / isChanged / resetKey on an injected backend ───────────────────

class FakeBackend implements SettingsBackend {
  stored: Settings;
  lastSaved: Settings | null = null;
  constructor(initial: Settings) {
    this.stored = structuredClone(initial);
  }
  async load(): Promise<Settings | null> {
    return structuredClone(this.stored);
  }
  async reload(): Promise<Settings | null> {
    return structuredClone(this.stored);
  }
  async save(settings: Settings): Promise<void> {
    this.stored = structuredClone(settings);
    this.lastSaved = structuredClone(settings);
  }
}

describe('settingsStore', () => {
  let backend: FakeBackend;

  beforeEach(async () => {
    backend = new FakeBackend(DEFAULTS);
    await settingsStore.init(backend); // browser=false → no DOM / watcher wiring
  });

  it('set writes the reactive tree and persists through the backend', async () => {
    await settingsStore.set('general.lanPort', 9000);
    expect(settingsStore.get<number>('general.lanPort')).toBe(9000);
    expect(backend.lastSaved?.general.lanPort).toBe(9000);
  });

  it('isChanged compares against the default', async () => {
    expect(settingsStore.isChanged('general.lanPort')).toBe(false);
    await settingsStore.set('general.lanPort', 9000);
    expect(settingsStore.isChanged('general.lanPort')).toBe(true);
    expect(settingsStore.isChanged('general.confirmDestructive')).toBe(false);
  });

  it('resetKey restores the default value', async () => {
    await settingsStore.set('general.lanPort', 9000);
    settingsStore.resetKey('general.lanPort');
    expect(settingsStore.get<number>('general.lanPort')).toBe(defaultFor('general.lanPort'));
    expect(settingsStore.isChanged('general.lanPort')).toBe(false);
  });

  it('resetSection restores a whole category', async () => {
    await settingsStore.set('appearance.theme', 'light');
    await settingsStore.set('appearance.grain', false);
    settingsStore.resetSection('appearance');
    expect(settingsStore.get('appearance.theme')).toBe(DEFAULTS.appearance.theme);
    expect(settingsStore.get('appearance.grain')).toBe(DEFAULTS.appearance.grain);
  });

  it('addInstance generates an id and persists the instance', async () => {
    const id = await settingsStore.addInstance({
      name: 'My key',
      runner: 'claude',
      provider: 'anthropic',
      mode: 'apiKey',
    });
    expect(id).toBeTruthy();
    const inst = settingsStore.data.ai.instances.find((i) => i.id === id);
    expect(inst?.name).toBe('My key');
    expect(backend.lastSaved?.ai.instances.some((i) => i.id === id)).toBe(true);
  });

  it('resolvedTheme resolves an explicit theme without a media query', () => {
    void settingsStore.set('appearance.theme', 'light');
    expect(settingsStore.resolvedTheme).toBe('light');
  });
});
