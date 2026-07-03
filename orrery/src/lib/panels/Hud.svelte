<script lang="ts">
  // HUD overlay — glance-first instrument readout. Status pill (the four
  // not-running rest-states + running), cumUsd (tabular), current item, quota
  // countdown. Reads the runes store; numbers are tabular for honesty.

  import { runStore } from '../stores/run.svelte';
  import { sessionStore } from '../stores/session.svelte';
  import { fmtClock, fmtDuration, fmtRelative } from '../timefmt';
  import type { SixPhase } from '../types';

  const s = $derived(runStore.state);

  // ── phase strip (wave U2 Task 2): the retired 6-gear Mechanism restated the
  // six phases as spinning gears; this compact strip keeps the same six-step
  // shape (discover → assemble → execute → verify → persist → decide) as a
  // row of tiny dots + the active phase name in plain text, tinted by the
  // active model's spectral color (matches --spectral-* in tokens.css — the
  // same mapping the Mechanism used).
  const PHASES: { key: SixPhase; label: string }[] = [
    { key: 'discover', label: 'discover' },
    { key: 'assemble', label: 'assemble' },
    { key: 'execute', label: 'execute' },
    { key: 'verify', label: 'verify' },
    { key: 'persist', label: 'persist' },
    { key: 'decide', label: 'decide' },
  ];
  const activePhaseIdx = $derived(PHASES.findIndex((p) => p.key === s.phase.sixPhase));

  type Pill = { label: string; cls: string; sub?: string };
  // quota resume sub-text, shared by the quota-frost restState and the transient quota-wait
  // status (before restState settles) — "+ reset time if available".
  function quotaSub(): string | undefined {
    const left = runStore.quotaSecondsLeft;
    return left != null ? `resumes in ${fmtCountdown(left)}` : undefined;
  }
  const pill = $derived.by<Pill>((): Pill => {
    const rest = s.run.restState;
    if (rest === 'failed-dark') return { label: 'FAILED', cls: 'failed' };
    if (rest === 'certified-done') return { label: 'DONE · VERIFIED', cls: 'done' };
    if (rest === 'stopped-ember')
      return { label: 'PAUSED', cls: 'ember', sub: 'resumable from checkpoint' };
    if (rest === 'quota-frost') return { label: 'QUOTA PAUSE', cls: 'frost', sub: quotaSub() };
    if (rest === 'handoff-beacon') return { label: 'NEEDS YOU', cls: 'beacon' };
    if (s.run.status === 'running') return { label: 'RUNNING', cls: 'running' };
    if (s.run.status === 'error') return { label: 'FAILED', cls: 'failed' };
    if (s.run.status === 'quota-wait') return { label: 'QUOTA PAUSE', cls: 'frost', sub: quotaSub() };
    return { label: 'IDLE', cls: 'idle' };
  });

  function fmtUsd(n: number): string {
    return '$' + n.toFixed(2);
  }
  function fmtCountdown(sec: number | null): string {
    if (sec == null) return '';
    if (sec <= 0) return 'probing…';
    const h = Math.floor(sec / 3600);
    const m = Math.floor((sec % 3600) / 60);
    const ss = sec % 60;
    if (h > 0) return `${h}h ${String(m).padStart(2, '0')}m`;
    if (m > 0) return `${m}m ${String(ss).padStart(2, '0')}s`;
    return `${ss}s`;
  }

  function fmtMins(n: number): string {
    // ~Ym to ceiling; switch to h:mm once it's long enough to be unwieldy in minutes.
    const m = Math.round(n);
    if (m >= 100) return `${Math.floor(m / 60)}h${String(m % 60).padStart(2, '0')}`;
    return `${m}m`;
  }

  const quotaLeft = $derived(runStore.quotaSecondsLeft);
  const horizonPct = $derived(Math.round(runStore.horizonFrac * 100));

  // scrub-safe forecast: only when actively burning toward a meaningful ceiling
  const minsToCeiling = $derived(runStore.minsToCeiling);
  const showForecast = $derived(minsToCeiling != null && runStore.ceilingUsd > 0);

  // model spectral chip (always paired with the text label, never hue-only)
  const model = $derived(s.phase.model);

  // cache activity (hide entirely when cold + no hits)
  const cacheActive = $derived(s.cache.hitRatio > 0 || s.cache.warm);
  const cachePct = $derived(Math.round(s.cache.hitRatio * 100));

  // "N ring"/"N rings" — proper pluralization (irregular nouns need an explicit plural form).
  function pl(n: number, singular: string, plural: string = singular + 's'): string {
    return `${n} ${n === 1 ? singular : plural}`;
  }

  // ── trust chip (wave U1 Task 2, re-themed wave U2 Task 3): the product's core trust
  // signal — an agent CLAIMING a pass vs an independent verifier CONFIRMING it —
  // promoted from a tiny dashed/solid planet ring to a compact text chip next to the
  // current item. null when there's nothing to report yet (no gate/verdict seen for
  // the current item). The retired Observatory "lighthouse" used to be the only signal
  // that an audit was in flight/pending (runStore.auditTargetKey); that meaning now
  // lives here as the 'verifying' state, so the claimed-but-unaudited item still reads
  // as "being watched", not just statically unverified.
  type TrustChip = { label: string; glyph: string; cls: 'verified' | 'verifying' } | null;
  const trustChip = $derived.by<TrustChip>((): TrustChip => {
    const cur = runStore.current;
    if (!cur) return null;
    if (cur.certified) return { label: 'VERIFIED', glyph: '✓', cls: 'verified' };
    if (cur.gate?.green || cur.status === 'review')
      return { label: 'verifying…', glyph: '◌', cls: 'verifying' };
    return null;
  });

  // ── wall-clock anchors (Task 4): run.startedAt / run.lastEventAt. A coarse 30s ticker (not a
  // 60fps one — this is a text readout, not motion) so "running 2h 12m" advances live.
  let nowTick = $state(Date.now());
  $effect(() => {
    const id = setInterval(() => {
      nowTick = Date.now();
    }, 30000);
    return () => clearInterval(id);
  });
  const timeline = $derived.by<string | null>((): string | null => {
    if (s.run.status === 'running') {
      const clock = fmtClock(s.run.startedAt);
      if (!clock) return null;
      // Replay has no live wall-clock — nowTick (real Date.now()) against a fixture's frozen
      // event timestamps would read as e.g. "running 336h 41m" on a 14-day-old fixture.
      // Both startedAt and lastEventAt are event-time (reduce.ts), so use their own span
      // instead of the real clock.
      if (sessionStore.transportKind === 'replay') {
        if (!s.run.lastEventAt) return `started ${clock}`;
        const dur = fmtDuration(Date.parse(s.run.lastEventAt) - Date.parse(s.run.startedAt!));
        return `started ${clock} · running ${dur}`;
      }
      const dur = fmtDuration(nowTick - Date.parse(s.run.startedAt!));
      return `started ${clock} · running ${dur}`;
    }
    const rel = fmtRelative(s.run.lastEventAt, nowTick);
    return rel ? `ended ${rel}` : null;
  });
