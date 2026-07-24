import { useState } from 'react'
import { Box, Button, Paper, Stack, Typography } from '@mui/material'
import AddIcon from '@mui/icons-material/Add'
import PhoneOutlinedIcon from '@mui/icons-material/PhoneOutlined'
import { useQuery } from '@tanstack/react-query'

import { useSalesAuth } from '../contexts/SalesAuthContext'
import { getMyCallsToday } from '../services/api'
import AppointmentsToday from './AppointmentsToday'
import LeadSearch from './LeadSearch'
import SalesWalkInDialog from './SalesWalkInDialog'

function CallsTodayTile() {
  const { data } = useQuery({
    queryKey: ['my-calls-today'],
    queryFn: getMyCallsToday,
  })
  const count = data?.calls_today ?? 0
  return (
    <Paper variant="outlined" sx={{ borderRadius: 2, px: 2.5, py: 2, display: 'flex', alignItems: 'center', gap: 2 }}>
      <PhoneOutlinedIcon color="primary" />
      <Box>
        <Typography variant="h5" sx={{ fontWeight: 600, lineHeight: 1 }}>
          {count}
        </Typography>
        <Typography variant="body2" color="text.secondary">
          {count === 1 ? 'Call logged today' : 'Calls logged today'}
        </Typography>
      </Box>
    </Paper>
  )
}

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
      <CallsTodayTile />
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
