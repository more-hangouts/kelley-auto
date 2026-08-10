import api from './client'

// Contacts rolodex list. Params: { query, tag, sort, limit, offset }.
// Returns { items, total, limit, offset, tags: [{tag, count}] }.
export async function listContacts(params = {}) {
  const { data } = await api.get('/contacts', { params })
  return data
}

export async function getContact(contactId) {
  const { data } = await api.get(`/contacts/${contactId}`)
  return data
}

export async function updateContact(contactId, patch) {
  const { data } = await api.patch(`/contacts/${contactId}`, patch)
  return data
}

export async function createContact(payload) {
  const { data } = await api.post('/contacts', payload)
  return data
}

// D3 of the CRM record deletion plan. Admin archive/restore verbs.
// Each `archive*` helper expects {reason, note?}; restore helpers take
// no body. Status mapping is documented in api/routers/admin_archive.py.
export async function archiveContact(contactId, { reason, note } = {}) {
  const { data } = await api.post(
    `/admin/contacts/${contactId}/archive`,
    { reason, note: note ?? null },
  )
  return data
}

export async function restoreContact(contactId) {
  const { data } = await api.post(`/admin/contacts/${contactId}/restore`)
  return data
}

// Native-dialer call-attempt tracking (Phase 7). The client logs the tap
// BEFORE opening tel:. payload: { phone, event_id?, source?, idempotency_key? }.
// Server owns the salesperson identity — never pass a user id.
export async function logCallAttempt(contactId, payload) {
  const { data } = await api.post(`/contacts/${contactId}/call-attempts`, payload)
  return data
}

// Attach a salesperson-reported outcome/notes. patch: { outcome?, notes? }.
export async function updateCallAttempt(contactId, attemptId, patch) {
  const { data } = await api.patch(
    `/contacts/${contactId}/call-attempts/${attemptId}`,
    patch,
  )
  return data
}

export async function listCallAttempts(contactId) {
  const { data } = await api.get(`/contacts/${contactId}/call-attempts`)
  return data
}

// Twilio Voice click-to-call bridge (business-number call path). Asks the
// server to ring the rep first, then bridge to the contact so the contact
// sees the business number instead of the rep's personal cell. Logs a call
// attempt server-side just like the native path. payload: { rep_phone?,
// event_id?, idempotency_key? }. Returns { call_attempt_id, provider_call_sid,
// ... }. 503 when Twilio voice isn't configured; the UI falls back to tel:.
export async function startBridgeCall(contactId, payload) {
  const { data } = await api.post(
    `/contacts/${contactId}/call-attempts/bridge`,
    payload,
  )
  return data
}

// Browser softphone: authorize + log one dashboard-placed call. Returns
// { call_attempt_id, dial_token, ... }. The dial_token is handed to the Twilio
// Voice SDK as a custom parameter; the server's TwiML route trusts only that
// token for the destination, so the browser never names the number it dials.
// 503 when the softphone isn't configured; the UI falls back to tel:/bridge.
export async function startBrowserCall(contactId, payload = {}) {
  const { data } = await api.post(
    `/contacts/${contactId}/call-attempts/browser`,
    payload,
  )
  return data
}

// Voice/softphone endpoints now live in ./voice.js.

// Manager/admin call-activity reporting (business-local day).
export async function getCallActivitySummary(params = {}) {
  const { data } = await api.get('/admin/call-activity/summary', { params })
  return data
}

export async function getRecentCallActivity(params = {}) {
  const { data } = await api.get('/admin/call-activity/recent', { params })
  return data
}

// The signed-in rep's own call count for the business-local day (sales-scoped).
export async function getMyCallsToday() {
  const { data } = await api.get('/sales/call-activity/today')
  return data
}
