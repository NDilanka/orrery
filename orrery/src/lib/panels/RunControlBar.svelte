<script lang="ts">
  // Run control bar — Start / Stop(phase|story) / Resume. Calls the transport's
  // control() method. In dev replay these no-op gracefully (logged); in the real
  // Tauri app they invoke start_loop / stop_loop / resume_loop (§6). The
  // mechanism is braked, never killed.

  import { runStore } from '../stores/run.svelte';

  let { control }: { control: (action: string) => void | Promise<void> } = $props();

  const s = $derived(runStore.state);
  const running = $derived(s.run.status === 'running' || s.run.status === 'quota-wait');
  const stopPending = $derived(s.run.stopPending);

  async function fire(action: string) {
    await control(action);
  }
</script>

<div class="control">
  {#if !running}
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

  {#if !running && (s.run.restState === 'stopped-ember' || s.run.resumeCmd)}
    <button class="btn resume" onclick={() => fire('resume')}>Reignite</button>
  {/if}

  {#if stopPending}
    <span class="pending mono">brake → {stopPending}</span>
  {/if}
</div>

<style>
  .control {
    position: absolute;
    bottom: 18px;
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
    color: var(--ember);
    letter-spacing: 0.06em;
  }
</style>
