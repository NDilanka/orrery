# Loop / Orrery Improvement Plan — 2026-07-02

Produced from a five-track deep audit (Python engine, Rust/Tauri backend, Svelte frontend,
code-level UX audit, loop-config surface) plus a live screenshot pass of all UI modes.
Direction decisions (owner): **personal tool first, OSS later** · **hybrid UX — ops clarity
first, cosmic identity kept as visual language**.

---

## Part 1 — Architecture: state of play

**What's genuinely good (preserve these):**
- Engine: pure-core/imperative-shell discipline with golden byte-parity tests vs the legacy
  PowerShell (`engine/tests/test_events_golden.py`); injected clocks/sleeps everywhere;
  `AgentRunner` ABC + registry is a real plugin seam (`engine/loop/runners/`).
- Rust: pure idempotent reducer, cross-language golden parity (`src-tauri/tests/golden_parity.rs`);
  no unwraps in production paths; STOP-brake overlay deliberately kept outside the pure reducer.
- Frontend: transport abstraction (tauri/ws/replay behind one interface) genuinely decouples the
  component tree; derived orbital geometry centralized in `run.svelte.ts`; design tokens +
  reduced-motion honored everywhere.

**The core structural weakness:** the "generalized loop platform" is one real abstraction
(runners) plus three parallel hand-wired orchestrations (`core.run_loop`, `bmad.driver.run`,
`qa.discover.run`) with three incompatible config schemas, no shared driver contract, and a
protocol doc (PROTOCOL.md) that no longer matches shipped code.

---

## Part 2 — Architecture plan (phased)

### Phase A0 — Stop the signals lying (small, high-leverage; do first)
1. `stop{ok:false}` → construct `RunStatus::Error` in `reducer.rs` (+ TS mirror + golden case).
   Today 16/17 real stops render as clean stops.
2. Add `token-usage` arm to both reducers (feeds cache hitRatio/warm + cost series); document in
   PROTOCOL.md §2. ~22% of the real bmad event stream is currently dropped.
3. Wire `Heartbeat` into the generic `core.run_loop` (mirror `ResilientRunner.run`,
   `driver.py:457-466`) — the flagship command has no liveness signal today.
4. CI: add `npm run test:unit` to the orrery job — the TS golden-parity test never runs in CI.
5. Persist raw agent stdout/stderr per iteration (`.loop/run-<iter>.out`) — currently nothing
   is written anywhere for post-hoc debugging.
6. Fix `composeLoopDef()` in `blueprints.ts:381-385`: it emits `start: pwsh -File loop.ps1`,
   which does not exist — every Tuning-Console-created loop fails to start. Emit the
   `loop --loop-json loop.json` form used by hello/brain2-regression; add a dry-run round-trip test.

### Phase A1 — Don't hang, don't lose work (reliability of overnight runs)
1. Thread a wall-clock timeout through `EngineConfig` and every BMAD phase `runner.run()` call —
   today `iter_timeout_sec = 0` is hardcoded (`core.py:394`) and a hung claude call stalls forever.
   Likely a root cause of historical "loop stopped on its own" incidents.
2. SIGINT/SIGTERM handling in the engine: any exit path tree-kills the spawned agent process tree
   (today only `TimeoutExpired` does; Ctrl+C orphans children).
