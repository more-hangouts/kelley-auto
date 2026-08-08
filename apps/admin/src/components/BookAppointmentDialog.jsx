import { useEffect, useState } from 'react'
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
} from '@mui/material'
import { useMutation, useQuery } from '@tanstack/react-query'
import dayjs from 'dayjs'

import { createStaffAppointment, salesListAssignableStaff } from '../services/api'

// Why the customer is being booked. Mirrors the server's booking_context
// vocabulary (migration 104); 'walk_in' is absent because that context
// belongs to the arrival receipt the walk-in flow writes, not to a future
// visit somebody schedules.
const CONTEXT_OPTIONS = [
  { value: 'phone_call', label: 'They called in' },
  { value: 'existing_customer', label: 'Existing customer' },
  { value: 'admin', label: 'Office scheduled it' },
  { value: 'other', label: 'Other' },
]

const DURATION_OPTIONS = [30, 45, 60, 90]

// Server-side conflict codes, in rep language. These are the hard ones —
// the booking did not happen.
const CONFLICT_COPY = {
  slot_in_past: 'That time has already passed.',
  slot_in_blackout: 'That time is blocked off on the calendar.',
  rep_double_booked:
    'That rep already has an appointment overlapping this time.',
  slot_start_not_timezone_aware: 'That start time could not be read.',
}

// Advisory codes — the booking DID happen, these are just worth knowing.
const WARNING_COPY = {
  outside_published_hours:
    'Booked outside the hours published on the website.',
  slot_at_capacity:
    'Another appointment already overlaps this time.',
}

function describeError(err) {
  const status = err?.response?.status
  const detail = err?.response?.data?.detail
  if (status === 409 && detail?.code === 'slot_conflict') {
    const parts = (detail.conflicts || []).map((c) => CONFLICT_COPY[c] || c)
    return parts.join(' ') || 'That slot is not available.'
  }
  if (status === 422 && detail === 'invalid_duration') {
    return 'Pick a duration between 15 and 240 minutes.'
  }
  if (status === 404 && detail === 'contact_not_found') {
    return 'This deal has no contact to book.'
  }
  if (status === 401 || status === 403) {
    return 'You do not have permission to book appointments.'
  }
  if (typeof detail === 'string') return detail
  return err?.message || 'Could not book the appointment.'
}

/**
 * Book a real future appointment from the CRM — the path staff never had.
 *
 * Takes a free datetime rather than a slot picker on purpose. The public
 * widget's slot grid is generated from availability rules that still
 * describe the boutique's hours (Wed-Sun, 12:00-19:00, capacity 1), so a
 * picker built on it would show staff a near-empty calendar and refuse
 * most real bookings. The server validates instead, refusing only what is
 * genuinely a conflict and returning the rest as warnings — see
 * services/staff_appointments.py.
 */
