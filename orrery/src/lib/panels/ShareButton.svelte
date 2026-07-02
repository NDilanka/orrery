<script lang="ts">
  // ShareButton (wave U4 Task 1) — the Cosmos top-bar "share to phone" affordance. A trigger
  // pill + an anchored popover: starting the LAN server (src-tauri/src/lan.rs, invoked via
  // shareStore) and showing the exact url+token a phone on the same Wi-Fi needs, as both text
  // and a QR. See stores/share.svelte.ts for the hasTauri()-gated start/stop + the dev-preview
  // fallback (an obviously-fake token so this is screenshot-able without a real server).
  //
  // The QR is drawn on a plain <canvas> (not innerHTML/SVG-string injection) with the library's
  // isDark()/getModuleCount() directly — deliberately NOT the app's dark palette: cameras need a
  // light quiet zone + dark-on-light contrast to scan reliably regardless of the UI's theme.

  import qrcode from 'qrcode-generator';
  import { shareStore } from '../stores/share.svelte';
  import { focusTrap } from '../actions/focusTrap';

  let rootEl = $state<HTMLDivElement | null>(null);
  let canvasEl = $state<HTMLCanvasElement | null>(null);
  let copied = $state(false);
  let copyTimer: ReturnType<typeof setTimeout> | null = null;

  const url = $derived(shareStore.shareUrl);

  // Draw whenever the share url changes (a fresh start → a fresh token → a fresh code).
  $effect(() => {
    const u = url;
    const canvas = canvasEl;
    if (!u || !canvas) return;
    const qr = qrcode(0, 'M');
    qr.addData(u);
    qr.make();
    const modules = qr.getModuleCount();
    const QUIET = 4; // modules — the QR spec's minimum quiet zone
    const SIZE = 216; // px, fixed canvas size
    const cell = SIZE / (modules + QUIET * 2);
    const ctx = canvas.getContext('2d');
    if (!ctx) return;
    canvas.width = SIZE;
    canvas.height = SIZE;
    ctx.fillStyle = '#ffffff';
    ctx.fillRect(0, 0, SIZE, SIZE);
    ctx.fillStyle = '#0b0b12';
    for (let row = 0; row < modules; row++) {
      for (let col = 0; col < modules; col++) {
        if (qr.isDark(row, col)) {
          ctx.fillRect((col + QUIET) * cell, (row + QUIET) * cell, Math.ceil(cell), Math.ceil(cell));
        }
      }
    }
  });

  // click-outside-to-close (the popover isn't a full scrim modal — it's anchored chrome)
  $effect(() => {
    if (!shareStore.open) return;
    function onDocPointer(e: PointerEvent) {
      if (rootEl && !rootEl.contains(e.target as Node)) shareStore.closePopover();
    }
    document.addEventListener('pointerdown', onDocPointer);
    return () => document.removeEventListener('pointerdown', onDocPointer);
  });

  async function copyUrl() {
    if (!url) return;
    try {
      await navigator.clipboard.writeText(url);
    } catch {
      /* clipboard permission denied — the field is still selectable text */
    }
    copied = true;
    if (copyTimer) clearTimeout(copyTimer);
    copyTimer = setTimeout(() => (copied = false), 1600);
  }

  function toggle() {
    if (shareStore.open) shareStore.closePopover();
    else void shareStore.openPopover();
  }

  function selectAll(e: Event) {
    (e.target as HTMLInputElement).select();
  }
</script>

