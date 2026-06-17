import { useEffect, useMemo, useState } from 'react'
import {
  Alert,
  Box,
  Button,
  Card,
  CardContent,
  Chip,
  CircularProgress,
  IconButton,
  Stack,
  Tooltip,
  Typography,
} from '@mui/material'
import ChevronLeftIcon from '@mui/icons-material/ChevronLeft'
import ChevronRightIcon from '@mui/icons-material/ChevronRight'
import RefreshIcon from '@mui/icons-material/Refresh'

import { listAppointments } from '../services/api'
import { useCommandPalette } from '../contexts/CommandPaletteContext'

const RANGE_DAYS = 14

const STATUS_COLORS = {
  confirmed: 'primary',
  attended: 'success',
  no_show: 'warning',
  cancelled: 'default',
  rescheduled: 'info',
  pending: 'secondary',
}

function startOfRange(offsetWeeks) {
  const d = new Date()
  d.setHours(0, 0, 0, 0)
  d.setDate(d.getDate() + offsetWeeks * RANGE_DAYS)
  return d
}

function ymd(d) {
  const y = d.getFullYear()
  const m = String(d.getMonth() + 1).padStart(2, '0')
  const day = String(d.getDate()).padStart(2, '0')
  return `${y}-${m}-${day}`
}

function dayLabel(d) {
  return d.toLocaleDateString(undefined, {
    weekday: 'long',
    month: 'short',
    day: 'numeric',
  })
}

function timeLabel(iso) {
  return new Date(iso).toLocaleTimeString(undefined, {
    hour: 'numeric',
    minute: '2-digit',
  })
}

