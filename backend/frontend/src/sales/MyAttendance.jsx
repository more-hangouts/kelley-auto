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
  Divider,
  Stack,
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableRow,
  TextField,
  Typography,
} from '@mui/material'

import {
  salesCancelCorrectionRequest,
  salesConfirmMyPunch,
  salesListMyCorrectionRequests,
  salesSubmitCorrectionRequest,
} from '../services/api'
import { useClockStatus } from './useClockStatus'

// Stylist-side attendance surface (Phase 7 Slice 2B-2). Two flows:
//
//   1. "System closed, confirm hours" — auto-closed punches surface
//      a Confirm button right next to today's punch list. The owner
//      sees the confirmation in the audit log and the punch leaves
//      the review queue.
//   2. Missed-punch correction — stylist submits "I forgot to clock
//      out, I actually left at 6:15." Owner approves or denies from
//      the admin attendance review queue.
//
// The user explicitly directed Slice 2B-2 to keep correction approval
// separate from manual punch adjustment so the timeline stays
// understandable; this surface only exposes the stylist-side halves
// of those two flows.

function formatLocalTimestamp(localIso) {
  if (!localIso) return ''
  try {
    const d = new Date(localIso)
    return d.toLocaleString(undefined, {
      month: 'short',
      day: 'numeric',
      hour: 'numeric',
      minute: '2-digit',
    })
  } catch {
    return localIso
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

export default function MyAttendance() {
  const { status: clockStatus, refetch: refetchClock } = useClockStatus()

  const [corrections, setCorrections] = useState(null)
  const [loadError, setLoadError] = useState(null)
  const [actionError, setActionError] = useState(null)
  const [busyId, setBusyId] = useState(null)
  const [submitOpen, setSubmitOpen] = useState(false)
  const [submitForm, setSubmitForm] = useState({
    punch_id: '',
    requested_check_in_at: '',
    requested_check_out_at: '',
    reason: '',
  })

  const todayPunches = useMemo(
    () => clockStatus?.today_punches || [],
    [clockStatus],
  )
  const reviewablePunches = useMemo(
    () =>
      todayPunches.filter(
        (p) =>
          p.auto_closed ||
          p.hours_confirmation_status === 'needs_review' ||
          p.hours_confirmation_status === 'adjusted',
      ),
    [todayPunches],
  )

  async function refresh() {
    setLoadError(null)
    try {
      const data = await salesListMyCorrectionRequests()
      setCorrections(data.correction_requests || [])
    } catch {
      setLoadError('Could not load your correction requests.')
      setCorrections([])
    }
  }

  useEffect(() => {
    refresh()
  }, [])

  async function handleConfirm(punchId) {
    setActionError(null)
    setBusyId(punchId)
    try {
      await salesConfirmMyPunch(punchId)
      refetchClock?.()
    } catch (err) {
      const code = err?.response?.data?.detail?.code
      if (code === 'punch_not_in_review') {
        setActionError('That punch is not in review state.')
      } else {
        setActionError('Could not confirm hours.')
      }
    } finally {
      setBusyId(null)
    }
  }

  async function handleSubmitCorrection() {
    if (!submitForm.reason.trim()) {
      setActionError('Tell the owner what happened so they can decide.')
      return
    }
    if (
      !submitForm.requested_check_in_at &&
      !submitForm.requested_check_out_at
    ) {
      setActionError('Pick a proposed clock-in OR clock-out time (or both).')
      return
    }
    setActionError(null)
    setBusyId('submit')
    try {
      const body = { reason: submitForm.reason.trim() }
      if (submitForm.punch_id) {
        body.punch_id = Number(submitForm.punch_id)
      }
      if (submitForm.requested_check_in_at) {
        body.requested_check_in_at = new Date(
          submitForm.requested_check_in_at,
        ).toISOString()
      }
      if (submitForm.requested_check_out_at) {
        body.requested_check_out_at = new Date(
          submitForm.requested_check_out_at,
        ).toISOString()
      }
      await salesSubmitCorrectionRequest(body)
      setSubmitOpen(false)
      setSubmitForm({
        punch_id: '',
        requested_check_in_at: '',
        requested_check_out_at: '',
        reason: '',
      })
      await refresh()
    } catch (err) {
      const code = err?.response?.data?.detail?.code
      if (code === 'naive_datetime') {
        setActionError('Date pickers should provide local time.')
      } else if (code === 'punch_not_yours') {
        setActionError('You can only file a correction against your own punches.')
      } else {
        setActionError('Could not submit the correction.')
      }
    } finally {
      setBusyId(null)
    }
  }

  async function handleCancel(requestId) {
    setActionError(null)
    setBusyId(requestId)
    try {
      await salesCancelCorrectionRequest(requestId)
      await refresh()
    } catch {
      setActionError('Could not cancel that request.')
    } finally {
      setBusyId(null)
    }
  }

  return (
    <Stack spacing={3}>
      <Box>
        <Typography variant="h5">My attendance</Typography>
        <Typography variant="body2" color="text.secondary">
          Confirm auto-closed punches and file a missed-punch correction
          if you forgot to clock in or out.
        </Typography>
      </Box>

      {loadError && <Alert severity="error">{loadError}</Alert>}
      {actionError && (
        <Alert severity="error" onClose={() => setActionError(null)}>
          {actionError}
        </Alert>
      )}

      <Card variant="outlined">
        <CardContent sx={{ p: { xs: 2, sm: 3 } }}>
          <Typography variant="h6" sx={{ mb: 1 }}>
            Today's punches
          </Typography>
          {todayPunches.length === 0 ? (
            <Typography variant="body2" color="text.secondary">
              You haven't punched in or out today yet.
            </Typography>
          ) : (
            <Table size="small">
              <TableHead>
                <TableRow>
                  <TableCell>Direction</TableCell>
                  <TableCell>Time</TableCell>
                  <TableCell>Status</TableCell>
                  <TableCell align="right">Action</TableCell>
                </TableRow>
              </TableHead>
              <TableBody>
                {todayPunches.map((p) => {
                  const reviewable =
                    p.auto_closed ||
                    p.hours_confirmation_status === 'needs_review' ||
                    p.hours_confirmation_status === 'adjusted'
                  return (
                    <TableRow key={p.id}>
                      <TableCell>
                        {p.direction === 'in' ? 'Clock in' : 'Clock out'}
                      </TableCell>
                      <TableCell>
                        {formatLocalTimestamp(
                          p.punched_at_local || p.punched_at,
                        )}
                      </TableCell>
                      <TableCell>
                        <Stack direction="row" spacing={0.5}>
                          {p.auto_closed && (
                            <Chip
                              size="small"
                              color="warning"
                              label={`Auto-closed (${p.auto_close_reason || 'system'})`}
                            />
                          )}
                          {p.hours_confirmation_status === 'needs_review' && (
                            <Chip
                              size="small"
                              color="warning"
                              variant="outlined"
                              label="Needs review"
                            />
                          )}
                          {p.hours_confirmation_status === 'confirmed' && (
                            <Chip
                              size="small"
                              color="success"
                              variant="outlined"
                              label="Confirmed"
                            />
                          )}
                        </Stack>
                      </TableCell>
                      <TableCell align="right">
                        {reviewable ? (
                          <Button
                            size="small"
                            disabled={busyId === p.id}
                            onClick={() => handleConfirm(p.id)}
                          >
                            Confirm hours
                          </Button>
                        ) : (
                          <span style={{ color: 'rgba(0,0,0,0.4)' }}>—</span>
                        )}
                      </TableCell>
                    </TableRow>
                  )
                })}
              </TableBody>
            </Table>
          )}
          {reviewablePunches.length > 0 && (
            <Alert severity="info" sx={{ mt: 2 }}>
              The system closed your session automatically. Confirm the
              hours match what you actually worked, or file a correction
              with the right time.
            </Alert>
          )}
        </CardContent>
      </Card>

      <Card variant="outlined">
        <CardContent sx={{ p: { xs: 2, sm: 3 } }}>
          <Stack
            direction="row"
            justifyContent="space-between"
            alignItems="center"
            sx={{ mb: 1 }}
          >
            <Typography variant="h6">Correction requests</Typography>
            <Button
              variant="outlined"
              size="small"
              onClick={() => setSubmitOpen(true)}
            >
              New correction
            </Button>
          </Stack>

          {corrections === null ? (
            <CircularProgress size={20} />
          ) : corrections.length === 0 ? (
            <Typography variant="body2" color="text.secondary">
              You haven't filed a correction yet.
            </Typography>
          ) : (
            <Table size="small">
              <TableHead>
                <TableRow>
                  <TableCell>Submitted</TableCell>
                  <TableCell>Proposed in</TableCell>
                  <TableCell>Proposed out</TableCell>
                  <TableCell>Reason</TableCell>
                  <TableCell>Status</TableCell>
                  <TableCell align="right">Action</TableCell>
                </TableRow>
              </TableHead>
              <TableBody>
                {corrections.map((r) => (
                  <TableRow key={r.id}>
                    <TableCell>{formatLocalTimestamp(r.created_at)}</TableCell>
                    <TableCell>
                      {r.requested_check_in_at_local
                        ? formatLocalTimestamp(r.requested_check_in_at_local)
                        : '—'}
                    </TableCell>
                    <TableCell>
                      {r.requested_check_out_at_local
                        ? formatLocalTimestamp(r.requested_check_out_at_local)
                        : '—'}
                    </TableCell>
                    <TableCell>{r.reason}</TableCell>
                    <TableCell>{statusChip(r.status)}</TableCell>
                    <TableCell align="right">
                      {r.status === 'pending' ? (
                        <Button
                          size="small"
                          color="error"
                          disabled={busyId === r.id}
                          onClick={() => handleCancel(r.id)}
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
        <DialogTitle>File a correction</DialogTitle>
        <DialogContent>
          <DialogContentText sx={{ mb: 2 }}>
            Tell the owner what should have been recorded. The owner
            approves or denies. Approval applies the change with an
            audit trail.
          </DialogContentText>
          <Stack spacing={2}>
            <TextField
              label="Punch this is about (optional)"
              type="number"
              value={submitForm.punch_id}
              onChange={(e) =>
                setSubmitForm({ ...submitForm, punch_id: e.target.value })
              }
              helperText="Leave blank if this is about a punch you forgot to make at all."
              fullWidth
            />
            <TextField
              label="Proposed clock-in (local time)"
              type="datetime-local"
              value={submitForm.requested_check_in_at}
              onChange={(e) =>
                setSubmitForm({
                  ...submitForm,
                  requested_check_in_at: e.target.value,
                })
              }
              InputLabelProps={{ shrink: true }}
              fullWidth
            />
            <TextField
              label="Proposed clock-out (local time)"
              type="datetime-local"
              value={submitForm.requested_check_out_at}
              onChange={(e) =>
                setSubmitForm({
                  ...submitForm,
                  requested_check_out_at: e.target.value,
                })
              }
              InputLabelProps={{ shrink: true }}
              fullWidth
            />
            <TextField
              label="What happened?"
              value={submitForm.reason}
              onChange={(e) =>
                setSubmitForm({ ...submitForm, reason: e.target.value })
              }
              required
              multiline
              minRows={2}
              fullWidth
            />
            <Divider />
            <Typography variant="caption" color="text.secondary">
              At least one proposed time is required. The owner sees
              both your local time and the underlying UTC stamp.
            </Typography>
          </Stack>
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setSubmitOpen(false)}>Cancel</Button>
          <Button
            variant="contained"
            onClick={handleSubmitCorrection}
            disabled={busyId === 'submit'}
          >
            Submit
          </Button>
        </DialogActions>
      </Dialog>
    </Stack>
  )
}
