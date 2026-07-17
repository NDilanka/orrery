# Changelog

All notable changes to Orrery are documented here. The format roughly follows
[Keep a Changelog](https://keepachangelog.com/). While pre-1.0, expect breaking
changes between minor versions.

## [0.5.0] — 2026-07-17

A hardening release: a five-perspective code review (engine, frontend, Rust, cross-cutting
adversarial, docs/CI) followed by fixes for everything it surfaced.

### Fixed — guardrail integrity (engine)
- **Hash-lock covers every configured `lockGlobs` pattern.** Previously only the *first*
  glob was hash-locked — extra patterns were silently unprotected, so an agent could
  rewrite e.g. `*.spec.ts` tests undetected in a two-glob config.
- **A judge returning a string `failingCriteria`** (instead of a list) no longer refutes a
  green verdict character-by-character; it's coerced to a single criterion.
- **Quota survival can't spin forever on non-probing runners** (aider/codex): reactive
  rate-limit waits are now capped (4 consecutive blind waits), then surface as a real
  failure instead of an unbounded sleep-retry loop that bypassed `maxIters` and ceilings.
- **`checkpoint.json` and text-file writes are atomic** (same-dir temp + `os.replace`),
  so a hard kill mid-write can't corrupt resume state.
- **The state-dir lockfile is acquired atomically** (`O_CREAT|O_EXCL`), closing the race
  where two simultaneous starts both "won" the lock and ran concurrently.
- **A transient `gh` failure right after a BMAD merge** no longer crashes the driver
  mid-state-machine; post-merge PR-state lookups degrade to the handoff path. All `gh`
  calls now carry a finite timeout (120 s).
- **A non-numeric `ceilingUsd`/`maxIters`/… in `loop.json`** warns and keeps the default
  instead of aborting the load with a traceback.

### Fixed — desktop app (Rust)
- **LAN observe actually works for seeded loops:** the `/ws` path resolved a relative
  `stateDir` against the app's cwd instead of `loops/<id>/`, so phones tailing any seed
  loop saw an empty run forever.
- **Finished loop processes are reaped** — no more zombie accumulation over a long
  desktop session (one per completed run).
- **The log tailer is UTF-8-split-safe:** a multibyte character flushed across two reads
  no longer corrupts (and silently drops) that JSONL event line.
- **`start.program` allowlist:** loop definitions can only spawn the engine commands
  (`loop`, `loop-bmad`, `loop-qa`, `loop-supervise`, `loop-stop`, `python`), enforced at
  authoring and at spawn — defense-in-depth against a hostile `loop.json` landing in
  your loops directory.
- **LAN token moved out of the WebSocket URL** into `Sec-WebSocket-Protocol` (query
  param still accepted, deprecated) so it can't leak via access logs or history.
- **A restrictive CSP** replaces `csp: null` in `tauri.conf.json`.
- Blocking log reads and thread joins moved off the LAN server's async workers;
  fractional-timestamp ISO formatting now truncates like JS (`reduce.ts` parity).

### Fixed — app UI
- Switching loops quickly can no longer flash the previous loop's state/log into the new
  System (transport callbacks are epoch-guarded).
- A repeat verifier refutation of the same story re-surfaces the Verdict panel.
- Settings: the scope switcher is keyboard-navigable (radiogroup arrow keys), and an
  external settings change no longer gets clobbered by a focused field's later blur.
- Command palette: keyboard selection survives live run-state ticks, and run-control
  failures surface as a toast instead of vanishing.
- Tuning Console / blueprints: a gate stage must have a *command* (a name alone no
  longer authors a loop whose gate runs nothing).
- Observatory: per-story tracking maps are pruned with the planet pool (slow leak);
  label anchor hysteresis is a pure derived + effect; unmount-during-init can't throw.
- Alert stack: collapse state resets when the overflow clears; the chime reuses one
  AudioContext.

### Added
- **Golden-corpus case 10 (`guardrails`)**: generic `gate`, `handoff`, `plateau` and
  `parse_error` events are now pinned by the Rust⇆TS reducer parity suite (they occurred
  zero times across the previous nine cases).
- **CI runs the Playwright e2e suite** (browser replay) alongside engine, unit and Rust
  jobs; `PW_CHROMIUM_PATH` lets sandboxed environments point at a system Chromium.
- PROTOCOL §3 documents the `metrics` RunState field (it was emitted by both reducers
  but missing from the canonical interface).

### Changed
- README states the *default* guardrail posture honestly: held-out verify + mutation
  audit are opt-in; out of the box you get exit-code gate + test-file hash-locks +
  count-can't-drop (+ `gate.lockInfra` opt-in for runner configs).
- `engine/pyproject.toml` reads its version from `orrery_loop.__version__` (hatchling
  dynamic) — the two can no longer drift.
- Bare `npx vitest run` no longer tries to collect the Playwright e2e specs.
- Dropped unused dependencies: `@tauri-apps/plugin-opener` (npm), `regex` (Cargo).

## [0.4.0] — 2026-07-10

### Added — engine hardening from a dogfood run
- **`gate.lockInfra`** (opt-in): extends the test hash-lock over test *infrastructure*
  (`conftest.py`, `vitest.config`, etc.) so the suite can't be neutered from outside the
  test files.
- **`stop.tokenCeiling`** (opt-in): a token-budget stop for subscription runs where the
  dollar ceiling doesn't bind.
- The anti-false-green **verifier now sees the gate's real result** alongside the diff,
  so it can't be talked into a pass the gate contradicts.
- **Config validation** covers every engine sub-block — unknown keys warn instead of
  being silently ignored.
- `python -m orrery_loop` propagates the loop's real exit code (parity with the `loop`
  console script).

