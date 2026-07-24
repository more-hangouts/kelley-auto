import { useMemo } from 'react'
import { useOutletContext } from 'react-router-dom'
import {
  Alert,
  Avatar,
  Box,
  Button,
  CircularProgress,
  Paper,
  Stack,
  Typography,
} from '@mui/material'
import HistoryIcon from '@mui/icons-material/History'
import PersonIcon from '@mui/icons-material/Person'
import StorefrontIcon from '@mui/icons-material/Storefront'
import SettingsIcon from '@mui/icons-material/Settings'
import { useInfiniteQuery } from '@tanstack/react-query'
import dayjs from 'dayjs'
import relativeTime from 'dayjs/plugin/relativeTime'

import { listEventActivity } from '../../../services/api'
import { formatUSD } from '../../../utils/money'

dayjs.extend(relativeTime)

// Render copy lives in the client so the JSON payload stays small and
// the strings are easy to localize later. Each entry is a function that
// takes the activity row and returns the displayed message. Falls back
// to a plain "Activity" line for unknown types so a future server-side
// addition does not crash the UI.
const RENDERERS = {
  'invoice.created': (a) =>
    `Created invoice (${formatUSD(a.payload?.total_cents || 0)})`,
  'invoice.updated': (a) =>
    `Updated invoice (revision ${a.payload?.revision || '?'})`,
  'invoice.sent': (a) =>
    `Sent invoice ${a.payload?.invoice_number || ''}`.trim(),
  'invoice.resent': (a) =>
    `Resent invoice ${a.payload?.invoice_number || ''}`.trim(),
  'invoice.viewed': (a) =>
    `Customer opened invoice ${a.payload?.invoice_number || ''}`.trim(),
  'invoice.paid': (a) =>
    `Marked invoice ${a.payload?.invoice_number || ''} as paid`.trim(),
  'invoice.cancelled': (a) =>
    `Cancelled invoice ${a.payload?.invoice_number || ''}`.trim(),
  'invoice.deleted': (a) =>
    `Deleted invoice ${a.payload?.invoice_number || ''}`.trim(),
  'quote.created': (a) =>
    `Created quote (${formatUSD(a.payload?.total_cents || 0)})`,
  'quote.updated': (a) =>
    `Updated quote (revision ${a.payload?.revision || '?'})`,
  'quote.sent': (a) =>
    `Sent quote ${a.payload?.quote_number || ''}`.trim(),
  'quote.resent': (a) =>
    `Resent quote ${a.payload?.quote_number || ''}`.trim(),
  'quote.viewed': (a) =>
    `Customer opened quote ${a.payload?.quote_number || ''}`.trim(),
  'quote.signed': (a) =>
    `Customer signed quote ${a.payload?.quote_number || ''} as ${a.payload?.signature_name || ''}`.trim(),
  'quote.approved': (a) =>
    `Quote ${a.payload?.quote_number || ''} approved`.trim(),
  'quote.rejected': (a) =>
    `Quote ${a.payload?.quote_number || ''} rejected`.trim(),
  'quote.cancelled': (a) =>
    `Cancelled quote ${a.payload?.quote_number || ''}`.trim(),
  'quote.converted': (a) => {
    const q = a.payload?.quote_number || ''
    const inv = a.payload?.invoice_number || `#${a.payload?.invoice_id}`
    return `Converted quote ${q} into invoice ${inv}`.trim()
  },
  'quote.unconverted': (a) => {
    const q = a.payload?.quote_number || ''
    return `Quote ${q} returned to Approved (linked draft invoice was deleted)`.trim()
  },
  'quote.deleted': (a) =>
    `Deleted quote ${a.payload?.quote_number || ''}`.trim(),
  'payment.created': (a) =>
    `Recorded ${formatUSD(a.payload?.amount_cents || 0)} payment via ${a.payload?.method || ''}`.trim(),
  'payment.refunded': (a) =>
    `Refunded ${formatUSD(a.payload?.amount_cents || 0)} via ${a.payload?.method || ''}`.trim(),
  'payment.voided': (a) =>
    `Voided payment ${a.payload?.payment_number || ''}`.trim(),
  'payment.applied': (a) =>
    `Applied ${formatUSD(a.payload?.applied_cents || 0)} from ${a.payload?.payment_number || ''} to an invoice`.trim(),
  'payment.unapplied': (a) =>
    `Unapplied ${a.payload?.payment_number || ''} from an invoice`.trim(),
  'event.status_changed': (a) => {
    const from = a.payload?.from_status || 'new'
    const to = a.payload?.to_status || '?'
    return `Status changed from ${from} to ${to}`
  },
  'event.walk_in_created': (a) => {
    const isNew = a.payload?.was_new_contact
    return isNew
      ? 'Captured as walk-in lead (new contact)'
      : 'Captured as walk-in lead (existing contact)'
  },
  'invitation.revoked': () => 'Revoked a customer-portal link',
  'invitation.resent': () => 'Resent a customer-portal link',
  'invoice.reminder_sent': (a) => {
    const idx = a.payload?.reminder_index
    const num = a.payload?.invoice_number || ''
    const label = idx ? ` #${idx}` : ''
    const tail = a.payload?.delivered === false ? ' (skipped, no email)' : ''
    return `Sent reminder${label} for invoice ${num}${tail}`.trim()
  },
  'quote.expired': (a) =>
    `Quote ${a.payload?.quote_number || ''} expired`.trim(),
  // CRM record deletion plan, D3-D3. Archive payloads carry
  // {reason, note, dependency_snapshot, ...}; restore payloads
  // carry the snapshot only. Reason and note are surfaced inline
  // so the timeline reads as a complete audit entry without
  // expanding the row.
  'contact.archived': (a) =>
    `Archived this contact${archiveSuffix(a)}`,
  'contact.restored': () => 'Restored this contact from the Recycle Bin',
  'event.archived': (a) => `Archived this event${archiveSuffix(a)}`,
  'event.restored': () => 'Restored this event from the Recycle Bin',
  'event_participant.archived': (a) => {
    const who = a.payload?.display_name
      ? `${a.payload.display_name} (${a.payload.role || 'participant'})`
      : 'a participant'
    return `Archived ${who}${archiveSuffix(a)}`
  },
  'event_participant.restored': (a) => {
    const who = a.payload?.display_name
      ? `${a.payload.display_name} (${a.payload.role || 'participant'})`
      : 'a participant'
    return `Restored ${who}`
  },
  'special_order.archived': (a) => {
    const size = a.payload?.size_label ? ` size ${a.payload.size_label}` : ''
    const wasStatus = a.payload?.status_at_archive
      ? `; status was ${a.payload.status_at_archive}`
      : ''
    return `Archived special order${size}${wasStatus}${archiveSuffix(a)}`
  },
  'special_order.restored': (a) => {
    const size = a.payload?.size_label ? ` size ${a.payload.size_label}` : ''
    return `Restored special order${size}`
  },
}

