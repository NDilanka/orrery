// Unit tests for the alert edge-detection logic (wave U4 Task 3) — the pure `detectEdge`
// function plus the AlertStore's observe/dismiss/reset behavior it drives.

import { describe, it, expect, beforeEach } from 'vitest';
import { detectEdge, alertKindFor, messageFor, alertStore } from './alerts.svelte';

describe('alertKindFor', () => {
  it('maps every offered rest-state to a kind (done/stopped now fireable)', () => {
    expect(alertKindFor('failed-dark')).toBe('failed');
    expect(alertKindFor('handoff-beacon')).toBe('handoff');
    expect(alertKindFor('quota-frost')).toBe('quota');
    expect(alertKindFor('certified-done')).toBe('done');
    expect(alertKindFor('stopped-ember')).toBe('stopped');
    expect(alertKindFor(null)).toBeNull();
  });
});

describe('messageFor', () => {
  it('formats the plain one-liners', () => {
    expect(messageFor('failed', 'bmad')).toBe('bmad failed — resume or restart');
    expect(messageFor('handoff', 'bmad')).toBe('bmad needs a decision');
    expect(messageFor('done', 'bmad')).toBe('bmad finished — all stories done');
    expect(messageFor('stopped', 'bmad')).toBe('bmad stopped — resume when ready');
  });
  it('quota includes an HH:MM eta when resumeAt is present, else a generic fallback', () => {
    expect(messageFor('quota', 'bmad', '2026-07-02T14:05:00')).toBe(
      'bmad hit its quota — resumes ~14:05',
    );
    expect(messageFor('quota', 'bmad', null)).toBe('bmad hit its quota — resumes later');
  });
});

describe('detectEdge', () => {
  it('never fires on the first observation (baseline only)', () => {
    expect(detectEdge(undefined, 'failed-dark')).toEqual({ clearKind: null, fireKind: null });
    expect(detectEdge(undefined, null)).toEqual({ clearKind: null, fireKind: null });
  });

  it('no-ops when the state is unchanged', () => {
    expect(detectEdge('failed-dark', 'failed-dark')).toEqual({ clearKind: null, fireKind: null });
    expect(detectEdge(null, null)).toEqual({ clearKind: null, fireKind: null });
  });

  it('fires on a transition INTO an alertable state', () => {
    expect(detectEdge(null, 'failed-dark')).toEqual({ clearKind: null, fireKind: 'failed' });
    // running → done is now a fireable event (certified-done maps to 'done')
    expect(detectEdge(null, 'certified-done')).toEqual({ clearKind: null, fireKind: 'done' });
  });

  it('clears when leaving an alertable state back to running (null)', () => {
    // null (running) is the only non-alertable state now that all five rest palettes map
    expect(detectEdge('failed-dark', null)).toEqual({ clearKind: 'failed', fireKind: null });
    expect(detectEdge('quota-frost', null)).toEqual({ clearKind: 'quota', fireKind: null });
  });

  it('clears the old AND fires the new across two alertable rest states', () => {
    // stopped-ember and certified-done are both alertable now, so this is a clear+fire
    expect(detectEdge('stopped-ember', 'handoff-beacon')).toEqual({
      clearKind: 'stopped',
      fireKind: 'handoff',
    });
    expect(detectEdge('handoff-beacon', 'certified-done')).toEqual({
      clearKind: 'handoff',
      fireKind: 'done',
    });
  });

  it('clears the old kind AND fires the new one on a direct alertable-to-alertable jump', () => {
    expect(detectEdge('failed-dark', 'quota-frost')).toEqual({
      clearKind: 'failed',
      fireKind: 'quota',
    });
  });
});

