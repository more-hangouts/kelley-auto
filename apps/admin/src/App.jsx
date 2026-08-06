import { lazy, Suspense } from 'react'
import { BrowserRouter, Navigate, Route, Routes, useParams } from 'react-router-dom'
import { CssBaseline, ThemeProvider } from '@mui/material'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'

import DashboardLayout from './components/DashboardLayout'
import ProtectedRoute from './components/ProtectedRoute'
import RouteFallback from './components/RouteFallback'
import { AuthProvider } from './contexts/AuthContext'
// Narrow import: pull hostname detection straight from the client module so
// the admin shell never evaluates the domain API barrel (and its ~1,800
// lines of endpoint helpers) just to decide which surface to mount. That
// keeps the whole API surface out of the initial graph and lets the route
// chunks below split cleanly.
import { isSalesSubdomain } from './services/api/client'
import theme from './theme'

// The sales surface is its own React app; lazy so the admin host never
// downloads it (and vice versa). This is the single biggest split point.
const SalesApp = lazy(() => import('./sales/SalesApp'))

// Page-level route components — each becomes its own on-demand chunk. The
// shell (providers, DashboardLayout, ProtectedRoute) stays eager above.
const AdminCatalog = lazy(() => import('./pages/AdminCatalog'))
const AdminVehicles = lazy(() => import('./pages/AdminVehicles'))
const AdminHolidays = lazy(() => import('./pages/AdminHolidays'))
const AdminScheduleFinalizedWeek = lazy(() => import('./pages/AdminScheduleFinalizedWeek'))
const AdminScheduleGrid = lazy(() => import('./pages/AdminScheduleGrid'))
const AdminOpenShifts = lazy(() => import('./pages/AdminOpenShifts'))
const AdminSchedulePresets = lazy(() => import('./pages/AdminSchedulePresets'))
const AdminShiftRequests = lazy(() => import('./pages/AdminShiftRequests'))
const AdminStaffLocations = lazy(() => import('./pages/AdminStaffLocations'))
const AdminTimeOff = lazy(() => import('./pages/AdminTimeOff'))
const AppointmentsCalendar = lazy(() => import('./pages/AppointmentsCalendar'))
const AttendanceReview = lazy(() => import('./pages/AttendanceReview'))
const BookingWidgetSettings = lazy(() => import('./pages/BookingWidgetSettings'))
const BusinessProfile = lazy(() => import('./pages/BusinessProfile'))
const ContactDetail = lazy(() => import('./pages/ContactDetail'))
const Contacts = lazy(() => import('./pages/Contacts'))
const Dashboard = lazy(() => import('./pages/Dashboard'))
const SalesActivity = lazy(() => import('./pages/SalesActivity'))
const CallActivity = lazy(() => import('./pages/CallActivity'))
const StorefrontAnalytics = lazy(() => import('./pages/StorefrontAnalytics'))
const EventDetailLayout = lazy(() => import('./pages/event/EventDetailLayout'))
const Timeline = lazy(() => import('./pages/event/tabs/Timeline'))
const Payments = lazy(() => import('./pages/event/tabs/Payments'))
const InvoicesGlobal = lazy(() => import('./pages/InvoicesGlobal'))
const Overview = lazy(() => import('./pages/event/tabs/Overview'))
const Login = lazy(() => import('./pages/Login'))
const Pipeline = lazy(() => import('./pages/Pipeline'))
const RecycleBin = lazy(() => import('./pages/RecycleBin'))
const SalesStaffSchedule = lazy(() => import('./pages/SalesStaffSchedule'))
const SalesStaffSettings = lazy(() => import('./pages/SalesStaffSettings'))
const Settings = lazy(() => import('./pages/Settings'))
const NotificationSubscribers = lazy(() => import('./pages/NotificationSubscribers'))
const Inbox = lazy(() => import('./pages/Inbox'))
const StaffManagementLayout = lazy(() => import('./pages/StaffManagementLayout'))
const StaffScheduleLayout = lazy(() => import('./pages/StaffScheduleLayout'))

