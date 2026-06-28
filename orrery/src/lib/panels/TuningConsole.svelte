<script lang="ts">
  // THE TUNING CONSOLE (A5) — "Set Orbital Parameters". You don't fill a form;
  // you calibrate an instrument, same obsidian-and-brass language as the running
  // orrery (plan §4). The shape:
  //
  //   BLUEPRINT  — a preset star-chart (Grind · Sprint · Explore · Custom) that
  //                ships smart defaults for the full engine config (PROTOCOL §7).
  //   3 DIALS    — three coordinated forces. Each dial is one slider that moves a
  //                BUNDLE of engine params at once (Ambition⟷Thrift ·
  //                Patience⟷Fussiness · Autonomy⟷Company).
  //   DESTINATION— the human-authored heart: acceptance criteria (→ the ghost
  //                target constellation) + ordered gate stages (→ the airlock).
  //   DRAWERS    — collapsed advanced overrides (Models · Economy · Gate ·
  //                Resilience · Quota · Tools · Q&A). Each shows its blueprint
  //                default; any change lights an Amber override-dot + offers reset.
  //   PREVIEW    — a live mini-orrery summary: where the cost horizon lands,
  //                est. $, audit on/off, strikes. "Preview night" fast-forwards a
  //                simulated run before any real quota is spent.
  //   IGNITE     — composes the loop.json and calls create_loop, then returns to
  //                the Cosmos where the new star-system appears.
  //
  // In dev (no Tauri) everything renders + validates; create no-ops gracefully.

  import { onMount } from 'svelte';
  import {
    BLUEPRINTS,
    BLUEPRINT_ORDER,
    composeEngine,
    composeLoopDef,
    projectPreview,
    previewNight,
    validateDraft,
    type BlueprintId,
    type Blueprint,
    type DialState,
    type EngineConfig,
    type GateStageDef,
    type ConsoleInput,
  } from '../blueprints';
  import { cosmosStore } from '../stores/cosmos.svelte';

  let {
    mode = 'create',
    editId = null,
    onClose,
    onCreated,
  }: {
    mode?: 'create' | 'edit';
    editId?: string | null;
    onClose: () => void;
    onCreated?: (id: string, ctx: { mode: 'create' | 'edit'; persisted: boolean }) => void;
  } = $props();

  // ── core console state ─────────────────────────────────────────────────────
  let blueprintId = $state<BlueprintId>('grind');
  const blueprint = $derived<Blueprint>(BLUEPRINTS[blueprintId]);

  let dials = $state<DialState>({ ...BLUEPRINTS.grind.dials });
  let loopId = $state('');
  let loopName = $state('');
  let stateDir = $state('.loop');
  let task = $state('TASK.md');

  // ── pristine-form guard: don't scold an untouched form. The "give it an id"
  // warning stays quiet until the user types/edits or attempts to ignite. ──
  let touched = $state(false);
  function markTouched() {
    touched = true;
  }

  let acceptanceCriteria = $state<string[]>([...BLUEPRINTS.grind.destination.acceptanceCriteria]);
  let gateStages = $state<GateStageDef[]>(
    BLUEPRINTS.grind.destination.gateStages.map((s) => ({ ...s })),
  );

  // drawer overrides: a sparse partial EngineConfig, deep-merged at compose time.
  let overrides = $state<Partial<EngineConfig>>({});
  let openDrawer = $state<string | null>(null);
  let nightOpen = $state(false);
  let busy = $state(false);
  let createError = $state<string | null>(null);

  // ── selecting a blueprint resets the dials + destination seed to its chart ──
  function selectBlueprint(id: BlueprintId) {
    blueprintId = id;
    dials = { ...BLUEPRINTS[id].dials };
    // only re-seed the destination if the user hasn't diverged far (custom keeps theirs)
    if (id !== 'custom') {
      acceptanceCriteria = [...BLUEPRINTS[id].destination.acceptanceCriteria];
      gateStages = BLUEPRINTS[id].destination.gateStages.map((s) => ({ ...s }));
    }
    overrides = {}; // a fresh chart clears prior overrides (the dots reset)
  }

  // ── the composed engine (blueprint + dials + destination), pre-overrides ────
  const composed = $derived<EngineConfig>(
    composeEngine(blueprint, dials, { acceptanceCriteria, gateStages }, task),
  );
  // the final engine the loop.json will carry (composed + drawer overrides)
  const finalEngine = $derived<EngineConfig>(mergeOver(composed, overrides));
  const preview = $derived(projectPreview(finalEngine));
  const night = $derived(previewNight(finalEngine));

  function mergeOver(base: EngineConfig, over: Partial<EngineConfig>): EngineConfig {
    const out = { ...base } as unknown as Record<string, unknown>;
    for (const k of Object.keys(over) as (keyof EngineConfig)[]) {
      const ov = over[k];
      const bv = base[k];
      if (ov && typeof ov === 'object' && !Array.isArray(ov) && bv && typeof bv === 'object') {
        out[k] = { ...(bv as object), ...(ov as object) };
      } else if (ov !== undefined) {
        out[k] = ov as unknown;
      }
    }
    return out as unknown as EngineConfig;
  }

  // ── validation (mirrors the Rust guard) ─────────────────────────────────────
  const validation = $derived(
    validateDraft(
      { id: loopId, name: loopName, acceptanceCriteria, gateStages },
      // editing keeps its own id legal
      cosmosStore.existingIds.filter((id) => id !== editId),
    ),
  );

  // ── override-dot bookkeeping: is a given section overridden? ─────────────────
  function overridden(section: keyof EngineConfig): boolean {
    return overrides[section] !== undefined;
  }
  function setOverride<K extends keyof EngineConfig>(section: K, value: EngineConfig[K]) {
    overrides = { ...overrides, [section]: value };
  }
  function resetOverride(section: keyof EngineConfig) {
    const next = { ...overrides };
    delete next[section];
    overrides = next;
  }

  // ── destination editing ─────────────────────────────────────────────────────
  function addCriterion() {
    acceptanceCriteria = [...acceptanceCriteria, ''];
  }
  function removeCriterion(i: number) {
    acceptanceCriteria = acceptanceCriteria.filter((_, idx) => idx !== i);
  }
  function addStage() {
    gateStages = [...gateStages, { name: '', command: '' }];
  }
  function removeStage(i: number) {
    gateStages = gateStages.filter((_, idx) => idx !== i);
  }

  // ── modal contract: focus move-in / trap / restore (WCAG 2.4.3 + 2.1.2) ─────
  let dialogEl = $state<HTMLDivElement | null>(null);
  let triggerEl: HTMLElement | null = null;

  function focusable(): HTMLElement[] {
    if (!dialogEl) return [];
    return Array.from(
      dialogEl.querySelectorAll<HTMLElement>(
        'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])',
      ),
    ).filter((el) => el.offsetParent !== null || el === document.activeElement);
  }

  // Escape closes, Tab/Shift+Tab wrap inside the console. Bound on the dialog so
  // the .console keydown no longer needs to swallow the event.
  function onDialogKeydown(e: KeyboardEvent) {
    if (e.key === 'Escape') {
      e.preventDefault();
      onClose();
      return;
    }
    if (e.key !== 'Tab') return;
    const items = focusable();
    if (items.length === 0) return;
    const first = items[0];
    const last = items[items.length - 1];
    const active = document.activeElement as HTMLElement | null;
    if (e.shiftKey && (active === first || !dialogEl?.contains(active))) {
      e.preventDefault();
      last.focus();
    } else if (!e.shiftKey && active === last) {
      e.preventDefault();
      first.focus();
    }
  }

  // ── ignite ──────────────────────────────────────────────────────────────────
  async function ignite() {
    if (!validation.ok || busy) {
      touched = true; // surface any latent validation messages on a blocked submit
      return;
    }
    busy = true;
    createError = null;
    try {
      const input: ConsoleInput = {
        id: loopId.trim(),
        name: loopName.trim(),
        blueprint,
        dials,
        destination: {
          acceptanceCriteria: acceptanceCriteria.map((s) => s.trim()).filter(Boolean),
          gateStages: gateStages.filter((s) => s.name.trim() || s.command.trim()),
        },
        stateDir: stateDir.trim() || '.loop',
        task: task.trim() || 'TASK.md',
        engineOverrides: overrides,
      };
      const def = composeLoopDef(input) as unknown as Record<string, unknown>;
      const { id, persisted } = await cosmosStore.createLoop(def, { mode, editId });
      // Pass mode + whether it actually hit disk so the shell only flies into a System for a
      // NEW, persisted loop — a SAVE (edit) or a dev-mode no-op create stays at the Cosmos.
      onCreated?.(id, { mode, persisted });
      onClose();
    } catch (e) {
      createError = e instanceof Error ? e.message : String(e);
    } finally {
      busy = false;
    }
  }

  // a fresh editable id so a new loop opens with a sensible suggestion instead
  // of an instant "give it an id" error. Stays unique against existing systems.
  function friendlyDefaultId(): string {
    const base = 'new-loop';
    const taken = new Set(cosmosStore.existingIds);
    if (!taken.has(base)) return base;
    for (let n = 2; n < 1000; n++) if (!taken.has(`${base}-${n}`)) return `${base}-${n}`;
    return `${base}-${Date.now().toString(36)}`;
  }

  // ── prefill when editing an existing loop ───────────────────────────────────
  onMount(() => {
    // capture the trigger so focus can be restored on close (WCAG 2.4.3)
    triggerEl = document.activeElement as HTMLElement | null;

    if (mode === 'edit' && editId) {
      loopId = editId;
      // load the def async, but keep onMount sync so it can return a teardown
      void (async () => {
        const def = await cosmosStore.loadLoopDef(editId);
        if (def) {
          loopName = String(def.name ?? editId);
          if (typeof def.stateDir === 'string') stateDir = def.stateDir;
          const eng = def.engine as Partial<EngineConfig> | undefined;
          if (eng) {
            if (typeof eng.task === 'string') task = eng.task;
            if (Array.isArray(eng.verify?.contract)) acceptanceCriteria = [...eng.verify.contract];
            if (Array.isArray(eng.gate?.stages)) gateStages = eng.gate.stages.map((s) => ({ ...s }));
            // editing starts from Custom so nothing is silently re-seeded
            blueprintId = 'custom';
            overrides = eng as Partial<EngineConfig>;
          }
        }
      })();
    } else {
      // the friendly default id the comment promised
      loopId = friendlyDefaultId();
    }

    // move focus into the dialog: first focusable field, else the container
    queueMicrotask(() => {
      const items = focusable();
      (items[0] ?? dialogEl)?.focus();
    });

    // restore focus to the element that opened the console on teardown
    return () => triggerEl?.focus?.();
  });

  // human-readable settings for the dial aria-valuetext (screen readers hear the
  // actual config the dial currently maps to, not a bare 0–1 number).
  const ambitionText = $derived(
    `Ambition: ${finalEngine.models.execute}, ${fmtUsd(finalEngine.cost.ceilingUsd)} ceiling, ${finalEngine.stop.maxIters} iters`,
  );
  const patienceText = $derived(
    `Patience: ${finalEngine.verify.strictness} verifier, ${finalEngine.regression.strikeBudget} strikes, plateau K ${finalEngine.decide.plateauK}`,
  );
  const autonomyText = $derived(
    `Autonomy: ${finalEngine.qa.humanInLoop ? 'human-in-loop' : 'overnight-auto'}, ${finalEngine.permissionMode}`,
  );

  const DRAWERS: { key: keyof EngineConfig | 'models' | 'tools'; label: string }[] = [
    { key: 'models', label: 'Models' },
    { key: 'cost', label: 'Economy' },
    { key: 'gate', label: 'Gate' },
    { key: 'regression', label: 'Resilience' },
    { key: 'quota', label: 'Quota' },
    { key: 'allowedTools', label: 'Tools' },
    { key: 'qa', label: 'Q&A' },
  ];

  function fmtUsd(n: number): string {
    return '$' + n.toFixed(2);
  }
  function pct(n: number): string {
    return Math.round(n * 100) + '%';
  }
  function horizonColor(p: number): string {
    if (p >= 1) return 'var(--crimson)';
    if (p >= 0.8) return 'var(--horizon-rose)';
    if (p >= 0.5) return 'var(--amber)';
    return 'var(--plasma-green)';
  }
