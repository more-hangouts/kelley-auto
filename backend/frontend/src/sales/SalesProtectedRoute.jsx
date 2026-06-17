import { Box, CircularProgress } from '@mui/material'
import { Navigate, useLocation } from 'react-router-dom'

import { useSalesAuth } from '../contexts/SalesAuthContext'
import { useClockStatus } from './useClockStatus'

// Routes that stay reachable while punched out. The Phase 7 plan
// keeps schedule, time-off, the PIN change flow, and sign-out usable
// even when the stylist hasn't clocked in yet — only floor mutations
// are gated. /clock itself must obviously be reachable so the user
// can clock in.
const PUNCHED_OUT_ALLOWLIST = new Set([
  '/clock',
  '/change-pin',
  // Filing a missed-punch correction has to work while clocked out —
  // the whole point of this surface is "I forgot to punch in
  // yesterday." Confirming an auto-closed punch is similar: by
  // definition the stylist has already gone home.
  '/my-attendance',
  // Phase 8 schedule + time-off: viewing the upcoming schedule and
  // filing a time-off request both have to work before a stylist
  // clocks in (or after they go home). They never mutate appointment
  // state so the gate is irrelevant here.
  '/schedule',
  '/time-off',
])

export default function SalesProtectedRoute({ children }) {
  const { isAuthenticated, isLoading, forcePinChange } = useSalesAuth()
  const location = useLocation()
  const { status: clockStatus, isLoading: clockLoading } = useClockStatus()

  if (isLoading) {
    return (
      <Box
        sx={{
          minHeight: '100vh',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
        }}
      >
        <CircularProgress color="primary" />
      </Box>
    )
  }

  if (!isAuthenticated) {
    return <Navigate to="/login" replace />
  }

  // Force the change-PIN flow before any other authenticated route loads.
  if (forcePinChange && location.pathname !== '/change-pin') {
    return <Navigate to="/change-pin" replace />
  }

  // Phase 7 Slice 2B redirect: a punched-out stylist with the gate
  // enabled gets routed to /clock (with `?next=` so we can send them
  // back to where they were trying to go after they punch in). We
  // wait for the first clock-status load before deciding so we don't
  // bounce them mid-fetch on every navigation.
  const allowed = PUNCHED_OUT_ALLOWLIST.has(location.pathname)
  if (
    !allowed &&
    !clockLoading &&
    clockStatus &&
    clockStatus.attendance_gate_enabled &&
    clockStatus.state === 'out'
  ) {
    const nextParam = encodeURIComponent(
      location.pathname + (location.search || ''),
    )
    return <Navigate to={`/clock?next=${nextParam}`} replace />
  }

  return children
}
