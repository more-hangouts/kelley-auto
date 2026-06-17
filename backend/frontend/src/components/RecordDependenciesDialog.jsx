import { useEffect, useState } from 'react'
import {
  Alert,
  Box,
  Button,
  Chip,
  CircularProgress,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  Divider,
  List,
  ListItem,
  ListItemText,
  MenuItem,
  Stack,
  TextField,
  Typography,
} from '@mui/material'
import { useQuery } from '@tanstack/react-query'

import { getRecordDependencies } from '../services/api'

// D1 of the CRM record deletion plan
// (docs/CRM_RECORD_DELETION_PLAN.md). Reusable confirm modal that
// renders dependency reports for the four target entity types and,
// when wired, dispatches archive / restore through the parent's
// onConfirm callback.
//
// Props:
//   entityType    'contact' | 'event' | 'event_participant' | 'special_order'
//   entityId      number
//   open          boolean
//   onClose       () => void
//   title         optional string. Defaults to a label derived from entity.
//   confirmLabel  optional string. If unset, the modal renders only a
//                 Close button (pure preview). When set, an action
//                 button appears; if onConfirm is also set it is
//                 wired up and disabled when the report blocks the
//                 action.
//   confirmMode   'archive' (default) or 'restore'. In archive mode a
//                 reason picker + optional note field render in the
//                 body and onConfirm receives {reason, note}; in
//                 restore mode onConfirm is called with no args.
//   onConfirm     ({reason, note}) => void | Promise — in archive mode.
//                 () => void | Promise — in restore mode.
//   isSubmitting  optional boolean — disables the confirm button +
//                 swaps the label for a spinner while the parent
//                 mutation is in flight.
//   submitError   optional string — surface a parent-side error
//                 (e.g. archive_blocked the server returned) without
//                 closing the dialog.

const ENTITY_LABEL = {
  contact: 'contact',
  event: 'event',
  event_participant: 'event participant',
  special_order: 'special order',
}

const REASON_OPTIONS = [
  { value: 'duplicate', label: 'Duplicate' },
  { value: 'test_record', label: 'Test record' },
  { value: 'created_by_mistake', label: 'Created by mistake' },
  { value: 'customer_requested', label: 'Customer requested removal' },
  { value: 'other', label: 'Other' },
]

// Translates backend `kind` slugs to human labels. Slugs that do not
// appear here fall back to the slug itself with underscores replaced.
const KIND_LABEL = {
  events: 'Linked events',
  event_participants: 'Event roles',
  participants: 'Participants',
  appointments: 'Booked appointments',
  invoices: 'Invoices',
  quotes: 'Quotes',
  payments: 'Payments',
  special_orders: 'Special orders',
  event_documents: 'Documents',
  linked_invoice_line: 'Linked invoice line',
}

function humanizeKind(kind) {
  if (KIND_LABEL[kind]) return KIND_LABEL[kind]
  return kind
    .split('_')
    .map((part) => (part ? part[0].toUpperCase() + part.slice(1) : ''))
    .join(' ')
}

function describeError(err) {
  const status = err?.response?.status
  const detail = err?.response?.data?.detail
  if (status === 400) {
    return typeof detail === 'string'
      ? detail
      : 'This entity type is not supported.'
  }
  if (status === 404) {
    return 'This record no longer exists. Reload and try again.'
  }
  if (status === 401 || status === 403) {
    return 'You do not have permission to view dependencies for this record.'
  }
  if (typeof detail === 'string') return detail
  return 'Could not load dependencies. Try again.'
}

