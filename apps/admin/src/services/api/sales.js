import api from './client'

export async function salesGetClockStatus({ signal } = {}) {
  const { data } = await api.get('/sales/clock/status', { signal })
  return data
}

function _buildClockForm({ latitude, longitude, accuracy_m, selfieBlob }) {
  const form = new FormData()
  // Coords are optional: on the shop WiFi a staffer can punch
  // before (or without) a GPS fix. Only send them when we actually
  // have both — a half-set pair is treated server-side as no fix.
  if (
    latitude !== undefined &&
    latitude !== null &&
    longitude !== undefined &&
    longitude !== null
  ) {
    form.append('client_latitude', String(latitude))
    form.append('client_longitude', String(longitude))
    if (accuracy_m !== undefined && accuracy_m !== null) {
      form.append('client_accuracy_m', String(accuracy_m))
    }
  }
  if (selfieBlob) {
    // Filename is informative only; backend ignores it and trusts
    // content-type + Pillow decode. Use ".jpg" so Safari does not
    // pick a weird name.
    form.append('selfie', selfieBlob, 'selfie.jpg')
  }
  return form
}

export async function salesPunchIn(payload) {
  const { data } = await api.post('/sales/clock/in', _buildClockForm(payload))
  return data
}

export async function salesPunchOut(payload) {
  const { data } = await api.post('/sales/clock/out', _buildClockForm(payload))
  return data
}

// ---------------------------------------------------------------------------
// Sales Portal — Today's appointments (Phase 2)
// ---------------------------------------------------------------------------

export async function salesListAppointmentsToday({ mine = false } = {}) {
  const params = mine ? { mine: true } : {}
  const { data } = await api.get('/sales/appointments/today', { params })
  return data
}

export async function salesGetAppointmentDetail(appointmentId) {
  const { data } = await api.get(`/sales/appointments/${appointmentId}`)
  return data
}

export async function salesPostAppointmentStatus(appointmentId, action, notes) {
  const body = { action }
  if (notes) body.notes = notes
  const { data } = await api.post(
    `/sales/appointments/${appointmentId}/status`,
    body,
  )
  return data
}

export async function salesPatchAppointmentNotes(appointmentId, internalNotes) {
  const { data } = await api.patch(
    `/sales/appointments/${appointmentId}/notes`,
    { internal_notes: internalNotes },
  )
  return data
}

// Catalog search (dual-scope GET; sales staff search the same fields
// admins do). Lightweight wrapper over the existing /api/catalog list.
export async function searchCatalogForSales({ q, limit = 25 } = {}) {
  const params = { limit }
  if (q && q.trim()) params.q = q.trim()
  const { data } = await api.get('/catalog', { params })
  return data
}

// ---------------------------------------------------------------------------
// Sales Portal — PIN auth (Phase 1)
// ---------------------------------------------------------------------------

export async function salesPinLogin(identifier, pin) {
  const { data } = await api.post('/sales/auth/pin', { identifier, pin })
  return data
}

export async function salesGetStaffPicker() {
  // Returns [{username, full_name}] of active sales users who have
  // a PIN minted. Used by the kiosk-style PIN login picker so a
  // stylist can tap their name instead of typing their username.
  const { data } = await api.get('/sales/auth/staff-picker')
  return data
}

export async function salesGetMe() {
  const { data } = await api.get('/sales/auth/me')
  return data
}

// D2: sales-side server logout. Mirrors `logout()` for the admin path —
// bumps users.token_version so the just-used PIN-session JWT becomes
// 401 on every subsequent request. Caller should not block local
// state cleanup on the response.
export async function salesLogout() {
  await api.post('/sales/auth/logout')
}

// Kiosk quick-lock: clears the sales session + CSRF cookies on this
// device only. Unlike salesLogout it does NOT bump token_version, so
// the stylist stays signed in on their other devices. Used by the
// shared-tablet "Lock / Switch" button and the idle auto-lock timer.
export async function salesKioskLock() {
  await api.post('/sales/auth/kiosk-lock')
}

// Sales-portal lead search. Parallel to the admin /api/search; never
// returns invoice or quote rows. `signal` lets callers abort an
// in-flight request when the query changes.
export async function salesSearchLeads({ q, limit, signal } = {}) {
  const { data } = await api.get('/sales/search/leads', {
    params: { q, limit },
    signal,
  })
  return data
}

// Sales-portal walk-in capture. Body shape mirrors the admin
// /api/walk-in-leads endpoint plus an optional `assigned_user_id`
// (server defaults to the punched-in stylist when omitted).
export async function salesCreateWalkIn(body) {
  const { data } = await api.post('/sales/walk-ins', body)
  return data
}

// Active sales users that can be picked as an assignee. Read-only,
// no attendance gate, so the dropdown works for off-shift stylists
// planning ahead.
export async function salesListAssignableStaff() {
  const { data } = await api.get('/sales/staff/assignable')
  return data
}

// Reassign a single appointment. Pass `null` to unassign.
export async function salesAssignAppointment(appointmentId, assignedUserId) {
  const { data } = await api.patch(
    `/sales/appointments/${appointmentId}/assignment`,
    { assigned_user_id: assignedUserId },
  )
  return data
}

// Reassign a lead (event). Cascades onto every appointment for this
// event with slot_start_at >= NOW(). Past appointments stay frozen.
export async function salesAssignLead(eventId, ownerUserId) {
  const { data } = await api.patch(
    `/sales/leads/${eventId}/assignment`,
    { owner_user_id: ownerUserId },
  )
  return data
}