describe('AlertStore', () => {
  beforeEach(() => {
    alertStore.reset();
  });

  it('does not alert for a loop already broken the first time it is observed', () => {
    alertStore.observe('bmad', 'failed-dark', 'system');
    expect(alertStore.alerts).toHaveLength(0);
  });

  it('fires exactly one alert on a genuine transition, keyed by loopId+kind', () => {
    alertStore.observe('bmad', null, 'system');
    alertStore.observe('bmad', 'failed-dark', 'system');
    expect(alertStore.alerts).toHaveLength(1);
    expect(alertStore.alerts[0]).toMatchObject({
      id: 'bmad:failed',
      loopId: 'bmad',
      kind: 'failed',
      source: 'system',
    });
    // observing the SAME state again is a no-op, not a duplicate
    alertStore.observe('bmad', 'failed-dark', 'system');
    expect(alertStore.alerts).toHaveLength(1);
  });

  it('auto-clears once the state moves on (a resume/restart)', () => {
    alertStore.observe('bmad', null, 'system');
    alertStore.observe('bmad', 'failed-dark', 'system');
    expect(alertStore.alerts).toHaveLength(1);
    alertStore.observe('bmad', null, 'system'); // resumed → restState clears
    expect(alertStore.alerts).toHaveLength(0);
  });

  it('dismiss removes an alert by id', () => {
    alertStore.observe('bmad', null, 'system');
    alertStore.observe('bmad', 'handoff-beacon', 'system');
    expect(alertStore.alerts).toHaveLength(1);
    alertStore.dismiss('bmad:handoff');
    expect(alertStore.alerts).toHaveLength(0);
  });

  it('tracks multiple loops independently', () => {
    alertStore.observe('a', null, 'cosmos');
    alertStore.observe('b', null, 'cosmos');
    alertStore.observe('a', 'failed-dark', 'cosmos');
    alertStore.observe('b', 'quota-frost', 'cosmos');
    expect(alertStore.alerts.map((a) => a.id).sort()).toEqual(['a:failed', 'b:quota']);
  });

  it('a dismissed alert can re-fire on a later genuine re-transition', () => {
    alertStore.observe('bmad', null, 'system');
    alertStore.observe('bmad', 'failed-dark', 'system');
    alertStore.dismiss('bmad:failed');
    alertStore.observe('bmad', null, 'system'); // resumed
    alertStore.observe('bmad', 'failed-dark', 'system'); // failed again — a new event
    expect(alertStore.alerts).toHaveLength(1);
  });
});

describe('AlertStore — allowed (alertOn) filter', () => {
  beforeEach(() => {
    alertStore.reset();
  });

  it('suppresses the fire for a state not in `allowed`', () => {
    const allowed: NonNullable<import('../types').RestState>[] = ['failed-dark'];
    alertStore.observe('bmad', null, 'system', null, allowed);
    alertStore.observe('bmad', 'quota-frost', 'system', null, allowed); // not allowed
    expect(alertStore.alerts).toHaveLength(0);
  });

  it('still fires for a state that IS in `allowed`', () => {
    const allowed: NonNullable<import('../types').RestState>[] = ['failed-dark'];
    alertStore.observe('bmad', null, 'system', null, allowed);
    alertStore.observe('bmad', 'failed-dark', 'system', null, allowed);
    expect(alertStore.alerts.map((a) => a.id)).toEqual(['bmad:failed']);
  });

  it('clear/baseline bookkeeping runs even for filtered-out states (no stale alert, edge still detects)', () => {
    const allowed: NonNullable<import('../types').RestState>[] = ['failed-dark'];
    // start running, briefly pass through a filtered-out quota state, then fail — the
    // baseline must have advanced so the later allowed transition still edge-detects.
    alertStore.observe('bmad', null, 'system', null, allowed);
    alertStore.observe('bmad', 'quota-frost', 'system', null, allowed); // filtered → no alert
    expect(alertStore.alerts).toHaveLength(0);
    alertStore.observe('bmad', 'failed-dark', 'system', null, allowed); // allowed → fires
    expect(alertStore.alerts.map((a) => a.id)).toEqual(['bmad:failed']);
  });

  it('an omitted `allowed` fires everything (back-compat)', () => {
    alertStore.observe('bmad', null, 'system');
    alertStore.observe('bmad', 'certified-done', 'system');
    expect(alertStore.alerts.map((a) => a.id)).toEqual(['bmad:done']);
  });
});

describe('AlertStore — quota-resume signal', () => {
  beforeEach(() => {
    alertStore.reset();
  });

  it('records lastQuotaResume when a loop leaves quota-frost back to running', () => {
    expect(alertStore.lastQuotaResume).toBeNull();
    alertStore.observe('bmad', null, 'system');
    alertStore.observe('bmad', 'quota-frost', 'system');
    expect(alertStore.lastQuotaResume).toBeNull(); // entering quota is not a resume
    alertStore.observe('bmad', null, 'system'); // resumed
    expect(alertStore.lastQuotaResume).toMatchObject({ loopId: 'bmad', seq: 1 });
  });

  it('does NOT record a resume for quota → a different rest state (not back to running)', () => {
    alertStore.observe('bmad', null, 'system');
    alertStore.observe('bmad', 'quota-frost', 'system');
    alertStore.observe('bmad', 'certified-done', 'system'); // finished, not resumed
    expect(alertStore.lastQuotaResume).toBeNull();
  });

  it('increments seq on each distinct resume so a watcher can edge-detect', () => {
    alertStore.observe('bmad', null, 'system');
    alertStore.observe('bmad', 'quota-frost', 'system');
    alertStore.observe('bmad', null, 'system'); // resume 1
    alertStore.observe('bmad', 'quota-frost', 'system');
    alertStore.observe('bmad', null, 'system'); // resume 2
    expect(alertStore.lastQuotaResume?.seq).toBe(2);
  });
});
