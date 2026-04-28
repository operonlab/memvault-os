import { defineConfig, devices } from '@playwright/test';

/**
 * Playwright config for memvault-os web E2E.
 *
 * Stack assumptions (read from docs/web_dependency_inventory.md, never from src):
 *   - React 19 + react-router-dom v7 SPA, served by nginx (apps/web/nginx.conf).
 *   - nginx reverse-proxies `/api/` to the api service on port 8080.
 *   - Default local docker-compose maps web container to host port 3000.
 *
 * Override via env:
 *   MEMVAULT_TEST_BASE_URL — defaults to http://localhost:3000
 *   MEMVAULT_TEST_API_MOCK=1 — opt-in API stub mode for CI runs without docker (Group 5 only).
 */
export default defineConfig({
  testDir: './tests/e2e',
  timeout: 30 * 1000,
  expect: { timeout: 5_000 },
  fullyParallel: false,
  retries: process.env.CI ? 2 : 0,
  workers: process.env.CI ? 1 : undefined,
  reporter: process.env.CI ? 'github' : 'list',
  use: {
    baseURL: process.env.MEMVAULT_TEST_BASE_URL || 'http://localhost:3000',
    screenshot: 'only-on-failure',
    trace: 'on-first-retry',
    video: 'retain-on-failure',
    locale: 'zh-Hant-TW',
    timezoneId: 'Asia/Taipei',
  },
  projects: [
    {
      name: 'chromium',
      use: { ...devices['Desktop Chrome'] },
    },
  ],
});
