<script lang="ts">
  // Run control bar — Start / Stop(phase|story) / Cancel / Resume (B10). Calls the
  // transport's control() method. In dev replay these no-op gracefully (logged);
  // in the real Tauri app they invoke start_loop / stop_loop / cancel_stop /
  // resume_loop (§6). The mechanism is BRAKED, never killed: a stop coasts to the
  // next safe tooth, banks to an ember, and Resume re-engages the same tooth.
  // Reflects run.stopPending and run.restState live.

  import { onDestroy } from 'svelte';
  import { runStore } from '../stores/run.svelte';
  import { sessionStore } from '../stores/session.svelte';

  const s = $derived(runStore.state);
  const running = $derived(s.run.status === 'running' || s.run.status === 'quota-wait');
  const stopPending = $derived(s.run.stopPending);
  const rest = $derived(s.run.restState);
  // paused (you stopped it) or there is a resume command → can Resume
  const banked = $derived(rest === 'stopped-ember' || s.run.status === 'stopped');
  // a GENUINE crash (stop{ok:false}) — never confused with a cooperative brake/ember.
  // failed-dark is authoritative; status==='error' is a defensive fallback in case a
  // non-BMAD adapter ever reaches 'error' without the reducer's restState alongside it.
  const failed = $derived(rest === 'failed-dark' || s.run.status === 'error');
  // does the last checkpoint offer a real resume path?
  const canResume = $derived(!!s.run.resumeCmd);

  // Optimistic brake feedback. stop_loop writes a STOP file, but the watcher only re-reduces on
  // new LOG lines — so during a long silent phase the reduced `stopPending` (read from that file)
  // would not surface until the engine emits its next event (minutes later), making Brake read as
  // "nothing happened". We reflect the request the instant it's clicked and reconcile with the
  // real reduced value; `effectiveStop` is the union the UI renders.
  //   'phase'|'story'|'now' → optimistically braking · 'cancel' → optimistically cleared · null →
  // trust the reduced value. Masks `stopPending` in BOTH directions until reality catches up.
  let stopOverride = $state<'phase' | 'story' | 'now' | 'cancel' | null>(null);
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
  // looks inert. We show "starting…" until the run goes running, escalate to a slow note, and
  // finally GIVE UP — so a crash-on-start (engine spawns then dies before any log event, e.g.
  // the cp1252 bug) can never strand the button as a permanent disabled "starting…".
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
  // stopped/banked status as failure here — that is the STARTING state for a Resume — so
  // the give-up timer below is what catches a spawn that never reaches running.
  $effect(() => {
    if (running && (pending || phase)) resetPending();
  });

  async function fire(action: string) {
    error = null;
    const startish = action === 'start' || action === 'resume';
    const stopish =
      action === 'stop:phase' ||
      action === 'stop:story' ||
      action === 'stop:now' ||
      action === 'cancel-stop';
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
        action === 'cancel-stop'
          ? 'cancel'
          : action === 'stop:phase'
            ? 'phase'
            : action === 'stop:story'
              ? 'story'
              : 'now';
    }
    try {
      await sessionStore.control(action);
    } catch (e) {
      error = e instanceof Error ? e.message : String(e);
      if (startish) resetPending();
      if (stopish) stopOverride = prevOverride; // the brake/cancel didn't take — restore prior state
    }
  }

  onDestroy(clearTimers);
</script>

