import { useQuery, useQueryClient } from '@tanstack/react-query'

import { salesGetClockStatus } from '../services/api'
import { useSalesAuth } from '../contexts/SalesAuthContext'

const CLOCK_STATUS_KEY = ['sales', 'clock', 'status']

/**
 * Read-only hook for the SalesApp clock state. Backed by React Query
 * so multiple components share a single in-flight request and the
 * data refreshes on window focus (a stylist coming back from the
 * lock screen sees the right state without a manual reload).
 *
 * Returns `{ status, isLoading, error, refetch }`. `status` mirrors
 * `GET /api/sales/clock/status`:
 *   - state: 'in' | 'out'
 *   - last_punch: object | null
 *   - today_punches: array
 *   - timezone, business_date, selfie_policy, attendance_gate_enabled
 *
 * Disabled when no sales user is authenticated, so the hook can be
 * called unconditionally inside SalesApp components.
 */
export function useClockStatus() {
  const { isAuthenticated, forcePinChange } = useSalesAuth()
  const enabled = isAuthenticated && !forcePinChange

  const query = useQuery({
    queryKey: CLOCK_STATUS_KEY,
    queryFn: ({ signal }) => salesGetClockStatus({ signal }),
    enabled,
    // Fresh enough that the SalesProtectedRoute's redirect decision
    // catches a punch quickly, gentle enough that we don't hammer
    // the API every render.
    staleTime: 15_000,
    refetchOnWindowFocus: true,
    retry: 1,
  })

  return {
    status: query.data || null,
    isLoading: query.isLoading,
    error: query.error,
    refetch: query.refetch,
  }
}

/**
 * Imperative invalidation. Components that mutate punch state (the
 * /clock screen) call this after a successful punch so every
 * subscriber re-fetches.
 */
export function useInvalidateClockStatus() {
  const queryClient = useQueryClient()
  return () => queryClient.invalidateQueries({ queryKey: CLOCK_STATUS_KEY })
}
