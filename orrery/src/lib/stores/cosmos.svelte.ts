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
import { base } from '$app/paths';
import { browser } from '$app/environment';

// Path to the loops/ registry (mirrors tauri-transport's default). Tauri's
// `list_loops` reads loop.json from here; dev replay ignores it.
export const DEFAULT_LOOPS_DIR = 'D:/dev/loop/orrery/loops';

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
  return {
    id: choice.id,
    name: choice.name,
    theme: choice.theme,
    adapter: choice.adapter,
    status: state.run.status,
    restState: state.run.restState,
    cumUsd: state.run.cumUsd,
    ceilingUsd: ceil,
    horizonFrac,
    currentItem: state.currentItem,
    itemCount: items.length,
    doneCount: items.filter((it) => it.status === 'done').length,
    events: state.events,
    ratePerMin: state.cost.ratePerMin,
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

  // the placeholder "Tuning Console (A5)" notice — opening the ignite affordance
  // flips this on; the shell shows a small toast/overlay. A5 replaces the stub.
  ignitePlaceholder = $state(false);

  /** Load (or reload) every registered loop's Tier-1 summary. */
  async load(): Promise<void> {
    if (!browser) return;
    this.loading = true;
    this.error = null;
    try {
      if (hasTauri()) {
        this.source = 'tauri';
        await this.loadTauri();
      } else {
        this.source = 'dev';
        await this.loadDev();
      }
    } catch (e) {
      this.error = e instanceof Error ? e.message : String(e);
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
    }>;
    const summaries: LoopSummary[] = [];
    for (const def of defs) {
      const adapter = def.adapter === 'bmad' ? 'bmad' : 'generic';
      try {
        // load_run(stateDir, adapter) → an already-reduced RunState (Rust side)
        const state = (await invoke('load_run', {
          stateDir: def.stateDir,
          adapter: def.adapter,
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
        });
      }
    }
    this.loops = summaries;
  }

  /** The ✦ ignite-new-loop affordance (A5 stub). */
  igniteNew(): void {
    this.ignitePlaceholder = true;
  }
  dismissIgnite(): void {
    this.ignitePlaceholder = false;
  }

  get(id: string): LoopSummary | null {
    return this.loops.find((l) => l.id === id) ?? null;
  }
}

export const cosmosStore = new CosmosStore();
export type { CosmosStore };
