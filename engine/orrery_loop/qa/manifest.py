"""Build the acceptance-criteria manifest the QA discovery pass judges against.

Parses a directory of BMAD-style story files (``<epic>-<story>-<slug>.md``) into a
structured, per-epic manifest. The parser is deliberately *shallow*: it pulls the
story identity (id / epic / title / status), the raw ``## Acceptance Criteria``
markdown block, and the list of ``**ACn — title**`` headers — but does NOT try to
parse every Given/When/Then sub-clause. The discovery agent reads the raw AC
markdown and decides which clauses are *browser-observable*; over-parsing here
would be brittle across 30+ hand/agent-authored files for no gain.

The output JSON is the oracle: ``epics[] -> stories[] -> {criteria[], acMarkdown}``.
Keys are camelCase to match the Orrery wire protocol (PROTOCOL.md), so the same
artifact can feed both the Python driver and any TS consumer.

CLI::

    python -m orrery_loop.qa.manifest <stories-dir> [-o out.json] [--app NAME] [--base-url URL]
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path

# ``2-1-capture-input-and-thread-display.md`` -> epic 2, story 1. Non-story files
# (``sprint-status.yaml``, ``epic-2-retro-….md``, ``deferred-work.md``) never match.
_STORY_FILE_RE = re.compile(r"^(\d+)-(\d+)-.+\.md$")
_TITLE_RE = re.compile(r"^#\s+Story\s+([\d.]+)\s*:\s*(.+?)\s*$", re.MULTILINE)
_STATUS_RE = re.compile(r"^Status:\s*(.+?)\s*$", re.MULTILINE)
# ``**AC1 — Capture input renders (messaging model)**`` — separator may be an em/en
# dash, hyphen, or colon; the title is optional (``**AC1**`` still counts).
_AC_RE = re.compile(r"\*\*AC(\d+)\b[ \t]*[—–:.\-]*[ \t]*(.*?)\s*\*\*")
_H2_RE = re.compile(r"^##\s+\S")
_AC_H2_RE = re.compile(r"^##\s+Acceptance Criteria\s*$", re.IGNORECASE)


@dataclass
class AcceptanceCriterion:
    id: str  # "AC1"
    title: str  # "Capture input renders (messaging model)" ("" when unlabelled)


@dataclass
class Story:
    id: str  # "2.1"
    epic: int  # 2
    title: str
    status: str
    file: str
    criteria: list[AcceptanceCriterion]
    ac_markdown: str  # raw text of the ## Acceptance Criteria block


def _ac_block(md: str) -> str:
    """Return the text under ``## Acceptance Criteria`` up to the next ``## `` header."""
    out: list[str] = []
    capturing = False
    for line in md.splitlines():
        if _AC_H2_RE.match(line):
            capturing = True
            continue
        if capturing and _H2_RE.match(line):
            break
        if capturing:
            out.append(line)
    return "\n".join(out).strip()


def _criteria(block: str) -> list[AcceptanceCriterion]:
    """Extract ``ACn`` headers from an AC block, de-duplicated, in source order."""
    seen: dict[str, AcceptanceCriterion] = {}
    for num, title in _AC_RE.findall(block):
        cid = f"AC{num}"
        if cid not in seen:
            seen[cid] = AcceptanceCriterion(id=cid, title=title.strip())
    return list(seen.values())


def parse_story_file(path: str | Path) -> Story | None:
    """Parse one story ``.md`` file. Returns ``None`` for non-story filenames."""
    p = Path(path)
    fm = _STORY_FILE_RE.match(p.name)
    if not fm:
        return None
    md = p.read_text(encoding="utf-8")
    tm = _TITLE_RE.search(md)
    sid = tm.group(1) if tm else f"{int(fm.group(1))}.{int(fm.group(2))}"
    title = tm.group(2).strip() if tm else p.stem
    sm = _STATUS_RE.search(md)
    status = sm.group(1).strip() if sm else "unknown"
    block = _ac_block(md)
    return Story(
        id=sid,
        epic=int(fm.group(1)),
        title=title,
        status=status,
        file=str(p),
        criteria=_criteria(block),
        ac_markdown=block,
    )


def _sort_key(story_id: str) -> tuple[int, ...]:
    parts: list[int] = []
    for chunk in story_id.split("."):
        try:
            parts.append(int(chunk))
        except ValueError:
            parts.append(0)
    return tuple(parts)


def _story_dict(s: Story) -> dict:
    return {
        "id": s.id,
        "epic": s.epic,
        "title": s.title,
        "status": s.status,
        "file": s.file,
        "acCount": len(s.criteria),
        "criteria": [{"id": c.id, "title": c.title} for c in s.criteria],
        "acMarkdown": s.ac_markdown,
    }


def build_manifest(
    stories_dir: str | Path,
    *,
    app: str | None = None,
    base_url: str | None = None,
) -> dict:
    """Scan ``stories_dir`` for story files and group them into a per-epic manifest."""
    d = Path(stories_dir)
    stories = [s for f in sorted(d.glob("*.md")) if (s := parse_story_file(f))]
    stories.sort(key=lambda s: _sort_key(s.id))

    by_epic: dict[int, list[Story]] = {}
    for s in stories:
        by_epic.setdefault(s.epic, []).append(s)

    epics = [
        {
            "epic": e,
            "storyCount": len(by_epic[e]),
            "acCount": sum(len(s.criteria) for s in by_epic[e]),
            "stories": [_story_dict(s) for s in by_epic[e]],
        }
        for e in sorted(by_epic)
    ]
    return {
        "app": app,
        "baseUrl": base_url,
        "generatedFrom": str(d),
        "epicCount": len(epics),
        "storyCount": len(stories),
        "acCount": sum(len(s.criteria) for s in stories),
        "epics": epics,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="orrery_loop.qa.manifest", description="Build the AC manifest from story .md files."
    )
    parser.add_argument("stories_dir", help="directory of <epic>-<story>-<slug>.md files")
    parser.add_argument("-o", "--out", default=None, help="write JSON here (default: stdout)")
    parser.add_argument("--app", default=None, help="app name to record in the manifest")
    parser.add_argument("--base-url", default=None, help="base URL of the app under test")
    args = parser.parse_args(argv)

    manifest = build_manifest(args.stories_dir, app=args.app, base_url=args.base_url)
    text = json.dumps(manifest, indent=2, ensure_ascii=False)
    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text + "\n", encoding="utf-8")
        print(
            f"{manifest['epicCount']} epics · {manifest['storyCount']} stories · "
            f"{manifest['acCount']} ACs -> {out}"
        )
    else:
        print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
