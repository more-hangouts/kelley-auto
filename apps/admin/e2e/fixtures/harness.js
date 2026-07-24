import { test as base, expect } from '@playwright/test'

// Minimal-but-valid JSON shapes for the endpoints the pages fire on mount.
// These are intentionally empty/degenerate — the goal is that each page
// RENDERS without throwing, not that it shows real data.
const OK = (body) => ({ status: 200, contentType: 'application/json', body: JSON.stringify(body) })

const ADMIN_USER = { id: 1, username: 'e2e', full_name: 'E2E Admin', role: 'admin' }
const SALES_USER = { id: 2, username: 'rep', full_name: 'E2E Rep', role: 'sales', force_pin_change: false }

// The array is written MOST-SPECIFIC FIRST for readability. Playwright resolves
// routes last-registered-first, so the installer (installMocks) registers this
// array in REVERSE, making the specific entries here win over the broad ones
// below them. Every handler returns a 200 with a valid-shaped body matching
// what the page actually reads.
function authedRoutes() {
  const routes = [
    // Auth probes — presence of a user keeps the app out of the login redirect.
    [/\/api\/auth\/me$/, OK(ADMIN_USER)],
    [/\/api\/sales\/auth\/me$/, OK(SALES_USER)],

    // Admin dashboard widgets — shapes match each widget's data access exactly.
    [/\/api\/dashboard\/ar-summary/, OK({
      outstanding_balance_cents: 0, outstanding_invoice_count: 0,
      overdue_balance_cents: 0, overdue_invoice_count: 0,
      deposits_collected_this_month_cents: 0,
    })],
    [/\/api\/dashboard\/recent-payments/, OK([])],          // widget reads query.data.length (array)
    [/\/api\/dashboard\/awaiting-signature/, OK([])],       // array
    [/\/api\/dashboard\/agenda-today/, OK({ appointments: [] })],
    [/\/api\/dashboard\/pipeline-counts/, OK({ lanes: [] })],
    [/\/api\/dashboard\/splh-leaderboard/, OK({ from_date: '2026-07-20', to_date: '2026-07-26', rows: [] })],

    // Deals / events / pipeline
    [/\/api\/events\/board/, OK({ columns: [], items: [] })],
    [/\/api\/events\/workflow\//, OK({ statuses: [], transitions: {} })],
    [/\/api\/events\/[^/]+\/documents/, OK({ items: [] })],
    [/\/api\/events\/[^/]+\/document-counts/, OK({ counts: {} })],
    [/\/api\/events\/[^/]+\/journey/, OK({ items: [] })],
    [/\/api\/events\/[^/]+\/activity/, OK({ items: [] })],
    [/\/api\/events\/[^/]+\/invoices/, OK({ items: [] })],
    [/\/api\/events\/[^/]+\/quotes/, OK({ items: [] })],
    [/\/api\/events\/[^/]+\/payments/, OK({ items: [] })],
    // Event detail by NUMERIC id only, so it never shadows /events/board.
    [/\/api\/events\/\d+(\?|$)/, OK({ id: 123, event_type: 'vehicle_sale', status: 'new', event_name: 'E2E Deal', participants: [] })],

    // Contacts
    [/\/api\/contacts\/[^/?]+$/, OK({ id: 1, full_name: 'E2E Contact', phone: '+15125550100', emails: [], events: [] })],
    [/\/api\/contacts(\?|$)/, OK({ items: [], total: 0, limit: 25, offset: 0, tags: [] })],

    // Inbox / messaging
    [/\/api\/inbox\/unread-count/, OK({ count: 0 })],
    [/\/api\/inbox\/conversations/, OK({ conversations: [], items: [] })],

    // Analytics — funnel MUST be an array (page does funnel.map()).
    [/\/api\/admin\/storefront-analytics\/summary/, OK({ funnel: [], series: [], totals: {} })],
    [/\/api\/analytics/, OK({ funnel: [], series: [], totals: {} })],

    // Scheduling (admin grid)
    [/\/api\/admin\/schedule\/week/, OK({ entries: [], days: [] })],
    [/\/api\/admin\/schedule/, OK({ items: [], entries: [] })],
    [/\/api\/admin\//, OK({ items: [] })],

    // Sales surface
    [/\/api\/sales\/auth\/staff-picker/, OK([{ username: 'rep', full_name: 'E2E Rep' }])],
    [/\/api\/sales\/clock\/status/, OK({ clocked_in: false })],
    [/\/api\/sales\/appointments\/today/, OK({ appointments: [] })],
    // Appointment detail is a rich page: it reads data.appointment.<many>,
    // data.event, data.contact, data.enrichment, and the arrays
    // data.participants / data.recent_activity (both .length'd). Provide the
    // full envelope so the page renders instead of dropping to its error state.
    [/\/api\/sales\/appointments\/\d+(\?|$)/, OK({
      appointment: {
        id: 123, status: 'scheduled', internal_notes: '',
        timezone: 'America/Chicago',
        slot_start_at: '2026-07-27T15:00:00Z', slot_end_at: '2026-07-27T16:00:00Z',
        celebrant_first_name: 'E2E', celebrant_last_name: 'Guest',
        parent_first_name: '', parent_last_name: '',
        email: 'guest@example.com', phone: '+15125550100',
        party_size_bucket: null, confirmation_code: 'E2E123',
        assigned_user_id: null, assigned_user_full_name: null,
        event_participant_id: null,
      },
      event: null,
      contact: null,
      enrichment: null,
      participants: [],
      recent_activity: [],
    })],
    // Team schedule reads teamData?.entries; my-schedule reads data.days.map
    // and data.posts, so both need their real shapes (guarded fields use ?? []
    // but data.days.map is unguarded).
    [/\/api\/sales\/schedule\/team/, OK({ entries: [] })],
    [/\/api\/sales\/schedule/, OK({ days: [], posts: [], entries: [] })],
    [/\/api\/sales\//, OK({ items: [] })],
  ]
  return routes
}

// Fixture: an authenticated page for `surface` ('admin'|'sales') with all API
// calls mocked, plus console-error / pageerror / asset-404 collectors that are
// asserted empty at teardown.
export const test = base.extend({
  authedPage: async ({ page }, use, testInfo) => {
    await installCollectors(page, testInfo)
    // Playwright matches routes LAST-registered-first. Register the broad
    // catch-all FIRST (lowest priority). Then register authedRoutes() in
    // REVERSE so the most-specific entries (written first in the array) are
    // registered LAST and therefore WIN over the broader domain patterns
    // (/api/sales/, /api/admin/) that sit below them in the array. Without the
    // reverse, those broad patterns would shadow every specific sales/analytics
    // mock and pages would render on garbage shapes.
    await page.route(/\/api\//, (route) =>
      route.fulfill({ status: 200, contentType: 'application/json', body: '{"items":[],"data":[],"ok":true}' }),
    )
    for (const [pattern, response] of [...authedRoutes()].reverse()) {
      await page.route(pattern, (route) => route.fulfill(response))
    }
    await use(page)
    await assertClean(page, testInfo)
  },

  // Unauthenticated page: /auth/me and /sales/auth/me return 401 so the app
  // performs its REAL protected-route redirect. The sales PIN-login screen is
  // itself the unauthenticated surface and fetches the (pre-auth) staff picker
  // on mount, so that one must still return its real array shape.
  unauthPage: async ({ page }, use, testInfo) => {
    await installCollectors(page, testInfo)
    await page.route(/\/api\/sales\/auth\/staff-picker/, (route) =>
      route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify([{ username: 'rep', full_name: 'E2E Rep' }]) }),
    )
    await page.route(/\/api\/(sales\/)?auth\/me$/, (route) =>
      route.fulfill({ status: 401, contentType: 'application/json', body: '{"detail":"unauthenticated"}' }),
    )
    await use(page)
    // Note: unauth redirect tests may legitimately log a 401 rejection; the
    // collector allowlist below tolerates it.
    await assertClean(page, testInfo)
  },
})

function installCollectors(page, testInfo) {
  const consoleErrors = []
  const pageErrors = []
  const badAssets = []
  page.on('console', (m) => {
    if (m.type() === 'error') consoleErrors.push(m.text())
  })
  page.on('pageerror', (e) => pageErrors.push(String(e)))
  page.on('response', (r) => {
    const u = r.url()
    if (/\/assets\/.+\.(js|css|svg)$/.test(u) && r.status() !== 200) {
      badAssets.push(`${r.status()} ${u}`)
    }
  })
  testInfo._collectors = { consoleErrors, pageErrors, badAssets }
}

// Console noise we tolerate: mocked-401 network rejections and the known
// httpx/react-query dev warnings that are not Phase-6 regressions.
const ALLOWED_CONSOLE = [
  /401/,
  /unauthenticated/i,
  /Failed to load resource/i,
  /net::ERR/i,
]

async function assertClean(page, testInfo) {
  const { consoleErrors, pageErrors, badAssets } = testInfo._collectors
  const realConsole = consoleErrors.filter((t) => !ALLOWED_CONSOLE.some((re) => re.test(t)))
  expect(pageErrors, `page errors:\n${pageErrors.join('\n')}`).toEqual([])
  expect(realConsole, `unexpected console errors:\n${realConsole.join('\n')}`).toEqual([])
  expect(badAssets, `asset 404s:\n${badAssets.join('\n')}`).toEqual([])
}

export { expect }