</script>

<div class="hud panel panel-tier-a">
  <div class="row top">
    <span class="status-pill {pill.cls}">
      {#if pill.cls === 'done'}
        <span class="seal" aria-hidden="true">✓</span>
      {:else}
        <span class="dot"></span>
      {/if}{pill.label}
    </span>
    {#if model}
      <span class="model-chip {model}">{model}</span>
    {/if}
  </div>
  {#if pill.sub}
    <div class="pill-sub">{pill.sub}</div>
  {/if}
  {#if s.phase.sixPhase}
    <!-- compact phase strip (wave U2 Task 2, replaces the retired 6-gear Mechanism):
         six tiny dots trace discover→assemble→execute→verify→persist→decide, the
         active one tinted by the model's spectral color; the name reads in plain
         text next to it (e.g. "● execute"). Static — no spinning. -->
    <div class="phasestrip" aria-label="phase {activePhaseIdx + 1} of 6: {s.phase.sixPhase}">
      <span class="pdots" aria-hidden="true">
        {#each PHASES as p, i (p.key)}
          <span class="pdot {i === activePhaseIdx ? 'active ' + model : ''}"></span>
        {/each}
      </span>
      <span class="pname mono">{s.phase.sixPhase}{s.phase.label ? ' · ' + s.phase.label : ''}</span>
    </div>
  {/if}
  {#if timeline}
    <div class="timeline mono">{timeline}</div>
  {/if}

  <div class="cost">
    <span class="num big">{fmtUsd(s.run.cumUsd)}</span>
    <span class="label">cum spend</span>
    {#if runStore.horizonVisible}
      <span class="horizon {horizonPct >= 100 ? 'crit' : horizonPct >= 80 ? 'warn' : ''}">
        horizon {horizonPct}%
      </span>
    {/if}
  </div>

  {#if showForecast}
    <div class="forecast">
      <span class="num">{fmtUsd(runStore.usdRemaining)}</span>
      <span>left · ~</span>
      <span class="num">{fmtMins(minsToCeiling!)}</span>
      <span>to ceiling</span>
    </div>
  {/if}

  {#if s.currentItem}
    <div class="current">
      <span class="label">current</span>
      <span class="key mono">{s.currentItem}</span>
      {#if runStore.current?.gate}
        {@const g = runStore.current.gate}
        <span class="gate num">
          {g.pass}/{g.total} {g.green ? 'green' : 'red'}
        </span>
      {/if}
      {#if trustChip}
        <span class="trust {trustChip.cls}"
          ><span aria-hidden="true">{trustChip.glyph}</span> {trustChip.label}</span
        >
      {/if}
    </div>
  {/if}

  {#if quotaLeft != null}
    <div class="quota">
      <span class="label"
        >{s.quota.resetType === 'weekly' ? 'quota pause (weekly)' : 'quota pause'} · resumes in</span
      >
      <span class="num">{fmtCountdown(quotaLeft)}</span>
      {#if s.quota.probe > 0}
        <span class="probe num">probe #{s.quota.probe}</span>
      {/if}
    </div>
  {/if}

  <div class="meta mono">
    <span>{pl(Object.keys(s.items).length, 'body', 'bodies')}</span>
    <span>·</span>
    <span>{pl(Object.keys(s.groups).length, 'ring')}</span>
    <span>·</span>
    <span>{pl(s.events, 'event')}</span>
    {#if s.cost.ratePerMin > 0}
      <span>·</span>
      <span>{fmtUsd(s.cost.ratePerMin)}/min</span>
    {/if}
    {#if cacheActive}
      <span>·</span>
      <span class="cache">↻ cache <span class="num">{cachePct}%</span>{#if s.cache.warm}<span class="warm" title="warm">•</span>{/if}</span>
    {/if}
  </div>
</div>

<style>
  .hud {
    /* wave U2 Task 1: docked at the top of the left rail — the grid places it,
       this is internal styling only now. M4.4: Tier A, the screen's only elevated
       rail surface — `.panel .panel-tier-a` (primitives.css) supply the raised --n3
       background + hairline + radius + padding; this rule only adds the layout this
       component needs on top. */
    width: 100%;
    box-sizing: border-box;
    display: flex;
    flex-direction: column;
    gap: var(--space-2);
    pointer-events: none;
    user-select: none;
  }
  .row.top {
    display: flex;
    align-items: center;
    gap: var(--space-2);
    flex-wrap: wrap;
  }
  /* named .status-pill (not .pill) to avoid colliding with the shared .pill primitive
     (primitives.css, the navbar/ignite-fab chip shape) — this is HUD's own status badge,
     unrelated to that chip shape and never positioned/blurred like it. */
  /* M4.4: the status word is one of the two loudest elements on the whole screen
     (the other is the spend figure below) — a real scale jump off the old --text-xs
     badge size, so it reads before anything else in the rail. */
  .status-pill {
    display: inline-flex;
    align-items: center;
    gap: var(--space-2);
    padding: var(--space-2) var(--space-4);
    border-radius: var(--radius-pill);
    font-size: var(--text-lg);
    font-weight: 600;
    letter-spacing: 0.06em;
    border: 1px solid transparent;
  }
  .status-pill .dot {
    width: 7px;
    height: 7px;
    border-radius: 50%;
    background: currentColor;
  }
  .status-pill .seal {
    font-size: var(--text-md);
    line-height: 1;
  }
  /* M4.1/M4.4 monochrome-by-default: running/done/paused/idle carry no hue at all —
     status-run-core/status-ok-core already resolve to --em-hi and status-idle-core to
     --em-low (tokens.css M4.1 remap), so these three rules are already grayscale;
     kept on the semantic --status-* tokens (not raw --em-*) so the token STRUCTURE
     stays consistent with the rest of the status system. ONLY failed (crashed) and
     beacon (handoff) below keep real chroma — the app's two genuine alert colors. */
  .status-pill.running {
    color: var(--status-run-core);
    border-color: color-mix(in srgb, var(--status-run-core) 40%, transparent);
    background: color-mix(in srgb, var(--status-run-base) 16%, transparent);
  }
  .status-pill.running .dot {
    /* the one breathing element in the calm-state set — a slow white glow marks
       "alive and working", not an alert; shares the app's one attention keyframe. */
    --glow: var(--status-run-core);
    --breathe-r: 8px;
    animation: breathe 2.4s var(--ease-standard) infinite;
  }
  .status-pill.done {
    color: var(--status-ok-core);
    border-color: color-mix(in srgb, var(--status-ok-core) 45%, transparent);
    background: color-mix(in srgb, var(--status-ok-base) 16%, transparent);
  }
  /* paused (resumable-from-checkpoint) is a calm rest state, not an alert — M4.1
     owner decision moved it off its old warn-amber treatment onto the same
     monochrome --status-idle-* family as plain idle. */
  .status-pill.ember {
    color: var(--status-idle-core);
    border-color: var(--hairline);
  }
  /* quota-frost: M4.1 owner decision reclassified quota-pause as a needs-attention
     state, so it now shares the warn-amber family with handoff/beacon below (was its
     own literal --frost blue). */
  .status-pill.frost {
    color: var(--status-warn-core);
    border-color: color-mix(in srgb, var(--status-warn-core) 45%, transparent);
    background: color-mix(in srgb, var(--status-warn-base) 16%, transparent);
  }
  /* handoff/"needs you" — M4.1: the app's alert palette is red (failed/crashed) and
     amber (needs-you/handoff/quota); beacon is a needs-you state, not a crash, so it
     moved off --status-err-* onto --status-warn-* here. */
  .status-pill.beacon {
    color: var(--status-warn-core);
    /* the loudest state. Urgency reads as a slow breathing GLOW, never an opacity
       blink; the static border+shadow stays high-contrast so reduced-motion
       (which freezes the animation) never makes the state disappear. Now consumes
       the shared `breathe` keyframe (primitives.css) instead of a bespoke one — one
       attention grammar app-wide (plan §1 principle 5). */
    border-color: var(--status-warn-core);
    background: color-mix(in srgb, var(--status-warn-base) 20%, transparent);
    --glow: var(--status-warn-core);
    --breathe-r: 14px;
    animation: breathe 2.2s var(--ease-standard) infinite;
  }
  /* the crashed state — the app's other surviving hue (red), but STEADY (no
     breathing). failed-dark reads as dim/dead (no glow), not an urgent call to act
     right now. */
  .status-pill.failed {
    color: var(--status-err-core);
    border-color: var(--status-err-core);
    background: color-mix(in srgb, var(--status-err-base) 18%, transparent);
  }
  .status-pill.idle {
    color: var(--status-idle-core);
    border-color: var(--hairline);
  }
  /* compact phase strip (wave U2 Task 2) — six static dots + the plain-text phase
     name, one line. The active dot is tinted by the model's spectral color; the
     rest stay a dim hairline gray. No motion — a glance-first readout, not a gauge. */
  .phasestrip {
    display: flex;
    align-items: center;
    gap: var(--space-2);
    margin-top: -2px;
  }
  .pdots {
    display: inline-flex;
    align-items: center;
    gap: var(--space-1);
  }
  /* M4.4: grayscale — the active dot is just the brightest tier (--em-hi), not the
     model's spectral tint (that distinction still lives in the model chip below). */
  .pdot {
    width: 5px;
    height: 5px;
    border-radius: 50%;
    background: var(--em-faint);
  }
  .pdot.active {
    width: 6px;
    height: 6px;
    background: var(--em-hi);
  }
  .pname {
    font-size: var(--text-xs);
    color: var(--text-dim);
    letter-spacing: 0.04em;
  }
  /* rest-state sub-text (e.g. "resumable from checkpoint") — quiet, secondary to the pill */
  .pill-sub {
    font-size: var(--text-2xs);
    color: var(--text-meta);
    letter-spacing: 0.02em;
    margin-top: calc(var(--space-1) * -1);
  }
  /* wall-clock anchors (Task 4): "started HH:MM · running 2h 12m" / "ended 5m ago" —
     M4.4: a timestamp, so it recedes to --em-faint, the dimmest tier. */
  .timeline {
    font-size: var(--text-2xs);
    color: var(--em-faint);
    letter-spacing: 0.02em;
    margin-top: calc(var(--space-1) * -1);
  }
  /* model spectral chip — heat color is decorative; the text label always carries
     the meaning (status never by hue alone). */
  .model-chip {
    font-size: var(--text-2xs);
    text-transform: lowercase;
    letter-spacing: 0.06em;
    /* 1px vertical pad has no --space-* equivalent (scale floors at 4px) on a chip
       this small; left literal rather than distorting its proportions. */
    padding: 1px var(--space-2);
    border-radius: var(--radius-pill);
    border: 1px solid currentColor;
    line-height: 1.4;
  }
  .model-chip.haiku {
    color: var(--spectral-haiku);
  }
  .model-chip.sonnet {
    color: var(--spectral-sonnet);
  }
  .model-chip.opus {
    color: var(--spectral-opus);
  }
  .cost {
    display: flex;
    align-items: baseline;
    gap: var(--space-2);
    flex-wrap: wrap;
  }
  /* M4.4: the loudest figure in the rail — --text-display already gave it scale; --em-hi
     (was --starlight, a near-identical literal) puts it on the same emphasis system as
     everything else instead of a one-off hex. */
  .big {
    font-size: var(--text-display);
    font-weight: 600;
    color: var(--em-hi);
    line-height: 1;
  }
  /* CUM SPEND / CURRENT labels — the quietest text in the block (--em-faint), so the
     values they introduce read as the loud part. */
  .label {
    font-size: var(--text-2xs);
    text-transform: uppercase;
    letter-spacing: 0.14em;
    color: var(--em-faint);
  }
  .horizon {
    /* below 80% of the ceiling is a plain readout, not a warning — pinned cross-wave
       contract (tokens.css): neutral em tier here, amber only kicks in at .warn (80–99%),
       red at .crit (≥100%). */
    font-size: var(--text-xs);
    color: var(--em-mid);
    font-family: var(--font-mono);
  }
  .horizon.warn { color: var(--horizon-rose); }
  .horizon.crit { color: var(--crimson); }
  /* calm cost forecast — a quiet readout, not an alarm */
  .forecast {
    display: flex;
    align-items: baseline;
    gap: var(--space-1);
    font-size: var(--text-2xs);
    letter-spacing: 0.04em;
    color: var(--text-meta);
    margin-top: calc(var(--space-1) * -1);
  }
  .forecast .num {
    color: var(--text-dim);
    font-size: var(--text-xs);
  }
  .current,
  .quota {
    display: flex;
    align-items: center;
    gap: var(--space-2);
    flex-wrap: wrap;
    font-size: var(--text-sm);
  }
  /* M4.4: the current story name — a secondary-but-still-bright anchor (--em-hi), one
     scale step down from the spend figure (--text-md, not --text-display). */
  .key {
    color: var(--em-hi);
    font-size: var(--text-md);
    /* a long story id (e.g. a generated slug) must never blow out the HUD's fixed rail
       width — shrink within the flex row and ellipsize rather than overflowing. */
    flex: 1 1 auto;
    min-width: 0;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  /* M4.4: "gate count --em-mid" — a plain readout now; the pass/fail count already
     spells out "green"/"red" in words (never-hue-alone), so the chip doesn't need to
     also carry it in color. Kept out of the alert palette on purpose: a per-item gate
     result isn't the same class of event as a crashed/handoff run. */
  .gate {
    color: var(--em-mid);
  }
  /* trust chip — the claimed-vs-verified signal, promoted to text. Auditor-white,
     gently pulsing = actively being audited (was the Observatory lighthouse's job,
     wave U2 Task 3); green (filled) = an independent verifier confirmed it. Never
     hue-alone: the glyph (◌ / ✓) and the label text both carry the meaning too. */
  .trust {
    display: inline-flex;
    align-items: center;
    gap: var(--space-1);
    padding: 1px var(--space-2);
    border-radius: var(--radius-pill);
    font-size: var(--text-2xs);
    letter-spacing: 0.08em;
    border: 1px solid currentColor;
  }
  /* claimed-but-unaudited — M4.1: this is a genuine attention state ("claimed,
     not yet independently verified"), so it's one of the two chips that keeps chroma;
     warn-amber, same family as handoff/quota. */
  .trust.verifying {
    color: var(--status-warn-core);
    animation: verifyPulse 2.2s ease-in-out infinite;
  }
  @keyframes verifyPulse {
    0%, 100% { opacity: 1; }
    50% { opacity: 0.55; }
  }
  /* certified — independently confirmed, the calm/resolved end state: --em-hi + the
     ✓ seal glyph, no hue (M4.1 "success = pure monochrome"). */
  .trust.verified {
    color: var(--em-hi);
    background: color-mix(in srgb, var(--em-hi) 10%, transparent);
  }
  /* quota countdown — part of the same needs-attention family as the frost pill above. */
  .quota .num { color: var(--status-warn-core); }
  .probe.num {
    color: var(--text-meta);
    font-size: var(--text-2xs);
  }
  .cache {
    display: inline-flex;
    align-items: baseline;
    gap: var(--space-1);
    color: var(--cache-teal);
  }
  .cache .num {
    color: var(--cache-teal);
    font-size: var(--text-2xs);
  }
  .cache .warm {
    color: var(--cache-teal);
    margin-left: 1px;
  }
  .meta {
    display: flex;
    gap: var(--space-1);
    font-size: var(--text-2xs);
    color: var(--em-faint);
    flex-wrap: wrap;
  }

  /* phone: a compact HUD that fits a 390px column — drop the meta row, shrink
     the headline (the grid already makes it full-width). */
  @media (max-width: 640px) {
    .hud {
      padding: var(--space-3);
      gap: var(--space-2);
    }
    .big {
      font-size: var(--text-xl);
    }
    .meta {
      display: none;
    }
    /* the forecast is an at-a-glance desktop nicety; drop it so the compact
       phone HUD never overflows (cache badge already rides in the hidden meta). */
    .forecast {
      display: none;
    }
    /* wall-clock line is a nicety too — drop it on the compact phone HUD */
    .timeline {
      display: none;
    }
  }
</style>
