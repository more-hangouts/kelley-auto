import api from './client'

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
