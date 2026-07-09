<script lang="ts">
  // SettingRow — one setting: label + description (query-highlighted), the control (delegated
  // by meta.control), and right-side affordances (reset dot when changed, restart badge, scope
  // badge). Value reads via settingsStore.get / writes via settingsStore.set. Text/number/path
  // controls validate on commit through meta.validate; invalid values are shown but NOT
  // persisted, and Escape reverts.
  import { settingsStore } from '../../stores/settings.svelte';
  import type { SettingMeta } from '../../settings/schema';
  import Toggle from './Toggle.svelte';
  import Segmented from './Segmented.svelte';
  import SliderRow from './SliderRow.svelte';
  import NumberField from './NumberField.svelte';
  import TextField from './TextField.svelte';
  import SelectField from './SelectField.svelte';
  import PathField from './PathField.svelte';

  let { meta, query = '' }: { meta: SettingMeta; query?: string } = $props();

  const value = $derived(settingsStore.get<unknown>(meta.key));
  const changed = $derived(settingsStore.isChanged(meta.key));
  let error = $state<string | null>(null);

  // control:'seg' whose value is an array (notifications.alertOn) → multi-select toggle chips.
  const isMulti = $derived(meta.control === 'seg' && Array.isArray(value));

  function commit(raw: unknown) {
    const coerced = meta.control === 'number' ? Number(raw) : raw;
    const err = meta.validate ? meta.validate(coerced) : null;
    if (err) {
      error = err;
      return;
    }
    error = null;
    void settingsStore.set(meta.key, coerced);
  }
  function cancel() {
    error = null;
  }
  function resetKey() {
    error = null;
    settingsStore.resetKey(meta.key);
  }
  function toggleMember(opt: string) {
    const arr = Array.isArray(value) ? [...(value as string[])] : [];
    const i = arr.indexOf(opt);
    if (i >= 0) arr.splice(i, 1);
    else arr.push(opt);
    void settingsStore.set(meta.key, arr);
  }

  // Contiguous, case-insensitive substring highlight. Subsequence-only matches (which the
  // search still counts) simply don't highlight — acceptable and cheap.
  function parts(text: string): { t: string; hit: boolean }[] {
    const q = query.trim().toLowerCase();
    if (!q) return [{ t: text, hit: false }];
    const idx = text.toLowerCase().indexOf(q);
    if (idx < 0) return [{ t: text, hit: false }];
    return [
      { t: text.slice(0, idx), hit: false },
      { t: text.slice(idx, idx + q.length), hit: true },
      { t: text.slice(idx + q.length), hit: false },
    ].filter((p) => p.t.length > 0);
  }
</script>

