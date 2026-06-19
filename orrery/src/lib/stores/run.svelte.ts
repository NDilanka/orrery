// Active-run store (Svelte 5 runes). Holds the reduced RunState as $state and
// exposes $derived orbital geometry the Observatory renders:
//   - per-item orbit angle  (group ringIndex + index)
//   - star radius from cumUsd (r0 + k·log1p)
//   - cost-horizon fraction + quota countdown
// Events mutate the store; the renderer's rAF loop interpolates toward targets.

import type { RunState, WorkItem } from '../types';
import { initialState } from '../reduce';

// ─── geometry constants (shared with the renderer) ──────────────────────────
export const STAR_R0 = 14; // px-equivalent base star radius
export const STAR_K = 9; // log scale factor: r = r0 + k·log1p(cumUsd)
export const RING_BASE = 70; // first ring radius
export const RING_GAP = 52; // spacing between rings
export const DEFAULT_CEILING = 80; // fallback cost ceiling (USD) when unset

export interface OrbitGeom {
  key: string;
  group: string | null;
  ringIndex: number;
  ringRadius: number;
  angle: number; // radians
  status: WorkItem['status'];
  certified: boolean;
  claimedGreen: boolean; // gate-green OR review, but NOT yet certified (A2: anxious)
  hasGhost: boolean;
  strikes: number;
  strikeBudget: number;
  merged: boolean;
}

export interface RingGeom {
  id: string;
  ringIndex: number;
  radius: number;
  status: 'backlog' | 'in-progress' | 'done';
  count: number;
}

function angleFor(index: number, count: number, ringIndex: number): number {
  // distribute items around their ring; offset each ring so planets don't line up
  const n = Math.max(count, 1);
  const base = (index / n) * Math.PI * 2;
  const ringOffset = ringIndex * 0.6;
  return base + ringOffset;
}

class RunStore {
  state = $state<RunState>(initialState());

  // ── derived geometry ──────────────────────────────────────────────────────
  starRadius = $derived(STAR_R0 + STAR_K * Math.log1p(Math.max(0, this.state.run.cumUsd)));

  rings = $derived.by<RingGeom[]>(() => {
    const groups = Object.values(this.state.groups).sort((a, b) => a.ringIndex - b.ringIndex);
    return groups.map((g) => ({
      id: g.id,
      ringIndex: g.ringIndex,
      radius: RING_BASE + g.ringIndex * RING_GAP,
      status: g.status,
      count: Object.values(this.state.items).filter((i) => i.group === g.id).length,
    }));
  });

  orbits = $derived.by<OrbitGeom[]>(() => {
    const items = Object.values(this.state.items);
    // group items by their ring to count siblings for angle spacing
    const byGroup = new Map<string | null, WorkItem[]>();
    for (const it of items) {
      const arr = byGroup.get(it.group) ?? [];
      arr.push(it);
      byGroup.set(it.group, arr);
    }
    const out: OrbitGeom[] = [];
    for (const [group, arr] of byGroup) {
      arr.sort((a, b) => a.index - b.index);
      const ringIndex = group != null ? (this.state.groups[group]?.ringIndex ?? 0) : 0;
      const ringRadius =
        group != null ? RING_BASE + ringIndex * RING_GAP : RING_BASE; // grouped → its ring; ungrouped → base ring
      arr.forEach((it, i) => {
        out.push({
          key: it.key,
          group,
          ringIndex,
          ringRadius,
          angle: angleFor(i, arr.length, ringIndex),
          status: it.status,
          certified: it.certified,
          claimedGreen: !it.certified && (!!it.gate?.green || it.status === 'review'),
          hasGhost: !!it.ghost,
          strikes: it.strikes,
          strikeBudget: it.strikeBudget,
          merged: !!it.pr?.merged,
        });
      });
    }
    return out;
  });

  // cost-horizon fraction (0..1+); the ring closes on the star as it nears 1
  ceilingUsd = $derived(this.state.cost.ceilingUsd ?? DEFAULT_CEILING);
  horizonFrac = $derived(
    this.ceilingUsd > 0 ? Math.min(2, this.state.run.cumUsd / this.ceilingUsd) : 0,
  );
  horizonVisible = $derived(this.horizonFrac >= 0.5);

