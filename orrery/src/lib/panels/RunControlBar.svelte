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
  import { settingsStore } from '../stores/settings.svelte';

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
  // Replay has no engine to drive — control verbs (Start/Brake/Stop/Resume/Restart/Cancel)
  // would just no-op there, so the whole button set is withheld; playback controls live in
  // TransportBar instead. Status narration (below) stays — it reflects real reduced state.
  const showControls = $derived(sessionStore.transportKind !== 'replay');

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

  // ✦ Create & start: consume the one-shot intent the Tuning Console parked on the session
  // store, but only once a transport has ACTUALLY mounted (the System tree renders before
  // mountLoop resolves, so at first render there is nothing to control yet). Always consume —
  // a replay mount must drop the intent, not save it for a later System — and fire through
  // fire('start') so a failed auto-start gets the same pending/slow/stalled/error treatment
  // as a hand-clicked ✦ Start.
  $effect(() => {
    if (!sessionStore.autostartPending || !sessionStore.transportKind) return;
    const live = sessionStore.transportKind !== 'replay';
    sessionStore.consumeAutostart();
    if (live && !running && !banked && !failed && !pending) void fire('start');
  });

  // Restart fresh discards the crashed run and starts over from the merge-base — a
  // destructive path, so gate it behind the native confirm when the user has that on
  // (settings.general.confirmDestructive; native confirm is the house pattern, cf.
  // SettingsOverlay/AiByokPanel). Plain Start never routes through here, so it never confirms.
  function restartFresh() {
    if (
      settingsStore.data.general.confirmDestructive &&
      typeof window !== 'undefined' &&
      !window.confirm('Restart fresh? This discards the crashed run and starts the loop over.')
    ) {
      return;
    }
    fire('start');
  }

  onDestroy(clearTimers);
</script>

