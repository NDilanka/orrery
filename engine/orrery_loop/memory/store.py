"""Cross-run lessons memory: the interface + a file-backed implementation.

This module is intentionally self-contained (pure logic + local file IO, no
network, no clock calls in the ranking/pruning paths). See the package docstring
for the research lineage. The loop integrates this later via constructor
injection; the default is :class:`NullMemoryStore` so the feature is OFF unless a
caller wires up a :class:`FileMemoryStore`.

Key invariants
--------------
* **Append-only** persistence (JSONL). ``record`` only appends a line; it never
  rewrites the file body. Compaction (``prune``) is the *only* path that rewrites,
  and it is an explicit, bounded, opt-in operation — never triggered implicitly by
  ``record``. (ACE anti-collapse rule.)
* **Provenance on every lesson** — outcome / run_id / iter / created_ts — so a bad
  lesson is attributable and can be selectively forgotten (anti-poisoning).
* **Deterministic** ranking & pruning — all time inputs are INJECTED (``now`` /
  ``created_ts``). Nothing here calls ``time.time()``.
"""

from __future__ import annotations

import json
import math
import re
from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable, Iterable

# Outcomes that are SAFE to learn from. Anything else (e.g. 'regress', 'handoff')
# is dropped by ``record_if_useful`` so failed attempts cannot poison memory.
USEFUL_OUTCOMES: frozenset[str] = frozenset({"green", "progress"})

# Recency half-life: a lesson's recency factor halves every this-many seconds.
# 14 days — long enough to keep durable strategies warm, short enough that stale
# episodic lessons fade. Used only with an INJECTED ``now``.
_RECENCY_HALF_LIFE_SEC: float = 14 * 24 * 3600.0

# Token splitter for the Jaccard relevance overlap (lowercased word characters).
_WORD_RE = re.compile(r"[a-z0-9]+")

# Stopwords excluded from the relevance token set so generic filler words do not
# inflate overlap between unrelated tasks.
_STOPWORDS: frozenset[str] = frozenset(
    {
        "the", "a", "an", "and", "or", "to", "of", "in", "on", "for", "with",
        "is", "are", "be", "this", "that", "it", "as", "at", "by", "from",
        "into", "we", "you", "i", "do", "does", "did", "fix", "make", "add",
    }
)


@dataclass
class Lesson:
    """A single distilled lesson / strategy with full provenance.

    ``kind`` separates durable repo facts (``'semantic'``) from run-specific
    lessons (``'episodic'``) per CoALA. The provenance fields (``outcome``,
    ``run_id``, ``iter``, ``created_ts``) make every lesson attributable and
    forgettable. ``created_ts`` is an INJECTED epoch — never call the clock here.
    """

    text: str
    kind: str  # 'episodic' | 'semantic'
    task: str
    outcome: str  # 'green' | 'progress' | 'regress' | 'handoff'
    run_id: str
    iter: int
    created_ts: float
    weight: float = 1.0

    def to_json(self) -> str:
        """Serialize to a single compact JSON line (for JSONL append)."""
        return json.dumps(asdict(self), ensure_ascii=False, sort_keys=True)

    @classmethod
    def from_json(cls, line: str) -> "Lesson":
        """Parse one JSONL line back into a :class:`Lesson` (round-trip)."""
        d = json.loads(line)
        return cls(
            text=str(d["text"]),
            kind=str(d["kind"]),
            task=str(d.get("task", "")),
            outcome=str(d.get("outcome", "")),
            run_id=str(d.get("run_id", "")),
            iter=int(d.get("iter", 0)),
            created_ts=float(d.get("created_ts", 0.0)),
            weight=float(d.get("weight", 1.0)),
        )


# --------------------------------------------------------------------------- #
# Pure helpers (no IO, no clock) — the deterministic ranking core.
# --------------------------------------------------------------------------- #

