import api from './client'

export async function getArSummary() {
  const { data } = await api.get('/dashboard/ar-summary')
  return data
}

export async function getRecentPayments(limit = 10) {
  const { data } = await api.get('/dashboard/recent-payments', {
    params: { limit },
  })
  return data.payments
}

export async function getAwaitingSignatureQuotes({ minAgeDays = 3, limit = 25 } = {}) {
  const { data } = await api.get('/dashboard/awaiting-signature', {
    params: { min_age_days: minAgeDays, limit },
  })
  return data.quotes
}

export async function getAgendaToday() {
  const { data } = await api.get('/dashboard/agenda-today')
  return data
}

export async function getPipelineCounts(eventType = 'vehicle_sale') {
  const { data } = await api.get('/dashboard/pipeline-counts', {
    params: { event_type: eventType },
  })
  return data
}

export async function getSplhLeaderboard({ fromDate, toDate, limit = 10 } = {}) {
  const params = { limit }
  if (fromDate) params.from_date = fromDate
  if (toDate) params.to_date = toDate
  const { data } = await api.get('/dashboard/splh-leaderboard', { params })
  return data
}

// ---------------------------------------------------------------------------
// Catalog (Phase 3 line-item picker)
// ---------------------------------------------------------------------------
