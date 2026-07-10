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
  //                default; any change lights a bright override-dot + offers reset.
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
    PRESETS,
    PRESET_ORDER,
    presetFromDials,
    type BlueprintId,
    type Blueprint,
    type DialState,
    type EngineConfig,
    type GateStageDef,
    type ConsoleInput,
    type PresetName,
  } from '../blueprints';
  import {
    composeBmadLoopDef,
    composeQaLoopDef,
    validateExternalDraft,
    BMAD_PROFILES,
    type ExternalAdapter,
    type BmadPhase,
    type ReviewRetroMode,
    type SmokeMode,
  } from '../externalRecipes';
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
    onCreated?: (
      id: string,
      ctx: { mode: 'create' | 'edit'; persisted: boolean; startAfterCreate: boolean },
    ) => void;
  } = $props();

  // ── core console state ─────────────────────────────────────────────────────
  let blueprintId = $state<BlueprintId>('grind');
  const blueprint = $derived<Blueprint>(BLUEPRINTS[blueprintId]);

  let dials = $state<DialState>({ ...BLUEPRINTS.grind.dials });
  let loopId = $state('');
  let loopName = $state('');
  let stateDir = $state('.loop');
  let task = $state('TASK.md');
  // follow-up #2 — where a GENERIC loop runs its gate/git/agent (emitted as --cwd).
  // Empty = its own loops/<id>/ folder (the default); an absolute path targets an
  // external repo. Not used by the external (bmad/qa) recipes — they carry --project-root.
  let cwd = $state('');

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
  // numeric bounds cover the drawer-typed engine fields (ceiling/iters/turns/
  // timeout/limits) — see blueprints.ts's NUMERIC_FIELD_LABEL comment for why
  // each is bounded the way it is (only iterTimeoutMin has a documented 0=off).
  const validation = $derived(
    validateDraft(
      {
        id: loopId,
        name: loopName,
        acceptanceCriteria,
        gateStages,
        numeric: {
          ceilingUsd: finalEngine.cost.ceilingUsd,
          maxIters: finalEngine.stop.maxIters,
          maxTurns: finalEngine.maxTurns,
          iterTimeoutMin: finalEngine.iterTimeoutMin,
          stagnationLimit: finalEngine.stop.stagnationLimit,
          plateauLimit: finalEngine.stop.plateauLimit,
          regressLimit: finalEngine.stop.regressLimit,
          recallLimit: finalEngine.memory.recallLimit,
        },
      },
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
  async function ignite(startAfterCreate = false) {
    if (!activeValid.ok || busy) {
      touched = true; // surface any latent validation messages on a blocked submit
      return;
    }
    busy = true;
    createError = null;
    try {
      let def: Record<string, unknown>;
      if (recipeKind === 'external' && externalAdapter === 'bmad') {
        def = composeBmadLoopDef({
          id: loopId.trim(),
          name: loopName.trim(),
          targetRepo: bmadRepo.trim(),
          mergeBase: bmadMergeBase.trim() || 'develop',
          stateDir: stateDir.trim() || '.loop',
          models: bmadModels,
          effort: bmadEffort,
          reviewMode: bmadReviewMode,
          smokeMode: bmadSmokeMode,
          retroMode: bmadRetroMode,
          noSmoke: bmadNoSmoke,
          noMerge: bmadNoMerge,
          noRetro: bmadNoRetro,
          noVerify: bmadNoVerify,
          noPlanGate: bmadNoPlanGate,
        }) as unknown as Record<string, unknown>;
      } else if (recipeKind === 'external' && externalAdapter === 'qa') {
        def = composeQaLoopDef({
          id: loopId.trim(),
          name: loopName.trim(),
          targetRepo: qaRepo.trim(),
          manifest: qaManifest.trim(),
          stateDir: stateDir.trim() || '.loop',
          baseUrl: qaBaseUrl.trim(),
          app: qaApp.trim(),
          storageState: qaStorageState.trim(),
          seedSummary: qaSeedSummary.trim(),
          costCeilingUsd: Number.isFinite(qaCostCeiling) && qaCostCeiling > 0 ? qaCostCeiling : undefined,
        }) as unknown as Record<string, unknown>;
      } else {
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
          cwd: cwd.trim() || undefined,
        };
        def = composeLoopDef(input) as unknown as Record<string, unknown>;
      }
      const { id, persisted } = await cosmosStore.createLoop(def, { mode, editId });
      // U3 Task 2: a brand-new GENERIC loop starts against a scaffolded TASK.md that matches
      // what the console promised. Only on a freshly PERSISTED create, while the task path is
      // still the default. External (bmad/qa) recipes carry their spec in the TARGET repo
      // (sprint files / AC manifest), so we never scaffold a TASK.md for them.
      if (
        recipeKind === 'generic' &&
        mode === 'create' &&
        persisted &&
        (task.trim() || 'TASK.md') === 'TASK.md'
      ) {
        await scaffoldTaskFile(id);
        if (taskFileState === 'created') {
          // hold the console open just long enough to show the confirmation before the
          // shell flies into the new System.
          await new Promise((r) => setTimeout(r, 700));
        }
      }
      // Pass mode + whether it hit disk + whether the user asked to start it, so the shell
      // only flies into a System (and, for ✦ Create & start, starts the run) for a NEW,
      // persisted loop — a SAVE (edit) or a dev-mode no-op create stays at the Cosmos.
      onCreated?.(id, { mode, persisted, startAfterCreate });
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
        if (!def) return;
        loopName = String(def.name ?? editId);
        if (typeof def.stateDir === 'string') stateDir = def.stateDir;
        const start = def.start as { program?: string; args?: string[] } | undefined;
        const program = start?.program;
        const args = Array.isArray(start?.args) ? (start!.args as string[]) : [];
        const argVal = (flag: string): string => {
          const i = args.indexOf(flag);
          return i >= 0 && i + 1 < args.length ? args[i + 1] : '';
        };
        if (program === 'loop-bmad') {
          // editing an external BMAD loop → the dedicated compact surface, prefilled from
          // start.args + the top-level `bmad` block (never the generic dials/engine).
          recipeKind = 'external';
          externalAdapter = 'bmad';
          bmadRepo = argVal('--project-root');
          bmadMergeBase = argVal('--merge-base') || 'develop';
          bmadNoSmoke = args.includes('--no-smoke');
          bmadNoMerge = args.includes('--no-merge');
          bmadNoRetro = args.includes('--no-retro');
          bmadNoVerify = args.includes('--no-verify');
          bmadNoPlanGate = args.includes('--no-plan-gate');
          const b = (def.bmad ?? {}) as Record<string, unknown>;
          if (b.models) bmadModels = { ...bmadModels, ...(b.models as Record<BmadPhase, string>) };
          if (b.effort) bmadEffort = { ...bmadEffort, ...(b.effort as Record<BmadPhase, string>) };
          if (b.reviewMode) bmadReviewMode = b.reviewMode as ReviewRetroMode;
          if (b.smokeMode) bmadSmokeMode = b.smokeMode as SmokeMode;
          if (b.retroMode) bmadRetroMode = b.retroMode as ReviewRetroMode;
        } else if (program === 'loop-qa') {
          recipeKind = 'external';
          externalAdapter = 'qa';
          qaRepo = argVal('--project-root');
          qaManifest = argVal('--manifest') || 'ac-manifest.json';
          const q = (def.qa ?? {}) as Record<string, unknown>;
          if (typeof q.baseUrl === 'string') qaBaseUrl = q.baseUrl;
          if (typeof q.app === 'string') qaApp = q.app;
          if (typeof q.storageState === 'string') qaStorageState = q.storageState;
          if (typeof q.seedSummary === 'string') qaSeedSummary = q.seedSummary;
          // The saved ceiling must round-trip, else every edit-save silently resets it to the
          // $state default. An absent key means UNCAPPED (ignite only emits it when > 0) → 0.
          qaCostCeiling =
            typeof q.costCeilingUsd === 'number' && q.costCeilingUsd > 0 ? q.costCeilingUsd : 0;
        } else {
          // generic loop: prefill from the engine block + preserve any external --cwd target
          const c = argVal('--cwd');
          if (c && c !== '.') cwd = c;
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

  // ══ REDESIGN (adaptive sequence) — flow/lane/step state layered over the draft ══
  // create opens on the recipe gallery; edit skips straight to the config surface
  // (there is nothing to pick — the loaded engine is already the "recipe"). The
  // gallery only ever renders in create mode (see the template guard), so this can
  // start 'gallery' unconditionally without reading the `mode` prop here.
  let flow = $state<'gallery' | 'config'>('gallery');
  let lane = $state<'quick' | 'guided'>('quick');
  let step = $state(0);
  let reached = $state(0);
  let advOpen = $state(false);
  // the loop id is the folder name; we auto-slugify it from the name so the user
  // only thinks about the name — unless they deliberately edit the id.
  let idTouched = $state(false);

  // which named guardrail preset the current dials sit on (null = Custom)
  const preset = $derived<PresetName | null>(presetFromDials(dials));

  // ══ EXTERNAL-ADAPTER RECIPES (BMAD / QA) — a parallel compact surface ══
  // These loops have no dials, no acceptance-criteria/gate, and no `engine` block: the
  // Python engine picks the adapter by start.program (loop-bmad / loop-qa) and reads a
  // top-level `bmad`/`qa` block. So they get their own screen (no lane, no steps), and
  // ignite() composes them via externalRecipes.ts instead of composeLoopDef.
  let recipeKind = $state<'generic' | 'external'>('generic');
  let externalAdapter = $state<ExternalAdapter | null>(null);
  let extAdvOpen = $state(false);

  const BMAD_PHASES: BmadPhase[] = ['create', 'dev', 'review', 'smoke', 'retro', 'decider'];

  // BMAD draft (seeded from the Max-power profile = the shipped loops/bmad seed the owner
  // reproduces; one chip switches to the engine's cost-aware defaults).
  let bmadRepo = $state('');
  let bmadMergeBase = $state('develop');
  let bmadModels = $state<Record<BmadPhase, string>>({ ...BMAD_PROFILES.maxPower.models });
  let bmadEffort = $state<Record<BmadPhase, string>>({ ...BMAD_PROFILES.maxPower.effort });
  let bmadReviewMode = $state<ReviewRetroMode>('single-pass');
  let bmadSmokeMode = $state<SmokeMode>('single-pass');
  let bmadRetroMode = $state<ReviewRetroMode>('single-pass');
  let bmadNoSmoke = $state(true);
  let bmadNoMerge = $state(false);
  let bmadNoRetro = $state(false);
  let bmadNoVerify = $state(false);
  let bmadNoPlanGate = $state(false);

  // QA draft
  let qaRepo = $state('');
  let qaManifest = $state('ac-manifest.json');
  let qaBaseUrl = $state('http://localhost:3000');
  let qaApp = $state('app');
  let qaStorageState = $state('');
  let qaSeedSummary = $state('');
  let qaCostCeiling = $state(30);

  function bmadMatches(p: 'maxPower' | 'costAware'): boolean {
    const prof = BMAD_PROFILES[p];
    return (
      BMAD_PHASES.every(
        (ph) => bmadModels[ph] === prof.models[ph] && bmadEffort[ph] === prof.effort[ph],
      ) &&
      bmadReviewMode === prof.reviewMode &&
      bmadSmokeMode === prof.smokeMode &&
      bmadRetroMode === prof.retroMode
    );
  }
  // which BMAD power-profile the model/effort/mode grid currently matches (else 'custom')
  const bmadProfile = $derived<'maxPower' | 'costAware' | 'custom'>(
    bmadMatches('maxPower') ? 'maxPower' : bmadMatches('costAware') ? 'costAware' : 'custom',
  );
  function applyBmadProfile(p: 'maxPower' | 'costAware') {
    const prof = BMAD_PROFILES[p];
    bmadModels = { ...prof.models };
    bmadEffort = { ...prof.effort };
    bmadReviewMode = prof.reviewMode;
    bmadSmokeMode = prof.smokeMode;
    bmadRetroMode = prof.retroMode;
  }

  // the two external recipes shown in the gallery alongside the generic blueprints
  const EXTERNAL_RECIPES: {
    adapter: ExternalAdapter;
    glyph: string;
    title: string;
    blurb: string;
    meta: string;
  }[] = [
    {
      adapter: 'bmad',
      glyph: '◆',
      title: 'Work a backlog (BMAD)',
      blurb:
        'Drives a BMAD sprint in one of your repos — create → dev → review → merge, story by story.',
      meta: 'loop-bmad · external repo · needs BMAD installed',
    },
    {
      adapter: 'qa',
      glyph: '◈',
      title: 'QA a web app',
      blurb:
        'Drives a headless browser through your app’s acceptance criteria and authors regression specs.',
      meta: 'loop-qa · external repo · needs a manifest + running app',
    },
  ];

  function pickExternalRecipe(adapter: ExternalAdapter) {
    recipeKind = 'external';
    externalAdapter = adapter;
    if (mode === 'create' && !loopName.trim()) {
      loopName = adapter === 'bmad' ? 'Work a backlog' : 'QA a web app';
      if (!idTouched) loopId = uniqueSlug(loopName);
    }
    flow = 'config';
  }

  // external draft validity — the external analogue of `validation`. Generic drafts
  // keep using `validation`; the active footer/CTA gate on `activeValid`.
  const externalValidation = $derived(
    externalAdapter
      ? validateExternalDraft(
          {
            id: loopId,
            name: loopName,
            targetRepo: externalAdapter === 'bmad' ? bmadRepo : qaRepo,
            mergeBase: bmadMergeBase,
            manifest: qaManifest,
          },
          externalAdapter,
          cosmosStore.existingIds.filter((id) => id !== editId),
        )
      : { ok: true, errors: [] as string[] },
  );
  const activeValid = $derived(recipeKind === 'external' ? externalValidation : validation);

  // recipe = blueprint, re-presented in plain language for the gallery. Honest copy
  // only (see blueprints.ts — Sprint no longer claims BMAD; it's a build→lint→test gate).
  const RECIPE_META: Record<BlueprintId, { title: string; blurb: string; recommended?: boolean }> = {
    grind: {
      title: 'Fix until green',
      blurb: 'Keeps editing and re-running your tests until they pass — cheap, hash-locked, rolls back mistakes.',
      recommended: true,
    },
    sprint: {
      title: 'Build + verify',
      blurb: 'A multi-stage gate — build, then lint, then test. Every stage must pass to be done.',
    },
    explore: {
      title: 'Explore with me',
      blurb: 'Attended and careful — asks before it edits and keeps a human in the loop.',
    },
    custom: {
      title: 'Blank',
      blurb: 'A bare instrument — set the goal, the test, and the guardrails yourself.',
    },
  };

  const PRESET_META: Record<PresetName, { label: string; sub: string }> = {
    careful: { label: 'Careful', sub: 'strict · cheap' },
    balanced: { label: 'Balanced', sub: 'recommended' },
    fast: { label: 'Fast', sub: 'looser · pricier' },
    overnight: { label: 'Overnight', sub: 'long · unattended' },
  };

  const STEP_DEFS = [
    { key: 'Goal', sub: 'name + spec' },
    { key: 'Done when', sub: 'criteria + gate' },
    { key: 'Guardrails', sub: 'budget + autonomy' },
    { key: 'Review', sub: 'confirm + create' },
  ];

  // plain-language reads for the live summary (every value is a real engine field)
  const autonomyPlain = $derived(
    dials.autonomy > 0.8 ? 'unattended' : dials.autonomy > 0.5 ? 'semi-attended' : 'attended',
  );
  const permPlain = $derived(
    finalEngine.permissionMode === 'bypassPermissions'
      ? 'acts without asking'
      : finalEngine.permissionMode === 'plan'
        ? 'plans before acting'
        : finalEngine.permissionMode === 'default'
          ? 'asks before acting'
          : 'applies edits, asks for the rest',
  );
  const critCount = $derived(acceptanceCriteria.filter((a) => a.trim()).length);
  const gateTested = $derived(
    Object.values(probeState).some((p) => p.result?.exitCode === 0),
  );

  function slugify(s: string): string {
    return s
      .toLowerCase()
      .replace(/[^a-z0-9]+/g, '-')
      .replace(/^-+|-+$/g, '')
      .slice(0, 64);
  }
  function uniqueSlug(name: string): string {
    const base = slugify(name);
    if (!base) return friendlyDefaultId();
    const taken = new Set(cosmosStore.existingIds.filter((x) => x !== editId));
    if (!taken.has(base)) return base;
    for (let n = 2; n < 1000; n++) if (!taken.has(`${base}-${n}`)) return `${base}-${n}`;
    return `${base}-${Date.now().toString(36)}`;
  }
  function onNameInput() {
    markTouched();
    if (mode === 'create' && !idTouched) loopId = uniqueSlug(loopName);
  }
  function onIdInput() {
    idTouched = true;
    markTouched();
  }
  function pickRecipe(id: BlueprintId) {
    recipeKind = 'generic';
    externalAdapter = null;
    selectBlueprint(id);
    if (mode === 'create' && !loopName.trim()) {
      loopName = RECIPE_META[id].title;
      if (!idTouched) loopId = uniqueSlug(loopName);
    }
    flow = 'config';
  }
  function applyPreset(name: PresetName) {
    dials = { ...PRESETS[name] };
  }

  // per-step validity — a slice of the same guards validateDraft enforces overall,
  // so a step can't advance into an invalid state (and the final CTA still runs the
  // full validateDraft).
  function stepValid(i: number): boolean {
    if (i === 0) {
      const id = loopId.trim();
      return (
        !!loopName.trim() &&
        !!id &&
        isSafeLoopId(id) &&
        !cosmosStore.existingIds.filter((x) => x !== editId).includes(id)
      );
    }
    if (i === 1) {
      return (
        acceptanceCriteria.some((a) => a.trim()) &&
        gateStages.some((s) => s.command.trim() || s.name.trim())
      );
    }
    return true;
  }
  function stepHint(i: number): string {
    if (i === 0) return 'name it to continue';
    if (i === 1) return 'add a criterion and a test command';
    return '';
  }
  function nextStep() {
    if (!stepValid(step)) {
      touched = true;
      return;
    }
    step = Math.min(STEP_DEFS.length - 1, step + 1);
    reached = Math.max(reached, step);
  }
  function prevStep() {
    step = Math.max(0, step - 1);
  }
  function goStep(i: number) {
    if (i <= reached) step = i;
  }
</script>

<!-- scrim + dialog -->
<div class="scrim" role="presentation" onclick={(e) => { if (e.target === e.currentTarget) onClose(); }}>
  <div
    class="console floating-card"
    role="dialog"
    aria-modal="true"
    aria-labelledby="tc-title"
    tabindex="-1"
    use:focusTrap={{ onClose }}
  >
    <!-- header -->
    <header class="hdr">
      <div id="tc-title" class="title mono">{mode === 'edit' ? '✦ LOOP SETTINGS' : '✦ NEW LOOP'}</div>
      <div class="sub mono">
        {flow === 'gallery'
          ? 'pick a starting point'
          : mode === 'edit'
            ? `recalibrating ${editId}`
            : lane === 'quick'
              ? 'confirm the essentials'
              : 'one decision at a time'}
      </div>
      <button class="x" aria-label="close" onclick={onClose}>✕</button>
    </header>

    {#if mode === 'create' && flow === 'gallery'}
      {@render recipeGallery()}
    {:else}
      <!-- topbar: recipe (or edit) chip + adaptive lane toggle -->
      <div class="tc-topbar">
        {#if mode === 'edit'}
          <span class="tc-chip mono"><span class="tc-chip-glyph">✎</span> editing {editId}</span>
        {:else if recipeKind === 'external'}
          <span class="tc-chip mono">
            <span class="tc-chip-glyph">{externalAdapter === 'bmad' ? '◆' : '◈'}</span>{externalAdapter ===
            'bmad'
              ? 'Work a backlog (BMAD)'
              : 'QA a web app'}
            <button class="tc-chip-change mono" onclick={() => (flow = 'gallery')}>change</button>
          </span>
        {:else}
          <span class="tc-chip mono">
            <span class="tc-chip-glyph">{blueprint.glyph}</span>{RECIPE_META[blueprintId].title}
            <button class="tc-chip-change mono" onclick={() => (flow = 'gallery')}>change</button>
          </span>
        {/if}
        {#if recipeKind === 'generic'}
          <div class="seg tc-lane" role="group" aria-label="how to fill this in">
            <button
              class="seg-item {lane === 'quick' ? 'selected' : ''}"
              aria-pressed={lane === 'quick'}
              onclick={() => (lane = 'quick')}
            >⚡ Quick create</button>
            <button
              class="seg-item {lane === 'guided' ? 'selected' : ''}"
              aria-pressed={lane === 'guided'}
              onclick={() => {
                lane = 'guided';
                reached = Math.max(reached, step);
              }}
            >🧭 Walk me through it</button>
          </div>
        {/if}
      </div>

      <!-- U3 Task 2: TASK.md scaffold feedback. Create mode shows a transient confirmation
           (set by ignite() just before it closes); edit mode offers an explicit, opt-in
           regenerate control that never clobbers a hand-authored file without confirming. -->
      {#if recipeKind === 'generic' && mode === 'edit' && (task.trim() || 'TASK.md') === 'TASK.md'}
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

      <!-- config surface: main column + persistent live summary -->
      <div class="tc-2col">
        <div class="tc-main">
          {#if recipeKind === 'external'}
            {#if externalAdapter === 'bmad'}
              {@render bmadBody()}
            {:else}
              {@render qaBody()}
            {/if}
            {@render externalFooter()}
          {:else if lane === 'quick'}
            {@render nameField()}
            {@render doneWhen()}
            {@render whereField()}
            {@render advancedBody()}
            {@render quickFooter()}
          {:else}
            {@render stepRail()}
            <div class="tc-step">
              {#if step === 0}
                <div class="step-h">
                  <span class="sk mono">Step 1 of 4 · Goal</span>
                  <h3>What should this loop do?</h3>
                  <p>Give it a name, and — if you like — a spec file it reads each pass.</p>
                </div>
                {@render nameField()}
                <label class="field">
                  <span class="flab mono">SPEC FILE <em class="opt">optional · defaults to TASK.md</em></span>
                  <input class="inp mono" bind:value={task} placeholder="TASK.md" />
                  <span class="fhelp">We scaffold this from your answers if it doesn't exist yet.</span>
                </label>
                {@render whereField()}
              {:else if step === 1}
                <div class="step-h">
                  <span class="sk mono">Step 2 of 4 · Done when</span>
                  <h3>How will it know it's finished?</h3>
                  <p>
                    List what must be true, and give the one command that decides “green”. Test it
                    now so a broken gate never costs you a run.
                  </p>
                </div>
                {@render doneWhen()}
              {:else if step === 2}
                <div class="step-h">
                  <span class="sk mono">Step 3 of 4 · Guardrails</span>
                  <h3>How ambitious, careful, and hands-off?</h3>
                  <p>Pick a preset, or nudge the three dials. Each shows exactly what it sets — nothing hidden.</p>
                </div>
                {@render guardrailsBody()}
                {@render advancedBody()}
              {:else}
                <div class="step-h">
                  <span class="sk mono">Step 4 of 4 · Review</span>
                  <h3>Here's what this loop will do</h3>
                  <p>Check it, change anything, then create.</p>
                </div>
                {@render reviewBody()}
              {/if}
            </div>
            {@render stepNav()}
          {/if}
        </div>
        {#if recipeKind === 'external'}
          {@render externalSummary()}
        {:else}
          {@render summaryAside()}
        {/if}
      </div>
    {/if}

    {#snippet recipeGallery()}
      <p class="gallery-lead mono">Step 1 · Pick a starting point</p>
      <div class="recipes">
        {#each BLUEPRINT_ORDER as id (id)}
          {@const bp = BLUEPRINTS[id]}
          {@const meta = RECIPE_META[id]}
          <button
            class="recipe {recipeKind === 'generic' && blueprintId === id ? 'sel' : ''}"
            onclick={() => pickRecipe(id)}
          >
            {#if meta.recommended}<span class="recipe-tag mono">recommended</span>{/if}
            <span class="recipe-glyph">{bp.glyph}</span>
            <span class="recipe-name">{meta.title}</span>
            <span class="recipe-blurb">{meta.blurb}</span>
            <span class="recipe-meta mono">{bp.tagline}</span>
          </button>
        {/each}
      </div>
      <p class="gallery-sub mono">…or drive one of your own repos</p>
      <div class="recipes">
        {#each EXTERNAL_RECIPES as r (r.adapter)}
          <button
            class="recipe recipe-ext {recipeKind === 'external' && externalAdapter === r.adapter
              ? 'sel'
              : ''}"
            onclick={() => pickExternalRecipe(r.adapter)}
          >
            <span class="recipe-glyph">{r.glyph}</span>
            <span class="recipe-name">{r.title}</span>
            <span class="recipe-blurb">{r.blurb}</span>
            <span class="recipe-meta mono">{r.meta}</span>
          </button>
        {/each}
      </div>
    {/snippet}

    {#snippet nameField()}
      <label class="field">
        <span class="flab mono">NAME <em class="req">required</em></span>
        <input
          class="inp"
          placeholder="Describe the mission…"
          bind:value={loopName}
          oninput={onNameInput}
        />
      </label>
      <label class="field id-field">
        <span class="flab mono">LOOP ID <em class="opt">folder name · auto from the name</em></span>
        <input
          class="inp mono"
          placeholder="my-loop"
          bind:value={loopId}
          oninput={onIdInput}
          disabled={mode === 'edit'}
        />
      </label>
    {/snippet}

    {#snippet whereField()}
      <label class="field">
        <span class="flab mono">WHERE IT RUNS <em class="opt">optional · defaults to its own folder</em></span>
        <input
          class="inp mono"
          placeholder="C:/dev/your-repo — blank = a self-contained loop"
          bind:value={cwd}
        />
        <span class="fhelp">
          Point it at an external repo to run the gate, git, and agent there (emitted as
          <code>--cwd</code>). Blank runs inside the loop's own <code>loops/&lt;id&gt;/</code> folder.
        </span>
      </label>
    {/snippet}

    <!-- ════ external-adapter (BMAD / QA) compact screens ════ -->
    {#snippet bmadBody()}
      {@render nameField()}
      <label class="field">
        <span class="flab mono">BMAD REPO <em class="req">required</em></span>
        <input
          class="inp mono"
          placeholder="C:/dev/your-project"
          bind:value={bmadRepo}
          oninput={markTouched}
        />
        <span class="fhelp">
          The repo the sprint runs in — it must already have BMAD installed
          (<code>_bmad-output/…</code>). Passed as <code>--project-root</code>.
        </span>
      </label>
      <label class="field">
        <span class="flab mono">INTEGRATION BRANCH <em class="opt">PRs merge into this · e.g. develop / main</em></span>
        <input class="inp mono" placeholder="develop" bind:value={bmadMergeBase} oninput={markTouched} />
      </label>

      <div class="ext-field">
        <span class="flab mono">MODEL PROFILE</span>
        <div class="presets ext-presets">
          <button
            class="pchip {bmadProfile === 'maxPower' ? 'on' : ''}"
            onclick={() => applyBmadProfile('maxPower')}
          >
            <b>Max power</b><small class="mono">Opus · xhigh · single-pass</small>
          </button>
          <button
            class="pchip {bmadProfile === 'costAware' ? 'on' : ''}"
            onclick={() => applyBmadProfile('costAware')}
          >
            <b>Cost-aware</b><small class="mono">Sonnet · inherit · qa</small>
          </button>
          <span class="preset-cur mono">{bmadProfile === 'custom' ? '· custom' : ''}</span>
        </div>
      </div>

      <label class="ext-toggle">
        <input type="checkbox" bind:checked={bmadNoSmoke} />
        <span>Skip the browser-smoke phase <em class="mono">(recommended for non-Next.js repos)</em></span>
      </label>

      {@render bmadAdvanced()}
    {/snippet}

    {#snippet bmadAdvanced()}
      <div class="adv" data-open={extAdvOpen}>
        <button class="adv-top" onclick={() => (extAdvOpen = !extAdvOpen)}>
          <span class="adv-chev mono">▸</span>
          Advanced BMAD settings
          <span class="adv-sub mono">
            {bmadProfile === 'custom'
              ? 'custom models/effort'
              : `${bmadProfile === 'maxPower' ? 'Max power' : 'Cost-aware'} profile`}
          </span>
        </button>
        {#if extAdvOpen}
          <div class="adv-body">
            <div class="phase-grid">
              <div class="phase-row phase-head mono"><span>phase</span><span>model</span><span>effort</span></div>
              {#each BMAD_PHASES as ph (ph)}
                <div class="phase-row">
                  <span class="mono phase-name">{ph}</span>
                  <input
                    class="inp mono"
                    placeholder="inherit"
                    value={bmadModels[ph]}
                    onchange={(e) => (bmadModels = { ...bmadModels, [ph]: e.currentTarget.value })}
                  />
                  <select
                    value={bmadEffort[ph]}
                    onchange={(e) => (bmadEffort = { ...bmadEffort, [ph]: e.currentTarget.value })}
                  >
                    <option value="">inherit</option>
                    <option value="low">low</option>
                    <option value="medium">medium</option>
                    <option value="high">high</option>
                    <option value="xhigh">xhigh</option>
                    <option value="max">max</option>
                  </select>
                </div>
              {/each}
            </div>
            <div class="def mono">
              Empty model/effort = inherit your Claude Code default. A model can be a tier
              (haiku/sonnet/opus) or a pinned id like <code>claude-opus-4-8[1m]</code>. Keep the
              decider cheap.
            </div>

            <div class="row ext-modes">
              <label class="kv"><span class="mono">review</span>
                <select
                  value={bmadReviewMode}
                  onchange={(e) => (bmadReviewMode = e.currentTarget.value as ReviewRetroMode)}
                >
                  <option value="qa">qa</option><option value="single-pass">single-pass</option>
                </select>
              </label>
              <label class="kv"><span class="mono">smoke</span>
                <select
                  value={bmadSmokeMode}
                  onchange={(e) => (bmadSmokeMode = e.currentTarget.value as SmokeMode)}
                >
                  <option value="iterative">iterative</option><option value="single-pass">single-pass</option>
                </select>
              </label>
              <label class="kv"><span class="mono">retro</span>
                <select
                  value={bmadRetroMode}
                  onchange={(e) => (bmadRetroMode = e.currentTarget.value as ReviewRetroMode)}
                >
                  <option value="qa">qa</option><option value="single-pass">single-pass</option>
                </select>
              </label>
            </div>

            <div class="ext-toggles">
              <label class="ext-toggle"><input type="checkbox" bind:checked={bmadNoMerge} /><span>Open PRs but don't auto-merge <em class="mono">(--no-merge)</em></span></label>
              <label class="ext-toggle"><input type="checkbox" bind:checked={bmadNoRetro} /><span>Skip epic retrospectives <em class="mono">(--no-retro)</em></span></label>
              <label class="ext-toggle"><input type="checkbox" bind:checked={bmadNoVerify} /><span>Disable adversarial verify-before-merge <em class="mono">(--no-verify)</em></span></label>
              <label class="ext-toggle"><input type="checkbox" bind:checked={bmadNoPlanGate} /><span>Disable the plan-gate readiness check <em class="mono">(--no-plan-gate)</em></span></label>
            </div>
            <div class="def mono">
              The gate defaults to <code>bun run codegen · lint · test</code> and the dev-server to
              <code>bun run dev</code>. For a non-bun repo, override <code>gateStages</code> /
              <code>devServerArgv</code> in the loop.json after creating.
            </div>
          </div>
        {/if}
      </div>
    {/snippet}

    {#snippet qaBody()}
      {@render nameField()}
      <label class="field">
        <span class="flab mono">WEBAPP REPO <em class="req">required</em></span>
        <input
          class="inp mono"
          placeholder="C:/dev/your-webapp"
          bind:value={qaRepo}
          oninput={markTouched}
        />
        <span class="fhelp">
          The app repo the agent runs in and writes specs into. Passed as <code>--project-root</code>.
        </span>
      </label>
      <label class="field">
        <span class="flab mono">AC MANIFEST <em class="req">required</em></span>
        <input
          class="inp mono"
          placeholder="ac-manifest.json"
          bind:value={qaManifest}
          oninput={markTouched}
        />
        <span class="fhelp">
          The acceptance-criteria oracle. Build it first with
          <code>python -m orrery_loop.qa.manifest &lt;stories-dir&gt;</code>. Passed as
          <code>--manifest</code>.
        </span>
      </label>
      <label class="field">
        <span class="flab mono">BASE URL <em class="opt">the running app</em></span>
        <input class="inp mono" placeholder="http://localhost:3000" bind:value={qaBaseUrl} />
        <span class="fhelp">Your app must already be running here — the loop drives the browser, it doesn't boot the app.</span>
      </label>
      <label class="field">
        <span class="flab mono">APP NAME <em class="opt">shown in the report</em></span>
        <input class="inp mono" placeholder="app" bind:value={qaApp} />
      </label>

      {@render qaAdvanced()}
    {/snippet}

    {#snippet qaAdvanced()}
      <div class="adv" data-open={extAdvOpen}>
        <button class="adv-top" onclick={() => (extAdvOpen = !extAdvOpen)}>
          <span class="adv-chev mono">▸</span>
          Advanced QA settings
          <span class="adv-sub mono">auth · seed · budget</span>
        </button>
        {#if extAdvOpen}
          <div class="adv-body">
            <label class="field">
              <span class="flab mono">AUTH storageState <em class="opt">for auth-gated apps</em></span>
              <input class="inp mono" placeholder="C:/…/storage-state.json" bind:value={qaStorageState} />
              <span class="fhelp">A saved Playwright auth session. Leave blank for a public/no-auth app.</span>
            </label>
            <label class="field">
              <span class="flab mono">SEED SUMMARY <em class="opt">the data oracle</em></span>
              <textarea
                class="inp mono"
                rows="2"
                placeholder="e.g. 2 lists (Work, Home); 3 todos; darkMode off"
                bind:value={qaSeedSummary}
              ></textarea>
            </label>
            <label class="field">
              <span class="flab mono">COST CEILING <em class="opt">USD · stops between epics · 0 = uncapped</em></span>
              <input class="inp mono" type="number" step="1" min="0" bind:value={qaCostCeiling} />
            </label>
            <div class="def mono">
              Model, effort, spec dir and turn/timeout caps keep the engine defaults; tune them in
              the loop.json after creating.
            </div>
          </div>
        {/if}
      </div>
    {/snippet}

    {#snippet externalFooter()}
      <footer class="ftr">
        {@render validStatus()}
        <div class="actions">
          <button class="btn btn-ghost btn-md" onclick={onClose}>cancel</button>
          {#if mode === 'create'}
            <button
              class="btn btn-ghost btn-lg"
              disabled={!activeValid.ok || busy}
              onclick={() => ignite(true)}
              title="create the loop, then start the run in its system view"
            >
              {busy ? 'creating…' : '✦ Create & start'}
            </button>
          {/if}
          <button
            class="btn btn-primary btn-lg ignite"
            disabled={!activeValid.ok || busy}
            onclick={() => ignite(false)}
          >
            {busy
              ? mode === 'edit'
                ? 'saving…'
                : 'creating…'
              : mode === 'edit'
                ? '✦ Save loop'
                : '✦ Create loop'}
          </button>
        </div>
      </footer>
    {/snippet}

    {#snippet externalSummary()}
      <aside class="summary" aria-label="what will happen">
        <div class="summary-body">
          <h4 class="mono">
            {loopName.trim() || (externalAdapter === 'bmad' ? 'your BMAD sprint' : 'your QA pass')}
          </h4>
          {#if externalAdapter === 'bmad'}
            <p class="summary-lede">
              Runs <b>loop-bmad</b> against <b>{bmadRepo.trim() || 'your repo'}</b>, branching off
              <b>{bmadMergeBase.trim() || 'develop'}</b>. Works the sprint story by story: create → dev
              → review{bmadNoSmoke ? '' : ' → smoke'} → {bmadNoMerge ? 'PR' : 'merge'}.
            </p>
            <ul class="summary-stats mono">
              <li><span>dev model</span><strong>{bmadModels.dev || 'inherit'}</strong></li>
              <li>
                <span>profile</span>
                <strong>
                  {bmadProfile === 'maxPower'
                    ? 'Max power'
                    : bmadProfile === 'costAware'
                      ? 'Cost-aware'
                      : 'custom'}
                </strong>
              </li>
              <li><span>smoke</span><strong>{bmadNoSmoke ? 'off' : bmadSmokeMode}</strong></li>
            </ul>
            <p class="ext-prereq mono">
              Needs in the repo: BMAD installed (<code>_bmad-output/…</code>), a populated
              <code>sprint-status.yaml</code> + story files, <code>gh</code> authed, and the
              <code>{bmadMergeBase.trim() || 'develop'}</code> branch.
            </p>
          {:else}
            <p class="summary-lede">
              Runs <b>loop-qa</b> against <b>{qaApp.trim() || 'your app'}</b> at
              <b>{qaBaseUrl.trim() || 'the base URL'}</b>, judging each epic's acceptance criteria in a
              headless browser and authoring regression specs.
            </p>
            <ul class="summary-stats mono">
              <li><span>manifest</span><strong>{qaManifest.trim() || '—'}</strong></li>
              <li><span>auth</span><strong>{qaStorageState.trim() ? 'storageState' : 'none'}</strong></li>
              <li><span>budget</span><strong>{qaCostCeiling > 0 ? fmtUsd(qaCostCeiling) : 'uncapped'}</strong></li>
            </ul>
            <p class="ext-prereq mono">
              Needs first: the app running at the base URL, an <code>ac-manifest.json</code> (build with
              <code>orrery_loop.qa.manifest</code>), and — for auth-gated apps — a Playwright
              storageState file.
            </p>
          {/if}
          <p class="startnote mono">
            Creating <b>saves</b> this loop — it won't run until you press <b>✦ Start</b> (or use
            <b>✦ Create &amp; start</b>).
          </p>
        </div>
      </aside>
    {/snippet}

    {#snippet guardrailsBody()}
      <!-- one-click preset bundles (each just sets the three dials below) -->
      <div class="presets">
        {#each PRESET_ORDER as name (name)}
          <button
            class="pchip {preset === name ? 'on' : ''}"
            aria-pressed={preset === name}
            onclick={() => applyPreset(name)}
          >
            <b>{PRESET_META[name].label}</b>
            <small class="mono">{PRESET_META[name].sub}</small>
          </button>
        {/each}
        <span class="preset-cur mono">{preset ? '' : '· custom'}</span>
      </div>

      <!-- the same three dials, in plain language, each showing its concrete effect -->
      <div class="dial dial-plain">
        <div class="dial-head">
          <span class="dial-name">Budget &amp; horsepower</span>
          <span class="dial-poles mono">thrifty ⟷ ambitious</span>
        </div>
        <input
          class="slider thumb-brass"
          type="range"
          min="0"
          max="1"
          step="0.01"
          value={dials.ambition}
          style="--fill:{dials.ambition * 100}%"
          aria-label="Budget and horsepower"
          aria-valuetext={ambitionText}
          oninput={(e) => (dials = { ...dials, ambition: +e.currentTarget.value })}
        />
        <div class="dial-mean">
          Uses <b>{finalEngine.models.execute}</b> (and <b>{finalEngine.models.hard}</b> for the hard
          steps). Stops at <b>{fmtUsd(finalEngine.cost.ceilingUsd)}</b> or
          <b>{finalEngine.stop.maxIters} tries</b>, whichever comes first.
        </div>
      </div>

      <div class="dial dial-plain">
        <div class="dial-head">
          <span class="dial-name">How carefully it checks its work</span>
          <span class="dial-poles mono">fast ⟷ fussy</span>
        </div>
        <input
          class="slider thumb-brass"
          type="range"
          min="0"
          max="1"
          step="0.01"
          value={dials.patience}
          style="--fill:{dials.patience * 100}%"
          aria-label="How carefully it checks its work"
          aria-valuetext={patienceText}
          oninput={(e) => (dials = { ...dials, patience: +e.currentTarget.value })}
        />
        <div class="dial-mean">
          Self-audits <b>{finalEngine.verify.mutationAudit
            ? `every ${finalEngine.verify.mutationEvery || 1} green`
            : 'off'}</b>. Tolerates <b>{finalEngine.stop.plateauLimit}</b> stalled tries and rolls
          back after <b>{finalEngine.stop.regressLimit}</b> regressions.
        </div>
      </div>

      <div class="dial dial-plain">
        <div class="dial-head">
          <span class="dial-name">How hands-off</span>
          <span class="dial-poles mono">with you ⟷ on its own</span>
        </div>
        <input
          class="slider thumb-brass"
          type="range"
          min="0"
          max="1"
          step="0.01"
          value={dials.autonomy}
          style="--fill:{dials.autonomy * 100}%"
          aria-label="How hands-off"
          aria-valuetext={autonomyText}
          oninput={(e) => (dials = { ...dials, autonomy: +e.currentTarget.value })}
        />
        <div class="dial-mean">
          Runs <b>{autonomyPlain}</b> — it <b>{permPlain}</b>. Up to
          <b>{finalEngine.maxTurns}</b> turns/phase, <b>{finalEngine.iterTimeoutMin}m</b>/try.
        </div>
      </div>
    {/snippet}

    {#snippet summaryAside()}
      <aside class="summary" aria-label="what will happen">
        <div class="summary-orb">
          <div class="orrery-mini">
            <svg viewBox="0 0 200 130" class="mini">
              <circle cx="100" cy="65" r="11" fill="var(--starlight)" opacity="0.9" />
              <circle cx="100" cy="65" r="17" fill="none" stroke="var(--em-mid)" stroke-width="0.6" opacity="0.5" />
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
              <circle cx="150" cy="40" r="7" fill="none" stroke="var(--ghost-brass)" stroke-width="1" stroke-dasharray="2 2" />
              {#if preview.auditOn}
                <line x1="100" y1="65" x2="150" y2="40" stroke="var(--auditor-white)" stroke-width="0.6" opacity="0.5" />
                <circle cx="40" cy="30" r="3" fill="var(--auditor-white)" opacity="0.8" />
              {/if}
              {#each acceptanceCriteria.filter((a) => a.trim()) as _ac, i}
                <circle
                  cx={150 + Math.cos(i * 1.4) * 14}
                  cy={40 + Math.sin(i * 1.4) * 14}
                  r="1.6"
                  fill="var(--em-mid)"
                  opacity="0.7"
                />
              {/each}
            </svg>
          </div>
          <span class="summary-state mono">will run</span>
        </div>
        <div class="summary-body">
          <h4 class="mono">{loopName.trim() || 'your loop'}</h4>
          <p class="summary-lede">
            Each pass it works toward the goal, then runs the gate. Done when that's green and
            <b>{critCount} criteri{critCount === 1 ? 'on' : 'a'}</b> hold. Stops at
            <b>{fmtUsd(finalEngine.cost.ceilingUsd)}</b> or <b>{finalEngine.stop.maxIters} tries</b>.
          </p>
          <ul class="summary-stats mono">
            <li><span>model</span><strong>{finalEngine.models.execute} · {finalEngine.models.hard} hard</strong></li>
            <li><span>budget</span><strong>{fmtUsd(finalEngine.cost.ceilingUsd)} / {finalEngine.stop.maxIters}</strong></li>
            <li><span>autonomy</span><strong>{autonomyPlain}</strong></li>
            <li>
              <span>gate tested</span>
              <strong class={gateTested ? 'on' : 'off'}>{gateTested ? '✓ yes' : 'not yet'}</strong>
            </li>
          </ul>
          <button class="night-btn mono" onclick={() => (nightOpen = !nightOpen)}>
            {nightOpen ? '▾' : '▸'} preview a full run
          </button>
          {#if nightOpen}
            <div class="night">
              <svg viewBox="0 0 200 50" class="night-svg">
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
          <p class="startnote mono">
            Creating <b>saves</b> this loop — it won't run until you press <b>✦ Start</b> in its
            system view.
          </p>
        </div>
      </aside>
    {/snippet}

    {#snippet doneWhen()}
      <div class="dod">
        <div class="dest-grid">
          <div class="ac">
            <div class="dlab mono">✦ WHAT “DONE” LOOKS LIKE <em class="req">required</em></div>
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
            <div class="dlab mono">THE TEST THAT DECIDES IT'S GREEN <em class="req">required</em></div>
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
      </div>
    {/snippet}

    {#snippet advancedBody()}
      <div class="adv" data-open={advOpen}>
        <button class="adv-top" onclick={() => (advOpen = !advOpen)}>
          <span class="adv-chev mono">▸</span>
          Advanced engine settings
          <span class="adv-sub mono">
            {#if overrides && Object.keys(overrides).length}
              <span class="odot"></span> overridden
            {:else if preset && preset !== 'balanced'}
              set by {PRESET_META[preset].label} preset
            {:else}
              all defaults
            {/if}
          </span>
        </button>
        {#if advOpen}
          <div class="adv-body">
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
          </div>
        {/if}
      </div>
    {/snippet}

    {#snippet validStatus()}
      <div class="valid mono">
        {#if createError}
          <span class="verr" role="alert">✕ {createError}</span>
        {:else if !activeValid.ok && touched}
          <span class="verr">⚠ {activeValid.errors[0]}</span>
        {:else if activeValid.ok}
          <span class="vok">{mode === 'edit' ? '✓ ready to save' : '✓ ready to create'}</span>
        {:else}
          <span class="vhint">fill in the essentials, then create</span>
        {/if}
      </div>
    {/snippet}

    {#snippet quickFooter()}
      <!-- This writes/edits the loop's loop.json; it does NOT start a run. The run is
           started later with ✦ Start inside the System view — keep the verbs distinct. -->
      <footer class="ftr">
        {@render validStatus()}
        <div class="actions">
          <button class="btn btn-ghost btn-md" onclick={onClose}>cancel</button>
          {#if mode === 'create'}
            <button
              class="btn btn-ghost btn-lg"
              disabled={!validation.ok || busy}
              onclick={() => ignite(true)}
              title="create the loop, then start the run in its system view"
            >
              {busy ? 'creating…' : '✦ Create & start'}
            </button>
          {/if}
          <button
            class="btn btn-primary btn-lg ignite"
            disabled={!validation.ok || busy}
            onclick={() => ignite(false)}
          >
            {busy
              ? mode === 'edit'
                ? 'saving…'
                : 'creating…'
              : mode === 'edit'
                ? '✦ Save loop'
                : '✦ Create loop'}
          </button>
        </div>
      </footer>
    {/snippet}

    {#snippet stepRail()}
      <div class="rail">
        {#each STEP_DEFS as s, i (s.key)}
          <button
            class="rstep {i < reached ? 'done' : ''}"
            aria-current={i === step}
            disabled={i > reached}
            onclick={() => goStep(i)}
          >
            <span class="rdot mono">{i < reached ? '✓' : i + 1}</span>
            <span class="rlabel">
              <span class="rname">{s.key}</span>
              <span class="rsub mono">{s.sub}</span>
            </span>
          </button>
        {/each}
      </div>
    {/snippet}

    {#snippet stepNav()}
      <div class="tc-nav">
        {#if step > 0}
          <button class="btn btn-ghost btn-md" onclick={prevStep}>← Back</button>
        {/if}
        <span class="nav-spring"></span>
        {#if step < STEP_DEFS.length - 1}
          {#if !stepValid(step)}<span class="verr mono">⚠ {stepHint(step)}</span>{/if}
          <button class="btn btn-primary btn-md" disabled={!stepValid(step)} onclick={nextStep}>
            Next →
          </button>
        {:else}
          {@render validStatus()}
          {#if mode === 'create'}
            <button
              class="btn btn-ghost btn-lg"
              disabled={!validation.ok || busy}
              onclick={() => ignite(true)}
              title="create the loop, then start the run in its system view"
            >
              {busy ? 'creating…' : '✦ Create & start'}
            </button>
          {/if}
          <button
            class="btn btn-primary btn-lg ignite"
            disabled={!validation.ok || busy}
            onclick={() => ignite(false)}
          >
            {busy
              ? mode === 'edit'
                ? 'saving…'
                : 'creating…'
              : mode === 'edit'
                ? '✦ Save loop'
                : '✦ Create loop'}
          </button>
        {/if}
      </div>
    {/snippet}

    {#snippet reviewBody()}
      <div class="review-grid">
        <div class="rev">
          <span class="rk mono">name</span>
          <span class="rv">
            {loopName.trim() || '—'}
            <small class="mono">id: {loopId.trim() || '—'}</small>
          </span>
          <button class="rev-change mono" onclick={() => goStep(0)}>change</button>
        </div>
        <div class="rev">
          <span class="rk mono">done when</span>
          <span class="rv">
            {#each acceptanceCriteria.filter((a) => a.trim()) as c}
              <span class="rev-crit">• {c}</span>
            {/each}
            <small class="mono">
              green on: {gateStages
                .filter((s) => s.command.trim())
                .map((s) => s.command)
                .join(' · ') || '—'}{gateTested ? ' · ✓ tested' : ''}
            </small>
          </span>
          <button class="rev-change mono" onclick={() => goStep(1)}>change</button>
        </div>
        <div class="rev">
          <span class="rk mono">guardrails</span>
          <span class="rv">
            {finalEngine.models.execute} · stops at {fmtUsd(finalEngine.cost.ceilingUsd)} or {finalEngine
              .stop.maxIters} tries
            <small class="mono">{autonomyPlain} · {permPlain}</small>
          </span>
          <button class="rev-change mono" onclick={() => goStep(2)}>change</button>
        </div>
      </div>
      <div class="startnote-box mono">
        ⚠ <b>Creating saves this loop — it won't run yet.</b> Start it with <b>✦ Start</b> from its
        system view. Keeping “create” and “start” separate is deliberate.
      </div>
    {/snippet}
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
    gap: var(--space-3);
    padding: var(--space-4) var(--space-5) var(--space-4);
  }

  .hdr {
    display: flex;
    align-items: baseline;
    gap: var(--space-3);
    border-bottom: 1px solid var(--hairline);
    padding-bottom: var(--space-3);
  }
  .title {
    font-size: var(--text-md);
    letter-spacing: 0.2em;
    color: var(--brass);
  }
  .sub {
    font-size: var(--text-2xs);
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
    font-size: var(--text-xs);
    transition:
      background var(--dur-feedback) var(--ease-standard),
      border-color var(--dur-feedback) var(--ease-standard),
      color var(--dur-feedback) var(--ease-standard);
  }
  .x:hover {
    background: color-mix(in srgb, var(--n4) 70%, transparent);
    border-color: var(--crimson);
    color: var(--crimson);
  }
  .x:active {
    background: color-mix(in srgb, var(--n4) 90%, transparent);
  }

  .taskfile {
    display: flex;
    align-items: center;
    gap: var(--space-2);
    margin-top: -4px;
    font-size: var(--text-xs);
  }
  .taskfile-btn {
    background: transparent;
    border: 1px solid var(--hairline);
    color: var(--plasma-cyan);
    border-radius: var(--radius-pill);
    padding: 3px 10px;
    font-size: var(--text-2xs);
    cursor: pointer;
    transition:
      background var(--dur-feedback) var(--ease-standard),
      border-color var(--dur-feedback) var(--ease-standard);
  }
  .taskfile-btn:hover {
    background: color-mix(in srgb, var(--n4) 60%, transparent);
    border-color: var(--plasma-cyan);
  }
  .taskfile-btn:active {
    background: color-mix(in srgb, var(--n4) 85%, transparent);
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
    gap: var(--space-1);
  }
  .flab {
    font-size: var(--text-xs);
    letter-spacing: 0.14em;
    color: var(--text-meta);
  }
  .inp {
    background: var(--surface-void);
    border: 1px solid var(--hairline);
    border-radius: var(--radius-sm);
    color: var(--starlight);
    font-family: var(--font-grotesk);
    font-size: var(--text-sm);
    padding: 7px 9px;
    transition: border-color var(--dur-feedback) var(--ease-standard);
    min-width: 0;
  }
  .inp.mono {
    font-family: var(--font-mono);
    font-size: var(--text-xs);
  }
  .inp:hover:not(:disabled) {
    border-color: color-mix(in srgb, var(--em-mid) 30%, var(--hairline));
  }
  .inp:focus {
    /* matches QAConsole's .qinput:focus / DecisionSheet's .answer:focus */
    border-color: color-mix(in srgb, var(--em-mid) 50%, transparent);
  }
  .inp:disabled {
    opacity: 0.4;
    cursor: not-allowed;
  }

  /* dials */
  .dial {
    margin-bottom: 14px;
  }
  .dial:last-child {
    margin-bottom: 0;
  }
  .dial input[type='range'] {
    width: 100%;
    appearance: none;
    height: 3px;
    border-radius: 2px;
    cursor: pointer;
    transition: filter var(--dur-feedback) var(--ease-standard);
    /* the filled portion ends at the actual value (--fill); past it the track is
       an unlit hairline, so the bright span literally equals the setting. */
    background: linear-gradient(
      90deg,
      var(--em-hi) 0%,
      var(--em-mid) var(--fill, 50%),
      var(--n3) var(--fill, 50%),
      var(--n3) 100%
    );
  }
  .dial input[type='range']:hover:not(:disabled) {
    filter: brightness(1.15);
  }
  .dial input[type='range']:disabled {
    opacity: 0.4;
    cursor: not-allowed;
  }
  /* thumb appearance + focus ring now come from the shared .slider.thumb-brass
     (primitives.css) — see the `class="slider thumb-brass"` on each dial <input>. */
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
  .night-btn {
    margin-top: 8px;
    background: transparent;
    border: 1px solid var(--hairline);
    color: var(--em-mid);
    border-radius: var(--radius-pill);
    padding: 5px 12px;
    font-size: var(--text-2xs);
    cursor: pointer;
    letter-spacing: 0.06em;
    transition:
      background var(--dur-feedback) var(--ease-standard),
      border-color var(--dur-feedback) var(--ease-standard);
  }
  .night-btn:hover {
    background: color-mix(in srgb, var(--n4) 55%, transparent);
    border-color: var(--panel-edge);
  }
  .night-btn:active {
    background: color-mix(in srgb, var(--n4) 80%, transparent);
  }
  .night {
    margin-top: 8px;
  }
  .night-svg {
    width: 100%;
    height: 50px;
  }
  .night-cap {
    font-size: var(--text-2xs);
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
    gap: var(--space-3);
  }
  .dlab {
    font-size: var(--text-xs);
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
    font-size: var(--text-xs);
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
    font-size: var(--text-xs);
  }
  .probe-btn {
    flex: none;
    background: transparent;
    border: 1px solid var(--hairline);
    color: var(--text-dim);
    border-radius: var(--radius-pill);
    padding: 4px 9px;
    font-size: var(--text-2xs);
    letter-spacing: 0.04em;
    cursor: pointer;
    white-space: nowrap;
    transition:
      background var(--dur-feedback) var(--ease-standard),
      border-color var(--dur-feedback) var(--ease-standard),
      color var(--dur-feedback) var(--ease-standard);
  }
  .probe-btn:hover:not(:disabled) {
    /* ghost hover (matches BodyView's .back:hover / DecisionSheet's .x:hover) */
    background: color-mix(in srgb, var(--n4) 55%, transparent);
    border-color: var(--panel-edge);
    color: var(--starlight);
  }
  .probe-btn:active:not(:disabled) {
    background: color-mix(in srgb, var(--n4) 80%, transparent);
  }
  .probe-btn:disabled {
    opacity: 0.4;
    cursor: not-allowed;
  }
  .probe-line {
    display: flex;
    align-items: center;
    gap: 8px;
    margin: -2px 0 6px;
    padding-left: 2px;
    font-size: var(--text-2xs);
    color: var(--text-meta);
  }
  .probe-line.ok {
    color: var(--em-hi);
  }
  .probe-line.bad {
    color: var(--status-err-core);
  }
  .probe-line.warn {
    color: var(--amber);
  }
  .probe-toggle {
    background: transparent;
    border: none;
    color: var(--em-mid);
    cursor: pointer;
    font-size: var(--text-2xs);
    text-decoration: underline;
    padding: 0;
    transition: color var(--dur-feedback) var(--ease-standard);
  }
  .probe-toggle:hover {
    color: var(--starlight);
  }
  .probe-tail {
    margin: -2px 0 8px;
    padding: 8px 10px;
    background: var(--surface-void);
    border: 1px solid var(--hairline);
    border-radius: var(--radius-sm);
    color: var(--text-dim);
    font-size: var(--text-2xs);
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
    font-size: var(--text-2xs);
    padding: 2px 4px;
    border-radius: var(--radius-sm);
    transition:
      background var(--dur-feedback) var(--ease-standard),
      color var(--dur-feedback) var(--ease-standard);
  }
  .mini-x:hover {
    background: color-mix(in srgb, var(--n4) 55%, transparent);
    color: var(--crimson);
  }
  .add {
    background: transparent;
    border: 1px dashed var(--hairline);
    color: var(--text-dim);
    border-radius: var(--radius-sm);
    padding: 5px 10px;
    font-size: var(--text-2xs);
    cursor: pointer;
    margin-top: 2px;
    transition:
      background var(--dur-feedback) var(--ease-standard),
      border-color var(--dur-feedback) var(--ease-standard),
      color var(--dur-feedback) var(--ease-standard);
  }
  .add:hover {
    background: color-mix(in srgb, var(--n4) 45%, transparent);
    border-color: var(--panel-edge);
    color: var(--starlight);
  }
  .add:active {
    background: color-mix(in srgb, var(--n4) 70%, transparent);
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
    font-size: var(--text-2xs);
    cursor: pointer;
    letter-spacing: 0.04em;
    transition: all var(--dur-feedback) var(--ease-standard);
  }
  .dtab:hover {
    background: color-mix(in srgb, var(--n4) 55%, var(--void-3));
    border-color: color-mix(in srgb, var(--em-mid) 40%, transparent);
  }
  .dtab:active {
    background: color-mix(in srgb, var(--n4) 80%, var(--void-3));
  }
  .dtab.on {
    border-color: var(--em-mid);
    color: var(--starlight);
  }
  .odot {
    width: 6px;
    height: 6px;
    border-radius: 50%;
    background: var(--em-hi);
    box-shadow: 0 0 6px var(--em-hi);
  }
  .drawer-body {
    margin-top: 10px;
    padding: var(--space-3) var(--space-4);
    background: var(--surface-raised);
    border: 1px solid var(--hairline);
    border-radius: var(--radius);
    display: flex;
    flex-direction: column;
    gap: var(--space-3);
  }
  .row {
    display: flex;
    gap: var(--space-3);
    flex-wrap: wrap;
    align-items: flex-end;
  }
  .kv {
    display: flex;
    flex-direction: column;
    gap: var(--space-1);
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
    font-size: var(--text-xs);
    color: var(--text-meta);
    letter-spacing: 0.08em;
  }
  .kv select {
    background: var(--surface-void);
    border: 1px solid var(--hairline);
    color: var(--starlight);
    border-radius: var(--radius-sm);
    padding: 6px 8px;
    font-family: var(--font-mono);
    font-size: var(--text-xs);
    cursor: pointer;
    transition: border-color var(--dur-feedback) var(--ease-standard);
  }
  .kv select:hover {
    border-color: color-mix(in srgb, var(--em-mid) 30%, var(--hairline));
  }
  .kv input[type='number'] {
    width: 84px;
  }
  .def {
    font-size: var(--text-2xs);
    color: var(--text-faint);
  }
  .reset {
    align-self: flex-start;
    background: transparent;
    border: 1px solid var(--border-hairline);
    color: var(--em-mid);
    border-radius: var(--radius-pill);
    padding: 4px 11px;
    font-size: var(--text-2xs);
    cursor: pointer;
    transition: background var(--dur-feedback) var(--ease-standard);
  }
  .reset:hover {
    background: var(--n4);
  }
  .reset:active {
    background: color-mix(in srgb, var(--n4) 85%, white 15%);
  }

  /* footer */
  .ftr {
    display: flex;
    align-items: center;
    gap: var(--space-3);
    border-top: 1px solid var(--hairline);
    padding-top: var(--space-3);
  }
  .valid {
    flex: 1;
    font-size: var(--text-xs);
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
  /* M4.5: fill/border/color/shape/size now come from the shared `.btn`/`.btn-primary`/
     `.btn-lg` primitives (primitives.css) — solid light replaces solid amber, the
     monochrome-inversion CTA. `.ignite` only keeps this button's own letter-spacing
     and press/hover lift. */
  .ignite {
    letter-spacing: 0.08em;
  }
  .ignite:hover:not(:disabled) {
    transform: translateY(-1px);
  }
  .ignite:active:not(:disabled) {
    transform: translateY(0);
  }

  @media (max-width: 720px) {
    .dest-grid {
      grid-template-columns: 1fr;
    }
    .tc-2col {
      grid-template-columns: 1fr;
    }
    .summary {
      position: static;
    }
    .rail {
      flex-direction: row;
      overflow-x: auto;
      border-right: none;
      border-bottom: 1px solid var(--hairline);
      padding-right: 0;
      padding-bottom: var(--space-2);
    }
    .rstep {
      flex: none;
    }
  }

  /* ══ REDESIGN — gallery / lanes / steps / summary / presets / accordion ══ */

  .gallery-lead {
    font-size: var(--text-xs);
    letter-spacing: 0.14em;
    text-transform: uppercase;
    color: var(--text-meta);
    margin: 0 0 var(--space-2);
  }
  .recipes {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
    gap: var(--space-3);
  }
  .recipe {
    position: relative;
    display: flex;
    flex-direction: column;
    gap: var(--space-2);
    text-align: left;
    min-height: 148px;
    padding: var(--space-4);
    border: 1px solid var(--hairline);
    border-radius: var(--radius);
    background: color-mix(in srgb, var(--n3) 40%, transparent);
    color: var(--text-dim);
    cursor: pointer;
    font-family: var(--font-grotesk);
    transition:
      border-color var(--dur-feedback) var(--ease-standard),
      background var(--dur-feedback) var(--ease-standard),
      transform var(--dur-feedback) var(--ease-standard);
  }
  .recipe:hover {
    border-color: color-mix(in srgb, var(--em-mid) 45%, transparent);
    background: color-mix(in srgb, var(--n4) 55%, transparent);
    transform: translateY(-2px);
  }
  .recipe.sel {
    border-color: var(--em-hi);
  }
  .recipe-tag {
    position: absolute;
    top: 10px;
    right: 10px;
    font-size: var(--text-2xs);
    letter-spacing: 0.08em;
    text-transform: uppercase;
    color: var(--n1);
    background: var(--em-hi);
    border-radius: var(--radius-pill);
    padding: 2px 7px;
    font-weight: 600;
  }
  .recipe-glyph {
    font-size: var(--text-xl);
    color: var(--starlight);
    line-height: 1;
  }
  .recipe-name {
    font-size: var(--text-lg);
    font-weight: 600;
    color: var(--starlight);
    letter-spacing: -0.01em;
  }
  .recipe-blurb {
    font-size: var(--text-sm);
    color: var(--text-dim);
    line-height: 1.45;
  }
  .recipe-meta {
    margin-top: auto;
    font-size: var(--text-2xs);
    letter-spacing: 0.02em;
    color: var(--text-faint);
  }

  .tc-topbar {
    display: flex;
    align-items: center;
    gap: var(--space-3);
    flex-wrap: wrap;
  }
  .tc-chip {
    display: inline-flex;
    align-items: center;
    gap: var(--space-2);
    border: 1px solid var(--hairline);
    border-radius: var(--radius-pill);
    padding: 4px 6px 4px 12px;
    background: color-mix(in srgb, var(--n3) 45%, transparent);
    font-size: var(--text-sm);
    color: var(--starlight);
  }
  .tc-chip-glyph {
    font-size: var(--text-md);
  }
  .tc-chip-change {
    border: none;
    background: color-mix(in srgb, var(--n4) 70%, transparent);
    color: var(--text-dim);
    border-radius: var(--radius-pill);
    font-size: var(--text-2xs);
    letter-spacing: 0.06em;
    text-transform: uppercase;
    padding: 3px 9px;
    cursor: pointer;
    transition: color var(--dur-feedback) var(--ease-standard);
  }
  .tc-chip-change:hover {
    color: var(--starlight);
  }
  .tc-lane {
    margin-left: auto;
  }

  .tc-2col {
    display: grid;
    grid-template-columns: 1fr 264px;
    gap: var(--space-4);
    align-items: start;
  }
  .tc-main {
    display: flex;
    flex-direction: column;
    gap: var(--space-3);
    min-width: 0;
  }

  .id-field :global(.inp) {
    max-width: 260px;
  }
  .fhelp {
    font-size: var(--text-2xs);
    color: var(--text-faint);
    line-height: 1.4;
  }
  .req {
    color: var(--amber);
    font-style: normal;
    font-size: var(--text-2xs);
    letter-spacing: 0.06em;
  }
  .opt {
    color: var(--text-faint);
    font-style: normal;
    font-size: var(--text-2xs);
    letter-spacing: 0.04em;
  }

  /* guided step chrome */
  .step-h {
    margin-bottom: var(--space-2);
  }
  .step-h .sk {
    font-size: var(--text-2xs);
    letter-spacing: 0.16em;
    text-transform: uppercase;
    color: var(--text-faint);
  }
  .step-h h3 {
    margin: 4px 0 3px;
    font-size: var(--text-lg);
    font-weight: 600;
    color: var(--starlight);
    letter-spacing: -0.01em;
  }
  .step-h p {
    margin: 0;
    font-size: var(--text-sm);
    color: var(--text-dim);
    max-width: 52ch;
    line-height: 1.45;
  }
  .tc-step {
    display: flex;
    flex-direction: column;
    gap: var(--space-3);
    min-height: 280px;
    min-width: 0;
  }
  /* the guided step column is narrower than the quick lane (the rail takes 172px),
     so the Definition-of-Done stacks to a single column here instead of overflowing */
  .tc-step .dest-grid {
    grid-template-columns: 1fr;
  }

  /* guided step rail */
  .rail {
    display: flex;
    flex-direction: column;
    gap: 2px;
    border-right: 1px solid var(--hairline);
    padding-right: var(--space-3);
  }
  .tc-main:has(.rail) {
    display: grid;
    grid-template-columns: 172px 1fr;
    gap: var(--space-3) var(--space-4);
    align-items: start;
  }
  /* the rail is column 1; the step body + nav share column 2 (nav must not fall
     into the narrow rail column via grid auto-placement) */
  .tc-main:has(.rail) .tc-step {
    grid-column: 2;
  }
  .tc-main:has(.rail) .tc-nav {
    grid-column: 2;
  }
  .tc-nav .btn {
    white-space: nowrap;
  }
  .rstep {
    display: flex;
    align-items: center;
    gap: var(--space-2);
    padding: 8px;
    border: none;
    background: transparent;
    border-radius: var(--radius-sm);
    text-align: left;
    color: var(--text-meta);
    cursor: pointer;
    font-family: var(--font-grotesk);
    transition:
      background var(--dur-feedback) var(--ease-standard),
      color var(--dur-feedback) var(--ease-standard);
  }
  .rstep:hover:not(:disabled) {
    background: color-mix(in srgb, var(--n4) 45%, transparent);
  }
  .rstep:disabled {
    cursor: default;
    opacity: 0.55;
  }
  .rstep[aria-current='true'] {
    background: color-mix(in srgb, var(--n4) 60%, transparent);
    color: var(--starlight);
  }
  .rdot {
    flex: none;
    width: 22px;
    height: 22px;
    display: grid;
    place-items: center;
    border: 1px solid var(--hairline);
    border-radius: 50%;
    font-size: var(--text-2xs);
    color: var(--text-meta);
  }
  .rstep[aria-current='true'] .rdot {
    border-color: var(--em-hi);
    color: var(--em-hi);
  }
  .rstep.done .rdot {
    background: var(--plasma-green);
    border-color: var(--plasma-green);
    color: var(--n1);
  }
  .rlabel {
    display: flex;
    flex-direction: column;
    gap: 1px;
  }
  .rname {
    font-size: var(--text-sm);
  }
  .rsub {
    font-size: var(--text-2xs);
    color: var(--text-faint);
  }

  .tc-nav {
    display: flex;
    align-items: center;
    gap: var(--space-3);
    margin-top: var(--space-4);
    padding-top: var(--space-3);
    border-top: 1px solid var(--hairline);
  }
  .nav-spring {
    flex: 1;
  }

  /* guardrail presets */
  .presets {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: var(--space-2);
    margin-bottom: var(--space-2);
  }
  .pchip {
    display: inline-flex;
    flex-direction: column;
    gap: 1px;
    text-align: left;
    border: 1px solid var(--hairline);
    background: color-mix(in srgb, var(--n3) 40%, transparent);
    color: var(--text-dim);
    border-radius: var(--radius);
    padding: 7px 13px;
    cursor: pointer;
    font-family: var(--font-grotesk);
    transition:
      border-color var(--dur-feedback) var(--ease-standard),
      color var(--dur-feedback) var(--ease-standard),
      background var(--dur-feedback) var(--ease-standard);
  }
  .pchip b {
    font-weight: 600;
    font-size: var(--text-sm);
  }
  .pchip small {
    font-size: 9px;
    letter-spacing: 0.04em;
    text-transform: uppercase;
    color: var(--text-faint);
  }
  .pchip:hover {
    border-color: color-mix(in srgb, var(--em-mid) 40%, transparent);
    color: var(--starlight);
  }
  .pchip.on {
    border-color: var(--em-hi);
    background: color-mix(in srgb, var(--n4) 65%, transparent);
    color: var(--starlight);
  }
  .pchip.on small {
    color: var(--text-meta);
  }
  .preset-cur {
    font-size: var(--text-2xs);
    color: var(--text-faint);
  }

  /* plain-language dials */
  .dial-plain {
    margin-bottom: var(--space-4);
  }
  .dial-head {
    display: flex;
    justify-content: space-between;
    align-items: baseline;
    gap: var(--space-3);
    margin-bottom: 5px;
  }
  .dial-name {
    font-size: var(--text-md);
    color: var(--starlight);
    font-weight: 500;
  }
  .dial-poles {
    font-size: var(--text-2xs);
    letter-spacing: 0.06em;
    text-transform: uppercase;
    color: var(--text-faint);
  }
  .dial-mean {
    margin-top: 6px;
    font-size: var(--text-sm);
    color: var(--text-dim);
    line-height: 1.45;
  }
  .dial-mean b {
    color: var(--starlight);
    font-weight: 500;
  }

  /* advanced accordion (wraps the existing drawers) */
  .adv {
    border: 1px solid var(--hairline);
    border-radius: var(--radius);
    overflow: hidden;
  }
  .adv-top {
    width: 100%;
    display: flex;
    align-items: center;
    gap: var(--space-2);
    padding: 12px var(--space-4);
    background: color-mix(in srgb, var(--n3) 35%, transparent);
    border: none;
    color: var(--starlight);
    font-family: var(--font-grotesk);
    font-size: var(--text-md);
    text-align: left;
    cursor: pointer;
    transition: background var(--dur-feedback) var(--ease-standard);
  }
  .adv-top:hover {
    background: color-mix(in srgb, var(--n4) 50%, transparent);
  }
  .adv-chev {
    color: var(--text-faint);
    font-size: var(--text-xs);
    transition: transform var(--dur-mid) var(--ease-standard);
  }
  .adv[data-open='true'] .adv-chev {
    transform: rotate(90deg);
  }
  .adv-sub {
    margin-left: auto;
    display: inline-flex;
    align-items: center;
    gap: 6px;
    font-size: var(--text-2xs);
    letter-spacing: 0.03em;
    color: var(--text-faint);
  }
  .adv-body {
    padding: var(--space-3) var(--space-4) var(--space-4);
    border-top: 1px solid var(--hairline);
  }

  /* live summary aside */
  .summary {
    position: sticky;
    top: 0;
    border: 1px solid var(--panel-edge);
    border-radius: var(--radius);
    background: var(--surface-raised);
    overflow: hidden;
  }
  .summary-orb {
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: var(--space-1);
    padding: var(--space-3) var(--space-3) var(--space-2);
    border-bottom: 1px solid var(--hairline);
  }
  .summary-orb .orrery-mini {
    padding: 0;
  }
  .summary-orb .mini {
    max-width: 150px;
  }
  .summary-state {
    font-size: var(--text-2xs);
    letter-spacing: 0.16em;
    text-transform: uppercase;
    color: var(--amber);
  }
  .summary-body {
    padding: var(--space-3) var(--space-4) var(--space-4);
  }
  .summary-body h4 {
    margin: 0 0 4px;
    font-size: var(--text-md);
    color: var(--starlight);
    font-weight: 500;
    letter-spacing: 0.02em;
  }
  .summary-lede {
    margin: 0 0 var(--space-3);
    font-size: var(--text-sm);
    color: var(--text-dim);
    line-height: 1.5;
  }
  .summary-lede b {
    color: var(--starlight);
    font-weight: 500;
  }
  .summary-stats {
    list-style: none;
    margin: 0 0 var(--space-2);
    padding: 0;
    border-top: 1px solid var(--hairline);
  }
  .summary-stats li {
    display: flex;
    justify-content: space-between;
    gap: var(--space-2);
    padding: 7px 0;
    border-bottom: 1px solid var(--hairline);
    font-size: var(--text-2xs);
  }
  .summary-stats span {
    color: var(--text-meta);
    letter-spacing: 0.04em;
    text-transform: uppercase;
  }
  .summary-stats strong {
    color: var(--starlight);
    font-weight: 500;
    text-align: right;
  }
  .summary-stats strong.on {
    color: var(--plasma-green);
  }
  .summary-stats strong.off {
    color: var(--text-faint);
  }
  .startnote {
    margin: var(--space-3) 0 0;
    font-size: var(--text-2xs);
    color: var(--text-dim);
    line-height: 1.5;
    border: 1px solid color-mix(in srgb, var(--amber) 30%, transparent);
    background: color-mix(in srgb, var(--amber) 8%, transparent);
    border-radius: var(--radius-sm);
    padding: 9px 11px;
  }
  .startnote b {
    color: var(--amber);
    font-weight: 500;
  }

  /* review (check-answers) */
  .review-grid {
    border: 1px solid var(--hairline);
    border-radius: var(--radius);
    overflow: hidden;
  }
  .rev {
    display: grid;
    grid-template-columns: 110px 1fr auto;
    gap: var(--space-3);
    padding: 12px var(--space-4);
    border-bottom: 1px solid var(--hairline);
    align-items: baseline;
  }
  .rev:last-child {
    border-bottom: none;
  }
  .rk {
    font-size: var(--text-2xs);
    letter-spacing: 0.08em;
    text-transform: uppercase;
    color: var(--text-meta);
  }
  .rev .rv {
    color: var(--starlight);
    font-size: var(--text-sm);
    line-height: 1.5;
    display: flex;
    flex-direction: column;
    gap: 2px;
  }
  .rev .rv small {
    color: var(--text-faint);
    font-size: var(--text-2xs);
  }
  .rev-crit {
    display: block;
  }
  .rev-change {
    background: transparent;
    border: none;
    color: var(--amber);
    font-size: var(--text-2xs);
    letter-spacing: 0.06em;
    text-transform: uppercase;
    cursor: pointer;
    padding: 2px 4px;
  }
  .rev-change:hover {
    text-decoration: underline;
  }
  .startnote-box {
    margin-top: var(--space-3);
    font-size: var(--text-xs);
    color: var(--text-dim);
    line-height: 1.5;
    border: 1px solid color-mix(in srgb, var(--amber) 30%, transparent);
    background: color-mix(in srgb, var(--amber) 8%, transparent);
    border-radius: var(--radius-sm);
    padding: 10px 12px;
  }
  .startnote-box b {
    color: var(--amber);
    font-weight: 500;
  }

  /* ── external-adapter (BMAD / QA) recipe surface ──────────────────────────
     Chrome stays monochrome (M5 calm-chrome law): grays + amber/red only. */
  .gallery-sub {
    font-size: var(--text-xs);
    letter-spacing: 0.14em;
    text-transform: uppercase;
    color: var(--text-meta);
    margin: var(--space-4) 0 var(--space-2);
  }
  .recipe-ext .recipe-glyph {
    color: var(--em-hi);
  }
  .ext-field {
    display: flex;
    flex-direction: column;
    gap: var(--space-2);
    margin: var(--space-3) 0;
  }
  .ext-presets {
    margin-bottom: 0;
  }
  .ext-toggle {
    display: flex;
    align-items: flex-start;
    gap: var(--space-2);
    font-size: var(--text-sm);
    color: var(--text-dim);
    line-height: 1.4;
    cursor: pointer;
    margin: var(--space-2) 0;
  }
  .ext-toggle input {
    margin-top: 2px;
    accent-color: var(--em-hi);
    flex: none;
  }
  .ext-toggle em {
    color: var(--text-faint);
    font-style: normal;
  }
  .ext-toggles {
    display: flex;
    flex-direction: column;
    gap: var(--space-1);
    margin: var(--space-3) 0;
  }
  .phase-grid {
    display: flex;
    flex-direction: column;
    gap: 4px;
    margin-bottom: var(--space-2);
  }
  .phase-row {
    display: grid;
    grid-template-columns: 72px 1fr 108px;
    align-items: center;
    gap: var(--space-2);
  }
  .phase-head {
    font-size: var(--text-2xs);
    letter-spacing: 0.08em;
    text-transform: uppercase;
    color: var(--text-faint);
  }
  .phase-name {
    font-size: var(--text-xs);
    color: var(--text-dim);
  }
  .phase-row .inp,
  .phase-row select {
    padding: 4px 8px;
    font-size: var(--text-xs);
  }
  .ext-modes {
    margin-top: var(--space-2);
  }
  .ext-prereq {
    margin-top: var(--space-3);
    font-size: var(--text-xs);
    color: var(--text-dim);
    line-height: 1.5;
    border: 1px solid var(--hairline);
    border-radius: var(--radius-sm);
    padding: 9px 11px;
    background: color-mix(in srgb, var(--n3) 30%, transparent);
  }
  .ext-prereq code,
  .fhelp code,
  .def code {
    font-family: var(--font-mono);
    font-size: 0.92em;
    color: var(--text-meta);
  }
</style>
