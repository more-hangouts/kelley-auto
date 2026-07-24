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

for (const route of ['/', '/clock', '/schedule', '/appointments/123']) {
  test(`sales route ${route} loads`, async ({ authedPage }) => {
    await authedPage.goto(route)
    await expect(authedPage.locator('#root')).not.toBeEmpty()
    // SalesLayout renders a top app bar with a menu/nav; assert some button
    // chrome is present (the eager shell mounted around the lazy page).
    await expect(authedPage.getByRole('button').first()).toBeAttached({ timeout: 10_000 })
  })
}

// Cross-surface isolation: the sales host must never request an admin PAGE
// chunk (Pipeline / Contacts / Inbox / analytics live only on the admin side).
test('sales host does not load admin page chunks', async ({ authedPage }) => {
  const requested = []
  authedPage.on('request', (r) => {
    if (/\/assets\/.+\.js$/.test(r.url())) requested.push(r.url())
  })
  await authedPage.goto('/')
  await expect(authedPage.locator('#root')).not.toBeEmpty()
  await authedPage.goto('/schedule')
  const adminLike = requested.filter((u) =>
    /Pipeline|ContactDetail|Contacts-|InvoicesGlobal|StorefrontAnalytics|AdminScheduleGrid|AttendanceReview/i.test(u),
  )
  expect(adminLike, `unexpected admin chunks on sales host:\n${adminLike.join('\n')}`).toEqual([])
})
