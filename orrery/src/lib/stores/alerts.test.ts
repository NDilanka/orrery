// Unit tests for the alert edge-detection logic (wave U4 Task 3) — the pure `detectEdge`
// function plus the AlertStore's observe/dismiss/reset behavior it drives.

import { describe, it, expect, beforeEach } from 'vitest';
import { detectEdge, alertKindFor, messageFor, alertStore } from './alerts.svelte';

describe('alertKindFor', () => {
  it('maps only the three alertable rest-states', () => {
    expect(alertKindFor('failed-dark')).toBe('failed');
    expect(alertKindFor('handoff-beacon')).toBe('handoff');
    expect(alertKindFor('quota-frost')).toBe('quota');
    expect(alertKindFor('certified-done')).toBeNull();
    expect(alertKindFor('stopped-ember')).toBeNull();
    expect(alertKindFor(null)).toBeNull();
  });
});

describe('messageFor', () => {
  it('formats the plain one-liners', () => {
    expect(messageFor('failed', 'bmad')).toBe('bmad failed — resume or restart');
    expect(messageFor('handoff', 'bmad')).toBe('bmad needs a decision');
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
    expect(detectEdge('stopped-ember', 'handoff-beacon')).toEqual({
      clearKind: null,
      fireKind: 'handoff',
    });
  });

  it('clears when leaving an alertable state for a non-alertable one', () => {
    expect(detectEdge('failed-dark', null)).toEqual({ clearKind: 'failed', fireKind: null });
    expect(detectEdge('handoff-beacon', 'certified-done')).toEqual({
      clearKind: 'handoff',
      fireKind: null,
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
