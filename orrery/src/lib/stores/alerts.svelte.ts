// Alert store (wave U4 Task 3) — dismissible top-of-viewport banners for UNATTENDED-run
// events: a loop's restState transitioning INTO 'failed-dark' / 'handoff-beacon' /
// 'quota-frost'. Edge-detected HERE, store-level, as a plain function + a thin $state
// class — mirrors VerdictPanel's `$effect` + "lastX" comparison pattern (panels/
// VerdictPanel.svelte's `lastRefuted`), but as a reusable, unit-testable pure function
// instead of inline component state that only a mounted component could exercise.
//
// Deliberately NOT fed from a replay-backed session (see the transportKind guard where
// this is wired up, routes/+page.svelte) — replay is a fixture a human is actively
// watching/scrubbing right now (TransportBar's slider can move either direction any time,
// even outside Rewind mode), so "it happened while I wasn't looking" doesn't apply, and
// feeding it here would let scrubbing fire/clear the same alert repeatedly as the
// timeline crosses a boundary back and forth.

import type { RestState } from '../types';

export type AlertKind = 'failed' | 'handoff' | 'quota';

export interface RunAlert {
  /** `${loopId}:${kind}` — stable per loop+kind: re-observing the same state is a no-op,
   * and a genuine re-transition (clear, then a later fire) replaces it cleanly. */
  id: string;
  loopId: string;
  kind: AlertKind;
  message: string;
  createdAt: number;
  /** Which altitude first observed the transition. Cosmos-sourced alerts carry a
   * jump-to-loop action; System-sourced ones fired while you were already there. */
  source: 'system' | 'cosmos';
}

const KIND_BY_REST: Partial<Record<NonNullable<RestState>, AlertKind>> = {
  'failed-dark': 'failed',
  'handoff-beacon': 'handoff',
  'quota-frost': 'quota',
};

export function alertKindFor(restState: RestState): AlertKind | null {
  return restState ? (KIND_BY_REST[restState] ?? null) : null;
}

function hhmm(iso: string | null | undefined): string | null {
  if (!iso) return null;
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return null;
  return `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`;
}

/** Plain one-liners (task copy): "BMAD failed — resume or restart" etc., with the loop id
 * standing in for "BMAD" so multiple concurrent alerts stay distinguishable. */
export function messageFor(kind: AlertKind, loopId: string, resumeAt?: string | null): string {
  switch (kind) {
    case 'failed':
      return `${loopId} failed — resume or restart`;
    case 'handoff':
      return `${loopId} needs a decision`;
    case 'quota': {
      const eta = hhmm(resumeAt);
      return eta
        ? `${loopId} hit its quota — resumes ~${eta}`
        : `${loopId} hit its quota — resumes later`;
    }
  }
}

export interface EdgeResult {
  clearKind: AlertKind | null;
  fireKind: AlertKind | null;
}

/**
 * Pure edge detector. `prev === undefined` means this loop has never been observed
 * before — establish the baseline WITHOUT firing: a loop that's already broken the first
 * time you look isn't an "event", it's just current state (the Cosmos roster / rest-state
 * glyphs / Planetarium threshold already surface that passively).
 *
 * A direct transition between two alertable states (e.g. failed-dark → quota-frost, were
 * that ever reachable) both clears the old alert and fires the new one in one call.
 */
export function detectEdge(prev: RestState | undefined, next: RestState): EdgeResult {
  if (prev === undefined || prev === next) return { clearKind: null, fireKind: null };
  const prevKind = alertKindFor(prev);
  const nextKind = alertKindFor(next);
  return {
    clearKind: prevKind && prevKind !== nextKind ? prevKind : null,
    fireKind: nextKind,
  };
}

class AlertStore {
  alerts = $state<RunAlert[]>([]);
  private lastRestState = new Map<string, RestState>();

  /** Feed one loop's freshly-observed restState. Call this only from a live (non-replay)
   * source — see the module comment above. */
  observe(loopId: string, restState: RestState, source: 'system' | 'cosmos', resumeAt?: string | null): void {
    const prev = this.lastRestState.has(loopId) ? this.lastRestState.get(loopId) : undefined;
    this.lastRestState.set(loopId, restState);
    const { clearKind, fireKind } = detectEdge(prev, restState);
    if (clearKind) {
      const clearId = `${loopId}:${clearKind}`;
      this.alerts = this.alerts.filter((a) => a.id !== clearId);
    }
    if (fireKind) {
      const id = `${loopId}:${fireKind}`;
      const alert: RunAlert = {
        id,
        loopId,
        kind: fireKind,
        message: messageFor(fireKind, loopId, resumeAt),
        createdAt: Date.now(),
        source,
      };
      this.alerts = [...this.alerts.filter((a) => a.id !== id), alert];
    }
  }

  dismiss(id: string): void {
    this.alerts = this.alerts.filter((a) => a.id !== id);
  }

  /** Forget a loop (e.g. it left the roster) — the next observation re-baselines instead
   * of comparing against stale history. */
  forget(loopId: string): void {
    this.lastRestState.delete(loopId);
  }

  reset(): void {
    this.alerts = [];
    this.lastRestState.clear();
  }
}

export const alertStore = new AlertStore();
export type { AlertStore };
