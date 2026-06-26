import axios from 'axios'

// D3: cookie-name constants used by the CSRF interceptor. Mirrors
// api/cookies.py — keep in sync. The __Secure- prefix is a browser-
// enforced contract that the cookie MUST be set with Secure (HTTPS
// only), which matches our production-only target.
const ADMIN_CSRF_COOKIE = '__Secure-kelley_autoplex_csrf'
const SALES_CSRF_COOKIE = '__Secure-kelley_autoplex_sales_csrf'

// Surface detection. Sales lives at `sales.kelleyautoplex.com`; admin
// at `admin.kelleyautoplex.com` (or anything else, including localhost).
// The VITE_FORCE_SUBDOMAIN escape hatch lets a dev hit the sales tree
// on localhost without DNS — set it to `sales` in .env.local.
//
// The trailing dot in `startsWith('sales.')` is load-bearing: it
// keeps a future `salesreports.kelleyautoplex.com` (or any other
// `sales*` host) from accidentally routing into the sales app.
export function isSalesSubdomain() {
  if (typeof window === 'undefined') return false
  if (import.meta.env?.VITE_FORCE_SUBDOMAIN === 'sales') return true
  return window.location.hostname.startsWith('sales.')
}

function readCookie(name) {
  if (typeof document === 'undefined') return null
  const target = name + '='
  for (const part of document.cookie.split(';')) {
    const trimmed = part.trim()
    if (trimmed.startsWith(target)) {
      return decodeURIComponent(trimmed.slice(target.length))
    }
  }
  return null
}

const _UNSAFE_METHODS = new Set(['post', 'put', 'patch', 'delete'])

const api = axios.create({
  baseURL: import.meta.env.VITE_API_URL,
  // D3: send + accept the HttpOnly session + readable CSRF cookies
  // set by /api/auth/login and /api/sales/auth/pin. Without this,
  // axios would not attach cookies on cross-subdomain requests
  // (admin → api / sales → api), so every authenticated call would
  // 401.
  withCredentials: true,
})

api.interceptors.request.use((config) => {
  // D3: the JWT itself rides in an HttpOnly cookie that JS cannot
  // read; the browser attaches it automatically. The only header
  // the JS still has to set is X-CSRF-Token on unsafe methods,
  // mirroring the readable CSRF cookie. The backend CSRF middleware
  // verifies the cookie/header pair before the request reaches the
  // route handler.
  const method = (config.method || 'get').toLowerCase()
  if (_UNSAFE_METHODS.has(method)) {
    const cookieName = isSalesSubdomain() ? SALES_CSRF_COOKIE : ADMIN_CSRF_COOKIE
    const csrf = readCookie(cookieName)
    if (csrf) {
      config.headers['X-CSRF-Token'] = csrf
    }
  }
  return config
})

api.interceptors.response.use(
  (response) => response,
  (error) => {
    if (error.response?.status === 401) {
      // D3: nothing to clear locally — the session lives in cookies
      // the server controls. Just bounce the user to /login so the
      // auth flow re-runs. Same path for admin and sales SPAs.
      if (window.location.pathname !== '/login') {
        window.location.assign('/login')
      }
    }
    return Promise.reject(error)
  },
)

export async function login(email, password) {
  const { data } = await api.post('/auth/login', { email, password })
  return data
}

export async function getMe() {
  const { data } = await api.get('/auth/me')
  return data
}

// D2: server-side logout bumps users.token_version so every token
// previously minted for this user becomes 401 on next request. Callers
// should not block local-state cleanup on the response — if the
// network drops, we still want the client to clear its stored token.
export async function logout() {
  await api.post('/auth/logout')
}

export async function changeOwnAdminPassword(currentPassword, newPassword) {
  await api.post('/admin/me/change-password', {
    current_password: currentPassword,
    new_password: newPassword,
  })
}

export async function listAppointments(params) {
  const { data } = await api.get('/admin/booking/appointments', { params })
  return data
}