export default function RecordDependenciesDialog({
  entityType,
  entityId,
  open,
  onClose,
  title,
  confirmLabel,
  confirmMode = 'archive',
  onConfirm,
  isSubmitting = false,
  submitError = null,
}) {
  const reportQuery = useQuery({
    queryKey: ['record-dependencies', entityType, entityId],
    queryFn: () => getRecordDependencies(entityType, entityId),
    enabled: open && Boolean(entityType) && Boolean(entityId),
    staleTime: 0,
    refetchOnWindowFocus: false,
  })

  const [reason, setReason] = useState('')
  const [note, setNote] = useState('')

  // Reset the reason/note inputs whenever the dialog (re)opens or the
  // entity changes so a stale picker selection doesn't carry between
  // distinct archive operations.
  useEffect(() => {
    if (open) {
      setReason('')
      setNote('')
    }
  }, [open, entityType, entityId])

  const report = reportQuery.data
  const headerLabel = title || (
    entityType
      ? `Dependencies for this ${ENTITY_LABEL[entityType] || entityType}`
      : 'Dependencies'
  )

  const showReasonControls =
    Boolean(confirmLabel) && confirmMode === 'archive'

  const confirmDisabled = (() => {
    if (!confirmLabel) return true
    if (!report) return true
    if (isSubmitting) return true
    if (confirmMode === 'restore') return !report.can_restore
    if (!report.can_archive) return true
    if (!reason) return true
    return false
  })()

  const handleConfirm = () => {
    if (!onConfirm) return
    if (confirmMode === 'restore') {
      onConfirm()
    } else {
      onConfirm({ reason, note: note.trim() || null })
    }
  }

  return (
    <Dialog
      open={open}
      onClose={onClose}
      fullWidth
      maxWidth="sm"
      aria-labelledby="record-dependencies-dialog-title"
    >
      <DialogTitle id="record-dependencies-dialog-title">
        {headerLabel}
      </DialogTitle>
      <DialogContent dividers>
        {reportQuery.isLoading && (
          <Stack alignItems="center" sx={{ py: 4 }}>
            <CircularProgress size={28} />
          </Stack>
        )}

        {reportQuery.isError && (
          <Alert severity="error">{describeError(reportQuery.error)}</Alert>
        )}

        {report && (
          <Stack spacing={2}>
            {report.is_currently_deleted && (
              <Alert severity="info">
                This record is currently in the Recycle Bin.
              </Alert>
            )}

            {report.block_reasons?.length > 0 && (
              <Alert severity="warning">
                <Typography variant="body2" sx={{ fontWeight: 600, mb: 0.5 }}>
                  Archive is blocked:
                </Typography>
                <Box component="ul" sx={{ m: 0, pl: 2 }}>
                  {report.block_reasons.map((reason) => (
                    <li key={reason}>
                      <Typography variant="body2">{reason}</Typography>
                    </li>
                  ))}
                </Box>
              </Alert>
            )}

            <Box>
              <Typography variant="overline" color="text.secondary">
                Linked records
              </Typography>
              <List dense disablePadding>
                {report.dependencies.map((dep) => {
                  const sample = report.sample_titles?.[dep.kind] || []
                  const secondary = []
                  if (dep.deleted_count > 0) {
                    secondary.push(`${dep.deleted_count} in Recycle Bin`)
                  }
                  if (sample.length > 0) {
                    secondary.push(`Sample: ${sample.join(', ')}`)
                  }
                  return (
                    <ListItem
                      key={dep.kind}
                      divider
                      secondaryAction={
                        <Stack direction="row" spacing={1} alignItems="center">
                          <Chip
                            size="small"
                            label={dep.active_count}
                            color={dep.active_count > 0 ? 'primary' : 'default'}
                            variant={dep.active_count > 0 ? 'filled' : 'outlined'}
                          />
                          {dep.blocking && dep.active_count > 0 && (
                            <Chip size="small" color="warning" label="Blocks" />
                          )}
                        </Stack>
                      }
                    >
                      <ListItemText
                        primary={humanizeKind(dep.kind)}
                        secondary={secondary.join(' · ') || null}
                      />
                    </ListItem>
                  )
                })}
              </List>
              {report.dependencies.length === 0 && (
                <Typography variant="body2" color="text.secondary" sx={{ mt: 1 }}>
                  No linked records.
                </Typography>
              )}
            </Box>

            <Divider />
            <Typography variant="caption" color="text.secondary">
              Counts reflect records currently visible to this admin. Items in
              the Recycle Bin are shown separately.
            </Typography>

            {showReasonControls && report.can_archive && (
              <Stack spacing={1.5}>
                <TextField
                  select
                  label="Reason"
                  value={reason}
                  onChange={(e) => setReason(e.target.value)}
                  size="small"
                  required
                >
                  {REASON_OPTIONS.map((o) => (
                    <MenuItem key={o.value} value={o.value}>
                      {o.label}
                    </MenuItem>
                  ))}
                </TextField>
                <TextField
                  label="Note (optional)"
                  value={note}
                  onChange={(e) => setNote(e.target.value)}
                  size="small"
                  multiline
                  minRows={2}
                  inputProps={{ maxLength: 2000 }}
                />
              </Stack>
            )}

            {submitError && (
              <Alert severity="error">{submitError}</Alert>
            )}
          </Stack>
        )}
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose} disabled={isSubmitting}>
          Close
        </Button>
        {confirmLabel && (
          <Button
            variant="contained"
            color={confirmMode === 'restore' ? 'primary' : 'error'}
            disabled={confirmDisabled}
            onClick={handleConfirm}
            startIcon={
              isSubmitting ? <CircularProgress size={16} color="inherit" /> : null
            }
          >
            {confirmLabel}
          </Button>
        )}
      </DialogActions>
    </Dialog>
  )
}
