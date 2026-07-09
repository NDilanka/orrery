<script module lang="ts">
  import type { Settings } from '../../settings/schema';
  // The six real settings categories (every `keyof Settings` except the bookkeeping `version`).
  type Category = Exclude<keyof Settings, 'version'>;
  // Last-viewed category persists across opens within a session (module-level, not per-mount).
  let lastActive: Category = 'general';
</script>

<script lang="ts">
  // SettingsOverlay — the Settings modal shell. Mirrors the app's modal contract (HelpOverlay /
  // CommandPalette): a --scrim backdrop (click-outside closes), a .floating-card dialog with a
  // focus trap (Esc closes; initial focus lands in the search box). Chrome = monochrome only.
  //
  // Header:  title · ScopeSwitcher · search · close.
  // Body:    SettingsNav (left) + the active category panel (right).
  // Footer:  Edit config file · Import… · Export… · Reset all…
  //
  // Search flows overlay → nav → row: searchSettings(query) yields per-category counts (dim
  // empty nav categories) + the matched key set (rows filter + highlight). A sole matching
  // category auto-selects.
  import { focusTrap } from '../../actions/focusTrap';
  import { settingsStore } from '../../stores/settings.svelte';
  import { searchSettings } from '../../settings/search';
  import { exportSettings, importSettings } from '../../settings/settingsIo';
  import { CMD } from '../../settings/contract';
  import { hasTauri } from '../../settings/backend';
  import SettingsNav from './SettingsNav.svelte';
  import ScopeSwitcher from './ScopeSwitcher.svelte';
  import GeneralPanel from './GeneralPanel.svelte';
  import AppearancePanel from './AppearancePanel.svelte';
  import LoopDefaultsPanel from './LoopDefaultsPanel.svelte';
  import NotificationsPanel from './NotificationsPanel.svelte';
  import AiByokPanel from './AiByokPanel.svelte';
  import AboutDiagnosticsPanel from './AboutDiagnosticsPanel.svelte';

  let { onClose }: { onClose: () => void } = $props();

  const tauri = hasTauri();

  let active = $state<Category>(lastActive);
  let query = $state('');
  let inputEl = $state<HTMLInputElement | null>(null);

  // remember the last-viewed category for the next open.
  $effect(() => {
    lastActive = active;
  });

  const PANELS = {
    general: GeneralPanel,
    appearance: AppearancePanel,
    loopDefaults: LoopDefaultsPanel,
    notifications: NotificationsPanel,
    ai: AiByokPanel,
    diagnostics: AboutDiagnosticsPanel,
  } as const;

  const searchResult = $derived(query.trim() ? searchSettings(query) : null);
  const counts = $derived(searchResult?.byCategory);
  const matched = $derived(searchResult?.keys);

  // auto-select the sole matching category while searching (never fights a later manual click:
  // this only re-runs when the search RESULT changes, not when `active` does).
  $effect(() => {
    if (!searchResult) return;
    const cats = Object.keys(searchResult.byCategory);
    if (cats.length === 1) active = cats[0] as Category;
  });

  const ActivePanel = $derived(PANELS[active]);

  async function editConfig() {
    if (!tauri) return;
    try {
      const { invoke } = await import('@tauri-apps/api/core');
      await invoke(CMD.openSettingsFile);
    } catch {
      /* editor unavailable — no-op */
    }
  }
  async function doImport() {
    await importSettings();
  }
  async function doExport() {
    await exportSettings();
  }
  function resetAll() {
    if (
      typeof window === 'undefined' ||
      window.confirm('Reset ALL settings to their defaults? This cannot be undone.')
    ) {
      settingsStore.resetAll();
    }
  }
</script>

<div
  class="scrim"
  role="presentation"
  onclick={(e) => {
    if (e.target === e.currentTarget) onClose();
  }}
