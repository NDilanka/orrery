// Cosmos store (A4) — the multi-loop home. Holds the registry of loops, each
// reduced to a lightweight Tier-1 summary the Cosmos field renders: status /
// restState (which not-running palette), cumUsd, the cost-horizon fraction, and
// the current work item. "Creating a loop = igniting a star"; this is the list
// of stars.
//
//   - In Tauri: source loop defs via `invoke('list_loops', { loopsDir })`, then
//     `invoke('load_run', { stateDir, adapter })` per loop → reduce already done
//     Rust-side. (We keep a dev fallback so the browser preview is never blank.)
//   - In dev (no Tauri): load the seed loop.json set + each loop's fixture log,
//     run it through reduce.ts, and derive the summary. The A3 "showcase" demo
//     is registered as a loop too.
//
// Cosmos = Tier-1 ONLY (plan §3 glance hierarchy): no planets / chambers at this
// altitude — color + motion + silhouette + the one cost-horizon ring, nothing
// more. The System view (the existing Observatory) is what you fly *into*.

import type { RestState, RunState, RunStatus } from '../types';
import { reduce } from '../reduce';
import { normalizeAll } from '../adapters';
import { hasTauri, LOOPS, type LoopChoice } from '../transport';
import { DEFAULT_LOOPS_DIR } from '../paths';
import { base } from '$app/paths';
import { browser } from '$app/environment';

/** Wire shape of `probe_command` (PROTOCOL §6, U3 Task 3). */
export interface ProbeResult {
  exitCode: number | null;
  durationMs: number;
  tail: string;
  timedOut: boolean;
}

/** The Tier-1 summary the Cosmos renders for one star-system (one loop). */
export interface LoopSummary {
  id: string;
  name: string;
  theme: string;
  adapter: 'generic' | 'bmad';
  // Tier-1 glanceables (plan §3 "Cosmos = Tier-1 only")
  status: RunStatus;
  restState: RestState; // which not-running palette, if any
  cumUsd: number;
  ceilingUsd: number | null; // for the cost-horizon ring
  horizonFrac: number; // 0..1+ : ring tightness by spend
  currentItem: string | null;
  // small extras for the hover/label card
  itemCount: number;
  doneCount: number;
  events: number;
  ratePerMin: number;
  // ── Task 4 (wave U1) wall-clock anchor, threaded straight from run.lastEventAt ──
  lastEventAt: string | null;
  // ── Task 2 (wave U1) trust signal: the current item's claimed-vs-verified state.
  // 'verified' = an independent verifier confirmed it (certified); 'unverified' = the agent
  // claims a pass (gate green / in review) but no verifier has confirmed it yet; null = nothing
  // to report (no current item, or it's not yet claimed-green).
  trust: 'verified' | 'unverified' | null;
  // ── Wave 3 roster-at-scale derived fields (TS-only, NOT parity-bound) ──
  // needsYou: this loop is waiting on a human (handoff beacon or a crash).
  // Shared by the filter pills, the per-row badge, and the N-needs-you count.
  needsYou: boolean;
  // urgency: a sort weight so the DOM roster floats the loops that want
  // attention to the top (handoff/error → running → quota → idle → done).
  // The Pixi star field stays POSITIONAL — this orders the roster only.
  urgency: number;
  // retroStatus: rolled up from the loaded state's epics (groups). 'pending'
  // wins (a retro is owed) over 'done'; null when no group carries a retro.
  retroStatus: 'pending' | 'done' | null;
}

// Roster sort weight by state — lower sorts first. Handoff / error are the
// "needs you" tier and lead; certified-done sinks to the bottom. We read the
// rest-state first (it wins over status), then fall back to status.
function urgencyOf(status: RunStatus, restState: RestState): number {
  if (restState === 'handoff-beacon' || status === 'handoff' || status === 'error') return 0;
  if (restState === 'quota-frost' || status === 'quota-wait') return 3;
  if (restState === 'stopped-ember') return 4;
  if (restState === 'certified-done') return 6;
  if (status === 'running') return 1;
  if (status === 'stopping') return 2;
  return 5; // idle ember
}

// Roll the per-epic retro status up to one roster tag: a pending retro (owed)
// outranks a done one; ignore 'optional'/null so the row stays clean.
function rollupRetro(groups: RunState['groups']): 'pending' | 'done' | null {
  let sawDone = false;
  for (const g of Object.values(groups)) {
    if (g.retroStatus === 'pending') return 'pending';
    if (g.retroStatus === 'done') sawDone = true;
  }
  return sawDone ? 'done' : null;
}

// Default ceiling when a loop.json / engine never declares one (keeps the
// horizon ring honest rather than absent). Matches run.svelte's fallback.
const DEFAULT_CEILING = 80;

