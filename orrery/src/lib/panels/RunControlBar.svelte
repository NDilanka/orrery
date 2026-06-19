<script lang="ts">
  // Run control bar — Start / Stop(phase|story) / Cancel / Resume (B10). Calls the
  // transport's control() method. In dev replay these no-op gracefully (logged);
  // in the real Tauri app they invoke start_loop / stop_loop / cancel_stop /
  // resume_loop (§6). The mechanism is BRAKED, never killed: a stop coasts to the
  // next safe tooth, banks to an ember, and Resume re-engages the same tooth.
  // Reflects run.stopPending and run.restState live.

  import { runStore } from '../stores/run.svelte';

  let { control }: { control: (action: string) => void | Promise<void> } = $props();

  const s = $derived(runStore.state);
  const running = $derived(s.run.status === 'running' || s.run.status === 'quota-wait');
  const stopPending = $derived(s.run.stopPending);
  const rest = $derived(s.run.restState);
  // banked ember (you stopped it) or there is a resume command → can reignite
  const banked = $derived(rest === 'stopped-ember' || s.run.status === 'stopped');

  async function fire(action: string) {
    await control(action);
  }
</script>

<div class="control">
  {#if !running && !banked}
    <button class="btn ignite" onclick={() => fire('start')}>✦ Ignite</button>
  {/if}

  {#if running}
    <button class="btn stop" disabled={stopPending === 'phase'} onclick={() => fire('stop:phase')}>
      Brake · phase
    </button>
    <button class="btn stop" disabled={stopPending === 'story'} onclick={() => fire('stop:story')}>
      Brake · story
    </button>
  {/if}

  {#if stopPending}
    <button class="btn cancel" onclick={() => fire('cancel-stop')}>Cancel brake</button>
  {/if}

  {#if banked || s.run.resumeCmd}
    <button class="btn resume" onclick={() => fire('resume')}>↻ Reignite</button>
  {/if}

  {#if stopPending}
    <span class="pending mono braking">⏛ stopping at next {stopPending}</span>
  {:else if banked}
    <span class="pending mono ember">banked ember · parked at {s.run.stage ?? 'tooth'}</span>
  {/if}
</div>

<style>
  .control {
    position: absolute;
    bottom: 180px;
    left: 50%;
    transform: translateX(-50%);
    display: flex;
    align-items: center;
    gap: 9px;
    padding: 9px 12px;
    background: var(--panel);
    border: 1px solid var(--panel-edge);
    border-radius: var(--radius-pill);
    backdrop-filter: blur(8px);
  }
  .btn {
    font-family: var(--font-grotesk);
    font-size: 12px;
    font-weight: 600;
    letter-spacing: 0.04em;
    padding: 7px 15px;
    border-radius: var(--radius-pill);
    border: 1px solid var(--hairline);
    background: var(--void-3);
    color: var(--starlight);
    cursor: pointer;
    transition: border-color 0.2s, background 0.2s, transform 0.1s;
  }
  .btn:hover:not(:disabled) {
    border-color: var(--brass);
    transform: translateY(-1px);
  }
  .btn:active:not(:disabled) { transform: translateY(0); }
  .btn:disabled { opacity: 0.4; cursor: default; }
  .btn.ignite {
    color: var(--amber);
    border-color: color-mix(in srgb, var(--amber) 45%, transparent);
    background: color-mix(in srgb, var(--amber) 8%, transparent);
  }
  .btn.stop {
    color: var(--ember);
    border-color: color-mix(in srgb, var(--ember) 35%, transparent);
  }
  .btn.resume {
    color: var(--plasma-green);
    border-color: color-mix(in srgb, var(--plasma-green) 40%, transparent);
  }
  .btn.cancel { color: var(--text-dim); }
  .pending {
    font-size: 11px;
    letter-spacing: 0.06em;
  }
  .pending.braking {
    color: var(--ember);
    animation: brakePulse 1.4s ease-in-out infinite;
  }
  .pending.ember { color: var(--ember); opacity: 0.8; }
  @keyframes brakePulse {
    0%, 100% { opacity: 1; }
    50% { opacity: 0.5; }
  }
  /* reduced-motion: no pulsing (urgency reads from text, not blink) */
  @media (prefers-reduced-motion: reduce) {
    .pending.braking { animation: none; }
  }
</style>