>
  <div
    class="card floating-card"
    role="dialog"
    aria-modal="true"
    aria-labelledby="settings-title"
    tabindex="-1"
    use:focusTrap={{ onClose, initialFocus: () => inputEl }}
  >
    <header class="hdr">
      <span class="dot" aria-hidden="true"></span>
      <span id="settings-title" class="title mono">SETTINGS</span>
      <ScopeSwitcher />
      <div class="searchbox">
        <span class="sicon mono" aria-hidden="true">⌕</span>
        <input
          bind:this={inputEl}
          bind:value={query}
          class="search"
          type="text"
          aria-label="Search settings"
          placeholder="Search settings…"
        />
      </div>
      <button class="x" aria-label="close settings" onclick={onClose}>✕</button>
    </header>

    <div class="body">
      <SettingsNav {active} onSelect={(c) => (active = c as Category)} {counts} />
      <div class="panelhost">
        <ActivePanel {query} {matched} />
      </div>
    </div>

    <footer class="ftr">
      {#if tauri}
        <button type="button" class="btn btn-ghost btn-sm" onclick={editConfig}>
          Edit config file
        </button>
      {/if}
      <button type="button" class="btn btn-ghost btn-sm" onclick={doImport}>Import…</button>
      <button type="button" class="btn btn-ghost btn-sm" onclick={doExport}>Export…</button>
      <span class="spacer"></span>
      <button type="button" class="btn btn-danger btn-sm" onclick={resetAll}>Reset all…</button>
    </footer>
  </div>
</div>

<style>
  .scrim {
    position: absolute;
    inset: 0;
    display: flex;
    align-items: center;
    justify-content: center;
    background: var(--scrim);
    backdrop-filter: blur(4px);
    z-index: var(--z-modal);
    padding: var(--chrome-inset);
  }
  .card {
    width: min(880px, 95vw);
    height: min(620px, 88vh);
    display: flex;
    flex-direction: column;
    gap: var(--space-4);
    padding: var(--space-4) var(--space-5);
  }

  /* ── header ── */
  .hdr {
    display: flex;
    align-items: center;
    gap: var(--space-3);
    border-bottom: 1px solid var(--hairline);
    padding-bottom: var(--space-3);
  }
  .dot {
    flex: none;
    width: 7px;
    height: 7px;
    border-radius: 50%;
    background: var(--em-hi);
    box-shadow: 0 0 6px color-mix(in srgb, var(--em-hi) 70%, transparent);
  }
  .title {
    flex: none;
    font-size: var(--text-md);
    letter-spacing: 0.2em;
    color: var(--em-hi);
  }
  .searchbox {
    flex: 1;
    min-width: 0;
    display: flex;
    align-items: center;
    gap: var(--space-2);
    padding: 5px var(--space-3);
    border: 1px solid var(--hairline);
    border-radius: var(--radius-sm);
    background: var(--surface-void);
    transition: border-color var(--dur-feedback) var(--ease-standard);
  }
  .searchbox:focus-within {
    border-color: color-mix(in srgb, var(--em-mid) 50%, transparent);
  }
  .sicon {
    flex: none;
    font-size: var(--text-sm);
    color: var(--em-faint);
  }
  .search {
    flex: 1;
    min-width: 0;
    background: transparent;
    border: none;
    outline: none;
    font-family: var(--font-grotesk);
    font-size: var(--text-sm);
    color: var(--em-hi);
  }
  .search::placeholder {
    color: var(--em-faint);
  }
  .x {
    flex: none;
    width: 24px;
    height: 24px;
    background: transparent;
    border: 1px solid var(--hairline);
    border-radius: var(--radius-pill);
    color: var(--em-mid);
    cursor: pointer;
    font-size: var(--text-xs);
    transition:
      border-color var(--dur-feedback) var(--ease-standard),
      color var(--dur-feedback) var(--ease-standard);
  }
  .x:hover {
    border-color: var(--em-hi);
    color: var(--em-hi);
  }

  /* ── body: nav rail + active panel ── */
  .body {
    flex: 1;
    min-height: 0;
    display: grid;
    grid-template-columns: 208px 1fr;
    gap: var(--space-4);
  }
  .panelhost {
    min-width: 0;
    overflow-y: auto;
    padding-right: 2px;
  }

  /* ── footer ── */
  .ftr {
    display: flex;
    align-items: center;
    gap: var(--space-2);
    border-top: 1px solid var(--hairline);
    padding-top: var(--space-3);
  }
  .spacer {
    flex: 1;
  }

  @media (max-width: 640px) {
    .body {
      grid-template-columns: 1fr;
    }
  }
</style>
