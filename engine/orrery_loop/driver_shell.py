"""The shared driver shell — the ONE lifecycle every loop driver runs inside.

``orrery_loop.core.run_loop``, ``orrery_loop.bmad.driver.run``, and ``orrery_loop.qa.discover.run`` each hand-wired
the same four things: acquire the single-flight lockfile (refusing with exit code ``2`` when
another instance is live), write ``checkpoint.json`` in the PROTOCOL §7 shape at every safe
boundary, interpret the cooperative ``STOP`` flag against the boundary the driver is currently
at (:func:`orrery_loop.checkpoint.get_stop_mode`), and release the lock on every exit path. This module
hoists exactly that — no more — into :func:`run_driver` plus two small helpers
(:func:`read_stop_request`, :func:`write_checkpoint_now`) so a driver's entry point collapses to
"parse config -> ``run_driver(state_dir, ...)``" while its actual orchestration (the part that
makes it a *generic loop* vs *BMAD* vs *QA* driver) stays exactly where it was.

Deliberately NOT a framework: there is no base class, no required hook order beyond what's
described below, and every driver's orchestration body is still one ordinary function it fully
owns. Only the lock/checkpoint/STOP plumbing that was byte-identical across all three drivers
moved here.

## Contract: what a conforming driver must do

A driver built on :func:`run_driver` (``run_driver(state_dir, guard_label=..., body=...)``)
gets the following for free, and in return must follow these rules:

1. **State files live under ``state_dir``** (PROTOCOL §1): ``log.jsonl`` (append-only —
   :func:`orrery_loop.logio.append_event`), ``checkpoint.json`` (:func:`write_checkpoint_now`),
   ``STOP`` (cooperative-stop flag), and ``activity.json`` (the liveness heartbeat —
   :class:`orrery_loop.heartbeat.Heartbeat`, wrapped around every blocking agent call). A driver may
   add its own additional files (e.g. ``findings/epic-N.json``) but must not repurpose these
   names.
2. **The lock is exclusive and automatic.** ``run_driver`` acquires
   ``<state_dir>/lock`` (:mod:`orrery_loop.lockfile` — the ONE lock name shared by every driver, so a
   generic loop / BMAD run / QA run racing the same state dir correctly serialize) BEFORE
   calling ``body``, and releases it in a ``finally`` no matter how ``body`` returns or raises.
   A refused lock (another live instance holds it) short-circuits ``body`` entirely and returns
   ``2`` — a driver never sees this case; it only ever runs with the lock held.
3. **STOP is interpreted, never enforced by force.** A driver polls the flag itself, at ITS OWN
   safe boundaries (between iterations, between stories, after a phase — whatever is safe for
   that driver's unit of work) via :func:`read_stop_request`, honors it by writing a checkpoint
   + emitting ``cooperative-stop`` + clearing the flag, and returns/continues on its own terms.
   Nothing here kills a driver mid-step.
4. **Checkpoints follow ONE shape.** Every checkpoint write goes through
   :func:`write_checkpoint_now` (which wraps :func:`orrery_loop.events.new_checkpoint` +
   :func:`orrery_loop.logio.write_checkpoint`) so ``updatedAt``/``resume``/``cumUsd`` rounding are
   consistent across drivers — a driver never hand-rolls the checkpoint dict.
5. **``body(state: Path) -> int`` returns a process exit code**: ``0`` success/clean-stop,
   ``1`` handoff/halt, ``2`` is RESERVED for the lock-refusal case (a driver must not return it
   for any other reason, so a caller can always read exit ``2`` as "a live instance exists").

Emitting events, running the actual agent, and everything else driver-specific (phase
orchestration, gate stages, quota survival) is entirely up to ``body`` — this module has no
opinion on any of it.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from orrery_loop.checkpoint import get_stop_mode
from orrery_loop.events import new_checkpoint
from orrery_loop.lockfile import LOCK_NAME, acquire_lock, release_lock
from orrery_loop.logio import read_stop_flag, write_checkpoint

__all__ = ["run_driver", "read_stop_request", "write_checkpoint_now"]


def run_driver(
    state_dir: Any,
    *,
    guard_label: str,
    body: Callable[[Path], int],
    lock_name: str = LOCK_NAME,
) -> int:
    """Own the lock lifecycle around ``body``; return ``body``'s exit code, or ``2`` on refusal.

    Creates ``state_dir`` if it doesn't exist, acquires ``<state_dir>/<lock_name>``
    (:mod:`orrery_loop.lockfile`), calls ``body(state)`` with the lock held, and releases the lock in a
    ``finally`` — so a driver's own orchestration never has to touch lock plumbing. ``guard_label``
    names the driver in the refusal message (``"[GUARD] another <guard_label> is already running
    against state dir '<state>'."``) printed when the lock is already live.
    """
    state = Path(state_dir)
    state.mkdir(parents=True, exist_ok=True)
    lock_path = state / lock_name
    if not acquire_lock(lock_path):
        print(f"[GUARD] another {guard_label} is already running against state dir '{state}'.")
        return 2
    try:
        return body(state)
    finally:
        release_lock(lock_path)


def read_stop_request(stop_path: Any, scope: str) -> dict[str, Any]:
    """Read + normalize the cooperative ``STOP`` flag at a given boundary ``scope``.

    A thin pairing of :func:`orrery_loop.logio.read_stop_flag` (the file read) with
    :func:`orrery_loop.checkpoint.get_stop_mode` (the pure normalize-and-decide) — every driver did this
    exact pairing inline; this is the one place it lives now. Returns
    ``{requested, honor, mode}`` (see :func:`orrery_loop.checkpoint.get_stop_mode`).
    """
    return get_stop_mode(read_stop_flag(stop_path), scope=scope)


def write_checkpoint_now(
    checkpoint_path: Any,
    *,
    stage: str,
    story: str | None,
    branch: str,
    merge_base: str,
    cum_usd: float,
    resume: str,
    updated_at=None,
) -> None:
    """Build + write a PROTOCOL §7 ``checkpoint.json`` (the ONE shape every driver writes).

    Wraps :func:`orrery_loop.events.new_checkpoint` (the pure builder — stamps ``updatedAt``, rounds
    ``cumUsd``) and :func:`orrery_loop.logio.write_checkpoint` (the file write) so no driver hand-rolls
    the checkpoint dict directly.
    """
    cp = new_checkpoint(
        stage=stage,
        story=story,
        branch=branch,
        merge_base=merge_base,
        cum_usd=cum_usd,
        resume=resume,
        updated_at=updated_at,
    )
    write_checkpoint(checkpoint_path, cp)
