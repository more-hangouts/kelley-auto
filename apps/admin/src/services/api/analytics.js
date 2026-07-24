import api from './client'

// Phase 14: sales activity monitoring (admin-scope). `range` is
// 'today' | 'yesterday' | 'week'. Returns { since, until, reps: [...] }.
export async function getSalesActivitySummary(params = {}) {
  const { data } = await api.get('/admin/sales-activity/summary', { params })
  return data
}

// Storefront analytics rollup (admin-scope): funnel, traffic/leads/revenue
// by channel, shop-local daily series, most-viewed vehicles.
export async function getStorefrontAnalyticsSummary(days = 30) {
  const { data } = await api.get('/admin/storefront-analytics/summary', {
    params: { days },
  })
  return data
}

// Recent activity rows for one rep. Returns { actor_user_id, rows, next_before_id }.
export async function getSalesActivityRepRecent(userId, params = {}) {
  const { data } = await api.get(`/admin/sales-activity/rep/${userId}/recent`, {
    params,
  })
  return data
}
