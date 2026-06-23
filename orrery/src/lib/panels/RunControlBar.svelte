<script lang="ts">
  // Run control bar — Start / Stop(phase|story) / Cancel / Resume (B10). Calls the
  // transport's control() method. In dev replay these no-op gracefully (logged);
  // in the real Tauri app they invoke start_loop / stop_loop / cancel_stop /
  // resume_loop (§6). The mechanism is BRAKED, never killed: a stop coasts to the
  // next safe tooth, banks to an ember, and Resume re-engages the same tooth.
  // Reflects run.stopPending and run.restState live.

  import { onDestroy } from 'svelte';
  import { runStore } from '../stores/run.svelte';

  let { control }: { control: (action: string) => void | Promise<void> } = $props();

  const s = $derived(runStore.state);
  const running = $derived(s.run.status === 'running' || s.run.status === 'quota-wait');
  const stopPending = $derived(s.run.stopPending);
  const rest = $derived(s.run.restState);
  // banked ember (you stopped it) or there is a resume command → can reignite
  const banked = $derived(rest === 'stopped-ember' || s.run.status === 'stopped');

  // Optimistic brake feedback. stop_loop writes a STOP file, but the watcher only re-reduces on
  // new LOG lines — so during a long silent phase the reduced `stopPending` (read from that file)
  // would not surface until the engine emits its next event (minutes later), making Brake read as
  // "nothing happened". We reflect the request the instant it's clicked and reconcile with the
  // real reduced value; `effectiveStop` is the union the UI renders.
  //   'phase'|'story' → optimistically braking · 'cancel' → optimistically cleared · null → trust
  // the reduced value. Masks `stopPending` in BOTH directions until reality catches up.
  let stopOverride = $state<'phase' | 'story' | 'cancel' | null>(null);
  const effectiveStop = $derived(stopOverride === 'cancel' ? null : (stopOverride ?? stopPending));
  // Drop the optimism once reality matches it — or the run is no longer running at all (stopped,
  // banked, handoff, error, quota), so a phantom "stopping…/Cancel" can never stick.
  $effect(() => {
    if (!stopOverride) return;
    if (!running) stopOverride = null;
    else if (stopOverride === 'cancel' && stopPending == null) stopOverride = null;
    else if (stopOverride !== 'cancel' && stopPending === stopOverride) stopOverride = null;
  });

  // Surface control failures instead of swallowing them: a failed start/resume (e.g. a
  // missing loop.json, an engine that isn't on PATH, or an AlreadyRunning guard) would
  // otherwise reject silently and read as "nothing happened".
  let error = $state<string | null>(null);

  // In-flight feedback. invoke('start_loop') resolves the instant the engine is SPAWNED, but
  // the run only visibly "starts" once that process writes its first log event (a cold start
  // can take a few seconds: git preflight, gate baseline). Without a pending state the click
  // looks inert. We show "igniting…" until the run goes running, escalate to a slow note, and
  // finally GIVE UP — so a crash-on-start (engine spawns then dies before any log event, e.g.
  // the cp1252 bug) can never strand the button as a permanent disabled "igniting…".
  let pending = $state<null | 'start' | 'resume'>(null);
  let phase = $state<'spawning' | 'slow' | 'stalled' | null>(null);
  let slowTimer: ReturnType<typeof setTimeout> | null = null;
  let giveUpTimer: ReturnType<typeof setTimeout> | null = null;

  function clearTimers() {
    if (slowTimer) {
      clearTimeout(slowTimer);
      slowTimer = null;
    }
    if (giveUpTimer) {
      clearTimeout(giveUpTimer);
      giveUpTimer = null;
    }
  }
  function resetPending() {
    pending = null;
    phase = null;
    clearTimers();
  }

  // The run going live clears the indicator entirely (success). Note: we do NOT treat a
  // stopped/banked status as failure here — that is the STARTING state for a Reignite — so
  // the give-up timer below is what catches a spawn that never reaches running.
  $effect(() => {
    if (running && (pending || phase)) resetPending();
  });

  async function fire(action: string) {
    error = null;
    const startish = action === 'start' || action === 'resume';
    const stopish = action === 'stop:phase' || action === 'stop:story' || action === 'cancel-stop';
    const prevOverride = stopOverride;
    if (startish) {
      resetPending();
      pending = action as 'start' | 'resume';
      phase = 'spawning';
      slowTimer = setTimeout(() => {
        if (pending) phase = 'slow';
      }, 8000);
      // Give-up ceiling: a real cold start (git checkout of the merge-base + a full baseline gate)
      // can run well past a minute before the engine's first event lands, so wait generously
      // before declaring a stall — this only catches an engine that never starts at all.
      giveUpTimer = setTimeout(() => {
        if (pending) {
          pending = null;
          phase = 'stalled';
          clearTimers();
        }
      }, 120000);
    } else if (stopish) {
      stopOverride =
        action === 'cancel-stop' ? 'cancel' : action === 'stop:phase' ? 'phase' : 'story';
    }
    try {
      await control(action);
    } catch (e) {
      error = e instanceof Error ? e.message : String(e);
      if (startish) resetPending();
      if (stopish) stopOverride = prevOverride; // the brake/cancel didn't take — restore prior state
    }
  }

  onDestroy(clearTimers);
