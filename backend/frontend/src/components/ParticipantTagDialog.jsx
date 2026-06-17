import { useEffect, useMemo, useState } from 'react'
import {
  Alert,
  Button,
  CircularProgress,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  MenuItem,
  Stack,
  TextField,
  Typography,
  useMediaQuery,
  useTheme,
} from '@mui/material'
import { useMutation } from '@tanstack/react-query'

import {
  attendanceGateMessage,
  isAttendanceGateError,
} from '../sales/attendanceGate'

const UNTAGGED_VALUE = '__untagged__'

function describeError(err) {
  // Sales path can hit the attendance gate; admin path can't, but the
  // probe is safe either way (admin requests will never set the gate
  // detail shape so the check is a cheap no-op).
  if (isAttendanceGateError(err)) return attendanceGateMessage()

  const status = err?.response?.status
  const detail = err?.response?.data?.detail
  if (status === 404 && detail === 'appointment_not_found') {
    return 'This appointment no longer exists. Reload and try again.'
  }
  if (status === 404 && detail === 'participant_not_found') {
    return 'That participant is no longer on this event. Reload and try again.'
  }
  if (status === 400 && detail === 'participant_event_mismatch') {
    return 'That participant belongs to a different event.'
  }
  if (status === 400 && detail === 'appointment_unlinked_from_event') {
    return 'This appointment is not linked to a CRM event yet.'
  }
  if (status === 401 || status === 403) {
    return 'You do not have permission to change the buyer journey.'
  }
  if (typeof detail === 'string') return detail
  return 'Could not save the buyer journey. Try again.'
}

/**
 * Tag an appointment to a specific event_participant.
 *
 * Surface-agnostic: callers pass a `tagFn(appointmentId, eventParticipantId)`
 * that hits whichever route applies to them (sales floor uses
 * `salesTagAppointmentParticipant`; admin uses `adminTagAppointmentParticipant`).
 * Both routes share the same service and audit behavior; this component
 * doesn't need to know which surface it's rendered on.
 */
export default function ParticipantTagDialog({
  open,
  onClose,
  appointmentId,
  participants,
  currentEventParticipantId,
  tagFn,
  onSuccess,
}) {
  const theme = useTheme()
  const fullScreen = useMediaQuery(theme.breakpoints.down('sm'))

  const [selection, setSelection] = useState(UNTAGGED_VALUE)
  const [error, setError] = useState(null)

  useEffect(() => {
    if (!open) return
    setSelection(
      currentEventParticipantId == null
        ? UNTAGGED_VALUE
        : String(currentEventParticipantId),
    )
    setError(null)
  }, [open, currentEventParticipantId])

  const submit = useMutation({
    mutationFn: () => {
      const value =
        selection === UNTAGGED_VALUE ? null : Number(selection)
      return tagFn(appointmentId, value)
    },
    onSuccess: (result) => {
      onSuccess?.(result)
      onClose?.()
    },
    onError: (err) => setError(describeError(err)),
  })

  const isUnchanged = useMemo(() => {
    const currentAsValue =
      currentEventParticipantId == null
        ? UNTAGGED_VALUE
        : String(currentEventParticipantId)
    return selection === currentAsValue
  }, [currentEventParticipantId, selection])

  function handleSubmit(e) {
    e?.preventDefault?.()
    if (submit.isPending) return
    setError(null)
    submit.mutate()
  }

  const hasParticipants = (participants || []).length > 0

  return (
    <Dialog
      open={open}
      onClose={submit.isPending ? undefined : onClose}
      fullScreen={fullScreen}
      fullWidth
      maxWidth="sm"
    >
      <DialogTitle>Tag buyer journey</DialogTitle>
      <DialogContent dividers>
        <Stack spacing={2.5}>
          {error && (
            <Alert severity="error" onClose={() => setError(null)}>
              {error}
            </Alert>
          )}

          <Typography variant="body2" color="text.secondary">
            Mark which event participant this appointment is for. Untag
            it to mean "celebrant or unspecified."
          </Typography>

          {hasParticipants ? (
            <TextField
              select
              fullWidth
              size="small"
              label="Buyer"
              value={selection}
              onChange={(e) => setSelection(e.target.value)}
            >
              <MenuItem value={UNTAGGED_VALUE}>
                <em>Untagged (celebrant or unspecified)</em>
              </MenuItem>
              {(participants || []).map((p) => (
                <MenuItem key={p.id} value={String(p.id)}>
                  {p.role}: {p.display_name}
                </MenuItem>
              ))}
            </TextField>
          ) : (
            <Typography variant="body2" color="text.secondary">
              No participants are listed on this event yet. Add a
              participant first from the event detail.
            </Typography>
          )}
        </Stack>
      </DialogContent>
      <DialogActions sx={{ px: 3, py: 2 }}>
        <Button onClick={onClose} disabled={submit.isPending}>
          Cancel
        </Button>
        <Button
          variant="contained"
          onClick={handleSubmit}
          disabled={submit.isPending || isUnchanged || !hasParticipants}
          startIcon={
            submit.isPending ? <CircularProgress size={16} /> : null
          }
        >
          Save
        </Button>
      </DialogActions>
    </Dialog>
  )
}
