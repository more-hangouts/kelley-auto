import { useState } from 'react'
import {
  Alert,
  Box,
  Button,
  Chip,
  CircularProgress,
  List,
  ListItem,
  ListItemButton,
  ListItemIcon,
  ListItemText,
  MenuItem,
  Snackbar,
  Stack,
  TextField,
  Typography,
} from '@mui/material'
import ArchiveOutlinedIcon from '@mui/icons-material/ArchiveOutlined'
import ArrowBackIcon from '@mui/icons-material/ArrowBack'
import HistoryIcon from '@mui/icons-material/History'
import InfoOutlinedIcon from '@mui/icons-material/InfoOutlined'
import PaymentsOutlinedIcon from '@mui/icons-material/PaymentsOutlined'
import WarningAmberOutlinedIcon from '@mui/icons-material/WarningAmberOutlined'
import {
  Link as RouterLink,
  NavLink,
  Outlet,
  useNavigate,
  useParams,
} from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import dayjs from 'dayjs'
import relativeTime from 'dayjs/plugin/relativeTime'

import RecordDependenciesDialog from '../../components/RecordDependenciesDialog'
import {
  archiveEvent,
  getDealTimeline,
  getDocumentCounts,
  getEvent,
  getEventWorkflow,
  patchEventStatus,
} from '../../services/api'

dayjs.extend(relativeTime)

const RAIL_WIDTH = 200

// Documents / Quotes / Invoices were retired from the deal page: financing
// and paperwork run on their own systems, and all three tabs had zero rows
// in production. Their routes now redirect to Overview so old links land
// somewhere sane. What's left is what a salesperson works from.
const TABS = [
  // One story, not three surfaces. Timeline merges what used to be the
  // Activity tab, the Notes tab, and the separate text-messages box —
  // reps were being asked to reconcile them by hand. It's first because
  // "what happened with this customer?" is the question they open a deal
  // to answer.
  { to: 'timeline', label: 'Timeline', icon: HistoryIcon, countKey: 'open_follow_ups' },
  { to: 'overview', label: 'Overview', icon: InfoOutlinedIcon, countKey: null },
  // Phase 6: payments tab. No badge for v1 — would need a dedicated
  // counts query for "unapplied funds present" which is uncommon enough
  // that staff can spot-check via the tab itself.
  { to: 'payments', label: 'Payments', icon: PaymentsOutlinedIcon, countKey: null },
]

// Where this lead came from and what happened last — the two questions a
// rep asks before picking up the phone. Reads from the same timeline
// payload the tab uses, so there's no second source of truth.
function LeadSourceStrip({ summary }) {
  if (!summary) return null

  const bits = []
  // Who took this deal, and how. First question a rep asks about a deal
  // they don't recognize.
  if (summary.created_via) {
    bits.push({
      key: 'created',
      label: 'Created',
      value: summary.created_by_name
        ? `${summary.created_via} · ${summary.created_by_name}`
        : summary.created_via,
    })
  }
  if (summary.lead_source) {
    const page = summary.lead_source_page
    bits.push({
      key: 'source',
      label: 'Came from',
      value:
        summary.lead_source === 'website' && page
          ? `Website ${page}`
          : summary.lead_source,
    })
  }
  if (summary.vehicle_label) {
    bits.push({ key: 'vehicle', label: 'Asking about', value: summary.vehicle_label })
  }
  // Only present when they closed on a DIFFERENT car — the API suppresses it
  // when the two match, so this never restates the line above.
  if (summary.sold_vehicle_label) {
    bits.push({ key: 'sold_vehicle', label: 'Bought', value: summary.sold_vehicle_label })
  }
  if (summary.customer_phone) {
    bits.push({ key: 'phone', label: 'Phone', value: summary.customer_phone })
  }
  if (summary.last_touch_label) {
    bits.push({
      key: 'last',
      label: 'Last touch',
      value: `${summary.last_touch_label}${
        summary.last_touch_at ? ` · ${dayjs(summary.last_touch_at).fromNow()}` : ''
      }`,
    })
  }
  if (!bits.length && !(summary.flags || []).length) return null

  return (
    <Box sx={{ mt: 1 }}>
      <Stack direction="row" spacing={2} flexWrap="wrap" useFlexGap>
        {bits.map((b) => (
          <Box key={b.key}>
            <Typography variant="caption" color="text.secondary" display="block">
              {b.label}
            </Typography>
            <Typography variant="body2">{b.value}</Typography>
          </Box>
        ))}
      </Stack>
      {(summary.flags || []).length > 0 && (
        <Stack direction="row" spacing={1} sx={{ mt: 1 }} flexWrap="wrap" useFlexGap>
          {summary.flags.map((f) => (
            <Chip
              key={f.code}
              size="small"
              color={f.severity === 'warning' ? 'warning' : 'default'}
              icon={<WarningAmberOutlinedIcon />}
              label={f.label}
            />
          ))}
        </Stack>
      )}
    </Box>
  )
}

