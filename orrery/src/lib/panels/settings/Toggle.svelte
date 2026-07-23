<script lang="ts">
  // Toggle — a boolean switch styled off --em-* (chrome monochrome). role="switch".
  // Commits immediately (settings are instant-apply); SettingRow owns the store write.
  let {
    value,
    label,
    onChange,
  }: { value: boolean; label: string; onChange: (v: boolean) => void } = $props();
</script>

<button
  type="button"
  role="switch"
  aria-checked={value}
  aria-label={label}
  class="switch"
  class:on={value}
  onclick={() => onChange(!value)}
>
  <span class="knob" aria-hidden="true"></span>
</button>

<style>
  .switch {
    --sw-w: 34px;
    --sw-h: 18px;
    position: relative;
    flex: none;
    width: var(--sw-w);
    height: var(--sw-h);
    padding: 0;
    border: 1px solid var(--hairline);
    border-radius: var(--radius-pill);
    background: var(--surface-void);
    cursor: pointer;
    transition:
      background-color var(--dur-feedback) var(--ease-standard),
      border-color var(--dur-feedback) var(--ease-standard);
  }
  .switch.on {
    background: var(--em-hi);
    border-color: var(--em-hi);
  }
  .knob {
    position: absolute;
    top: 50%;
    left: 2px;
    width: 12px;
    height: 12px;
    border-radius: 50%;
    background: var(--em-mid);
    transform: translateY(-50%);
    transition:
      transform var(--dur-feedback) var(--ease-standard),
      background-color var(--dur-feedback) var(--ease-standard);
  }
  .switch.on .knob {
    /* dark knob on the bright fill — highest contrast in a hueless palette */
    background: var(--surface-void);
    transform: translate(calc(var(--sw-w) - 16px), -50%);
  }
  /* Cobalt skin — the ON state is the indigo affordance (a white knob on the fill) */
  :global(:root[data-skin='cobalt']) .switch.on {
    background: var(--primary);
    border-color: var(--primary);
  }
  :global(:root[data-skin='cobalt']) .switch.on .knob {
    background: var(--primary-foreground);
  }
  @media (prefers-reduced-motion: reduce) {
    :global(:root:not([data-motion='full'])) .switch,
    :global(:root:not([data-motion='full'])) .knob {
      transition: none;
    }
  }
  /* mirrors the media block above, for the user-forced reduced-motion setting */
  :global(:root[data-motion='reduced']) .switch,
  :global(:root[data-motion='reduced']) .knob {
    transition: none;
  }
</style>
