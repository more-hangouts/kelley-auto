import api from './client'

export async function listAppointments(params) {
  const { data } = await api.get('/admin/booking/appointments', { params })
  return data
}

export async function listAvailabilityRules() {
  const { data } = await api.get('/admin/booking/availability/rules')
  return data
}

export async function createAvailabilityRule(body) {
  const { data } = await api.post('/admin/booking/availability/rules', body)
  return data
}

export async function updateAvailabilityRule(id, body) {
  const { data } = await api.patch(`/admin/booking/availability/rules/${id}`, body)
  return data
}

export async function deleteAvailabilityRule(id) {
  await api.delete(`/admin/booking/availability/rules/${id}`)
}

export async function listBlackouts() {
  const { data } = await api.get('/admin/booking/blackouts')
  return data
}

// Staff-created appointment (migration 104). Books a real future slot from
// the CRM — a deal, or a contact with no deal yet. 409s carry
// {code:'slot_conflict', conflicts:[...]}; a 201 may still carry advisory
// `warnings` (outside published hours, shared capacity in use).
export async function createStaffAppointment(body) {
  const { data } = await api.post('/admin/booking/appointments', body)
  return data
}

export async function createBlackout(body) {
  const { data } = await api.post('/admin/booking/blackouts', body)
  return data
}

export async function deleteBlackout(id) {
  await api.delete(`/admin/booking/blackouts/${id}`)
}

export async function getWidgetSettings() {
  const { data } = await api.get('/admin/booking/settings')
  return data
}

export async function updateWidgetSettings(body) {
  const { data } = await api.put('/admin/booking/settings', body)
  return data
}

// ---------------------------------------------------------------------------
// Event documents
// ---------------------------------------------------------------------------
