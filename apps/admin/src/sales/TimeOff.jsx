import { useEffect, useMemo, useState } from 'react'
import {
  Alert,
  Box,
  Button,
  Card,
  CardContent,
  Chip,
  CircularProgress,
  Dialog,
  DialogActions,
  DialogContent,
  DialogContentText,
  DialogTitle,
  FormControlLabel,
  Stack,
  Switch,
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableRow,
  TextField,
  Typography,
} from '@mui/material'

import {
  salesCancelTimeOff,
  salesListMyTimeOff,
  salesSubmitTimeOff,
} from '../services/api'

// Stylist /time-off (Phase 8 Slice D). List of past + pending
// requests, with a "Request time off" form. Cancel button on
// `pending` rows only — the API enforces this with a 409 on
// terminal status, which we treat as "stale UI, refresh."
//
// Per the doc lock-in: cancel uses POST /time-off/{id}/cancel (the
// row sticks around with status='cancelled' for the audit trail).
// The button is still labeled "Cancel" — UX is honest about
// lifecycle.

const REASON_SUGGESTIONS = ['Sick', 'Vacation', 'Family/Personal']

const EMPTY_FORM = {
  allDay: true,
  startDate: '',
  endDate: '',
  partialDate: '',
  startTime: '09:00',
  endTime: '17:00',
  reason: '',
}

function formatLocal(iso) {
  if (!iso) return ''
  try {
    return new Date(iso).toLocaleString(undefined, {
      month: 'short',
      day: 'numeric',
      hour: 'numeric',
      minute: '2-digit',
    })
  } catch {
    return iso
  }
}

function statusChip(status) {
  if (status === 'pending') {
    return <Chip label="Pending" size="small" color="warning" />
  }
  if (status === 'approved') {
    return <Chip label="Approved" size="small" color="success" />
  }
  if (status === 'denied') {
    return <Chip label="Denied" size="small" color="error" />
  }
  if (status === 'cancelled') {
    return <Chip label="Cancelled" size="small" />
  }
  return <Chip label={status} size="small" />
}

// Build a Date in the browser's local timezone from a YYYY-MM-DD
// date and an HH:MM:SS clock. We assemble the wall-clock pieces
// explicitly so DST transitions stay correct — `new Date('2026-03-08T02:30:00')`
// is interpreted as local by the runtime, which is what we want.
function localDateTime(dateStr, timeStr) {
  if (!dateStr || !timeStr) return null
  const [y, m, d] = dateStr.split('-').map((n) => parseInt(n, 10))
  const [hh, mm, ss] = timeStr.split(':').map((n) => parseInt(n, 10))
  if (!y || !m || !d || !Number.isFinite(hh) || !Number.isFinite(mm)) {
    return null
  }
  return new Date(y, m - 1, d, hh || 0, mm || 0, ss || 0, 0)
}