function summarize(
  choice: { id: string; name: string; theme: string; adapter: 'generic' | 'bmad' },
  state: RunState,
  ceilingUsd: number | null,
): LoopSummary {
  const items = Object.values(state.items);
  const ceil = ceilingUsd ?? state.cost.ceilingUsd ?? DEFAULT_CEILING;
  const horizonFrac = ceil > 0 ? Math.min(2, state.run.cumUsd / ceil) : 0;
  const status = state.run.status;
  const restState = state.run.restState;
  // "needs you" = a human gate: a handoff beacon or an outright error.
  const needsYou = restState === 'handoff-beacon' || status === 'handoff' || status === 'error';
  // the current item's claimed-vs-verified trust state (Task 2) — mirrors run.svelte's
  // auditTargetKey/claimedGreen logic, applied to just the current item for the roster card.
  const current = state.currentItem ? (state.items[state.currentItem] ?? null) : null;
  const trust: LoopSummary['trust'] = current?.certified
    ? 'verified'
    : current && (current.gate?.green || current.status === 'review')
      ? 'unverified'
      : null;
  return {
    id: choice.id,
    name: choice.name,
    theme: choice.theme,
    adapter: choice.adapter,
    status,
    restState,
    cumUsd: state.run.cumUsd,
    ceilingUsd: ceil,
    horizonFrac,
    currentItem: state.currentItem,
    itemCount: items.length,
    doneCount: items.filter((it) => it.status === 'done').length,
    events: state.events,
    ratePerMin: state.cost.ratePerMin,
    lastEventAt: state.run.lastEventAt,
    trust,
    needsYou,
    urgency: urgencyOf(status, restState),
    retroStatus: rollupRetro(state.groups),
  };
}

// resolve a fixture URL under SvelteKit's base path
function withBase(url: string): string {
  return `${base}/${url}`;
}

async function fetchText(url: string): Promise<string> {
  const res = await fetch(url);
  if (!res.ok) throw new Error(`cosmos: failed to fetch ${url} (${res.status})`);
  return res.text();
}

function parseJsonl(text: string) {
  const out = [];
  for (const raw of text.split(/\r?\n/)) {
    const line = raw.trim();
    if (!line) continue;
    try {
      out.push(JSON.parse(line));
    } catch {
      // tolerate a trailing partial / malformed line
    }
  }
  return out;
}

// Per-loop cost ceiling for the dev summaries. The seed loop.json files carry
// engine.cost.ceilingUsd = 3 for the generic loops; the bmad/demo runs are big
// multi-hour sprints, so a larger ceiling keeps the horizon legible. (When the
// log itself emits a `cost-alert` with a ceiling, the reducer wins.)
const DEV_CEILINGS: Record<string, number> = {
  roman: 3,
  calc: 3,
  bmad: 30,
  demo: 8,
  'failed-dark': 8,
};

/** Reduce one dev loop's fixture into a Tier-1 summary. */
async function devSummary(choice: LoopChoice): Promise<LoopSummary> {
  const text = await fetchText(withBase(choice.fixtureUrl));
  const events = normalizeAll(choice.adapter, parseJsonl(text));
  let checkpoint;
  if (choice.checkpointUrl) {
    try {
      checkpoint = JSON.parse(await fetchText(withBase(choice.checkpointUrl)));
    } catch {
      checkpoint = undefined;
    }
  }
  const state = reduce(events, { checkpoint, loopId: choice.id });
  return summarize(choice, state, DEV_CEILINGS[choice.id] ?? null);
}

class CosmosStore {
  loops = $state<LoopSummary[]>([]);
  loading = $state(true);
  source = $state<'tauri' | 'dev'>('dev');
  error = $state<string | null>(null);
  // Set only when we're running under Tauri and the real backend load failed,
  // forcing the fixture fallback below — i.e. the Cosmos LOOKS populated but is
  // showing fake demo data. Non-Tauri dev mode (fixtures are the intended source
  // there) must never set this. Cleared on the next successful Tauri load.
  backendError = $state<string | null>(null);

  // ── A5 Tuning Console ──────────────────────────────────────────────────────
  // `console` is null when closed; otherwise it carries the edit target (an
  // existing loop id to edit/clone, or null = author a brand-new loop).
  console = $state<{ mode: 'create' | 'edit'; editId: string | null } | null>(null);