<div class="control" role="group" aria-label="Run control">
  {#if showControls && !running && !banked && !failed}
    <button
      class="btn btn-primary btn-md"
      class:working={pending === 'start'}
      aria-label="Start the loop"
      disabled={pending === 'start'}
      onclick={() => fire('start')}>{pending === 'start' ? '✦ starting…' : '✦ Start'}</button
    >
  {/if}

  <!-- a crashed loop NEVER shows a bare "Start" — it's honest about what happened
       and offers the real recovery paths: resume the banked checkpoint (if one
       exists) and/or start over. -->
  {#if showControls && failed}
    {#if canResume}
      <button
        class="btn btn-primary btn-md"
        class:working={pending === 'resume'}
        aria-label="Resume from checkpoint — re-engage the last banked tooth"
        disabled={pending !== null}
        onclick={() => fire('resume')}
        >{pending === 'resume' ? '↻ resuming…' : '↻ Resume from checkpoint'}</button
      >
    {/if}
    <!-- when a Resume path exists it leads (primary); Restart fresh is then the
         secondary choice (ghost) — same action, just visually quieter so Resume
         wins. With no checkpoint at all, Restart fresh IS the primary action. -->
    <button
      class="btn btn-md {canResume ? 'btn-ghost' : 'btn-primary'}"
      class:working={pending === 'start'}
      aria-label="Restart fresh — start the loop over, discarding the crashed run"
      disabled={pending !== null}
      onclick={restartFresh}
      >{pending === 'start' ? '✦ restarting…' : '✦ Restart fresh'}</button
    >
  {/if}

  {#if showControls && running && !failed}
    <button
      class="btn btn-ghost btn-md"
      aria-label="Brake · phase — stop at the next phase boundary"
      disabled={effectiveStop != null}
      onclick={() => fire('stop:phase')}
    >
      Brake · phase
    </button>
    <button
      class="btn btn-ghost btn-md"
      aria-label="Brake · story — stop at the next story boundary"
      disabled={effectiveStop != null}
      onclick={() => fire('stop:story')}
    >
      Brake · story
    </button>
    <!-- the most urgent brake — visually distinct (danger, the one hue besides
         warn that survives M4). Still cooperative (a STOP file, not a kill):
         the honest tooltip says so. Stays clickable to ESCALATE from an
         already-pending phase/story brake; only disables once a stop:now
         itself is in flight. -->
    <button
      class="btn btn-danger btn-md stopnow"
      class:working={effectiveStop === 'now'}
      aria-label="Stop now — request an immediate stop; a running agent call finishes first"
      title="Stops at the engine's next check — a running agent call finishes first."
      disabled={effectiveStop === 'now'}
      onclick={() => fire('stop:now')}
      >{effectiveStop === 'now' ? '⛔ stopping…' : '⛔ Stop now'}</button
    >
  {/if}

  {#if showControls && effectiveStop}
    <button class="btn btn-ghost btn-md" aria-label="Cancel brake — cancel the pending brake" onclick={() => fire('cancel-stop')}
      >Cancel brake</button
    >
  {/if}

  {#if showControls && !failed && (banked || s.run.resumeCmd)}
    <button
      class="btn btn-primary btn-md"
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
    /* placed by the System dock's single bottom bar (+page.svelte merges this
       with TransportBar into one full-width dock — plan §M4.3/M4.5). M4.5:
       this component no longer draws its own pill-card chrome (background/
       border/backdrop-filter/padding, max-width) — the dock container supplies
       that now; this is just the button row's internal layout, wrapping on
       narrow widths. Buttons themselves are the shared `.btn` system
       (primitives.css) — see the variant classes in the markup above. */
    display: flex;
    align-items: center;
    flex-wrap: wrap;
    gap: var(--space-2);
  }
  /* a start/resume in flight: keep the primary button lit and gently pulsing so
     the click clearly registered while the engine cold-starts. White breathe —
     Start/Resume are `.btn-primary` (monochrome) now, not an alert, so the glow
     is em-hi rather than the old amber. Uses the shared `breathe` keyframe
     (primitives.css) — one attention grammar app-wide. */
  .btn.working {
    --glow: var(--em-hi);
    --breathe-r: 12px;
    animation: breathe 1.2s ease-in-out infinite;
  }
  /* stop:now in flight — the destructive glow (err-red, not the primary white),
     so the danger button never pulses the wrong hue while "stopping…" */
  .stopnow.working {
    --glow: var(--status-err-core);
    animation: breathe 1.2s ease-in-out infinite;
  }
  .pending {
    font-size: var(--text-xs);
    letter-spacing: 0.06em;
  }
  /* M4.5 monochrome sweep: cooperative process narration (braking/igniting/
     banked) is not an alert — the user asked for it, nothing needs their
     attention — so it goes grayscale. Only genuine failures (crashed/
     stalled/a rejected control call) stay err-red below. Was an opacity-blink
     (brakePulse) before that; the same glow-breathe as everything else now,
     just a smaller radius for inline text. */
  .pending.braking {
    color: var(--em-mid);
    --glow: var(--em-hi);
    --breathe-r: 8px;
    animation: breathe 1.4s ease-in-out infinite;
  }
  .pending.igniting {
    color: var(--em-mid);
    --glow: var(--em-hi);
    --breathe-r: 8px;
    animation: breathe 1.4s ease-in-out infinite;
  }
  /* gave up waiting for the engine: a genuine failure to start → err-red, no
     pulse (it's a settled state, not in-flight) */
  .pending.igniting.stalled {
    color: var(--status-err-core);
    animation: none;
  }
  .pending.ember { color: var(--em-mid); opacity: 0.8; }
  .pending.crashed { color: var(--status-err-core); }
  .pending.failed {
    /* a rejected control call (e.g. AlreadyRunning) → a genuine error, err-red */
    color: var(--status-err-core);
    max-width: 320px;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  /* reduced-motion: no pulsing (urgency reads from text, not blink) */
  @media (prefers-reduced-motion: reduce) {
    :global(:root:not([data-motion='full'])) .pending.braking,
    :global(:root:not([data-motion='full'])) .pending.igniting { animation: none; }
    :global(:root:not([data-motion='full'])) .btn.working,
    :global(:root:not([data-motion='full'])) .stopnow.working { animation: none; }
  }
  /* mirrors the media block above, for the user-forced reduced-motion setting */
  :global(:root[data-motion='reduced']) .pending.braking,
  :global(:root[data-motion='reduced']) .pending.igniting { animation: none; }
  :global(:root[data-motion='reduced']) .btn.working,
  :global(:root[data-motion='reduced']) .stopnow.working { animation: none; }
</style>
