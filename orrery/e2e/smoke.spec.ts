import { test, expect, type Page, type ConsoleMessage } from '@playwright/test';

// ─────────────────────────────────────────────────────────────────────────────
// Orrery E2E — BROWSER REPLAY mode (no Tauri, no quota, no live engine).
//
// In a plain chromium against `vite dev`, `window.__TAURI__` is absent and there
// is no `?token`/`?ws` deep-link, so `createTransport()` (src/lib/transport)
// selects the ReplayTransport and the app animates a static fixture .jsonl. That
// makes every assertion deterministic.
//
// Selectors are read-only from the app source (we may NOT add test-ids — that
// file is owned by a parallel agent):
//   - Cosmos canvas .......... PixiJS <canvas> appended under `.cosmos .field`
//   - Cosmos→System nav ...... the DOM legend `<button class="chip">` carrying
//                              each loop id (Cosmos.svelte `.station .enter .lid`)
//   - HUD .................... `.hud` block with "cum spend" + a status pill
//                              (Hud.svelte) and a "<n> events" meta readout
//   - TransportBar ........... play/pause `[aria-label="play|pause"]`, restart
//                              `[aria-label="restart"]`, scrub
//                              `[aria-label="timeline scrub"]`, a `cursor/total`
//                              position readout, and 1×/4×/16× speed buttons
// ─────────────────────────────────────────────────────────────────────────────

/** Attach error/console collectors; returns getters the asserts can read. */
function collectErrors(page: Page) {
  const pageErrors: Error[] = [];
  const consoleErrors: string[] = [];
  page.on('pageerror', (err) => pageErrors.push(err));
  page.on('console', (msg: ConsoleMessage) => {
    if (msg.type() === 'error') consoleErrors.push(msg.text());
  });
  return { pageErrors, consoleErrors };
}

/** First on-screen canvas with a non-zero rendered box (a mounted Pixi scene). */
async function expectLiveCanvas(page: Page) {
  const canvas = page.locator('canvas').first();
  await expect(canvas).toBeVisible({ timeout: 20_000 });
  await expect
    .poll(
      async () => {
        const box = await canvas.boundingBox();
        return box ? Math.min(box.width, box.height) : 0;
      },
      { timeout: 20_000, message: 'canvas should acquire a non-zero size' },
    )
    .toBeGreaterThan(0);
}

