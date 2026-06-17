import { useState } from 'react'
import {
  Alert,
  Box,
  Button,
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
import DescriptionOutlinedIcon from '@mui/icons-material/DescriptionOutlined'
import HistoryIcon from '@mui/icons-material/History'
import InfoOutlinedIcon from '@mui/icons-material/InfoOutlined'
import PaymentsOutlinedIcon from '@mui/icons-material/PaymentsOutlined'
import ReceiptLongOutlinedIcon from '@mui/icons-material/ReceiptLongOutlined'
import RequestQuoteOutlinedIcon from '@mui/icons-material/RequestQuoteOutlined'
import {
  Link as RouterLink,
  NavLink,
  Outlet,
  useNavigate,
  useParams,
} from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import RecordDependenciesDialog from '../../components/RecordDependenciesDialog'
import {
  archiveEvent,
  getDocumentCounts,
  getEvent,
  getEventWorkflow,
  patchEventStatus,
} from '../../services/api'
import { celebrantDiffersFromContact } from '../../utils/eventCelebrant'

const RAIL_WIDTH = 200

const TABS = [
  { to: 'overview', label: 'Overview', icon: InfoOutlinedIcon, countKey: null },
  { to: 'documents', label: 'Documents', icon: DescriptionOutlinedIcon, countKey: 'document' },
  // Phase 5: quotes get their own tab. No badge for v1 (would need a
  // dedicated counts query); polish backlog can add it later.
  { to: 'quotes', label: 'Quotes', icon: RequestQuoteOutlinedIcon, countKey: null },
  // Phase 4b: invoices badge re-sourced from canonical `outstanding_invoices`
  // (was 'invoice' in pre-Phase-4b document_counts). Counts unpaid bills,
  // not file rows.
  { to: 'invoices', label: 'Invoices', icon: ReceiptLongOutlinedIcon, countKey: 'outstanding_invoices' },
  // Phase 6: payments tab. No badge for v1 — would need a dedicated
  // counts query for "unapplied funds present" which is uncommon enough
  // that staff can spot-check via the tab itself.
  { to: 'payments', label: 'Payments', icon: PaymentsOutlinedIcon, countKey: null },
  // Phase 9: activity timeline. No badge — every event has activity by
  // definition; a count would just say "yes, there's stuff".
  { to: 'activity', label: 'Activity', icon: HistoryIcon, countKey: null },
]

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
    queryKey: ['events', 'workflow', event?.event_type || 'quinceanera'],
    queryFn: () => getEventWorkflow(event?.event_type || 'quinceanera'),
    enabled: !!event,
    staleTime: 5 * 60_000,
  })

  const { data: counts } = useQuery({
    queryKey: ['event', numericId, 'document-counts'],
    queryFn: () => getDocumentCounts(numericId),
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
        to="/pipeline"
        startIcon={<ArrowBackIcon />}
        size="small"
        sx={{ mb: 2 }}
      >
        Back to Pipeline
      </Button>

      <Stack
        direction="row"
        justifyContent="space-between"
        alignItems="flex-start"
        mb={3}
      >
        <Box>
          <Typography variant="overline" color="text.secondary">
            Event #{event.id} · Quinceañera
          </Typography>
          <Typography variant="h4" sx={{ fontWeight: 600 }}>
            {event.event_name}
          </Typography>
          {celebrantDiffersFromContact(event) ? (
            <Typography variant="caption" color="text.secondary">
              Contact: {event.primary_contact?.display_name}
            </Typography>
          ) : (
            <Typography color="text.secondary">
              {event.primary_contact?.display_name}
            </Typography>
          )}
        </Box>
        <Stack direction="row" spacing={1} alignItems="center">
          <TextField
            select
            size="small"
            label="Status"
            value={event.status}
            onChange={(e) => changeStatus.mutate(e.target.value)}
            sx={{ minWidth: 200 }}
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
          <List sx={{ p: 0 }}>
            {TABS.map(({ to, label, icon: Icon, countKey }) => {
              const count = countKey && counts ? counts[countKey] : null
              return (
              <ListItem key={to} disablePadding sx={{ mb: 0.5 }}>
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
