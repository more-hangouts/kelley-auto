import api from './client'

export async function listAttendancePunches(params) {
  // params: { range_key | from_date+to_date, staff_user_id?, review_queue_only? }
  const { data } = await api.get('/admin/attendance/punches', { params })
  return data
}

export async function listAttendanceTotals(params) {
  // params: { range_key | from_date+to_date, bucket? = 'day' | 'week' | 'biweek' | 'month' }
  const { data } = await api.get('/admin/attendance/totals', { params })
  return data
}

export async function downloadAttendanceTotalsCsv(params = {}) {
  // CSV is owner-only and authenticated, so we can't use a plain
  // <a download href>. Pull the bytes via axios with the bearer
  // header, build a Blob URL, and fire a synthetic click.
  const response = await api.get('/admin/attendance/totals/export.csv', {
    params,
    responseType: 'blob',
  })
  const dispo = response.headers['content-disposition'] || ''
  const match = dispo.match(/filename="([^"]+)"/)
  const filename = match ? match[1] : 'attendance.csv'
  const url = URL.createObjectURL(response.data)
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  document.body.appendChild(a)
  a.click()
  document.body.removeChild(a)
  URL.revokeObjectURL(url)
}

export async function confirmAttendancePunch(punchId) {
  const { data } = await api.post(
    `/admin/attendance/punches/${punchId}/confirm`,
    {},
  )
  return data
}

export async function adjustAttendancePunch(punchId, body) {
  // body: { new_punched_at: ISO string, reason }
  const { data } = await api.post(
    `/admin/attendance/punches/${punchId}/adjust`,
    body,
  )
  return data
}

export async function voidAttendancePunch(punchId, reason) {
  const { data } = await api.post(
    `/admin/attendance/punches/${punchId}/void`,
    { reason },
  )
  return data
}

export async function listOpenSessions() {
  const { data } = await api.get('/admin/attendance/open-sessions')
  return data
}

export async function adminClockOutPunch(punchId, reason) {
  // punchId is the staffer's open in-punch. reason is optional.
  const { data } = await api.post(
    `/admin/attendance/punches/${punchId}/clock-out`,
    { reason: reason || null },
  )
  return data
}

export async function clockEveryoneOut(reason) {
  const { data } = await api.post('/admin/attendance/clock-everyone-out', {
    reason: reason || null,
  })
  return data
}

export async function listAttendanceCorrectionRequests(params = {}) {
  const { data } = await api.get('/admin/attendance/correction-requests', {
    params,
  })
  return data
}

export async function getCronHealth() {
  const { data } = await api.get('/admin/cron-health')
  return data
}

export async function decideAttendanceCorrectionRequest(requestId, body) {
  // body: { status: 'approved' | 'denied', decision_notes? }
  const { data } = await api.post(
    `/admin/attendance/correction-requests/${requestId}/decide`,
    body,
  )
  return data
}

// ---------------------------------------------------------------------------
// Stylist correction + confirmation (sales scope)
// ---------------------------------------------------------------------------