<div class="share" bind:this={rootEl}>
  <button
    class="trigger"
    class:active={shareStore.status === 'active'}
    aria-haspopup="dialog"
    aria-expanded={shareStore.open}
    onclick={toggle}
  >
    <span aria-hidden="true">⇪</span> Share
    {#if shareStore.status === 'active'}<span class="dot" aria-hidden="true"></span>{/if}
  </button>

  {#if shareStore.open}
    <div
      class="popover floating-card"
      role="dialog"
      aria-label="Share to phone"
      tabindex="-1"
      use:focusTrap={{ onClose: () => shareStore.closePopover() }}
    >
      <header class="phead">
        <span class="ptitle">SHARE TO PHONE</span>
        <button class="pclose" aria-label="close" onclick={() => shareStore.closePopover()}>✕</button>
      </header>

      {#if shareStore.status === 'starting'}
        <p class="pstate mono">starting the LAN server…</p>
      {:else if shareStore.status === 'error'}
        <div class="perror">
          <p class="pstate mono">
            couldn't start the server{shareStore.error ? `: ${shareStore.error}` : ''}
          </p>
          <button class="retry" onclick={() => shareStore.start()}>retry</button>
        </div>
      {:else if shareStore.status === 'active' && url}
        {#if shareStore.simulated}
          <p class="simnote mono">dev preview — no Tauri backend; this link isn't reachable</p>
        {/if}
        <div class="qrwrap">
          <canvas bind:this={canvasEl} class="qr" aria-label="QR code for {url}"></canvas>
        </div>
        <div class="urlrow">
          <input class="urlfield mono" type="text" readonly value={url} onclick={selectAll} />
          <button class="copy" onclick={copyUrl}>{copied ? 'copied' : 'copy'}</button>
        </div>
        <p class="note">
          Anyone with this link on the <strong>same Wi-Fi</strong> can watch the loop; the token
          is required to drive it (start / stop / answer). The link stops working the moment you
          stop sharing.
        </p>
        <button class="stop" onclick={() => shareStore.stop()}>stop sharing</button>
      {/if}
    </div>
  {/if}
</div>

<style>
  .share {
    position: relative;
  }
  .trigger {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    font-family: var(--font-grotesk);
    font-size: var(--text-xs);
    font-weight: 600;
    letter-spacing: 0.04em;
    padding: 6px 13px;
    border-radius: var(--radius-pill);
    border: 1px solid var(--hairline);
    background: var(--void-3);
    color: var(--starlight);
    cursor: pointer;
    transition: border-color var(--dur-fast) var(--ease-standard),
      transform var(--dur-feedback) var(--ease-standard);
  }
  .trigger:hover {
    border-color: var(--brass);
    transform: translateY(-1px);
  }
  .trigger.active {
    border-color: color-mix(in srgb, var(--plasma-cyan) 45%, transparent);
    color: var(--plasma-cyan);
  }
  .trigger .dot {
    width: 6px;
    height: 6px;
    border-radius: 50%;
    background: var(--plasma-cyan);
    box-shadow: 0 0 6px color-mix(in srgb, var(--plasma-cyan) 70%, transparent);
  }

  .popover {
    position: absolute;
    top: calc(100% + 10px);
    right: 0;
    width: min(300px, 88vw);
    display: flex;
    flex-direction: column;
    gap: var(--space-3);
    padding: var(--space-4);
    backdrop-filter: blur(10px);
    z-index: var(--z-popover);
  }
  .phead {
    display: flex;
    align-items: center;
    justify-content: space-between;
  }
  .ptitle {
    /* unified header pattern (M1.2): 11px caps-spaced --text-xs label, matching
       DecisionSheet's meta row and HelpOverlay's section labels. */
    font-size: var(--text-xs);
    letter-spacing: 0.14em;
    color: var(--text-meta);
  }
  .pclose {
    background: transparent;
    border: 1px solid var(--hairline);
    color: var(--text-dim);
    border-radius: var(--radius-pill);
    width: 22px;
    height: 22px;
    font-size: var(--text-2xs);
    cursor: pointer;
    transition: border-color var(--dur-feedback) var(--ease-standard),
      color var(--dur-feedback) var(--ease-standard);
  }
  .pclose:hover {
    border-color: var(--crimson);
    color: var(--crimson);
  }
  .pstate {
    margin: 0;
    font-size: var(--text-xs);
    color: var(--text-dim);
  }
  .perror {
    display: flex;
    flex-direction: column;
    gap: var(--space-2);
    align-items: flex-start;
  }
  .perror .pstate {
    color: var(--horizon-rose);
  }
  .retry {
    font-family: var(--font-grotesk);
    font-size: var(--text-xs);
    font-weight: 600;
    padding: 5px 12px;
    border-radius: var(--radius-pill);
    border: 1px solid var(--hairline);
    background: var(--surface-2);
    color: var(--starlight);
    cursor: pointer;
    transition: border-color var(--dur-feedback) var(--ease-standard);
  }
  .retry:hover {
    border-color: var(--brass);
  }
  .simnote {
    margin: 0;
    font-size: var(--text-2xs);
    color: var(--amber);
    padding: 6px 9px;
    border: 1px dashed color-mix(in srgb, var(--amber) 45%, transparent);
    border-radius: var(--radius-sm);
  }
  .qrwrap {
    align-self: center;
    padding: 10px;
    /* CORRECT to keep literal — QR needs a light quiet zone + dark-on-light contrast
       to scan reliably regardless of the app's dark theme (see file header note).
       Only the container's radius (an exact --radius match) moves onto the scale. */
    background: #ffffff;
    border-radius: var(--radius);
    line-height: 0;
  }
  .qr {
    display: block;
    width: 180px;
    height: 180px;
  }
  .urlrow {
    display: flex;
    gap: var(--space-2);
  }
  .urlfield {
    flex: 1;
    min-width: 0;
    background: var(--void-3);
    border: 1px solid var(--hairline);
    border-radius: var(--radius-sm);
    color: var(--starlight);
    /* 10.5px was one of the audit's near-duplicate micro-sizes (#1); collapsed onto
       --text-2xs alongside TransportBar's matching .pos readout. */
    font-size: var(--text-2xs);
    padding: 7px 9px;
  }
  .copy {
    flex: none;
    font-family: var(--font-grotesk);
    font-size: var(--text-xs);
    font-weight: 600;
    padding: 6px 12px;
    border-radius: var(--radius-pill);
    border: 1px solid var(--brass);
    background: color-mix(in srgb, var(--brass) 14%, transparent);
    color: var(--brass);
    cursor: pointer;
    transition: background var(--dur-feedback) var(--ease-standard);
  }
  .copy:hover {
    background: color-mix(in srgb, var(--brass) 24%, transparent);
  }
  .note {
    margin: 0;
    font-size: var(--text-2xs);
    line-height: 1.5;
    color: var(--text-faint);
  }
  .note strong {
    color: var(--text-meta);
  }
  .stop {
    font-family: var(--font-grotesk);
    font-size: var(--text-xs);
    font-weight: 600;
    letter-spacing: 0.04em;
    padding: 8px 14px;
    border-radius: var(--radius-pill);
    border: 1px solid color-mix(in srgb, var(--crimson) 45%, transparent);
    background: color-mix(in srgb, var(--crimson) 10%, transparent);
    color: var(--crimson);
    cursor: pointer;
    transition: background var(--dur-feedback) var(--ease-standard);
  }
  .stop:hover {
    background: color-mix(in srgb, var(--crimson) 20%, transparent);
  }

  @media (max-width: 640px) {
    .popover {
      position: fixed;
      top: auto;
      bottom: 12px;
      left: 12px;
      right: 12px;
      width: auto;
    }
  }
</style>