export default function TimeOff() {
  const [requests, setRequests] = useState(null)
  const [loadError, setLoadError] = useState(null)
  const [actionError, setActionError] = useState(null)
  const [busyId, setBusyId] = useState(null)

  const [submitOpen, setSubmitOpen] = useState(false)
  const [submitForm, setSubmitForm] = useState(EMPTY_FORM)

  const payload = useMemo(() => buildPayload(submitForm), [submitForm])

  async function refresh() {
    setLoadError(null)
    try {
      const data = await salesListMyTimeOff()
      setRequests(data.requests || [])
    } catch {
      setLoadError("Couldn't load your time-off history. Try again.")
      setRequests([])
    }
  }

  useEffect(() => {
    refresh()
  }, [])

  function openSubmit() {
    setSubmitForm(EMPTY_FORM)
    setActionError(null)
    setSubmitOpen(true)
  }

  async function handleSubmit() {
    if (!payload) {
      setActionError(
        submitForm.allDay
          ? 'Pick a start and end date.'
          : 'Pick a date and a start/end time.'
      )
      return
    }
    if (payload.endsAt <= payload.startsAt) {
      setActionError('End must be after start.')
      return
    }
    setActionError(null)
    setBusyId('submit')
    try {
      await salesSubmitTimeOff({
        starts_at: payload.startsAt.toISOString(),
        ends_at: payload.endsAt.toISOString(),
        reason: submitForm.reason.trim() || null,
      })
      setSubmitOpen(false)
      setSubmitForm(EMPTY_FORM)
      await refresh()
    } catch (err) {
      const code = err?.response?.data?.detail?.code
      if (code === 'invalid_date_range') {
        setActionError('End must be after start.')
      } else {
        setActionError("Couldn't submit your request.")
      }
    } finally {
      setBusyId(null)
    }
  }

  async function handleCancel(req) {
    setActionError(null)
    setBusyId(req.id)
    try {
      await salesCancelTimeOff(req.id)
      await refresh()
    } catch (err) {
      const code = err?.response?.data?.detail?.code
      if (code === 'time_off_request_terminal') {
        setActionError('That request was already decided. Refresh.')
        await refresh()
      } else {
        setActionError("Couldn't cancel that request.")
      }
    } finally {
      setBusyId(null)
    }
  }

  return (
    <Stack spacing={2}>
      <Stack
        direction={{ xs: 'column', sm: 'row' }}
        justifyContent="space-between"
        alignItems={{ xs: 'flex-start', sm: 'center' }}
        spacing={1}
      >
        <Box>
          <Typography variant="h5">Time off</Typography>
          <Typography variant="body2" color="text.secondary">
            Request time off and see prior decisions. The owner approves
            or denies; you'll get an email either way.
          </Typography>
        </Box>
        <Button variant="contained" onClick={openSubmit}>
          Request time off
        </Button>
      </Stack>

      {loadError && <Alert severity="error">{loadError}</Alert>}
      {actionError && (
        <Alert severity="error" onClose={() => setActionError(null)}>
          {actionError}
        </Alert>
      )}

      <Card variant="outlined">
        <CardContent sx={{ p: { xs: 2, sm: 3 } }}>
          {requests === null ? (
            <Box sx={{ display: 'flex', justifyContent: 'center', py: 3 }}>
              <CircularProgress size={28} />
            </Box>
          ) : requests.length === 0 ? (
            <Typography variant="body2" color="text.secondary">
              No requests yet.
            </Typography>
          ) : (
            <Table size="small">
              <TableHead>
                <TableRow>
                  <TableCell>Submitted</TableCell>
                  <TableCell>Start</TableCell>
                  <TableCell>End</TableCell>
                  <TableCell>Reason</TableCell>
                  <TableCell>Status</TableCell>
                  <TableCell align="right">Action</TableCell>
                </TableRow>
              </TableHead>
              <TableBody>
                {requests.map((r) => (
                  <TableRow key={r.id}>
                    <TableCell>{formatLocal(r.created_at)}</TableCell>
                    <TableCell>{formatLocal(r.starts_at_local)}</TableCell>
                    <TableCell>{formatLocal(r.ends_at_local)}</TableCell>
                    <TableCell>{r.reason || ''}</TableCell>
                    <TableCell>{statusChip(r.status)}</TableCell>
                    <TableCell align="right">
                      {r.status === 'pending' ? (
                        <Button
                          size="small"
                          color="error"
                          disabled={busyId === r.id}
                          onClick={() => handleCancel(r)}
                        >
                          Cancel
                        </Button>
                      ) : (
                        <span style={{ color: 'rgba(0,0,0,0.4)' }}>—</span>
                      )}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
        </CardContent>
      </Card>

      <Dialog
        open={submitOpen}
        onClose={() => setSubmitOpen(false)}
        maxWidth="sm"
        fullWidth
      >
        <DialogTitle>Request time off</DialogTitle>
        <DialogContent>
          <DialogContentText sx={{ mb: 2 }}>
            Pick the day or days you need. The owner will see it in their
            queue and approve or deny.
          </DialogContentText>
          <Stack spacing={2}>
            <FormControlLabel
              control={
                <Switch
                  checked={submitForm.allDay}
                  onChange={(e) =>
                    setSubmitForm({
                      ...submitForm,
                      allDay: e.target.checked,
                    })
                  }
                />
              }
              label="All day"
            />

            {submitForm.allDay ? (
              <Stack direction={{ xs: 'column', sm: 'row' }} spacing={2}>
                <TextField
                  label="Start date"
                  type="date"
                  value={submitForm.startDate}
                  onChange={(e) =>
                    setSubmitForm({
                      ...submitForm,
                      startDate: e.target.value,
                    })
                  }
                  InputLabelProps={{ shrink: true }}
                  required
                  fullWidth
                />
                <TextField
                  label="End date"
                  type="date"
                  value={submitForm.endDate}
                  onChange={(e) =>
                    setSubmitForm({
                      ...submitForm,
                      endDate: e.target.value,
                    })
                  }
                  InputLabelProps={{ shrink: true }}
                  required
                  fullWidth
                />
              </Stack>
            ) : (
              <Stack spacing={2}>
                <TextField
                  label="Date"
                  type="date"
                  value={submitForm.partialDate}
                  onChange={(e) =>
                    setSubmitForm({
                      ...submitForm,
                      partialDate: e.target.value,
                    })
                  }
                  InputLabelProps={{ shrink: true }}
                  required
                  fullWidth
                />
                <Stack direction={{ xs: 'column', sm: 'row' }} spacing={2}>
                  <TextField
                    label="Start time"
                    type="time"
                    value={submitForm.startTime}
                    onChange={(e) =>
                      setSubmitForm({
                        ...submitForm,
                        startTime: e.target.value,
                      })
                    }
                    InputLabelProps={{ shrink: true }}
                    required
                    fullWidth
                  />
                  <TextField
                    label="End time"
                    type="time"
                    value={submitForm.endTime}
                    onChange={(e) =>
                      setSubmitForm({
                        ...submitForm,
                        endTime: e.target.value,
                      })
                    }
                    InputLabelProps={{ shrink: true }}
                    required
                    fullWidth
                  />
                </Stack>
              </Stack>
            )}

            <Box>
              <Typography
                variant="caption"
                color="text.secondary"
                sx={{ display: 'block', mb: 0.5 }}
              >
                Quick reason
              </Typography>
              <Stack direction="row" spacing={1} sx={{ flexWrap: 'wrap', gap: 1 }}>
                {REASON_SUGGESTIONS.map((label) => (
                  <Chip
                    key={label}
                    label={label}
                    size="small"
                    variant={
                      submitForm.reason.includes(label) ? 'filled' : 'outlined'
                    }
                    color={
                      submitForm.reason.includes(label) ? 'primary' : 'default'
                    }
                    onClick={() =>
                      setSubmitForm({ ...submitForm, reason: label })
                    }
                  />
                ))}
              </Stack>
            </Box>

            <TextField
              label="Reason (optional)"
              value={submitForm.reason}
              onChange={(e) =>
                setSubmitForm({ ...submitForm, reason: e.target.value })
              }
              multiline
              minRows={2}
              fullWidth
            />
          </Stack>
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setSubmitOpen(false)}>Cancel</Button>
          <Button
            variant="contained"
            onClick={handleSubmit}
            disabled={busyId === 'submit' || !payload}
          >
            Submit
          </Button>
        </DialogActions>
      </Dialog>
    </Stack>
  )
}

// Translate the form state into the two local Date objects we'll
// send to the API. Returns null if the form is incomplete so the
// submit button can stay disabled.
function buildPayload(form) {
  if (form.allDay) {
    const startsAt = localDateTime(form.startDate, '00:00:00')
    const endsAt = localDateTime(form.endDate, '23:59:59')
    if (!startsAt || !endsAt) return null
    return { startsAt, endsAt }
  }
  const startsAt = localDateTime(form.partialDate, `${form.startTime}:00`)
  const endsAt = localDateTime(form.partialDate, `${form.endTime}:00`)
  if (!startsAt || !endsAt) return null
  return { startsAt, endsAt }
}
