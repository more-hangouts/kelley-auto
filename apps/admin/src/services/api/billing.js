import api from './client'

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
