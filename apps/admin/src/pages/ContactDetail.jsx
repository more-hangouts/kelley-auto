import { useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import {
  Alert,
  Avatar,
  Box,
  Button,
  Chip,
  CircularProgress,
  Divider,
  IconButton,
  Link,
  Paper,
  Snackbar,
  Stack,
  Tooltip,
  Typography,
} from '@mui/material'
import ArchiveOutlinedIcon from '@mui/icons-material/ArchiveOutlined'
import ArrowBackIcon from '@mui/icons-material/ArrowBack'
import EditOutlinedIcon from '@mui/icons-material/EditOutlined'
import EmailOutlinedIcon from '@mui/icons-material/EmailOutlined'
import EventOutlinedIcon from '@mui/icons-material/EventOutlined'
import PhoneOutlinedIcon from '@mui/icons-material/PhoneOutlined'
import PlaceOutlinedIcon from '@mui/icons-material/PlaceOutlined'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import dayjs from 'dayjs'

import ContactEditDialog from '../components/ContactEditDialog'
import RecordDependenciesDialog from '../components/RecordDependenciesDialog'
import { archiveContact, getContact } from '../services/api'

// Phase 3 destination for `contact` palette results. Read-only by
// design; edit drops into the existing ContactEditDialog so this
// page never duplicates the patch / phone-collision logic that the
// dialog already owns.
//
// The page consumes the Phase 3 contract expansion: address (JSONB
// blob) and linked_events (server-supplied route per row). Address
// shape is intentionally not enforced server-side, so this page
// renders any present string values without assuming a schema.

function initials(name) {
  if (!name) return '?'
  const parts = name.trim().split(/\s+/).slice(0, 2)
  return parts.map((p) => p[0]?.toUpperCase() ?? '').join('') || '?'
}

// Address ordering for the keys we know about; unknown keys fall to
// the end in their original insertion order so a future field shows
// up without a code change.
const ADDRESS_KEY_ORDER = [
  'line1',
  'line2',
  'street',
  'city',
  'state',
  'region',
  'postal_code',
  'zip',
  'country',
]

function orderedAddressEntries(address) {
  if (!address || typeof address !== 'object') return []
  const seen = new Set()
  const ordered = []
  for (const key of ADDRESS_KEY_ORDER) {
    const value = address[key]
    if (typeof value === 'string' && value.trim()) {
      ordered.push([key, value.trim()])
      seen.add(key)
    }
  }
  for (const [key, value] of Object.entries(address)) {
    if (seen.has(key)) continue
    if (typeof value === 'string' && value.trim()) {
      ordered.push([key, value.trim()])
    }
  }
  return ordered
}

function StatusChip({ status }) {
  const label = (status || '').replace(/_/g, ' ')
  return (
    <Chip
      size="small"
      label={label}
      sx={{
        textTransform: 'capitalize',
        fontSize: 11,
        height: 22,
        bgcolor: 'action.hover',
        color: 'text.secondary',
        fontWeight: 500,
      }}
    />
  )
}

function describeArchiveError(err) {
  const detail = err?.response?.data?.detail
  const code = detail?.code
  if (code === 'archive_blocked') {
    return detail?.message || 'Archive is blocked by linked records.'
  }
  if (code === 'contact_not_found') {
    return 'This contact no longer exists. Reload and try again.'
  }
  if (code === 'invalid_reason') {
    return 'Pick an archive reason and try again.'
  }
  return detail?.message || err?.message || 'Could not archive this contact.'
}

export default function ContactDetail() {
  const { contactId } = useParams()
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const id = Number(contactId)
  const [editOpen, setEditOpen] = useState(false)
  const [archiveOpen, setArchiveOpen] = useState(false)
  const [toast, setToast] = useState(null) // {message, severity}

  const { data, isLoading, isError, error } = useQuery({
    queryKey: ['contact', id],
    queryFn: () => getContact(id),
    enabled: Number.isFinite(id),
  })

  const archiveMutation = useMutation({
    mutationFn: ({ reason, note }) => archiveContact(id, { reason, note }),
    onSuccess: () => {
      setArchiveOpen(false)
      setToast({
        severity: 'success',
        message: 'Contact moved to the Recycle Bin.',
      })
      queryClient.invalidateQueries({ queryKey: ['contact', id] })
      queryClient.invalidateQueries({ queryKey: ['record-dependencies'] })
      // Bounce back so the now-archived contact's 404 page does not
      // greet the user. They land on whatever surface brought them in.
      navigate(-1)
    },
  })

  if (isLoading) {
    return (
      <Box sx={{ display: 'flex', justifyContent: 'center', py: 6 }}>
        <CircularProgress />
      </Box>
    )
  }

  if (isError) {
    const status = error?.response?.status
    const message =
      status === 404
        ? 'Contact not found.'
        : error?.response?.data?.detail || error?.message || 'Failed to load contact.'
    return (
      <Box sx={{ maxWidth: 720, mx: 'auto' }}>
        <Stack direction="row" spacing={1} alignItems="center" mb={2}>
          <IconButton onClick={() => navigate(-1)} aria-label="back">
            <ArrowBackIcon />
          </IconButton>
          <Typography variant="h5">Contact</Typography>
        </Stack>
        <Alert severity={status === 404 ? 'warning' : 'error'}>{message}</Alert>
      </Box>
    )
  }

  if (!data) return null

  const addressEntries = orderedAddressEntries(data.address)
  const tags = Array.isArray(data.tags) ? data.tags : []
  const linkedEvents = Array.isArray(data.linked_events) ? data.linked_events : []

  return (
    <Box sx={{ maxWidth: 880, mx: 'auto' }}>
      <Stack
        direction="row"
        spacing={1}
        alignItems="center"
        justifyContent="space-between"
        mb={3}
      >
        <Stack direction="row" spacing={1} alignItems="center">
          <IconButton onClick={() => navigate(-1)} aria-label="back">
            <ArrowBackIcon />
          </IconButton>
          <Typography variant="overline" color="text.secondary">
            Contact
          </Typography>
        </Stack>
        <Stack direction="row" spacing={1}>
          <Button
            variant="outlined"
            size="small"
            startIcon={<EditOutlinedIcon />}
            onClick={() => setEditOpen(true)}
          >
            Edit
          </Button>
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

      <Paper
        variant="outlined"
        sx={{ p: { xs: 2.5, md: 3.5 }, mb: 3, borderRadius: 2 }}
      >
        <Stack direction="row" spacing={2.5} alignItems="center">
          <Avatar
            sx={{
              width: 64,
              height: 64,
              bgcolor: 'primary.main',
              color: 'common.white',
              fontSize: 22,
              fontWeight: 600,
            }}
          >
            {initials(data.display_name)}
          </Avatar>
          <Box sx={{ minWidth: 0, flex: 1 }}>
            <Typography variant="h5" sx={{ lineHeight: 1.2 }}>
              {data.display_name}
            </Typography>
            {(data.first_name || data.last_name) &&
              `${data.first_name || ''} ${data.last_name || ''}`.trim() !==
                data.display_name && (
                <Typography variant="body2" color="text.secondary" mt={0.5}>
                  {[data.first_name, data.last_name].filter(Boolean).join(' ')}
                </Typography>
              )}
            {tags.length > 0 && (
              <Stack direction="row" spacing={0.75} mt={1.25} flexWrap="wrap">
                {tags.map((t) => (
                  <Chip
                    key={t}
                    label={t}
                    size="small"
                    sx={{ fontSize: 11, height: 22 }}
                  />
                ))}
              </Stack>
            )}
          </Box>
        </Stack>

        {(data.phone || data.email || addressEntries.length > 0) && (
          <>
            <Divider sx={{ my: 2.5 }} />
            <Stack spacing={1.25}>
              {data.phone && (
                <Stack direction="row" spacing={1.5} alignItems="center">
                  <PhoneOutlinedIcon
                    fontSize="small"
                    sx={{ color: 'text.secondary' }}
                  />
                  <Link href={`tel:${data.phone_e164 || data.phone}`} underline="hover">
                    {data.phone}
                  </Link>
                  {data.phone_e164 && data.phone_e164 !== data.phone && (
                    <Tooltip title="Normalized E.164">
                      <Typography variant="caption" color="text.secondary">
                        {data.phone_e164}
                      </Typography>
                    </Tooltip>
                  )}
                </Stack>
              )}
              {data.email && (
                <Stack direction="row" spacing={1.5} alignItems="center">
                  <EmailOutlinedIcon
                    fontSize="small"
                    sx={{ color: 'text.secondary' }}
                  />
                  <Link href={`mailto:${data.email}`} underline="hover">
                    {data.email}
                  </Link>
                </Stack>
              )}
              {addressEntries.length > 0 && (
                <Stack direction="row" spacing={1.5} alignItems="flex-start">
                  <PlaceOutlinedIcon
                    fontSize="small"
                    sx={{ color: 'text.secondary', mt: 0.25 }}
                  />
                  <Box>
                    {addressEntries.map(([key, value]) => (
                      <Typography
                        key={key}
                        variant="body2"
                        sx={{ lineHeight: 1.6 }}
                      >
                        {value}
                      </Typography>
                    ))}
                  </Box>
                </Stack>
              )}
            </Stack>
          </>
        )}

        {data.notes && (
          <>
            <Divider sx={{ my: 2.5 }} />
            <Typography
              variant="body2"
              color="text.secondary"
              sx={{ whiteSpace: 'pre-wrap' }}
            >
              {data.notes}
            </Typography>
          </>
        )}
      </Paper>

      <Paper variant="outlined" sx={{ borderRadius: 2 }}>
        <Box sx={{ px: 3, py: 2, borderBottom: '1px solid', borderColor: 'divider' }}>
          <Typography variant="subtitle1" sx={{ fontWeight: 600 }}>
            Linked events
          </Typography>
          <Typography variant="body2" color="text.secondary">
            {linkedEvents.length === 0
              ? 'This contact has no events yet.'
              : `${linkedEvents.length} event${linkedEvents.length === 1 ? '' : 's'} on file`}
          </Typography>
        </Box>
        {linkedEvents.length > 0 && (
          <Stack divider={<Divider />}>
            {linkedEvents.map((ev) => (
              <Box
                key={ev.id}
                role="button"
                tabIndex={0}
                onClick={() => navigate(ev.route)}
                onKeyDown={(e) => {
                  if (e.key === 'Enter' || e.key === ' ') {
                    e.preventDefault()
                    navigate(ev.route)
                  }
                }}
                sx={{
                  px: 3,
                  py: 1.75,
                  display: 'flex',
                  alignItems: 'center',
                  gap: 2,
                  cursor: 'pointer',
                  transition: 'background-color 120ms ease',
                  '&:hover, &:focus-visible': {
                    bgcolor: 'action.hover',
                    outline: 'none',
                  },
                }}
              >
                <EventOutlinedIcon
                  fontSize="small"
                  sx={{ color: 'text.secondary' }}
                />
                <Box sx={{ minWidth: 0, flex: 1 }}>
                  <Typography variant="body2" sx={{ fontWeight: 500 }} noWrap>
                    {ev.event_name}
                  </Typography>
                  <Typography variant="caption" color="text.secondary">
                    {ev.event_date
                      ? dayjs(ev.event_date).format('MMM D, YYYY')
                      : 'No date set'}
                  </Typography>
                </Box>
                <StatusChip status={ev.status} />
              </Box>
            ))}
          </Stack>
        )}
      </Paper>

      <ContactEditDialog
        open={editOpen}
        contactId={id}
        onClose={() => setEditOpen(false)}
      />

      <RecordDependenciesDialog
        entityType="contact"
        entityId={id}
        open={archiveOpen}
        onClose={() => {
          if (!archiveMutation.isPending) {
            setArchiveOpen(false)
            archiveMutation.reset()
          }
        }}
        title={`Archive ${data.display_name}?`}
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
