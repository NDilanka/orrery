import { defineConfig, devices } from '@playwright/test';

// E2E in BROWSER REPLAY mode: a plain chromium hits `vite dev`, where the app
// has no `window.__TAURI__` and no `?token`/`?ws` deep-link, so the transport
// falls back to FIXTURE REPLAY (static/fixtures/*.jsonl). No Tauri, no quota,
// no live engine — fully deterministic.
//
// NOTE: vite.config.js pins the dev server to port 1420 (strictPort), NOT the
// SvelteKit default 5173, so baseURL + webServer.url target 1420.
const PORT = 1420;
const baseURL = `http://localhost:${PORT}`;

export default defineConfig({
  testDir: 'e2e',
  // keep CI honest but don't fail a local run for a stray .only
  forbidOnly: false,
  fullyParallel: false,
  // One worker: all specs share a single reused `vite dev` server driving a
  // WebGL/Pixi-heavy scene, and the timing-sensitive replay specs (cursor
  // advance/settle) flake under the contention of parallel workers hammering
  // that one server. The suite is small and fully serial by design anyway.
  workers: 1,
  retries: 0,
  reporter: [['list']],

  use: {
    baseURL,
    trace: 'retain-on-failure',
    // Sandboxed/CI containers often ship a system Chromium instead of the
    // exact browser build this @playwright/test version would download.
    // Point PW_CHROMIUM_PATH at it to skip `npx playwright install`.
    ...(process.env.PW_CHROMIUM_PATH
      ? { launchOptions: { executablePath: process.env.PW_CHROMIUM_PATH } }
      : {}),
  },

  // lean: chromium only
  projects: [{ name: 'chromium', use: { ...devices['Desktop Chrome'] } }],

  // boot `vite dev` (fixture-replay mode) and reuse one if already running
  webServer: {
    command: 'npm run dev',
    url: baseURL,
    reuseExistingServer: true,
    timeout: 120_000,
  },
});
