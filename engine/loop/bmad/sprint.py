"""Parse ``sprint-status.yaml`` (BMAD) and select the next actionable story.

Faithful Python port of the sprint-scan + story-selection logic in
``bmad-loop.ps1`` (the BMAD v6.8.0 driver), modelled on the authoritative
schema the Rust reducer extracts in ``orrery/src-tauri/src/sprint.rs``:

The ``development_status`` map mixes three kinds of keys:

- ``epic-N``               -> group (epic) lifecycle: backlog | in-progress | done
- ``epic-N-retrospective`` -> group retro status:    optional | done | pending
- ``N-M-...`` (story key)   -> work item status:
  backlog | ready-for-dev/ready | in-progress | review | done | blocked | failed

Tolerant of absence/odd fields: a malformed file yields an empty
:class:`SprintStatus`; unknown statuses / unkeyable rows are skipped.

The git "done-but-unmerged" check that wins selection priority lives in the
driver (it shells out to git); to keep selection pure and testable, that check
is INJECTED into :func:`select_actionable` as the ``is_unmerged_done``
predicate.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Callable

# --- status vocabularies (PROTOCOL.md §3 ItemStatus + sprint.rs parse_*) -----
# Story statuses the driver acts on. 'ready' is the canonical alias of
# 'ready-for-dev' (sprint.rs treats them identically).
ITEM_STATUSES: dict[str, str] = {
    "backlog": "backlog",
    "ready": "ready",
    "ready-for-dev": "ready",
    "in-progress": "in-progress",
    "review": "review",
    "done": "done",
    "blocked": "blocked",
    "failed": "failed",
}
GROUP_STATUSES = frozenset({"backlog", "in-progress", "done"})
RETRO_STATUSES = frozenset({"optional", "done", "pending"})


@dataclass(frozen=True)
class Story:
    """One work item (a BMAD story).

    ``key`` is the raw ``N-M-...`` story key; ``raw_status`` is the verbatim
    string from the YAML (e.g. ``ready-for-dev``); ``status`` is its canonical
    form (``ready-for-dev`` -> ``ready``). ``epic`` is the leading number,
    ``index`` is the story's positional order within the file.
    """

    key: str
    status: str
    raw_status: str
    epic: str | None
    index: int


@dataclass(frozen=True)
class Epic:
    """One group (a BMAD epic): its ``epic-N`` lifecycle + retrospective state."""

    key: str
    status: str | None
    retro: str | None
    index: int


@dataclass
class SprintStatus:
    """Typed view of a parsed ``sprint-status.yaml`` (mirrors ``sprint.rs``)."""

    stories: list[Story] = field(default_factory=list)
    epics: list[Epic] = field(default_factory=list)

    def story(self, key: str) -> Story | None:
        """First story with an exact ``key`` (or ``None``)."""
        for s in self.stories:
            if s.key == key:
                return s
        return None

    def epic(self, epic_key: str) -> Epic | None:
        """The ``Epic`` row for ``epic_key`` (the bare number, e.g. ``"3"``)."""
        for e in self.epics:
            if e.key == epic_key:
                return e
        return None


# --- key helpers (mirror sprint.rs is_story_key / epic_of) -------------------
def _is_story_key(key: str) -> bool:
    """``N-M-...`` — first two dash-parts are non-empty all-digit (sprint.rs)."""
    parts = key.split("-")
    if len(parts) < 2:
        return False
    head, second = parts[0], parts[1]
    return bool(head) and head.isdigit() and bool(second) and second.isdigit()


def _epic_of(key: str) -> str | None:
    """Leading all-digit segment of a story key, else ``None`` (sprint.rs)."""
    head = key.split("-", 1)[0]
    return head if head and head.isdigit() else None


def parse_sprint_status(text_or_path: str | os.PathLike) -> SprintStatus:
    """Load ``sprint-status.yaml`` into a typed :class:`SprintStatus`.

    ``text_or_path`` may be the YAML text itself OR a path to the file. A path
    that exists is read; otherwise the argument is treated as YAML text. A
    missing file or malformed YAML yields an empty :class:`SprintStatus`
    (tolerant, like ``sprint.rs::parse_str`` / ``parse_file``).

    ``yaml`` (PyYAML) is imported lazily so importing this module stays cheap.
    """
    import yaml  # lazy: only needed to actually parse a sprint file

    text = text_or_path
    try:
        if isinstance(text_or_path, (str, os.PathLike)) and os.path.exists(text_or_path):
            with open(text_or_path, encoding="utf-8") as fh:
                text = fh.read()
    except OSError:
        return SprintStatus()

    try:
        root = yaml.safe_load(text)
    except yaml.YAMLError:
        return SprintStatus()

    if not isinstance(root, dict):
        return SprintStatus()
    dev = root.get("development_status")
    if not isinstance(dev, dict):
        return SprintStatus()

    stories: list[Story] = []
    epics_by_key: dict[str, dict] = {}
    order = 0  # preserves first-seen ordering for selection determinism

    for raw_key, raw_val in dev.items():
        key = str(raw_key)
        if raw_val is None:
            continue
        val = str(raw_val).strip()

        if key.startswith("epic-"):
            rest = key[len("epic-") :]
            if rest.endswith("-retrospective"):
                epic_num = rest[: -len("-retrospective")]
                if val in RETRO_STATUSES:
                    epics_by_key.setdefault(epic_num, _new_epic(epic_num, order))["retro"] = val
                    order += 1
            elif rest and rest.isdigit():
                if val in GROUP_STATUSES:
                    epics_by_key.setdefault(rest, _new_epic(rest, order))["status"] = val
                    order += 1
            continue

        if _is_story_key(key):
            canon = ITEM_STATUSES.get(val)
            if canon is None:
                continue  # unknown story status -> skip (tolerant)
            stories.append(
                Story(
                    key=key,
                    status=canon,
                    raw_status=val,
                    epic=_epic_of(key),
                    index=order,
                )
            )
            order += 1

    epics = [
        Epic(key=k, status=e["status"], retro=e["retro"], index=e["index"])
        for k, e in epics_by_key.items()
    ]
    epics.sort(key=lambda e: (not e.key.isdigit(), int(e.key) if e.key.isdigit() else 0, e.key))
    return SprintStatus(stories=stories, epics=epics)


def _new_epic(key: str, index: int) -> dict:
    return {"status": None, "retro": None, "index": index}


# --- selection: PRIORITY ORDER (port of bmad-loop.ps1 lines 646 / 707) -------
def _first_with_status(stories: list[Story], status: str) -> Story | None:
    """``First-WithStatus``: first story whose canonical status matches."""
    for s in stories:
        if s.status == status:
            return s
    return None


def select_actionable(
    stories: list[Story],
    *,
    is_unmerged_done: Callable[[Story], bool] | None = None,
) -> Story | None:
    """Pick the next actionable story in the driver's PRIORITY ORDER.

    Port of the selection chain in ``bmad-loop.ps1`` (the
    ``@((First-UnmergedDone ...),(First-WithStatus ... 'in-progress'),
    (First-WithStatus ... 'review'),(First-WithStatus ... 'ready-for-dev'),
    (First-WithStatus ... 'backlog')) | ? {$_} | Select -First 1`` expression):

    1. a ``done`` story that is dev'd + reviewed but NOT yet merged
       (``is_unmerged_done`` — INJECTED; the git descendant-of-merge-base check
       lives in the driver). Resume it at smoke+merge.
    2. ``in-progress``
    3. ``review``
    4. ``ready-for-dev`` / ``ready``
    5. ``backlog``

    Returns the chosen :class:`Story`, or ``None`` when nothing is actionable
    (e.g. every story already merged/``done`` with no unmerged work).
    """
    if is_unmerged_done is not None:
        for s in stories:
            if s.status == "done" and is_unmerged_done(s):
                return s
    for status in ("in-progress", "review", "ready", "backlog"):
        hit = _first_with_status(stories, status)
        if hit is not None:
            return hit
    return None


# --- epic helpers (retro triggering) ----------------------------------------
def epic_scope(sprint: SprintStatus, epic_key: str) -> list[Story]:
    """Every story belonging to ``epic_key`` (the leading number), in order.

    Mirrors the driver's ``$_.Key -split '-')[0] -eq $EpicOnly`` epic filter.
    """
    return [s for s in sprint.stories if s.epic == epic_key]


def epic_done(sprint: SprintStatus, epic_key: str) -> bool:
    """True when ``epic_key`` has >=1 story and ALL of them are ``done``.

    Port of ``Get-PendingRetro``'s "all stories done" test — the condition that
    makes an epic retrospective due (``$es.Count -gt 0 -and -not ($es | ?
    {$_.Status -ne 'done'})``).
    """
    scope = epic_scope(sprint, epic_key)
    return bool(scope) and all(s.status == "done" for s in scope)
