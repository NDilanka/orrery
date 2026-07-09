<script lang="ts">
  // AboutDiagnosticsPanel — composes the generic diagnostics scalar rows (logLevel, telemetry)
  // with a read-only About block: app version, resolved config-file + loops-folder paths, an
  // "Open" affordance for settings.json, and a "Copy diagnostics" button that copies a small,
  // SECRET-FREE JSON blob to the clipboard. Backend-only data comes from Tauri commands; in
  // `vite dev` (no Tauri) it shows fallbacks and disables the backend-only buttons. Chrome =
  // monochrome only (design law M5): --em-*, --surface-*, --hairline, shared .btn primitive.
  import GenericPanel from './GenericPanel.svelte';
  import { settingsStore } from '../../stores/settings.svelte';
  import { CMD } from '../../settings/contract';
  import { hasTauri } from '../../settings/backend';

  let { query = '', matched }: { query?: string; matched?: Set<string> } = $props();

  const store = settingsStore;
  const tauri = hasTauri();
  const FALLBACK_VERSION = '0.3.0';

  let version = $state(FALLBACK_VERSION);
  let configPath = $state<string | null>(null);
  let loopsDir = $state<string | null>(null);
  let copied = $state(false);

  let loadedOnce = false;
  $effect(() => {
    void load();
  });

  async function load() {
    if (loadedOnce || !tauri) return;
    loadedOnce = true;
    try {
      const { getVersion } = await import('@tauri-apps/api/app');
      version = await getVersion();
    } catch {
      /* keep the fallback version */
    }
    try {
      const { invoke } = await import('@tauri-apps/api/core');
      configPath = await invoke<string>(CMD.settingsConfigPath);
    } catch {
      /* path unresolved — leave null */
    }
    try {
      const { invoke } = await import('@tauri-apps/api/core');
      loopsDir = await invoke<string>(CMD.resolveLoopsDir);
    } catch {
      /* path unresolved — leave null */
    }
  }

  async function openConfig() {
    if (!tauri) return;
    try {
      const { invoke } = await import('@tauri-apps/api/core');
      await invoke(CMD.openSettingsFile);
    } catch {
      /* editor unavailable — no-op */
    }
  }

  async function copyDiagnostics() {
    // The settings tree is secret-free by construction — secrets live only in the OS keychain,
    // never in settings.json — so snapshot() carries no key/token, only versions/paths/prefs.
    const diag = {
      app: 'Orrery',
      version,
      tauri,
      configPath: configPath ?? '(dev — not resolved)',
      loopsDir: loopsDir ?? '(dev — not resolved)',
      userAgent: typeof navigator !== 'undefined' ? navigator.userAgent : '',
      settings: store.snapshot(),
    };
    try {
      await navigator.clipboard.writeText(JSON.stringify(diag, null, 2));
      copied = true;
      setTimeout(() => (copied = false), 1600);
    } catch {
      /* clipboard write denied — nothing to surface */
    }
  }
</script>

<section class="about">
  <GenericPanel category="diagnostics" {query} {matched} />

  <div class="block">
    <div class="panel-hd"><span>About</span></div>

    <dl class="rows">
      <div class="drow">
        <dt>Version</dt>
        <dd>
          <span class="val tabular">{version}</span>
          {#if !tauri}<span class="muted">dev</span>{/if}
        </dd>
      </div>

      <div class="drow">
        <dt>Config file</dt>
        <dd class="pathdd">
          <span class="path mono">{configPath ?? 'Available in the desktop app'}</span>
          <button
            type="button"
            class="btn btn-ghost btn-sm"
            disabled={!tauri}
            title={tauri ? 'Open settings.json in your editor' : 'Only available in the desktop app'}
            onclick={openConfig}
          >
            Open
          </button>
        </dd>
      </div>

      <div class="drow">
        <dt>Loops folder</dt>
        <dd class="pathdd">
          <span class="path mono">{loopsDir ?? 'Available in the desktop app'}</span>
        </dd>
      </div>
    </dl>

    <div class="aboutbtns">
      <button type="button" class="btn btn-ghost btn-sm" onclick={copyDiagnostics}>
        {copied ? 'Copied ✓' : 'Copy diagnostics'}
      </button>
      <span class="note">Versions, paths, and preferences only — never your keys.</span>
    </div>
  </div>
</section>

<style>
  .about {
    display: flex;
    flex-direction: column;
    gap: var(--space-4);
    min-width: 0;
  }
  .block {
    min-width: 0;
  }
  .rows {
    margin: 0;
    display: flex;
    flex-direction: column;
  }
  .drow {
    display: flex;
    align-items: flex-start;
    justify-content: space-between;
    gap: var(--space-4);
    padding: var(--space-3) 0;
    border-bottom: 1px solid var(--hairline);
  }
  .drow:last-child {
    border-bottom: none;
  }
  dt {
    flex: none;
    font-size: var(--text-sm);
    color: var(--em-mid);
  }
  dd {
    margin: 0;
    min-width: 0;
    display: flex;
    align-items: center;
    gap: var(--space-3);
    justify-content: flex-end;
    text-align: right;
  }
  .pathdd {
    flex: 1;
  }
  .val {
    font-size: var(--text-sm);
    color: var(--em-hi);
  }
  .path {
    min-width: 0;
    font-size: var(--text-xs);
    color: var(--em-low);
    word-break: break-all;
  }
  .muted {
    font-size: var(--text-2xs);
    letter-spacing: 0.06em;
    text-transform: uppercase;
    color: var(--em-faint);
    border: 1px solid var(--hairline);
    border-radius: var(--radius-pill);
    padding: 1px 6px;
  }
  .aboutbtns {
    display: flex;
    align-items: center;
    gap: var(--space-3);
    margin-top: var(--space-3);
  }
  .note {
    font-size: var(--text-xs);
    color: var(--em-faint);
  }
</style>
