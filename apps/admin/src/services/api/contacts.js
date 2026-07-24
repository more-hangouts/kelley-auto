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
