# Changelog

All notable changes to Orrery are documented here. The format roughly follows
[Keep a Changelog](https://keepachangelog.com/). While pre-1.0, expect breaking
changes between minor versions.

## [Unreleased]

### Added — quality machinery wired into the BMAD loop (default-on)
- **Adversarial verify-before-merge**: an independent, refute-biased checker (Haiku,
  single-turn) sees only the story's frozen acceptance criteria + the baseline→HEAD diff,
  right before push/PR. `VERDICT: REFUTE` blocks the PR with an actionable halt;
  skipped/inconclusive fail open (gate + smoke already passed). Disable: `--no-verify` /
  `bmad.verify.enabled`.
- **Test-integrity check** (git-based, survives resume): deleting a pre-existing test file
  halts as tampering; modifying one is surfaced to the verifier with a scrutinize-for-weakened-
  assertions note. `bmad.testIntegrity` block.
- **Plan-gate before dev-story**: one cheap decider-tier call judges the ACs/tasks as
  unambiguous, testable, one-story-sized; explicit `BLOCKED:` halts before the expensive dev
  phase, anything else fails open. Disable: `--no-plan-gate`.
- **Run-quality `metrics` event at stop** (zero model tokens): stories completed/halted, gate
  reds, flaky retries, quota waits, token totals + cache hit ratio, cum USD, duration.
- **Gate fail-fast** (opt-in, engine + BMAD): stop launching gate stages after the first
  failure; skipped stages carry a `skipped` marker safe for floor/flaky consumers.

### Fixed — overnight hang/burn holes (engine reliability)
- The review/retro **decider calls are now time-bounded** (default 10 min; `0` = unbounded) —
  one wedged cheap-model call could previously hang an unattended run forever.
- The **quota probe is time-bounded** (120s; a hung probe counts as "still limited" so the
  wait loop re-probes instead of hanging), preserving the ≤6h auto-resume guarantee.
- **Quota detection restored to PS parity**: any errored phase now triggers one independent
  quota probe (not just result-text matches), so a limit that surfaces as a bare error waits
  for reset instead of stopping the run.
- **Session-resume after a quota hit**: a phase interrupted by quota resumes its own session
  (`--resume <id>`) after the wait instead of re-running the whole (possibly Opus) phase from
  scratch — with exactly one fresh-run fallback if the resume itself errors.
- **dev-story completion check restored** (PS parity): a green gate alone no longer advances
  the story; the story file's Status must have reached `review`/`done`, otherwise the loop
  halts ("likely a BMAD HALT") instead of pushing half-done work into code-review.
- The pytest suite is **CWD-portable** (green from repo root and `engine/`) and no longer
  asserts a machine-specific absolute path.

### Changed — BMAD driver parity with the original `bmad-loop.ps1`
- Agent phases **inherit the user's Claude Code default model** (the runner omits `--model`
  when a phase tier is empty), matching the PowerShell loop, which never pinned a model. Pin
  per-phase tiers via the loop.json `bmad.models` block if you want them fixed.
- The gate's test-count parser is **vitest-anchored** (`Tests\s+(\d+)\s+passed`), so the
  passing-test floor reads the test count — not the "Test Files" count.
