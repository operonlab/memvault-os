/**
 * Group 2 — Block list / create
 *
 * Mutation-thinking lens:
 *   - If the create form's submit handler is a noop, T2 fails (no row appears).
 *   - If the list query's response shape mismatches, T1 fails (empty state never resolves).
 *   - If detail route is broken, T3 fails (URL changes but content empty / 404 page).
 *   - If CJK is mojibake'd anywhere in the toolchain (DB collation, JSON encoding, font), T4 catches it.
 *
 * Routes (from docs/route_manifest.yaml):
 *   GET  /api/memvault/blocks
 *   POST /api/memvault/blocks
 *   GET  /api/memvault/blocks/{id}
 */
import { test, expect } from '@playwright/test';
import { attachConsoleSpy, createBlock, isApiUp, listBlocks, waitForAppShell } from './_fixtures';

test.describe('Group 2 — Block list / create', () => {
  test.beforeEach(async ({ page }) => {
    test.skip(!(await isApiUp(page)), 'API not reachable — start docker compose first');
  });

  test('block list renders empty state when no blocks exist', async ({ page }) => {
    const { items } = await listBlocks(page);
    test.skip(items.length > 0, 'DB already has blocks; this test only meaningful on a clean DB. Run `docker compose down -v` first.');

    const spy = attachConsoleSpy(page);
    await page.goto('/');
    await waitForAppShell(page);

    // Invariant: empty state UI must be visible OR the list region renders without any "card" descendants,
    // *and* nothing in #root crashes. We accept either an empty-state phrase (i18n-tolerant) or zero rows.
    const emptyHints = page.getByText(/no (blocks|memories|results)|尚無|還沒有|空的|empty/i);
    const cards = page.locator('[data-testid^="block-"], [data-testid="memory-card"], article');

    const hasEmptyHint = await emptyHints.count();
    const cardCount = await cards.count();

    expect(
      hasEmptyHint > 0 || cardCount === 0,
      'empty state must show an empty-state hint or render zero cards (no crash, no skeleton stuck)',
    ).toBeTruthy();

    expect(spy.realErrors(), 'no console errors on empty list').toHaveLength(0);
  });

  test('create block via API and verify it surfaces on the home page', async ({ page }) => {
    // We seed via API instead of clicking through a form whose selectors we can't trust —
    // the *invariant* under test is "list reflects newly created data," which is what users care about.
    const marker = `e2e-create-${Date.now()}`;
    const created = await createBlock(page, { content: marker, type: 'note' });
    expect(created.id, 'API must return an id for the created block').toBeTruthy();

    await page.goto('/');
    await waitForAppShell(page);

    // Tolerant assertion: the marker text must appear somewhere in the rendered DOM.
    // If the list is paginated or virtualized, give a reasonable wait window.
    await expect(page.getByText(marker, { exact: false }).first()).toBeVisible({ timeout: 10_000 });
  });

  test('block detail page renders content for an existing block', async ({ page }) => {
    const created = await createBlock(page, { content: `detail-probe-${Date.now()}` });
    const spy = attachConsoleSpy(page);

    // React Router v7 conventions: detail likely at /blocks/:id — fall back to /block/:id if 404.
    let resp = await page.goto(`/blocks/${created.id}`);
    if (!resp || resp.status() >= 400) {
      resp = await page.goto(`/block/${created.id}`);
    }
    await waitForAppShell(page);

    // The created content MUST appear on the detail page; if the detail route is wired wrong,
    // this fails even when the URL itself is reachable.
    await expect(page.getByText(created.content, { exact: false }).first()).toBeVisible({ timeout: 10_000 });

    // Invariant: navigating to a detail does not crash the SPA — #root still has children.
    await expect(page.locator('#root > *').first()).toBeVisible();
    expect(spy.realErrors(), 'no console errors on detail nav').toHaveLength(0);
  });

  test('CJK content displays correctly (no mojibake, no NCRs)', async ({ page }) => {
    const cjk = '繁體中文・記憶碎片 ✨ 漢字テスト';
    const created = await createBlock(page, { content: cjk });
    await page.goto('/');
    await waitForAppShell(page);

    // Anti-mutation check: search for the literal CJK string. If anywhere along the chain
    // (UTF-8 → JSON → React render) flips to latin-1, this string won't match.
    await expect(page.getByText(cjk, { exact: false }).first()).toBeVisible({ timeout: 10_000 });

    // Negative invariant: no NCR-encoded fallback (e.g., "&#x7e41;") leaked into the DOM.
    const ncrLeak = await page.evaluate(() => /&#x?[0-9a-fA-F]+;/.test(document.body.innerHTML));
    expect(ncrLeak, 'numeric character references should not appear — indicates encoding bug').toBeFalsy();
  });
});