</script>

<div class="control" role="group" aria-label="Run control">
  {#if !running && !banked}
    <button
      class="btn ignite"
      class:working={pending === 'start'}
      aria-label="Ignite — start the loop"
      disabled={pending === 'start'}
      onclick={() => fire('start')}>{pending === 'start' ? '✦ igniting…' : '✦ Ignite'}</button
    >
  {/if}

  {#if running}
    <button
      class="btn stop"
      aria-label="Brake at next phase boundary"
      disabled={effectiveStop != null}
      onclick={() => fire('stop:phase')}
    >
      Brake · phase
    </button>
    <button
      class="btn stop"
      aria-label="Brake at next story boundary"
      disabled={effectiveStop != null}
      onclick={() => fire('stop:story')}
    >
      Brake · story
    </button>
  {/if}

  {#if effectiveStop}
    <button class="btn cancel" aria-label="Cancel the pending brake" onclick={() => fire('cancel-stop')}
      >Cancel brake</button
    >
  {/if}

  {#if banked || s.run.resumeCmd}
    <button
      class="btn resume"
      class:working={pending === 'resume'}
      aria-label="Reignite — resume the loop"
      disabled={pending === 'resume'}
      onclick={() => fire('resume')}>{pending === 'resume' ? '↻ reigniting…' : '↻ Reignite'}</button
    >
  {/if}

  {#if effectiveStop}
    <span class="pending mono braking" role="status"
      >⏛ stopping at next {effectiveStop}{stopPending == null ? ' (requested)' : ''}</span
    >
  {:else if banked}
    <span class="pending mono ember" role="status"
      >banked ember · parked at {s.run.stage ?? 'last checkpoint'}</span
    >
  {/if}

  {#if phase && !error}
    <span
      class="pending mono igniting"
      class:stalled={phase === 'stalled'}
      role="status"
      >{phase === 'spawning'
        ? 'spawning the engine…'
        : phase === 'slow'
          ? 'engine slow to respond — check the run log if this persists'
          : 'the engine never reported in — check the run log (it may have failed to start)'}</span
    >
  {/if}

  {#if error}
    <span class="pending mono failed" role="alert" title={error}>⚠ {error}</span>
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
  /* a start/resume in flight: keep the button lit (not greyed) and gently pulsing so the
     click clearly registered while the engine cold-starts. */
  .btn.working {
    opacity: 1;
    animation: workPulse 1.2s ease-in-out infinite;
  }
  .pending {
    font-size: 11px;
    letter-spacing: 0.06em;
  }
  .pending.braking {
    color: var(--ember);
    animation: brakePulse 1.4s ease-in-out infinite;
  }
  .pending.igniting {
    color: var(--amber);
    animation: brakePulse 1.4s ease-in-out infinite;
  }
  /* gave up waiting for the engine: steady ember, no pulse (it's a quiet warning, not in-flight) */
  .pending.igniting.stalled {
    color: var(--ember);
    animation: none;
  }
  .pending.ember { color: var(--ember); opacity: 0.8; }
  .pending.failed {
    color: var(--ember);
    max-width: 320px;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  @keyframes brakePulse {
    0%, 100% { opacity: 1; }
    50% { opacity: 0.5; }
  }
  @keyframes workPulse {
    0%, 100% { box-shadow: 0 0 0 0 transparent; }
    50% { box-shadow: 0 0 12px 0 color-mix(in srgb, var(--amber) 45%, transparent); }
  }
  /* reduced-motion: no pulsing (urgency reads from text, not blink) */
  @media (prefers-reduced-motion: reduce) {
    .pending.braking,
    .pending.igniting { animation: none; }
    .btn.working { animation: none; }
  }
</style>
