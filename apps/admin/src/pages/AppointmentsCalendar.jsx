import { useMemo, useState } from 'react'
import {
  Alert,
  Box,
  Card,
  Chip,
  CircularProgress,
  IconButton,
  Stack,
  ToggleButton,
  ToggleButtonGroup,
  Tooltip,
  Typography,
  useMediaQuery,
  useTheme,
} from '@mui/material'
import ChevronLeftIcon from '@mui/icons-material/ChevronLeft'
import ChevronRightIcon from '@mui/icons-material/ChevronRight'
import TodayIcon from '@mui/icons-material/Today'
import { useNavigate } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import dayjs from 'dayjs'

import { listAppointments } from '../services/api'

const STATUS_COLORS = {
  confirmed: 'primary',
  attended: 'success',
  no_show: 'warning',
  cancelled: 'default',
  rescheduled: 'info',
  pending: 'secondary',
}

const VIEW_UNIT = { day: 'day', week: 'week', month: 'month' }
const WEEKDAYS = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat']

function apptName(a) {
  return (
    a.crm_event_name ||
    [a.celebrant_first_name, a.celebrant_last_name || a.parent_last_name]
      .filter(Boolean)
      .join(' ') ||
    '—'
  )
}

const ymd = (d) => d.format('YYYY-MM-DD')

// The date window to fetch + render for the active view. Month spills to full
// weeks so the grid has no ragged edges.
function useRange(view, anchor) {
  return useMemo(() => {
    if (view === 'day') {
      return { start: anchor.startOf('day'), end: anchor.endOf('day') }
    }
    if (view === 'week') {
      return { start: anchor.startOf('week'), end: anchor.endOf('week') }
    }
    return {
      start: anchor.startOf('month').startOf('week'),
      end: anchor.endOf('month').endOf('week'),
    }
  }, [view, anchor])
}

function StatusChip({ status }) {
  return (
    <Chip
      size="small"
      label={status}
      color={STATUS_COLORS[status] || 'default'}
      variant={status === 'cancelled' ? 'outlined' : 'filled'}
      sx={{ height: 20, fontSize: 11 }}
    />
  )
}

// One appointment as a clickable pill (→ its deal when linked).
function AppointmentPill({ appt, onOpen, dense = false }) {
  const color = STATUS_COLORS[appt.status] || 'default'
  return (
    <Box
      onClick={() => onOpen(appt)}
      title={`${dayjs(appt.slot_start_at).format('h:mm A')} · ${apptName(appt)} · ${appt.status}`}
      sx={{
        cursor: appt.crm_event_id ? 'pointer' : 'default',
        borderRadius: 1,
        px: 0.75,
        py: 0.25,
        bgcolor: 'background.default',
        borderLeft: '3px solid',
        borderColor: color === 'default' ? 'divider' : `${color}.main`,
        display: 'flex',
        alignItems: 'baseline',
        gap: 0.5,
        overflow: 'hidden',
        opacity: appt.status === 'cancelled' ? 0.55 : 1,
        '&:hover': appt.crm_event_id ? { bgcolor: 'action.hover' } : undefined,
      }}
    >
      <Typography
        variant="caption"
        sx={{ fontWeight: 600, whiteSpace: 'nowrap', fontVariantNumeric: 'tabular-nums' }}
      >
        {dayjs(appt.slot_start_at).format(dense ? 'h:mm' : 'h:mm A')}
      </Typography>
      <Typography variant="caption" noWrap sx={{ minWidth: 0 }}>
        {apptName(appt)}
      </Typography>
    </Box>
  )
}

// Day agenda — a time-ordered list, also reused inside each week column.
function DayAgenda({ appts, onOpen, emptyText = 'No appointments.' }) {
  if (appts.length === 0) {
    return (
      <Typography variant="body2" color="text.secondary" sx={{ fontStyle: 'italic', py: 1 }}>
        {emptyText}
      </Typography>
    )
  }
  return (
    <Stack spacing={0.75} sx={{ py: 0.5 }}>
      {appts.map((a) => (
        <Stack
          key={a.id}
          direction="row"
          spacing={1.5}
          alignItems="center"
          onClick={() => onOpen(a)}
          sx={{
            flexWrap: 'wrap',
            cursor: a.crm_event_id ? 'pointer' : 'default',
            borderRadius: 1,
            px: 1,
            py: 0.75,
            borderLeft: '3px solid',
            borderColor:
              STATUS_COLORS[a.status] === 'default' || !STATUS_COLORS[a.status]
                ? 'divider'
                : `${STATUS_COLORS[a.status]}.main`,
            bgcolor: 'background.default',
            opacity: a.status === 'cancelled' ? 0.6 : 1,
            '&:hover': a.crm_event_id ? { bgcolor: 'action.hover' } : undefined,
          }}
        >
          <Typography
            variant="body2"
            sx={{ minWidth: 78, fontFamily: 'monospace', fontSize: 13 }}
          >
            {dayjs(a.slot_start_at).format('h:mm A')}
          </Typography>
          <Typography variant="body2" sx={{ flexGrow: 1, fontWeight: 500 }}>
            {apptName(a)}
            <Typography component="span" variant="caption" color="text.secondary" sx={{ ml: 1 }}>
              {a.email} · {a.phone_e164 || a.phone}
            </Typography>
          </Typography>
          <StatusChip status={a.status} />
        </Stack>
      ))}
    </Stack>
  )
}

