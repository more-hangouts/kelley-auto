import { BrowserRouter, Navigate, Route, Routes } from 'react-router-dom'
import { CssBaseline, ThemeProvider } from '@mui/material'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'

import { SalesAuthProvider } from '../contexts/SalesAuthContext'
import theme from '../theme'
import AppointmentDetail from './AppointmentDetail'
import ChangePin from './ChangePin'
import ClockScreen from './ClockScreen'
import MyAttendance from './MyAttendance'
import Notifications from './Notifications'
import PinLogin from './PinLogin'
import RepDashboard from './RepDashboard'
import SalesLayout from './SalesLayout'
import SalesProtectedRoute from './SalesProtectedRoute'
import Schedule from './Schedule'
import TimeOff from './TimeOff'

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
              <Route path="/login" element={<PinLogin />} />
              <Route
                path="/change-pin"
                element={
                  <SalesProtectedRoute>
                    <ChangePin />
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
                <Route index element={<RepDashboard />} />
                <Route path="clock" element={<ClockScreen />} />
                <Route path="my-attendance" element={<MyAttendance />} />
                <Route path="schedule" element={<Schedule />} />
                <Route path="time-off" element={<TimeOff />} />
                <Route path="notifications" element={<Notifications />} />
                <Route
                  path="appointments/:appointmentId"
                  element={<AppointmentDetail />}
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