3. Mutation audit must not mutate the real source file (`core.py:849-899`) — use a scratch copy.
4. One shared lock module + one lock filename (`core.py:84` uses `lock`, `driver.py:85` uses
   `bmad-lock`; the two drivers can't see each other racing the same repo).
5. Fix resume: `checkpoint.resume` must carry `--loop-json` for every adapter (root cause that
   forced `supervise.ps1` into existence). Then absorb the supervisor policy (restart-on-crash
   with thrash guard) into the engine or Orrery as a first-class, visible feature.
6. Serialize check-then-spawn in `control.rs::start_with_spec` with a per-loop mutex (TOCTOU
   double-spawn race, desktop double-click or LAN racing desktop).
7. Fix the guard-token fallback (`control.rs:488-493`) that reintroduces the bystander-match bug
   for loops without distinguishing start args.

### Phase A2 — One engine, many loops (the generalization debt)
1. Extract a `LoopDriver` contract (events emitted, STOP semantics, lock, heartbeat, checkpoint
   conventions) that core/bmad/qa all implement — today conformance is copy-paste convention.
2. BMAD adopts the shared `decide()` core for its regression/halt checks instead of 4+ inline
   reimplementations inside the 360-line `_process_story`.
3. One typed config loader: camel/snake resolution in one place, warn on unknown keys (typos are
   silently swallowed today), and `main_bmad` stops discarding parsed fields (`cli.py:243-254`).
4. Collapse the three config schemas: one `loop.json` with adapter-namespaced blocks
   (`engine`, `bmad`, `qa`), documented in PROTOCOL.md §7.
5. Delete or wire dead config: `gate.greenWhen` (parsed, documented, never consulted) and the
   blueprint dial fields (`regression`, `decide`, `qa`, `concurrency`) that `EngineConfig`
   doesn't have — the UI must only show knobs the engine actually consumes.
6. PROTOCOL.md refresh: A5 CRUD commands, `token-usage`, `qa-ac`, QA artifact files
   (`report.md`, `findings/*.json`), and the "unknown events are dropped" rule.

### Phase A3 — Watcher, perf, LAN hardening
1. Watcher lifecycle: stop leaking one notify-watcher + thread per `watch_run` remount
   (`control.rs:210-213`); key by state dir, tear down on channel drop.
2. Incremental reduction: keep a live `Reducer` in the watcher thread and feed only new lines —
   today every tail batch re-reduces the whole log (O(log size) per tick, multi-day runs).
3. Tailer rotation detection beyond shrink-only (`tailer.rs:48-52`).
4. LAN: require a token for `/ws` observe (or at minimum Origin checks), never fall back to
   binding `0.0.0.0` (`lan.rs:188-192`). Personal-tool priority: medium — but it's your real
   run data on any Wi-Fi you join.
5. Frontend: single source for `DEFAULT_LOOPS_DIR` (hardcoded `D:/dev/...` in 3 files); extract
   transport lifecycle from `+page.svelte` into a session store; dedupe focus-trap/Q&A-flow/
   color-map copies; remove unused `@xyflow/svelte`.

### Phase A4 — OSS readiness (deferred until you want it)
Relative/portable loop paths, POSIX process-group detachment, shell-dialect caveats for gate
commands, third-party adapter guide, `RunState` schema made adapter-neutral (today it is BMAD's
shape with optional fields).

---

## Part 3 — UX: state of play

**Concept as built:** three-altitude zoom (Cosmos → System/Observatory → Body) with three
System modes (Observatory / Planetarium / Rewind), rendered via a full astronomy metaphor plus
two more metaphor systems layered on top (a lighthouse auditor, a six-gear clockwork phase
machine), ~15 invented terms, and a help overlay that explains none of them.

**Keep (verdict from audit + screenshots):** the three-altitude zoom; the four rest-state
glyphs (triple-coded shape+motion+color — a real accessibility win); the cost-horizon ring;
the Rewind event-timeline scrubber; the honest LIVE/REPLAY/LAN badge; staged ignite feedback;
the Planetarium *concept* (ambient threshold-based display); MetricsPanel's metric vocabulary;
the cost/quota strip and $/min burn readout; `needsYou` urgency sorting.

**Cut or demote:** the lighthouse (85 lines of beam geometry for a binary badge, invisible on
phone), the Mechanism gear cluster (restates HUD text, costs a 190px layout slot), the particle
stream (pure ambiance), dusk/polar-night as *vocabulary* (keep the visual wash, name it "quota
pause").

---

## Part 4 — UX plan (phased)

### Phase U0 — Operational truth (pairs with A0; the UI must stop lying)
1. Add an `error` rest-state: distinct silhouette + "FAILED" pill; crashed ≠ idle. Recovery
   button must say "Resume from checkpoint" vs "Restart fresh" — never just "Ignite".
2. Add **Stop now** to RunControlBar (transport already implements `stop:now`; nothing calls it).
   A runaway loop is currently unkillable from the app.
3. Cosmos auto-refresh (it's a frozen snapshot today — the one screen meant for overnight
   glancing), and reconcile cosmosStore with runStore for the mounted loop.
4. MetricsPanel: rolling in-run updates (emit periodic `metrics` from the engine), not
   once-at-stop — a thrashing run must look different from a healthy one *during* the night.
5. Desktop staleness signal (Tauri channel has no freshness concept; a stuck watcher is
   invisible on desktop today).

### Phase U1 — Language & legibility (the hybrid re-skin)
1. Plain-language labels everywhere, metaphor demoted to flavor: "BANKED EMBER" → "Paused —
   resumable", "CERTIFIED DONE" → "Done · verified", "claimed green" → "agent claims pass —
   unverified", "GATE (airlock)" → "Test gate", "Ignite/Reignite" → "Start/Resume" (keep ✦ as
   iconography). One metaphor system: astronomy visuals only; retire lighthouse/gears/ember/
   frost *as words*.
2. Promote the claimed-vs-certified trust signal to a first-class badge at every altitude
   (today: a dashed vs solid ring on a 5-9px dot; absent from Cosmos entirely).
3. Label the orbit bodies; add timestamps/durations ("started 23:40 · running 2h 12m") to HUD
   and Cosmos cards; un-truncate log lines (wrap or expand-on-hover); fix "1 rings".
4. Help overlay becomes a real reference: modes, statuses, trust states, controls — not just
   6 keybindings.
5. Cosmos information density: cards gain last-event time, health, claimed/verified chip;
   the field stops being 4 dots in a void.

### Phase U2 — Layout system (kill the floating-panel stack)
1. Replace 9 hand-`calc()`-stacked floating panels with a CSS grid dock (left rail: HUD+log;
   right rail: quality/verdict/QA; bottom: controls+scrubber+cost strip; center: canvas).
   Today five files hand-track each other's pixel heights across three breakpoints.
2. Rationalize responsive tiers; fix the phone bug where "Observatory" mode still renders
   Planetarium content plus desktop panels (`ui.svelte.ts:39` tierOne coupling).
3. BodyView becomes a proper right-side drawer at desktop (its content is already the best
   in the app), not a card floating in darkness.

### Phase U3 — Creation & onboarding
1. Fix the broken create path (A0.6), scaffold `TASK.md` from the console, and add a
   **"probe gate"** button that runs the gate command once before Ignite (a bad command is
   currently discovered on the first paid iteration).
2. Dials only move real config (after A2.5); keep the slider→concrete-caption pattern
   ("sonnet · ceiling $7.72 · 16 iters") — it's the best thing in that dialog.
3. Empty state walks through: create → task → gate → dry-run → start.

### Phase U4 — Remote & ambient
1. Share-to-phone: show the LAN URL + token as QR from the desktop app (today the WS mode is
   unreachable without hand-building a URL).
2. Phone Planetarium as the flagship glance view: rest-state glyph, cost, quota countdown,
   needs-decision — plus the U0 error state.
3. Optional: OS notifications on handoff-beacon / error / quota-night.

---

## Sequencing recommendation

A0+U0 together (one wave, ~all quick wins, mostly independent small diffs) → A1 (overnight
reliability) → U1+U2 (the visible redesign) → A2 (generalization debt) → U3 → A3 → U4 → A4.

Rationale: A0/U0 items are cheap and each removes a way the system actively misleads you;
A1 protects the runs you already depend on; the big UX waves then land on foundations that
tell the truth.
