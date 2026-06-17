import { useEffect, useState } from 'react'
import { Link as RouterLink } from 'react-router-dom'
import {
  Alert,
  Box,
  Card,
  CardActionArea,
  CardContent,
  Chip,
  CircularProgress,
  FormControlLabel,
  Stack,
  Switch,
  Tooltip,
  Typography,
} from '@mui/material'

import { salesListAppointmentsToday } from '../services/api'

const MINE_TOGGLE_KEY = 'bellas_xv_sales_today_mine'

const STATUS_COLORS = {
  confirmed: 'primary',
  pending: 'default',
  attended: 'success',
  no_show: 'warning',
  cancelled: 'default',
}

function formatLocalTime(iso, tz) {
  if (!iso) return ''
  try {
    return new Intl.DateTimeFormat(undefined, {
      hour: 'numeric',
      minute: '2-digit',
      timeZone: tz,
    }).format(new Date(iso))
  } catch {
    return new Date(iso).toLocaleTimeString()
  }
}

function partySizeLabel(bucket) {
  switch (bucket) {
    case 'solo':
      return 'Solo'
    case 'pair':
    case '2_3':
      return 'Small group'
    case '3_4':
      return 'Medium group'
    case '4_plus':
    case '5_plus':
      return 'Large group'
    default:
      return bucket
  }
}

function enrichmentLine(summary) {
  if (!summary) return null
  const bits = []
  if (summary.budget_range) bits.push(summary.budget_range)
  if (summary.quince_theme) bits.push(summary.quince_theme)
  if (summary.dress_styles?.length) bits.push(summary.dress_styles.join(', '))
  return bits.length ? bits.join(' · ') : null
}

function fullName(first, last) {
  return [first, last].filter(Boolean).join(' ').trim()
}

export default function AppointmentsToday({ refreshKey = 0 }) {
  const [data, setData] = useState(null)
  const [error, setError] = useState(null)
  const [mine, setMine] = useState(() => {
    try {
      return localStorage.getItem(MINE_TOGGLE_KEY) === '1'
    } catch {
      return false
    }
  })

  useEffect(() => {
    let cancelled = false
    setError(null)
    setData((prev) => prev) // keep prior data while refetching
    salesListAppointmentsToday({ mine })
      .then((d) => {
        if (!cancelled) setData(d)
      })
      .catch((err) => {
        if (cancelled) return
        setError(err?.response?.data?.detail || 'Could not load today.')
      })
    return () => {
      cancelled = true
    }
  }, [mine, refreshKey])

  function handleToggleMine(checked) {
    setMine(checked)
    try {
      localStorage.setItem(MINE_TOGGLE_KEY, checked ? '1' : '0')
    } catch {
      /* ignore */
    }
  }

  // The toggle stays disabled until at least one appointment in
  // today's window has assigned_user_id set. Phase 0 confirmed nothing
  // populates that column today; silently filtering on it would render
  // an empty list and look broken.
  const toggleDisabled = !data?.has_assigned && !mine

  return (
    <Stack spacing={2}>
      <Stack
        direction={{ xs: 'column', sm: 'row' }}
        justifyContent="space-between"
        alignItems={{ xs: 'flex-start', sm: 'center' }}
        spacing={1}
      >
        <Box>
          <Typography variant="h5" sx={{ fontWeight: 600 }}>
            Today
          </Typography>
          <Typography variant="body2" color="text.secondary">
            {data?.date
              ? new Intl.DateTimeFormat(undefined, {
                  weekday: 'long',
                  month: 'long',
                  day: 'numeric',
                }).format(new Date(`${data.date}T00:00:00`))
              : '—'}
          </Typography>
        </Box>
        <Tooltip
          title={
            toggleDisabled
              ? "Available once appointments get assigned to specific stylists"
              : ''
          }
          placement="left"
        >
          <FormControlLabel
            control={
              <Switch
                checked={mine}
                onChange={(e) => handleToggleMine(e.target.checked)}
                disabled={toggleDisabled}
              />
            }
            label="Mine only"
          />
        </Tooltip>
      </Stack>

      {error && <Alert severity="error">{error}</Alert>}

      {data === null && !error ? (
        <Box sx={{ display: 'flex', justifyContent: 'center', py: 6 }}>
          <CircularProgress />
        </Box>
      ) : data?.appointments.length === 0 ? (
        <Card variant="outlined">
          <CardContent>
            <Typography variant="body1" sx={{ fontWeight: 500 }}>
              No appointments today.
            </Typography>
            <Typography variant="body2" color="text.secondary" sx={{ mt: 0.5 }}>
              {mine
                ? 'Nothing on your calendar. Toggle off "Mine only" to see the floor.'
                : 'Enjoy the breather.'}
            </Typography>
          </CardContent>
        </Card>
      ) : (
        <Stack spacing={1.5}>
          {data?.appointments.map((appt) => (
            <Card key={appt.id} variant="outlined">
              <CardActionArea component={RouterLink} to={`/appointments/${appt.id}`}>
                <CardContent>
                  <Stack direction="row" justifyContent="space-between" spacing={2}>
                    <Box sx={{ minWidth: 0 }}>
                      <Typography
                        variant="subtitle2"
                        color="text.secondary"
                        sx={{ fontVariantNumeric: 'tabular-nums' }}
                      >
                        {formatLocalTime(appt.slot_start_at, appt.timezone)} ·{' '}
                        {appt.slot_duration_minutes} min
                      </Typography>
                      <Typography variant="h6" sx={{ fontWeight: 600 }}>
                        {fullName(appt.celebrant_first_name, appt.celebrant_last_name) ||
                          '(no name)'}
                      </Typography>
                      <Typography variant="body2" color="text.secondary" noWrap>
                        {fullName(appt.parent_first_name, appt.parent_last_name) ||
                          'Parent unknown'}{' '}
                        · {partySizeLabel(appt.party_size_bucket)}
                      </Typography>
                      {enrichmentLine(appt.enrichment_summary) && (
                        <Typography
                          variant="caption"
                          color="text.secondary"
                          display="block"
                          sx={{ mt: 0.5 }}
                          noWrap
                        >
                          {enrichmentLine(appt.enrichment_summary)}
                        </Typography>
                      )}
                    </Box>
                    <Stack alignItems="flex-end" spacing={0.5}>
                      <Chip
                        size="small"
                        label={appt.status.replace('_', ' ')}
                        color={STATUS_COLORS[appt.status] || 'default'}
                        variant={appt.status === 'attended' ? 'filled' : 'outlined'}
                      />
                      {appt.crm_event_status && (
                        <Chip
                          size="small"
                          label={appt.crm_event_status}
                          variant="outlined"
                        />
                      )}
                    </Stack>
                  </Stack>
                </CardContent>
              </CardActionArea>
            </Card>
          ))}
        </Stack>
      )}
    </Stack>
  )
}
