// Native-dialer call-attempt tracking E2E (Phase 7).
//
// Two behaviors the spec pins down (both from the feature requirements):
//   1. The call attempt is LOGGED via the API BEFORE the native dialer opens
//      (tel:). We intercept POST /call-attempts and capture the moment tel: is
//      invoked, then assert the ordering.
//   2. When the mobile browser becomes visible again after a logged call, the
//      outcome sheet appears; picking an outcome PATCHes the attempt.
//
// Uses the shared authed harness (mocked auth + broad API mock). We register
// call-attempt-specific mocks on top (Playwright matches last-registered-first).

import { test, expect } from '../../fixtures/harness.js'

const CONTACT_ID = 1

// The component dials by clicking a real <a href="tel:…"> element. window.location
// cannot be patched in Chromium, but HTMLAnchorElement.prototype.click CAN be —
// so we hook it and record (with a browser-clock timestamp) any tel: click
// instead of letting it navigate the test browser. We ALSO hook XHR (axios uses
// XHR) to timestamp — in the SAME performance.now() clock — the moment the
// call-attempts POST response arrives, so we can assert true happens-before:
// the POST completes before the dialer opens.
async function captureTelNavigation(page) {
  await page.addInitScript(() => {
    window.__telCalls = []
    window.__postStartAt = null // when the call-attempts POST was sent
    window.__postDoneAt = null // when its response arrived (readyState 4)

    const origClick = HTMLAnchorElement.prototype.click
    HTMLAnchorElement.prototype.click = function () {
      if (String(this.href).startsWith('tel:')) {
        window.__telCalls.push({ value: this.href, t: performance.now() })
        return // swallow — don't navigate
      }
      return origClick.apply(this, arguments)
    }

    // Timestamp the call-attempts POST lifecycle in the browser clock (axios
    // uses XHR). readystatechange→4 fires in the same task the response is
    // delivered, before axios resolves its promise.
    const origOpen = XMLHttpRequest.prototype.open
    XMLHttpRequest.prototype.open = function (method, url) {
      this.__isCallPost =
        String(method).toUpperCase() === 'POST' &&
        /\/contacts\/\d+\/call-attempts$/.test(String(url))
      return origOpen.apply(this, arguments)
    }
    const origSend = XMLHttpRequest.prototype.send
    XMLHttpRequest.prototype.send = function () {
      if (this.__isCallPost) {
        window.__postStartAt = performance.now()
        this.addEventListener('readystatechange', () => {
          if (this.readyState === 4 && window.__postDoneAt === null) {
            window.__postDoneAt = performance.now()
          }
        })
      }
      return origSend.apply(this, arguments)
    }
  })
}

test('call attempt logs via API before opening the dialer', async ({ authedPage }) => {
    const events = []

    // Record when the POST is received (server-side ordering anchor). Add a
    // deliberate delay so that IF the component dialed before awaiting the POST,
    // tel: would fire measurably BEFORE the POST completes — making the
    // happens-before assertion below meaningful rather than coincidental.
    await authedPage.route(/\/api\/contacts\/\d+\/call-attempts$/, async (route) => {
      if (route.request().method() === 'POST') {
        events.push('post')
        await new Promise((r) => setTimeout(r, 300))
        await route.fulfill({
          status: 201,
          contentType: 'application/json',
          body: JSON.stringify({
            id: 501,
            contact_id: CONTACT_ID,
            outcome: 'call_initiated',
            outcome_pending: true,
            created: true,
          }),
        })
        return
      }
      await route.fulfill({ status: 200, contentType: 'application/json', body: '{"call_attempts":[]}' })
    })

    await captureTelNavigation(authedPage)
    await authedPage.goto(`/contacts/${CONTACT_ID}`, { waitUntil: 'networkidle' })

    // The phone renders as the CallContact control (a button/link). Click it.
    const callControl = authedPage.getByRole('button', { name: /5125550100|call/i }).first()
    await callControl.click()

    // Wait until the dialer has fired.
    await expect
      .poll(async () => (await authedPage.evaluate(() => window.__telCalls.length)) > 0)
      .toBeTruthy()

    const { telAt, postStartAt, postDoneAt } = await authedPage.evaluate(() => ({
      telAt: window.__telCalls[0]?.t ?? null,
      postStartAt: window.__postStartAt,
      postDoneAt: window.__postDoneAt,
    }))
    // A POST happened and the dialer opened.
    expect(events[0]).toBe('post')
    expect(telAt).not.toBeNull()
    expect(postStartAt).not.toBeNull()
    expect(postDoneAt).not.toBeNull()
    // TRUE happens-before, jitter-proof: the POST was mocked with a 300ms delay,
    // so the dialer opening at least 250ms after the POST was SENT proves the
    // component waited for the response before dialing (a dial-first bug would
    // fire tel: within a few ms of the send, far below this threshold).
    expect(telAt).toBeGreaterThanOrEqual(postStartAt + 250)
    // And the dialer opened after the response actually arrived.
    expect(telAt).toBeGreaterThanOrEqual(postDoneAt - 5)
    // Exactly one tel: navigation (no double-fire).
    const telCount = await authedPage.evaluate(() => window.__telCalls.length)
    expect(telCount).toBe(1)
  })

