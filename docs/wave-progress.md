# Improvement-wave execution log

Autonomous execution of `docs/improvement-plan.md`, started 2026-07-02.
Branch: `feat/qa-loop-harness`. One checkpoint commit per completed chunk; nothing pushed.
Owner decisions: personal-tool-first; hybrid UX (ops clarity + cosmic identity).

## Status

| Wave | Status | Commit |
|---|---|---|
| A0 state layer (reducers error/failed-dark/token-usage, engine heartbeat/capture/live-metrics, CI vitest) | DONE — all suites green | 29f6830 |
| U0 frontend (failed-dark visuals, resume/restart copy, Stop-now, Cosmos auto-refresh, desktop staleness, MetricsPanel copy, composeLoopDef fix) | DONE — check 0/0, vitest 16, screenshot pass | see U0+A1 commit |
| A1 Rust (per-loop spawn mutex covering desktop+LAN, guard fallback removal) | DONE — cargo 47+8 | see U0+A1 commit |
| A1 engine (iterTimeoutMin=60 + BMAD per-phase timeouts, BaseException tree-kill, mutation-backup + INIT recovery, shared lockfile (QA gained one too), generic resume fidelity, loop-supervise entrypoint) | DONE — pytest 505, ruff clean | see U0+A1 commit |
| U1 plain language, trust chips, body labels, timestamps, help reference | DONE | 79b5ff0 |
| U2 grid dock, Mechanism/lighthouse retired, phone Observatory, BodyView drawer | DONE (recovered from session-limit interruption) | 8a88330 |
| A2 driver shell, floor_breach, configkeys loader, single-file configs, greenWhen removed, PROTOCOL refresh | DONE — pytest 538 | 8a88330 |
| U3 creation & onboarding (TASK.md scaffold, probe-gate, honest dials incl. blueprints greenWhen/dial-field cleanup, empty state) | pending | — |
| A3 watcher/perf/LAN + frontend dedup | pending | — |
| U4 remote & ambient (QR share, phone polish, notifications) | pending | — |
| A4 OSS readiness subset | pending | — |

## Verification protocol per wave
- `cargo test --manifest-path orrery/src-tauri/Cargo.toml`
- `cd orrery && npm run test:unit && npm run check`
- `ruff check engine` + `pytest engine/tests -q` (venv at .venv/Scripts)
- UI waves: `npm run dev` (port 1420) + `node _shots.mjs <outdir>` screenshot review

## Notes
- First commit (29f6830) includes the pre-existing in-flight qa-loop-harness
  working-tree changes because they shared files with the wave; restructure
  later if desired.
- `orrery/loops/**/.auth/` now gitignored (browser session state).
  `orrery/loops/brain2-qa/HANDOFF.md` deliberately left uncommitted (may
  reference credentials).
- Baselines before waves: pytest 442 passed; cargo 44 lib + 8 golden;
  vitest 16; svelte-check 0 errors.
