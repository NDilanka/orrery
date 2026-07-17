<script lang="ts">
  // TextField — a text <input> on the shared .input primitive. Same uncontrolled-draft +
  // commit-on-blur/Enter + revert-on-Escape contract as NumberField; SettingRow validates.
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
</script>

<input
  bind:this={el}
  class="input"
  class:invalid
  type="text"
  aria-label={label}
  aria-invalid={invalid}
  onblur={() => onCommit(el?.value ?? '')}
  onkeydown={onKeydown}
/>

<style>
  .input {
    min-width: 200px;
  }
</style>
