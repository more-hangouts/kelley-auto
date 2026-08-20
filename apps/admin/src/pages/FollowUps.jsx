import { useMemo, useState } from 'react'
import {
  Alert,
  Box,
  Button,
  Chip,
  CircularProgress,
  Divider,
  Link,
  Paper,
  Stack,
  Typography,
} from '@mui/material'
import EventAvailableOutlinedIcon from '@mui/icons-material/EventAvailableOutlined'
import { useNavigate } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import dayjs from 'dayjs'
import relativeTime from 'dayjs/plugin/relativeTime'

import { getFollowUpQueue } from '../services/api'
import CallContact from '../components/CallContact'
import LogFollowUpDialog from '../components/LogFollowUpDialog'

dayjs.extend(relativeTime)

// Bucket presentation. Order is the working order: what's late, then what's
// due, then what's coming, then the pile nobody has decided about.
const BUCKETS = [
  {
    key: 'overdue',
    label: 'Overdue',
    color: 'error',
    blurb: 'Past their follow-up date. Work these first.',
  },
  {
    key: 'due_today',
    label: 'Due today',
    color: 'warning',
    blurb: 'Promised a call today.',
  },
  {
    key: 'upcoming',
    label: 'Upcoming',
    color: 'info',
    blurb: 'Scheduled for a later date — nothing to do yet.',
  },
  {
    key: 'no_reminder',
    label: 'No follow-up set',
    color: 'default',
    blurb:
      'Live deals nobody has scheduled. Stalest first — give each one a date or close it.',
  },
]

function dueLabel(item, today) {
  if (!item.remind_at) return null
  const due = dayjs(item.remind_at)
  const days = due.startOf('day').diff(dayjs(today).startOf('day'), 'day')
  if (days === 0) return `Today ${due.format('h:mm A')}`
  if (days === -1) return 'Yesterday'
  if (days < 0) return `${Math.abs(days)} days ago`
  if (days === 1) return `Tomorrow ${due.format('h:mm A')}`
  return due.format('ddd, MMM D')
}

function FollowUpRow({ item, today, onWork }) {
  const navigate = useNavigate()
  const due = dueLabel(item, today)

  return (
    <Paper
      variant="outlined"
      sx={{
        p: 1.75,
        borderRadius: 2,
        display: 'flex',
        alignItems: 'flex-start',
        gap: 2,
        '&:hover': { borderColor: 'primary.main' },
      }}
    >
      <Box sx={{ minWidth: 0, flex: 1 }}>
        <Stack direction="row" alignItems="center" spacing={1} flexWrap="wrap">
          <Link
            component="button"
            variant="subtitle2"
            underline="hover"
            onClick={() => navigate(`/deals/${item.event_id}`)}
            sx={{ fontWeight: 600 }}
          >
            {item.contact_name || `Deal #${item.event_id}`}
          </Link>
          {due && (
            <Typography variant="caption" color="text.secondary">
              · {due}
            </Typography>
          )}
          {item.days_since_status_change != null && !item.remind_at && (
            <Typography variant="caption" color="text.disabled">
              · no movement in {item.days_since_status_change}d
            </Typography>
          )}
        </Stack>

        <Typography variant="caption" color="text.secondary" display="block">
          {item.vehicle_label || 'No vehicle linked'}
          {item.vehicle_stock_number ? ` · ${item.vehicle_stock_number}` : ''}
          {' · '}
          {item.owner_name || 'Unassigned'}
        </Typography>

        {item.last_note_body && (
          <Typography
            variant="body2"
            color="text.secondary"
            sx={{
              mt: 0.75,
              display: '-webkit-box',
              WebkitLineClamp: 2,
              WebkitBoxOrient: 'vertical',
              overflow: 'hidden',
            }}
          >
            {item.last_note_body}
          </Typography>
        )}
      </Box>

      <Stack direction="row" spacing={0.5} alignItems="center" sx={{ flexShrink: 0 }}>
        {/* Logs the attempt and prompts for an outcome on return from the
            dialer — same component the deal detail uses. */}
        {item.contact_phone ? (
          <CallContact
            contactId={item.contact_id}
            phone={item.contact_phone}
            eventId={item.event_id}
            source="follow_up_queue"
            variant="icon"
          />
        ) : null}
        <Button
          size="small"
          variant="outlined"
          startIcon={<EventAvailableOutlinedIcon />}
          onClick={() => onWork(item)}
        >
          Log &amp; schedule
        </Button>
      </Stack>
    </Paper>
  )
}

