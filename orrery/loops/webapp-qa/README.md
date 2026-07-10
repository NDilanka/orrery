# webapp-qa — QA loop template

A **template** (not runnable as-is) showing how to point the `loop-qa` engine at your own
web app. The QA loop drives your running app in a headless browser, judges each acceptance
criterion from `ac-manifest.json` against real behavior, and authors Playwright specs into
your app's `specDir` for the ones that pass.

The example manifest describes a fictional todo-list app so you can see the expected shape.

## Easiest path: author it in the app

Orrery's Tuning Console has a **QA a web app** recipe (✦ new loop → *…or drive one of your
own repos*) that fills the essentials for you — project root, manifest path, app name, base
URL, storage state, seed summary — and can **✦ Create & start** in one click. This template
is the hand-authored equivalent, and the place to set the keys the recipe doesn't surface
(e.g. `qa.caps`, `qa.headless`, `qa.specDir`, per-phase `model`/`effort`).

## What to customize

- `loop.json` — the engine config lives inline under its `qa` block (the split
  `qa-engine.json` file still works but is deprecated; see PROTOCOL.md §7):
  - `--project-root` and `qa.storageState`: replace the `C:/path/to/...` placeholders with
    real absolute paths (your app repo; a Playwright storage-state file if your app needs auth).
  - `qa.app`, `qa.baseUrl`, `qa.specDir`: your app's name, dev-server URL, and the directory
    (inside your app repo) where generated Playwright specs should land.
  - `qa.seedSummary`: a plain-English description of the seeded test data the agent will find.
  - `qa.model` / `effort` / budgets (`maxTurns`, `timeoutSec`, `costCeilingUsd`): tune to taste.
  - `qa.headless` (default `true`) and `qa.caps` (browser capability profile, default
    `devtools`): only needed when the defaults don't fit your app.
- `ac-manifest.json`: replace with your real acceptance criteria — usually generated from
  your story/spec files with the bundled generator:

  ```bash
  python -m orrery_loop.qa.manifest --help
  ```

  Keep the same keys (`epics[].stories[].acMarkdown` holds the Given/When/Then text the
  agent judges against).

Start your app's dev server first; the loop only drives the browser, it does not boot the app.
