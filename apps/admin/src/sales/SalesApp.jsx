import { lazy, Suspense } from 'react'
import { BrowserRouter, Navigate, Route, Routes } from 'react-router-dom'
import { CssBaseline, ThemeProvider } from '@mui/material'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'

import RouteFallback from '../components/RouteFallback'
import { SalesAuthProvider } from '../contexts/SalesAuthContext'
import theme from '../theme'
import SalesLayout from './SalesLayout'
import SalesProtectedRoute from './SalesProtectedRoute'

// Page-level sales screens — each its own on-demand chunk. The shell
// (providers, SalesLayout, SalesProtectedRoute) stays eager above so the
// PIN-login/auth flow and kiosk idle-lock are always present.
const AppointmentDetail = lazy(() => import('./AppointmentDetail'))
const ChangePin = lazy(() => import('./ChangePin'))
const ClockScreen = lazy(() => import('./ClockScreen'))
const MyAttendance = lazy(() => import('./MyAttendance'))
const Notifications = lazy(() => import('./Notifications'))
const PinLogin = lazy(() => import('./PinLogin'))
const RepDashboard = lazy(() => import('./RepDashboard'))
const Schedule = lazy(() => import('./Schedule'))
const TimeOff = lazy(() => import('./TimeOff'))

const queryClient = new QueryClient({
  defaultOptions: {
    queries: { staleTime: 30_000, refetchOnWindowFocus: false },
  },
})

export default function SalesApp() {
  return (
    <ThemeProvider theme={theme}>
      <CssBaseline />
      <QueryClientProvider client={queryClient}>
        <BrowserRouter>
          <SalesAuthProvider>
            <Routes>
              <Route
                path="/login"
                element={
                  <Suspense fallback={<RouteFallback />}>
                    <PinLogin />
                  </Suspense>
                }
              />
              <Route
                path="/change-pin"
                element={
                  <SalesProtectedRoute>
                    <Suspense fallback={<RouteFallback />}>
                      <ChangePin />
                    </Suspense>
                  </SalesProtectedRoute>
                }
              />
              <Route
                path="/"
                element={
                  <SalesProtectedRoute>
                    <SalesLayout />
                  </SalesProtectedRoute>
                }
              >
                <Route index element={<Suspense fallback={<RouteFallback />}><RepDashboard /></Suspense>} />
                <Route path="clock" element={<Suspense fallback={<RouteFallback />}><ClockScreen /></Suspense>} />
                <Route path="my-attendance" element={<Suspense fallback={<RouteFallback />}><MyAttendance /></Suspense>} />
                <Route path="schedule" element={<Suspense fallback={<RouteFallback />}><Schedule /></Suspense>} />
                <Route path="time-off" element={<Suspense fallback={<RouteFallback />}><TimeOff /></Suspense>} />
                <Route path="notifications" element={<Suspense fallback={<RouteFallback />}><Notifications /></Suspense>} />
                <Route
                  path="appointments/:appointmentId"
                  element={<Suspense fallback={<RouteFallback />}><AppointmentDetail /></Suspense>}
                />
              </Route>
              <Route path="*" element={<Navigate to="/" replace />} />
            </Routes>
          </SalesAuthProvider>
        </BrowserRouter>
      </QueryClientProvider>
    </ThemeProvider>
  )
}
