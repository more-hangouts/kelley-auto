import { Box, Tab, Tabs } from '@mui/material'
import { Outlet, useLocation, useNavigate } from 'react-router-dom'

// Second-level tab bar inside Staff Management > Schedule & time off.
// Keeps Time off (approval queue) and Holidays (advisory calendar)
// next to each other without crowding either page.

const SUBTABS = [
  { value: '/settings/staff/schedule/grid', label: 'Schedule grid' },
  { value: '/settings/staff/schedule/finalized', label: 'Finalized Week' },
  { value: '/settings/staff/schedule/presets', label: 'Shift presets' },
  { value: '/settings/staff/schedule/time-off', label: 'Time off' },
  {
    value: '/settings/staff/schedule/shift-requests',
    label: 'Shift requests',
  },
  {
    value: '/settings/staff/schedule/open-shifts',
    label: 'Open shifts',
  },
  { value: '/settings/staff/schedule/holidays', label: 'Holidays' },
]

function activeSubtab(pathname) {
  return SUBTABS.find((t) => pathname.startsWith(t.value))?.value ?? SUBTABS[0].value
}

export default function StaffScheduleLayout() {
  const { pathname } = useLocation()
  const navigate = useNavigate()
  const current = activeSubtab(pathname)

  return (
    <Box>
      <Box sx={{ borderBottom: 1, borderColor: 'divider', mb: 2.5 }}>
        <Tabs value={current} onChange={(_, value) => navigate(value)}>
          {SUBTABS.map((tab) => (
            <Tab key={tab.value} value={tab.value} label={tab.label} />
          ))}
        </Tabs>
      </Box>
      <Outlet />
    </Box>
  )
}
