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
  retries: 0,
  reporter: [['list']],

  use: {
    baseURL,
    trace: 'retain-on-failure',
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