def _tokens(text: str) -> frozenset[str]:
    """Lowercased word-token set with stopwords removed (for Jaccard overlap)."""
    return frozenset(t for t in _WORD_RE.findall(text.lower()) if t not in _STOPWORDS)


def _jaccard(a: frozenset[str], b: frozenset[str]) -> float:
    """Jaccard similarity of two token sets in ``[0, 1]`` (0 when both empty)."""
    if not a and not b:
        return 0.0
    union = a | b
    if not union:
        return 0.0
    return len(a & b) / len(union)


def _recency_factor(created_ts: float, now: float) -> float:
    """Exponential recency decay in ``(0, 1]`` with a fixed half-life.

    Deterministic given the INJECTED ``now``. A lesson created in the future
    (clock skew) or "now" scores 1.0; older lessons decay toward 0.
    """
    age = now - created_ts
    if age <= 0:
        return 1.0
    return math.pow(0.5, age / _RECENCY_HALF_LIFE_SEC)


def _normalize_text(text: str) -> str:
    """Normalize a lesson's text for near-duplicate detection.

    Lowercase, collapse all runs of non-alphanumerics to single spaces, strip.
    "Use --no-verify on commit." and "use   --no-verify, on commit!" collapse to
    the same key.
    """
    return " ".join(_WORD_RE.findall(text.lower()))


def score_lesson(lesson: Lesson, task_tokens: frozenset[str], now: float) -> float:
    """Relevance score: ``relevance x recency x weight`` (Generative-Agents style).

    * **relevance** = Jaccard overlap of the lesson's (text + task) tokens with the
      query ``task`` tokens. Semantic facts get a small floor so durable facts stay
      surfaced even when token overlap with the current task is thin.
    * **recency**   = exponential decay from the INJECTED ``now``.
    * **weight**    = the lesson's importance/trust weight (decayable; a poisoned
      lesson can be weight-decayed toward irrelevance).

    Pure and deterministic given its inputs.
    """
    lesson_tokens = _tokens(lesson.text) | _tokens(lesson.task)
    relevance = _jaccard(lesson_tokens, task_tokens)
    if lesson.kind == "semantic":
        # Durable repo facts keep a relevance floor so they remain recall-able.
        relevance = max(relevance, 0.15)
    recency = _recency_factor(lesson.created_ts, now)
    return relevance * recency * max(lesson.weight, 0.0)


# --------------------------------------------------------------------------- #
# Interface + Null implementation.
# --------------------------------------------------------------------------- #

class MemoryStore(ABC):
    """The interface the loop uses to recall and record cross-run lessons."""

    @abstractmethod
    def recall(self, task: str, *, limit: int = 5) -> str:
        """Return a compact text block (for the stable prompt prefix).

        Semantic facts first, then the most task-relevant episodic lessons.
        Returns ``""`` when there is nothing to recall.
        """

    @abstractmethod
    def record(self, lesson: Lesson) -> None:
        """Persist a lesson (append-only)."""

    @abstractmethod
    def prune(
        self,
        *,
        max_entries: int = 200,
        max_age_sec: float | None = None,
        now: float | None = None,
    ) -> int:
        """Drop aged / over-cap entries; return the count removed."""


class NullMemoryStore(MemoryStore):
    """The default OFF store: recall -> ``""``, record / prune -> no-op."""

    def recall(self, task: str, *, limit: int = 5) -> str:
        return ""

    def record(self, lesson: Lesson) -> None:
        return None

    def record_if_useful(self, lesson: Lesson) -> bool:
        """No-op; always reports "not recorded"."""
        return False

    def prune(
        self,
        *,
        max_entries: int = 200,
        max_age_sec: float | None = None,
        now: float | None = None,
    ) -> int:
        return 0


# --------------------------------------------------------------------------- #
# File-backed implementation.
# --------------------------------------------------------------------------- #

