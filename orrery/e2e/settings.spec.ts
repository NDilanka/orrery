import { test, expect, type Page, type ConsoleMessage } from '@playwright/test';

// ─────────────────────────────────────────────────────────────────────────────
// Orrery E2E — Settings overlay, BROWSER REPLAY mode (no Tauri, no keychain).
//
// Same contract as smoke.spec.ts: a plain chromium hits `vite dev`, so
// `window.__TAURI__` is absent and the app runs the fixture-replay transport —
// fully deterministic. We therefore also exercise the app's NO-BACKEND paths:
// the Settings modal renders and its live controls (theme) apply, while
// keychain-backed BYOK affordances present their disabled/desktop-only state
// rather than pretending an OS keychain exists.
//
// Read-only selectors from the app source (no test-ids — that file is owned by
// a parallel agent):
//   - gear .................. `[aria-label="Open settings"]` (nav rail button)
//   - modal ................. `[role="dialog"]` carrying the "SETTINGS" title
//                             (SettingsOverlay `.floating-card`)
//   - close ................. `[aria-label="close settings"]`
//   - category rail ......... buttons "General" / "Appearance" / "AI / Models"
//                             (SettingsNav accessible labels)
//   - mode control .......... a `radiogroup` named "Mode" with "Light"/"Dark"/
//                             "System" radios (Segmented.svelte). ("Theme" now
//                             names the Classic/Cobalt skin picker.)
//   - resolved theme ........ `document.documentElement.dataset.theme`
//                             (settingsStore.applyTheme)
// ─────────────────────────────────────────────────────────────────────────────

/** Attach error collectors; returns getters the asserts can read. */
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

/** The Settings dialog, keyed by its "SETTINGS" title (mirrors smoke's HelpOverlay locator). */
function settingsDialog(page: Page) {
  return page.locator('[role="dialog"]', { hasText: 'SETTINGS' });
}

/** Load Cosmos and open the Settings modal via the ⚙ gear; returns the dialog locator. */
async function openSettings(page: Page) {
  await page.goto('/');
  await expectLiveCanvas(page);
  const gear = page.locator('[aria-label="Open settings"]');
  await expect(gear).toBeVisible({ timeout: 20_000 });
  await gear.click();
  const dialog = settingsDialog(page);
  await expect(dialog).toBeVisible({ timeout: 5_000 });
  return dialog;
}

/** The white-point token; flips between dark (≈white) and light (≈ink) themes. */
function readEmHi(page: Page) {
  return page.evaluate(() =>
    getComputedStyle(document.documentElement).getPropertyValue('--em-hi').trim(),
  );
}

// Each test drives a full Cosmos boot (goto + a live Pixi canvas) before touching
// Settings, so give them headroom above the 30s default — on a loaded single-worker
// run the warmup alone can approach it, and we assert real behavior, not timing.
test.describe.configure({ timeout: 60_000 });

test.describe('orrery — settings overlay (browser, no Tauri)', () => {
  test('1 · ⚙ gear opens the modal; the General panel renders a settings row', async ({ page }) => {
    const { pageErrors, consoleErrors } = collectErrors(page);

    const dialog = await openSettings(page);

    // General is the default category — its rows carry real setting labels.
    await expect(dialog.getByRole('button', { name: 'General' })).toHaveAttribute(
      'aria-current',
      'page',
    );
    await expect(dialog).toContainText('Confirm destructive actions'); // a General SettingRow

    expect(
      pageErrors,
      `uncaught page errors: ${pageErrors.map((e) => e.message).join(' | ')}`,
    ).toEqual([]);
    expect(consoleErrors, `console.error while open: ${consoleErrors.join(' | ')}`).toEqual([]);
  });

  test('2 · Appearance → Mode=Light applies to the document, then back to Dark', async ({
    page,
  }) => {
    const { pageErrors } = collectErrors(page);

    const dialog = await openSettings(page);
    // default resolved theme is dark (fresh context → schema defaults).
    await expect
      .poll(() => page.evaluate(() => document.documentElement.dataset.theme), { timeout: 5_000 })
      .toBe('dark');
    const darkWhite = await readEmHi(page);

    await dialog.getByRole('button', { name: 'Appearance' }).click();
    // the light/dark control is the "Mode" radiogroup ("Theme" now selects the
    // Classic/Cobalt skin — see appearance.skin in settings/schema.ts).
    const theme = dialog.getByRole('radiogroup', { name: 'Mode' });
    await expect(theme).toBeVisible({ timeout: 5_000 });

    // flip to Light: the resolved theme attribute AND the theme tokens actually change.
    await theme.getByRole('radio', { name: 'Light' }).click();
    await expect
      .poll(() => page.evaluate(() => document.documentElement.dataset.theme), {
        timeout: 5_000,
        message: 'theme should resolve to light',
      })
      .toBe('light');
    expect(await readEmHi(page), 'light theme should repaint the white-point token').not.toBe(
      darkWhite,
    );

    // restore Dark so no persisted state leaks (and to prove the toggle is reversible).
    await theme.getByRole('radio', { name: 'Dark' }).click();
    await expect
      .poll(() => page.evaluate(() => document.documentElement.dataset.theme), { timeout: 5_000 })
      .toBe('dark');

    expect(pageErrors, pageErrors.map((e) => e.message).join(' | ')).toEqual([]);
  });

  test('3 · keyboard: Esc closes the modal, Ctrl+, reopens it', async ({ page }) => {
    const { pageErrors } = collectErrors(page);

    const dialog = await openSettings(page);

    // Esc — handled by the dialog's focusTrap (initial focus is the search input).
    await page.keyboard.press('Escape');
    await expect(dialog).toHaveCount(0);

    // Ctrl/Cmd+, — the macOS-convention reopen chord (+page keydown owns it while closed).
    await page.keyboard.press('Control+,');
    await expect(settingsDialog(page)).toBeVisible({ timeout: 5_000 });

    expect(pageErrors, pageErrors.map((e) => e.message).join(' | ')).toEqual([]);
  });

  test('4 · AI / Models tab shows the desktop-only BYOK state (no keychain in-browser)', async ({
    page,
  }) => {
    const { pageErrors } = collectErrors(page);

    const dialog = await openSettings(page);
    await dialog.getByRole('button', { name: 'AI / Models' }).click();

    // fresh context → no instances → the empty state, not a fabricated credential list.
    await expect(dialog).toContainText('No AI providers yet');

    // open the add form; the default (Claude Code · Anthropic · Subscription) draft has no
    // keychain account, so it surfaces the sign-in path with a Test button that is DISABLED
    // in the browser — the app does not pretend an OS keychain / reachability probe exists.
    await dialog.getByRole('button', { name: 'Add a provider' }).click();
    const testBtn = dialog.getByRole('button', { name: 'Test connection' });
    await expect(testBtn).toBeVisible({ timeout: 5_000 });
    await expect(testBtn).toBeDisabled();
    await expect(testBtn).toHaveAttribute('title', 'Only available in the desktop app');

    expect(pageErrors, pageErrors.map((e) => e.message).join(' | ')).toEqual([]);
  });
});
