import api from './client'

export async function listAdminShifts(params = {}) {
  const { data } = await api.get('/admin/shifts', { params })
  return data
}

export async function createAdminShift(body) {
  const { data } = await api.post('/admin/shifts', body)
  return data
}

export async function patchAdminShift(shiftId, body) {
  const { data } = await api.patch(`/admin/shifts/${shiftId}`, body)
  return data
}

export async function deleteAdminShift(shiftId) {
  await api.delete(`/admin/shifts/${shiftId}`)
}

export async function listAdminShiftOverlaps(params) {
  const { data } = await api.get('/admin/shifts/overlaps', { params })
  return data
}

export async function listAdminShiftOverrides(params = {}) {
  const { data } = await api.get('/admin/shift-overrides', { params })
  return data
}

export async function createAdminShiftOverride(body) {
  const { data } = await api.post('/admin/shift-overrides', body)
  return data
}

export async function deleteAdminShiftOverride(overrideId) {
  await api.delete(`/admin/shift-overrides/${overrideId}`)
}

export async function listAdminHolidays(params = {}) {
  const { data } = await api.get('/admin/holidays', { params })
  return data
}

export async function createAdminHoliday(body) {
  const { data } = await api.post('/admin/holidays', body)
  return data
}

export async function patchAdminHoliday(holidayId, body) {
  const { data } = await api.patch(`/admin/holidays/${holidayId}`, body)
  return data
}

export async function deleteAdminHoliday(holidayId) {
  await api.delete(`/admin/holidays/${holidayId}`)
}

export async function listAdminStaffLocations() {
  const { data } = await api.get('/admin/staff-locations')
  return data
}

export async function createAdminStaffLocation(body) {
  const { data } = await api.post('/admin/staff-locations', body)
  return data
}

export async function patchAdminStaffLocation(locationId, body) {
  const { data } = await api.patch(`/admin/staff-locations/${locationId}`, body)
  return data
}

export async function deleteAdminStaffLocation(locationId) {
  await api.delete(`/admin/staff-locations/${locationId}`)
}

export async function testStaffLocationGeofence(locationId, body) {
  const { data } = await api.post(
    `/admin/staff-locations/${locationId}/test-geofence`,
    body,
  )
  return data
}

export async function listAdminTimeOff(params) {
  const { data } = await api.get('/admin/time-off', { params })
  return data
}

export async function decideAdminTimeOff(requestId, body) {
  const { data } = await api.post(
    `/admin/time-off/${requestId}/decide`,
    body,
  )
  return data
}

export async function amendAdminTimeOff(requestId, body) {
  const { data } = await api.post(
    `/admin/time-off/${requestId}/amend`,
    body,
  )
  return data
}

// Scheduling Phase 1/2: owner shift-request queue + approval.
export async function listAdminShiftRequests(params) {
  const { data } = await api.get('/admin/schedule/shift-requests', { params })
  return data
}

export async function getAdminShiftRequest(requestId) {
  const { data } = await api.get(
    `/admin/schedule/shift-requests/${requestId}`,
  )
  return data
}

export async function decideAdminShiftRequest(requestId, body) {
  const { data } = await api.post(
    `/admin/schedule/shift-requests/${requestId}/decide`,
    body,
  )
  return data
}

// Scheduling Phase 3: admin open-shift management.
export async function listAdminOpenShifts(params) {
  const { data } = await api.get('/admin/schedule/open-shifts', { params })
  return data
}

export async function createAdminOpenShift(body) {
  const { data } = await api.post('/admin/schedule/open-shifts', body)
  return data
}

export async function cancelAdminOpenShift(postId) {
  const { data } = await api.post(
    `/admin/schedule/open-shifts/${postId}/cancel`,
  )
  return data
}

// ---------------------------------------------------------------------------
// Phase 10 — per-day published schedule (manager grid + attendance cards)
// ---------------------------------------------------------------------------

// FastAPI parses repeated keys (`?user_ids=1&user_ids=2`) as a `list[int]`.
// Axios's default array serializer emits `user_ids[]=...` which FastAPI
// reads as a single key. Build a URLSearchParams that emits the
// repeated-key form so the backend deserializer is happy.
function _scheduleParams({ week_start, from_date, to_date, user_id, user_ids }) {
  const out = new URLSearchParams()
  if (week_start) out.set('week_start', week_start)
  if (from_date) out.set('from_date', from_date)
  if (to_date) out.set('to_date', to_date)
  if (user_id !== undefined && user_id !== null) {
    out.set('user_id', String(user_id))
  }
  if (Array.isArray(user_ids)) {
    for (const id of user_ids) out.append('user_ids', String(id))
  }
  return out
}

