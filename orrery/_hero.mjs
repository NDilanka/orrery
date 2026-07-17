// Hero-GIF frame capture — BROWSER REPLAY mode (no Tauri, no engine, no quota).
// Usage: node _hero.mjs <outDir>   (vite dev must be serving localhost:1420)
import { chromium } from '@playwright/test';
import path from 'node:path';

const OUT = process.argv[2] || 'hero-frames';
const BASE = 'http://localhost:1420';

let n = 0;
async function frames(page, count, gapMs = 60) {
  for (let i = 0; i < count; i++) {
    await page.screenshot({ path: path.join(OUT, `f${String(n++).padStart(4, '0')}.png`) });
    await page.waitForTimeout(gapMs);
  }
  console.log(`frames: ${n}`);
}

const browser = await chromium.launch();
const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 }, deviceScaleFactor: 1 });
const page = await ctx.newPage();
page.on('pageerror', (e) => console.log('PAGEERR', e.message));

await page.goto(BASE);
await page.locator('.station .enter').first().waitFor({ timeout: 20000 });
await page.waitForTimeout(2500); // let the Pixi field settle + stations fade in

// Beat 1 — Cosmos: the orbital star field with the loop stations.
await frames(page, 14);

// Beat 2 — enter the bmad system; replay auto-plays (153 events ≈ 14 s at 1×),
// so keep this beat short enough to stay mid-run.
await page.locator('.station .enter', { hasText: 'bmad' }).click();
await page.locator('.hud').waitFor({ timeout: 20000 });
await page.waitForTimeout(600);
await frames(page, 30);

// Beat 3 — fly into the current item's Body dossier while the run is still hot.
const fly = page.locator('.nbtn.body');
await fly.waitFor({ timeout: 10000 });
await fly.click();
await page.waitForTimeout(900);
await frames(page, 16);
const back = page.locator('button.back', { hasText: 'system' });
if (await back.count()) await back.click().catch(() => {});
await page.waitForTimeout(700);

// Beat 4 — restart the replay so the finale is a fresh run in motion
// (loops cleanly back into the Cosmos opening).
const restart = page.locator('.transport [aria-label="restart"]');
if (await restart.count()) await restart.click().catch(() => {});
await page.waitForTimeout(500);
await frames(page, 26);

await browser.close();
console.log(`DONE total=${n}`);
