# webapp-qa — QA loop template

A **template** (not runnable as-is) showing how to point the `loop-qa` engine at your own
web app. The QA loop drives your running app in a headless browser, judges each acceptance
criterion from `ac-manifest.json` against real behavior, and authors Playwright specs into
your app's `specDir` for the ones that pass.

The example manifest describes a fictional todo-list app so you can see the expected shape.

## What to customize

- `loop.json` / `qa-engine.json`
  - `--project-root` and `qa.storageState`: replace the `C:/path/to/...` placeholders with
    real absolute paths (your app repo; a Playwright storage-state file if your app needs auth).
  - `qa.app`, `qa.baseUrl`, `qa.specDir`: your app's name, dev-server URL, and the directory
    (inside your app repo) where generated Playwright specs should land.
  - `qa.seedSummary`: a plain-English description of the seeded test data the agent will find.
  - `qa.model` / `effort` / budgets (`maxTurns`, `timeoutSec`, `costCeilingUsd`): tune to taste.
- `ac-manifest.json`: replace with your real acceptance criteria — usually generated from your
  story/spec files. Keep the same keys (`epics[].stories[].acMarkdown` holds the Given/When/Then
  text the agent judges against).

Start your app's dev server first; the loop only drives the browser, it does not boot the app.
