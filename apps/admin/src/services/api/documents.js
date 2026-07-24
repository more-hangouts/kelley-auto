import api from './client'

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