  /** Load (or reload) every registered loop's Tier-1 summary. */
  async load(): Promise<void> {
    if (!browser) return;
    this.loading = true;
    this.error = null;
    try {
      if (hasTauri()) {
        this.source = 'tauri';
        await this.loadTauri();
        // a real backend load just succeeded — any prior "showing demo fixtures" warning is stale
        this.backendError = null;
      } else {
        this.source = 'dev';
        await this.loadDev();
      }
    } catch (e) {
      const detail = e instanceof Error ? e.message : String(e);
      this.error = detail;
      // Tauri backend failed — we're about to silently fall back to fixtures below.
      // Record that (never set in plain-browser dev, where fixtures ARE the source).
      if (this.source === 'tauri') {
        this.backendError = `couldn't list loops at ${DEFAULT_LOOPS_DIR} — ${detail}`;
      }
      // dev fallback so the Cosmos is never blank even if Tauri commands fail
      if (this.loops.length === 0) {
        try {
          await this.loadDev();
        } catch {
          /* leave the error surfaced */
        }
      }
    } finally {
      this.loading = false;
    }
  }

  private async loadDev(): Promise<void> {
    const summaries = await Promise.all(LOOPS.map((c) => devSummary(c)));
    this.loops = summaries;
  }

  private async loadTauri(): Promise<void> {
    const { invoke } = await import('@tauri-apps/api/core');
    // list_loops(loopsDir) → LoopDef[] (PROTOCOL §6/§7)
    const defs = (await invoke('list_loops', { loopsDir: DEFAULT_LOOPS_DIR })) as Array<{
      id: string;
      name: string;
      theme?: string;
      adapter: string;
      stateDir: string;
      logFile?: string;
    }>;
    const summaries: LoopSummary[] = [];
    for (const def of defs) {
      const adapter = def.adapter === 'bmad' ? 'bmad' : 'generic';
      try {
        // load_run(stateDir, adapter, logFile) → an already-reduced RunState (Rust side).
        // Pass logFile so the Tier-1 summary reads the SAME log the loop writes (the engine
        // writes log.jsonl); without it a real bmad run would read a non-existent file and
        // always look idle in the Cosmos.
        const state = (await invoke('load_run', {
          stateDir: def.stateDir,
          adapter: def.adapter,
          logFile: def.logFile,
        })) as RunState;
        summaries.push(
          summarize(
            { id: def.id, name: def.name, theme: def.theme ?? 'plasma', adapter },
            state,
            DEV_CEILINGS[def.id] ?? null,
          ),
        );
      } catch {
        // a loop with no readable stateDir yet still appears as a dim ember
        summaries.push({
          id: def.id,
          name: def.name,
          theme: def.theme ?? 'plasma',
          adapter,
          status: 'idle',
          restState: null,
          cumUsd: 0,
          ceilingUsd: DEV_CEILINGS[def.id] ?? null,
          horizonFrac: 0,
          currentItem: null,
          itemCount: 0,
          doneCount: 0,
          events: 0,
          ratePerMin: 0,
          lastEventAt: null,
          trust: null,
          needsYou: false,
          urgency: urgencyOf('idle', null),
          retroStatus: null,
        });
      }
    }
    this.loops = summaries;
  }

  /** Open the Tuning Console to author a brand-new loop (the ✦ ignite affordance). */
  igniteNew(): void {
    this.console = { mode: 'create', editId: null };
  }
  /** Open the Tuning Console to edit an existing loop (the per-loop ✎ affordance). */
  editLoop(id: string): void {
    this.console = { mode: 'edit', editId: id };
  }
  /** Close the console without creating anything. */
  dismissIgnite(): void {
    this.console = null;
  }

  /** Ids already in use — the console forbids colliding with these. */
  get existingIds(): string[] {
    return this.loops.map((l) => l.id);
  }

  /**
   * Read a loop's full loop.json so the console can prefill an edit. In Tauri we
   * have list_loops (carries the opaque engine); in dev we fetch the seed file.
   */
  async loadLoopDef(id: string): Promise<Record<string, unknown> | null> {
    if (!browser) return null;
    try {
      if (hasTauri()) {
        const { invoke } = await import('@tauri-apps/api/core');
        const defs = (await invoke('list_loops', { loopsDir: DEFAULT_LOOPS_DIR })) as Array<
          Record<string, unknown>
        >;
        return defs.find((d) => d.id === id) ?? null;
      }
      // dev: the seed loop.json files live under loops/<id>/loop.json, served as
      // static assets (vite serves the repo root? no) — fall back to the public
      // copy under static/loops if present, else null (console seeds from blueprint).
      const res = await fetch(withBase(`loops/${id}/loop.json`));
      if (res.ok) return (await res.json()) as Record<string, unknown>;
    } catch {
      /* fall through to blueprint defaults */
    }
    return null;
  }

