// Resolve a stored vehicle-media path to a loadable browser URL.
//
// Uploaded photos are stored origin-relative ("/api/public/media/vehicles/…")
// so the DB stays host-independent. To load them we join onto the API
// *origin* (scheme + host) — NOT VITE_API_URL, which in production ends in
// "/api" and would produce "…/api/api/public/media/…" (a 404). External
// http(s) URLs are returned untouched.

function apiOrigin() {
  const raw = import.meta.env.VITE_API_URL || ''
  try {
    return new URL(raw).origin
  } catch {
    // VITE_API_URL wasn't absolute — strip a trailing "/api" and slash.
    return raw.replace(/\/api\/?$/, '').replace(/\/$/, '')
  }
}

const API_ORIGIN = apiOrigin()

export function mediaSrc(url) {
  if (!url) return ''
  if (/^https?:\/\//i.test(url)) return url
  return `${API_ORIGIN}${url.startsWith('/') ? '' : '/'}${url}`
}
