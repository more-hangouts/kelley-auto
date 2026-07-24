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

export default api
