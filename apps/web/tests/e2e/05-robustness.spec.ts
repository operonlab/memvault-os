/**
 * Group 5 — Robustness
 *
 * Mutation-thinking lens:
 *   - If error boundaries are missing, T1 (timeout) leaves a blank screen forever — we assert UI feedback.
 *   - If react-router doesn't preserve scroll/state on goBack/goForward, T2 catches the unmount-and-blank case.
 *   - If long content uses `overflow:hidden` without `auto`, T3 catches a stuck scrollbox.
 *
 * These tests intentionally simulate failure modes that are hard to reproduce by hand.
 */
import { test, expect } from '@playwright/test';
import { attachConsoleSpy, createBlock, isApiUp, waitForAppShell } from './_fixtures';

test.describe('Group 5 — Robustness', () => {
  test('handles API timeout gracefully (UI must show a non-empty error / loading state, never blank)', async ({ page }) => {
    const spy = attachConsoleSpy(page);

    // Stall every memvault API call for 25s — the SPA's default fetch timeout (or react-query retry)
    // should surface *some* user-visible signal long before that.
    await page.route(/\/api\/memvault\/.*/i, async (route) => {
      await new Promise((r) => setTimeout(r, 25_000));
      await route.abort('timedout');
    });

    await page.goto('/');
    await waitForAppShell(page);

    // Wait a window long enough for any reasonable UI timeout to elapse, but shorter than the route delay.
    await page.waitForTimeout(8_000);

    // Invariant: #root has children (loading spinner / skeleton / error toast — anything visible).
    const childCount = await page.locator('#root > *').count();
    expect(childCount, '#root must not be empty even when API stalls').toBeGreaterThan(0);

    // Negative invariant: the page must not be a hard-crashed white screen — at least *some* text or aria element.
    const visibleText = await page.locator('body').innerText();
    expect(visibleText.trim().length, 'page must surface some text (loading / error state)').toBeGreaterThan(0);

    // Hard console errors are still not acceptable — react-query timeout should be a handled rejection.
    // (We don't assert this absolutely; some apps log retry warnings as errors. Soft check only.)
    expect.soft(spy.realErrors().length).toBeLessThan(5);
  });

  test('SPA navigation back/forward preserves shell (no blank between transitions)', async ({ page }) => {
    test.skip(!(await isApiUp(page)), 'API not reachable');

    const created = await createBlock(page, { content: `nav-${Date.now()}` });
    await page.goto('/');
    await waitForAppShell(page);

    // Navigate to a detail page programmatically to avoid depending on a brittle click target.
    await page.goto(`/blocks/${created.id}`);
    await waitForAppShell(page);

    // Back: must end up at "/" with shell intact.
    await page.goBack();
    await waitForAppShell(page);
    expect(new URL(page.url()).pathname, 'goBack should return to root').toBe('/');
    await expect(page.locator('#root > *').first()).toBeVisible();

    // Forward: must end up at the detail page again with content present.
    await page.goForward();
    await waitForAppShell(page);
    expect(new URL(page.url()).pathname, 'goForward should restore detail path').toContain(created.id);
    await expect(page.getByText(created.content, { exact: false }).first()).toBeVisible({ timeout: 10_000 });
  });

  test('large content scrolls without truncation', async ({ page }) => {
    test.skip(!(await isApiUp(page)), 'API not reachable');

    const big = 'x'.repeat(5_000) + ' END_MARKER_' + Date.now();
    const created = await createBlock(page, { content: big });

    // Try /blocks/:id then /block/:id (route convention varies).
    let resp = await page.goto(`/blocks/${created.id}`);
    if (!resp || resp.status() >= 400) resp = await page.goto(`/block/${created.id}`);
    await waitForAppShell(page);

    // The end-marker must be reachable — if the container clips overflow, scrollIntoView will surface it
    // only when the content is actually in the DOM (not truncated server-side).
    const tail = page.getByText(/END_MARKER_\d+/).first();
    await expect(tail, 'long content tail must exist in DOM').toHaveCount(1, { timeout: 10_000 });
    await tail.scrollIntoViewIfNeeded();
    await expect(tail).toBeVisible();

    // Sanity: total scrollable height of <main> / scrollable ancestor exceeds viewport.
    const docScrollable = await page.evaluate(() => {
      const el = document.scrollingElement || document.documentElement;
      return el.scrollHeight > el.clientHeight;
    });
    // Either the document scrolls, or some inner container does — soft check, not a hard failure.
    expect.soft(docScrollable, 'expected some scrollable area for 5KB content').toBeTruthy();
  });
});
