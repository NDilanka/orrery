<script lang="ts">
  // CommandPalette (M3.1) — Ctrl/Cmd+K. The one *new* chrome element the modernization plan
  // adds: a searchable launcher over every action the app already exposes elsewhere (Run
  // control, mode switches, altitude nav, Help/Share/New-loop, jump-to-loop). PURE dispatch —
  // every row calls an existing store method or a page-local nav closure passed in as a prop;
  // no new state, no protocol/reducer changes (plan §0 "visual-only... anything that adds UI
  // state must thread through both reducers" — this adds none).
  //
  // Gating mirrors the source component for each action (RunControlBar's running/banked/failed/
  // stopPending logic, the navbar's "fly into body" sel check, ShareButton/ignite-fab's
  // cosmos-only visibility, ModeBar's canRewind disable) so a row never offers something the
  // real UI wouldn't. +page.svelte owns whether this is mounted at all (mirrors HelpOverlay);
  // the keybinding there already refuses to open this while another modal is up.
  //
  // Accessibility: a combobox/listbox pattern (aria-activedescendant) — DOM focus never leaves
  // the filter input, so Arrow/Enter/Escape/Tab all resolve on one element and the shared
  // `focusTrap` action (Escape → onClose, Tab trapped) just works without any double-handling
  // against +page.svelte's own window-level Escape branch.

  import { focusTrap } from '../actions/focusTrap';
  import { runStore } from '../stores/run.svelte';
  import { sessionStore } from '../stores/session.svelte';
  import { uiStore } from '../stores/ui.svelte';
  import { cosmosStore } from '../stores/cosmos.svelte';
  import { shareStore } from '../stores/share.svelte';
  import { hasWsServer } from '../transport';

  type View = 'cosmos' | 'system' | 'body';

  let {
    onClose,
    view,
    activeLoop,
    onEnterSystem,
    onEnterBody,
    onBackToSystem,
    onBackToCosmos,
    onOpenHelp,
    onOpenSettings,
    kbdChip = '⌘K',
  }: {
    onClose: () => void;
    view: View;
    activeLoop: string | null;
    onEnterSystem: (id: string) => void;
    onEnterBody: (key: string | null) => void;
    onBackToSystem: () => void;
    onBackToCosmos: () => void;
    onOpenHelp: () => void;
    onOpenSettings: () => void;
    /** platform-aware chip text for the palette's own trigger ("⌘K" mac / "Ctrl K" else) —
     * computed once by +page.svelte (the only place `navigator` is read) and threaded
     * through so this component needs no platform-detection of its own. */
    kbdChip?: string;
  } = $props();

  type Group = 'actions' | 'navigate' | 'loops';
  interface Row {
    id: string;
    label: string;
    group: Group;
    hint?: string;
    disabled?: boolean;
    run: () => void;
  }

  const GROUP_ORDER: Group[] = ['actions', 'navigate', 'loops'];
  const GROUP_LABEL: Record<Group, string> = {
    actions: 'actions',
    navigate: 'navigate',
    loops: 'loops',
  };

  let inputEl = $state<HTMLInputElement | null>(null);
  let query = $state('');
  let selectedIndex = $state(0);

  // ── the row list — rebuilt from live store state + the current view every time anything it
  // reads changes (Svelte 5 tracks reads made synchronously inside a $derived's evaluation, even
  // through a helper function, so this stays live without a manual dependency list). ──
  function buildRows(): Row[] {
    const rows: Row[] = [];
    const s = runStore.state;
    const running = s.run.status === 'running' || s.run.status === 'quota-wait';
    const stopPending = s.run.stopPending;
    const rest = s.run.restState;
    const banked = rest === 'stopped-ember' || s.run.status === 'stopped';
    const failed = rest === 'failed-dark' || s.run.status === 'error';
    const canResume = !!s.run.resumeCmd;
    const inSystem = view !== 'cosmos'; // matches +page.svelte onKeydown's i/b/r gate
    const sel = runStore.selectedItem ?? s.currentItem;
    // Replay has no engine to drive — RunControlBar withholds every control verb there
    // (playback lives in TransportBar instead), so the palette mirrors that: no
    // start/brake/stop/resume/restart rows while watching a fixture.
    const isReplay = sessionStore.transportKind === 'replay';

    // ── Run control (RunControlBar's own show/disable logic, mirrored) ──
    if (inSystem && !isReplay) {
      if (!running && !banked && !failed) {
        rows.push({
          id: 'start',
          label: '✦ Start the loop',
          group: 'actions',
          hint: 'i',
          run: () => void sessionStore.control('start'),
        });
      }
      if (failed) {
        if (canResume) {
          rows.push({
            id: 'resume-checkpoint',
            label: '↻ Resume from checkpoint',
            group: 'actions',
            hint: 'r',
            run: () => void sessionStore.control('resume'),
          });
        }
        rows.push({
          id: 'restart',
          label: '✦ Restart fresh',
          group: 'actions',
          hint: 'i',
          run: () => void sessionStore.control('start'),
        });
      }
      if (running && !failed) {
        rows.push({
          id: 'brake-phase',
          label: 'Brake · phase',
          group: 'actions',
          hint: 'b',
          disabled: stopPending != null,
          run: () => void sessionStore.control('stop:phase'),
        });
        rows.push({
          id: 'brake-story',
          label: 'Brake · story',
          group: 'actions',
          disabled: stopPending != null,
          run: () => void sessionStore.control('stop:story'),
        });
        rows.push({
          id: 'stop-now',
          label: '⛔ Stop now',
          group: 'actions',
          disabled: stopPending === 'now',
          run: () => void sessionStore.control('stop:now'),
        });
      }
      if (stopPending != null) {
        rows.push({
          id: 'cancel-brake',
          label: 'Cancel brake',
          group: 'actions',
          run: () => void sessionStore.control('cancel-stop'),
        });
      }
      if (!failed && (banked || s.run.resumeCmd)) {
        rows.push({
          id: 'resume',
          label: '↻ Resume',
          group: 'actions',
          hint: 'r',
          run: () => void sessionStore.control('resume'),
        });
      }
    }

    // ── always-available chrome ──
    rows.push({
      id: 'help',
      label: 'Open Help — keyboard & legend',
      group: 'actions',
      hint: '?',
      run: onOpenHelp,
    });
    rows.push({
      id: 'settings',
      label: 'Open Settings',
      group: 'actions',
      hint: ',',
      run: onOpenSettings,
    });

    // ── Cosmos-altitude-only chrome (ShareButton / ignite-fab's own gating) ──
    if (view === 'cosmos') {
      if (!hasWsServer()) {
        rows.push({
          id: 'share',
          label: 'Share to phone',
          group: 'actions',
          run: () => void shareStore.openPopover(),
        });
      }
      rows.push({
        id: 'new-loop',
        label: '✦ New loop',
        group: 'actions',
        run: () => cosmosStore.igniteNew(),
      });
    }

    // ── altitude / mode navigation ──
    if (view !== 'cosmos') {
      rows.push({
        id: 'zoom-cosmos',
        label: '✦ Zoom out to Cosmos',
        group: 'navigate',
        run: onBackToCosmos,
      });
    }
    if (view === 'body') {
      rows.push({
        id: 'back-system',
        label: '← Back to System',
        group: 'navigate',
        run: onBackToSystem,
      });
    }
    if (view === 'system' && sel) {
      rows.push({
        id: 'fly-body',
        label: `Fly into body → ${sel}`,
        group: 'navigate',
        run: () => onEnterBody(sel),
      });
    }
    if (view === 'system') {
      rows.push({
        id: 'mode-observatory',
        label: '✦ Switch to Observatory',
        group: 'navigate',
        run: () => uiStore.setMode('observatory'),
      });
      rows.push({
        id: 'mode-ambient',
        label: '◐ Switch to Ambient',
        group: 'navigate',
        run: () => uiStore.setMode('planetarium'),
      });
      rows.push({
        id: 'mode-rewind',
        label: '⟲ Switch to Rewind',
        group: 'navigate',
        disabled: !sessionStore.playback,
        run: () => uiStore.setMode('rewind'),
      });
    }

    // ── jump to any known loop (the Cosmos roster) ──
    for (const l of cosmosStore.loops) {
      rows.push({
        id: `loop-${l.id}`,
        label: `Jump to ${l.name}`,
        group: 'loops',
        disabled: view === 'system' && activeLoop === l.id,
        run: () => onEnterSystem(l.id),
      });
    }

    return rows;
  }

  const allRows = $derived(buildRows());

  // simple case-insensitive substring-or-subsequence match (plan: "fuzzy-ish… is fine")
  function matches(label: string, q: string): boolean {
    if (!q) return true;
    const l = label.toLowerCase();
    const query = q.toLowerCase();
    if (l.includes(query)) return true;
    let i = 0;
    for (const ch of l) {
      if (ch === query[i]) i++;
      if (i === query.length) return true;
    }
    return false;
  }

  const filtered = $derived(allRows.filter((r) => matches(r.label, query)));
  const groupedFiltered = $derived(
    GROUP_ORDER.map((g) => ({
      group: g,
      rows: filtered.map((r, i) => ({ r, i })).filter(({ r }) => r.group === g),
    })).filter((g) => g.rows.length > 0),
  );
  const activeIndex = $derived(filtered.length ? Math.min(selectedIndex, filtered.length - 1) : 0);

  // reset selection to the first ENABLED row whenever the filtered set changes (a new query, or
  // the underlying state making a row appear/disappear/disable out from under the cursor)
  $effect(() => {
    const firstEnabled = filtered.findIndex((r) => !r.disabled);
    selectedIndex = firstEnabled >= 0 ? firstEnabled : 0;
  });

  function nextIndex(from: number, dir: 1 | -1): number {
    const n = filtered.length;
    if (!n) return 0;
    let i = from;
    for (let step = 0; step < n; step++) {
      i = (i + dir + n) % n;
      if (!filtered[i].disabled) return i;
    }
    return from; // every row disabled — stay put
  }

  function execute(r: Row | undefined) {
    if (!r || r.disabled) return;
    r.run();
    onClose();
  }

  function onInputKeydown(e: KeyboardEvent) {
    if (e.key === 'ArrowDown') {
      e.preventDefault();
      selectedIndex = nextIndex(activeIndex, 1);
    } else if (e.key === 'ArrowUp') {
      e.preventDefault();
      selectedIndex = nextIndex(activeIndex, -1);
    } else if (e.key === 'Enter') {
      e.preventDefault();
      execute(filtered[activeIndex]);
    }
    // Escape / Tab are intentionally left alone — they bubble to the dialog's
    // focusTrap (Escape → onClose, Tab → trapped) with no double-handling.
  }