export default function BookAppointmentDialog({
  open,
  onClose,
  eventId,
  contactId,
  contactName,
  onBooked,
}) {
  const [form, setForm] = useState(null)
  const [error, setError] = useState(null)
  const [warnings, setWarnings] = useState([])

  // Same assignable-staff source the owner dialog uses, so the two pickers
  // can never disagree about who is on the floor.
  const staffQuery = useQuery({
    queryKey: ['sales', 'staff', 'assignable'],
    queryFn: salesListAssignableStaff,
    enabled: open,
    staleTime: 5 * 60_000,
  })

  useEffect(() => {
    if (!open) return
    setForm({
      // Default to tomorrow at 10am local — a plausible next visit that
      // is never in the past, so the dialog opens in a bookable state.
      slot_start: dayjs().add(1, 'day').hour(10).minute(0).format('YYYY-MM-DDTHH:mm'),
      duration_minutes: 45,
      booking_context: 'phone_call',
      assigned_user_id: '',
      internal_notes: '',
    })
    setError(null)
    setWarnings([])
  }, [open])

  const submit = useMutation({
    mutationFn: (body) => createStaffAppointment(body),
    onSuccess: (resp) => {
      if (resp?.warnings?.length) {
        // Booked, but worth saying out loud. Keep the dialog open so the
        // warning is actually read, and swap the action to Done.
        setWarnings(resp.warnings)
        onBooked?.(resp)
        return
      }
      onBooked?.(resp)
      onClose?.()
    },
    onError: (err) => setError(describeError(err)),
  })

  const reps = staffQuery.data || []

  if (!form) return null

  function patch(updates) {
    setForm((f) => ({ ...f, ...updates }))
  }

  function handleSubmit(e) {
    e?.preventDefault?.()
    if (submit.isPending) return
    setError(null)
    setWarnings([])
    submit.mutate({
      event_id: eventId ?? null,
      contact_id: contactId ?? null,
      // Sent as local wall time; the server reads a naive datetime as
      // shop-local, matching the customer reschedule route.
      slot_start: form.slot_start,
      duration_minutes: form.duration_minutes,
      booking_context: form.booking_context,
      assigned_user_id: form.assigned_user_id
        ? Number(form.assigned_user_id)
        : null,
      internal_notes: form.internal_notes.trim() || null,
    })
  }

  const booked = warnings.length > 0

  return (
    <Dialog open={open} onClose={submit.isPending ? undefined : onClose} maxWidth="xs" fullWidth>
      <DialogTitle>Book appointment</DialogTitle>
      <DialogContent dividers>
        <Stack spacing={2.5} component="form" onSubmit={handleSubmit}>
          {contactName && (
            <Typography variant="body2" color="text.secondary">
              Booking <strong>{contactName}</strong>.
            </Typography>
          )}

          {error && (
            <Alert severity="error" onClose={() => setError(null)}>
              {error}
            </Alert>
          )}

          {booked && (
            <Alert severity="success">
              Appointment booked.{' '}
              {warnings.map((w) => WARNING_COPY[w] || w).join(' ')}
            </Alert>
          )}

          <TextField
            fullWidth
            size="small"
            type="datetime-local"
            label="When"
            value={form.slot_start}
            onChange={(e) => patch({ slot_start: e.target.value })}
            InputLabelProps={{ shrink: true }}
            disabled={booked}
          />

          <Stack direction="row" spacing={2}>
            <TextField
              select
              fullWidth
              size="small"
              label="How long"
              value={form.duration_minutes}
              onChange={(e) => patch({ duration_minutes: Number(e.target.value) })}
              disabled={booked}
            >
              {DURATION_OPTIONS.map((m) => (
                <MenuItem key={m} value={m}>
                  {m} min
                </MenuItem>
              ))}
            </TextField>

            <TextField
              select
              fullWidth
              size="small"
              label="Why"
              value={form.booking_context}
              onChange={(e) => patch({ booking_context: e.target.value })}
              disabled={booked}
            >
              {CONTEXT_OPTIONS.map((o) => (
                <MenuItem key={o.value} value={o.value}>
                  {o.label}
                </MenuItem>
              ))}
            </TextField>
          </Stack>

          <TextField
            select
            fullWidth
            size="small"
            label="Rep"
            value={form.assigned_user_id}
            onChange={(e) => patch({ assigned_user_id: e.target.value })}
            disabled={booked || staffQuery.isLoading}
            helperText="Optional. An assigned rep can't be double-booked."
          >
            <MenuItem value="">
              <em>Unassigned</em>
            </MenuItem>
            {reps.map((u) => (
              <MenuItem key={u.id} value={String(u.id)}>
                {u.full_name || u.username}
              </MenuItem>
            ))}
          </TextField>

          <TextField
            fullWidth
            multiline
            minRows={2}
            size="small"
            label="Internal notes"
            value={form.internal_notes}
            onChange={(e) => patch({ internal_notes: e.target.value })}
            placeholder="Coming back with spouse"
            disabled={booked}
          />
        </Stack>
      </DialogContent>
      <DialogActions sx={{ px: 3, py: 2 }}>
        <Button onClick={onClose} disabled={submit.isPending}>
          {booked ? 'Done' : 'Cancel'}
        </Button>
        {!booked && (
          <Button
            variant="contained"
            onClick={handleSubmit}
            disabled={submit.isPending || !form.slot_start}
            startIcon={submit.isPending ? <CircularProgress size={16} /> : null}
          >
            Book
          </Button>
        )}
      </DialogActions>
    </Dialog>
  )
}
