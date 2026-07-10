<script lang="ts">
  // AiByokPanel — the custom multi-provider BYOK editor (replaces the GenericPanel stub).
  // Adapts a provider-instance model: a list of configured (runner + provider + auth-mode)
  // credential profiles, each backed by an OS-keychain secret that is WRITE-ONLY from the UI
  // (set/cleared, never read back — presence is mirrored via keychain_has). The auth-mode
  // cascade is driven off PROVIDER_MATRIX so unsupported (provider, runner, mode) triples can
  // never be authored, and an explainer surfaces exactly which env vars a spawn will set/clear.
  // Chrome = monochrome only (design law M5): --em-*, --surface-*, --hairline, shared .btn/.seg/
  // .input/.field primitives; amber only for a genuine "missing credential / unsupported" alert.
  import { settingsStore } from '../../stores/settings.svelte';
  import {
    PROVIDER_MATRIX,
    providerRule,
    type ProviderInstance,
    type ProviderId,
    type RunnerId,
    type AuthMode,
  } from '../../settings/schema';
  import type { ByokAuthStatus } from '../../settings/contract';
  import {
    accountFor,
    keychainSet,
    keychainHas,
    keychainDelete,
    byokAuthStatus,
  } from '../../settings/keychain';
  import { hasTauri } from '../../settings/backend';
  import TextField from './TextField.svelte';
  import Segmented from './Segmented.svelte';
  import SelectField from './SelectField.svelte';

  let { query = '', matched }: { query?: string; matched?: Set<string> } = $props();

  const store = settingsStore;
  const tauri = hasTauri();

  const instances = $derived(store.data.ai.instances);
  const defaultId = $derived(store.data.ai.defaultInstanceId);
  const fallbackId = $derived(store.data.ai.fallbackInstanceId);

  // The whole AI tab is a single search anchor (registry key 'ai'); if a query is active and
  // didn't match it, say so rather than showing the full editor under an unrelated search.
  const noMatch = $derived(!!query.trim() && !!matched && !matched.has('ai'));

  // ── option lists + label maps ──────────────────────────────────────────────
  const RUNNER_OPTS = [
    { value: 'claude', label: 'Claude Code' },
    { value: 'codex', label: 'Codex' },
    { value: 'aider', label: 'Aider' },
  ];
  const PROVIDER_OPTS = [
    { value: 'anthropic', label: 'Anthropic' },
    { value: 'openai', label: 'OpenAI' },
    { value: 'google', label: 'Google' },
    { value: 'openrouter', label: 'OpenRouter' },
    { value: 'bedrock', label: 'Bedrock' },
    { value: 'vertex', label: 'Vertex' },
    { value: 'local', label: 'Local' },
  ];
  const RUNNER_LABEL: Record<RunnerId, string> = {
    claude: 'Claude Code',
    codex: 'Codex',
    aider: 'Aider',
  };
  const PROVIDER_LABEL: Record<ProviderId, string> = {
    anthropic: 'Anthropic',
    openai: 'OpenAI',
    google: 'Google',
    openrouter: 'OpenRouter',
    bedrock: 'Bedrock',
    vertex: 'Vertex',
    local: 'Local',
  };
  const MODE_LABEL: Record<AuthMode, string> = {
    subscription: 'Subscription',
    apiKey: 'API key',
    gateway: 'Gateway',
    cloud: 'Cloud',
    local: 'Local',
  };
  const ALL_MODES: AuthMode[] = ['subscription', 'apiKey', 'gateway', 'cloud', 'local'];

  /** Modes the matrix marks supported for this (provider, runner) — drives the mode cascade. */
  function modesFor(p: ProviderId, r: RunnerId): AuthMode[] {
    return ALL_MODES.filter((m) => providerRule(p, r, m).supported);
  }
  /** Which runners CAN drive this provider — used to explain an unsupported combo. */
  function runnersForProvider(p: ProviderId): RunnerId[] {
    return [...new Set(PROVIDER_MATRIX.filter((e) => e.provider === p).map((e) => e.runner))];
  }

  // ── keychain presence mirror (secret never read back) ──────────────────────
  let presence = $state<Record<string, boolean>>({});
  const probed = new Set<string>(); // accounts already probed (plain, non-reactive)
  $effect(() => {
    for (const inst of instances) {
      const acct = accountFor(inst);
      if (acct && !probed.has(acct)) {
        probed.add(acct);
        void keychainHas(acct).then((has) => {
          presence = { ...presence, [acct]: has };
        });
      }
    }
  });

  // ── auth-probe results, keyed by instance id (or 'draft' for the open form) ──
  let statuses = $state<Record<string, ByokAuthStatus | null>>({});
  let testing = $state<string | null>(null);

  // ── the add / edit form ─────────────────────────────────────────────────────
  type Draft = {
    name: string;
    runner: RunnerId;
    provider: ProviderId;
    mode: AuthMode;
    baseUrl: string;
    region: string;
    projectId: string;
    defaultModel: string;
  };
  let editingId = $state<string | null>(null); // an instance id, 'new', or null (closed)
  let draft = $state<Draft>(blankDraft());
  let secretDraft = $state('');
  let showSecret = $state(false);
  let busy = $state(false);

  function blankDraft(): Draft {
    return {
      name: '',
      runner: 'claude',
      provider: 'anthropic',
      mode: 'subscription',
      baseUrl: '',
      region: '',
      projectId: '',
      defaultModel: '',
    };
  }

  const rule = $derived(providerRule(draft.provider, draft.runner, draft.mode));
  const supportedModes = $derived(modesFor(draft.provider, draft.runner));
  const modeOpts = $derived(supportedModes.map((m) => ({ value: m, label: MODE_LABEL[m] })));
  const account = $derived(rule.keychainAccount);
  const needsBaseUrl = $derived(draft.mode === 'gateway' || draft.mode === 'local');
  const needsRegion = $derived(draft.mode === 'cloud');
  const needsProjectId = $derived(draft.mode === 'cloud' && draft.provider === 'vertex');

  /** Keep the selected mode inside the supported set whenever provider/runner change. */
  function ensureValidMode() {
    const modes = modesFor(draft.provider, draft.runner);
    if (modes.length && !modes.includes(draft.mode)) draft.mode = modes[0];
  }
  function setProvider(p: string) {
    draft.provider = p as ProviderId;
    ensureValidMode();
  }
  function setRunner(r: string) {
    draft.runner = r as RunnerId;
    ensureValidMode();
  }
  function setMode(m: string) {
    draft.mode = m as AuthMode;
  }

  function openAdd() {
    editingId = 'new';
    draft = blankDraft();
    secretDraft = '';
    showSecret = false;
    const s = { ...statuses };
    delete s.draft;
    statuses = s;
  }
  function openEdit(inst: ProviderInstance) {
    editingId = inst.id;
    draft = {
      name: inst.name,
      runner: inst.runner,
      provider: inst.provider,
      mode: inst.mode,
      baseUrl: inst.baseUrl ?? '',
      region: inst.region ?? '',
      projectId: inst.projectId ?? '',
      defaultModel: inst.defaultModel ?? '',
    };
    secretDraft = '';
    showSecret = false;
  }
  function closeForm() {
    editingId = null;
    secretDraft = '';
    showSecret = false;
  }

  async function pasteSecret() {
    try {
      secretDraft = await navigator.clipboard.readText();
    } catch {
      /* clipboard read denied — user can type/paste manually */
    }
  }
  async function clearSecret() {
    secretDraft = '';
    if (account) {
      await keychainDelete(account);
      presence = { ...presence, [account]: false };
      // The account is shared per provider/mode, so the delete affects every sibling
      // instance too — sync their persisted hasSecret mirrors rather than leaving them
      // claiming a credential that no longer exists.
      for (const i of store.data.ai.instances) {
        if (i.hasSecret && accountFor(i) === account) {
          await store.updateInstance(i.id, { hasSecret: false });
        }
      }
    }
  }

  async function testConnection(inst: ProviderInstance, key: string) {
    testing = key;
    try {
      statuses = { ...statuses, [key]: await byokAuthStatus(inst) };
    } finally {
      testing = null;
    }
  }

  async function save() {
    if (!draft.name.trim() || !supportedModes.length) return;
    busy = true;
    try {
      if (account && secretDraft.trim()) {
        await keychainSet(account, secretDraft.trim());
        presence = { ...presence, [account]: true };
      }
      const patch: Omit<ProviderInstance, 'id'> = {
        name: draft.name.trim(),
        runner: draft.runner,
        provider: draft.provider,
        mode: draft.mode,
        baseUrl: needsBaseUrl && draft.baseUrl.trim() ? draft.baseUrl.trim() : undefined,
        region: needsRegion && draft.region.trim() ? draft.region.trim() : undefined,
        projectId: needsProjectId && draft.projectId.trim() ? draft.projectId.trim() : undefined,
        defaultModel: draft.defaultModel.trim() || undefined,
        hasSecret: account ? !!(presence[account] || secretDraft.trim()) : false,
      };
      if (editingId === 'new') {
        const id = await store.addInstance(patch);
        // first instance authored becomes the default so loops have something to run.
        if (!defaultId) await store.set('ai.defaultInstanceId', id);
      } else if (editingId) {
        await store.updateInstance(editingId, patch);
      }
      closeForm();
    } finally {
      busy = false;
    }
  }

  async function remove(inst: ProviderInstance) {
    if (
      store.data.general.confirmDestructive &&
      typeof window !== 'undefined' &&
      !window.confirm(`Remove “${inst.name || 'this provider'}”? Its stored credential (if any) is deleted too.`)
    ) {
      return;
    }
    const acct = accountFor(inst);
    // removeInstance also clears the keychain secret + orphaned default/fallback pointers (WS-A).
    await store.removeInstance(inst.id);
    if (editingId === inst.id) closeForm();
    if (acct) {
      // re-probe next tick (another instance may still share this account).
      probed.delete(acct);
      const p = { ...presence };
      delete p[acct];
      presence = p;
    }
  }

  // Persist default/fallback through the generic setter — the store exposes no dedicated
  // setter for these two ids (only add/update/removeInstance), so set() by dotted path is used.
  function setDefault(v: string) {
    void store.set('ai.defaultInstanceId', v || null);
  }
  function setFallback(v: string) {
    void store.set('ai.fallbackInstanceId', v || null);
  }

  /** Status chip: a test result if one was run, else keychain presence / sign-in expectation. */
  function chip(inst: ProviderInstance): { label: string; tone: 'ok' | 'warn' | 'muted' } {
    const st = statuses[inst.id];
    if (st) {
      return st.ok
        ? { label: 'Reachable', tone: 'ok' }
        : { label: st.detail ?? 'Unreachable', tone: 'warn' };
    }
    const acct = accountFor(inst);
    if (acct == null) {
      return inst.mode === 'local'
        ? { label: 'Local endpoint', tone: 'muted' }
        : { label: 'Sign-in expected', tone: 'muted' };
    }
    return presence[acct]
      ? { label: 'Key set', tone: 'ok' }
      : { label: 'No credential', tone: 'warn' };
  }

  /** The vendor CLI's own login command for a subscription runner. */
  function loginCmd(r: RunnerId): string {
    return r === 'codex' ? 'codex login' : 'claude login';
  }

  const selectOpts = $derived([
    { value: '', label: 'None' },
    ...instances.map((i) => ({ value: i.id, label: i.name || PROVIDER_LABEL[i.provider] })),
  ]);