  // quota countdown (seconds remaining toward resumeAt; falls back to waitSec)
  quotaSecondsLeft = $derived.by<number | null>(() => {
    const q = this.state.quota;
    if (!q.active) return null;
    if (q.resumeAt) {
      const left = Math.round((new Date(q.resumeAt).getTime() - Date.now()) / 1000);
      return left > 0 ? left : 0;
    }
    return q.waitSec || 0;
  });

  // current work item resolved to the full WorkItem (or null)
  current = $derived.by<WorkItem | null>(() => {
    const k = this.state.currentItem;
    return k ? this.state.items[k] ?? null : null;
  });

  // ── living-motion signals (consumed by the Observatory rAF loop) ──────────
  // Active model's spectral class (heat). Defaults to sonnet/brass when a loop
  // never emits a `model` event so the mechanism is never colorless.
  model = $derived(this.state.phase.model ?? 'sonnet');

  // Normalised burn (0..1): how hard the star should bloom. Derived from the
  // cost rate, soft-clamped so a tiny demo still breathes and a hot run blooms.
  burn = $derived.by<number>(() => {
    const rate = this.state.cost.ratePerMin;
    if (rate <= 0) return this.state.run.status === 'running' ? 0.18 : 0;
    // ~$8/min reads as fully hot; log-soft so it is legible across scales
    return Math.min(1, Math.log1p(rate) / Math.log1p(8));
  });

  // Cache-hit fraction (0..1): the share of particles that recirculate as
  // cache-teal recycled fuel. `warm` gives it a small floor.
  cacheFrac = $derived.by<number>(() => {
    const c = this.state.cache;
    const r = Math.max(0, Math.min(1, c.hitRatio));
    return c.warm ? Math.max(0.25, r) : r;
  });

  // Which night is falling: 'dusk' (5h nap) vs 'polar' (weekly hibernation).
  nightType = $derived.by<'dusk' | 'polar' | null>(() => {
    const q = this.state.quota;
    if (!q.active && this.state.run.restState !== 'quota-frost') return null;
    return q.resetType === 'weekly' ? 'polar' : 'dusk';
  });

  // ── A3: the auditor & rest-states ─────────────────────────────────────────
  // The Lighthouse activates only when a planet is claimed-green (gate green or
  // status 'review') but not yet certified — there is something to audit.
  auditTargetKey = $derived.by<string | null>(() => {
    // prefer the current item if it is claimed-green-not-certified…
    const cur = this.current;
    if (cur && !cur.certified && (cur.gate?.green || cur.status === 'review')) return cur.key;
    // …else any claimed-green-not-certified item (latest event wins)
    let best: WorkItem | null = null;
    for (const it of Object.values(this.state.items)) {
      if (!it.certified && (it.gate?.green || it.status === 'review')) {
        if (!best || it.lastEventTs >= best.lastEventTs) best = it;
      }
    }
    return best?.key ?? null;
  });

  // The latest verdict and which item it refers to (for the VerdictPanel auto-pop
  // on a refute, and the Lighthouse beam target).
  latestVerdict = $derived.by<{ key: string; verdict: RunState['verdicts'][string] } | null>(() => {
    const cur = this.state.currentItem;
    if (cur && this.state.verdicts[cur]) return { key: cur, verdict: this.state.verdicts[cur] };
    const keys = Object.keys(this.state.verdicts);
    if (!keys.length) return null;
    const k = keys[keys.length - 1];
    return { key: k, verdict: this.state.verdicts[k] };
  });

  // The active stop mode pending (brake target), surfaced for the renderer.
  stopPending = $derived(this.state.run.stopPending);
  restState = $derived(this.state.run.restState);

  // ── transient UI selection (which planet's VerdictPanel is open) ──────────
  selectedItem = $state<string | null>(null);
  selectItem(key: string | null) {
    this.selectedItem = key;
  }

  // ── mutations (called by the transport) ──────────────────────────────────
  set(next: RunState) {
    this.state = next;
  }

  reset() {
    this.state = initialState();
    this.selectedItem = null;
  }
}

export const runStore = new RunStore();
export type { RunStore };
