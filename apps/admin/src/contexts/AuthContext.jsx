import { createContext, useContext, useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'

import {
  getMe,
  login as apiLogin,
  logout as apiLogout,
} from '../services/api'

const AuthContext = createContext(null)

export function AuthProvider({ children }) {
  const navigate = useNavigate()
  const [user, setUser] = useState(null)
  const [isLoading, setIsLoading] = useState(true)

  useEffect(() => {
    // D3: no local token to check — the session lives in an HttpOnly
    // cookie the JS cannot read. Ask the server whether the cookie is
    // valid by calling /auth/me. A 401 means the user is signed out
    // (cookie missing, expired, or revoked); anything else surfaces
    // as a "no user" state.
    getMe()
      .then((u) => setUser(u))
      .catch(() => setUser(null))
      .finally(() => setIsLoading(false))
  }, [])

  async function login(email, password) {
    const data = await apiLogin(email, password)
    // The login response sets the session + CSRF cookies via Set-Cookie.
    // We just hold onto the returned user object for the React tree.
    setUser(data.user)
    return data.user
  }

  async function logout() {
    // D2 + D3: server bumps token_version AND clears the session +
    // CSRF cookies. We still swallow network errors so a flaky
    // connection cannot leave the UI half-deauthenticated — the
    // local setUser(null) is the user-visible "logged out."
    try {
      await apiLogout()
    } catch {
      // Best-effort — the local clear below is what the user sees.
    }
    setUser(null)
    navigate('/login', { replace: true })
  }

  const value = {
    user,
    isAuthenticated: user !== null,
    isLoading,
    login,
    logout,
  }

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
}

// eslint-disable-next-line react-refresh/only-export-components
export function useAuth() {
  const ctx = useContext(AuthContext)
  if (!ctx) throw new Error('useAuth must be used inside AuthProvider')
  return ctx
}
