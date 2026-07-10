# bmad — BMAD sprint loop template

A **template** (not runnable as-is) showing how to point the `loop-bmad` engine at a
[BMAD-method](https://github.com/bmad-code-org/BMAD-METHOD) project. The loop drives a
multi-story epic pipeline — create-story → dev → code-review → (smoke) → PR → merge →
retrospective — against your repo, story by story.

## Easiest path: author it in the app

Orrery's Tuning Console has a **Work a backlog (BMAD)** recipe (✦ new loop → *…or drive
one of your own repos*) that fills the essentials — project root, merge base, per-phase
models/effort — and can **✦ Create & start** in one click. This template is the
hand-authored equivalent, and the place to set keys the recipe doesn't surface (e.g.
`bmad.gateStages`, `bmad.devServerArgv` for non-bun repos, quota-wait and flaky-gate
tuning — the full surface is documented in [`../../../engine/README.md`](../../../engine/README.md)).

## What to customize

- `start.args`: replace the `C:/path/to/your-project` placeholder in `--project-root`
  with your BMAD repo's real absolute path, and set `--merge-base` to the branch stories
  merge into (`develop` here).
- `bmad.models` / `bmad.effort`: per-phase model tiers and reasoning effort. The
  `decider` runs many cheap judgment calls — keep it on a small tier.
- The engine config lives inline under the `bmad` block (the split `bmad-engine.json`
  form still works but is deprecated; see [`../../PROTOCOL.md`](../../PROTOCOL.md) §7).

Your project needs BMAD installed (story files under its BMAD layout) and the `claude`
CLI authenticated. A real sprint run spends real quota/API money — set your cost ceiling.
