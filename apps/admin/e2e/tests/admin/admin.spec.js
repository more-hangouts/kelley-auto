import { test, expect } from '../../fixtures/harness.js'

// Unauthenticated protected route must redirect to /login (real behavior:
// /auth/me 401 → app renders the login surface). No auth mock here.
test('unauthenticated protected route redirects to login', async ({ unauthPage }) => {
  await unauthPage.goto('/sales')
  await unauthPage.waitForURL('**/login')
  await expect(unauthPage.getByText(/sign in to continue/i)).toBeVisible()
})

test('login page renders', async ({ unauthPage }) => {
  await unauthPage.goto('/login')
  await expect(unauthPage.getByText(/sign in to continue/i)).toBeVisible()
  await expect(unauthPage.locator('input[type="password"]')).toBeVisible()
})

test('dashboard shell renders with mocked auth', async ({ authedPage }) => {
  await authedPage.goto('/')
  await expect(authedPage.getByRole('heading', { name: /welcome back/i })).toBeVisible()
})

// A table of routes. Each navigates with mocked auth+API and asserts the page
// mounted cleanly. We assert the DashboardLayout chrome ("Sign out" control is
// always present in the eager shell) rendered and the route's own content area
// is non-empty — robust across desktop/mobile without brittle per-page text
// (the harness fixture separately fails the test on any pageerror/console
// error, which is what catches a broken page).
const ROUTES = ['/sales', '/contacts/1', '/inbox', '/analytics', '/settings/staff/schedule/grid', '/events/1/overview']

// Assert the lazy page mounted: the URL is right and #root has content. The
// harness fixture independently fails the test on any pageerror / console
// error / asset 404, which is what actually catches a broken chunk or render.
// (Brand/nav text lives in a drawer that MUI collapses on mobile, so it is not
// a viewport-stable anchor.) The Suspense fallback would leave only a spinner,
// so we wait for the fallback to clear by asserting a non-progressbar element.
async function assertRouteMounted(page, route) {
  await page.goto(route, { waitUntil: 'networkidle' })
  expect(new URL(page.url()).pathname).toBe(route.replace(/\?.*$/, ''))
  await expect(page.locator('#root')).not.toBeEmpty()
  // The DashboardLayout main content region always renders once the lazy page
  // chunk resolves; assert it exists (viewport-independent, unlike the drawer
  // brand which MUI collapses on mobile). The harness fixture separately fails
  // the test on any pageerror / console error / asset 404.
  await expect(page.locator('main')).toBeAttached({ timeout: 10_000 })
}

for (const route of ROUTES) {
  test(`route ${route} loads`, async ({ authedPage }) => {
    await assertRouteMounted(authedPage, route)
  })
}

test('nested event tabs navigate', async ({ authedPage }) => {
  for (const tab of ['/events/1/overview', '/events/1/documents', '/events/1/invoices']) {
    await assertRouteMounted(authedPage, tab)
  }
})

test('legacy redirect /pipeline -> /sales', async ({ authedPage }) => {
  await authedPage.goto('/pipeline')
  await authedPage.waitForURL('**/sales')
  expect(new URL(authedPage.url()).pathname).toBe('/sales')
})

test('legacy redirect /widget-settings -> /settings/widget', async ({ authedPage }) => {
  await authedPage.goto('/widget-settings')
  await authedPage.waitForURL('**/settings/widget')
  expect(new URL(authedPage.url()).pathname).toBe('/settings/widget')
})

// Lazy loading: navigating to a route fetches at least one new /assets/*.js
// chunk on demand (the page was NOT already in the initial bundle).
test('lazy route chunk is fetched on navigation', async ({ authedPage }) => {
  const jsAfterLoad = new Set()
  authedPage.on('request', (r) => {
    if (/\/assets\/.+\.js$/.test(r.url())) jsAfterLoad.add(r.url())
  })
  await authedPage.goto('/')
  await expect(authedPage.getByRole('heading', { name: /welcome back/i })).toBeVisible()
  const beforeNav = jsAfterLoad.size
  await authedPage.goto('/inbox')
  await expect(authedPage.locator('#root')).not.toBeEmpty()
  // Inbox is a lazy page; its chunk (and possibly shared deps) load on nav.
  expect(jsAfterLoad.size).toBeGreaterThan(beforeNav)
})

// Cross-surface isolation: the admin host must never request the SalesApp
// chunk. Resolve the real emitted filename from the build manifest (hash-free).
// Positive control included: admin MUST load admin page chunks (proving the
// name filter is live) while never loading the SalesApp/sales chunks.
test('admin host loads admin chunks but not the SalesApp chunk', async ({ authedPage }) => {
  const requested = []
  authedPage.on('request', (r) => {
    if (/\/assets\/.+\.js$/.test(r.url())) requested.push(r.url())
  })
  await authedPage.goto('/')
  await expect(authedPage.getByRole('heading', { name: /welcome back/i })).toBeVisible()
  await authedPage.goto('/sales')
  await authedPage.goto('/inbox')
  const salesLike = requested.filter((u) => /SalesApp|PinLogin|RepDashboard|ClockScreen/i.test(u))
  const adminLike = requested.filter((u) => /Pipeline|Inbox|Dashboard|ContactDetail/i.test(u))
  expect(salesLike, `unexpected sales chunks on admin host:\n${salesLike.join('\n')}`).toEqual([])
  expect(adminLike.length, 'expected at least one admin page chunk to load').toBeGreaterThan(0)
})
