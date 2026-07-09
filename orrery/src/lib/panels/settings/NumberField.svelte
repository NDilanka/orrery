<script lang="ts">
  // NumberField — a numeric <input> on the shared .input primitive. Uncontrolled draft: the
  // element holds the in-progress text; SettingRow validates on commit (blur / Enter) and only
  // then persists. Escape reverts to the stored value. `invalid` tints the border red.
  let {
    value,
    min,
    max,
    step,
    unit,
    label,
    invalid = false,
    onCommit,
    onCancel,
  }: {
    value: number;
    min?: number;
    max?: number;
    step?: number;
    unit?: string;
    label: string;
    invalid?: boolean;
    onCommit: (raw: string) => void;
    onCancel: () => void;
  } = $props();

  let el = $state<HTMLInputElement | null>(null);

  // Sync the field to the stored value whenever it changes externally (reset / import / valid
  // commit), but never clobber what the user is mid-typing (skip while focused).
  $effect(() => {
    const v = value == null ? '' : String(value);
    if (el && document.activeElement !== el) el.value = v;
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

<div class="numwrap">
  <input
    bind:this={el}
    class="input"
    class:invalid
    type="number"
    {min}
    {max}
    {step}
    aria-label={label}
    aria-invalid={invalid}
    onblur={() => onCommit(el?.value ?? '')}
    onkeydown={onKeydown}
  />
  {#if unit}<span class="unit">{unit}</span>{/if}
</div>

<style>
  .numwrap {
    display: flex;
    align-items: center;
    gap: var(--space-2);
    min-width: 0;
  }
  .input {
    width: 96px;
    text-align: right;
  }
  .unit {
    flex: none;
    font-size: var(--text-xs);
    color: var(--em-low);
  }
</style>
