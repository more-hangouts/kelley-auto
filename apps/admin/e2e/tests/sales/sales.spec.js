import { test, expect } from '../../fixtures/harness.js'

// These run against the forced-sales build (VITE_FORCE_SUBDOMAIN=sales) served
// on :4174, so the app mounts SalesApp regardless of hostname. The harness
// fixture fails any test on a pageerror / unexpected console error / asset 404,
// so "mounts non-empty without errors" is the core signal that a lazy sales
// page chunk loaded and rendered.

test('sales unauthenticated protected route redirects to login', async ({ unauthPage }) => {
  await unauthPage.goto('/schedule')
  await unauthPage.waitForURL('**/login')
  await expect(unauthPage.locator('#root')).not.toBeEmpty()
})

test('sales pin login renders', async ({ unauthPage }) => {
  await unauthPage.goto('/login')
  await expect(unauthPage.locator('#root')).not.toBeEmpty()
  // PinLogin always shows a "Sign in" affordance.
  await expect(unauthPage.getByText(/sign in/i).first()).toBeVisible({ timeout: 10_000 })
})

// Each route asserts the URL stayed put (not bounced to /login, which would
// mean auth broke) AND a page-specific piece of content rendered — so a broken
// lazy chunk or a wrong mock shape (which would drop the page to an error state
// or redirect) fails the test. The harness also fails on any pageerror.
// Each route asserts the URL stayed put (not bounced to /login) and a piece of
// the page's MAIN content rendered. We scope the text query to the <main>
// content region so it never matches the SalesLayout nav drawer (which MUI
// hides on mobile — matching a hidden nav link would flake the mobile run).
const SALES_ROUTES = [
  ['/', /appointment|today|lead|search|available|mine/i],
  ['/clock', /clock|punch|shift|in|out|location/i],
  ['/schedule', /schedule|shift|week|availability|coworker|your/i],
  ['/appointments/123', /appointment|status|note|confirmation|guest/i],
]

for (const [route, content] of SALES_ROUTES) {
  test(`sales route ${route} loads`, async ({ authedPage }) => {
    await authedPage.goto(route, { waitUntil: 'networkidle' })
    // Did NOT redirect to /login (auth held).
    expect(new URL(authedPage.url()).pathname).toBe(route)
    await expect(authedPage.locator('#root')).not.toBeEmpty()
    // Main-content text rendered (scoped to <main>, not the collapsible nav).
    await expect(authedPage.locator('main').getByText(content).first()).toBeVisible({ timeout: 10_000 })
  })
}

// Cross-surface isolation: the sales host must never request an admin PAGE
// chunk (Pipeline / Contacts / Inbox / analytics live only on the admin side).
// Includes a POSITIVE CONTROL: the sales host MUST load at least one sales page
// chunk, proving the name-pattern filter is actually live (guards against a
// vacuous pass if chunk naming ever changes).
test('sales host loads sales chunks but not admin page chunks', async ({ authedPage }) => {
  const requested = []
  authedPage.on('request', (r) => {
    if (/\/assets\/.+\.js$/.test(r.url())) requested.push(r.url())
  })
  await authedPage.goto('/')
  await expect(authedPage.locator('#root')).not.toBeEmpty()
  await authedPage.goto('/schedule')
  await expect(authedPage.locator('#root')).not.toBeEmpty()
  const adminLike = requested.filter((u) =>
    /Pipeline|ContactDetail|Contacts-|InvoicesGlobal|StorefrontAnalytics|AdminScheduleGrid|AttendanceReview/i.test(u),
  )
  const salesLike = requested.filter((u) => /SalesApp|RepDashboard|Schedule|PinLogin|ClockScreen/i.test(u))
  expect(adminLike, `unexpected admin chunks on sales host:\n${adminLike.join('\n')}`).toEqual([])
  expect(salesLike.length, 'expected at least one sales page chunk to load').toBeGreaterThan(0)
})