// Reason → human label for the archive verbs. Mirrors
// ARCHIVE_REASONS in services/record_dependencies.py; the dialog's
// reason picker uses the same enum.
const ARCHIVE_REASON_LABEL = {
  duplicate: 'Duplicate',
  test_record: 'Test record',
  created_by_mistake: 'Created by mistake',
  customer_requested: 'Customer requested removal',
  other: 'Other',
}

function archiveSuffix(activity) {
  const reasonKey = activity.payload?.reason
  const note = activity.payload?.note
  const reasonLabel = reasonKey ? (ARCHIVE_REASON_LABEL[reasonKey] || reasonKey) : null
  const parts = []
  if (reasonLabel) parts.push(`reason: ${reasonLabel}`)
  if (note) parts.push(`note: ${note}`)
  if (parts.length === 0) return ''
  return ` (${parts.join('; ')})`
}

function describe(activity) {
  const fn = RENDERERS[activity.activity_type]
  if (fn) {
    try {
      return fn(activity)
    } catch {
      // fall through to default
    }
  }
  return activity.activity_type
}

const ACTOR_ICON = {
  staff: PersonIcon,
  customer: StorefrontIcon,
  system: SettingsIcon,
}

const ACTOR_BG = {
  staff: 'primary.main',
  customer: 'secondary.main',
  system: 'grey.400',
}

function actorLabel(activity) {
  if (activity.actor_kind === 'customer') return 'Customer'
  if (activity.actor_kind === 'system') return 'System'
  return activity.actor_display_name || 'Staff'
}

export default function Activity() {
  const { event } = useOutletContext()
  const eventId = event.id

  const query = useInfiniteQuery({
    queryKey: ['event', eventId, 'activity'],
    initialPageParam: undefined,
    queryFn: ({ pageParam }) =>
      listEventActivity(eventId, { limit: 50, beforeId: pageParam }),
    getNextPageParam: (lastPage) => lastPage?.next_before_id ?? undefined,
  })

  const rows = useMemo(() => {
    const pages = query.data?.pages || []
    return pages.flatMap((p) => p.activities || [])
  }, [query.data])

  if (query.isLoading) {
    return (
      <Box sx={{ display: 'flex', justifyContent: 'center', p: 4 }}>
        <CircularProgress />
      </Box>
    )
  }
  if (query.error) {
    return (
      <Alert severity="error">
        {query.error?.response?.data?.detail || 'Could not load activity.'}
      </Alert>
    )
  }
  if (rows.length === 0) {
    return (
      <Paper sx={{ p: 4, textAlign: 'center' }}>
        <HistoryIcon sx={{ fontSize: 36, color: 'text.disabled', mb: 1 }} />
        <Typography variant="body2" color="text.secondary">
          Nothing has happened on this event yet. Activity rows will appear as
          quotes, invoices, and payments are created.
        </Typography>
      </Paper>
    )
  }

  return (
    <Box>
      <Paper sx={{ overflow: 'hidden' }}>
        {rows.map((a, i) => {
          const Icon = ACTOR_ICON[a.actor_kind] || PersonIcon
          return (
            <Stack
              key={a.id}
              direction="row"
              spacing={2}
              alignItems="flex-start"
              sx={{
                p: 2,
                borderBottom: i < rows.length - 1 ? '1px solid' : 'none',
                borderColor: 'divider',
              }}
            >
              <Avatar
                sx={{
                  bgcolor: ACTOR_BG[a.actor_kind] || 'grey.500',
                  width: 32,
                  height: 32,
                }}
              >
                <Icon fontSize="small" />
              </Avatar>
              <Box sx={{ flex: 1, minWidth: 0 }}>
                <Typography variant="body2">{describe(a)}</Typography>
                <Typography variant="caption" color="text.secondary">
                  {actorLabel(a)} · {dayjs(a.created_at).format('MMM D, YYYY h:mm A')}{' '}
                  ({dayjs(a.created_at).fromNow()})
                </Typography>
              </Box>
            </Stack>
          )
        })}
      </Paper>

      {query.hasNextPage && (
        <Box sx={{ display: 'flex', justifyContent: 'center', mt: 2 }}>
          <Button
            variant="outlined"
            onClick={() => query.fetchNextPage()}
            disabled={query.isFetchingNextPage}
          >
            {query.isFetchingNextPage ? 'Loading…' : 'Load earlier'}
          </Button>
        </Box>
      )}
    </Box>
  )
}
