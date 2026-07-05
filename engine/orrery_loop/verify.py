"""Held-out test-split gate hardening + a SAFE, advisory mutation-strength audit.

Two independent anti-reward-hacking defenses, both PURE (no network, no real test
runner, runners injected by the caller). They layer ON TOP of :mod:`orrery_loop.gate`
(``run_gate`` produces the ``gate_result`` consumed here) and :mod:`orrery_loop.hashlock`
(the held-out lock globs feed the tamper detector).

Why this exists
---------------
The #1 way a coding agent "reward hacks" a test gate is to special-case the exact
inputs it can read — overfitting to the visible suite rather than learning the
intended behavior (Krakovna et al., *Specification Gaming*; METR's 2025
reward-hacking findings). The defense is a HELD-OUT (hidden) suite the agent never
sees: it runs, it counts toward green, but its output is filtered out of every piece
of feedback shown to the agent, and its files are hash-locked so they cannot be
edited to neuter them.

Held-out policy (the contract the integration layer enforces)
-------------------------------------------------------------
* A gate stage opts in with ``stage['held_out'] = True``.
* **Overall green = visible green AND held-out green.** :func:`held_out_green`
  answers the second conjunct; the caller ANDs it with the visible result.
* The agent ONLY ever sees :func:`visible_feedback_raw` — the gate ``raw`` with the
  held-out stages' sections stripped, so the hidden suite's output never leaks.
* Held-out test files SHOULD be added to the hash-lock glob so the agent cannot edit
  them; :func:`held_out_lock_globs` surfaces any ``lock_globs`` declared on held-out
  stages for the caller to merge into its lock set.

Mutation audit (advisory)
-------------------------
Coverage says a line *ran*; it does not say a test would *notice* if that line were
wrong. Mutation score — the fraction of injected faults a suite kills — tracks real
fault-detection ability far better (Just et al., 2014). :func:`mutation_audit` is a
small, SAFE, text-only probe: it rewrites the SOURCE STRING with simple operator /
literal swaps and asks an INJECTED ``run_tests`` whether the suite still passes. A
surviving mutant is a weak spot. It is advisory only — it never gates a run and never
touches the filesystem.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any

# ---------------------------------------------------------------------------
# Held-out / hidden test split
# ---------------------------------------------------------------------------

# The per-stage section header ``run_gate`` writes into ``raw`` (gate.py:157):
#   ### stage '<name>' (exit=<code>)
# We anchor on it (multiline) to slice ``raw`` back into per-stage sections so the
# held-out ones can be dropped before feedback reaches the agent.
_SECTION_RE = re.compile(r"(?m)^### stage '(?P<name>.*?)' \(exit=")


def partition_stages(
    stages: list[dict[str, Any]] | None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Split gate ``stages`` into ``(visible, held_out)`` by the ``held_out`` flag.

    A stage is held-out when ``stage.get("held_out")`` is truthy. Order within each
    bucket is preserved. ``None`` / empty -> two empty lists.
    """
    visible: list[dict[str, Any]] = []
    held_out: list[dict[str, Any]] = []
    for s in stages or []:
        (held_out if s.get("held_out") else visible).append(s)
    return visible, held_out


def held_out_names(stages: list[dict[str, Any]] | None) -> list[str]:
    """Names of the held-out stages (convenience for the other helpers)."""
    _, held = partition_stages(stages)
    return [s.get("name") for s in held]


def held_out_green(gate_result: dict[str, Any], held_out_names: list[str]) -> bool:
    """True only if EVERY held-out stage passed (``ok`` is True) in ``gate_result``.

    A held-out stage that is named but missing from the result is treated as NOT
    passed (fail-closed) — a held-out suite that did not run cannot count as green.
    An empty ``held_out_names`` is vacuously green (no hidden suite to satisfy).
    """
    if not held_out_names:
        return True
    by_name = {s.get("name"): s for s in gate_result.get("stages") or []}
    for name in held_out_names:
        stage = by_name.get(name)
        if stage is None or not stage.get("ok"):
            return False
    return True


def _iter_sections(raw: str) -> list[tuple[str, int, int]]:
    """Slice ``raw`` into ``(stage_name, start, end)`` spans by the section header.

    Each span runs from the start of a ``### stage '<name>' (exit=`` header to the
    start of the next header (or end of string). Any preamble before the first header
    is ignored (``run_gate`` never emits one, but we tolerate it).
    """
    matches = list(_SECTION_RE.finditer(raw))
    spans: list[tuple[str, int, int]] = []
    for i, m in enumerate(matches):
        start = m.start()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(raw)
        spans.append((m.group("name"), start, end))
    return spans


def visible_feedback_raw(gate_result: dict[str, Any], held_out_names: list[str]) -> str:
    """The gate ``raw`` with the held-out stages' sections REMOVED.

    Feedback shown to the agent must never leak the hidden suite's output (test
    names, assertion messages, diffs) — otherwise the agent could special-case to it.
    We split ``raw`` on the ``### stage '<name>' (exit=..)`` headers and drop every
    section whose name is in ``held_out_names``, then re-join the survivors with the
    same ``"\\n"`` separator ``run_gate`` used. A name not present in ``raw`` is a
    no-op. Empty ``held_out_names`` returns ``raw`` unchanged.
    """
    raw = gate_result.get("raw") or ""
    if not held_out_names:
        return raw
    hidden = set(held_out_names)
    kept: list[str] = []
    for name, start, end in _iter_sections(raw):
        if name in hidden:
            continue
        # ``run_gate`` joins sections with "\n"; each section text already ends with a
        # trailing newline from the section that followed it, so strip the boundary
        # newline we'd otherwise duplicate and re-join consistently.
        kept.append(raw[start:end].rstrip("\n"))
    return "\n".join(kept)


