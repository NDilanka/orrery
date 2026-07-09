<script lang="ts">
  // SelectField — a native <select> on the shared .input primitive. Commits on change.
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
</script>

<select
  class="input select"
  aria-label={label}
  onchange={(e) => onChange(e.currentTarget.value)}
>
  {#each options as o (o.value)}
    <option value={o.value} selected={o.value === value}>{o.label}</option>
  {/each}
</select>

<style>
  .select {
    width: auto;
    min-width: 140px;
    cursor: pointer;
  }
</style>
