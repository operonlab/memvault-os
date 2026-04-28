/**
 * Group 1 — App boots
 *
 * What we're testing (mutation-thinking lens):
 *   - If main.tsx never mounts the React tree, T1 fails (root stays empty).
 *   - If a top-level component throws on mount, T2 catches the pageerror.
 *   - If a fetch on initial render 500s and is unhandled, T2 catches the console error.
 *
 * We do *not* assert specific copy / class names — only structural invariants.
 */
import { test, expect } from '@playwright/test';
import { attachConsoleSpy, waitForAppShell } from './_fixtures';

test.describe('Group 1 — App boots', () => {
  test('home page loads and hydrates into #root', async ({ page }) => {
    const spy = attachConsoleSpy(page);
    const response = await page.goto('/');
    expect(response, 'navigation must produce a Response').not.toBeNull();
    expect(response!.status(), 'index.html should be served (200 or 304)').toBeLessThan(400);

    await waitForAppShell(page);

    // Invariant: <title> is set per index.html, and the root has rendered children.
    await expect(page).toHaveTitle(/Memvault/i);
    const rootChildCount = await page.locator('#root > *').count();
    expect(rootChildCount, '#root must contain at least one rendered child').toBeGreaterThan(0);

    // No uncaught pageerrors or hard console errors during initial mount.
    const real = spy.realErrors();
    expect(real, `unexpected console errors on first paint:\n${real.map((e) => e.text()).join('\n')}`).toHaveLength(0);
  });

  test('no console errors on initial load (filtered by KNOWN_BENIGN_CONSOLE_PATTERNS)', async ({ page }) => {
    const spy = attachConsoleSpy(page);
    await page.goto('/');
    await waitForAppShell(page);
    // Allow a settle window for any async hooks (react-query, zustand) to finish their first cycle.
    await page.waitForTimeout(1_000);

    const real = spy.realErrors();
    expect.soft(spy.warnings.length, 'warnings recorded for visibility (non-blocking)').toBeGreaterThanOrEqual(0);
    expect(real, `real console errors:\n${real.map((e) => e.text()).join('\n')}`).toHaveLength(0);
  });
});