def held_out_lock_globs(stages: list[dict[str, Any]] | None) -> list[str]:
    """All ``lock_globs`` declared on held-out stages, de-duplicated, order-stable.

    The integration layer merges these into its hash-lock glob set so the hidden test
    files cannot be edited by the agent (see :mod:`orrery_loop.hashlock`). A stage may give
    ``lock_globs`` as a single string or a list; both are accepted.
    """
    _, held = partition_stages(stages)
    out: list[str] = []
    seen: set[str] = set()
    for s in held:
        globs = s.get("lock_globs")
        if globs is None:
            continue
        if isinstance(globs, str):
            globs = [globs]
        for g in globs:
            if g not in seen:
                seen.add(g)
                out.append(g)
    return out


# ---------------------------------------------------------------------------
# Mutation-strength audit (SAFE, advisory, text-only)
# ---------------------------------------------------------------------------

# Token-level swaps. ORDER MATTERS: longer / compound operators are tried before
# their substrings (``==`` before ``=``, ``<=`` before ``<``) and word operators are
# matched on word boundaries so we never corrupt identifiers. Each entry is a
# (compiled-pattern, replacement) pair applied to ONE site at a time to keep mutants
# minimal and deterministic.
_MUTATORS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"=="), "!="),
    (re.compile(r"!="), "=="),
    (re.compile(r"<="), ">"),
    (re.compile(r">="), "<"),
    # Bare ``<`` / ``>`` not part of <=, >=, <<, >> (negative lookarounds keep us off
    # shift operators and the compound forms handled above).
    (re.compile(r"(?<![<>=!])<(?![=<])"), ">="),
    (re.compile(r"(?<![<>=!])>(?![=>])"), "<="),
    # Arithmetic ``+`` / ``-`` that are not augmented-assign / unary-ish ``+=``, ``-=``.
    (re.compile(r"(?<![+\-=])\+(?![+=])"), "-"),
    (re.compile(r"(?<![+\-=])-(?![\-=>])"), "+"),
    (re.compile(r"\bTrue\b"), "False"),
    (re.compile(r"\bFalse\b"), "True"),
    (re.compile(r"\band\b"), "or"),
    (re.compile(r"\bor\b"), "and"),
    # ``return <expr>`` -> ``return None`` (drop the returned value). The expression
    # is anything up to the end of the logical line.
    (re.compile(r"(?m)\breturn\s+(?!None\b)\S.*$"), "return None"),
]


def _generate_mutants(source: str, max_mutants: int) -> list[tuple[str, str]]:
    """Up to ``max_mutants`` ``(label, mutated_source)`` pairs from ``source``.

    Deterministic: mutators are tried in a fixed order, and within each mutator the
    match SITES are tried left-to-right; exactly one site is changed per mutant. The
    ``label`` is a short human-readable snippet of the mutated line for ``survivors``.
    Mutants identical to the original (no-op swaps) are skipped.
    """
    mutants: list[tuple[str, str]] = []
    seen: set[str] = set()
    for pattern, repl in _MUTATORS:
        for m in pattern.finditer(source):
            mutated = source[: m.start()] + _expand(m, repl) + source[m.end() :]
            if mutated == source or mutated in seen:
                continue
            seen.add(mutated)
            mutants.append((_label(source, m.start(), pattern.pattern, repl), mutated))
            if len(mutants) >= max_mutants:
                return mutants
    return mutants


def _expand(m: re.Match[str], repl: str) -> str:
    """Replacement for a single match. ``return ...`` rewrites to the literal
    ``return None``; all other swaps are plain literal replacements."""
    return repl


def _label(source: str, pos: int, pattern: str, repl: str) -> str:
    """A short, stable description of a mutation: the trimmed source line it hit plus
    the operator change applied — enough for a human to find the weak spot."""
    line_start = source.rfind("\n", 0, pos) + 1
    line_end = source.find("\n", pos)
    if line_end == -1:
        line_end = len(source)
    snippet = source[line_start:line_end].strip()
    return f"{pattern!s}->{repl!s} @ {snippet}"


def mutation_audit(
    source: str,
    run_tests: Callable[[str], bool],
    *,
    max_mutants: int = 8,
) -> dict[str, Any]:
    """Advisory mutation-strength audit over a SOURCE STRING (Just et al., 2014).

    Generate up to ``max_mutants`` textual mutants of ``source`` via simple operator /
    literal swaps (``==``<->``!=``, ``<``<->``>=``, ``<=``<->``>``, ``+``<->``-``,
    ``True``<->``False``, ``and``<->``or``, ``return x`` -> ``return None``). For each,
    call the INJECTED ``run_tests(mutated_source) -> bool`` (True = the suite still
    passes on this mutant). A mutant is KILLED when the suite FAILS on it (good — the
    fault was detected); a SURVIVOR means the suite was blind to that fault.

    Returns ``{mutants, killed, survived, score, survivors}`` where ``score =
    killed / mutants`` (0.0 when no mutants could be generated) and ``survivors`` lists
    the surviving mutants' labels. PURE and deterministic given ``source`` +
    ``run_tests``: NO file writes, NO real test runner here (the caller injects one
    that copies to a temp dir, runs the gate, and always restores).
    """
    mutants = _generate_mutants(source, max(0, max_mutants))
    killed = 0
    survivors: list[str] = []
    for label, mutated in mutants:
        passed = run_tests(mutated)
        if passed:
            # Suite still green on a broken program -> the fault SURVIVED (weak spot).
            survivors.append(label)
        else:
            killed += 1
    total = len(mutants)
    score = (killed / total) if total else 0.0
    return {
        "mutants": total,
        "killed": killed,
        "survived": total - killed,
        "score": score,
        "survivors": survivors,
    }
