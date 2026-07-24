import api from './client'

export async function listSalesStaff({ archived = false } = {}) {
  const { data } = await api.get('/admin/sales-staff', {
    params: { archived },
  })
  return data
}

export async function archiveSalesStaff(userId, body = {}) {
  const { data } = await api.post(
    `/admin/sales-staff/${userId}/archive`,
    body,
  )
  return data
}

export async function restoreSalesStaff(userId) {
  const { data } = await api.post(`/admin/sales-staff/${userId}/restore`)
  return data
}

export async function createSalesStaff(body) {
  const { data } = await api.post('/admin/sales-staff', body)
  return data
}

export async function patchSalesStaff(userId, body) {
  const { data } = await api.patch(`/admin/sales-staff/${userId}`, body)
  return data
}

export async function mintSalesPin(userId) {
  const { data } = await api.post(`/admin/sales-staff/${userId}/pin`)
  return data
}

export async function clearSalesPin(userId) {
  await api.delete(`/admin/sales-staff/${userId}/pin`)
}

export async function unlockSalesStaff(userId) {
  const { data } = await api.post(`/admin/sales-staff/${userId}/unlock`)
  return data
}

export async function sendStaffPasswordReset(userId) {
  await api.post(`/admin/staff/${userId}/send-password-reset`)
}

// ---------------------------------------------------------------------------
// Owner attendance review (admin scope, Phase 7 Slice 2B-2)
// ---------------------------------------------------------------------------