// Read-only preview of the future appointments a lead reassignment
// would cascade onto. Same cutoff as the PATCH (slot_start_at >= NOW()),
// ordered ascending. Used by the assignment dialog to show the cascade
// scope before the user confirms.
export async function salesGetLeadCascadePreview(eventId) {
  const { data } = await api.get(`/sales/leads/${eventId}/cascade-preview`)
  return data
}

// Tag this appointment to a specific event_participant — the buyer
// journey link (Phase 10.3a). Pass `null` to clear.
export async function salesTagAppointmentParticipant(
  appointmentId,
  eventParticipantId,
) {
  const { data } = await api.patch(
    `/sales/appointments/${appointmentId}/participant`,
    { event_participant_id: eventParticipantId },
  )
  return data
}

export async function salesChangePin(currentPin, newPin) {
  await api.post('/sales/auth/change-pin', {
    current_pin: currentPin,
    new_pin: newPin,
  })
}

// ---------------------------------------------------------------------------
// Owner-side sales-staff management (admin-scope only)
// ---------------------------------------------------------------------------

export async function salesConfirmMyPunch(punchId) {
  const { data } = await api.post(
    `/sales/attendance/punches/${punchId}/confirm`,
    {},
  )
  return data
}

export async function salesListMyCorrectionRequests() {
  const { data } = await api.get('/sales/attendance/correction-requests')
  return data
}

export async function salesSubmitCorrectionRequest(body) {
  // body: { punch_id?, requested_check_in_at?, requested_check_out_at?, reason }
  const { data } = await api.post(
    '/sales/attendance/correction-requests',
    body,
  )
  return data
}

export async function salesCancelCorrectionRequest(requestId) {
  const { data } = await api.post(
    `/sales/attendance/correction-requests/${requestId}/cancel`,
  )
  return data
}

// ---------------------------------------------------------------------------
// Phase 8 Slice D: schedule + time-off (sales surface)
// ---------------------------------------------------------------------------

export async function salesGetSchedule({ from_date, to_date }) {
  const { data } = await api.get('/sales/schedule', {
    params: { from_date, to_date },
  })
  return data
}

export async function salesGetTeamSchedule({ from_date, to_date }) {
  // Phase 10 Slice 5: coworker-visible weekly schedule. Response
  // shape is `{ from_date, to_date, viewer_user_id, entries: [...] }`
  // where each entry exposes ONLY user_id/username/full_name and
  // entry_id/business_date/starts_at_local/ends_at_local — no
  // manager_notes, no attendance_status. Privacy is enforced server-
  // side; this helper just forwards.
  const { data } = await api.get('/sales/schedule/team', {
    params: { from_date, to_date },
  })
  return data
}

// Phase 10 Slice 6 (Epic 3.4): recurring stylist unavailability.
// Self-serve from the stylist's portal — no admin approval flow.
export async function salesListMyAvailability({ includeExpired = false } = {}) {
  const { data } = await api.get('/sales/schedule/availability', {
    params: { include_expired: includeExpired },
  })
  return data
}

export async function salesCreateAvailability(body) {
  const { data } = await api.post('/sales/schedule/availability', body)
  return data
}

export async function salesPatchAvailability(blockId, body) {
  const { data } = await api.patch(
    `/sales/schedule/availability/${blockId}`,
    body,
  )
  return data
}

export async function salesDeleteAvailability(blockId) {
  await api.delete(`/sales/schedule/availability/${blockId}`)
}

export async function salesListMyTimeOff() {
  const { data } = await api.get('/sales/time-off')
  return data
}

export async function salesSubmitTimeOff(body) {
  const { data } = await api.post('/sales/time-off', body)
  return data
}

export async function salesCancelTimeOff(requestId) {
  const { data } = await api.post(`/sales/time-off/${requestId}/cancel`)
  return data
}

// ---------------------------------------------------------------------------
// Scheduling Phase 1: staff shift requests (cover/drop/swap). Read-only
// queue plus the two transitions staff drive themselves (create/cancel);
// approval lands in Phase 2.
// ---------------------------------------------------------------------------

export async function salesListMyShiftRequests() {
  const { data } = await api.get('/sales/schedule/shift-requests')
  return data
}

export async function salesCreateShiftRequest(body) {
  const { data } = await api.post('/sales/schedule/shift-requests', body)
  return data
}

export async function salesCancelShiftRequest(requestId) {
  const { data } = await api.post(
    `/sales/schedule/shift-requests/${requestId}/cancel`,
  )
  return data
}

export async function salesAcceptShiftRequest(requestId) {
  const { data } = await api.post(
    `/sales/schedule/shift-requests/${requestId}/accept`,
  )
  return data
}

export async function salesDeclineShiftRequest(requestId) {
  const { data } = await api.post(
    `/sales/schedule/shift-requests/${requestId}/decline`,
  )
  return data
}

// Scheduling Phase 3: open-shift pickup board (staff-facing).
export async function salesListOpenShifts({ from_date, to_date }) {
  const { data } = await api.get('/sales/schedule/open-shifts', {
    params: { from_date, to_date },
  })
  return data
}

export async function salesClaimOpenShift(postId) {
  const { data } = await api.post(
    `/sales/schedule/open-shifts/${postId}/claim`,
  )
  return data
}

// ---------------------------------------------------------------------------
// Sales-portal notification preferences (B2.5).
// ---------------------------------------------------------------------------

export async function salesListNotificationPreferences() {
  const { data } = await api.get('/sales/me/notifications/preferences')
  return data
}

export async function salesUpdateNotificationPreferences(updates) {
  // updates: [{event_kind, enabled}, ...]
  const { data } = await api.put('/sales/me/notifications/preferences', {
    updates,
  })
  return data
}

// ---------------------------------------------------------------------------
// Phase 8 Slice D: shift + override + holiday admin + time-off review
// ---------------------------------------------------------------------------
