import { Box, Tab, Tabs } from '@mui/material'
import { Outlet, matchPath, useLocation, useNavigate } from 'react-router-dom'

import SettingsPageHeader from '../components/SettingsPageHeader'

// Wraps every /settings/staff/* route. Renders the shared breadcrumb
// header and a tab bar; the active route's element renders into the
// <Outlet />. Tabs are nested routes (not local state), so the URL is
// always the source of truth and the back button moves tab-by-tab.
//
// The `paths` list also matches deeper routes (e.g. the per-stylist
// schedule page beneath /settings/staff/profiles), so drilling into a
// sub-page keeps the parent tab highlighted.

const TABS = [
  { value: '/settings/staff/profiles', label: 'Staff profiles', paths: ['/settings/staff/profiles/*'] },
  { value: '/settings/staff/schedule', label: 'Schedule & time off', paths: ['/settings/staff/schedule/*'] },
  { value: '/settings/staff/locations', label: 'Locations', paths: ['/settings/staff/locations'] },
  { value: '/settings/staff/attendance', label: 'Attendance review', paths: ['/settings/staff/attendance'] },
]

function activeTab(pathname) {
  for (const tab of TABS) {
    for (const pattern of tab.paths) {
      if (matchPath({ path: pattern, end: pattern.endsWith('*') ? false : true }, pathname)) {
        return tab.value
      }
    }
  }
  return TABS[0].value
}

export default function StaffManagementLayout() {
  const { pathname } = useLocation()
  const navigate = useNavigate()
  const current = activeTab(pathname)

  return (
    <Box>
      <SettingsPageHeader
        crumbs={[
          { label: 'Settings', to: '/settings' },
          { label: 'Staff management' },
        ]}
      />
      <Box sx={{ borderBottom: 1, borderColor: 'divider', mb: 2.5 }}>
        <Tabs
          value={current}
          onChange={(_, value) => navigate(value)}
          variant="scrollable"
          scrollButtons="auto"
        >
          {TABS.map((tab) => (
            <Tab key={tab.value} value={tab.value} label={tab.label} />
          ))}
        </Tabs>
      </Box>
      <Outlet />
    </Box>
  )
}
