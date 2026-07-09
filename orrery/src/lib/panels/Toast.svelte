<script lang="ts">
  // Toast — a small, self-contained, MONOCHROME chrome toast (design law: no --scene-*,
  // no red/amber; those are reserved for genuine alerts). Bottom-center, auto-dismisses
  // after ~6s, manually dismissable, aria-live="polite", reduced-motion-safe entrance.
  //
  // Its one wired source today is the quota-resume signal: alertStore records the fact
  // (settings-free) as `lastQuotaResume`; THIS component decides whether to surface it
  // (settings.notifications.quotaResumeToast). The internal `show()` is generic, so other
  // toast uses can be added later without a framework.

  import { onDestroy } from 'svelte';
  import { alertStore } from '../stores/alerts.svelte';
  import { settingsStore } from '../stores/settings.svelte';
  import { uiStore } from '../stores/ui.svelte';

  const AUTO_DISMISS_MS = 6000;

  let toast = $state<{ id: number; message: string } | null>(null);
  let timer: ReturnType<typeof setTimeout> | null = null;
  // last quota-resume seq we've consumed — edge-detects so a re-render never re-toasts.
  let lastSeq = -1;

  function clearTimer() {
    if (timer) {
      clearTimeout(timer);
      timer = null;
    }
  }

  function show(message: string) {
    clearTimer();
    toast = { id: Date.now(), message };
    timer = setTimeout(dismiss, AUTO_DISMISS_MS);
  }

  function dismiss() {
    clearTimer();
    toast = null;
  }

  // Watch the store's quota-resume signal. `seq` monotonically increments, so comparing it
  // against lastSeq edge-detects a fresh resume even for a repeat loopId. We consume the seq
  // before the settings check so a toggled-off toast doesn't queue up to fire retroactively.
  $effect(() => {
    const sig = alertStore.lastQuotaResume;
    if (!sig || sig.seq === lastSeq) return;
    lastSeq = sig.seq;
    if (!settingsStore.data.notifications.quotaResumeToast) return;
    show(`${sig.loopId} resumed after quota wait`);
  });

  onDestroy(clearTimer);
</script>

{#if toast}
  <div class="toast-wrap" aria-live="polite">
    <div class="toast" class:reduced={uiStore.reducedMotion} role="status">
      <span class="msg">{toast.message}</span>
      <button
        class="dismiss btn btn-ghost btn-icon"
        aria-label="dismiss notification"
        onclick={dismiss}>✕</button
      >
    </div>
  </div>
{/if}

<style>
  .toast-wrap {
    position: fixed;
    left: 50%;
    bottom: calc(var(--space-4, 16px) + env(safe-area-inset-bottom, 0px));
    transform: translateX(-50%);
    z-index: 48; /* above chrome/modals (--z-chrome 20 … --z-popover 45), below nothing else */
    pointer-events: none; /* only the toast itself is interactive */
    max-width: min(92vw, 30rem);
  }
  .toast {
    pointer-events: auto;
    display: flex;
    align-items: center;
    gap: var(--space-3);
    padding: 10px 12px 10px var(--space-3);
    /* MONOCHROME chrome only — void surface + hairline, never a scene/alert hue. */
    background: color-mix(in srgb, var(--em-hi) 6%, var(--void-2));
    border: 1px solid var(--hairline);
    border-radius: var(--radius);
    box-shadow: 0 8px 24px oklch(0 0 0 / 35%);
    backdrop-filter: blur(8px);
    animation: toastIn var(--dur-mid) var(--ease-out);
  }
  .toast.reduced {
    animation: none;
  }
  @keyframes toastIn {
    from {
      opacity: 0;
      transform: translateY(8px);
    }
    to {
      opacity: 1;
      transform: translateY(0);
    }
  }
  @media (prefers-reduced-motion: reduce) {
    :global(:root:not([data-motion='full'])) .toast {
      animation: none;
    }
  }
  /* mirrors the media block above, for the user-forced reduced-motion setting */
  :global(:root[data-motion='reduced']) .toast {
    animation: none;
  }
  .msg {
    flex: 1;
    min-width: 0;
    font-family: var(--font-grotesk);
    font-size: var(--text-sm);
    font-weight: 600;
    letter-spacing: 0.01em;
    color: var(--em-hi);
  }
  .dismiss {
    flex: none;
  }
</style>