@dataclass
class _RecallConfig:
    """Tunables for :meth:`FileMemoryStore.recall` (kept small & explicit)."""

    max_semantic: int = 3  # at most this many durable facts lead the block.


class FileMemoryStore(MemoryStore):
    """Append-only JSONL store: episodic lessons + a semantic facts section.

    Persistence is a single JSONL file (one :class:`Lesson` per line). ``record``
    only ever *appends* (ACE anti-collapse). ``recall`` reads + ranks in memory and
    never mutates the file. ``prune`` / ``forget`` are the only rewrite paths and
    they are explicit and bounded.

    The ``kind`` field keeps SEMANTIC (durable repo facts) distinct from EPISODIC
    (run-specific lessons): ``recall`` leads with up to a few semantic facts, then
    fills with the highest-scoring episodic lessons.
    """

    recall_cfg = _RecallConfig()

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    # -- persistence ------------------------------------------------------- #

    def _load(self) -> list[Lesson]:
        """Read all lessons from disk (skips blank/corrupt lines tolerantly)."""
        if not self.path.exists():
            return []
        out: list[Lesson] = []
        with self.path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    out.append(Lesson.from_json(line))
                except (ValueError, KeyError, TypeError):
                    # A single bad line must never break recall for the rest.
                    continue
        return out

    def record(self, lesson: Lesson) -> None:
        """Append one lesson as a JSONL line (creates the file/dirs if needed).

        Append-only: the existing file body is never read or rewritten here.
        """
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(lesson.to_json() + "\n")

    def record_if_useful(self, lesson: Lesson) -> bool:
        """Green-gated, de-duped append. Returns ``True`` iff it was recorded.

        Drops lessons whose ``outcome`` is not in :data:`USEFUL_OUTCOMES` (so
        ``regress`` / ``handoff`` attempts never poison memory) and de-dupes
        near-identical ``text`` (normalized) already present, to bound growth.
        The green-gate decision is normally the caller's; this is the safety net.
        """
        if lesson.outcome not in USEFUL_OUTCOMES:
            return False
        key = _normalize_text(lesson.text)
        if not key:
            return False
        existing = {_normalize_text(prev.text) for prev in self._load()}
        if key in existing:
            return False
        self.record(lesson)
        return True

    # -- recall ------------------------------------------------------------ #

    def recall(self, task: str, *, limit: int = 5, now: float | None = None) -> str:
        """Compact bulleted block: semantic facts first, then top episodic.

        Ranking is ``relevance x recency x weight`` (see :func:`score_lesson`).
        Deterministic when ``now`` is injected; when ``now`` is ``None`` recency is
        neutralized (every lesson gets recency 1.0) so the result stays
        reproducible without a clock call.
        """
        if limit <= 0:
            return ""
        lessons = self._load()
        if not lessons:
            return ""

        task_tokens = _tokens(task)
        # Neutralize recency when no clock is injected: rank by relevance x weight.
        if now is None:
            scored = [
                (score_lesson(lsn, task_tokens, lsn.created_ts), idx, lsn)
                for idx, lsn in enumerate(lessons)
            ]
        else:
            scored = [
                (score_lesson(lsn, task_tokens, now), idx, lsn)
                for idx, lsn in enumerate(lessons)
            ]

        semantic = [t for t in scored if t[2].kind == "semantic"]
        episodic = [t for t in scored if t[2].kind != "semantic"]

        # Stable ordering: score desc, then insertion order asc (deterministic).
        semantic.sort(key=lambda t: (-t[0], t[1]))
        episodic.sort(key=lambda t: (-t[0], t[1]))

        chosen: list[Lesson] = []
        for _, _, lsn in semantic[: self.recall_cfg.max_semantic]:
            chosen.append(lsn)
        for _, _, lsn in episodic:
            if len(chosen) >= limit:
                break
            chosen.append(lsn)
        chosen = chosen[:limit]
        if not chosen:
            return ""

        lines = ["Lessons from prior runs:"]
        for lsn in chosen:
            tag = "fact" if lsn.kind == "semantic" else lsn.outcome
            lines.append(f"- [{tag}] {lsn.text.strip()}")
        return "\n".join(lines)

    # -- decay / retention / anti-poisoning -------------------------------- #

    def _rewrite(self, lessons: Iterable[Lesson]) -> None:
        """Atomically replace the file body (the ONLY rewrite path).

        Used by ``prune`` / ``forget`` only — never by ``record``. Writes to a temp
        sibling then ``os.replace`` so a crash can't leave a half-written file.
        """
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        with tmp.open("w", encoding="utf-8") as fh:
            for lsn in lessons:
                fh.write(lsn.to_json() + "\n")
        tmp.replace(self.path)

    def prune(
        self,
        *,
        max_entries: int = 200,
        max_age_sec: float | None = None,
        now: float | None = None,
    ) -> int:
        """Decay/retention compaction. Returns the number of entries removed.

        Two stages:
          1. **Age cutoff** — when ``max_age_sec`` is set, drop entries older than
             that relative to the INJECTED ``now`` (required if age cutoff used).
          2. **Cap** — if still over ``max_entries``, keep the highest-SCORING
             survivors (relevance is ``1.0`` here since prune has no task query, so
             this reduces to recency x weight) and drop the rest.

        Semantic facts are scored like any other entry but their relevance floor
        plus typical higher weight makes them robust to the cap. Pure given inputs.
        """
        lessons = self._load()
        original = len(lessons)
        if original == 0:
            return 0

        survivors = lessons
        if max_age_sec is not None:
            if now is None:
                raise ValueError("prune(max_age_sec=...) requires an injected now")
            cutoff = now - max_age_sec
            survivors = [lsn for lsn in survivors if lsn.created_ts >= cutoff]

        if max_entries is not None and len(survivors) > max_entries:
            ref_now = now if now is not None else 0.0
            # Score with an empty task -> relevance collapses to the semantic floor
            # (or 0 for episodic), so ordering is driven by recency x weight; ties
            # break by recency. Keep the top ``max_entries``.
            ranked = sorted(
                survivors,
                key=lambda lsn: (
                    score_lesson(lsn, frozenset(), ref_now),
                    lsn.created_ts,
                ),
                reverse=True,
            )
            survivors = ranked[:max_entries]

        removed = original - len(survivors)
        if removed > 0:
            # Preserve original insertion order in the rewritten file.
            keep = set(map(id, survivors))
            ordered = [lsn for lsn in lessons if id(lsn) in keep]
            self._rewrite(ordered)
        return removed

    def forget(self, predicate: Callable[[Lesson], bool]) -> int:
        """Anti-poisoning removal: delete every lesson matching ``predicate``.

        Returns the count removed. This is the targeted forget path — e.g.
        ``forget(lambda l: l.run_id == bad_run)`` to excise a poisoned run, or
        ``forget(lambda l: l.outcome == 'regress')``. Rewrites the file body.
        """
        lessons = self._load()
        kept = [lsn for lsn in lessons if not predicate(lsn)]
        removed = len(lessons) - len(kept)
        if removed > 0:
            self._rewrite(kept)
        return removed

    def decay_weights(
        self, predicate: Callable[[Lesson], bool], *, factor: float = 0.5
    ) -> int:
        """Anti-poisoning soft-forget: multiply ``weight`` of matches by ``factor``.

        A gentler alternative to :meth:`forget` — a suspect lesson is demoted in
        ranking rather than deleted, so it fades without losing its provenance
        trail. Returns the number of lessons whose weight changed. Rewrites once.
        """
        lessons = self._load()
        changed = 0
        for lsn in lessons:
            if predicate(lsn):
                lsn.weight *= factor
                changed += 1
        if changed > 0:
            self._rewrite(lessons)
        return changed