export default function AppointmentsCalendar() {
  const navigate = useNavigate()
  const theme = useTheme()
  const isMobile = useMediaQuery(theme.breakpoints.down('sm'))
  const [view, setView] = useState('week')
  const [anchor, setAnchor] = useState(() => dayjs())

  const { start, end } = useRange(view, anchor)

  const { data, isLoading, error } = useQuery({
    queryKey: ['appointments', 'calendar', ymd(start), ymd(end)],
    queryFn: () =>
      listAppointments({ from: ymd(start), to: ymd(end), limit: 200, offset: 0 }),
  })
  // Memoized so the reference is stable across renders — otherwise `byDay`'s
  // useMemo below (and anything else depending on `items`) would recompute
  // every render because `data?.items || []` is a fresh array each time.
  const items = useMemo(() => data?.items || [], [data])

  const byDay = useMemo(() => {
    const m = new Map()
    items.forEach((a) => {
      const k = dayjs(a.slot_start_at).format('YYYY-MM-DD')
      if (!m.has(k)) m.set(k, [])
      m.get(k).push(a)
    })
    m.forEach((list) =>
      list.sort((x, y) => new Date(x.slot_start_at) - new Date(y.slot_start_at)),
    )
    return m
  }, [items])

  const dayAppts = (d) => byDay.get(d.format('YYYY-MM-DD')) || []
  const openAppt = (a) => {
    if (a.crm_event_id) navigate(`/deals/${a.crm_event_id}`)
  }
  const goToDay = (d) => {
    setAnchor(d)
    setView('day')
  }

  const shift = (dir) => setAnchor((a) => a.add(dir, VIEW_UNIT[view]))
  const today = dayjs()
  const title =
    view === 'day'
      ? anchor.format('dddd, MMMM D, YYYY')
      : view === 'week'
        ? `${start.format('MMM D')} – ${end.format('MMM D, YYYY')}`
        : anchor.format('MMMM YYYY')

  const liveCount = items.filter(
    (a) => a.status === 'confirmed' || a.status === 'pending',
  ).length

  const weekDays = useMemo(() => {
    const days = []
    for (let i = 0; i < 7; i++) days.push(start.add(i, 'day'))
    return days
  }, [start])

  const monthCells = useMemo(() => {
    const cells = []
    let cur = start
    while (cur.isBefore(end) || cur.isSame(end, 'day')) {
      cells.push(cur)
      cur = cur.add(1, 'day')
    }
    return cells
  }, [start, end])

  return (
    <Stack spacing={2}>
      <Stack
        direction={{ xs: 'column', md: 'row' }}
        justifyContent="space-between"
        alignItems={{ xs: 'stretch', md: 'center' }}
        spacing={1.5}
      >
        <Typography variant="h4">Calendar</Typography>
        <Stack
          direction="row"
          spacing={1}
          alignItems="center"
          justifyContent="space-between"
        >
          <ToggleButtonGroup
            size="small"
            exclusive
            value={view}
            onChange={(_e, v) => v && setView(v)}
          >
            <ToggleButton value="day">Day</ToggleButton>
            <ToggleButton value="week">Week</ToggleButton>
            <ToggleButton value="month">Month</ToggleButton>
          </ToggleButtonGroup>
          <Stack direction="row" spacing={0.5} alignItems="center">
            <IconButton size="small" onClick={() => shift(-1)}>
              <ChevronLeftIcon />
            </IconButton>
            <Tooltip title="Jump to today">
              <IconButton size="small" onClick={() => setAnchor(dayjs())}>
                <TodayIcon fontSize="small" />
              </IconButton>
            </Tooltip>
            <IconButton size="small" onClick={() => shift(1)}>
              <ChevronRightIcon />
            </IconButton>
          </Stack>
        </Stack>
      </Stack>

      <Stack
        direction="row"
        justifyContent="space-between"
        alignItems="baseline"
        flexWrap="wrap"
      >
        <Typography variant="h6" sx={{ fontWeight: 600 }}>
          {title}
        </Typography>
        <Typography variant="body2" color="text.secondary">
          {isLoading ? 'Loading…' : `${liveCount} live in view`}
        </Typography>
      </Stack>

      {error && (
        <Alert severity="error">
          {error?.response?.data?.detail || error.message}
        </Alert>
      )}

      {isLoading && items.length === 0 ? (
        <Box sx={{ py: 6, textAlign: 'center' }}>
          <CircularProgress size={24} />
        </Box>
      ) : view === 'day' ? (
        <Card sx={{ p: { xs: 2, sm: 3 } }}>
          <DayAgenda
            appts={dayAppts(anchor)}
            onOpen={openAppt}
            emptyText="No appointments on this day."
          />
        </Card>
      ) : view === 'week' ? (
        <Box
          sx={{
            display: 'grid',
            gridTemplateColumns: { xs: '1fr', sm: 'repeat(7, 1fr)' },
            gap: 1,
          }}
        >
          {weekDays.map((d) => {
            const isToday = d.isSame(today, 'day')
            return (
              <Card
                key={ymd(d)}
                variant="outlined"
                sx={{
                  p: 1,
                  minHeight: { sm: 160 },
                  borderColor: isToday ? 'primary.main' : 'divider',
                }}
              >
                <Box
                  onClick={() => goToDay(d)}
                  sx={{ cursor: 'pointer', mb: 0.5 }}
                >
                  <Typography variant="caption" color="text.secondary">
                    {d.format('ddd')}
                  </Typography>
                  <Typography
                    variant="subtitle2"
                    sx={{
                      fontWeight: isToday ? 700 : 500,
                      color: isToday ? 'primary.main' : 'text.primary',
                    }}
                  >
                    {d.format('MMM D')}
                  </Typography>
                </Box>
                <Stack spacing={0.5}>
                  {dayAppts(d).length === 0 ? (
                    <Typography variant="caption" color="text.disabled">
                      —
                    </Typography>
                  ) : (
                    dayAppts(d).map((a) => (
                      <AppointmentPill key={a.id} appt={a} onOpen={openAppt} dense />
                    ))
                  )}
                </Stack>
              </Card>
            )
          })}
        </Box>
      ) : (
        <Box>
          <Box
            sx={{
              display: 'grid',
              gridTemplateColumns: 'repeat(7, 1fr)',
              gap: 0.5,
              mb: 0.5,
            }}
          >
            {WEEKDAYS.map((w) => (
              <Typography
                key={w}
                variant="caption"
                color="text.secondary"
                sx={{ textAlign: 'center', fontWeight: 600 }}
              >
                {isMobile ? w[0] : w}
              </Typography>
            ))}
          </Box>
          <Box
            sx={{
              display: 'grid',
              gridTemplateColumns: 'repeat(7, 1fr)',
              gap: 0.5,
            }}
          >
            {monthCells.map((d) => {
              const inMonth = d.isSame(anchor, 'month')
              const isToday = d.isSame(today, 'day')
              const appts = dayAppts(d)
              return (
                <Box
                  key={ymd(d)}
                  sx={{
                    minHeight: { xs: 64, sm: 104 },
                    border: '1px solid',
                    borderColor: isToday ? 'primary.main' : 'divider',
                    borderRadius: 1,
                    p: 0.5,
                    bgcolor: inMonth ? 'background.paper' : 'action.hover',
                    opacity: inMonth ? 1 : 0.6,
                    overflow: 'hidden',
                  }}
                >
                  <Typography
                    variant="caption"
                    onClick={() => goToDay(d)}
                    sx={{
                      cursor: 'pointer',
                      display: 'inline-block',
                      fontWeight: isToday ? 700 : 500,
                      color: isToday ? 'primary.main' : 'text.secondary',
                      px: 0.5,
                    }}
                  >
                    {d.format('D')}
                  </Typography>
                  {isMobile ? (
                    appts.length > 0 && (
                      <Box
                        onClick={() => goToDay(d)}
                        sx={{
                          mt: 0.25,
                          mx: 'auto',
                          width: 18,
                          height: 18,
                          borderRadius: '50%',
                          bgcolor: 'primary.main',
                          color: 'primary.contrastText',
                          fontSize: 11,
                          fontWeight: 700,
                          display: 'flex',
                          alignItems: 'center',
                          justifyContent: 'center',
                          cursor: 'pointer',
                        }}
                      >
                        {appts.length}
                      </Box>
                    )
                  ) : (
                    <Stack spacing={0.25} sx={{ mt: 0.25 }}>
                      {appts.slice(0, 3).map((a) => (
                        <AppointmentPill key={a.id} appt={a} onOpen={openAppt} dense />
                      ))}
                      {appts.length > 3 && (
                        <Typography
                          variant="caption"
                          color="text.secondary"
                          onClick={() => goToDay(d)}
                          sx={{ cursor: 'pointer', pl: 0.5 }}
                        >
                          +{appts.length - 3} more
                        </Typography>
                      )}
                    </Stack>
                  )}
                </Box>
              )
            })}
          </Box>
        </Box>
      )}
    </Stack>
  )
}