</script>

<!-- scrim + dialog -->
<div class="scrim" role="presentation" onclick={onClose}>
  <div
    class="console"
    role="dialog"
    aria-modal="true"
    aria-labelledby="tc-title"
    tabindex="-1"
    bind:this={dialogEl}
    onclick={(e) => e.stopPropagation()}
    onkeydown={onDialogKeydown}
  >
    <!-- header -->
    <header class="hdr">
      <div id="tc-title" class="title mono">✦ SET ORBITAL PARAMETERS</div>
      <div class="sub mono">{mode === 'edit' ? `recalibrating ${editId}` : 'new loop'}</div>
      <button class="x" aria-label="close" onclick={onClose}>✕</button>
    </header>

    <!-- identity row -->
    <div class="idrow">
      <label class="field">
        <span class="flab mono">ID</span>
        <input
          class="inp mono"
          placeholder="my-loop"
          bind:value={loopId}
          oninput={markTouched}
          disabled={mode === 'edit'}
        />
      </label>
      <label class="field grow">
        <span class="flab mono">NAME</span>
        <input
          class="inp"
          placeholder="Describe the mission…"
          bind:value={loopName}
          oninput={markTouched}
        />
      </label>
      <label class="field">
        <span class="flab mono">TASK</span>
        <input class="inp mono" bind:value={task} />
      </label>
    </div>

    <!-- blueprint selector -->
    <section class="blueprints">
      <span class="seclab mono">BLUEPRINT</span>
      <div class="bp-row">
        {#each BLUEPRINT_ORDER as id (id)}
          {@const bp = BLUEPRINTS[id]}
          <button
            class="bp {blueprintId === id ? 'on' : ''}"
            onclick={() => selectBlueprint(id)}
            title={bp.tagline}
          >
            <span class="bp-glyph">{bp.glyph}</span>
            <span class="bp-name">{bp.name}</span>
          </button>
        {/each}
      </div>
      <div class="bp-tag mono">{blueprint.tagline}</div>
    </section>

    <div class="cols">
      <!-- LEFT: calibration dials -->
      <section class="panel calib">
        <span class="seclab mono">CALIBRATION</span>

        <!-- All three dials share ONE orientation: the slider value IS the stored
             0–1 force, left label is the 0-pole, right label the 1-pole, and the
             colored fill grows from the left to exactly value% — so the filled
             track visually equals the setting. -->
        <div class="dial">
          <div class="dlabels mono"><span>THRIFT</span><span>AMBITION</span></div>
          <input
            type="range"
            min="0"
            max="1"
            step="0.01"
            value={dials.ambition}
            style="--fill:{dials.ambition * 100}%"
            aria-label="Ambition vs Thrift"
            aria-valuetext={ambitionText}
            aria-describedby="dhint-ambition"
            oninput={(e) => (dials = { ...dials, ambition: +e.currentTarget.value })}
          />
          <div id="dhint-ambition" class="dhint mono">
            {finalEngine.models.execute} · ceiling {fmtUsd(finalEngine.cost.ceilingUsd)} · {finalEngine
              .stop.maxIters} iters
          </div>
        </div>

        <div class="dial">
          <div class="dlabels mono"><span>FUSSY</span><span>PATIENCE</span></div>
          <input
            type="range"
            min="0"
            max="1"
            step="0.01"
            value={dials.patience}
            style="--fill:{dials.patience * 100}%"
            aria-label="Patience vs Fussiness"
            aria-valuetext={patienceText}
            aria-describedby="dhint-patience"
            oninput={(e) => (dials = { ...dials, patience: +e.currentTarget.value })}
          />
          <div id="dhint-patience" class="dhint mono">
            verifier {finalEngine.verify.strictness} · strikes {finalEngine.regression
              .strikeBudget} · plateau K={finalEngine.decide.plateauK}
          </div>
        </div>

        <div class="dial">
          <div class="dlabels mono"><span>COMPANY</span><span>AUTONOMY</span></div>
          <input
            type="range"
            min="0"
            max="1"
            step="0.01"
            value={dials.autonomy}
            style="--fill:{dials.autonomy * 100}%"
            aria-label="Autonomy vs Company"
            aria-valuetext={autonomyText}
            aria-describedby="dhint-autonomy"
            oninput={(e) => (dials = { ...dials, autonomy: +e.currentTarget.value })}
          />
          <div id="dhint-autonomy" class="dhint mono">
            {finalEngine.qa.humanInLoop ? 'human-in-loop' : 'overnight-auto'} · {finalEngine.permissionMode}
          </div>
        </div>
      </section>

      <!-- RIGHT: live preview orrery -->
      <section class="panel preview">
        <span class="seclab mono">PREVIEW ORRERY</span>
        <div class="orrery-mini">
          <svg viewBox="0 0 200 130" class="mini">
            <!-- star -->
            <circle cx="100" cy="65" r="11" fill="var(--starlight)" opacity="0.9" />
            <circle cx="100" cy="65" r="17" fill="none" stroke="var(--brass)" stroke-width="0.6" opacity="0.5" />
            <!-- cost horizon ring (tightens + reddens with est spend) -->
            {#if preview.horizonAtPct >= 0.5}
              <circle
                cx="100"
                cy="65"
                r={48 - Math.min(1, preview.horizonAtPct) * 18}
                fill="none"
                stroke={horizonColor(preview.horizonAtPct)}
                stroke-width="1.2"
                opacity="0.8"
              />
            {/if}
            <!-- ghost target -->
            <circle cx="150" cy="40" r="7" fill="none" stroke="var(--ghost-brass)" stroke-width="1" stroke-dasharray="2 2" />
            <!-- audit lighthouse beam -->
            {#if preview.auditOn}
              <line x1="100" y1="65" x2="150" y2="40" stroke="var(--auditor-white)" stroke-width="0.6" opacity="0.5" />
              <circle cx="40" cy="30" r="3" fill="var(--auditor-white)" opacity="0.8" />
            {/if}
            <!-- AC constellation -->
            {#each acceptanceCriteria.filter((a) => a.trim()) as _ac, i}
              <circle
                cx={150 + Math.cos(i * 1.4) * 14}
                cy={40 + Math.sin(i * 1.4) * 14}
                r="1.6"
                fill="var(--brass)"
                opacity="0.7"
              />
            {/each}
          </svg>
        </div>
        <ul class="pstats mono">
          <li>
            <span>horizon</span>
            <strong style="color:{horizonColor(preview.horizonAtPct)}">
              {pct(preview.horizonAtPct)} of {fmtUsd(preview.ceilingUsd)}
            </strong>
          </li>
          <li><span>est. spend</span><strong>~{fmtUsd(preview.estUsd)}</strong></li>
          <li>
            <span>audit</span><strong class={preview.auditOn ? 'on' : 'off'}>
              {preview.auditOn ? '▷ on' : 'off'}</strong
            >
          </li>
          <li><span>strikes</span><strong>{preview.strikeBudget}</strong></li>
          <li><span>gate stages</span><strong>{preview.stageCount}</strong></li>
          <li><span>AC stars</span><strong>{preview.acCount}</strong></li>
        </ul>
        <button class="night-btn mono" onclick={() => (nightOpen = !nightOpen)}>
          {nightOpen ? '▾' : '▸'} preview night
        </button>
        {#if nightOpen}
          <div class="night">
            <svg viewBox="0 0 200 50" class="night-svg">
              <!-- ceiling line -->
              <line x1="0" y1="6" x2="200" y2="6" stroke="var(--crimson)" stroke-width="0.5" stroke-dasharray="2 2" opacity="0.6" />
              {#if night.series.length > 1}
                {@const maxC = Math.max(night.ceilingUsd, ...night.series.map((s) => s.cum))}
                <polyline
                  points={night.series
                    .map(
                      (s, i) =>
                        `${(i / Math.max(1, night.series.length - 1)) * 196 + 2},${
                          46 - (s.cum / maxC) * 40
                        }`,
                    )
                    .join(' ')}
                  fill="none"
                  stroke={horizonColor(night.landsAtPct)}
                  stroke-width="1.4"
                />
              {/if}
            </svg>
            <div class="night-cap mono">
              lands at {fmtUsd(night.landsAtUsd)} ({pct(night.landsAtPct)})
              {#if night.greenAtIter}· green ~iter {night.greenAtIter}{/if}
              {#if night.hitsCeiling}<span class="warn">· hits ceiling</span>{/if}
            </div>
          </div>
        {/if}
      </section>
    </div>

    <!-- DESTINATION -->
    <section class="panel destination">
      <span class="seclab mono">DESTINATION <em>— describe the finished planet</em></span>
      <div class="dest-grid">
        <div class="ac">
          <div class="dlab mono">AC → ghost ✦</div>
          {#each acceptanceCriteria as _ac, i}
            <div class="ac-row">
              <span class="ac-star">✦</span>
              <input
                class="inp"
                placeholder="e.g. 401 on an expired token"
                bind:value={acceptanceCriteria[i]}
              />
              <button class="mini-x" aria-label="remove" onclick={() => removeCriterion(i)}>✕</button>
            </div>
          {/each}
          <button class="add mono" onclick={addCriterion}>+ add criterion</button>
        </div>
        <div class="gate">
          <div class="dlab mono">GATE (airlock)</div>
          {#each gateStages as _st, i}
            <div class="st-row">
              <input class="inp st-name mono" placeholder="stage" bind:value={gateStages[i].name} />
              <span class="arrow">→</span>
              <input
                class="inp st-cmd mono"
                placeholder="command (e.g. bun test)"
                bind:value={gateStages[i].command}
              />
              <button class="mini-x" aria-label="remove" onclick={() => removeStage(i)}>✕</button>
            </div>
          {/each}
          <button class="add mono" onclick={addStage}>+ add stage</button>
        </div>
      </div>
    </section>

    <!-- ADVANCED DRAWERS -->
    <section class="drawers">
      <div class="drawer-tabs mono">
        {#each DRAWERS as d (d.key)}
          <button
            class="dtab {openDrawer === d.key ? 'on' : ''}"
            onclick={() => (openDrawer = openDrawer === d.key ? null : (d.key as string))}
          >
            {d.label}
            {#if overridden(d.key as keyof EngineConfig)}<span class="odot" title="overridden"></span>{/if}
          </button>
        {/each}
      </div>

      {#if openDrawer}
        <div class="drawer-body">
          {#if openDrawer === 'models'}
            <div class="row">
              {#each ['discover', 'execute', 'judge', 'hard'] as phase}
                <label class="kv">
                  <span class="mono">{phase}</span>
                  <select
                    value={finalEngine.models[phase as keyof EngineConfig['models']]}
                    onchange={(e) =>
                      setOverride('models', {
                        ...finalEngine.models,
                        [phase]: e.currentTarget.value,
                      } as EngineConfig['models'])}
                  >
                    <option>haiku</option>
                    <option>sonnet</option>
                    <option>opus</option>
                  </select>
                </label>
              {/each}
            </div>
            <div class="def mono">blueprint default via dials: {composed.models.execute} / {composed.models.hard}</div>
          {:else if openDrawer === 'cost'}
            <div class="row">
              <label class="kv">
                <span class="mono">ceiling $</span>
                <input
                  class="inp mono"
                  type="number"
                  step="0.5"
                  value={finalEngine.cost.ceilingUsd}
                  onchange={(e) =>
                    setOverride('cost', {
                      ...finalEngine.cost,
                      ceilingUsd: +e.currentTarget.value,
                    })}
                />
              </label>
              <label class="kv">
                <span class="mono">maxIters</span>
                <input
                  class="inp mono"
                  type="number"
                  value={finalEngine.stop.maxIters}
                  onchange={(e) =>
                    setOverride('stop', { ...finalEngine.stop, maxIters: +e.currentTarget.value })}
                />
              </label>
            </div>
            <div class="def mono">blueprint default: {fmtUsd(composed.cost.ceilingUsd)} ceiling · {composed.stop.maxIters} iters</div>
          {:else if openDrawer === 'gate'}
            <label class="kv wide">
              <span class="mono">greenWhen</span>
              <input
                class="inp mono"
                value={finalEngine.gate.greenWhen}
                onchange={(e) =>
                  setOverride('gate', { ...finalEngine.gate, greenWhen: e.currentTarget.value })}
              />
            </label>
            <label class="kv wide">
              <span class="mono">lockGlobs (test-tamper)</span>
              <input
                class="inp mono"
                value={finalEngine.gate.lockGlobs.join(', ')}
                onchange={(e) =>
                  setOverride('gate', {
                    ...finalEngine.gate,
                    lockGlobs: e.currentTarget.value
                      .split(',')
                      .map((s) => s.trim())
                      .filter(Boolean),
                  })}
              />
            </label>
            <div class="def mono">stages edited in the Destination airlock above ({finalEngine.gate.stages.length}).</div>
          {:else if openDrawer === 'regression'}
            <div class="row">
              <label class="kv">
                <span class="mono">strikeBudget</span>
                <input
                  class="inp mono"
                  type="number"
                  value={finalEngine.regression.strikeBudget}
                  onchange={(e) =>
                    setOverride('regression', {
                      ...finalEngine.regression,
                      strikeBudget: +e.currentTarget.value,
                    })}
                />
              </label>
              <label class="kv">
                <span class="mono">plateau K</span>
                <input
                  class="inp mono"
                  type="number"
                  value={finalEngine.decide.plateauK}
                  onchange={(e) =>
                    setOverride('decide', {
                      ...finalEngine.decide,
                      plateauK: +e.currentTarget.value,
                    })}
                />
              </label>
              <label class="kv chk">
                <input
                  type="checkbox"
                  checked={finalEngine.regression.autoRollback}
                  onchange={(e) =>
                    setOverride('regression', {
                      ...finalEngine.regression,
                      autoRollback: e.currentTarget.checked,
                    })}
                />
                <span class="mono">auto-rollback</span>
              </label>
            </div>
          {:else if openDrawer === 'quota'}
            <div class="row">
              <label class="kv chk">
                <input
                  type="checkbox"
                  checked={finalEngine.quota.enabled}
                  onchange={(e) =>
                    setOverride('quota', { ...finalEngine.quota, enabled: e.currentTarget.checked })}
                />
                <span class="mono">survive quota</span>
              </label>
              <label class="kv">
                <span class="mono">maxWaits</span>
                <input
                  class="inp mono"
                  type="number"
                  value={finalEngine.quota.maxWaits}
                  onchange={(e) =>
                    setOverride('quota', { ...finalEngine.quota, maxWaits: +e.currentTarget.value })}
                />
              </label>
              <label class="kv chk">
                <input
                  type="checkbox"
                  checked={finalEngine.quota.manualContinue}
                  onchange={(e) =>
                    setOverride('quota', {
                      ...finalEngine.quota,
                      manualContinue: e.currentTarget.checked,
                    })}
                />
                <span class="mono">manual-continue</span>
              </label>
            </div>
          {:else if openDrawer === 'allowedTools'}
            <label class="kv wide">
              <span class="mono">allowedTools</span>
              <input
                class="inp mono"
                value={finalEngine.allowedTools.join(', ')}
                onchange={(e) =>
                  setOverride(
                    'allowedTools',
                    e.currentTarget.value
                      .split(',')
                      .map((s) => s.trim())
                      .filter(Boolean),
                  )}
              />
            </label>
            <label class="kv wide">
              <span class="mono">permissionMode</span>
              <select
                value={finalEngine.permissionMode}
                onchange={(e) =>
                  setOverride(
                    'permissionMode',
                    e.currentTarget.value as EngineConfig['permissionMode'],
                  )}
              >
                <option>acceptEdits</option>
                <option>plan</option>
                <option>default</option>
                <option>bypassPermissions</option>
              </select>
            </label>
          {:else if openDrawer === 'qa'}
            <div class="row">
              <label class="kv chk">
                <input
                  type="checkbox"
                  checked={finalEngine.qa.humanInLoop}
                  onchange={(e) =>
                    setOverride('qa', { ...finalEngine.qa, humanInLoop: e.currentTarget.checked })}
                />
                <span class="mono">human-in-loop</span>
              </label>
              <label class="kv">
                <span class="mono">maxTurns</span>
                <input
                  class="inp mono"
                  type="number"
                  value={finalEngine.qa.maxTurns}
                  onchange={(e) =>
                    setOverride('qa', { ...finalEngine.qa, maxTurns: +e.currentTarget.value })}
                />
              </label>
            </div>
          {/if}

          {#if overridden(openDrawer as keyof EngineConfig)}
            <button class="reset mono" onclick={() => resetOverride(openDrawer as keyof EngineConfig)}>
              ↺ reset to blueprint
            </button>
          {/if}
        </div>
      {/if}
    </section>

    <!-- footer: validation + actions -->
    <footer class="ftr">
      <div class="valid mono">
        {#if createError}
          <span class="verr" role="alert">✕ {createError}</span>
        {:else}
          <span class="vstatus" role="status">
            {#if !validation.ok && touched}
              <span class="verr">⚠ {validation.errors[0]}</span>
            {:else if validation.ok}
              <span class="vok">{mode === 'edit' ? '✓ ready to save' : '✓ ready to create'}</span>
            {:else}
              <span class="vhint">calibrate the dials, then ignite</span>
            {/if}
          </span>
        {/if}
      </div>
      <div class="actions">
        <button class="ghost mono" onclick={onClose}>cancel</button>
        <!-- This writes/edits the loop's loop.json; it does NOT start a run. The run is
             started later with ✦ Ignite inside the System view — keep the verbs distinct. -->
        <button class="ignite mono" disabled={!validation.ok || busy} onclick={ignite}>
          {busy
            ? mode === 'edit'
              ? 'saving…'
              : 'creating…'
            : mode === 'edit'
              ? '✦ SAVE LOOP'
              : '✦ CREATE LOOP'}
        </button>
      </div>
    </footer>
  </div>
</div>

<style>
  .scrim {
    position: absolute;
    inset: 0;
    display: flex;
    align-items: center;
    justify-content: center;
    background: rgba(5, 7, 14, 0.74);
    backdrop-filter: blur(4px);
    z-index: 40;
    padding: 18px;
  }
  .console {
    width: min(880px, 96vw);
    max-height: 94vh;
    overflow-y: auto;
    background: linear-gradient(180deg, rgba(11, 14, 28, 0.96), rgba(7, 9, 18, 0.98));
    border: 1px solid var(--panel-edge);
    border-radius: var(--radius);
    box-shadow: 0 24px 80px rgba(0, 0, 0, 0.6), inset 0 1px 0 rgba(201, 162, 75, 0.12);
    display: flex;
    flex-direction: column;
    gap: 14px;
    padding: 18px 20px 16px;
  }

  .hdr {
    display: flex;
    align-items: baseline;
    gap: 12px;
    border-bottom: 1px solid var(--hairline);
    padding-bottom: 12px;
  }
  .title {
    font-size: 13px;
    letter-spacing: 0.2em;
    color: var(--brass);
  }
  .sub {
    font-size: 10px;
    letter-spacing: 0.1em;
    color: var(--text-faint);
    text-transform: uppercase;
  }
  .x {
    margin-left: auto;
    background: transparent;
    border: 1px solid var(--hairline);
    color: var(--text-dim);
    border-radius: var(--radius-pill);
    width: 24px;
    height: 24px;
    cursor: pointer;
    font-size: 11px;
  }
  .x:hover {
    border-color: var(--crimson);
    color: var(--crimson);
  }

  .idrow {
    display: flex;
    gap: 10px;
  }
  .field {
    display: flex;
    flex-direction: column;
    gap: 4px;
  }
  .field.grow {
    flex: 1;
  }
  .flab {
    font-size: 10.5px;
    letter-spacing: 0.14em;
    color: var(--text-meta);
  }
  .inp {
    background: var(--void-3);
    border: 1px solid var(--hairline);
    border-radius: 6px;
    color: var(--starlight);
    font-family: var(--font-grotesk);
    font-size: 12px;
    padding: 7px 9px;
    transition: border-color 0.18s;
    min-width: 0;
  }
  .inp.mono {
    font-family: var(--font-mono);
    font-size: 11px;
  }
  .inp:focus {
    border-color: var(--brass);
  }
  .inp:disabled {
    opacity: 0.5;
  }

  .seclab {
    font-size: 10.5px;
    letter-spacing: 0.16em;
    color: var(--brass);
    text-transform: uppercase;
    display: block;
    margin-bottom: 8px;
  }
  .seclab em {
    color: var(--text-faint);
    font-style: normal;
    letter-spacing: 0.06em;
  }

  /* blueprint chips */
  .bp-row {
    display: flex;
    gap: 8px;
    flex-wrap: wrap;
  }
  .bp {
    display: flex;
    align-items: center;
    gap: 8px;
    padding: 8px 14px;
    background: var(--void-3);
    border: 1px solid var(--hairline);
    border-radius: var(--radius-pill);
    color: var(--text-dim);
    cursor: pointer;
    transition: all 0.18s;
  }
  .bp:hover {
    border-color: color-mix(in srgb, var(--brass) 40%, transparent);
  }
  .bp.on {
    border-color: var(--brass);
    color: var(--starlight);
    background: color-mix(in srgb, var(--brass) 12%, var(--void-3));
  }
  .bp-glyph {
    font-size: 15px;
    color: var(--brass);
  }
  .bp-name {
    font-size: 12px;
    font-weight: 600;
  }
  .bp-tag {
    margin-top: 6px;
    font-size: 10px;
    color: var(--text-faint);
  }

  .cols {
    display: grid;
    grid-template-columns: 1.05fr 0.95fr;
    gap: 12px;
  }
  .panel {
    background: rgba(7, 9, 18, 0.5);
    border: 1px solid var(--hairline);
    border-radius: var(--radius);
    padding: 12px 14px;
  }

  /* dials */
  .dial {
    margin-bottom: 14px;
  }
  .dial:last-child {
    margin-bottom: 0;
  }
  .dlabels {
    display: flex;
    justify-content: space-between;
    font-size: 10.5px;
    letter-spacing: 0.1em;
    color: var(--text-meta);
    margin-bottom: 4px;
  }
  .dial input[type='range'] {
    width: 100%;
    appearance: none;
    height: 3px;
    border-radius: 2px;
    /* the filled portion ends at the actual value (--fill); past it the track is
       an unlit hairline, so the bright span literally equals the setting. */
    background: linear-gradient(
      90deg,
      var(--brass) 0%,
      var(--plasma-cyan) var(--fill, 50%),
      var(--surface-3) var(--fill, 50%),
      var(--surface-3) 100%
    );
  }
  .dial input[type='range']::-webkit-slider-thumb {
    appearance: none;
    width: 14px;
    height: 14px;
    border-radius: 50%;
    background: var(--starlight);
    border: 2px solid var(--brass);
    cursor: pointer;
    box-shadow: 0 0 8px rgba(201, 162, 75, 0.5);
  }
  .dial input[type='range']::-moz-range-thumb {
    width: 14px;
    height: 14px;
    border-radius: 50%;
    background: var(--starlight);
    border: 2px solid var(--brass);
    cursor: pointer;
  }
  .dhint {
    margin-top: 5px;
    font-size: 10.5px;
    color: var(--text-meta);
    letter-spacing: 0.02em;
  }

  /* preview */
  .orrery-mini {
    display: flex;
    justify-content: center;
    padding: 2px 0 6px;
  }
  .mini {
    width: 100%;
    max-width: 240px;
    height: auto;
  }
  .pstats {
    list-style: none;
    margin: 0;
    padding: 0;
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 3px 14px;
    font-size: 10px;
  }
  .pstats li {
    display: flex;
    justify-content: space-between;
    color: var(--text-dim);
  }
  .pstats strong {
    color: var(--starlight);
    font-weight: 500;
  }
  .pstats strong.on {
    color: var(--auditor-white);
  }
  .pstats strong.off {
    color: var(--text-faint);
  }
  .night-btn {
    margin-top: 8px;
    background: transparent;
    border: 1px solid var(--hairline);
    color: var(--plasma-cyan);
    border-radius: var(--radius-pill);
    padding: 5px 12px;
    font-size: 10px;
    cursor: pointer;
    letter-spacing: 0.06em;
  }
  .night-btn:hover {
    border-color: var(--plasma-cyan);
  }
  .night {
    margin-top: 8px;
  }
  .night-svg {
    width: 100%;
    height: 50px;
  }
  .night-cap {
    font-size: 9.5px;
    color: var(--text-dim);
    margin-top: 2px;
  }
  .night-cap .warn {
    color: var(--crimson);
  }

  /* destination */
  .dest-grid {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 14px;
  }
  .dlab {
    font-size: 10.5px;
    letter-spacing: 0.1em;
    color: var(--text-meta);
    margin-bottom: 6px;
  }
  .ac-row,
  .st-row {
    display: flex;
    align-items: center;
    gap: 6px;
    margin-bottom: 5px;
  }
  .ac-star {
    color: var(--brass);
    font-size: 11px;
  }
  .ac-row .inp {
    flex: 1;
  }
  .st-name {
    width: 78px;
    flex: none;
  }
  .st-cmd {
    flex: 1;
  }
  .arrow {
    color: var(--text-faint);
    font-size: 11px;
  }
  .mini-x {
    background: transparent;
    border: none;
    color: var(--text-faint);
    cursor: pointer;
    font-size: 10px;
    padding: 2px 4px;
  }
  .mini-x:hover {
    color: var(--crimson);
  }
  .add {
    background: transparent;
    border: 1px dashed var(--hairline);
    color: var(--text-dim);
    border-radius: 6px;
    padding: 5px 10px;
    font-size: 10px;
    cursor: pointer;
    margin-top: 2px;
  }
  .add:hover {
    border-color: var(--brass);
    color: var(--brass);
  }

  /* drawers */
  .drawer-tabs {
    display: flex;
    gap: 6px;
    flex-wrap: wrap;
  }
  .dtab {
    display: flex;
    align-items: center;
    gap: 5px;
    background: var(--void-3);
    border: 1px solid var(--hairline);
    color: var(--text-dim);
    border-radius: var(--radius-pill);
    padding: 5px 12px;
    font-size: 10px;
    cursor: pointer;
    letter-spacing: 0.04em;
    transition: all 0.16s;
  }
  .dtab:hover {
    border-color: color-mix(in srgb, var(--brass) 40%, transparent);
  }
  .dtab.on {
    border-color: var(--brass);
    color: var(--starlight);
  }
  .odot {
    width: 6px;
    height: 6px;
    border-radius: 50%;
    background: var(--amber);
    box-shadow: 0 0 6px var(--amber);
  }
  .drawer-body {
    margin-top: 10px;
    padding: 12px 14px;
    background: rgba(7, 9, 18, 0.6);
    border: 1px solid var(--hairline);
    border-radius: var(--radius);
    display: flex;
    flex-direction: column;
    gap: 10px;
  }
  .row {
    display: flex;
    gap: 14px;
    flex-wrap: wrap;
    align-items: flex-end;
  }
  .kv {
    display: flex;
    flex-direction: column;
    gap: 4px;
  }
  .kv.wide {
    width: 100%;
  }
  .kv.chk {
    flex-direction: row;
    align-items: center;
    gap: 7px;
  }
  .kv span {
    font-size: 10.5px;
    color: var(--text-meta);
    letter-spacing: 0.08em;
  }
  .kv select {
    background: var(--void-3);
    border: 1px solid var(--hairline);
    color: var(--starlight);
    border-radius: 6px;
    padding: 6px 8px;
    font-family: var(--font-mono);
    font-size: 11px;
  }
  .kv input[type='number'] {
    width: 84px;
  }
  .def {
    font-size: 9.5px;
    color: var(--text-faint);
  }
  .reset {
    align-self: flex-start;
    background: transparent;
    border: 1px solid color-mix(in srgb, var(--amber) 40%, transparent);
    color: var(--amber);
    border-radius: var(--radius-pill);
    padding: 4px 11px;
    font-size: 10px;
    cursor: pointer;
  }

  /* footer */
  .ftr {
    display: flex;
    align-items: center;
    gap: 12px;
    border-top: 1px solid var(--hairline);
    padding-top: 12px;
  }
  .valid {
    flex: 1;
    font-size: 11px;
  }
  .verr {
    color: var(--crimson);
  }
  .vok {
    color: var(--plasma-green);
  }
  .vhint {
    color: var(--text-meta);
  }
  .actions {
    display: flex;
    gap: 9px;
  }
  .ghost {
    background: transparent;
    border: 1px solid var(--hairline);
    color: var(--text-dim);
    border-radius: var(--radius-pill);
    padding: 8px 16px;
    font-size: 11px;
    cursor: pointer;
  }
  .ghost:hover {
    color: var(--starlight);
  }
  .ignite {
    background: color-mix(in srgb, var(--amber) 14%, transparent);
    border: 1px solid var(--amber);
    color: var(--amber);
    border-radius: var(--radius-pill);
    padding: 8px 20px;
    font-size: 11px;
    font-weight: 600;
    letter-spacing: 0.08em;
    cursor: pointer;
    transition: all 0.16s;
  }
  .ignite:hover:not(:disabled) {
    background: color-mix(in srgb, var(--amber) 24%, transparent);
    transform: translateY(-1px);
  }
  .ignite:disabled {
    opacity: 0.4;
    cursor: default;
  }

  @media (max-width: 720px) {
    .cols,
    .dest-grid {
      grid-template-columns: 1fr;
    }
  }
</style>
