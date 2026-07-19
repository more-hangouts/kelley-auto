import { Fragment, useState } from 'react'
import {
  Alert,
  Box,
  Chip,
  CircularProgress,
  Collapse,
  IconButton,
  Paper,
  Stack,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableRow,
  ToggleButton,
  ToggleButtonGroup,
  Tooltip,
  Typography,
} from '@mui/material'
import KeyboardArrowDownIcon from '@mui/icons-material/KeyboardArrowDown'
import KeyboardArrowRightIcon from '@mui/icons-material/KeyboardArrowRight'
import RefreshIcon from '@mui/icons-material/Refresh'
import { useQuery } from '@tanstack/react-query'

import { getSalesActivityRepRecent, getSalesActivitySummary } from '../services/api'

// Phase 14.4: owner-visible view of commission-mode sales activity — who
// is actually using the app and reviewing leads/contacts. Reads the
// admin summary endpoint (per-rep counts + last seen) with a per-rep
// drilldown into recent events. Deliberately separate from payroll
// attendance: these are app-usage signals, not approved hours.

const RANGES = [
  { value: 'today', label: 'Today' },
  { value: 'yesterday', label: 'Yesterday' },
  { value: 'week', label: 'Last 7 days' },
]

const ACTIVITY_LABELS = {
  'sales.lead_viewed': 'Lead viewed',
  'sales.appointment_viewed': 'Appointment viewed',
  'sales.contact_viewed': 'Contact viewed',
  'sales.search_performed': 'Search',
}

// "Active in the last 5 minutes" — mirrors the recorder's throttle window
// and reads as "currently in the app" for the owner.
const LIVE_WINDOW_MS = 5 * 60 * 1000

function relativeTime(iso) {
  if (!iso) return '—'
  const then = new Date(iso).getTime()
  if (Number.isNaN(then)) return '—'
  const secs = Math.round((Date.now() - then) / 1000)
  if (secs < 45) return 'just now'
  const mins = Math.round(secs / 60)
  if (mins < 60) return `${mins}m ago`
  const hrs = Math.round(mins / 60)
  if (hrs < 24) return `${hrs}h ago`
  const days = Math.round(hrs / 24)
  return `${days}d ago`
}

function isLive(iso) {
  if (!iso) return false
  const then = new Date(iso).getTime()
  return !Number.isNaN(then) && Date.now() - then <= LIVE_WINDOW_MS
}

function activityLabel(type) {
  return ACTIVITY_LABELS[type] || type
}

function activityDetail(row) {
  const parts = []
  if (row.subject_kind && row.subject_id != null) {
    parts.push(`${row.subject_kind} #${row.subject_id}`)
  }
  const meta = row.metadata || {}
  if (row.activity_type === 'sales.search_performed') {
    if (typeof meta.result_count === 'number') {
      parts.push(`${meta.result_count} result${meta.result_count === 1 ? '' : 's'}`)
    }
  } else if (typeof meta.crm_event_id === 'number') {
    parts.push(`deal #${meta.crm_event_id}`)
  }
  return parts.join(' · ')
}