  /**
   * Persist a console-authored loop.json. In Tauri this invokes `create_loop`
   * (or `update_loop` when editing); in dev (no Tauri) it no-ops gracefully and
   * pushes a provisional summary so the new star shows up immediately for the
   * preview. Returns the created/updated loop id.
   */
  async createLoop(
    def: Record<string, unknown>,
    opts: { mode: 'create' | 'edit'; editId?: string | null } = { mode: 'create' },
  ): Promise<{ id: string; persisted: boolean }> {
    const id = String(def.id ?? '');
    if (browser && hasTauri()) {
      const { invoke } = await import('@tauri-apps/api/core');
      if (opts.mode === 'edit' && opts.editId) {
        await invoke('update_loop', { loopsDir: DEFAULT_LOOPS_DIR, id: opts.editId, def });
      } else {
        await invoke('create_loop', { loopsDir: DEFAULT_LOOPS_DIR, def });
      }
      await this.load(); // re-read the registry so the new system appears
      return { id, persisted: true };
    }
    // dev: no Tauri — we can't write the file, but the console still validates and
    // we provisionally add the star so the Cosmos reflects the act (plan §4
    // "in dev the console should still render and validate, no-op-ing create").
    if (opts.mode === 'create' && !this.loops.some((l) => l.id === id)) {
      const theme = String(def.theme ?? 'plasma');
      const ceiling =
        (def.engine as { cost?: { ceilingUsd?: number } } | undefined)?.cost?.ceilingUsd ?? null;
      this.loops = [
        ...this.loops,
        {
          id,
          name: String(def.name ?? id),
          theme,
          adapter: 'generic',
          status: 'idle',
          restState: null,
          cumUsd: 0,
          ceilingUsd: ceiling,
          horizonFrac: 0,
          currentItem: null,
          itemCount: 0,
          doneCount: 0,
          events: 0,
          ratePerMin: 0,
          lastEventAt: null,
          trust: null,
          needsYou: false,
          urgency: urgencyOf('idle', null),
          retroStatus: null,
        },
      ];
    }
    return { id, persisted: false };
  }

  /**
   * Scaffold a file (typically TASK.md) inside a loop's own dir (U3 Task 2, §6
   * `write_loop_file`). In Tauri this writes the real file; in dev (no Tauri) there is
   * nothing to write to, so it no-ops and reports `written: false` — the same shape
   * of graceful degradation `createLoop` uses. `overwrite` mirrors the Rust guard: a
   * missing/false value refuses to clobber an existing file.
   */
  async writeLoopFile(
    id: string,
    relPath: string,
    content: string,
    overwrite = false,
  ): Promise<{ written: boolean; error?: string }> {
    if (!(browser && hasTauri())) return { written: false };
    try {
      const { invoke } = await import('@tauri-apps/api/core');
      await invoke('write_loop_file', {
        loopId: id,
        loopsDir: DEFAULT_LOOPS_DIR,
        relPath,
        content,
        overwrite,
      });
      return { written: true };
    } catch (e) {
      return { written: false, error: e instanceof Error ? e.message : String(e) };
    }
  }

  /**
   * Probe a single shell command synchronously in a loop's own working dir (U3 Task 3,
   * §6 `probe_command`) — "discover your gate command is broken for free, not on
   * iteration 1." Works even for a loop that hasn't been created yet (the Rust side
   * falls back to the loop's future base dir). In dev (no Tauri, e.g. `vite dev` in a
   * plain browser) there is no process to spawn, so this returns a clearly-labelled
   * SIMULATED result — mirrors how `createLoop` no-ops gracefully in dev instead of
   * pretending to talk to a backend that isn't there.
   */
  async probeCommand(id: string, command: string, timeoutMs?: number): Promise<ProbeResult> {
    if (browser && hasTauri()) {
      const { invoke } = await import('@tauri-apps/api/core');
      return (await invoke('probe_command', {
        loopId: id,
        loopsDir: DEFAULT_LOOPS_DIR,
        command,
        timeoutMs,
      })) as ProbeResult;
    }
    // dev (no Tauri) — simulate a quick, honest-looking probe so the console still
    // demonstrates the feature; the tail says outright that it's not a real run.
    await new Promise((r) => setTimeout(r, 350));
    return {
      exitCode: command.trim() ? 0 : 1,
      durationMs: 350,
      tail: `(dev preview — no Tauri backend, this is a simulated result)\n$ ${command}`,
      timedOut: false,
    };
  }

  get(id: string): LoopSummary | null {
    return this.loops.find((l) => l.id === id) ?? null;
  }

  /** How many loops are waiting on a human (handoff beacon / error). */
  get needsYouCount(): number {
    return this.loops.reduce((n, l) => n + (l.needsYou ? 1 : 0), 0);
  }
}

export const cosmosStore = new CosmosStore();
export type { CosmosStore };
