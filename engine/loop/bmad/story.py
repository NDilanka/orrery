"""Pure string parsers for a BMAD story ``.md`` file's content.

Faithful Python ports of the PURE PowerShell functions in ``bmad-loop.ps1``:

- :func:`story_meta` <- ``Get-StoryMeta`` (~256-265): extract the story's
  ``Status:`` line and its ``baseline_commit:`` value.
- :func:`story_acs`  <- ``Get-StoryACs``  (~267-278): pull the
  ``## Acceptance Criteria`` section as raw text, truncated to ``max_chars``.

No file I/O — the driver owns reading the story file (``Get-ChildItem ... |
Get-Content -Raw``); these operate on the resulting string. ``story_acs``
returns the RAW section text for the smoke prompt, distinct from
:func:`loop.verdict.contract_criteria` (which returns a list of criteria).
"""

from __future__ import annotations

import re
from typing import Any

# baseline_commit: optionally quoted 7-40 hex chars (Get-StoryMeta).
_BASELINE_RX = re.compile(r"""baseline_commit:\s*["']?([0-9a-f]{7,40})""")
# Status: at the START of a line (PS used (?m)^Status:\s*(.+?)\s*$).
_STATUS_RX = re.compile(r"^Status:\s*(.+?)\s*$", re.MULTILINE)

# '## Acceptance Criteria' (case-insensitive) up to the next '## ' heading or EOF.
# PS: (?ms)^##\s*Acceptance Criteria\s*\r?\n(.+?)(?=^\#\#\s|\z)
_ACS_RX = re.compile(
    r"^##\s*Acceptance Criteria\s*\r?\n(.+?)(?=^\#\#\s|\Z)",
    re.IGNORECASE | re.MULTILINE | re.DOTALL,
)


def story_meta(text: str | None) -> dict[str, Any]:
    """Port of ``Get-StoryMeta``.

    Extract the small front-matter/body fields the driver reads from a story
    ``.md``: its ``status`` (a ``Status:`` line) and ``baseline`` (the
    ``baseline_commit:`` value, used for auto-rollback). Both are ``None`` when
    absent. Returns ``{status, baseline}``.
    """
    if not text:
        return {"status": None, "baseline": None}

    sm = _STATUS_RX.search(text)
    status = sm.group(1).strip() if sm else None

    bm = _BASELINE_RX.search(text)
    baseline = bm.group(1) if bm else None

    return {"status": status, "baseline": baseline}


def story_acs(text: str | None, *, max_chars: int = 2500) -> str:
    """Port of ``Get-StoryACs``.

    Pull the ``## Acceptance Criteria`` section (case-insensitive heading) as
    raw text, from just after the heading up to the next ``## `` heading (or end
    of file). The section is trimmed; if longer than ``max_chars`` it is
    truncated to ``max_chars`` with a trailing `` ...(truncated)`` marker (the
    PS source appended `` …(truncated)``). Returns ``""`` when there is no such
    section.
    """
    if not text:
        return ""
    m = _ACS_RX.search(text)
    if not m:
        return ""
    acs = m.group(1).strip()
    if len(acs) > max_chars:
        acs = acs[:max_chars] + " …(truncated)"
    return acs
