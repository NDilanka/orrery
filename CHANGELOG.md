# Changelog

All notable changes to Orrery are documented here. The format roughly follows
[Keep a Changelog](https://keepachangelog.com/). While pre-1.0, expect breaking
changes between minor versions.

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
