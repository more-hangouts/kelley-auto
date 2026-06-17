import { BrowserRouter, Navigate, Route, Routes, useParams } from 'react-router-dom'
import { CssBaseline, ThemeProvider } from '@mui/material'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'

import DashboardLayout from './components/DashboardLayout'
import ProtectedRoute from './components/ProtectedRoute'
import { AuthProvider } from './contexts/AuthContext'
import SalesApp from './sales/SalesApp'
import { isSalesSubdomain } from './services/api'
import AdminCatalog from './pages/AdminCatalog'
import AdminHolidays from './pages/AdminHolidays'
import AdminScheduleFinalizedWeek from './pages/AdminScheduleFinalizedWeek'
import AdminScheduleGrid from './pages/AdminScheduleGrid'
import AdminOpenShifts from './pages/AdminOpenShifts'
import AdminSchedulePresets from './pages/AdminSchedulePresets'
import AdminShiftRequests from './pages/AdminShiftRequests'
import AdminStaffLocations from './pages/AdminStaffLocations'
import AdminTimeOff from './pages/AdminTimeOff'
import AppointmentsCalendar from './pages/AppointmentsCalendar'
import AttendanceReview from './pages/AttendanceReview'
import BookingWidgetSettings from './pages/BookingWidgetSettings'
import BusinessProfile from './pages/BusinessProfile'
import ContactDetail from './pages/ContactDetail'
import Dashboard from './pages/Dashboard'
import EventDetailLayout from './pages/event/EventDetailLayout'
import Activity from './pages/event/tabs/Activity'
import Documents from './pages/event/tabs/Documents'
import Invoices from './pages/event/tabs/Invoices'
import Payments from './pages/event/tabs/Payments'
import Quotes from './pages/event/tabs/Quotes'
import InvoicesGlobal from './pages/InvoicesGlobal'
import Overview from './pages/event/tabs/Overview'
import Login from './pages/Login'
import Pipeline from './pages/Pipeline'
import RecycleBin from './pages/RecycleBin'
import SalesStaffSchedule from './pages/SalesStaffSchedule'
import SalesStaffSettings from './pages/SalesStaffSettings'
import Settings from './pages/Settings'
import StaffManagementLayout from './pages/StaffManagementLayout'
import StaffScheduleLayout from './pages/StaffScheduleLayout'
import theme from './theme'

const queryClient = new QueryClient({
  defaultOptions: {
    queries: { staleTime: 30_000, refetchOnWindowFocus: false },
  },
})

function LegacyStaffScheduleRedirect() {
  const { userId } = useParams()
  return <Navigate to={`/settings/staff/profiles/${userId}/schedule`} replace />
}

export default function App() {
  // The sales surface is its own React app — its own auth context,
  // its own routes, its own token storage key. Mounted on hostname
  // match so `admin.shopbellasxv.com` and `sales.shopbellasxv.com`
  // get the right tree without sharing routers or providers.
  if (isSalesSubdomain()) {
    return <SalesApp />
  }

  return (
    <ThemeProvider theme={theme}>
      <CssBaseline />
      <QueryClientProvider client={queryClient}>
        <BrowserRouter>
          <AuthProvider>
            <Routes>
              <Route path="/login" element={<Login />} />
              <Route
                path="/"
                element={
                  <ProtectedRoute>
                    <DashboardLayout />
                  </ProtectedRoute>
                }
              >
                <Route index element={<Dashboard />} />
                <Route path="pipeline" element={<Pipeline />} />
                <Route path="events/:eventId" element={<EventDetailLayout />}>
                  <Route index element={<Navigate to="overview" replace />} />
                  <Route path="overview" element={<Overview />} />
                  <Route path="documents" element={<Documents />} />
                  <Route path="quotes" element={<Quotes />} />
                  <Route path="invoices" element={<Invoices />} />
                  <Route path="payments" element={<Payments />} />
                  <Route path="activity" element={<Activity />} />
                </Route>
                <Route path="calendar" element={<AppointmentsCalendar />} />
                <Route path="contacts/:contactId" element={<ContactDetail />} />
                <Route path="invoices" element={<InvoicesGlobal />} />
                <Route path="products" element={<AdminCatalog />} />
                <Route path="settings" element={<Settings />} />
                <Route path="settings/widget" element={<BookingWidgetSettings />} />
                <Route path="settings/recycle-bin" element={<RecycleBin />} />
                <Route path="settings/business-profile" element={<BusinessProfile />} />
                {/* Legacy URLs — Products moved to top-level nav, Widget settings
                    moved under Settings. Keep old bookmarks/links working. */}
                <Route
                  path="widget-settings"
                  element={<Navigate to="/settings/widget" replace />}
                />
                <Route
                  path="settings/catalog"
                  element={<Navigate to="/products" replace />}
                />
                <Route path="settings/staff" element={<StaffManagementLayout />}>
                  <Route index element={<Navigate to="profiles" replace />} />
                  <Route path="profiles" element={<SalesStaffSettings />} />
                  <Route
                    path="profiles/:userId/schedule"
                    element={<SalesStaffSchedule />}
                  />
                  <Route path="schedule" element={<StaffScheduleLayout />}>
                    <Route index element={<Navigate to="grid" replace />} />
                    <Route path="grid" element={<AdminScheduleGrid />} />
                    <Route
                      path="finalized"
                      element={<AdminScheduleFinalizedWeek />}
                    />
                    <Route path="presets" element={<AdminSchedulePresets />} />
                    <Route path="time-off" element={<AdminTimeOff />} />
                    <Route
                      path="shift-requests"
                      element={<AdminShiftRequests />}
                    />
                    <Route
                      path="open-shifts"
                      element={<AdminOpenShifts />}
                    />
                    <Route path="holidays" element={<AdminHolidays />} />
                  </Route>
                  <Route path="locations" element={<AdminStaffLocations />} />
                  <Route path="attendance" element={<AttendanceReview />} />
                </Route>
                {/* Legacy URLs — keep bookmarks and links from older PDFs/emails working. */}
                <Route
                  path="settings/sales-staff"
                  element={<Navigate to="/settings/staff/profiles" replace />}
                />
                <Route
                  path="settings/sales-staff/:userId/schedule"
                  element={<LegacyStaffScheduleRedirect />}
                />
                <Route
                  path="settings/time-off"
                  element={<Navigate to="/settings/staff/schedule/time-off" replace />}
                />
                <Route
                  path="settings/holidays"
                  element={<Navigate to="/settings/staff/schedule/holidays" replace />}
                />
                <Route
                  path="settings/staff-locations"
                  element={<Navigate to="/settings/staff/locations" replace />}
                />
                <Route
                  path="reports/attendance"
                  element={<Navigate to="/settings/staff/attendance" replace />}
                />
              </Route>
              <Route path="*" element={<Navigate to="/" replace />} />
            </Routes>
          </AuthProvider>
        </BrowserRouter>
      </QueryClientProvider>
    </ThemeProvider>
  )
}
