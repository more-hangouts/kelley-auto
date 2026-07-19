import { useEffect, useMemo, useState } from 'react'
import {
  Alert,
  Box,
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
import TodayIcon from '@mui/icons-material/Today'

import { getAdminScheduleWeek } from '../services/api'

// Shop hours are Wed–Sun. We render every day for week context but
// emphasize the open ones so the manager's eye lands on them first.
const PRIMARY_DAYS = new Set([3, 4, 5, 6, 0]) // Wed..Sun (JS getDay)

function startOfWeek(d) {
  const day = d.getDay()
  const offset = day === 0 ? 6 : day - 1
  const monday = new Date(d)
  monday.setDate(d.getDate() - offset)
  monday.setHours(0, 0, 0, 0)
  return monday
}

function isoDate(d) {
  const y = d.getFullYear()
  const m = String(d.getMonth() + 1).padStart(2, '0')
  const day = String(d.getDate()).padStart(2, '0')
  return `${y}-${m}-${day}`
}

function addDays(d, n) {
  const out = new Date(d)
  out.setDate(d.getDate() + n)
  return out
}

function parseIsoDate(iso) {
  const [y, m, d] = iso.split('-').map(Number)
  return new Date(y, m - 1, d)
}

function formatDayHeader(iso) {
  const dt = parseIsoDate(iso)
  return dt.toLocaleDateString(undefined, {
    weekday: 'short',
    month: 'short',
    day: 'numeric',
  })
}

function formatTime(iso) {
  if (!iso) return ''
  try {
    return new Date(iso).toLocaleTimeString(undefined, {
      hour: 'numeric',
      minute: '2-digit',
    })
  } catch {
    return iso
  }
}

export default function AdminScheduleFinalizedWeek() {
  const [weekStart, setWeekStart] = useState(() =>
    isoDate(startOfWeek(new Date())),
  )
  const [data, setData] = useState({ staff: [], entries: [], days: [] })
  const [loading, setLoading] = useState(false)
  const [loadError, setLoadError] = useState(null)

  function bumpWeek(deltaDays) {
    const next = addDays(parseIsoDate(weekStart), deltaDays)
    setWeekStart(isoDate(next))
  }

  function goToday() {
    setWeekStart(isoDate(startOfWeek(new Date())))
  }

  useEffect(() => {
    let cancelled = false
    async function load() {
      setLoading(true)
      setLoadError(null)
      try {
        const body = await getAdminScheduleWeek({ week_start: weekStart })
        if (!cancelled) {
          setData(body)
        }
      } catch {
        if (!cancelled) {
          setLoadError("Couldn't load the schedule. Try again.")
          setData({ staff: [], entries: [], days: [] })
        }
      } finally {
        if (!cancelled) setLoading(false)
      }
    }
    load()
    return () => {
      cancelled = true
    }
  }, [weekStart])

  const days = data.days || []
  const staffById = useMemo(() => {
    const map = new Map()
    for (const s of data.staff || []) map.set(s.id, s)
    return map
  }, [data.staff])

  // Group published entries by business_date. Drafts are filtered out
  // entirely — this tab exists so the manager can hand the printed
  // week to staff without worrying that a half-finished draft is
  // showing up.
  const publishedByDay = useMemo(() => {
    const out = new Map()
    for (const e of data.entries || []) {
      if (e.status !== 'published') continue
      const list = out.get(e.business_date) || []
      list.push(e)
      out.set(e.business_date, list)
    }
    for (const list of out.values()) {
      list.sort((a, b) => {
        const cmp = (a.starts_at_local || '').localeCompare(
          b.starts_at_local || '',
        )
        if (cmp !== 0) return cmp
        const an = staffById.get(a.user_id)?.full_name || ''
        const bn = staffById.get(b.user_id)?.full_name || ''
        return an.localeCompare(bn)
      })
    }
    return out
  }, [data.entries, staffById])

  const totalPublished = useMemo(
    () =>
      Array.from(publishedByDay.values()).reduce(
        (acc, list) => acc + list.length,
        0,
      ),
    [publishedByDay],
  )

  return (
    <Stack spacing={2}>
      <Stack
        direction={{ xs: 'column', md: 'row' }}
        justifyContent="space-between"
        alignItems={{ xs: 'flex-start', md: 'center' }}
        spacing={1.5}
      >
        <Stack direction="row" spacing={1} alignItems="center">
          <Tooltip title="Previous week" arrow>
            <span>
              <IconButton
                size="small"
                onClick={() => bumpWeek(-7)}
                disabled={loading}
              >
                <ChevronLeftIcon />
              </IconButton>
            </span>
          </Tooltip>
          <Typography variant="h6" sx={{ minWidth: 220 }}>
            {`Week of ${formatDayHeader(weekStart)}`}
          </Typography>
          <Tooltip title="Next week" arrow>
            <span>
              <IconButton
                size="small"
                onClick={() => bumpWeek(7)}
                disabled={loading}
              >
                <ChevronRightIcon />
              </IconButton>
            </span>
          </Tooltip>
          <Tooltip title="Jump to this week" arrow>
            <span>
              <IconButton
                size="small"
                onClick={goToday}
                disabled={loading}
                sx={{ color: 'text.secondary' }}
              >
                <TodayIcon fontSize="small" />
              </IconButton>
            </span>
          </Tooltip>
        </Stack>
        <Stack direction="row" spacing={1} alignItems="center">
          <Chip
            label={`${totalPublished} published shift${
              totalPublished === 1 ? '' : 's'
            }`}
            size="small"
            color={totalPublished > 0 ? 'success' : 'default'}
            variant={totalPublished > 0 ? 'filled' : 'outlined'}
          />
          {loading && <CircularProgress size={18} />}
        </Stack>
      </Stack>

      {loadError && <Alert severity="error">{loadError}</Alert>}

      {totalPublished === 0 && !loading && !loadError && (
        <Card variant="outlined">
          <CardContent>
            <Typography variant="body2" color="text.secondary">
              No published shifts for this week.
            </Typography>
          </CardContent>
        </Card>
      )}

      <Stack spacing={1.5}>
        {days.map((dayIso) => {
          const dt = parseIsoDate(dayIso)
          const isPrimary = PRIMARY_DAYS.has(dt.getDay())
          const list = publishedByDay.get(dayIso) || []
          if (!isPrimary && list.length === 0) return null
          return (
            <Card
              key={dayIso}
              variant="outlined"
              sx={{
                borderLeft: isPrimary ? 4 : 1,
                borderLeftColor: isPrimary ? 'primary.main' : 'divider',
                opacity: isPrimary ? 1 : 0.85,
              }}
            >
              <CardContent
                sx={{ py: 1.5, '&:last-child': { pb: 1.5 } }}
              >
                <Stack
                  direction={{ xs: 'column', sm: 'row' }}
                  spacing={1.5}
                  alignItems={{ xs: 'flex-start', sm: 'center' }}
                  justifyContent="space-between"
                >
                  <Typography
                    variant={isPrimary ? 'subtitle1' : 'subtitle2'}
                    sx={{
                      fontWeight: isPrimary ? 700 : 500,
                      color: isPrimary ? 'text.primary' : 'text.secondary',
                      minWidth: 180,
                    }}
                  >
                    {formatDayHeader(dayIso)}
                  </Typography>
                  <Box sx={{ flex: 1, width: '100%' }}>
                    {list.length === 0 ? (
                      <Typography
                        variant="body2"
                        color="text.secondary"
                        sx={{ fontStyle: 'italic' }}
                      >
                        No published shifts.
                      </Typography>
                    ) : (
                      <Stack spacing={0.75}>
                        {list.map((e) => {
                          const staff = staffById.get(e.user_id)
                          const name =
                            staff?.full_name ||
                            staff?.username ||
                            `Stylist ${e.user_id}`
                          return (
                            <Stack
                              key={e.id}
                              direction={{ xs: 'column', sm: 'row' }}
                              spacing={{ xs: 0.25, sm: 2 }}
                              alignItems={{ xs: 'flex-start', sm: 'center' }}
                            >
                              <Typography
                                variant="body2"
                                sx={{ fontWeight: 600, minWidth: 200 }}
                              >
                                {name}
                              </Typography>
                              <Typography
                                variant="body2"
                                color="text.secondary"
                              >
                                {formatTime(e.starts_at_local)}
                                {' – '}
                                {formatTime(e.ends_at_local)}
                              </Typography>
                            </Stack>
                          )
                        })}
                      </Stack>
                    )}
                  </Box>
                </Stack>
              </CardContent>
            </Card>
          )
        })}
      </Stack>
    </Stack>
  )
}