<div class="control" role="group" aria-label="Run control">
  {#if !running && !banked && !failed}
    <button
      class="btn ignite"
      class:working={pending === 'start'}
      aria-label="Start the loop"
      disabled={pending === 'start'}
      onclick={() => fire('start')}>{pending === 'start' ? '✦ starting…' : '✦ Start'}</button
    >
  {/if}

  <!-- a crashed loop NEVER shows a bare "Start" — it's honest about what happened
       and offers the real recovery paths: resume the banked checkpoint (if one
       exists) and/or start over. -->
  {#if failed}
    {#if canResume}
      <button
        class="btn resume"
        class:working={pending === 'resume'}
        aria-label="Resume from checkpoint — re-engage the last banked tooth"
        disabled={pending !== null}
        onclick={() => fire('resume')}
        >{pending === 'resume' ? '↻ resuming…' : '↻ Resume from checkpoint'}</button
      >
    {/if}
    <button
      class="btn ignite restart"
      class:secondary={canResume}
      class:working={pending === 'start'}
      aria-label="Restart fresh — start the loop over, discarding the crashed run"
      disabled={pending !== null}
      onclick={() => fire('start')}
      >{pending === 'start' ? '✦ restarting…' : '✦ Restart fresh'}</button
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
    <!-- the most urgent brake — visually distinct (danger). Still cooperative (a
         STOP file, not a kill): the honest tooltip says so. Stays clickable to
         ESCALATE from an already-pending phase/story brake; only disables once
         a stop:now itself is in flight. -->
    <button
      class="btn stopnow"
      class:working={effectiveStop === 'now'}
      aria-label="Stop now — request an immediate stop; a running agent call finishes first"
      title="Stops at the engine's next check — a running agent call finishes first."
      disabled={effectiveStop === 'now'}
      onclick={() => fire('stop:now')}
      >{effectiveStop === 'now' ? '⛔ stopping…' : '⛔ Stop now'}</button
    >
  {/if}

  {#if effectiveStop}
    <button class="btn cancel" aria-label="Cancel the pending brake" onclick={() => fire('cancel-stop')}
      >Cancel brake</button
    >
  {/if}

  {#if !failed && (banked || s.run.resumeCmd)}
    <button
      class="btn resume"
      class:working={pending === 'resume'}
      aria-label="Resume the loop"
      disabled={pending === 'resume'}
      onclick={() => fire('resume')}>{pending === 'resume' ? '↻ resuming…' : '↻ Resume'}</button
    >
  {/if}

  {#if effectiveStop}
    <span class="pending mono braking" role="status"
      >{#if effectiveStop === 'now'}⛔ stopping now — finishing the current step{stopPending ==
        null
          ? ' (requested)'
          : ''}{:else}⏛ stopping at next {effectiveStop}{stopPending == null
          ? ' (requested)'
          : ''}{/if}</span
    >
  {:else if failed}
    <span class="pending mono crashed" role="status"
      >crashed{canResume ? ' · a checkpoint is available' : ' · no checkpoint — restart fresh'}</span
    >
  {:else if banked}
    <span class="pending mono ember" role="status"
      >paused · resumable from checkpoint · parked at {s.run.stage ?? 'last checkpoint'}</span
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
    /* placed by the System dock's bottom row (+page.svelte .g-bottom); wraps on narrow widths */
    display: flex;
    align-items: center;
    justify-content: center;
    flex-wrap: wrap;
    gap: var(--space-2);
    max-width: min(620px, 92vw);
    padding: 9px 12px;
    background: var(--panel);
    border: 1px solid var(--panel-edge);
    border-radius: var(--radius);
    backdrop-filter: blur(8px);
  }
  .btn {
    font-family: var(--font-grotesk);
    font-size: var(--text-sm);
    font-weight: 600;
    letter-spacing: 0.04em;
    padding: 7px 15px;
    border-radius: var(--radius-pill);
    border: 1px solid var(--hairline);
    background: var(--void-3);
    color: var(--starlight);
    cursor: pointer;
    /* hover is a +1 surface step (M1.4) — the feedback timing (--dur-feedback) is on the
       surface-changing properties; transform keeps its own slightly slower feel. */
    transition: border-color var(--dur-feedback) var(--ease-standard),
      background var(--dur-feedback) var(--ease-standard),
      transform var(--dur-fast) var(--ease-standard);
  }
  .btn:hover:not(:disabled) {
    border-color: var(--brass);
    background: color-mix(in srgb, var(--void-3) 70%, var(--n4) 30%);
    transform: translateY(-1px);
  }
  .btn:active:not(:disabled) { transform: translateY(0); }
  .btn:disabled { opacity: 0.4; cursor: not-allowed; }
  /* button family (plan §1 + M1.2): "go forward" actions (Start/Restart/Resume) are the
     primary family — solid, tinted fill. Brake/Cancel are secondary — ghost, hairline only,
     no fill (already were). Stop-now is destructive — see .btn.stopnow below. */
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
    /* primary-solid parity with .ignite — Resume is a "go forward" action too. */
    background: color-mix(in srgb, var(--plasma-green) 8%, transparent);
  }
  /* "Restart fresh" alongside a "Resume from checkpoint" reads as the secondary
     choice — same ignite/amber styling (it IS the same start action), just
     visually quieter so Resume leads. */
  .btn.restart.secondary {
    opacity: 0.75;
  }
  .btn.restart.secondary:hover:not(:disabled) {
    opacity: 1;
  }
  .btn.cancel { color: var(--text-dim); }
  /* the loudest, most urgent action — destructive styling, visually distinct from the
     ember brake buttons so it never gets mistaken for a graceful brake. Uses the M0
     two-tier status-err token (core = small/bright text+border) instead of the raw
     --crimson literal — the plan's explicit mapping for destructive actions. */
  .btn.stopnow {
    color: var(--status-err-core);
    border-color: color-mix(in srgb, var(--status-err-core) 55%, transparent);
    background: color-mix(in srgb, var(--status-err-core) 10%, transparent);
  }
  .btn.stopnow:hover:not(:disabled) {
    border-color: var(--status-err-core);
    background: color-mix(in srgb, var(--status-err-core) 20%, transparent);
  }
  /* a start/resume in flight: keep the button lit (not greyed) and gently pulsing so the
     click clearly registered while the engine cold-starts. Uses the shared `breathe`
     keyframe (primitives.css) — one attention grammar app-wide instead of a bespoke
     workPulse/stopPulse pair. */
  .btn.working {
    opacity: 1;
    --glow: var(--amber);
    --breathe-r: 12px;
    animation: breathe 1.2s ease-in-out infinite;
  }
  .pending {
    font-size: var(--text-xs);
    letter-spacing: 0.06em;
  }
  /* was an opacity-blink (brakePulse) — retired per plan §1 ("opacity-blink is retired");
     the same glow-breathe as everything else, just a smaller radius for inline text. */
  .pending.braking {
    color: var(--ember);
    --glow: var(--ember);
    --breathe-r: 8px;
    animation: breathe 1.4s ease-in-out infinite;
  }
  .pending.igniting {
    color: var(--amber);
    --glow: var(--amber);
    --breathe-r: 8px;
    animation: breathe 1.4s ease-in-out infinite;
  }
  /* gave up waiting for the engine: steady ember, no pulse (it's a quiet warning, not in-flight) */
  .pending.igniting.stalled {
    color: var(--ember);
    animation: none;
  }
  .pending.ember { color: var(--ember); opacity: 0.8; }
  .pending.crashed { color: var(--status-err-core); }
  .pending.failed {
    color: var(--ember);
    max-width: 320px;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  /* stop:now in flight — the destructive glow (not the generic amber), so the danger button
     never pulses the wrong hue while "stopping…" */
  .btn.stopnow.working {
    --glow: var(--status-err-core);
    animation: breathe 1.2s ease-in-out infinite;
  }
  /* reduced-motion: no pulsing (urgency reads from text, not blink) */
  @media (prefers-reduced-motion: reduce) {
    .pending.braking,
    .pending.igniting { animation: none; }
    .btn.working,
    .btn.stopnow.working { animation: none; }
  }
</style>
