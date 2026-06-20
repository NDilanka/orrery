# orrery-loop (engine)

Pure, testable harness logic for the Orrery loop engine — a faithful Python port of the
PowerShell `loopcore.ps1` decision/event cores plus the core event shapes defined in
`orrery/PROTOCOL.md` §2.

This package is **pure**: no network, no claude calls, no I/O beyond what the callers do.
It is the single source of truth for `log.jsonl` event shapes and the loop decision logic
on the Python side.

## Modules

- `loop.events` — builders for every `log.jsonl` event object and `checkpoint.json` (returns plain `dict`s).
- `loop.decide` — `decide()` (port of `Get-LoopDecision`) and `update_consecutive_fail()` (port of `Update-ConsecutiveFail`).

## Tests

```
python -m pytest tests -q
```

Golden parity fixtures are generated from the authoritative PowerShell source via
`tests/gen_golden.ps1` (run with PowerShell 7).
