<script lang="ts">
  // Segmented — wraps the shared .seg / .seg-item primitive as a single-select radiogroup
  // (used for control:'seg' and any small enum). Roving tabindex + arrow-key nav; selection
  // commits immediately via onChange.
  interface Opt {
    value: string;
    label: string;
  }
  let {
    value,
    options,
    label,
    onChange,
  }: {
    value: string;
    options: Opt[];
    label: string;
    onChange: (v: string) => void;
  } = $props();

  let btns = $state<HTMLButtonElement[]>([]);

  function move(i: number, dir: 1 | -1) {
    const n = (i + dir + options.length) % options.length;
    onChange(options[n].value);
    btns[n]?.focus();
  }
  function onKey(e: KeyboardEvent, i: number) {
    if (e.key === 'ArrowRight' || e.key === 'ArrowDown') {
      e.preventDefault();
      move(i, 1);
    } else if (e.key === 'ArrowLeft' || e.key === 'ArrowUp') {
      e.preventDefault();
      move(i, -1);
    }
  }
</script>

<div class="seg" role="radiogroup" aria-label={label}>
  {#each options as o, i (o.value)}
    <button
      bind:this={btns[i]}
      type="button"
      class="seg-item"
      class:selected={o.value === value}
      role="radio"
      aria-checked={o.value === value}
      tabindex={o.value === value ? 0 : -1}
      onclick={() => onChange(o.value)}
      onkeydown={(e) => onKey(e, i)}
    >
      {o.label}
    </button>
  {/each}
</div>
