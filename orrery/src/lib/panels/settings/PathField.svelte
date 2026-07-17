<script lang="ts">
  // PathField — a path <input> on the shared .input primitive, plus (in Tauri) a "Choose…"
  // folder picker via @tauri-apps/plugin-dialog. In `vite dev` (no Tauri) it degrades to a
  // plain editable text field. Same uncontrolled-draft + commit/revert contract as TextField.
  import { hasTauri } from '../../settings/backend';

  let {
    value,
    label,
    invalid = false,
    onCommit,
    onCancel,
  }: {
    value: string | null;
    label: string;
    invalid?: boolean;
    onCommit: (raw: string) => void;
    onCancel: () => void;
  } = $props();

  const tauri = hasTauri();
  let el = $state<HTMLInputElement | null>(null);
  // See NumberField: track the last text we wrote so an external update while focused isn't
  // later clobbered by the blur commit of stale field text.
  let lastSynced = '';

  $effect(() => {
    const v = value == null ? '' : String(value);
    if (!el) return;
    if (document.activeElement !== el || el.value === lastSynced) {
      el.value = v;
      lastSynced = v;
    }
  });

  function onKeydown(e: KeyboardEvent) {
    if (e.key === 'Enter') {
      e.preventDefault();
      onCommit(el?.value ?? '');
      el?.blur();
    } else if (e.key === 'Escape') {
      e.preventDefault();
      if (el) el.value = value == null ? '' : String(value);
      onCancel();
      el?.blur();
    }
  }

  async function choose() {
    try {
      const { open } = await import('@tauri-apps/plugin-dialog');
      const picked = await open({
        directory: true,
        multiple: false,
        defaultPath: value ?? undefined,
      });
      if (typeof picked === 'string') onCommit(picked);
    } catch {
      /* dialog unavailable — the text field remains editable */
    }
  }
</script>

<div class="pathrow">
  <input
    bind:this={el}
    class="input"
    class:invalid
    type="text"
    aria-label={label}
    aria-invalid={invalid}
    placeholder="Built-in default"
    onblur={() => onCommit(el?.value ?? '')}
    onkeydown={onKeydown}
  />
  {#if tauri}
    <button type="button" class="btn btn-ghost btn-sm" onclick={choose}>Choose…</button>
  {/if}
</div>

<style>
  .pathrow {
    display: flex;
    align-items: center;
    gap: var(--space-2);
    min-width: 0;
  }
  .input {
    min-width: 200px;
  }
</style>
