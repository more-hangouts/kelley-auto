import api from './client'

// D1 of the CRM record deletion plan. Read-only preview the future
// archive/restore confirm modal renders before any destructive action.
// Returns active/deleted counts per inbound relationship, product-level
// block reasons, and short sample titles. Supported entity_type values
// are 'contact', 'event', 'event_participant', 'special_order'.
export async function getRecordDependencies(entityType, entityId) {
  const { data } = await api.get(
    `/admin/dependencies/${entityType}/${entityId}`,
  )
  return data
}

// D3-D2: paginated list of archived rows for one entity type.
// Returns {entity_type, items: [...], next_before_id: number|null}.
// Each item carries display_name + secondary_label + audit metadata
// and, for participant / special_order, parent_event_id so the
// nested restore route can be called.
export async function listRecycleBin({
  entityType,
  beforeId,
  pageSize = 25,
  since,
  until,
  deletedByUserId,
} = {}) {
  const params = { entity_type: entityType, page_size: pageSize }
  if (beforeId != null) params.before_id = beforeId
  if (since) params.since = since
  if (until) params.until = until
  if (deletedByUserId != null) params.deleted_by_user_id = deletedByUserId
  const { data } = await api.get('/admin/recycle-bin', { params })
  return data
}

// Global Search Phase 2. Returns { query, results: [{type, id, label,
// sublabel, score, route}, ...] }. The `signal` lets React Query
// cancel in-flight requests when the debounced query supersedes
// itself; the backend cap means each call is small and bounded.
export async function searchGlobal({ q, types, limit, signal } = {}) {
  const params = { q }
  if (types && types.length) params.types = types.join(',')
  if (limit) params.limit = limit
  const { data } = await api.get('/search', { params, signal })
  return data
}

// ---------------------------------------------------------------------------
// Sales Portal — Clock-in (Phase 7)
// ---------------------------------------------------------------------------