export async function getEventBoard(eventType = 'quinceanera') {
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

export async function getEventWorkflow(eventType = 'quinceanera') {
  const { data } = await api.get(`/events/workflow/${eventType}`)
  return data
}

export async function getContact(contactId) {
  const { data } = await api.get(`/contacts/${contactId}`)
  return data
}

export async function updateContact(contactId, patch) {
  const { data } = await api.patch(`/contacts/${contactId}`, patch)
  return data
}

export async function createContact(payload) {
  const { data } = await api.post('/contacts', payload)
  return data
}

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

// D3 of the CRM record deletion plan. Admin archive/restore verbs.
// Each `archive*` helper expects {reason, note?}; restore helpers take
// no body. Status mapping is documented in api/routers/admin_archive.py.
export async function archiveContact(contactId, { reason, note } = {}) {
  const { data } = await api.post(
    `/admin/contacts/${contactId}/archive`,
    { reason, note: note ?? null },
  )
  return data
}

export async function restoreContact(contactId) {
  const { data } = await api.post(`/admin/contacts/${contactId}/restore`)
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

export async function listEventDocuments(eventId, kind) {
  const params = kind ? { kind } : undefined
  const { data } = await api.get(`/events/${eventId}/documents`, { params })
  return data.documents
}

export async function getDocumentCounts(eventId) {
  const { data } = await api.get(`/events/${eventId}/document-counts`)
  return data
}

export async function uploadEventDocument({
  eventId,
  file,
  kind,
  label,
  linkedInvoiceId,
  onProgress,
}) {
  const form = new FormData()
  form.append('file', file)
  form.append('kind', kind)
  if (label) form.append('label', label)
  if (linkedInvoiceId != null) {
    form.append('linked_invoice_id', String(linkedInvoiceId))
  }
  const { data } = await api.post(`/events/${eventId}/documents`, form, {
    onUploadProgress: (e) => {
      if (!onProgress || !e.total) return
      onProgress(Math.round((e.loaded * 100) / e.total))
    },
  })
  return data
}

export async function patchDocument(documentId, body) {
  const { data } = await api.patch(`/documents/${documentId}`, body)
  return data
}

export async function deleteDocument(documentId) {
  await api.delete(`/documents/${documentId}`)
}

// Triggers a browser download via a blob fetch so the Authorization header
// rides along — a plain <a href> would hit the API unauthenticated.
export async function downloadDocument(documentId, filename) {
  const resp = await api.get(`/documents/${documentId}/download`, {
    responseType: 'blob',
  })
  const url = URL.createObjectURL(resp.data)
  const a = document.createElement('a')
  a.href = url
  a.download = filename || 'download'
  document.body.appendChild(a)
  a.click()
  a.remove()
  URL.revokeObjectURL(url)
}

// Opens the document inline in a new tab (PDF/image preview, browser-native
// download/print controls). The window.open call has to run synchronously
// before any await so the browser keeps treating it as a user-initiated tab,
// otherwise popup blockers fire. We swap the URL once the blob arrives.
export async function viewDocument(documentId) {
  const win = window.open('', '_blank')
  if (!win) {
    const err = new Error('popup_blocked')
    err.code = 'popup_blocked'
    throw err
  }
  try {
    const resp = await api.get(`/documents/${documentId}/download`, {
      params: { disposition: 'inline' },
      responseType: 'blob',
    })
    const url = URL.createObjectURL(resp.data)
    win.location.href = url
    // Keep the blob alive long enough for the new tab to load it. Revoking
    // immediately would break the just-opened tab.
    setTimeout(() => URL.revokeObjectURL(url), 60_000)
  } catch (err) {
    win.close()
    throw err
  }
}

// ---------------------------------------------------------------------------
// Invoices (Phase 2 backend)
// ---------------------------------------------------------------------------

export async function listInvoices(eventId, options = {}) {
  const params = {}
  if (options.status) params.status = options.status
  if (options.includeDeleted) params.include_deleted = true
  const { data } = await api.get(`/events/${eventId}/invoices`, { params })
  return data.invoices
}

export async function getInvoice(invoiceId) {
  const { data } = await api.get(`/invoices/${invoiceId}`)
  return data
}

export async function createInvoice(eventId, body) {
  const { data } = await api.post(`/events/${eventId}/invoices`, body)
  return data
}

export async function updateInvoice(invoiceId, patch) {
  const { data } = await api.patch(`/invoices/${invoiceId}`, patch)
  return data
}

export async function sendInvoice(invoiceId) {
  const { data } = await api.post(`/invoices/${invoiceId}/send`)
  return data
}

export async function resendInvoice(invoiceId, contactIds) {
  const { data } = await api.post(`/invoices/${invoiceId}/resend`, {
    contact_ids: contactIds,
  })
  return data
}

export async function cancelInvoice(invoiceId, reason) {
  const body = reason ? { reason } : {}
  const { data } = await api.post(`/invoices/${invoiceId}/cancel`, body)
  return data
}

export async function deleteInvoice(invoiceId) {
  await api.delete(`/invoices/${invoiceId}`)
}

// PDF view/download/retry. The bytes have to come through axios so the
// browser sends the HttpOnly session cookie; a plain <a href> would hit
// the API unauthenticated. Caller decides whether to view (new tab) or
// save (download attribute on a synthetic <a>).
export async function viewInvoicePdf(invoiceId) {
  // Open a tab synchronously to keep the browser treating this as
  // user-initiated, then swap the URL once the blob arrives. Mirrors
  // viewDocument() above.
  const win = window.open('', '_blank')
  try {
    const resp = await api.get(`/invoices/${invoiceId}/pdf`, {
      responseType: 'blob',
    })
    const url = URL.createObjectURL(resp.data)
    if (win) {
      win.location.href = url
    } else {
      window.location.href = url
    }
    // Don't revoke immediately; the new tab needs the URL until it
    // finishes painting. Setting a timeout is the standard pattern.
    setTimeout(() => URL.revokeObjectURL(url), 60_000)
  } catch (e) {
    if (win) win.close()
    throw e
  }
}

export async function retryInvoicePdf(invoiceId) {
  const { data } = await api.post(`/invoices/${invoiceId}/pdf/retry`)
  return data
}

export async function searchInvoices(params = {}) {
  const out = {}
  if (params.q) out.q = params.q
  if (params.status) out.status = params.status
  if (params.eventId != null) out.event_id = params.eventId
  if (params.dateFrom) out.date_from = params.dateFrom
  if (params.dateTo) out.date_to = params.dateTo
  if (params.includeDeleted) out.include_deleted = true
  if (params.limit) out.limit = params.limit
  const { data } = await api.get('/invoices', { params: out })
  return data.invoices
}

// ---------------------------------------------------------------------------
// Quotes (Phase 5 backend)
// ---------------------------------------------------------------------------

export async function listQuotes(eventId, options = {}) {
  const params = {}
  if (options.status) params.status = options.status
  if (options.includeDeleted) params.include_deleted = true
  const { data } = await api.get(`/events/${eventId}/quotes`, { params })
  return data.quotes
}

export async function getQuote(quoteId) {
  const { data } = await api.get(`/quotes/${quoteId}`)
  return data
}

export async function createQuote(eventId, body) {
  const { data } = await api.post(`/events/${eventId}/quotes`, body)
  return data
}

export async function updateQuote(quoteId, patch) {
  const { data } = await api.patch(`/quotes/${quoteId}`, patch)
  return data
}

export async function sendQuote(quoteId) {
  const { data } = await api.post(`/quotes/${quoteId}/send`)
  return data
}

export async function resendQuote(quoteId, contactIds) {
  const { data } = await api.post(`/quotes/${quoteId}/resend`, {
    contact_ids: contactIds,
  })
  return data
}

export async function approveQuote(quoteId, { signatureBase64, signatureName, signatureIp = null }) {
  const { data } = await api.post(`/quotes/${quoteId}/approve`, {
    signature_base64: signatureBase64,
    signature_name: signatureName,
    signature_ip: signatureIp,
  })
  return data
}

export async function approveQuoteInStore(quoteId, { signatureBase64, signatureName }) {
  // Staff-witnessed approval. Server fills signature_ip from the
  // request; we don't try to capture a customer IP from the browser.
  const { data } = await api.post(`/quotes/${quoteId}/approve-in-store`, {
    signature_base64: signatureBase64,
    signature_name: signatureName,
    signature_ip: null,
  })
  return data
}

export async function rejectQuote(quoteId, reason) {
  const body = reason ? { reason } : {}
  const { data } = await api.post(`/quotes/${quoteId}/reject`, body)
  return data
}

export async function cancelQuote(quoteId, reason) {
  const body = reason ? { reason } : {}
  const { data } = await api.post(`/quotes/${quoteId}/cancel`, body)
  return data
}

export async function convertQuoteToInvoice(quoteId) {
  // Returns the new invoice's full detail so the caller can route into
  // its editor without a second round-trip.
  const { data } = await api.post(`/quotes/${quoteId}/convert`)
  return data
}

export async function deleteQuote(quoteId) {
  await api.delete(`/quotes/${quoteId}`)
}

export async function viewQuotePdf(quoteId) {
  const win = window.open('', '_blank')
  try {
    const resp = await api.get(`/quotes/${quoteId}/pdf`, {
      responseType: 'blob',
    })
    const url = URL.createObjectURL(resp.data)
    if (win) win.location.href = url
    else window.location.href = url
    setTimeout(() => URL.revokeObjectURL(url), 60_000)
  } catch (e) {
    if (win) win.close()
    throw e
  }
}

export async function retryQuotePdf(quoteId) {
  const { data } = await api.post(`/quotes/${quoteId}/pdf/retry`)
  return data
}

// ---------------------------------------------------------------------------
// Payments (Phase 6 backend)
// ---------------------------------------------------------------------------

export async function recordPayment(body) {
  const { data } = await api.post('/payments', body)
  return data
}

export async function getPayment(paymentId) {
  const { data } = await api.get(`/payments/${paymentId}`)
  return data
}

export async function applyUnapplied(paymentId, { invoiceId, appliedCents }) {
  const { data } = await api.post(`/payments/${paymentId}/apply`, {
    invoice_id: invoiceId,
    applied_cents: appliedCents,
  })
  return data
}

export async function unapplyAllocation(allocationId) {
  const { data } = await api.delete(`/payments/allocations/${allocationId}`)
  return data
}

export async function recordRefund(paymentId, body) {
  const { data } = await api.post(`/payments/${paymentId}/refunds`, body)
  return data
}

export async function viewPaymentReceiptPdf(paymentId) {
  const win = window.open('', '_blank')
  try {
    const resp = await api.get(`/payments/${paymentId}/receipt.pdf`, {
      responseType: 'blob',
    })
    const url = URL.createObjectURL(resp.data)
    if (win) win.location.href = url
    else window.location.href = url
    setTimeout(() => URL.revokeObjectURL(url), 60_000)
  } catch (e) {
    if (win) win.close()
    throw e
  }
}

export async function voidPayment(paymentId, reason) {
  const body = reason ? { reason } : {}
  const { data } = await api.post(`/payments/${paymentId}/void`, body)
  return data
}

export async function deletePayment(paymentId) {
  await api.delete(`/payments/${paymentId}`)
}

export async function listPaymentsForInvoice(invoiceId) {
  const { data } = await api.get(`/invoices/${invoiceId}/payments`)
  return data.payments
}

export async function listPaymentsForEvent(eventId) {
  const { data } = await api.get(`/events/${eventId}/payments`)
  return data.payments
}

// ---------------------------------------------------------------------------
// Business profile (Phase 3 backend)
// ---------------------------------------------------------------------------

export async function getBusinessProfile() {
  const { data } = await api.get('/business-profile')
  return data
}

export async function updateBusinessProfile(patch) {
  const { data } = await api.patch('/business-profile', patch)
  return data
}

export async function uploadBusinessLogo(file) {
  const form = new FormData()
  form.append('file', file)
  const { data } = await api.post('/business-profile/logo', form)
  return data
}

export async function deleteBusinessLogo() {
  const { data } = await api.delete('/business-profile/logo')
  return data
}

// The logo endpoint is auth-gated, so a plain <img src> request would
// 401 because the browser does not attach the bearer token outside of
// XHR/fetch flows. Fetch as a blob via Axios (interceptor adds the
// header) and let the caller turn it into an object URL for <img src>.
// Caller must URL.revokeObjectURL when done to avoid leaking the blob.
export async function fetchBusinessLogoBlob() {
  const resp = await api.get('/business-profile/logo', {
    responseType: 'blob',
  })
  return resp.data
}

// ---------------------------------------------------------------------------
// Activity log (Phase 9)
// ---------------------------------------------------------------------------

export async function listEventActivity(eventId, { limit = 100, beforeId } = {}) {
  const params = { limit }
  if (beforeId != null) params.before_id = beforeId
  const { data } = await api.get(`/events/${eventId}/activity`, { params })
  return data
}

// ---------------------------------------------------------------------------
// Dashboard rollups (Phase 10)
// ---------------------------------------------------------------------------

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

export async function getPipelineCounts(eventType = 'quinceanera') {
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

// Returns up to `limit` catalog rows matching `q`. The picker calls
// this on each keystroke (debounced upstream) and on open with q=''
// for the idle list. include_inactive surfaces retired rows when the
// staff toggles "include inactive." isSample = true narrows to floor
// samples (Phase 6 toggle), false hides them, undefined includes both.
export async function searchCatalog({
  q = '',
  includeInactive = false,
  isSample,
  group,
  designer,
  limit = 25,
} = {}) {
  const params = { limit }
  if (q && q.trim()) params.q = q.trim()
  if (includeInactive) params.include_inactive = true
  if (isSample === true) params.is_sample = true
  if (isSample === false) params.is_sample = false
  if (group) params.group = group
  if (designer) params.designer = designer
  const { data } = await api.get('/catalog', { params })
  return data
}

// Distinct designers + counts, for the admin Products vendor filter.
// Server-sourced so vendors past the per-request row cap still appear.
export async function listCatalogDesigners() {
  const { data } = await api.get('/catalog/designers')
  return Array.isArray(data) ? data : []
}

// Admin catalog CRUD. The list/search path above is shared with the
// editor's CatalogPicker; these two are admin-only writes used by
// the AdminCatalog page.
export async function createCatalogItem(body) {
  const { data } = await api.post('/catalog', body)
  return data
}

export async function updateCatalogItem(catalogItemId, patch) {
  const { data } = await api.patch(`/catalog/${catalogItemId}`, patch)
  return data
}

// Price decomposition for the catalog detail view: package vs dress-only
// and what each removable package item saves. Derived prices only — the
// backend never returns wholesale cost or the multiplier here.
export async function getCatalogPriceBreakdown(catalogItemId) {
  const { data } = await api.get(`/catalog/${catalogItemId}/price-breakdown`)
  return data
}

// ---------------------------------------------------------------------------
// Vehicles (Day 2 — Kelley Autoplex inventory)
// ---------------------------------------------------------------------------
//
// Vehicles are `catalog_items` rows with `is_vehicle=true` (migration 085).
// These wrappers reuse the same /catalog endpoints the dress catalog uses
// but scope reads to the vehicle group AND re-gate on the `is_vehicle`
// discriminator client-side — per the Day 1 rule, `is_vehicle` is the only
// reliable "this is a car" signal, so a backfilled non-vehicle row that
// happens to carry category='vehicle' or a vehicle_status can never leak
// onto the vehicle surface.

// Lists vehicles. `group=vehicle` filters on category server-side (and
// applies in search mode too when `q` is set); we then filter on
// `is_vehicle` so the discriminator — not the category — is the final
// gate. `status` filters by vehicle_status client-side (the list route
// has no status param).
export async function listVehicles({
  q = '',
  includeInactive = false,
  status,
  limit = 500,
} = {}) {
  const params = { group: 'vehicle', limit }
  if (q && q.trim()) params.q = q.trim()
  if (includeInactive) params.include_inactive = true
  const { data } = await api.get('/catalog', { params })
  const rows = Array.isArray(data) ? data : []
  let vehicles = rows.filter((row) => row.is_vehicle === true)
  if (status) vehicles = vehicles.filter((row) => row.vehicle_status === status)
  return vehicles
}

// Create a vehicle. Always stamps `is_vehicle: true` so the caller can
// never forget it. The API derives internal_sku<-stock_number,
// color<-exterior_color, category='vehicle', and mirrors make->designer /
// model->style_number; callers send stock_number, exterior_color, and the
// vehicle fields only.
export async function createVehicle(body) {
  const { data } = await api.post('/catalog', { ...body, is_vehicle: true })
  return data
}

// Patch mutable vehicle fields. `is_vehicle` is intentionally never sent —
// a row's car/not-car identity is fixed at create time. The PATCH route
// does not re-mirror make->designer, so the page also threads
// designer/style_number to keep the compat search columns in sync.
export async function updateVehicle(catalogItemId, patch) {
  const { data } = await api.patch(`/catalog/${catalogItemId}`, patch)
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

export async function salesGetClockStatus({ signal } = {}) {
  const { data } = await api.get('/sales/clock/status', { signal })
  return data
}

function _buildClockForm({ latitude, longitude, accuracy_m, selfieBlob }) {
  const form = new FormData()
  // Coords are optional: on the boutique WiFi a staffer can punch
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

// ---------------------------------------------------------------------------
// Sales Portal — Try-on log (Phase 4)
// ---------------------------------------------------------------------------

export async function salesListTriedOn(appointmentId) {
  const { data } = await api.get(
    `/sales/appointments/${appointmentId}/tried-on`,
  )
  return data
}

export async function salesAddTriedOn(appointmentId, body) {
  const { data } = await api.post(
    `/sales/appointments/${appointmentId}/tried-on`,
    body,
  )
  return data
}

export async function salesPatchTriedOn(triedOnId, patch) {
  const { data } = await api.patch(`/sales/tried-on/${triedOnId}`, patch)
  return data
}

export async function salesDeleteTriedOn(triedOnId) {
  await api.delete(`/sales/tried-on/${triedOnId}`)
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

export async function salesChangePin(currentPin, newPin) {
  await api.post('/sales/auth/change-pin', {
    current_pin: currentPin,
    new_pin: newPin,
  })
}

// ---------------------------------------------------------------------------
// Owner-side sales-staff management (admin-scope only)
// ---------------------------------------------------------------------------

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

export default api