export default function AppointmentsCalendar() {
  const palette = useCommandPalette()
  const [offsetWeeks, setOffsetWeeks] = useState(0)
  const [items, setItems] = useState([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)

  const rangeStart = useMemo(() => startOfRange(offsetWeeks), [offsetWeeks])
  const rangeEnd = useMemo(() => {
    const d = new Date(rangeStart)
    d.setDate(d.getDate() + RANGE_DAYS - 1)
    return d
  }, [rangeStart])

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    setError(null)
    // Pull a generous limit; for v1 a 14-day window won't realistically exceed 200.
    listAppointments({
      from: ymd(rangeStart),
      to: ymd(rangeEnd),
      limit: 200,
      offset: 0,
    })
      .then((data) => !cancelled && setItems(data.items))
      .catch((err) => !cancelled && setError(err?.response?.data?.detail || err.message))
      .finally(() => !cancelled && setLoading(false))
    return () => {
      cancelled = true
    }
  }, [rangeStart, rangeEnd])

  const buckets = useMemo(() => {
    // Build empty buckets for every day in the range so we can render gaps too.
    const map = new Map()
    for (let i = 0; i < RANGE_DAYS; i++) {
      const d = new Date(rangeStart)
      d.setDate(d.getDate() + i)
      map.set(ymd(d), { date: new Date(d), entries: [] })
    }
    items.forEach((appt) => {
      const dayKey = ymd(new Date(appt.slot_start_at))
      const bucket = map.get(dayKey)
      if (bucket) bucket.entries.push(appt)
    })
    map.forEach((bucket) => {
      bucket.entries.sort(
        (a, b) => new Date(a.slot_start_at) - new Date(b.slot_start_at),
      )
    })
    return Array.from(map.values())
  }, [items, rangeStart])

  const totalLive = useMemo(
    () =>
      items.filter((a) => a.status === 'confirmed' || a.status === 'pending').length,
    [items],
  )

  return (
    <Stack spacing={2}>
      <Stack
        direction={{ xs: 'column', sm: 'row' }}
        justifyContent="space-between"
        alignItems={{ xs: 'flex-start', sm: 'center' }}
        spacing={1}
      >
        <Typography variant="h4">Calendar</Typography>
        <Stack direction="row" spacing={1} alignItems="center">
          <IconButton onClick={() => setOffsetWeeks((v) => v - 1)} size="small">
            <ChevronLeftIcon />
          </IconButton>
          <Typography variant="body2" color="text.secondary" sx={{ minWidth: 220, textAlign: 'center' }}>
            {dayLabel(rangeStart)} → {dayLabel(rangeEnd)}
          </Typography>
          <IconButton onClick={() => setOffsetWeeks((v) => v + 1)} size="small">
            <ChevronRightIcon />
          </IconButton>
          <Tooltip title={offsetWeeks === 0 ? 'Already on today' : 'Jump to today'}>
            <span>
              <IconButton
                size="small"
                onClick={() => setOffsetWeeks(0)}
                disabled={offsetWeeks === 0}
              >
                <RefreshIcon />
              </IconButton>
            </span>
          </Tooltip>
        </Stack>
      </Stack>

      {error && <Alert severity="error">{error}</Alert>}

      <Card>
        <CardContent sx={{ p: { xs: 2, sm: 3 } }}>
          <Typography variant="body2" color="text.secondary" mb={2}>
            {loading ? 'Loading…' : `${totalLive} live appointment${totalLive === 1 ? '' : 's'} in this window`}
          </Typography>

          {loading && items.length === 0 ? (
            <Box sx={{ py: 6, textAlign: 'center' }}>
              <CircularProgress size={24} />
            </Box>
          ) : items.length === 0 ? (
            <Box sx={{ py: 5, textAlign: 'center' }}>
              <Stack spacing={1.5} alignItems="center">
                <Typography color="text.secondary">
                  No appointments in this window.
                </Typography>
                <Stack direction={{ xs: 'column', sm: 'row' }} spacing={1}>
                  <Button variant="contained" onClick={palette.openNewLead}>
                    Add walk-in lead
                  </Button>
                  <Button
                    variant="outlined"
                    onClick={() => setOffsetWeeks(0)}
                    disabled={offsetWeeks === 0}
                  >
                    Jump to today
                  </Button>
                </Stack>
              </Stack>
            </Box>
          ) : (
            <Stack spacing={2} divider={<Box sx={{ borderTop: '1px solid', borderColor: 'divider' }} />}>
              {buckets.map((b) => {
                const isToday =
                  ymd(b.date) === ymd(new Date()) && offsetWeeks === 0
                return (
                  <Stack
                    key={ymd(b.date)}
                    direction={{ xs: 'column', sm: 'row' }}
                    spacing={2}
                    sx={{ pt: 1 }}
                  >
                    <Box sx={{ minWidth: 180 }}>
                      <Typography
                        variant="subtitle2"
                        sx={{
                          fontWeight: isToday ? 700 : 500,
                          color: isToday ? 'primary.main' : 'text.primary',
                        }}
                      >
                        {dayLabel(b.date)}
                        {isToday && (
                          <Chip
                            size="small"
                            label="today"
                            color="primary"
                            sx={{ ml: 1, height: 18 }}
                          />
                        )}
                      </Typography>
                    </Box>
                    <Box sx={{ flexGrow: 1 }}>
                      {b.entries.length === 0 ? (
                        <Typography variant="body2" color="text.secondary" sx={{ fontStyle: 'italic' }}>
                          No appointments.
                        </Typography>
                      ) : (
                        <Stack spacing={0.75}>
                          {b.entries.map((appt) => (
                            <Stack
                              key={appt.id}
                              direction="row"
                              spacing={1.5}
                              alignItems="center"
                              sx={{ flexWrap: 'wrap' }}
                            >
                              <Typography variant="body2" sx={{ minWidth: 80, fontFamily: 'monospace', fontSize: 13 }}>
                                {timeLabel(appt.slot_start_at)}
                              </Typography>
                              <Typography variant="body2" sx={{ flexGrow: 1 }}>
                                {[
                                  appt.celebrant_first_name,
                                  appt.parent_last_name || appt.celebrant_last_name,
                                ]
                                  .filter(Boolean)
                                  .join(' ') || '—'}
                                <Typography
                                  component="span"
                                  variant="caption"
                                  color="text.secondary"
                                  sx={{ ml: 1 }}
                                >
                                  {(appt.parent_first_name || appt.parent_last_name) && (
                                    <>
                                      booked by {[appt.parent_first_name, appt.parent_last_name]
                                        .filter(Boolean)
                                        .join(' ')}
                                      {' · '}
                                    </>
                                  )}
                                  {appt.email} · {appt.phone_e164 || appt.phone}
                                </Typography>
                              </Typography>
                              <Chip
                                size="small"
                                label={appt.status}
                                color={STATUS_COLORS[appt.status] || 'default'}
                                variant={appt.status === 'cancelled' ? 'outlined' : 'filled'}
                              />
                              {appt.utm_source && (
                                <Chip
                                  size="small"
                                  label={appt.utm_source}
                                  variant="outlined"
                                />
                              )}
                            </Stack>
                          ))}
                        </Stack>
                      )}
                    </Box>
                  </Stack>
                )
              })}
            </Stack>
          )}
        </CardContent>
      </Card>
    </Stack>
  )
}
