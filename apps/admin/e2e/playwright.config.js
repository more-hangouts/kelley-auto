import { defineConfig, devices } from '@playwright/test'

// Two static servers, one per build:
//   :4173  → the normal admin build   (e2e/.build/admin)
//   :4174  → the forced-sales build   (e2e/.build/sales, VITE_FORCE_SUBDOMAIN=sales)
// Both builds bake VITE_API_URL=/api so page.route('**/api/**') intercepts the
// SPA's same-origin API calls. The builds are produced by `pnpm build:e2e`
// (run on the host — vite build needs no browser) into e2e/.build/*, then this
// config serves and drives them entirely inside the pinned Playwright image.
const ADMIN_PORT = 4173
const SALES_PORT = 4174

export default defineConfig({
  testDir: './tests',
  fullyParallel: false,
  workers: 2,
  retries: 0,
  forbidOnly: !!process.env.CI,
  reporter: [['list'], ['html', { outputFolder: 'report', open: 'never' }]],
  outputDir: 'test-results',
  use: {
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
    video: 'off',
  },
  // `serve -s` gives SPA history-fallback so deep links (/events/1/overview)
  // resolve to index.html. reuseExistingServer lets a human re-run against
  // already-running servers during local debugging.
  webServer: [
    {
      command: `npx serve -s .build/admin -l ${ADMIN_PORT}`,
      url: `http://127.0.0.1:${ADMIN_PORT}`,
      reuseExistingServer: !process.env.CI,
      timeout: 60_000,
    },
    {
      command: `npx serve -s .build/sales -l ${SALES_PORT}`,
      url: `http://127.0.0.1:${SALES_PORT}`,
      reuseExistingServer: !process.env.CI,
      timeout: 60_000,
    },
  ],
  projects: [
    {
      name: 'admin-desktop',
      testMatch: /admin\/.*\.spec\.js/,
      use: { ...devices['Desktop Chrome'], baseURL: `http://127.0.0.1:${ADMIN_PORT}` },
    },
    {
      name: 'admin-mobile',
      testMatch: /admin\/.*\.spec\.js/,
      use: { ...devices['Pixel 5'], baseURL: `http://127.0.0.1:${ADMIN_PORT}` },
    },
    {
      name: 'sales-desktop',
      testMatch: /sales\/.*\.spec\.js/,
      use: { ...devices['Desktop Chrome'], baseURL: `http://127.0.0.1:${SALES_PORT}` },
    },
    {
      name: 'sales-mobile',
      testMatch: /sales\/.*\.spec\.js/,
      use: { ...devices['Pixel 5'], baseURL: `http://127.0.0.1:${SALES_PORT}` },
    },
  ],
})
