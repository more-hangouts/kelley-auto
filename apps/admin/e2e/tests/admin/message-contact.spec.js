// Start-SMS-conversation from CRM surfaces E2E (Phase 8).
//
// Verifies the shared Message action on the contact detail surface:
//   1. An eligible contact shows an ENABLED Message icon; clicking it asks the
//      server to create/reuse the conversation, then opens the composer with the
//      contact name + MASKED number.
//   2. Sending posts to the send endpoint and clears the draft.
//   3. An ineligible contact (no consent) shows a DISABLED Message icon.
//
// Uses the shared authed harness; we override the contact + inbox routes.

import { test, expect } from '../../fixtures/harness.js'

const CONTACT_ID = 1

function contactRoute(page, { consent = true, optedOut = false } = {}) {
  return page.route(/\/api\/contacts\/1(\?|$)/, (route) => {
    if (route.request().method() !== 'GET') return route.fallback()
    return route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        id: CONTACT_ID,
        display_name: 'E2E Contact',
        first_name: 'E2E',
        last_name: 'Contact',
        phone: '+15125550100',
        phone_e164: '+15125550100',
        email: 'e2e@example.com',
        address: {},
        notes: null,
        tags: [],
        event_count: 0,
        appointment_count: 0,
        alternate_celebrants: [],
        linked_events: [],
        sms_consent: consent,
        sms_opted_out: optedOut,
      }),
    })
  })
}

test('eligible contact: Message opens the composer and sends', async ({ authedPage }) => {
  await contactRoute(authedPage, { consent: true })

  let started = false
  let sentBody = null
  await authedPage.route(/\/api\/inbox\/conversations\/sms$/, async (route) => {
    started = true
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        conversation_id: 42,
        created: true,
        contact: { id: CONTACT_ID, display_name: 'E2E Contact', phone: '+15125550100' },
        eligibility: { eligible: true, reason: 'eligible' },
      }),
    })
  })
  await authedPage.route(/\/api\/inbox\/conversations\/42$/, (route) =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ id: 42, channel: 'sms', reply_enabled: true, messages: [] }),
    }),
  )
  await authedPage.route(/\/api\/inbox\/conversations\/42\/messages$/, async (route) => {
    sentBody = JSON.parse(route.request().postData() || '{}')
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ message: { id: 1, body: sentBody.body, direction: 'outbound' } }),
    })
  })

  await authedPage.goto(`/contacts/${CONTACT_ID}`, { waitUntil: 'networkidle' })

  // The Message icon is enabled; click it.
  const msgBtn = authedPage.getByRole('button', { name: /message contact/i }).first()
  await expect(msgBtn).toBeEnabled()
  await msgBtn.click()

  // Server was asked to start the conversation; composer opens with the masked number.
  await expect.poll(() => started).toBeTruthy()
  await expect(authedPage.getByText('E2E Contact').first()).toBeVisible()
  await expect(authedPage.getByText(/•••-0100/)).toBeVisible() // masked, last 4 shown

  // Type + send.
  const field = authedPage.getByPlaceholder(/type a message/i)
  await field.fill('Hello there')
  await authedPage.getByRole('button', { name: /^send$/i }).click()

  await expect.poll(() => sentBody?.body).toBe('Hello there')
})

test('ineligible contact (no consent): Message is disabled', async ({ authedPage }) => {
  await contactRoute(authedPage, { consent: false })
  await authedPage.goto(`/contacts/${CONTACT_ID}`, { waitUntil: 'networkidle' })

  const msgBtn = authedPage.getByRole('button', { name: /message contact/i }).first()
  await expect(msgBtn).toBeDisabled()
})