function RepRecentRows({ userId }) {
  const { data, isLoading, isError } = useQuery({
    queryKey: ['sales-activity-recent', userId],
    queryFn: () => getSalesActivityRepRecent(userId, { limit: 50 }),
  })

  if (isLoading) {
    return (
      <Box sx={{ py: 2, display: 'flex', justifyContent: 'center' }}>
        <CircularProgress size={18} />
      </Box>
    )
  }
  if (isError) {
    return (
      <Alert severity="error" sx={{ my: 1 }}>
        Couldn't load recent activity.
      </Alert>
    )
  }
  const rows = data?.rows || []
  if (rows.length === 0) {
    return (
      <Typography variant="body2" color="text.secondary" sx={{ py: 1.5, px: 2 }}>
        No recorded activity for this rep.
      </Typography>
    )
  }
  return (
    <Table size="small" sx={{ bgcolor: 'action.hover' }}>
      <TableBody>
        {rows.map((r) => (
          <TableRow key={r.id}>
            <TableCell sx={{ width: 180, whiteSpace: 'nowrap' }}>
              {activityLabel(r.activity_type)}
            </TableCell>
            <TableCell sx={{ color: 'text.secondary' }}>
              {activityDetail(r)}
            </TableCell>
            <TableCell
              align="right"
              sx={{ width: 120, whiteSpace: 'nowrap', color: 'text.secondary' }}
            >
              <Tooltip title={new Date(r.created_at).toLocaleString()}>
                <span>{relativeTime(r.created_at)}</span>
              </Tooltip>
            </TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  )
}

function CountCell({ value }) {
  return (
    <TableCell align="right" sx={{ fontVariantNumeric: 'tabular-nums' }}>
      <Typography
        component="span"
        sx={{ fontWeight: value ? 600 : 400, color: value ? 'text.primary' : 'text.disabled' }}
      >
        {value ?? 0}
      </Typography>
    </TableCell>
  )
}

export default function SalesActivity() {
  const [range, setRange] = useState('today')
  const [expanded, setExpanded] = useState(null)

  const { data, isLoading, isError, isFetching, refetch } = useQuery({
    queryKey: ['sales-activity-summary', range],
    queryFn: () => getSalesActivitySummary({ range }),
    // Poll so the owner sees reps light up without a manual refresh.
    refetchInterval: 30_000,
  })

  const reps = data?.reps || []

  return (
    <Box>
      <Stack
        direction="row"
        alignItems="flex-start"
        justifyContent="space-between"
        spacing={2}
        sx={{ mb: 2 }}
      >
        <Box>
          <Typography variant="h4" gutterBottom>
            Rep activity
          </Typography>
          <Typography variant="body2" color="text.secondary">
            Who's active in the app and reviewing leads &amp; contacts. App-usage
            signals only — not payroll hours.
          </Typography>
        </Box>
        <Tooltip title="Refresh">
          <span>
            <IconButton onClick={() => refetch()} disabled={isFetching}>
              <RefreshIcon />
            </IconButton>
          </span>
        </Tooltip>
      </Stack>

      <ToggleButtonGroup
        value={range}
        exclusive
        size="small"
        onChange={(_e, v) => v && setRange(v)}
        sx={{ mb: 2 }}
      >
        {RANGES.map((r) => (
          <ToggleButton key={r.value} value={r.value}>
            {r.label}
          </ToggleButton>
        ))}
      </ToggleButtonGroup>

      {isError && (
        <Alert severity="error" sx={{ mb: 2 }}>
          Couldn't load sales activity.
        </Alert>
      )}

      {isLoading ? (
        <Box sx={{ display: 'flex', justifyContent: 'center', py: 6 }}>
          <CircularProgress />
        </Box>
      ) : reps.length === 0 ? (
        <Paper variant="outlined" sx={{ p: 4, textAlign: 'center' }}>
          <Typography color="text.secondary">
            No rep activity in this range yet.
          </Typography>
        </Paper>
      ) : (
        <TableContainer component={Paper} variant="outlined">
          <Table>
            <TableHead>
              <TableRow>
                <TableCell sx={{ width: 40 }} />
                <TableCell>Rep</TableCell>
                <TableCell align="right">Leads</TableCell>
                <TableCell align="right">Contacts</TableCell>
                <TableCell align="right">Searches</TableCell>
                <TableCell align="right">Appts</TableCell>
                <TableCell align="right">Last active</TableCell>
              </TableRow>
            </TableHead>
            <TableBody>
              {reps.map((rep) => {
                const open = expanded === rep.actor_user_id
                const live = isLive(rep.last_activity_at)
                return (
                  <Fragment key={rep.actor_user_id}>
                    <TableRow
                      hover
                      sx={{ cursor: 'pointer', '& > *': { borderBottom: open ? 'unset' : undefined } }}
                      onClick={() =>
                        setExpanded(open ? null : rep.actor_user_id)
                      }
                    >
                      <TableCell>
                        <IconButton size="small">
                          {open ? (
                            <KeyboardArrowDownIcon fontSize="small" />
                          ) : (
                            <KeyboardArrowRightIcon fontSize="small" />
                          )}
                        </IconButton>
                      </TableCell>
                      <TableCell>
                        <Stack direction="row" spacing={1} alignItems="center">
                          <Typography sx={{ fontWeight: 600 }}>
                            {rep.full_name || rep.username || `User #${rep.actor_user_id}`}
                          </Typography>
                          {live && (
                            <Chip
                              size="small"
                              color="success"
                              label="Active in app"
                              sx={{ height: 20 }}
                            />
                          )}
                        </Stack>
                      </TableCell>
                      <CountCell value={rep.leads_viewed} />
                      <CountCell value={rep.contacts_viewed} />
                      <CountCell value={rep.searches} />
                      <CountCell value={rep.appointments_viewed} />
                      <TableCell align="right" sx={{ whiteSpace: 'nowrap', color: 'text.secondary' }}>
                        <Tooltip
                          title={
                            rep.last_activity_at
                              ? new Date(rep.last_activity_at).toLocaleString()
                              : ''
                          }
                        >
                          <span>{relativeTime(rep.last_activity_at)}</span>
                        </Tooltip>
                      </TableCell>
                    </TableRow>
                    <TableRow>
                      <TableCell colSpan={7} sx={{ py: 0, borderBottom: open ? undefined : 'none' }}>
                        <Collapse in={open} timeout="auto" unmountOnExit>
                          <Box sx={{ my: 1 }}>
                            {open && <RepRecentRows userId={rep.actor_user_id} />}
                          </Box>
                        </Collapse>
                      </TableCell>
                    </TableRow>
                  </Fragment>
                )
              })}
            </TableBody>
          </Table>
        </TableContainer>
      )}
    </Box>
  )
}
