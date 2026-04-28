/**
 * Shared Playwright fixtures and helpers for memvault-os E2E.
 *
 * Design constraints (test-adversary mode):
 *   - We never read apps/web/src — selectors must be semantic / tolerant.
 *   - Routes are derived from docs/route_manifest.yaml (API) and react-router conventions (UI).
 *   - DOM probes prefer role / text / loose attribute matching, with multiple fallbacks.
 */
import { test as base, expect, type Page, type ConsoleMessage, type Request } from '@playwright/test';

export const API_PREFIX = '/api/memvault';

/** A console-error filter that ignores benign noise from third-party libs we know are loaded. */
export const KNOWN_BENIGN_CONSOLE_PATTERNS: RegExp[] = [
  // three.js / 3d-force-graph occasionally warns about WebGL deprecations in headless Chromium.
  /WebGL/i,
  /THREE\.\w+: \.\w+\(\) has been deprecated/i,
  // React 19 dev-mode hydration recovery messages (not errors-in-prod).
  /Warning: ReactDOM\.render is no longer supported/i,
  // React Router v7 may emit a future-flag warning that's informational.
  /future flag/i,
  // 404 favicon / source-map noise from Vite dev preview.
  /favicon\.ico/i,
  /sourceMappingURL/i,
];

export type ConsoleSpy = {
  errors: ConsoleMessage[];
  warnings: ConsoleMessage[];
  /** Errors filtered through KNOWN_BENIGN_CONSOLE_PATTERNS — only "real" errors. */
  realErrors(): ConsoleMessage[];
};

export function attachConsoleSpy(page: Page): ConsoleSpy {
  const errors: ConsoleMessage[] = [];
  const warnings: ConsoleMessage[] = [];
  page.on('console', (msg) => {
    if (msg.type() === 'error') errors.push(msg);
    else if (msg.type() === 'warning') warnings.push(msg);
  });
  page.on('pageerror', (err) => {
    // Convert uncaught exceptions into console errors so a single assertion catches both.
    errors.push({
      type: () => 'error',
      text: () => `[pageerror] ${err.message}`,
      location: () => ({ url: '', lineNumber: 0, columnNumber: 0 }),
      args: () => [],
      page: () => page,
    } as unknown as ConsoleMessage);
  });
  return {
    errors,
    warnings,
    realErrors() {
      return errors.filter((e) => {
        const text = e.text();
        return !KNOWN_BENIGN_CONSOLE_PATTERNS.some((rx) => rx.test(text));
      });
    },
  };
}

/**
 * Wait for the SPA shell to mount.
 *
 * Invariant we assert (six-tetsu rule "page load 後永遠有 main 元素"):
 *   - After a successful goto, *something* meaningful must be in #root.
 *   - We accept either a real <main>, an [role=main], or a non-empty #root subtree as proof.
 */
export async function waitForAppShell(page: Page) {
  await page.waitForLoadState('domcontentloaded');
  // The app must hydrate into #root within the test timeout.
  await page.waitForFunction(
    () => {
      const root = document.querySelector('#root');
      if (!root) return false;
      const text = (root.textContent || '').trim();
      const childCount = root.children.length;
      return childCount > 0 && text.length > 0;
    },
    null,
    { timeout: 15_000 },
  );
}

/**
 * Create a memory block via API. Returns the created block payload.
 * Uses Playwright's APIRequestContext (independent of the SPA fetch) so test setup is hermetic.
 */
export async function createBlock(
  page: Page,
  payload: { content: string; type?: string; tags?: string[]; source_session?: string } = { content: 'test block' },
) {
  const baseURL = (page.context() as unknown as { _options?: { baseURL?: string } })._options?.baseURL
    ?? new URL(page.url() || 'http://localhost:3000').origin;
  const res = await page.request.post(`${baseURL}${API_PREFIX}/blocks`, {
    data: {
      content: payload.content,
      type: payload.type ?? 'note',
      tags: payload.tags ?? [],
      source_session: payload.source_session ?? `e2e-${Date.now()}`,
    },
    failOnStatusCode: false,
  });
  if (!res.ok()) {
    const body = await res.text();
    throw new Error(`createBlock failed: ${res.status()} ${body}`);
  }
  return res.json() as Promise<{ id: string; content: string; type: string; tags?: string[] }>;
}

/** List blocks via API. */
export async function listBlocks(page: Page): Promise<{ items: Array<{ id: string; content: string }>; total?: number }> {
  const baseURL = process.env.MEMVAULT_TEST_BASE_URL || 'http://localhost:3000';
  const res = await page.request.get(`${baseURL}${API_PREFIX}/blocks`);
  if (!res.ok()) throw new Error(`listBlocks failed: ${res.status()}`);
  const body = (await res.json()) as { items?: Array<{ id: string; content: string }>; total?: number };
  return { items: body.items ?? [], total: body.total };
}

/**
 * Returns true if the API is reachable. Tests can soft-skip when run without the docker stack.
 */
export async function isApiUp(page: Page): Promise<boolean> {
  const baseURL = process.env.MEMVAULT_TEST_BASE_URL || 'http://localhost:3000';
  try {
    const res = await page.request.get(`${baseURL}${API_PREFIX}/status`, { timeout: 3_000 });
    return res.status() < 500;
  } catch {
    return false;
  }
}

/**
 * Tolerant locator that tries semantic role first, then falls back to data-testid / text.
 * Returns the first match — caller may still .first() / .nth() at need.
 */
export function findInteractive(page: Page, hints: { role?: 'button' | 'link' | 'textbox' | 'combobox'; name?: RegExp | string; testId?: string; text?: RegExp | string }) {
  const candidates = [];
  if (hints.role) candidates.push(page.getByRole(hints.role, hints.name ? { name: hints.name } : undefined));
  if (hints.testId) candidates.push(page.getByTestId(hints.testId));
  if (hints.text) candidates.push(page.getByText(hints.text, { exact: false }));
  return candidates;
}

export { base as test, expect };
