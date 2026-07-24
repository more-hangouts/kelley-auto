import { useState } from 'react'
import { Box, Button, Stack, Typography } from '@mui/material'
import AddIcon from '@mui/icons-material/Add'

import { useSalesAuth } from '../contexts/SalesAuthContext'
import AppointmentsToday from './AppointmentsToday'
import LeadSearch from './LeadSearch'
import SalesWalkInDialog from './SalesWalkInDialog'

function pickGreeting(now) {
  const h = now.getHours()
  if (h < 12) return 'Good morning'
  if (h < 18) return 'Good afternoon'
  return 'Good evening'
}

export default function RepDashboard() {
  const { user } = useSalesAuth()
  const [walkInOpen, setWalkInOpen] = useState(false)
  const [appointmentsRefreshKey, setAppointmentsRefreshKey] = useState(0)
  const displayName = user?.full_name || user?.username || ''
  const firstName = displayName.split(' ')[0]
  return (
    <Stack spacing={3}>
      <Stack
        direction={{ xs: 'column', sm: 'row' }}
        justifyContent="space-between"
        alignItems={{ xs: 'flex-start', sm: 'center' }}
        spacing={1.5}
      >
        <Box>
          <Typography
            variant="overline"
            color="text.secondary"
            sx={{ letterSpacing: 1 }}
          >
            Dashboard
          </Typography>
          <Typography variant="h4" sx={{ fontWeight: 600, lineHeight: 1.2 }}>
            {pickGreeting(new Date())}
            {firstName ? `, ${firstName}` : ''}.
          </Typography>
        </Box>
        <Button
          variant="contained"
          startIcon={<AddIcon />}
          onClick={() => setWalkInOpen(true)}
        >
          Add walk-in
        </Button>
      </Stack>
      <LeadSearch />
      <AppointmentsToday refreshKey={appointmentsRefreshKey} />
      <SalesWalkInDialog
        open={walkInOpen}
        onClose={() => setWalkInOpen(false)}
        onCreated={() => setAppointmentsRefreshKey((n) => n + 1)}
      />
    </Stack>
  )
}