### Added — create loops without hand-writing loop.json
- **Recipe gallery**: ✦ new loop opens a redesigned Tuning Console — pick from four
  generic blueprints (Fix until green, Build + verify, Explore with me, Blank) or two
  external recipes that drive one of your own repos: **Work a backlog (BMAD)**
  (`loop-bmad`) and **QA a web app** (`loop-qa`). BMAD and QA loops no longer need
  hand-authoring.
- **Run anywhere**: a generic loop takes an optional *where it runs* directory and emits
  `--cwd <path>`, so it can drive any repo on disk (blank = its own `loops/<id>/`).
- **✦ Create & start**: one click creates the loop, flies into its System, and ignites
  it (create-only remains the default; failures surface in the control bar).

### Added — app settings (Orrery's first)
- **Settings popup**: a gear / `Ctrl+,` / command-palette-reachable modal with six
  tabs (General, Appearance, Loops & Defaults, Notifications, AI / Models, About &
  Diagnostics), live search, a user ⇆ workspace scope switch, per-key / per-section /
  reset-all, and import / export.
- **Persistence**: settings live in `settings.json` via `tauri-plugin-store`, with a
  `localStorage` fallback for the browser dev/replay build and a pre-paint FOUC script
  (`app.html`) that applies theme/density before first render.
- **Full light theme**: a complete light palette across both chrome and the PixiJS
  canvas scene (`data-theme`), with light-mode glow polish (`screen`→`multiply`); dark
  stays the default and is byte-identical to before.
- **Multi-provider BYOK**: an AI / Models editor over a provider-instance model
  (runner × provider × auth-mode), backed by OS-keychain secrets (write-only). A
  `PROVIDER_MATRIX` mirrored in TS + Rust gates unsupported combinations and drives
  env injection / scrubbing at spawn (Vertex `ANTHROPIC_VERTEX_PROJECT_ID`, a fallback
  instance, and a Test-connection reachability probe); subscription mode injects nothing.
- **Loop defaults are live**: new loops seed from your Loops & Defaults (a projection
  layer); the Tuning Console's override dots now mean "differs from *your* defaults", and
  `--runner` / `--model` / `--effort` are emitted into `start.args` (a non-Claude BYOK
  instance supplies the model id).
- **Runtime wiring**: loops directory (`resolve_loops_dir` at boot) and LAN companion
  port are read from settings; `appearance.motion` / `appearance.density` project onto
  `data-motion` / `data-density` (compact spacing; a user 'full' defeats OS reduce).
- **Notifications**: an `alertOn` filter over done / stopped alert kinds, an optional
  WebAudio chime, and a quota-resume toast — all settings-gated.
- **Safety**: `confirmDestructive` now gates both Restart-fresh and a new roster ✕
  delete-loop (`cosmosStore.deleteLoop` → Rust `delete_loop`). Deleting a loop that is
  currently RUNNING stops its engine process tree first, so the confirmed delete can't
  leave an orphaned run or a half-removed directory.

## [0.3.0] — 2026-07-05

### Added — you no longer build from source
- **Prebuilt binaries**: pushing a `v*` tag builds Tauri bundles for Windows
  (`.msi` + `-setup.exe`), macOS (`.dmg`, arm64 + x64), and Linux
  (`.AppImage` / `.deb` / `.rpm`) and attaches them to a draft GitHub release
  (`.github/workflows/release.yml`). macOS builds are unsigned — right-click →
  Open, or `xattr -cr /Applications/orrery.app`.
