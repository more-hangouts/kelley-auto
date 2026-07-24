import api from './client'

export async function getEventBoard(eventType = 'vehicle_sale') {
  const { data } = await api.get('/events/board', { params: { event_type: eventType } })
  return data
}

export async function patchEventStatus(eventId, newStatus, notes) {
  const body = { status: newStatus }
  if (notes) body.notes = notes
  const { data } = await api.patch(`/events/${eventId}/status`, body)
  return data
}

export async function getEvent(eventId) {
  const { data } = await api.get(`/events/${eventId}`)
  return data
}

export async function getEventJourney(eventId) {
  const { data } = await api.get(`/events/${eventId}/journey`)
  return data
}

export async function addEventParticipant(eventId, body) {
  // Canonical home for the add-participant flow (Phase 6). Both admin
  // and sales tokens hit the same path; the deprecated alias under
  // `/sales/events/.../participants` is preserved server-side for one
  // rolling release but new code calls this helper.
  const { data } = await api.post(`/events/${eventId}/participants`, body)
  return data
}

export async function addSalesEventParticipant(eventId, body) {
  // Deprecated. Calls the legacy `/sales/...` alias which now delegates
  // to the canonical service. Kept only for any external integration
  // that may still target the old URL.
  const { data } = await api.post(`/sales/events/${eventId}/participants`, body)
  return data
}

export async function getEventWorkflow(eventType = 'vehicle_sale') {
  const { data } = await api.get(`/events/workflow/${eventType}`)
  return data
}

export async function archiveEvent(eventId, { reason, note } = {}) {
  const { data } = await api.post(
    `/admin/events/${eventId}/archive`,
    { reason, note: note ?? null },
  )
  return data
}

export async function restoreEvent(eventId) {
  const { data } = await api.post(`/admin/events/${eventId}/restore`)
  return data
}

export async function archiveEventParticipant(
  eventId,
  participantId,
  { reason, note } = {},
) {
  const { data } = await api.post(
    `/admin/events/${eventId}/participants/${participantId}/archive`,
    { reason, note: note ?? null },
  )
  return data
}

export async function restoreEventParticipant(eventId, participantId) {
  const { data } = await api.post(
    `/admin/events/${eventId}/participants/${participantId}/restore`,
  )
  return data
}

export async function archiveSpecialOrder(
  eventId,
  specialOrderId,
  { reason, note } = {},
) {
  const { data } = await api.post(
    `/admin/events/${eventId}/special-orders/${specialOrderId}/archive`,
    { reason, note: note ?? null },
  )
  return data
}

export async function restoreSpecialOrder(eventId, specialOrderId) {
  const { data } = await api.post(
    `/admin/events/${eventId}/special-orders/${specialOrderId}/restore`,
  )
  return data
}

export async function createWalkInLead(payload) {
  // POST writes contact + placeholder appointment + enrichment + event in
  // one transaction. Response shape:
  //   { contact: {id, display_name, ...},
  //     event: {id, event_name, status, event_date},
  //     appointment_id, was_new_contact }
  // Callers route to `/events/{event.id}/overview` on success.
  const { data } = await api.post('/walk-in-leads', payload)
  return data
}

export async function listEventActivity(eventId, { limit = 100, beforeId } = {}) {
  const params = { limit }
  if (beforeId != null) params.before_id = beforeId
  const { data } = await api.get(`/events/${eventId}/activity`, { params })
  return data
}

// ---------------------------------------------------------------------------
// Dashboard rollups (Phase 10)
// ---------------------------------------------------------------------------

// Phase 11: admin-side lead-owner reassignment. Same cascade rules as
// sales (future-dated appointments only, audit + notify per cascaded
// appt) — the route delegates to services/sales_assignment.py and tags
// the audit row with `reason: "admin_owner_change"`. Pass `null` to
// clear the owner.
export async function adminReassignEventOwner(eventId, ownerUserId) {
  const { data } = await api.patch(
    `/admin/events/${eventId}/owner`,
    { owner_user_id: ownerUserId },
  )
  return data
}

// Admin cascade preview — read-only, no floor gate, returns the same
// shape as the sales side. Used by the admin owner-change dialog to
// render the cascade list before the user confirms.
export async function adminGetOwnerCascadePreview(eventId) {
  const { data } = await api.get(`/admin/events/${eventId}/cascade-preview`)
  return data
}

// Admin twin of the above. Same shared service backing it; admin path
// has no attendance-gate and uses require_admin_scope.
export async function adminTagAppointmentParticipant(
  appointmentId,
  eventParticipantId,
) {
  const { data } = await api.patch(
    `/admin/booking/appointments/${appointmentId}/participant`,
    { event_participant_id: eventParticipantId },
  )
  return data
}
