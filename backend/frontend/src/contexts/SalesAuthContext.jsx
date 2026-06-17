import { createContext, useCallback, useContext, useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'

import {
  salesGetMe,
  salesKioskLock,
  salesLogout,
  salesPinLogin,
} from '../services/api'

// Shared-tablet idle thresholds. Warning fires first so a stylist who
// is reading the screen can tap to stay signed in; auto-lock fires if
// the device is genuinely unattended. Centralized so the owner can
// tune them later without grepping. Both are measured from the last
// pointer/key/touch event on `document`.
const KIOSK_IDLE_WARN_MS = 2 * 60 * 1000
const KIOSK_IDLE_LOCK_MS = 5 * 60 * 1000

const SalesAuthContext = createContext(null)

export function SalesAuthProvider({ children }) {
  const [user, setUser] = useState(null)
  const [forcePinChange, setForcePinChange] = useState(false)
  const [isLoading, setIsLoading] = useState(true)
  const [idleWarning, setIdleWarning] = useState(false)
  const navigate = useNavigate()

  useEffect(() => {
    // D3: nothing to read locally; the PIN session is an HttpOnly
    // cookie. Ask the server. A 401 means the kiosk has no live
    // session (cookie missing, expired, or revoked).
    salesGetMe()
      .then((u) => {
        setUser(u)
        setForcePinChange(Boolean(u.force_pin_change))
      })
      .catch(() => {
        setUser(null)
        setForcePinChange(false)
      })
      .finally(() => setIsLoading(false))
  }, [])

  const login = useCallback(async (identifier, pin) => {
    const data = await salesPinLogin(identifier, pin)
    // The PIN response sets the sales session + CSRF cookies. We just
    // hold the user object in React state.
    setUser(data.user)
    setForcePinChange(Boolean(data.force_pin_change))
    return data
  }, [])

  const logout = useCallback(async () => {
    // D2 + D3: server bumps token_version AND clears the sales
    // session + CSRF cookies. Local state is cleared regardless of
    // network outcome — a flaky kiosk WiFi must not strand the
    // stylist in a "logged in but invisible to the server" state.
    try {
      await salesLogout()
    } catch {
      // Best-effort; the local clear below is the user-visible logout.
    }
    setUser(null)
    setForcePinChange(false)
    setIdleWarning(false)
  }, [])

  const lock = useCallback(
    async (reason = 'manual') => {
      // Shared-tablet quick-lock. Clears the sales session + CSRF
      // cookies on this device only — does NOT bump token_version,
      // so the stylist stays signed in on every other device they
      // touched today. The next stylist enters their PIN on the
      // login screen and a fresh sales cookie is issued.
      try {
        await salesKioskLock()
      } catch {
        // Best-effort. Local state clear is the user-visible signal.
      }
      setUser(null)
      setForcePinChange(false)
      setIdleWarning(false)
      navigate(`/login?locked=${reason}`, { replace: true })
    },
    [navigate],
  )

  const refreshMe = useCallback(async () => {
    const u = await salesGetMe()
    setUser(u)
    setForcePinChange(Boolean(u.force_pin_change))
    return u
  }, [])

  // Idle activity tracking. Listeners are attached only while a
  // stylist is signed in: there's nothing to lock when the login
  // screen is showing. Every pointer/key/touch event resets both the
  // warning and the auto-lock timers. The "dismiss" action for the
  // warning banner is "do literally anything" — that already counts
  // as activity.
  useEffect(() => {
    if (!user) {
      setIdleWarning(false)
      return undefined
    }

    let warnTimer = null
    let lockTimer = null

    function clearTimers() {
      if (warnTimer !== null) {
        clearTimeout(warnTimer)
        warnTimer = null
      }
      if (lockTimer !== null) {
        clearTimeout(lockTimer)
        lockTimer = null
      }
    }

    function scheduleTimers() {
      clearTimers()
      setIdleWarning(false)
      warnTimer = setTimeout(() => setIdleWarning(true), KIOSK_IDLE_WARN_MS)
      lockTimer = setTimeout(() => {
        lock('idle')
      }, KIOSK_IDLE_LOCK_MS)
    }

    function bumpActivity() {
      scheduleTimers()
    }

    scheduleTimers()
    const events = ['pointerdown', 'keydown', 'touchstart', 'wheel']
    events.forEach((e) =>
      document.addEventListener(e, bumpActivity, { passive: true }),
    )

    return () => {
      events.forEach((e) => document.removeEventListener(e, bumpActivity))
      clearTimers()
    }
  }, [user, lock])

  const value = {
    user,
    isAuthenticated: user !== null,
    isLoading,
    forcePinChange,
    setForcePinChange,
    login,
    logout,
    lock,
    idleWarning,
    refreshMe,
  }

  return (
    <SalesAuthContext.Provider value={value}>{children}</SalesAuthContext.Provider>
  )
}

// eslint-disable-next-line react-refresh/only-export-components
export function useSalesAuth() {
  const ctx = useContext(SalesAuthContext)
  if (!ctx) {
    throw new Error('useSalesAuth must be used inside SalesAuthProvider')
  }
  return ctx
}
