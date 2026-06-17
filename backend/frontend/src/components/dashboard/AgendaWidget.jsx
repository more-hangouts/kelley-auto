import {
  Alert,
  Box,
  Button,
  Card,
  CardContent,
  Chip,
  CircularProgress,
  Stack,
  Typography,
} from '@mui/material'
import CalendarMonthOutlinedIcon from '@mui/icons-material/CalendarMonthOutlined'
import { useQuery } from '@tanstack/react-query'
import { Link as RouterLink } from 'react-router-dom'
import dayjs from 'dayjs'

import { getAgendaToday } from '../../services/api'
import { useCommandPalette } from '../../contexts/CommandPaletteContext'

const STATUS_COLOR = {
  confirmed: 'default',
  attended: 'success',
  no_show: 'error',
  cancelled: 'warning',
}

const STATUS_LABEL = {
  confirmed: 'Confirmed',
  attended: 'Attended',
  no_show: 'No-show',
  cancelled: 'Cancelled',
}

function rowRoute(item) {
  if (item.crm_event_id) return `/events/${item.crm_event_id}/overview`
  return '/calendar'
}

export default function AgendaWidget() {
  const palette = useCommandPalette()
  const query = useQuery({
    queryKey: ['dashboard', 'agenda-today'],
    queryFn: getAgendaToday,
    staleTime: 60_000,
  })

  const appointments = query.data?.appointments ?? []

  return (
    <Card>
      <CardContent>
        <Stack
          direction="row"
          spacing={1}
          alignItems="center"
          sx={{ mb: 1.5 }}
        >
          <CalendarMonthOutlinedIcon color="action" />
          <Typography variant="h6">Today's agenda</Typography>
        </Stack>

        {query.isLoading ? (
          <Box sx={{ display: 'flex', justifyContent: 'center', py: 2 }}>
            <CircularProgress size={20} />
          </Box>
        ) : query.error ? (
          <Alert severity="error">Could not load today's appointments.</Alert>
        ) : appointments.length === 0 ? (
          <Stack spacing={1.25} alignItems="flex-start">
            <Typography variant="body2" color="text.secondary">
              No appointments today.
            </Typography>
            <Stack direction={{ xs: 'column', sm: 'row' }} spacing={1}>
              <Button
                size="small"
                variant="contained"
                onClick={palette.openNewLead}
              >
                Add walk-in lead
              </Button>
              <Button
                size="small"
                variant="outlined"
                component={RouterLink}
                to="/calendar"
              >
                Open calendar
              </Button>
            </Stack>
          </Stack>
        ) : (
          <Stack divider={<Box sx={{ borderTop: '1px solid', borderColor: 'divider' }} />}>
            {appointments.map((a) => (
              <Box
                key={a.id}
                component={RouterLink}
                to={rowRoute(a)}
                sx={{
                  color: 'inherit',
                  textDecoration: 'none',
                  '&:hover': { bgcolor: 'rgba(93, 58, 107, 0.04)' },
                  px: 1,
                  mx: -1,
                  borderRadius: 1,
                  display: 'flex',
                  alignItems: 'center',
                  gap: 1.5,
                  py: 1,
                }}
              >
                <Box sx={{ minWidth: 56 }}>
                  <Typography variant="body2" sx={{ fontWeight: 600 }}>
                    {dayjs(a.slot_start_at).format('h:mm A')}
                  </Typography>
                </Box>
                <Box sx={{ flex: 1, minWidth: 0 }}>
                  <Typography variant="body2" sx={{ fontWeight: 500 }} noWrap>
                    {a.display_name}
                  </Typography>
                  <Typography variant="caption" color="text.secondary">
                    {a.party_size_bucket}
                  </Typography>
                </Box>
                <Chip
                  label={STATUS_LABEL[a.status] || a.status}
                  size="small"
                  color={STATUS_COLOR[a.status] || 'default'}
                  variant={a.status === 'confirmed' ? 'outlined' : 'filled'}
                />
              </Box>
            ))}
          </Stack>
        )}
      </CardContent>
    </Card>
  )
}