const queryClient = new QueryClient({
  defaultOptions: {
    queries: { staleTime: 30_000, refetchOnWindowFocus: false },
  },
})

function LegacyStaffScheduleRedirect() {
  const { userId } = useParams()
  return <Navigate to={`/settings/staff/profiles/${userId}/schedule`} replace />
}

// /events/134/overview -> /deals/134/overview. The tab segment rides along
// via the splat, so a bookmark deep into a tab lands on the same tab —
// except the three retired ones, which the deals routes bounce to Overview.
function LegacyEventRedirect() {
  const { eventId, '*': rest } = useParams()
  const tail = rest ? `/${rest}` : ''
  return <Navigate to={`/deals/${eventId}${tail}`} replace />
}

export default function App() {
  // The sales surface is its own React app — its own auth context,
  // its own routes, its own token storage key. Mounted on hostname
  // match so `admin.kelleyautoplex.com` and `sales.kelleyautoplex.com`
  // get the right tree without sharing routers or providers.
  if (isSalesSubdomain()) {
    return (
      <Suspense fallback={<RouteFallback />}>
        <SalesApp />
      </Suspense>
    )
  }

  return (
    <ThemeProvider theme={theme}>
      <CssBaseline />
      <QueryClientProvider client={queryClient}>
        <BrowserRouter>
          <AuthProvider>
            <Routes>
              <Route
                path="/login"
                element={
                  <Suspense fallback={<RouteFallback />}>
                    <Login />
                  </Suspense>
                }
              />
              <Route
                path="/"
                element={
                  <ProtectedRoute>
                    <DashboardLayout />
                  </ProtectedRoute>
                }
              >
                {/* Nested route pages render into DashboardLayout's <Outlet />.
                    One Suspense boundary here keeps the shell chrome mounted
                    and only swaps the content region while a page chunk loads. */}
                <Route index element={<Suspense fallback={<RouteFallback />}><Dashboard /></Suspense>} />
                {/* Deals is the single vehicle-sale board. The old quinceañera
                    pipeline was retired — keep the URL working for bookmarks. */}
                <Route path="pipeline" element={<Navigate to="/sales" replace />} />
                <Route
                  path="sales"
                  element={
                    <Suspense fallback={<RouteFallback />}>
                      <Pipeline
                        eventType="vehicle_sale"
                        title="Deals"
                        subtitleNoun="Vehicle deals"
                      />
                    </Suspense>
                  }
                />
                <Route
                  path="deals/:eventId"
                  element={<Suspense fallback={<RouteFallback />}><EventDetailLayout /></Suspense>}
                >
                  {/* Timeline is the landing tab: "what happened with this
                      customer?" is why a rep opens a deal. */}
                  <Route index element={<Navigate to="timeline" replace />} />
                  <Route path="timeline" element={<Suspense fallback={<RouteFallback />}><Timeline /></Suspense>} />
                  <Route path="overview" element={<Suspense fallback={<RouteFallback />}><Overview /></Suspense>} />
                  <Route path="payments" element={<Suspense fallback={<RouteFallback />}><Payments /></Suspense>} />
                  {/* Notes and Activity were merged INTO Timeline (along with
                      the standalone text-messages box). Old links follow. */}
                  <Route path="notes" element={<Navigate to="../timeline" replace />} />
                  <Route path="activity" element={<Navigate to="../timeline" replace />} />
                  {/* Documents / Quotes / Invoices were retired from the deal
                      page — financing and paperwork live outside this CRM, and
                      all three had zero rows. Land old bookmarks on Overview
                      rather than a blank router miss. */}
                  <Route path="documents" element={<Navigate to="../overview" replace />} />
                  <Route path="quotes" element={<Navigate to="../overview" replace />} />
                  <Route path="invoices" element={<Navigate to="../overview" replace />} />
                </Route>
                {/* Legacy /events/:id/* URLs — the surface is called Deals
                    everywhere in the UI, so the path says deals now. Keep every
                    old link (bookmarks, emailed links, dashboard widgets)
                    working. */}
                <Route path="events/:eventId/*" element={<LegacyEventRedirect />} />
                <Route path="inbox" element={<Suspense fallback={<RouteFallback />}><Inbox /></Suspense>} />
                <Route path="calendar" element={<Suspense fallback={<RouteFallback />}><AppointmentsCalendar /></Suspense>} />
                <Route path="contacts" element={<Suspense fallback={<RouteFallback />}><Contacts /></Suspense>} />
                <Route path="contacts/:contactId" element={<Suspense fallback={<RouteFallback />}><ContactDetail /></Suspense>} />
                <Route path="sales-activity" element={<Suspense fallback={<RouteFallback />}><SalesActivity /></Suspense>} />
                <Route path="call-activity" element={<Suspense fallback={<RouteFallback />}><CallActivity /></Suspense>} />
                <Route path="analytics" element={<Suspense fallback={<RouteFallback />}><StorefrontAnalytics /></Suspense>} />
                <Route path="invoices" element={<Suspense fallback={<RouteFallback />}><InvoicesGlobal /></Suspense>} />
                <Route path="inventory" element={<Suspense fallback={<RouteFallback />}><AdminVehicles /></Suspense>} />
                <Route path="products" element={<Suspense fallback={<RouteFallback />}><AdminCatalog /></Suspense>} />
                <Route path="settings" element={<Suspense fallback={<RouteFallback />}><Settings /></Suspense>} />
                <Route path="settings/widget" element={<Suspense fallback={<RouteFallback />}><BookingWidgetSettings /></Suspense>} />
                <Route path="settings/recycle-bin" element={<Suspense fallback={<RouteFallback />}><RecycleBin /></Suspense>} />
                <Route path="settings/business-profile" element={<Suspense fallback={<RouteFallback />}><BusinessProfile /></Suspense>} />
                <Route path="settings/notifications" element={<Suspense fallback={<RouteFallback />}><NotificationSubscribers /></Suspense>} />
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
                <Route
                  path="settings/staff"
                  element={<Suspense fallback={<RouteFallback />}><StaffManagementLayout /></Suspense>}
                >
                  <Route index element={<Navigate to="profiles" replace />} />
                  <Route path="profiles" element={<Suspense fallback={<RouteFallback />}><SalesStaffSettings /></Suspense>} />
                  <Route
                    path="profiles/:userId/schedule"
                    element={<Suspense fallback={<RouteFallback />}><SalesStaffSchedule /></Suspense>}
                  />
                  <Route
                    path="schedule"
                    element={<Suspense fallback={<RouteFallback />}><StaffScheduleLayout /></Suspense>}
                  >
                    <Route index element={<Navigate to="grid" replace />} />
                    <Route path="grid" element={<Suspense fallback={<RouteFallback />}><AdminScheduleGrid /></Suspense>} />
                    <Route
                      path="finalized"
                      element={<Suspense fallback={<RouteFallback />}><AdminScheduleFinalizedWeek /></Suspense>}
                    />
                    <Route path="presets" element={<Suspense fallback={<RouteFallback />}><AdminSchedulePresets /></Suspense>} />
                    <Route path="time-off" element={<Suspense fallback={<RouteFallback />}><AdminTimeOff /></Suspense>} />
                    <Route
                      path="shift-requests"
                      element={<Suspense fallback={<RouteFallback />}><AdminShiftRequests /></Suspense>}
                    />
                    <Route
                      path="open-shifts"
                      element={<Suspense fallback={<RouteFallback />}><AdminOpenShifts /></Suspense>}
                    />
                    <Route path="holidays" element={<Suspense fallback={<RouteFallback />}><AdminHolidays /></Suspense>} />
                  </Route>
                  <Route path="locations" element={<Suspense fallback={<RouteFallback />}><AdminStaffLocations /></Suspense>} />
                  <Route path="attendance" element={<Suspense fallback={<RouteFallback />}><AttendanceReview /></Suspense>} />
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
