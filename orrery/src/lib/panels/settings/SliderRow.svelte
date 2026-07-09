<script lang="ts">
  // SliderRow — the shared .slider.thumb-brass range input + a tabular value caption
  // (à la TuningConsole's dial → caption). Commits on input. No registry setting uses
  // control:'slider' today, but SettingRow dispatches to it so the primitive is complete.
  let {
    value,
    min = 0,
    max = 100,
    step = 1,
    unit,
    label,
    onChange,
  }: {
    value: number;
    min?: number;
    max?: number;
    step?: number;
    unit?: string;
    label: string;
    onChange: (v: number) => void;
  } = $props();
</script>

<div class="sliderrow">
  <input
    class="slider thumb-brass"
    type="range"
    {min}
    {max}
    {step}
    {value}
    aria-label={label}
    oninput={(e) => onChange(Number(e.currentTarget.value))}
  />
  <span class="cap num">{value}{unit ? ` ${unit}` : ''}</span>
</div>

<style>
  .sliderrow {
    display: flex;
    align-items: center;
    gap: var(--space-3);
    min-width: 180px;
  }
  .slider {
    flex: 1;
    min-width: 0;
    height: 3px;
    appearance: none;
    border-radius: 2px;
    background: var(--surface-raised);
  }
  .cap {
    flex: none;
    min-width: 4ch;
    text-align: right;
    font-size: var(--text-xs);
    color: var(--em-mid);
  }
</style>