<div class="row" class:invalid={!!error}>
  <div class="meta">
    <div class="labelline">
      <span class="label">
        {#each parts(meta.label) as p}{#if p.hit}<mark>{p.t}</mark>{:else}{p.t}{/if}{/each}
      </span>
      {#if changed}
        <button
          type="button"
          class="resetdot"
          title="Reset to default"
          aria-label={`Reset ${meta.label} to default`}
          onclick={resetKey}
        ></button>
      {/if}
      {#if meta.apply === 'restart'}
        <span class="badge" title="Takes effect after restart">restart</span>
      {/if}
      {#if meta.scope !== 'user'}
        <span class="badge" title="Applies to {meta.scope}">{meta.scope}</span>
      {/if}
    </div>
    <p class="desc">
      {#each parts(meta.description) as p}{#if p.hit}<mark>{p.t}</mark>{:else}{p.t}{/if}{/each}
    </p>
    {#if error}<p class="err" role="alert">{error}</p>{/if}
  </div>

  <div class="control">
    {#if isMulti}
      <div class="chips" role="group" aria-label={meta.label}>
        {#each meta.options ?? [] as o (o.value)}
          <button
            type="button"
            class="seg-item chip"
            class:selected={(value as string[]).includes(o.value)}
            aria-pressed={(value as string[]).includes(o.value)}
            onclick={() => toggleMember(o.value)}
          >
            {o.label}
          </button>
        {/each}
      </div>
    {:else if meta.control === 'toggle'}
      <Toggle value={value as boolean} label={meta.label} onChange={(v) => commit(v)} />
    {:else if meta.control === 'seg'}
      <Segmented
        value={value as string}
        options={meta.options ?? []}
        label={meta.label}
        onChange={(v) => commit(v)}
      />
    {:else if meta.control === 'select'}
      <SelectField
        value={value as string}
        options={meta.options ?? []}
        label={meta.label}
        onChange={(v) => commit(v)}
      />
    {:else if meta.control === 'slider'}
      <SliderRow
        value={value as number}
        min={meta.min}
        max={meta.max}
        step={meta.step}
        unit={meta.unit}
        label={meta.label}
        onChange={(v) => commit(v)}
      />
    {:else if meta.control === 'number'}
      <NumberField
        value={value as number}
        min={meta.min}
        max={meta.max}
        step={meta.step}
        unit={meta.unit}
        label={meta.label}
        invalid={!!error}
        onCommit={commit}
        onCancel={cancel}
      />
    {:else if meta.control === 'text'}
      <TextField
        value={value as string}
        label={meta.label}
        invalid={!!error}
        onCommit={commit}
        onCancel={cancel}
      />
    {:else if meta.control === 'path'}
      <PathField
        value={value as string | null}
        label={meta.label}
        invalid={!!error}
        onCommit={commit}
        onCancel={cancel}
      />
    {:else}
      <span class="readonly">
        {#if typeof value === 'boolean'}{value ? 'On' : 'Off'}{:else if value == null || typeof value === 'object'}—{:else}{String(value)}{/if}
      </span>
    {/if}
  </div>
</div>

<style>
  .row {
    display: flex;
    align-items: flex-start;
    justify-content: space-between;
    gap: var(--space-4);
    padding: var(--space-3) 0;
    border-bottom: 1px solid var(--hairline);
  }
  .row:last-child {
    border-bottom: none;
  }
  .meta {
    flex: 1;
    min-width: 0;
    display: flex;
    flex-direction: column;
    gap: 3px;
  }
  .labelline {
    display: flex;
    align-items: center;
    gap: var(--space-2);
    flex-wrap: wrap;
  }
  .label {
    font-size: var(--text-sm);
    color: var(--em-hi);
  }
  .desc {
    margin: 0;
    font-size: var(--text-xs);
    color: var(--em-low);
    line-height: 1.4;
  }
  .err {
    margin: 0;
    font-size: var(--text-2xs);
    color: var(--status-err-core);
    line-height: 1.35;
  }
  mark {
    /* monochrome highlight — never the browser-default yellow (chrome is hueless) */
    background: color-mix(in srgb, var(--em-hi) 20%, transparent);
    color: var(--em-hi);
    border-radius: 2px;
    padding: 0 1px;
  }
  .resetdot {
    flex: none;
    width: 8px;
    height: 8px;
    padding: 0;
    border: none;
    border-radius: 50%;
    background: var(--em-mid);
    cursor: pointer;
    transition: background-color var(--dur-feedback) var(--ease-standard);
  }
  .resetdot:hover {
    background: var(--em-hi);
  }
  .badge {
    font-size: var(--text-2xs);
    letter-spacing: 0.06em;
    text-transform: uppercase;
    color: var(--em-low);
    border: 1px solid var(--hairline);
    border-radius: var(--radius-pill);
    padding: 1px 6px;
  }
  .control {
    flex: none;
    display: flex;
    align-items: center;
    justify-content: flex-end;
    max-width: 58%;
  }
  .readonly {
    font-size: var(--text-sm);
    color: var(--em-low);
  }
  .chips {
    display: flex;
    flex-wrap: wrap;
    gap: var(--space-2);
    justify-content: flex-end;
  }
  .chip {
    border: 1px solid var(--hairline);
  }
</style>
