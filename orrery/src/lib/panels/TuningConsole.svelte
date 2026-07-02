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
    isSafeLoopId,
    type BlueprintId,
    type Blueprint,
    type DialState,
    type EngineConfig,
    type GateStageDef,
    type ConsoleInput,
  } from '../blueprints';
  import { cosmosStore, type ProbeResult } from '../stores/cosmos.svelte';
  import { focusTrap } from '../actions/focusTrap';

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
    // drop any stale probe result for this row + reindex the rest
    const next: Record<number, ProbeRowState> = {};
    for (const [k, v] of Object.entries(probeState)) {
      const idx = Number(k);
      if (idx === i) continue;
      next[idx > i ? idx - 1 : idx] = v;
    }
    probeState = next;
  }

  // ── U3 Task 3: probe a gate stage's command BEFORE the first paid iteration ──
  // "▸ test" runs the stage's command once, synchronously, via probe_command (§6).
  // Discovering a broken gate command should be free, not cost iteration 1.
  interface ProbeRowState {
    running: boolean;
    result: ProbeResult | null;
    error: string | null;
    expanded: boolean;
  }
  let probeState = $state<Record<number, ProbeRowState>>({});

  async function testStage(i: number) {
    const stage = gateStages[i];
    if (!stage || !stage.command.trim() || probeState[i]?.running) return;
    const id = (mode === 'edit' && editId ? editId : loopId).trim();
    if (!isSafeLoopId(id)) {
      probeState = {
        ...probeState,
        [i]: { running: false, result: null, error: 'give the loop a valid id first', expanded: false },
      };
      touched = true;
      return;
    }
    probeState = {
      ...probeState,
      [i]: { running: true, result: null, error: null, expanded: probeState[i]?.expanded ?? false },
    };
    try {
      const result = await cosmosStore.probeCommand(id, stage.command, 60_000);
      probeState = {
        ...probeState,
        [i]: { running: false, result, error: null, expanded: probeState[i]?.expanded ?? true },
      };
    } catch (e) {
      probeState = {
        ...probeState,
        [i]: {
          running: false,
          result: null,
          error: e instanceof Error ? e.message : String(e),
          expanded: false,
        },
      };
    }
  }
  function toggleProbeExpanded(i: number) {
    const cur = probeState[i];
    if (!cur) return;
    probeState = { ...probeState, [i]: { ...cur, expanded: !cur.expanded } };
  }

  // ── U3 Task 2: scaffold TASK.md from the console's own inputs ──────────────
  // A fresh loop starts against a spec that matches what the console promised: the
  // name as title, the acceptance criteria typed above, a context placeholder, and
  // the gate commands listed. Never clobbers a hand-authored file — write_loop_file
  // refuses to overwrite without an explicit `overwrite:true`.
  let taskFileState = $state<'idle' | 'writing' | 'created' | 'exists' | 'error'>('idle');
  let taskFileError = $state<string | null>(null);

  function buildTaskTemplate(): string {
    const lines: string[] = [];
    lines.push(`# ${loopName.trim() || loopId.trim() || 'Untitled loop'}`);
    lines.push('');
    lines.push('## Context');
    lines.push(
      '_Describe the codebase, constraints, and anything the agent needs to know before it starts. Replace this placeholder._',
    );
    lines.push('');
    lines.push('## Acceptance criteria');
    const criteria = acceptanceCriteria.map((a) => a.trim()).filter(Boolean);
    if (criteria.length) {
      for (const c of criteria) lines.push(`- [ ] ${c}`);
    } else {
      lines.push('- [ ] _(none written yet — go back and describe done)_');
    }
    lines.push('');
    lines.push('## Gate');
    lines.push("The loop is green when every command below exits 0:");
    const commands = gateStages.map((s) => s.command.trim()).filter(Boolean);
    if (commands.length) {
      for (const cmd of commands) lines.push(`- \`${cmd}\``);
    } else {
      lines.push('- _(no gate stage commands yet)_');
    }
    lines.push('');
    return lines.join('\n');
  }

  /** Attempt the scaffold write. Never clobbers an existing file unless `overwrite`. */
  async function scaffoldTaskFile(id: string, overwrite = false) {
    taskFileState = 'writing';
    taskFileError = null;
    const { written, error } = await cosmosStore.writeLoopFile(
      id,
      task.trim() || 'TASK.md',
      buildTaskTemplate(),
      overwrite,
    );
    if (written) {
      taskFileState = 'created';
    } else if (error?.includes('already exists')) {
      taskFileState = 'exists';
    } else if (error) {
      taskFileState = 'error';
      taskFileError = error;
    } else {
      // dev (no Tauri): writeLoopFile no-ops with written:false, no error — nothing to report
      taskFileState = 'idle';
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
      // U3 Task 2: a brand-new loop starts against a spec that matches what the console
      // promised. Only on a freshly PERSISTED create, and only while the task path is
      // still the default 'TASK.md' — an edit never auto-writes (see the manual
      // "regenerate" control by the TASK field, which respects a hand-authored file the
      // same way: write_loop_file never clobbers without an explicit overwrite).
      if (mode === 'create' && persisted && (task.trim() || 'TASK.md') === 'TASK.md') {
        await scaffoldTaskFile(id);
        if (taskFileState === 'created') {
          // hold the console open just long enough to show the confirmation before the
          // shell flies into the new System.
          await new Promise((r) => setTimeout(r, 700));
        }
      }
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
  // (focus move-in / trap / restore is now the shared `focusTrap` action on the dialog element)
  onMount(() => {
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
  });

  // human-readable settings for the dial aria-valuetext (screen readers hear the
  // actual config the dial currently maps to, not a bare 0–1 number).
  // Human-readable settings for the dial aria-valuetext. Every value quoted here is a
  // REAL EngineConfig field (see blueprints.ts's honesty constraint) — the caption
  // never promises a bundle the engine doesn't actually read.
  const ambitionText = $derived(
    `Ambition: ${finalEngine.models.execute}, ${fmtUsd(finalEngine.cost.ceilingUsd)} ceiling, ${finalEngine.stop.maxIters} iters`,
  );
  const patienceText = $derived(
    `Patience: ${finalEngine.verify.mutationAudit ? `mutation-audit every ${finalEngine.verify.mutationEvery || 1}` : 'mutation-audit off'}, plateau ${finalEngine.stop.plateauLimit}, regress-limit ${finalEngine.stop.regressLimit}`,
  );
  const autonomyText = $derived(
    `Autonomy: ${finalEngine.permissionMode}, ${finalEngine.maxTurns} turns/phase, ${finalEngine.iterTimeoutMin}m/iter`,
  );
  // caption-only label (not an emitted config field) — a plain-language read of the
  // autonomy dial itself, echoing what the bundle amounts to in practice.
  const autonomyLabel = $derived(dials.autonomy < 0.45 ? 'human-in-loop' : 'overnight-auto');

  const DRAWERS: { key: keyof EngineConfig | 'models' | 'tools'; label: string }[] = [
    { key: 'models', label: 'Models' },
    { key: 'cost', label: 'Economy' },
    { key: 'gate', label: 'Gate' },
    { key: 'stop', label: 'Resilience' },
    { key: 'maxTurns', label: 'Timing' },
    { key: 'allowedTools', label: 'Tools' },
    { key: 'feedback', label: 'Diagnostics' },
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
    class="console floating-card"
    role="dialog"
    aria-modal="true"
    aria-labelledby="tc-title"
    tabindex="-1"
    use:focusTrap={{ onClose }}
    onclick={(e) => e.stopPropagation()}
  >
    <!-- header -->
    <header class="hdr">
      <div id="tc-title" class="title mono">{mode === 'edit' ? '✦ LOOP SETTINGS' : '✦ NEW LOOP'}</div>
      <div class="sub mono">{mode === 'edit' ? `recalibrating ${editId}` : 'author a loop.json'}</div>
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

    <!-- U3 Task 2: TASK.md scaffold feedback. Create mode shows a transient confirmation
         (set by ignite() just before it closes); edit mode offers an explicit, opt-in
         regenerate control that never clobbers a hand-authored file without confirming. -->
    {#if mode === 'edit' && (task.trim() || 'TASK.md') === 'TASK.md'}
      <div class="taskfile mono">
        {#if taskFileState === 'idle'}
          <button class="taskfile-btn" onclick={() => editId && scaffoldTaskFile(editId)}>
            ✎ regenerate TASK.md from these settings
          </button>
        {:else if taskFileState === 'writing'}
          <span class="taskfile-status">writing TASK.md…</span>
        {:else if taskFileState === 'created'}
          <span class="taskfile-status ok">✓ TASK.md created</span>
        {:else if taskFileState === 'exists'}
          <span class="taskfile-status warn">TASK.md already has content —</span>
          <button class="taskfile-btn" onclick={() => editId && scaffoldTaskFile(editId, true)}>
            overwrite it
          </button>
        {:else if taskFileState === 'error'}
          <span class="taskfile-status bad">✕ {taskFileError}</span>
        {/if}
      </div>
    {:else if taskFileState === 'writing' || taskFileState === 'created' || taskFileState === 'error'}
      <div class="taskfile mono">
        {#if taskFileState === 'writing'}
          <span class="taskfile-status">writing TASK.md…</span>
        {:else if taskFileState === 'created'}
          <span class="taskfile-status ok">✓ TASK.md created</span>
        {:else}
          <span class="taskfile-status bad">✕ TASK.md not written: {taskFileError}</span>
        {/if}
      </div>
    {/if}

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
          <div class="dsub mono">budget &amp; models</div>
          <input
            class="slider thumb-brass"
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
          <div class="dsub mono">verification strictness</div>
          <input
            class="slider thumb-brass"
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
            {finalEngine.verify.mutationAudit
              ? `audit every ${finalEngine.verify.mutationEvery || 1}`
              : 'audit off'} · plateau {finalEngine.stop.plateauLimit} · regress-limit {finalEngine
              .stop.regressLimit}
          </div>
        </div>

        <div class="dial">
          <div class="dlabels mono"><span>COMPANY</span><span>AUTONOMY</span></div>
          <div class="dsub mono">how unattended</div>
          <input
            class="slider thumb-brass"
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
            {autonomyLabel} · {finalEngine.permissionMode} · {finalEngine.maxTurns} turns/phase · {finalEngine.iterTimeoutMin}m/iter
          </div>
        </div>
      </section>

      <!-- RIGHT: live preview orrery -->
      <section class="panel preview">
        <span class="seclab mono">PREVIEW</span>
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
          <li><span>regress-limit</span><strong>{preview.regressLimit}</strong></li>
          <li><span>gate stages</span><strong>{preview.stageCount}</strong></li>
          <li><span>AC stars</span><strong>{preview.acCount}</strong></li>
        </ul>
        <button class="night-btn mono" onclick={() => (nightOpen = !nightOpen)}>
          {nightOpen ? '▾' : '▸'} preview a full run
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
      <span class="seclab mono">DEFINITION OF DONE <em>— what must be true when it's finished</em></span>
      <div class="dest-grid">
        <div class="ac">
          <div class="dlab mono">ACCEPTANCE CRITERIA</div>
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
          <div class="dlab mono">TEST GATE — every stage must pass</div>
          {#each gateStages as _st, i}
            <div class="st-row">
              <input class="inp st-name mono" placeholder="stage" bind:value={gateStages[i].name} />
              <span class="arrow">→</span>
              <input
                class="inp st-cmd mono"
                placeholder="command (e.g. bun test)"
                bind:value={gateStages[i].command}
              />
              <!-- U3 Task 3: run this stage's command once, right now, in the loop's own
                   working dir — discover a broken gate command for free, not on iteration 1. -->
              <button
                class="probe-btn mono"
                disabled={!gateStages[i].command.trim() || probeState[i]?.running}
                title="Run this command once, synchronously, in the loop's working dir"
                onclick={() => testStage(i)}
              >
                {probeState[i]?.running ? '…' : '▸ test'}
              </button>
              <button class="mini-x" aria-label="remove" onclick={() => removeStage(i)}>✕</button>
            </div>
            {#if probeState[i]}
              {@const st = probeState[i]}
              <div
                class="probe-line mono"
                class:ok={st.result?.exitCode === 0}
                class:bad={(st.result && st.result.exitCode !== 0 && !st.result.timedOut) || !!st.error}
                class:warn={st.result?.timedOut}
              >
                {#if st.running}
                  <span>running…</span>
                {:else if st.error}
                  <span>✕ {st.error}</span>
                {:else if st.result?.timedOut}
                  <span>⏱ timed out after {st.result.durationMs}ms</span>
                {:else if st.result}
                  <span>
                    {st.result.exitCode === 0 ? '✓' : '✕'} exit {st.result.exitCode} · {st.result
                      .durationMs}ms
                  </span>
                  <button class="probe-toggle" onclick={() => toggleProbeExpanded(i)}>
                    {st.expanded ? 'hide output' : 'show output'}
                  </button>
                {/if}
              </div>
              {#if st.result && st.expanded}
                <pre class="probe-tail mono">{st.result.tail || '(no output)'}</pre>
              {/if}
            {/if}
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
            <div class="def mono">
              stages are edited above in Definition of Done ({finalEngine.gate.stages.length}). Green
              is not configurable here — a stage passes on exit 0, and the gate is green when every
              stage passes.
            </div>
          {:else if openDrawer === 'stop'}
            <div class="row">
              <label class="kv">
                <span class="mono">stagnationLimit</span>
                <input
                  class="inp mono"
                  type="number"
                  value={finalEngine.stop.stagnationLimit}
                  onchange={(e) =>
                    setOverride('stop', {
                      ...finalEngine.stop,
                      stagnationLimit: +e.currentTarget.value,
                    })}
                />
              </label>
              <label class="kv">
                <span class="mono">plateauLimit</span>
                <input
                  class="inp mono"
                  type="number"
                  value={finalEngine.stop.plateauLimit}
                  onchange={(e) =>
                    setOverride('stop', { ...finalEngine.stop, plateauLimit: +e.currentTarget.value })}
                />
              </label>
              <label class="kv">
                <span class="mono">regressLimit</span>
                <input
                  class="inp mono"
                  type="number"
                  value={finalEngine.stop.regressLimit}
                  onchange={(e) =>
                    setOverride('stop', { ...finalEngine.stop, regressLimit: +e.currentTarget.value })}
                />
              </label>
              <label class="kv chk">
                <input
                  type="checkbox"
                  checked={finalEngine.stop.gracefulAtPhase}
                  onchange={(e) =>
                    setOverride('stop', {
                      ...finalEngine.stop,
                      gracefulAtPhase: e.currentTarget.checked,
                    })}
                />
                <span class="mono">gracefulAtPhase</span>
              </label>
            </div>
            <div class="def mono">
              blueprint default via dials: stagnation {composed.stop.stagnationLimit} · plateau {composed
                .stop.plateauLimit} · regress-limit {composed.stop.regressLimit}
            </div>
          {:else if openDrawer === 'maxTurns'}
            <div class="row">
              <label class="kv">
                <span class="mono">maxTurns (per phase)</span>
                <input
                  class="inp mono"
                  type="number"
                  value={finalEngine.maxTurns}
                  onchange={(e) => setOverride('maxTurns', +e.currentTarget.value)}
                />
              </label>
              <label class="kv">
                <span class="mono">iterTimeoutMin (0 = unbounded)</span>
                <input
                  class="inp mono"
                  type="number"
                  value={finalEngine.iterTimeoutMin}
                  onchange={(e) => setOverride('iterTimeoutMin', +e.currentTarget.value)}
                />
              </label>
            </div>
            <div class="def mono">blueprint default via dials: {composed.maxTurns} turns/phase · {composed.iterTimeoutMin}m/iter cap</div>
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
          {:else if openDrawer === 'feedback'}
            <div class="row">
              <label class="kv chk">
                <input
                  type="checkbox"
                  checked={finalEngine.feedback.compact}
                  onchange={(e) =>
                    setOverride('feedback', { compact: e.currentTarget.checked })}
                />
                <span class="mono">compact feedback (first failure only)</span>
              </label>
              <label class="kv chk">
                <input
                  type="checkbox"
                  checked={finalEngine.verify.mutationAudit}
                  onchange={(e) =>
                    setOverride('verify', {
                      ...finalEngine.verify,
                      mutationAudit: e.currentTarget.checked,
                    })}
                />
                <span class="mono">mutation-audit</span>
              </label>
              <label class="kv">
                <span class="mono">audit every Nth green</span>
                <input
                  class="inp mono"
                  type="number"
                  value={finalEngine.verify.mutationEvery}
                  onchange={(e) =>
                    setOverride('verify', {
                      ...finalEngine.verify,
                      mutationEvery: +e.currentTarget.value,
                    })}
                />
              </label>
            </div>
            <div class="row">
              <label class="kv chk">
                <input
                  type="checkbox"
                  checked={finalEngine.memory.enabled}
                  onchange={(e) =>
                    setOverride('memory', { ...finalEngine.memory, enabled: e.currentTarget.checked })}
                />
                <span class="mono">cross-run memory</span>
              </label>
              <label class="kv">
                <span class="mono">recallLimit</span>
                <input
                  class="inp mono"
                  type="number"
                  value={finalEngine.memory.recallLimit}
                  onchange={(e) =>
                    setOverride('memory', {
                      ...finalEngine.memory,
                      recallLimit: +e.currentTarget.value,
                    })}
                />
              </label>
              <label class="kv chk">
                <input
                  type="checkbox"
                  checked={finalEngine.metrics.emit}
                  onchange={(e) => setOverride('metrics', { emit: e.currentTarget.checked })}
                />
                <span class="mono">emit run-quality metrics</span>
              </label>
            </div>
            <div class="def mono">
              verify.enabled is armed automatically once you describe at least one acceptance
              criterion above — {finalEngine.verify.enabled ? 'currently ON' : 'currently OFF'}.
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
              <span class="vhint">review the settings, then start</span>
            {/if}
          </span>
        {/if}
      </div>
      <div class="actions">
        <button class="ghost mono" onclick={onClose}>cancel</button>
        <!-- This writes/edits the loop's loop.json; it does NOT start a run. The run is
             started later with ✦ Start inside the System view — keep the verbs distinct. -->
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
    background: var(--scrim);
    backdrop-filter: blur(4px);
    z-index: var(--z-modal);
    padding: 18px;
  }
  .console {
    width: min(880px, 96vw);
    max-height: 94vh;
    overflow-y: auto;
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
  .taskfile {
    display: flex;
    align-items: center;
    gap: 8px;
    margin-top: -4px;
    font-size: 10.5px;
  }
  .taskfile-btn {
    background: transparent;
    border: 1px solid var(--hairline);
    color: var(--plasma-cyan);
    border-radius: var(--radius-pill);
    padding: 3px 10px;
    font-size: 10px;
    cursor: pointer;
  }
  .taskfile-btn:hover {
    border-color: var(--plasma-cyan);
  }
  .taskfile-status {
    color: var(--text-meta);
  }
  .taskfile-status.ok {
    color: var(--plasma-green);
  }
  .taskfile-status.warn {
    color: var(--amber);
  }
  .taskfile-status.bad {
    color: var(--crimson);
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
  .dsub {
    font-size: var(--text-2xs);
    letter-spacing: 0.04em;
    color: var(--text-faint);
    margin-bottom: 5px;
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
  /* thumb appearance + focus ring now come from the shared .slider.thumb-brass
     (primitives.css) — see the `class="slider thumb-brass"` on each dial <input>. */
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
  .probe-btn {
    flex: none;
    background: transparent;
    border: 1px solid var(--hairline);
    color: var(--text-dim);
    border-radius: var(--radius-pill);
    padding: 4px 9px;
    font-size: 9.5px;
    letter-spacing: 0.04em;
    cursor: pointer;
    white-space: nowrap;
  }
  .probe-btn:hover:not(:disabled) {
    border-color: var(--plasma-cyan);
    color: var(--plasma-cyan);
  }
  .probe-btn:disabled {
    opacity: 0.4;
    cursor: default;
  }
  .probe-line {
    display: flex;
    align-items: center;
    gap: 8px;
    margin: -2px 0 6px;
    padding-left: 2px;
    font-size: 10px;
    color: var(--text-meta);
  }
  .probe-line.ok {
    color: var(--plasma-green);
  }
  .probe-line.bad {
    color: var(--crimson);
  }
  .probe-line.warn {
    color: var(--amber);
  }
  .probe-toggle {
    background: transparent;
    border: none;
    color: var(--plasma-cyan);
    cursor: pointer;
    font-size: 9.5px;
    text-decoration: underline;
    padding: 0;
  }
  .probe-tail {
    margin: -2px 0 8px;
    padding: 8px 10px;
    background: rgba(0, 0, 0, 0.35);
    border: 1px solid var(--hairline);
    border-radius: 6px;
    color: var(--text-dim);
    font-size: 10px;
    line-height: 1.4;
    max-height: 160px;
    overflow-y: auto;
    white-space: pre-wrap;
    word-break: break-word;
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
