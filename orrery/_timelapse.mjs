// Overnight timelapse recorder — screenshots a LIVE loop via the LAN share server.
//
// Prep (once, in the desktop app):
//   1. `npm run build` in orrery/ — the LAN server serves the built SPA from build/.
//   2. Launch the desktop app and start Sharing (Share popover) — copy the share URL,
//      it looks like http://<lan-ip>:8787/?token=<hex>&ws=1
//   3. node _timelapse.mjs <outDir> <shareUrl> [loopId] [intervalSec]
//
// Frames land as <outDir>/YYYYMMDD-HHMMSS.png. Assemble afterwards, e.g.:
//   ffmpeg -framerate 12 -pattern_type glob -i '<outDir>/*.png' \
//     -vf scale=1440:-2 -c:v libx264 -pix_fmt yuv420p timelapse.mp4
//
// The share token lives only in the running app session — if the app restarts
// overnight the URL dies; the script detects that, logs loudly, and keeps
// retrying so a late-night app restart still resumes capture.

import { chromium } from '@playwright/test';
import path from 'node:path';
import fs from 'node:fs';

const OUT = process.argv[2];
const SHARE_URL = process.argv[3];
const LOOP_ID = process.argv[4] || '';
const INTERVAL_S = Number(process.argv[5] || 90);

if (!OUT || !SHARE_URL) {
  console.error('usage: node _timelapse.mjs <outDir> <shareUrl> [loopId] [intervalSec]');
  process.exit(1);
}
fs.mkdirSync(OUT, { recursive: true });
const origin = new URL(SHARE_URL).origin;

function stamp() {
  const d = new Date();
  const p = (n, w = 2) => String(n).padStart(w, '0');
  return `${d.getFullYear()}${p(d.getMonth() + 1)}${p(d.getDate())}-${p(d.getHours())}${p(d.getMinutes())}${p(d.getSeconds())}`;
}

async function health() {
  try {
    const r = await fetch(`${origin}/api/health`);
    return r.ok;
  } catch {
    return false;
  }
}

// Navigate to the share URL and fly into the target system. Returns true when
// the HUD is up and the transport is genuinely live (ws), not fixture replay —
// an overnight recording of replay data would be worse than no recording.
async function mount(page) {
  await page.goto(SHARE_URL, { timeout: 30000 });
  await page.locator('.station .enter').first().waitFor({ timeout: 30000 });
  const target = LOOP_ID
    ? page.locator('.station .enter', { hasText: LOOP_ID })
    : page.locator('.station .enter');
  if (!(await target.count())) throw new Error(`loop station "${LOOP_ID}" not in roster`);
  await target.first().click();
  await page.locator('.hud').waitFor({ timeout: 30000 });
  await page.waitForTimeout(3000);
  const badge = (await page.locator('.hud').textContent().catch(() => '')) || '';
  if (/replay/i.test(badge) && !/live/i.test(badge)) {
    throw new Error('transport badge says REPLAY — not recording fixtures; check ?token/ws=1');
  }
  return true;
}

const browser = await chromium.launch();
const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 }, deviceScaleFactor: 1 });
const page = await ctx.newPage();
page.on('pageerror', (e) => console.log('PAGEERR', e.message));

if (!(await health())) console.error(`WARN: ${origin}/api/health unreachable — is sharing started?`);
await mount(page);
console.log(`recording ${SHARE_URL} → ${OUT} every ${INTERVAL_S}s (ctrl-c to stop)`);

let failures = 0;
for (;;) {
  try {
    await page.screenshot({ path: path.join(OUT, `${stamp()}.png`) });
    failures = 0;
  } catch (e) {
    failures++;
    console.error(`SHOT FAILED (${failures}): ${e.message}`);
    if (failures >= 3) {
      // Page likely dead (app restarted, share session gone). Probe + remount
      // until it comes back rather than dying overnight.
      console.error('remounting…');
      while (!(await health())) {
        console.error(`share server down — retrying in ${INTERVAL_S}s`);
        await new Promise((r) => setTimeout(r, INTERVAL_S * 1000));
      }
      try {
        await mount(page);
        failures = 0;
        console.error('remounted OK');
      } catch (e2) {
        console.error(`remount failed: ${e2.message}`);
      }
    }
  }
  await new Promise((r) => setTimeout(r, INTERVAL_S * 1000));
}