test('double-tap logs only one call attempt and opens the dialer once', async ({ authedPage }) => {
    let postCount = 0
    await authedPage.route(/\/api\/contacts\/\d+\/call-attempts$/, async (route) => {
      if (route.request().method() === 'POST') {
        postCount += 1
        await new Promise((r) => setTimeout(r, 200))
        await route.fulfill({
          status: 201,
          contentType: 'application/json',
          body: JSON.stringify({ id: 601, contact_id: CONTACT_ID, outcome: 'call_initiated', outcome_pending: true, created: true }),
        })
        return
      }
      await route.fulfill({ status: 200, contentType: 'application/json', body: '{"call_attempts":[]}' })
    })

    await captureTelNavigation(authedPage)
    await authedPage.goto(`/contacts/${CONTACT_ID}`, { waitUntil: 'networkidle' })

    // Fire TWO clicks in the SAME synchronous tick (the real double-tap / touch
    // double-fire case). Playwright's serialized .click() can't reproduce this,
    // so dispatch two native click events back-to-back in one evaluate. The
    // component's synchronous in-flight ref must collapse them to one attempt.
    await authedPage.evaluate(() => {
      const btn = [...document.querySelectorAll('button')].find((b) =>
        /5125550100/.test(b.textContent || ''),
      )
      btn.click()
      btn.click()
    })

    await expect
      .poll(async () => (await authedPage.evaluate(() => window.__telCalls.length)) > 0)
      .toBeTruthy()
    await authedPage.waitForTimeout(400)

    expect(postCount).toBe(1) // exactly one row created
    const telCount = await authedPage.evaluate(() => window.__telCalls.length)
    expect(telCount).toBe(1) // dialer opened once
  })

  test('shows the outcome sheet after visibility returns and PATCHes the outcome', async ({ authedPage }) => {
    let patched = null

    await authedPage.route(/\/api\/contacts\/\d+\/call-attempts$/, async (route) => {
      if (route.request().method() === 'POST') {
        await route.fulfill({
          status: 201,
          contentType: 'application/json',
          body: JSON.stringify({ id: 777, contact_id: CONTACT_ID, outcome: 'call_initiated', outcome_pending: true, created: true }),
        })
        return
      }
      await route.fulfill({ status: 200, contentType: 'application/json', body: '{"call_attempts":[]}' })
    })
    await authedPage.route(/\/api\/contacts\/\d+\/call-attempts\/\d+$/, async (route) => {
      patched = JSON.parse(route.request().postData() || '{}')
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ id: 777, outcome: patched.outcome, outcome_pending: false }),
      })
    })

    await captureTelNavigation(authedPage)
    await authedPage.goto(`/contacts/${CONTACT_ID}`, { waitUntil: 'networkidle' })

    await authedPage.getByRole('button', { name: /5125550100|call/i }).first().click()
    // Let the POST resolve + the sheet arm.
    await authedPage.waitForTimeout(200)

    // Simulate the real mobile lifecycle: the dialer BACKGROUNDS the app
    // (visibility → hidden), then the rep returns (→ visible). The sheet must
    // only appear after a genuine background+return, not on any tab switch.
    await authedPage.evaluate(() => {
      let state = 'hidden'
      Object.defineProperty(document, 'visibilityState', { configurable: true, get: () => state })
      document.dispatchEvent(new Event('visibilitychange')) // hidden (dialer took over)
      state = 'visible'
      document.dispatchEvent(new Event('visibilitychange')) // returned
    })

    // The outcome sheet appears.
    await expect(authedPage.getByText(/how did the call go/i)).toBeVisible()

    // Pick "Connected" and save.
    await authedPage.getByRole('button', { name: /^connected$/i }).click()
    await authedPage.getByRole('button', { name: /save outcome/i }).click()

    // The PATCH carried the explicit outcome (never inferred).
    await expect.poll(() => patched?.outcome).toBe('connected')
  })

test('does not prompt for an outcome on a tab return without a real dialer background', async ({ authedPage }) => {
    await authedPage.route(/\/api\/contacts\/\d+\/call-attempts$/, async (route) => {
      if (route.request().method() === 'POST') {
        await route.fulfill({
          status: 201,
          contentType: 'application/json',
          body: JSON.stringify({ id: 888, contact_id: CONTACT_ID, outcome: 'call_initiated', outcome_pending: true, created: true }),
        })
        return
      }
      await route.fulfill({ status: 200, contentType: 'application/json', body: '{"call_attempts":[]}' })
    })

    await captureTelNavigation(authedPage)
    await authedPage.goto(`/contacts/${CONTACT_ID}`, { waitUntil: 'networkidle' })
    await authedPage.getByRole('button', { name: /5125550100|call/i }).first().click()
    await authedPage.waitForTimeout(200)

    // A bare "visible" event with NO preceding "hidden" (e.g. desktop tel: no-op,
    // or an unrelated focus event) must NOT open the sheet.
    await authedPage.evaluate(() => {
      Object.defineProperty(document, 'visibilityState', { configurable: true, get: () => 'visible' })
      document.dispatchEvent(new Event('visibilitychange'))
    })
    await authedPage.waitForTimeout(300)
    await expect(authedPage.getByText(/how did the call go/i)).toHaveCount(0)
  })
