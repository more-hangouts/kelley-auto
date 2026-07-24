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
