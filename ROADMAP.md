# Roadmap

Orrery is a best-effort personal project that's growing into a community one.
This roadmap is directional, not a promise — items move as reality intervenes.
Discuss anything here in [GitHub Discussions](https://github.com/NDilanka/orrery/discussions).

## Now (v0.3 → v0.4)

- **Prebuilt binaries** — GitHub releases with Windows/macOS/Linux installers on every
  tag, so nobody has to build from source. (`.github/workflows/release.yml`)
- **Engine on PyPI** — `pip install orrery-loop` for the Python engine alone
  (headless loops, no desktop app). Import package renamed to `orrery_loop` to be
  collision-proof; console-script names (`loop`, `loop-bmad`, `loop-qa`,
  `loop-supervise`, `loop-stop`) are unchanged.
- **In-app loop authoring** *(v0.4.0)* — the Tuning Console's recipe gallery creates
  generic, BMAD, and QA loops (external repos via `--cwd`) with one-click
  ✦ Create & start.
- **App settings** *(v0.4.0)* — settings popup with live persistence, a full light
  theme, notifications, and OS-keychain-backed multi-provider BYOK.

## Next

- **Community floor** — good-first-issues, branch protection, CODEOWNERS,
  Discussions as the front door.
- **Launch write-up** — a real overnight run recorded as a timelapse, plus a
  practice companion to `docs/loop-engineering.md` with real run data:
  stories completed, cost, verifier catch-rate.
- **macOS/Linux hardening** — `run-orrery.sh` is untested outside Windows;
  first-run experience on POSIX needs real-world passes.

## Later / exploring

- **More gallery recipes** — beyond the built-in BMAD and QA recipes: issue-triage
  loops, dependency-update loops, test-backfill loops.
- **Runner breadth** — the engine already abstracts runners (Claude Code, aider,
  codex); deepen the non-Claude paths as people use them.
- **Remote viewing polish** — the LAN control surface exists; make watching a
  loop from a phone genuinely pleasant.

## Non-goals (for now)

- A hosted/SaaS version. Orrery is local-first by design: your repo, your
  machine, your API budget.
- Windows-style auto-update. Grab a new release when you want one.
