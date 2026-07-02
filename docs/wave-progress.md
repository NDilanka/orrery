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
| U3 creation & onboarding (honest dials + schema-guard test, write_loop_file TASK.md scaffold, probe_command gate test, empty-state checklist) | DONE — cargo 59+8, vitest 28 | 3b79f1d |
| A3 watcher lifecycle, incremental reduce, tailer rotation, real `_t` timestamps in caller, /ws token auth, no-0.0.0.0, sessionStore/focusTrap/answerFlow/palette dedup | DONE — cargo 66+8 | f2060b5 |
| U4 remote & ambient (Share button + QR via qrcode-generator, ambient loudness parity for FAILED/quota, edge-detected alert banners w/ 14 tests; native OS notifications deferred — needs tauri-plugin-notification) | DONE — vitest 42 | see final commit |
| A4 portability + live timestamps (engine stamps `_t` at append_event, POSIX process_group(0), relative intra-loop seed paths, shell-dialect + adapter docs; RunState neutralization DEFERRED; also fixed 8 wire-contract tests broken by A3's fixture `_t`) | DONE — pytest 543 | see final commit |

**PLAN COMPLETE 2026-07-02.** Final integrated verification: ruff clean, pytest 543,
cargo 67 lib + 8 golden, vitest 42, svelte-check 0 errors (3 pre-existing a11y warnings),
final screenshot pass reviewed. Known deferred items: native OS notifications (Tauri
plugin), RunState schema neutralization (until a third adapter shape needs it),
failed-dark fixture `_t` backfill, third-party adapter guide beyond README section.

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