export default function FollowUps({ eventType = 'vehicle_sale' }) {
  const [working, setWorking] = useState(null)
  const navigate = useNavigate()

  const { data, isLoading, error } = useQuery({
    queryKey: ['events', 'follow-ups', eventType],
    queryFn: () => getFollowUpQueue({ eventType }),
  })

  const grouped = useMemo(() => {
    const out = {}
    for (const b of BUCKETS) out[b.key] = []
    for (const item of data?.items || []) {
      if (out[item.bucket]) out[item.bucket].push(item)
    }
    return out
  }, [data])

  if (isLoading) {
    return (
      <Box sx={{ display: 'flex', justifyContent: 'center', py: 8 }}>
        <CircularProgress />
      </Box>
    )
  }

  if (error) {
    return (
      <Alert severity="error">
        {error?.response?.data?.detail ||
          error.message ||
          'Failed to load follow-ups'}
      </Alert>
    )
  }

  const counts = data?.counts || {}
  const actionable = (counts.overdue || 0) + (counts.due_today || 0)
  const scheduled = data?.scheduled_total || 0

  return (
    <Box sx={{ overflowY: 'auto', pb: 4 }}>
      <Alert
        severity={actionable > 0 ? 'warning' : 'success'}
        sx={{ mb: 2 }}
        icon={false}
      >
        {actionable > 0 ? (
          <>
            <strong>{actionable}</strong> lead{actionable === 1 ? '' : 's'} need a
            call today — {counts.overdue || 0} overdue, {counts.due_today || 0}{' '}
            due today.
          </>
        ) : (
          <>Nothing overdue or due today. Nice.</>
        )}
      </Alert>

      {/* Held-out deals never just disappear — say how many and where they are. */}
      {scheduled > 0 && (
        <Alert severity="info" sx={{ mb: 2 }} icon={false}>
          <strong>{scheduled}</strong> more {scheduled === 1 ? 'lead has' : 'leads have'}{' '}
          a visit already booked, so {scheduled === 1 ? 'it is' : 'they are'} not in
          this call list.{' '}
          <Link component="button" underline="hover" onClick={() => navigate('/calendar')}>
            See the appointments calendar
          </Link>
          .
        </Alert>
      )}

      <Stack spacing={3}>
        {BUCKETS.map((bucket) => {
          const items = grouped[bucket.key] || []
          const total =
            bucket.key === 'no_reminder'
              ? data?.no_reminder_total ?? items.length
              : counts[bucket.key] ?? items.length
          const capped = total > items.length

          return (
            <Box key={bucket.key}>
              <Stack
                direction="row"
                alignItems="center"
                spacing={1}
                sx={{ mb: 1 }}
                flexWrap="wrap"
              >
                <Typography variant="h6">{bucket.label}</Typography>
                <Chip
                  size="small"
                  label={total}
                  color={total > 0 ? bucket.color : 'default'}
                  variant={total > 0 ? 'filled' : 'outlined'}
                />
                <Typography variant="caption" color="text.secondary">
                  {bucket.blurb}
                </Typography>
              </Stack>
              <Divider sx={{ mb: 1.5 }} />

              {items.length === 0 ? (
                <Typography variant="body2" color="text.disabled" sx={{ py: 1 }}>
                  Nothing here.
                </Typography>
              ) : (
                <Stack spacing={1}>
                  {items.map((item) => (
                    <FollowUpRow
                      key={item.event_id}
                      item={item}
                      today={data.today}
                      onWork={setWorking}
                    />
                  ))}
                </Stack>
              )}

              {/* Never let a capped list read as a complete one. */}
              {capped && (
                <Typography
                  variant="caption"
                  color="text.secondary"
                  sx={{ mt: 1, display: 'block' }}
                >
                  Showing the {items.length} stalest of {total}. Work these down
                  — the rest appear as these get a date or get closed.
                </Typography>
              )}
            </Box>
          )
        })}
      </Stack>

      <LogFollowUpDialog
        open={Boolean(working)}
        item={working}
        onClose={() => setWorking(null)}
      />
    </Box>
  )
}
