import api from './client'

export async function listNotificationSubscribers() {
  const { data } = await api.get('/admin/notification-subscribers')
  return data
}

export async function createNotificationSubscriber(body) {
  const { data } = await api.post('/admin/notification-subscribers', body)
  return data
}

export async function updateSubscriberSubscriptions(subscriberId, subscriptions) {
  const { data } = await api.put(
    `/admin/notification-subscribers/${subscriberId}/subscriptions`,
    { subscriptions },
  )
  return data
}

export async function setSubscriberActive(subscriberId, isActive) {
  const { data } = await api.patch(
    `/admin/notification-subscribers/${subscriberId}`,
    { is_active: isActive },
  )
  return data
}

export async function deleteNotificationSubscriber(subscriberId) {
  await api.delete(`/admin/notification-subscribers/${subscriberId}`)
}

// ─── Omnichannel inbox (Phase 2) ───────────────────────────────────────────

export async function listInboxConversations(params = {}) {
  const { data } = await api.get('/inbox/conversations', { params })
  return data
}

export async function getInboxConversation(conversationId) {
  const { data } = await api.get(`/inbox/conversations/${conversationId}`)
  return data
}

export async function patchInboxConversation(conversationId, body) {
  const { data } = await api.patch(`/inbox/conversations/${conversationId}`, body)
  return data
}

// Reply into a thread. Web-chat threads deliver immediately (the visitor's
// widget polls the row); SMS sends via Twilio once A2P sending is enabled.
// Pass allowQuietHours to override the quiet-hours guard after a 409.
export async function sendInboxMessage(conversationId, body, allowQuietHours = false) {
  const { data } = await api.post(
    `/inbox/conversations/${conversationId}/messages`,
    { body, allow_quiet_hours: allowQuietHours },
  )
  return data
}

export async function getInboxUnreadCount() {
  const { data } = await api.get('/inbox/unread-count')
  return data
}

// Phase 8: create or reuse the canonical SMS conversation for a contact.
// Idempotent + race-safe; does NOT send. Returns { conversation_id, created,
// contact, eligibility: { eligible, reason } }. Never pass a phone number —
// the server derives it from the contact record.
export async function startSmsConversation(contactId, eventId = null) {
  const { data } = await api.post('/inbox/conversations/sms', {
    contact_id: contactId,
    event_id: eventId,
  })
  return data
}