- Restored the dev-story **regression guard** (halt when passing tests drop vs the branch
  baseline, with `--auto-rollback` to the story's `baseline_commit` as an opt-in), the distinct
  **codegen P1** halt, and the post-review / post-smoke count-floor checks.
- Auto-merge is now **verified** (`gh pr view --json state` must be `MERGED`) and `develop` is
  pulled before the next story, so a merge queued behind branch protection can't strand the next
  branch on a stale base.

### Fixed
- The BMAD checkpoint `resume` string carried a literal `loop-bmad --project-root <root>`
  placeholder that broke Reignite; it now reconstructs the real project-root / state-dir command
  plus the tuning flags the run actually changed.

### Orrery — run a BMAD sprint live from the app
- A loop can drive an external project end-to-end from the desktop / LAN app: Ignite / Brake /
  Reignite spawn and resume the real `loop-bmad` engine, and the Observatory tails its `log.jsonl`.
- `watch_run` threads an explicit `logFile`; run-control failures **surface in the control bar**
  instead of failing silently; the loops directory resolves absolutely so control commands find
  loops regardless of the app's working directory.

### Fixed — the live loop now actually runs (Windows)
- **Engine**: git/gate subprocess output was decoded as Windows **cp1252**, so a stray byte (a
  tsc/eslint/vitest glyph, a branch name) crashed the reader thread (`UnicodeDecodeError`) and
  silently zeroed the gate (baseline 400→0). Both sites now decode UTF-8 with `errors="replace"`.
- The desktop app silently ran in **replay** mode: `hasTauri()` tested `window.__TAURI__`, which
  Tauri v2 doesn't inject without `withGlobalTauri`. It now detects `__TAURI_INTERNALS__`.
- The watcher **died on a not-yet-existent `.loop`** and never re-armed (live UI stayed empty); it
  now creates the dir before watching. The log default is `log.jsonl` (what the engine writes), and
  a relative `stateDir` resolves to absolute so the watcher and engine agree on one directory.
- A stale `STOP` flag braked a fresh run immediately — now cleared on start/resume. `loop-bmad`
  resolves to the bundled `.venv` so it spawns regardless of how the app was launched.
- The reducer minted a phantom, text-less Decision-Chamber card on `review`/`retro-complete`
  ("(awaiting question text…)"); dropped in both reducers (golden regenerated).

### Added — observability & control feedback (Orrery)
- **Live LOG panel** — a raw event tail (via the `Delta.event` channel, fed across tauri/ws/replay)
  so a long silent phase still shows a pulse; an `engine-start` heartbeat lands within ~1s of spawn.
- **Ignite/Reignite feedback** ("igniting…" with a give-up ceiling — no more silent "nothing
  happened") and a **responsive Brake** (optimistic, symmetric stop/cancel that doesn't wait on the
  next log line).
- An honest **LIVE/REPLAY badge** from the mounted transport, a **"no $ metering"** note for
  subscription runs (cum stays $0), and a fresh-loop **empty-state** prompt.
- **CREATE/SAVE LOOP** disambiguated from **Ignite** (and you fly into the new System on create);
  a keyboard / screen-reader work-item list for the click-only canvas; user-created loops are now
  enterable, not just the static seeds.

## [0.1.0] — 2026-06-20

First public release: a cross-platform Python loop engine plus the Orrery visualizer.

### Added

**Engine (`engine/`)** — pip-installable Python package with `loop`, `loop-bmad`, `loop-stop`:
- Generic fix-until-green loop with an **external test gate as truth**, per-iteration git
  rollback, quota survival (wait-and-resume), crash recovery, and cooperative safe-stop.
- **Pluggable agent backends**: Claude Code (default, faithful), aider, codex.
- **BMAD multi-story driver** (create-story → dev → code-review → browser-smoke → PR →
  merge → retrospective), generalized off any hardcoded project path.
- **Research-backed, default-off capabilities**: held-out/hidden test split
  (anti-reward-hacking), compact first-failure feedback, cross-run lessons memory,
  mutation strength audit, run-quality metrics. Grounded in Reflexion, ExpeL, ACE, CoALA,
  Agentless, SWE-agent, Krakovna (specification gaming), and Just et al. (mutation testing).
- Event output **byte-compatible** with the Orrery wire protocol, verified by a golden
  corpus generated from the original PowerShell implementation.

**Orrery visualizer (`orrery/`)** — Tauri 2 + Svelte 5 + PixiJS desktop/web app:
- Multi-loop Cosmos, Observatory orbital scene, fixture replay with scrub/rewind, LAN reach,
  and a run-quality **metrics panel**.

**Quality & tooling**:
- Cross-language **reducer-parity** golden harness (Rust ↔ TypeScript), Playwright E2E,
  vitest, and a pytest suite for the engine.
- GitHub Actions CI matrix (Linux + Windows).
- Docs: README, CONTRIBUTING, SECURITY, CODE_OF_CONDUCT, `docs/capabilities.md`, and a
  runnable `examples/hello` loop.

### Notes

- **Alpha.** APIs, the wire protocol, and engine internals may change before 1.0.
- The original generic PowerShell engine (`loop.ps1`, `loopcore.ps1`, `selftest*.ps1`) remains
  in the repo as the reference implementation.

### Known limitations / roadmap

- Orrery's seeded `loops/*.json` still launch the PowerShell engine; repointing them to the
  Python engine (with portable relative paths) is pending.
- Cross-run lessons-memory is not yet surfaced in the visualizer (needs a new protocol event).
- No published benchmark yet (e.g. SWE-bench) — roadmap.

[0.1.0]: https://github.com/NDilanka/orrery/releases/tag/v0.1.0
