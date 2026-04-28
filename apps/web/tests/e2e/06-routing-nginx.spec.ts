/**
 * Group 6 — Routing / nginx sanity (bonus, derived from apps/web/nginx.conf review)
 *
 * Concerns spotted in nginx.conf without reading src:
 *   - SPA fallback `try_files $uri $uri/ /index.html` will return 200 for *any* path, even nonsense.
 *     React Router must therefore render a 404 view itself — if it doesn't, deep-link typos
 *     silently render a blank page. T1 catches this.
 *   - The static-asset regex matches files by extension, but a hashed asset under `/assets/...`
 *     might get long-cache headers that block dev iteration. We don't deploy here, but T2 sanity-checks
 *     that the SPA shell HTML itself is served with `no-store`.
 *   - `/api/` is the only proxy path; calling `/api` (no trailing slash) is a known nginx 404 trap.
 *     T3 documents this gap.
 */
import { test, expect } from '@playwright/test';
import { attachConsoleSpy, waitForAppShell } from './_fixtures';

test.describe('Group 6 — Routing / nginx sanity', () => {
  test('unknown SPA route renders a 404 view (not a blank page)', async ({ page }) => {
    const spy = attachConsoleSpy(page);
    const resp = await page.goto('/this-route-definitely-does-not-exist-' + Date.now());
    expect(resp, 'navigation must return a Response').not.toBeNull();
    // nginx returns 200 with index.html (intended). The React app must own the 404.
    expect(resp!.status()).toBeLessThan(400);
    await waitForAppShell(page);

    // Invariant: either a 404 / not found phrase appears, or at minimum the shell is alive
    // and not stuck on a blank loading state.
    const notFound = page.getByText(/404|not found|找不到|頁面不存在/i);
    const childCount = await page.locator('#root > *').count();
    expect(childCount, 'shell must render even on unknown route').toBeGreaterThan(0);

    // Soft: prefer an explicit 404 message; if missing, flag for the dev to add one.
    const has404 = await notFound.count();
    expect.soft(has404, 'app should render an explicit 404 view for unknown SPA routes (currently absent → silent blank risk)').toBeGreaterThan(0);

    expect(spy.realErrors(), 'unknown route must not throw').toHaveLength(0);
  });

  test('index.html is served with no-store cache header (per nginx.conf line 28)', async ({ request }) => {
    const baseURL = process.env.MEMVAULT_TEST_BASE_URL || 'http://localhost:3000';
    const resp = await request.get(`${baseURL}/`);
    expect(resp.ok(), 'GET / must succeed').toBeTruthy();
    const cc = resp.headers()['cache-control'] || '';
    // Mutation-detect: if a future config change drops `no-store`, browsers will pin a stale shell.
    expect(cc.toLowerCase(), 'index.html must be served with no-store').toContain('no-store');
  });

  test('/api (no trailing slash) is NOT proxied — confirms the nginx location prefix gap', async ({ request }) => {
    const baseURL = process.env.MEMVAULT_TEST_BASE_URL || 'http://localhost:3000';
    // nginx's `location /api/` only matches paths starting with `/api/`.
    // A bare `/api` will fall through to the SPA fallback and return index.html (200).
    // This isn't a bug per se, but it's a routing gotcha worth documenting in a regression test.
    const resp = await request.get(`${baseURL}/api`);
    const ct = resp.headers()['content-type'] || '';
    expect(
      ct.includes('text/html'),
      '/api (no slash) currently falls through to SPA — if this changes to a 404 or proxy, update the routing docs',
    ).toBeTruthy();
  });
});