function describeArchiveError(err) {
  const detail = err?.response?.data?.detail
  const code = detail?.code
  if (code === 'archive_blocked') {
    return detail?.message || 'Archive is blocked by linked records.'
  }
  if (code === 'event_not_found') {
    return 'This event no longer exists. Reload and try again.'
  }
  if (code === 'invalid_reason') {
    return 'Pick an archive reason and try again.'
  }
  return detail?.message || err?.message || 'Could not archive this event.'
}

export default function EventDetailLayout() {
  const { eventId } = useParams()
  const numericId = Number(eventId)
  const queryClient = useQueryClient()
  const navigate = useNavigate()
  const [archiveOpen, setArchiveOpen] = useState(false)
  const [toast, setToast] = useState(null)

  const { data: event, isLoading, error } = useQuery({
    queryKey: ['event', numericId],
    queryFn: () => getEvent(numericId),
    enabled: Number.isFinite(numericId),
  })

  const { data: workflow } = useQuery({
    queryKey: ['events', 'workflow', event?.event_type || 'vehicle_sale'],
    queryFn: () => getEventWorkflow(event?.event_type || 'vehicle_sale'),
    enabled: !!event,
    staleTime: 5 * 60_000,
  })

  const { data: counts } = useQuery({
    queryKey: ['event', numericId, 'document-counts'],
    queryFn: () => getDocumentCounts(numericId),
    enabled: !!event,
  })

  // Shared with the Timeline tab's own query (same key), so opening the
  // tab is a cache hit rather than a second round trip.
  const { data: timeline } = useQuery({
    queryKey: ['event', numericId, 'timeline'],
    queryFn: () => getDealTimeline(numericId),
    enabled: !!event,
  })

  const changeStatus = useMutation({
    mutationFn: (newStatus) => patchEventStatus(numericId, newStatus),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['event', numericId] })
      queryClient.invalidateQueries({ queryKey: ['events', 'board'] })
      // Phase 9: status change emits event.status_changed.
      queryClient.invalidateQueries({ queryKey: ['event', numericId, 'activity'] })
    },
  })

  const archiveMutation = useMutation({
    mutationFn: ({ reason, note }) => archiveEvent(numericId, { reason, note }),
    onSuccess: () => {
      setArchiveOpen(false)
      setToast({
        severity: 'success',
        message: 'Event moved to the Recycle Bin.',
      })
      queryClient.invalidateQueries({ queryKey: ['event', numericId] })
      queryClient.invalidateQueries({ queryKey: ['events', 'board'] })
      queryClient.invalidateQueries({ queryKey: ['record-dependencies'] })
      navigate('/pipeline')
    },
  })

  if (isLoading) {
    return (
      <Box sx={{ p: 6, display: 'flex', justifyContent: 'center' }}>
        <CircularProgress />
      </Box>
    )
  }
  if (error) {
    return (
      <Box sx={{ p: 3 }}>
        <Alert severity="error">
          {error?.response?.data?.detail || error.message || 'Failed to load event'}
        </Alert>
      </Box>
    )
  }
  if (!event) return null

  return (
    <Box sx={{ maxWidth: 1180, mx: 'auto' }}>
      <Button
        component={RouterLink}
        to="/sales"
        startIcon={<ArrowBackIcon />}
        size="small"
        sx={{ mb: 2 }}
      >
        Back to Deals
      </Button>

      <Stack
        direction={{ xs: 'column', md: 'row' }}
        justifyContent="space-between"
        alignItems={{ xs: 'stretch', md: 'flex-start' }}
        spacing={2}
        mb={3}
      >
        <Box sx={{ minWidth: 0 }}>
          <Typography variant="overline" color="text.secondary">
            Deal #{event.id}
            {event.event_type === 'vehicle_sale' ? ' · Vehicle' : ''}
          </Typography>
          <Typography
            sx={{
              fontWeight: 600,
              fontSize: { xs: '1.4rem', md: '2.125rem' },
              lineHeight: 1.15,
            }}
          >
            {event.event_name}
          </Typography>
          <Typography color="text.secondary">
            {event.primary_contact?.display_name}
          </Typography>
          <LeadSourceStrip summary={timeline?.summary} />
        </Box>
        <Stack
          direction="row"
          spacing={1}
          alignItems="center"
          sx={{ flexShrink: 0 }}
        >
          <TextField
            select
            size="small"
            label="Status"
            value={event.status}
            onChange={(e) => changeStatus.mutate(e.target.value)}
            sx={{ minWidth: { xs: 0, md: 200 }, flex: { xs: 1, md: 'none' } }}
            disabled={changeStatus.isPending}
          >
            {(workflow?.statuses || []).map((s) => (
              <MenuItem key={s.code} value={s.code}>
                {s.label}
              </MenuItem>
            ))}
          </TextField>
          <Button
            variant="outlined"
            size="small"
            color="error"
            startIcon={<ArchiveOutlinedIcon />}
            onClick={() => setArchiveOpen(true)}
          >
            Archive
          </Button>
        </Stack>
      </Stack>

      <Stack direction={{ xs: 'column', md: 'row' }} spacing={3} alignItems="flex-start">
        <Box
          sx={{
            width: { xs: '100%', md: RAIL_WIDTH },
            flexShrink: 0,
          }}
        >
          {/* Vertical rail on desktop; horizontal scroll strip on mobile so the
              six tabs don't push the content a full screen down. */}
          <List
            sx={{
              p: 0,
              display: 'flex',
              flexDirection: { xs: 'row', md: 'column' },
              gap: 0.5,
              overflowX: { xs: 'auto', md: 'visible' },
              pb: { xs: 1, md: 0 },
            }}
          >
            {TABS.map(({ to, label, icon: Icon, countKey }) => {
              const count = countKey && counts ? counts[countKey] : null
              return (
              <ListItem
                key={to}
                disablePadding
                sx={{ mb: { xs: 0, md: 0.5 }, width: 'auto', flexShrink: 0 }}
              >
                <ListItemButton
                  component={NavLink}
                  to={to}
                  sx={{
                    borderRadius: 2,
                    position: 'relative',
                    color: 'text.secondary',
                    '&:hover': {
                      bgcolor: 'rgba(93, 58, 107, 0.06)',
                    },
                    '&.active': {
                      bgcolor: 'rgba(93, 58, 107, 0.10)',
                      color: 'secondary.dark',
                      fontWeight: 600,
                      '&::before': {
                        content: '""',
                        position: 'absolute',
                        left: 0,
                        top: 8,
                        bottom: 8,
                        width: 3,
                        borderRadius: 2,
                        bgcolor: 'primary.main',
                      },
                      '& .MuiListItemIcon-root': {
                        color: 'secondary.dark',
                      },
                    },
                  }}
                >
                  <ListItemIcon sx={{ minWidth: 36, color: 'inherit' }}>
                    <Icon fontSize="small" />
                  </ListItemIcon>
                  <ListItemText
                    primary={label}
                    primaryTypographyProps={{ fontSize: 14, fontWeight: 'inherit' }}
                  />
                  {count != null && count > 0 && (
                    <Box
                      component="span"
                      sx={{
                        bgcolor: 'rgba(93, 58, 107, 0.15)',
                        color: 'secondary.dark',
                        fontSize: 11,
                        fontWeight: 600,
                        borderRadius: 8,
                        px: 1,
                        py: 0.25,
                        minWidth: 20,
                        textAlign: 'center',
                      }}
                    >
                      {count}
                    </Box>
                  )}
                </ListItemButton>
              </ListItem>
              )
            })}
          </List>
        </Box>

        <Box sx={{ flexGrow: 1, minWidth: 0 }}>
          <Outlet context={{ event, workflow }} />
        </Box>
      </Stack>

      <RecordDependenciesDialog
        entityType="event"
        entityId={numericId}
        open={archiveOpen}
        onClose={() => {
          if (!archiveMutation.isPending) {
            setArchiveOpen(false)
            archiveMutation.reset()
          }
        }}
        title={`Archive ${event.event_name}?`}
        confirmLabel="Move to Recycle Bin"
        confirmMode="archive"
        isSubmitting={archiveMutation.isPending}
        submitError={
          archiveMutation.isError
            ? describeArchiveError(archiveMutation.error)
            : null
        }
        onConfirm={({ reason, note }) =>
          archiveMutation.mutate({ reason, note })
        }
      />

      <Snackbar
        open={Boolean(toast)}
        autoHideDuration={4000}
        onClose={() => setToast(null)}
        anchorOrigin={{ vertical: 'bottom', horizontal: 'center' }}
      >
        {toast ? (
          <Alert
            severity={toast.severity}
            onClose={() => setToast(null)}
            variant="filled"
          >
            {toast.message}
          </Alert>
        ) : undefined}
      </Snackbar>
    </Box>
  )
}
