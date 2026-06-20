"""Cross-run "lessons" memory store for the loop engine.

A small, pure-logic + local-file-IO memory subsystem that lets the loop carry
distilled *lessons* from one run/iteration into the stable prompt prefix of the
next. No network. Every time input is INJECTED so ranking and pruning are
deterministic.

Design grounding (research lineage):
  - **Reflexion** — verbal self-reflections persisted across attempts.
  - **ExpeL / ReasoningBank** — distil reusable *strategies*, not raw traces.
  - **ACE** — an append-only delta playbook; full rewrites cause "context
    collapse", so we PREFER append + light compaction (de-dupe / prune) over
    destructive rewrite.
  - **CoALA** — keep SEMANTIC repo-facts separate from EPISODIC lessons
    (the ``kind`` field).
  - **Generative Agents** — recall scores blend recency x importance(weight) x
    relevance.
  - **Memory-poisoning literature** — every lesson carries provenance
    (outcome/run/iter/ts) and a decay/forget path so one bad lesson cannot cause
    persistent drift; recording is green-gated.

Public surface::

    from loop.memory import (
        Lesson, MemoryStore, NullMemoryStore, FileMemoryStore,
    )

The default store is :class:`NullMemoryStore` so the feature is OFF unless a
caller explicitly configures a :class:`FileMemoryStore`.
"""

from __future__ import annotations

from loop.memory.store import (
    FileMemoryStore,
    Lesson,
    MemoryStore,
    NullMemoryStore,
)

__all__ = [
    "Lesson",
    "MemoryStore",
    "NullMemoryStore",
    "FileMemoryStore",
]
