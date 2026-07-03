# legacy/ — the original PowerShell reference engine

These PowerShell scripts are the **original reference implementation** of the Orrery
loop — the project's *first* engine. They are kept here for **provenance** and to
**regenerate the parity goldens** the Python port is checked against. They are **not**
the maintained engine: that is the Python package in [`../engine`](../engine), a
faithful port verified byte-compatible with [`../orrery/PROTOCOL.md`](../orrery/PROTOCOL.md).

| File | Role |
|---|---|
| `loop.ps1`       | the original generic fix-until-green loop driver |
| `loopcore.ps1`   | shared event/checkpoint builders + helpers |
| `gen_golden.ps1` | regenerates the golden parity corpus |

## Regenerating the golden corpus

`gen_golden.ps1` dot-sources its sibling `loopcore.ps1`, emits each event/checkpoint with
fixed deterministic args, and writes the corpus the Python builders are asserted against:

```bash
pwsh -NoProfile -File legacy/gen_golden.ps1
#  -> writes engine/tests/fixtures/golden_events.jsonl
```

The committed `engine/tests/fixtures/golden_events.jsonl` is the source of truth for
`engine/tests/test_events_golden.py`; that test does **not** run this script — it only
needs to be re-run if the wire shapes in `PROTOCOL.md` change.

> Requires PowerShell 7 (`pwsh`). Nothing else in the repo depends on these scripts at
> runtime; the supported engine is `../engine`.

## `demo/` — the original sample project

[`demo/`](demo) is the small TypeScript project (`roman.ts`/`calc.ts` plus their bun
tests, with the `TASK*.md` prompts and `settings.example.json`) that the original
PowerShell loop harness drove while the loop itself was being developed. Like the
scripts above it is kept for **provenance** and for regenerating the golden corpus —
it is not part of the maintained engine.