export async function getAdminScheduleWeek({ week_start, user_ids }) {
  const { data } = await api.get('/admin/schedule/week', {
    params: _scheduleParams({ week_start, user_ids }),
  })
  return data
}

export async function createScheduleEntry(body) {
  const { data } = await api.post('/admin/schedule/entries', body)
  return data
}

export async function patchScheduleEntry(entryId, body) {
  const { data } = await api.patch(
    `/admin/schedule/entries/${entryId}`,
    body,
  )
  return data
}

export async function deleteScheduleEntry(entryId) {
  await api.delete(`/admin/schedule/entries/${entryId}`)
}

export async function publishScheduleWeek(body) {
  const { data } = await api.post('/admin/schedule/publish', body)
  return data
}

export async function resendPublishedScheduleWeek(weekStart, body = {}) {
  // B2.4: re-send the staff.schedule_published email for every staffer
  // with a published shift in `weekStart` (ISO yyyy-mm-dd, must be a
  // Monday). Body may carry { user_ids: [...] } to narrow the fan-out.
  const { data } = await api.post(
    `/admin/schedule/weeks/${weekStart}/resend-published`,
    body,
  )
  return data
}

export async function publishScheduleEntry(entryId) {
  // Publish a single draft entry — companion to publishScheduleWeek
  // for the grid's per-entry "Publish shift" affordance. Same
  // backend conflict semantics; the caller handles
  // time_off_conflict / entry_already_published / entry_not_found.
  const { data } = await api.post(
    `/admin/schedule/entries/${entryId}/publish`,
  )
  return data
}

export async function setScheduleEntryNotes(entryId, notes) {
  const { data } = await api.post(
    `/admin/schedule/entries/${entryId}/notes`,
    { notes },
  )
  return data
}

export async function excuseScheduleEntry(entryId, notes) {
  const { data } = await api.post(
    `/admin/schedule/entries/${entryId}/excuse`,
    { notes },
  )
  return data
}

export async function resolveMissingOutPunch(entryId, body) {
  // body: { out_at_local: ISO string, notes?: string }
  const { data } = await api.post(
    `/admin/schedule/entries/${entryId}/resolve-missing-out`,
    body,
  )
  return data
}

export async function listFlaggedExceptions({ from_date, to_date, user_id }) {
  const { data } = await api.get('/admin/schedule/flagged-exceptions', {
    params: _scheduleParams({ from_date, to_date, user_id }),
  })
  return data
}

export async function getHoursVariance({ from_date, to_date, user_id }) {
  const { data } = await api.get('/admin/schedule/variance', {
    params: _scheduleParams({ from_date, to_date, user_id }),
  })
  return data
}

// ---------------------------------------------------------------------------
// Schedule shift presets (Phase 10 Slice 3) — backs the manager grid's
// "Preset" dropdown and the admin /settings/staff/schedule/presets page.
// ---------------------------------------------------------------------------

export async function listSchedulePresets({ includeArchived = false } = {}) {
  const { data } = await api.get('/admin/schedule/presets', {
    params: includeArchived ? { include_archived: true } : undefined,
  })
  return data
}

export async function createSchedulePreset(body) {
  const { data } = await api.post('/admin/schedule/presets', body)
  return data
}

export async function patchSchedulePreset(presetId, body) {
  const { data } = await api.patch(
    `/admin/schedule/presets/${presetId}`,
    body,
  )
  return data
}

export async function archiveSchedulePreset(presetId) {
  const { data } = await api.delete(
    `/admin/schedule/presets/${presetId}`,
  )
  return data
}

export async function getAutoScheduleRules() {
  const { data } = await api.get('/admin/schedule/auto-schedule/rules')
  return data
}

export async function generateDraftScheduleWeek({ week_start, overrides }) {
  const body = { week_start }
  if (overrides && Object.keys(overrides).length > 0) {
    body.overrides = overrides
  }
  const { data } = await api.post(
    '/admin/schedule/generate-draft-week',
    body,
  )
  return data
}

// ─── Notification subscribers ("who gets what"; Omnichannel Inbox Plan Part 1)
