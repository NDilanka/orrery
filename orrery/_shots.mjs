import { chromium } from '@playwright/test';
import { fileURLToPath } from 'node:url';
import path from 'node:path';

const OUT = process.argv[2] || '.';
const BASE = 'http://localhost:1420';

async function settle(page, ms = 1800) { await page.waitForTimeout(ms); }

async function shot(page, name) {
  await page.screenshot({ path: path.join(OUT, name) });
  console.log('shot', name);
}

const browser = await chromium.launch();

// ── DESKTOP 1440×900 ─────────────────────────────────────────────────────────
{
  const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 }, deviceScaleFactor: 1 });
  const page = await ctx.newPage();
  page.on('pageerror', e => console.log('PAGEERR', e.message));

  await page.goto(BASE);
  await page.locator('.station .enter').first().waitFor({ timeout: 20000 });
  await settle(page, 2500);
  await shot(page, '01-cosmos-desktop.png');

  // ignite-new (Tuning Console)
  const fab = page.locator('.ignite-fab');
  if (await fab.count()) {
    await fab.click();
    await settle(page, 1200);
    await shot(page, '02-tuning-console.png');
    // close it
    await page.keyboard.press('Escape').catch(()=>{});
    await page.locator('button', { hasText: /close|cancel|✕|×/i }).first().click().catch(()=>{});
    await settle(page, 600);
  }

  // enter the bmad system (rich fixture)
  const bmad = page.locator('.station .enter', { hasText: 'bmad' });
  if (await bmad.count()) {
    await bmad.click();
  } else {
    await page.locator('.station .enter').first().click();
  }
  await page.locator('.hud').waitFor({ timeout: 20000 }).catch(()=>{});
  await settle(page, 3500);
  await shot(page, '03-system-observatory-early.png');

  // let replay advance further
  await settle(page, 6000);
  await shot(page, '04-system-observatory-mid.png');

  // fly into body
  const flyBtn = page.locator('.nbtn.body');
  if (await flyBtn.count()) {
    await flyBtn.click();
    await settle(page, 1500);
    await shot(page, '05-body-view.png');
    // back to system
    await page.locator('.crumb', { hasText: /system|bmad/i }).first().click().catch(()=>{});
    await settle(page, 800);
  }

  // help overlay (wave U1 Task 5 rewrite)
  await page.keyboard.press('?').catch(()=>{});
  await settle(page, 800);
  await shot(page, '05b-help-overlay.png');
  await page.keyboard.press('Escape').catch(()=>{});
  await settle(page, 400);

  // back to cosmos, then drive into the failed-dark fixture loop (wave U1 verify step)
  await page.locator('.crumb', { hasText: /cosmos/i }).first().click().catch(()=>{});
  await settle(page, 1200);
  const failedDark = page.locator('.station .enter', { hasText: 'failed-dark' });
  if (await failedDark.count()) {
    await failedDark.click();
    await page.locator('.hud').waitFor({ timeout: 20000 }).catch(()=>{});
    await settle(page, 4000);
    await shot(page, '08-system-failed-dark.png');
  } else {
    console.log('NOTE: failed-dark station not found in roster');
  }

  await ctx.close();
}

// ── MOBILE 390×844 (phone → Tier-1 / Planetarium default) ─────────────────────
{
  const ctx = await browser.newContext({ viewport: { width: 390, height: 844 }, deviceScaleFactor: 2, isMobile: true });
  const page = await ctx.newPage();
  await page.goto(BASE);
  await page.locator('.station .enter').first().waitFor({ timeout: 20000 }).catch(()=>{});
  await settle(page, 2500);
  await shot(page, '06-cosmos-mobile.png');
  const chip = page.locator('.station .enter', { hasText: 'bmad' });
  if (await chip.count()) await chip.click(); else await page.locator('.station .enter').first().click().catch(()=>{});
  await settle(page, 4000);
  await shot(page, '07-system-mobile.png');
  await ctx.close();
}

await browser.close();
console.log('DONE');