- **Engine on PyPI**: `pip install orrery-loop` installs the engine standalone
  (headless loops, no desktop app). Published via OIDC trusted publishing on
  `engine-v*` tags (`.github/workflows/publish-pypi.yml`) — no stored token.

### Changed — Python import package renamed `loop` → `orrery_loop`
- Collision-proofs the PyPI distribution; ~200 imports, module-string refs
  (monkeypatch targets, entry points), and docs rewritten. **Console-script
  names are unchanged** (`loop`, `loop-stop`, `loop-bmad`, `loop-qa`,
  `loop-supervise`) — existing seeds and orrery's engine resolution keep
  working with zero changes. Filenames like `loop.json` are untouched.

### Added — community floor
- `ROADMAP.md`, `.github/CODEOWNERS`, GitHub Discussions enabled, a
  support-posture note in CONTRIBUTING (best-effort personal project), branch
  protection on `main` (CI required, linear history), and a first batch of
  `good first issue`s (#10–#17).

## [0.2.0] — 2026-07-03

### Changed — open-source restructure
- Repo reshaped for public use: legacy PS demo moved under `legacy/demo/`, internal
  working docs dropped, design essay now at `docs/loop-engineering.md`.
- Private seeded loops replaced by a generic `webapp-qa` template; the `bmad` seed and
  all tests/comments scrubbed of project-specific paths.
- Portability: orrery's loops-dir default is now resolved at build time (override with
  `VITE_LOOPS_DIR`); the BMAD smoke-phase prompt derives dev/test/lint commands from the
  configured gate stages instead of hardcoding bun.
- New README (hero GIF + screenshots in `docs/assets/`, zero-cost replay quickstart,
  architecture diagram) and GitHub issue/PR templates.

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

### Added — the new quality telemetry is visible in Orrery
- Both reducers (Rust + TS, new cross-language golden case) carry the four new engine events:
  **verify** verdicts join the trust chips in VerdictPanel (REFUTED = red alert),
  **test-integrity** tamper is a red alert with the deleted-file list, a blocked
  **plan-check** is amber with its reason, and MetricsPanel renders whichever **metrics**
  flavor arrives (BMAD or generic). Documented in PROTOCOL.md; all additive
  (omit-when-absent), older reducers keep dropping them safely.
- The quota probe is **lean**: `--strict-mcp-config` skips loading every MCP server on each
  wait cycle (same rate-limit verdict, cheaper outage).

### Added — opt-in CLI capability adoption (all default-off; verified vs claude 2.1.199)
- **`fallbackModel`** (engine / bmad / qa blocks): threads `--fallback-model` (comma chain)
  through every runner path for overload resilience.
- **`bmad.structuredVerdicts`**: the verify / plan-gate calls request a `--json-schema`
  verdict; a valid `structured_output` wins, the text-line parse remains the fallback.
- **Experimental `engine.sessionGate`** (`stop-hook` | `goal`): one invocation loops
  internally until the gate is green — stop-hook mode installs a Stop hook that re-runs the
  gate and blocks turn-end on red (`|| exit 2`); the external gate remains the arbiter.
  `goal` mode is best-effort (`/goal` absent from the installed CLI).

### Changed — shared resilience & one gate-verdict path (architecture)
- **The QA discovery pass now survives quota limits**: `default_invoke` routes through the
  shared `ClaudeRunner` + `ResilientRunner` (new `loop/resilient.py`, re-exported from
  `bmad.driver`), inheriting quota survive-and-wait, probe-on-any-error, finite timeouts,
  raw-output capture, liveness heartbeat, and token telemetry. Previously an overnight QA run
  died on the first rate limit.
- The review/smoke gate-halt logic exists once (`_gate_checkpoint` → `decide.floor_breach`);
  halt reasons byte-identical.
- The **smoke no-progress guard is back to PS semantics**: progress = the git signature
  (HEAD + porcelain) changed, not verdict-text equality — a smoke agent that claims a fix but
  touches nothing stops immediately; different failure text with no code change no longer spins.

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

[0.4.0]: https://github.com/NDilanka/orrery/releases/tag/v0.4.0
[0.3.0]: https://github.com/NDilanka/orrery/releases/tag/v0.3.0
[0.2.0]: https://github.com/NDilanka/orrery/releases/tag/v0.2.0
[0.1.0]: https://github.com/NDilanka/orrery/releases/tag/v0.1.0