</script>

<section class="ai">
  {#if noMatch}
    <p class="muted">No matches in AI providers.</p>
  {:else}
    <header class="hd">
      <span>AI providers &amp; keys</span>
      {#if instances.length}
        <button
          type="button"
          class="btn btn-ghost btn-sm"
          onclick={openAdd}
          disabled={editingId === 'new'}
        >
          Add provider
        </button>
      {/if}
    </header>

    {#if instances.length === 0 && editingId !== 'new'}
      <div class="empty">
        <p>No AI providers yet — add one so loops can run.</p>
        <button type="button" class="btn btn-primary btn-sm" onclick={openAdd}>Add a provider</button>
      </div>
    {:else if instances.length}
      <ul class="cards">
        {#each instances as inst (inst.id)}
          {@const c = chip(inst)}
          <li class="card" class:editing={editingId === inst.id}>
            <div class="cardmain">
              <div class="cardtop">
                <span class="name">{inst.name || 'Untitled provider'}</span>
                {#if defaultId === inst.id}<span class="tag">Default</span>{/if}
                {#if fallbackId === inst.id}<span class="tag">Fallback</span>{/if}
                <span class="chip chip-{c.tone}">{c.label}</span>
              </div>
              <div class="summary">
                {RUNNER_LABEL[inst.runner]} · {PROVIDER_LABEL[inst.provider]} · {MODE_LABEL[inst.mode]}
              </div>
            </div>
            <div class="cardactions">
              <button
                type="button"
                class="btn btn-ghost btn-sm"
                disabled={!tauri || testing === inst.id}
                title={tauri ? 'Probe reachability' : 'Only available in the desktop app'}
                onclick={() => testConnection(inst, inst.id)}
              >
                {testing === inst.id ? 'Testing…' : 'Test'}
              </button>
              <button type="button" class="btn btn-ghost btn-sm" onclick={() => openEdit(inst)}>Edit</button>
              <button type="button" class="btn btn-danger btn-sm" onclick={() => remove(inst)}>Remove</button>
            </div>
          </li>
        {/each}
      </ul>
    {/if}

    {#if editingId}
      <div class="form">
        <div class="formhd">{editingId === 'new' ? 'Add provider' : 'Edit provider'}</div>

        <div class="field">
          <span class="field-label">Name</span>
          <TextField
            value={draft.name}
            label="Provider name"
            onCommit={(v) => (draft.name = v)}
            onCancel={() => {}}
          />
        </div>

        <div class="field">
          <span class="field-label">Runner</span>
          <Segmented value={draft.runner} options={RUNNER_OPTS} label="Runner" onChange={setRunner} />
        </div>

        <div class="field">
          <span class="field-label">Provider</span>
          <SelectField
            value={draft.provider}
            options={PROVIDER_OPTS}
            label="Provider"
            onChange={setProvider}
          />
        </div>

        {#if supportedModes.length}
          <div class="field">
            <span class="field-label">Auth mode</span>
            <Segmented value={draft.mode} options={modeOpts} label="Auth mode" onChange={setMode} />
          </div>
        {:else}
          <p class="warn-note" role="alert">
            {PROVIDER_LABEL[draft.provider]} needs the
            {runnersForProvider(draft.provider)
              .map((r) => RUNNER_LABEL[r])
              .join(' or ')} runner — the {RUNNER_LABEL[draft.runner]} runner can’t use a
            {PROVIDER_LABEL[draft.provider]} credential.
          </p>
        {/if}

        {#if needsBaseUrl}
          <div class="field">
            <span class="field-label">{draft.mode === 'local' ? 'Endpoint URL' : 'Gateway base URL'}</span>
            <TextField
              value={draft.baseUrl}
              label="Base URL"
              onCommit={(v) => (draft.baseUrl = v)}
              onCancel={() => {}}
            />
          </div>
        {/if}
        {#if needsRegion}
          <div class="field">
            <span class="field-label">Region</span>
            <TextField
              value={draft.region}
              label="Region"
              onCommit={(v) => (draft.region = v)}
              onCancel={() => {}}
            />
          </div>
        {/if}
        {#if needsProjectId}
          <div class="field">
            <span class="field-label">Project ID</span>
            <TextField
              value={draft.projectId}
              label="Project ID"
              onCommit={(v) => (draft.projectId = v)}
              onCancel={() => {}}
            />
          </div>
        {/if}

        <div class="field">
          <span class="field-label">Default model <span class="opt">(optional)</span></span>
          <TextField
            value={draft.defaultModel}
            label="Default model"
            onCommit={(v) => (draft.defaultModel = v)}
            onCancel={() => {}}
          />
        </div>

        {#if account}
          <div class="field">
            <span class="field-label">
              Secret {#if presence[account]}<span class="setmark">•••• set</span>{/if}
            </span>
            <div class="secretrow">
              <input
                class="input"
                type={showSecret ? 'text' : 'password'}
                bind:value={secretDraft}
                placeholder={presence[account] ? 'Enter a new value to replace' : 'Paste your key…'}
                aria-label="Secret value"
                autocomplete="off"
                spellcheck="false"
              />
              <button type="button" class="btn btn-ghost btn-sm" onclick={() => (showSecret = !showSecret)}>
                {showSecret ? 'Hide' : 'Show'}
              </button>
              {#if tauri}
                <button type="button" class="btn btn-ghost btn-sm" onclick={pasteSecret}>Paste</button>
              {/if}
              <button type="button" class="btn btn-ghost btn-sm" onclick={clearSecret}>Clear</button>
            </div>
            <p class="hint">
              Stored in your OS keychain under <code>{account}</code>. Written only — the app never
              reads it back.
            </p>
          </div>
        {:else if draft.mode === 'local'}
          <p class="hint">No credential needed — this runner talks to your local endpoint directly.</p>
        {:else if draft.mode === 'cloud'}
          <p class="hint">
            No key stored here — the loop inherits your ambient
            {draft.provider === 'bedrock'
              ? 'AWS credential chain (aws configure / SSO)'
              : 'Google Cloud ADC (gcloud auth application-default login)'} at launch.
          </p>
        {:else}
          <div class="signin">
            <p class="hint">
              Sign in with your {PROVIDER_LABEL[draft.provider]} plan: run
              <code>{loginCmd(draft.runner)}</code> in your terminal. Orrery drives the official CLI;
              it never stores your subscription token.
            </p>
            <div class="signin-test">
              <button
                type="button"
                class="btn btn-ghost btn-sm"
                disabled={!tauri || testing === 'draft'}
                title={tauri ? 'Probe reachability' : 'Only available in the desktop app'}
                onclick={() =>
                  testConnection(
                    {
                      id: 'draft',
                      name: draft.name,
                      runner: draft.runner,
                      provider: draft.provider,
                      mode: draft.mode,
                      baseUrl: draft.baseUrl || undefined,
                      region: draft.region || undefined,
                      // gate on needsProjectId (same as save) so a stale projectId from a
                      // previous vertex draft can't ride along on a non-vertex probe.
                      projectId: needsProjectId && draft.projectId.trim() ? draft.projectId.trim() : undefined,
                    },
                    'draft',
                  )}
              >
                {testing === 'draft' ? 'Testing…' : 'Test connection'}
              </button>
              {#if statuses.draft}
                {@const ds = statuses.draft}
                <span class="chip chip-{ds.ok ? 'ok' : 'warn'}">
                  {ds.ok ? 'Reachable' : (ds.detail ?? 'Unreachable')}
                </span>
              {/if}
            </div>
          </div>
        {/if}

        {#if supportedModes.length}
          <div class="explain">
            <div class="explain-hd">At spawn, Orrery will</div>
            <div class="explain-row">
              <span class="k">Sets</span>
              <span class="v mono">{rule.inject.length ? rule.inject.join(', ') : 'nothing'}</span>
            </div>
            <div class="explain-row">
              <span class="k">Clears</span>
              <span class="v mono">{rule.scrub.length ? rule.scrub.join(', ') : 'nothing'}</span>
            </div>
          </div>
        {/if}

        <div class="formbtns">
          <button type="button" class="btn btn-ghost btn-sm" onclick={closeForm}>Cancel</button>
          <button
            type="button"
            class="btn btn-primary btn-sm"
            disabled={busy || !draft.name.trim() || !supportedModes.length}
            onclick={save}
          >
            {editingId === 'new' ? 'Add provider' : 'Save'}
          </button>
        </div>
      </div>
    {/if}

    {#if instances.length}
      <div class="defaults">
        <div class="formhd">Default &amp; fallback</div>
        <div class="field">
          <span class="field-label">Default provider</span>
          <SelectField
            value={defaultId ?? ''}
            options={selectOpts}
            label="Default provider"
            onChange={setDefault}
          />
        </div>
        <div class="field">
          <span class="field-label">Fallback provider <span class="opt">(optional)</span></span>
          <SelectField
            value={fallbackId ?? ''}
            options={selectOpts}
            label="Fallback provider"
            onChange={setFallback}
          />
        </div>
      </div>
    {/if}
  {/if}
</section>

<style>
  .ai {
    display: flex;
    flex-direction: column;
    gap: var(--space-4);
    min-width: 0;
  }
  .muted {
    margin: 0;
    font-size: var(--text-xs);
    color: var(--em-faint);
  }
  .hd {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: var(--space-3);
    font-size: var(--text-xs);
    font-weight: 600;
    letter-spacing: 0.08em;
    text-transform: uppercase;
    color: var(--em-low);
  }

  /* ── empty state ── */
  .empty {
    display: flex;
    flex-direction: column;
    align-items: flex-start;
    gap: var(--space-3);
    padding: var(--space-5) var(--space-4);
    border: 1px dashed var(--hairline);
    border-radius: var(--radius);
  }
  .empty p {
    margin: 0;
    font-size: var(--text-sm);
    color: var(--em-mid);
  }

  /* ── instance cards ── */
  .cards {
    list-style: none;
    margin: 0;
    padding: 0;
    display: flex;
    flex-direction: column;
    gap: var(--space-2);
  }
  .card {
    display: flex;
    align-items: flex-start;
    justify-content: space-between;
    gap: var(--space-3);
    padding: var(--space-3);
    border: 1px solid var(--hairline);
    border-radius: var(--radius-sm);
    background: var(--surface-void);
  }
  .card.editing {
    border-color: color-mix(in srgb, var(--em-mid) 40%, var(--hairline));
  }
  .cardmain {
    min-width: 0;
    display: flex;
    flex-direction: column;
    gap: 3px;
  }
  .cardtop {
    display: flex;
    align-items: center;
    flex-wrap: wrap;
    gap: var(--space-2);
  }
  .name {
    font-size: var(--text-sm);
    color: var(--em-hi);
  }
  .summary {
    font-size: var(--text-xs);
    color: var(--em-low);
  }
  .cardactions {
    flex: none;
    display: flex;
    align-items: center;
    gap: var(--space-1);
  }

  /* ── tags / chips ── */
  .tag {
    font-size: var(--text-2xs);
    letter-spacing: 0.06em;
    text-transform: uppercase;
    color: var(--em-mid);
    border: 1px solid var(--hairline);
    border-radius: var(--radius-pill);
    padding: 1px 6px;
  }
  .chip {
    font-size: var(--text-2xs);
    letter-spacing: 0.02em;
    border: 1px solid var(--hairline);
    border-radius: var(--radius-pill);
    padding: 1px 7px;
  }
  .chip-ok {
    color: var(--em-hi);
    border-color: color-mix(in srgb, var(--em-mid) 35%, var(--hairline));
  }
  .chip-muted {
    color: var(--em-low);
  }
  .chip-warn {
    color: var(--status-warn-core);
    border-color: color-mix(in srgb, var(--status-warn-core) 45%, transparent);
  }

  /* ── add / edit form ── */
  .form,
  .defaults {
    display: flex;
    flex-direction: column;
    gap: var(--space-3);
    padding: var(--space-4);
    border: 1px solid var(--hairline);
    border-radius: var(--radius);
    background: var(--surface-void);
  }
  .formhd {
    font-size: var(--text-xs);
    font-weight: 600;
    letter-spacing: 0.08em;
    text-transform: uppercase;
    color: var(--em-low);
  }
  .opt {
    text-transform: none;
    letter-spacing: 0;
    color: var(--em-faint);
    font-weight: 400;
  }
  .setmark {
    text-transform: none;
    letter-spacing: 0.04em;
    color: var(--em-mid);
  }

  .secretrow {
    display: flex;
    align-items: center;
    gap: var(--space-2);
  }
  .secretrow .input {
    width: auto;
    flex: 1 1 auto;
    min-width: 0;
  }

  .hint {
    margin: 0;
    font-size: var(--text-xs);
    color: var(--em-low);
    line-height: 1.45;
  }
  code {
    font-family: var(--font-mono);
    font-size: var(--text-2xs);
    color: var(--em-mid);
    background: color-mix(in srgb, var(--em-hi) 8%, transparent);
    border-radius: 3px;
    padding: 1px 4px;
  }
  .warn-note {
    margin: 0;
    font-size: var(--text-xs);
    color: var(--status-warn-core);
    line-height: 1.45;
  }

  .signin {
    display: flex;
    flex-direction: column;
    gap: var(--space-2);
  }
  .signin-test {
    display: flex;
    align-items: center;
    gap: var(--space-2);
  }

  /* ── spawn explainer ── */
  .explain {
    display: flex;
    flex-direction: column;
    gap: var(--space-1);
    padding: var(--space-3);
    border: 1px solid var(--hairline);
    border-radius: var(--radius-sm);
    background: var(--surface-panel);
  }
  .explain-hd {
    font-size: var(--text-2xs);
    letter-spacing: 0.06em;
    text-transform: uppercase;
    color: var(--em-faint);
  }
  .explain-row {
    display: flex;
    gap: var(--space-3);
    font-size: var(--text-xs);
    line-height: 1.5;
  }
  .explain-row .k {
    flex: none;
    width: 46px;
    color: var(--em-low);
  }
  .explain-row .v {
    min-width: 0;
    color: var(--em-mid);
    word-break: break-word;
  }

  .formbtns {
    display: flex;
    justify-content: flex-end;
    gap: var(--space-2);
  }
</style>