test.describe('orrery — browser replay smoke', () => {
  test('1 · Cosmos loads at / with a live canvas and no uncaught errors', async ({ page }) => {
    const { pageErrors, consoleErrors } = collectErrors(page);

    await page.goto('/');
    await expectLiveCanvas(page); // Cosmos PixiJS field renders

    // the always-visible loop legend confirms the Cosmos store loaded fixtures
    await expect(page.locator('.station .enter').first()).toBeVisible({ timeout: 20_000 });

    expect(pageErrors, `uncaught page errors: ${pageErrors.map((e) => e.message).join(' | ')}`).toEqual(
      [],
    );
    expect(consoleErrors, `console.error during load: ${consoleErrors.join(' | ')}`).toEqual([]);
  });

  test('2 · enter a System → Observatory canvas + HUD render', async ({ page }) => {
    const { pageErrors } = collectErrors(page);

    await page.goto('/');
    await expectLiveCanvas(page);

    // Cosmos→System: click the legend chip for the seeded "demo" loop. The Pixi
    // glyph hit-areas aren't DOM-locatable, but Cosmos.svelte renders a parallel
    // DOM legend of <button.chip> whose `.lid` is the loop id — that's the
    // accessible affordance we drive.
    const demoChip = page.locator('.station .enter', { hasText: 'demo' });
    await expect(demoChip).toBeVisible({ timeout: 20_000 });
    await demoChip.click();

    // System view = the existing Observatory (its own Pixi canvas) + the HUD.
    await expectLiveCanvas(page); // Observatory canvas
    const hud = page.locator('.hud');
    await expect(hud).toBeVisible({ timeout: 20_000 });
    await expect(hud).toContainText('cum spend'); // HUD cost label
    await expect(hud).toContainText(/\$\d+\.\d{2}/); // a $ cum-spend figure
    await expect(hud.locator('.pill')).toBeVisible(); // the status pill

    expect(pageErrors, pageErrors.map((e) => e.message).join(' | ')).toEqual([]);
  });

  test('3 · TransportBar replay: events advance, pause settles', async ({ page }) => {
    const { pageErrors } = collectErrors(page);

    await page.goto('/');
    await expectLiveCanvas(page);

    // enter the "bmad" system: its fixture is large and animates fast (rateMs 90)
    // so the events readout visibly advances within the test window.
    const bmadChip = page.locator('.station .enter', { hasText: 'bmad' });
    await expect(bmadChip).toBeVisible({ timeout: 20_000 });
    await bmadChip.click();

    await expect(page.locator('.hud')).toBeVisible({ timeout: 20_000 });

    // TransportBar position readout: "<cursor>/<total>" (TransportBar `.pos`).
    const pos = page.locator('.transport .pos');
    await expect(pos).toBeVisible({ timeout: 20_000 });

    const cursorOf = async () => {
      const txt = (await pos.textContent()) ?? '';
      return Number(txt.split('/')[0]?.trim() ?? '0');
    };

    // replay auto-plays on mount → the cursor should advance from its start.
    const start = await cursorOf();
    await expect
      .poll(cursorOf, { timeout: 20_000, message: 'replay cursor should advance while playing' })
      .toBeGreaterThan(start);

    // pause → the cursor should settle (stop advancing). Toggle via the play/pause
    // control (aria-label flips between "play" and "pause").
    const playPause = page.locator('.transport .tbtn.play');
    await expect(playPause).toBeVisible();
    if ((await playPause.getAttribute('aria-label')) === 'pause') {
      await playPause.click(); // currently playing → pause it
    }
    await expect(playPause).toHaveAttribute('aria-label', 'play'); // now paused

    const settled = await cursorOf();
    await page.waitForTimeout(500); // brief settle window (not a timing assertion)
    expect(await cursorOf(), 'paused cursor should not keep advancing').toBe(settled);

    // scrub: drive the timeline range to the start; the readout reflects the seek.
    const scrub = page.locator('.transport input[aria-label="timeline scrub"]');
    await expect(scrub).toBeVisible();
    await scrub.fill('0');
    await expect
      .poll(cursorOf, { timeout: 5_000, message: 'scrub to 0 should reset the cursor readout' })
      .toBe(0);

    expect(pageErrors, pageErrors.map((e) => e.message).join(' | ')).toEqual([]);
  });

  test('4 · ? opens the keyboard HelpOverlay; Esc closes it', async ({ page }) => {
    const { pageErrors } = collectErrors(page);

    await page.goto('/');
    await expectLiveCanvas(page);
    // enter a System so we're in instrument context (shortcuts are guarded elsewhere)
    await page.locator('.station .enter').first().click();
    await expect(page.locator('.hud')).toBeVisible({ timeout: 20_000 });

    const help = page.locator('[role="dialog"]', { hasText: 'KEYBOARD' });
    await expect(help).toHaveCount(0); // closed by default
    await page.keyboard.press('Shift+Slash'); // "?"
    await expect(help).toBeVisible({ timeout: 5_000 });
    await page.keyboard.press('Escape');
    await expect(help).toHaveCount(0);

    expect(pageErrors, pageErrors.map((e) => e.message).join(' | ')).toEqual([]);
  });

  test('5 · Body view: in-card back returns to the System', async ({ page }) => {
    const { pageErrors } = collectErrors(page);

    await page.goto('/');
    await expectLiveCanvas(page);
    const bmad = page.locator('.station .enter', { hasText: 'bmad' });
    await expect(bmad).toBeVisible({ timeout: 20_000 });
    await bmad.click();
    await expect(page.locator('.hud')).toBeVisible({ timeout: 20_000 });

    // fly into the current item's Body dossier
    const fly = page.locator('.nbtn.body');
    await expect(fly).toBeVisible({ timeout: 20_000 });
    await fly.click();

    // the dossier's in-card back control returns to the System (HUD chrome returns)
    const back = page.locator('button.back', { hasText: 'system' });
    await expect(back).toBeVisible({ timeout: 10_000 });
    await back.click();
    await expect(page.locator('.hud')).toBeVisible({ timeout: 10_000 });
    await expect(back).toHaveCount(0);

    expect(pageErrors, pageErrors.map((e) => e.message).join(' | ')).toEqual([]);
  });
});
