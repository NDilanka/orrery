// Unit tests for SessionStore.mountLoop's re-entrancy guard (mountEpoch) — a fast loop-switch
// starts a second mountLoop before the first's awaits resolve; the stale call must back out
// without clobbering the newer mount's transport/transportKind, and must stop any transport IT
// created. Also covers the stale-badge fix: an unresolvable loop id must clear transport/
// transportKind rather than leaving them pointed at the transport we just stopped.
//
// Transports are pure mocks (no real Tauri/replay/ws wiring) so this stays fast + deterministic;
// `createTransport` and `cosmosStore.loadLoopDef` are the only two seams mocked.

import { describe, it, expect, vi, beforeEach } from 'vitest';

const { createTransportMock, loadLoopDefMock } = vi.hoisted(() => ({
  createTransportMock: vi.fn(),
  loadLoopDefMock: vi.fn(async () => null as unknown),
}));

vi.mock('$app/environment', () => ({ browser: true }));
vi.mock('$app/paths', () => ({ base: '' }));

vi.mock('../transport', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../transport')>();
  return { ...actual, createTransport: createTransportMock };
});

vi.mock('./cosmos.svelte', () => ({
  cosmosStore: { loadLoopDef: loadLoopDefMock },
}));

import { sessionStore } from './session.svelte';
import type { Transport } from '../transport';

// A controllable mock Transport. `pending` = true holds `start()` unresolved until the test
// calls `resolveStart()`, so the test can land right in the middle of mountLoop's second await.
function makeTransport(kind: Transport['kind'], pending: boolean) {
  let resolveStart = () => {};
  const startPromise = pending
    ? new Promise<void>((res) => {
        resolveStart = res;
      })
    : Promise.resolve();
  const transport: Transport = {
    kind,
    start: vi.fn(() => startPromise),
    stop: vi.fn(),
    control: vi.fn(async () => {}),
  };
  return { transport, resolveStart: () => resolveStart() };
}

beforeEach(() => {
  createTransportMock.mockReset();
  loadLoopDefMock.mockReset();
  loadLoopDefMock.mockResolvedValue(null);
  sessionStore.unmountLoop();
});

describe('SessionStore.mountLoop re-entrancy guard', () => {
  it('a mount superseded while transport.start() is pending stops its own transport and leaves the newer mount alone', async () => {
    // 'demo' is a static LOOPS entry, so choice resolution is synchronous — mountLoop runs
    // straight through to `await transport.start()` before returning control to us.
    const { transport: t1, resolveStart: resolveStart1 } = makeTransport('tauri', true);
    createTransportMock.mockReturnValueOnce(t1);
    const p1 = sessionStore.mountLoop('demo');

    // 'bmad' is also a static LOOPS entry; its own start() resolves immediately.
    const { transport: t2 } = makeTransport('replay', false);
    createTransportMock.mockReturnValueOnce(t2);
    const p2 = sessionStore.mountLoop('bmad');
    await p2;

    expect(sessionStore.transportKind).toBe('replay');
    expect(t2.start).toHaveBeenCalledTimes(1);

    // now let the superseded first mount's start() resolve
    resolveStart1();
    await p1;

    expect(t1.stop).toHaveBeenCalled();
    // the newer mount must still own the store — p1 touches nothing on the far side
    expect(sessionStore.transportKind).toBe('replay');
  });

  it('a mount superseded during loopChoiceFromDef never creates a transport, even if the id turns out unresolvable', async () => {
    let resolveDef: (v: unknown) => void = () => {};
    loadLoopDefMock.mockImplementationOnce(
      () =>
        new Promise((res) => {
          resolveDef = res;
        }),
    );
    // 'custom-x' is not a static LOOPS entry → mountLoop awaits loopChoiceFromDef immediately.
    const p1 = sessionStore.mountLoop('custom-x');

    const { transport: t2 } = makeTransport('replay', false);
    createTransportMock.mockReturnValueOnce(t2);
    const p2 = sessionStore.mountLoop('demo');
    await p2;
    expect(sessionStore.transportKind).toBe('replay');

    resolveDef(null); // custom-x resolves to "unknown loop" — but p1 is already superseded
    await p1;

    expect(sessionStore.transportKind).toBe('replay');
    expect(createTransportMock).toHaveBeenCalledTimes(1); // only p2 ever created a transport
  });

  it('mounting an unresolvable loop clears transport/transportKind instead of leaving them stale', async () => {
    const { transport: t1 } = makeTransport('tauri', false);
    createTransportMock.mockReturnValueOnce(t1);
    await sessionStore.mountLoop('demo');
    expect(sessionStore.transportKind).toBe('tauri');

    loadLoopDefMock.mockResolvedValueOnce(null);
    await sessionStore.mountLoop('does-not-exist');

    expect(sessionStore.transportKind).toBeNull();
    expect(t1.stop).toHaveBeenCalled();
  });

  it('unmountLoop supersedes an in-flight mountLoop', async () => {
    const { transport: t1, resolveStart: resolveStart1 } = makeTransport('tauri', true);
    createTransportMock.mockReturnValueOnce(t1);
    const p1 = sessionStore.mountLoop('demo');

    sessionStore.unmountLoop();
    expect(sessionStore.transportKind).toBeNull();

    resolveStart1();
    await p1;

    expect(sessionStore.transportKind).toBeNull(); // p1 must not resurrect it
    expect(t1.stop).toHaveBeenCalled();
  });
});