</script>

<div class="scrim" role="presentation" onclick={(e) => { if (e.target === e.currentTarget) onClose(); }}>
  <div
    class="palette floating-card"
    role="dialog"
    aria-modal="true"
    aria-label="Command palette"
    tabindex="-1"
    use:focusTrap={{ onClose, initialFocus: () => inputEl }}
  >
    <div class="inputrow">
      <span class="prefix mono" aria-hidden="true">{kbdChip}</span>
      <input
        bind:this={inputEl}
        bind:value={query}
        class="filter"
        type="text"
        role="combobox"
        aria-expanded="true"
        aria-controls="cmdp-listbox"
        aria-autocomplete="list"
        aria-activedescendant={filtered[activeIndex] ? `cmdp-row-${filtered[activeIndex].id}` : undefined}
        placeholder="Type a command or search…"
        onkeydown={onInputKeydown}
      />
    </div>

    <div class="rows" id="cmdp-listbox" role="listbox" aria-label="commands">
      {#each groupedFiltered as g (g.group)}
        <div class="ghdr mono" role="presentation">{GROUP_LABEL[g.group]}</div>
        {#each g.rows as { r, i } (r.id)}
          <div
            id={`cmdp-row-${r.id}`}
            role="option"
            tabindex="-1"
            aria-selected={i === activeIndex}
            aria-disabled={r.disabled || undefined}
            class="row"
            class:active={i === activeIndex}
            class:disabled={r.disabled}
            onmouseenter={() => (selectedIndex = i)}
            onclick={() => execute(r)}
            onkeydown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); execute(r); } }}
          >
            <span class="rlabel">{r.label}</span>
            {#if r.hint}<span class="rhint mono">{r.hint}</span>{/if}
          </div>
        {/each}
      {/each}
      {#if !filtered.length}
        <div class="empty mono">no matching commands</div>
      {/if}
    </div>

    <div class="ftr mono">↑↓ navigate · ↵ select · esc close</div>
  </div>
</div>

<style>
  .scrim {
    position: absolute;
    inset: 0;
    display: flex;
    align-items: flex-start;
    justify-content: center;
    padding-top: min(14vh, 140px);
    background: var(--scrim);
    backdrop-filter: blur(4px);
    z-index: var(--z-popover);
  }
  .palette {
    width: min(560px, 92vw);
    max-height: 70vh;
    display: flex;
    flex-direction: column;
    /* the flagship .floating-card is the one place backdrop-filter is sanctioned (plan §1
       "glass only above the scene") — layered on top of the shared gradient/shadow/border. */
    backdrop-filter: blur(16px);
    animation: palette-in var(--dur-fast) var(--ease-out);
  }
  @keyframes palette-in {
    from {
      opacity: 0;
      transform: translateY(-6px) scale(0.98);
    }
    to {
      opacity: 1;
      transform: none;
    }
  }
  @media (prefers-reduced-motion: reduce) {
    :global(:root:not([data-motion='full'])) .palette {
      animation: none;
    }
  }
  /* mirrors the media block above, for the user-forced reduced-motion setting */
  :global(:root[data-motion='reduced']) .palette {
    animation: none;
  }

  .inputrow {
    display: flex;
    align-items: center;
    gap: var(--space-3);
    padding: var(--space-4) var(--space-4) var(--space-3);
    border-bottom: 1px solid var(--hairline);
  }
  .prefix {
    flex: none;
    font-size: var(--text-2xs);
    letter-spacing: 0.08em;
    color: var(--text-faint);
    padding: 2px 6px;
    border: 1px solid var(--hairline);
    border-radius: var(--radius-sm);
  }
  .filter {
    flex: 1;
    min-width: 0;
    background: transparent;
    border: none;
    outline: none;
    font-family: var(--font-grotesk);
    font-size: var(--text-sm);
    color: var(--text-primary);
  }
  .filter::placeholder {
    color: var(--text-faint);
  }

  .rows {
    overflow-y: auto;
    padding: var(--space-2) 0;
  }
  .ghdr {
    padding: var(--space-2) var(--space-4) 4px;
    font-size: var(--text-xs);
    font-weight: 600;
    letter-spacing: 0.08em;
    text-transform: uppercase;
    color: var(--em-low);
  }
  .row {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: var(--space-3);
    padding: 7px var(--space-4);
    font-size: var(--text-xs);
    color: var(--text-dim);
    cursor: pointer;
    border-left: 2px solid transparent;
    transition: background var(--dur-feedback) var(--ease-standard),
      border-color var(--dur-feedback) var(--ease-standard),
      color var(--dur-feedback) var(--ease-standard);
  }
  .row.active {
    background: var(--surface-hover);
    border-left-color: var(--em-hi);
    color: var(--text-primary);
  }
  .row.disabled {
    color: var(--text-faint);
    opacity: 0.45;
    cursor: not-allowed;
    pointer-events: none;
  }
  .rlabel {
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .rhint {
    flex: none;
    font-size: var(--text-2xs);
    letter-spacing: 0.04em;
    color: var(--text-faint);
    padding: 1px 6px;
    border: 1px solid var(--hairline);
    border-radius: var(--radius-sm);
  }
  .empty {
    padding: var(--space-4);
    font-size: var(--text-xs);
    color: var(--text-faint);
    text-align: center;
  }

  .ftr {
    padding: var(--space-2) var(--space-4);
    font-size: var(--text-2xs);
    letter-spacing: 0.04em;
    color: var(--text-faint);
    border-top: 1px solid var(--hairline);
  }
</style>
