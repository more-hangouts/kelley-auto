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
  Stack,
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableRow,
  TextField,
  ToggleButton,
  ToggleButtonGroup,
  Typography,
} from '@mui/material'

import {
  amendAdminTimeOff,
  decideAdminTimeOff,
  listAdminTimeOff,
} from '../services/api'

// Owner time-off review (Phase 8 Slice D), mounted at
// /settings/time-off. Date-bounded list with pending requests at the
// top. Two action paths:
//
//   - Decide: approve or deny (terminal status, writes audit row,
//     emails the stylist).
//   - Amend: edit proposed times before approval; status stays
//     'pending'. Designed for "the dates work but I want to trim a
//     day" so the timeline records the owner's edit instead of
//     forcing the stylist to refile.

const RANGE_OPTIONS = [
  { value: 'this_month', label: 'This month' },
  { value: 'next_30', label: 'Next 30 days' },
  { value: 'next_90', label: 'Next 90 days' },
]

function todayPlus(days) {
  const d = new Date()
  d.setDate(d.getDate() + days)
  return d.toISOString().slice(0, 10)
}

function startOfMonth() {
  const d = new Date()
  d.setDate(1)
  return d.toISOString().slice(0, 10)
}

function endOfMonth() {
  const d = new Date()
  d.setMonth(d.getMonth() + 1, 0)
  return d.toISOString().slice(0, 10)
}

function rangeFor(key) {
  if (key === 'this_month') {
    return { from_date: startOfMonth(), to_date: endOfMonth() }
  }
  if (key === 'next_30') {
    return { from_date: todayPlus(0), to_date: todayPlus(30) }
  }
  return { from_date: todayPlus(0), to_date: todayPlus(90) }
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

export default function AdminTimeOff() {
  const [rangeKey, setRangeKey] = useState('next_30')
  const range = useMemo(() => rangeFor(rangeKey), [rangeKey])

  const [requests, setRequests] = useState(null)
  const [loadError, setLoadError] = useState(null)
  const [actionError, setActionError] = useState(null)
  const [busyId, setBusyId] = useState(null)

  const [decideDialog, setDecideDialog] = useState(null)
  const [amendDialog, setAmendDialog] = useState(null)

  async function refresh() {
    setLoadError(null)
    try {
      const data = await listAdminTimeOff(range)
      // Pending first, then by created_at desc (server already orders
      // by created_at desc, so we just stably partition).
      const pending = (data.requests || []).filter(
        (r) => r.status === 'pending',
      )
      const decided = (data.requests || []).filter(
        (r) => r.status !== 'pending',
      )
      setRequests([...pending, ...decided])
    } catch {
      setLoadError("Couldn't load the time-off queue.")
      setRequests([])
    }
  }

  useEffect(() => {
    refresh()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [rangeKey])

  async function submitDecision() {
    if (!decideDialog) return
    setActionError(null)
    setBusyId(decideDialog.request.id)
    try {
      await decideAdminTimeOff(decideDialog.request.id, {
        status: decideDialog.decision,
        decision_notes:
          (decideDialog.notes || '').trim() || null,
      })
      setDecideDialog(null)
      await refresh()
    } catch (err) {
      const code = err?.response?.data?.detail?.code
      if (code === 'time_off_request_terminal') {
        setActionError('That request was already decided. Refresh.')
        await refresh()
      } else {
        setActionError("Couldn't record the decision.")
      }
    } finally {
      setBusyId(null)
    }
  }

  async function submitAmend() {
    if (!amendDialog) return
    if (!amendDialog.starts_at && !amendDialog.ends_at) {
      setActionError('Change at least one of start or end.')
      return
    }
    setActionError(null)
    setBusyId(amendDialog.request.id)
    try {
      const body = {}
      if (amendDialog.starts_at) {
        body.starts_at = new Date(amendDialog.starts_at).toISOString()
      }
      if (amendDialog.ends_at) {
        body.ends_at = new Date(amendDialog.ends_at).toISOString()
      }
      if ((amendDialog.notes || '').trim()) {
        body.decision_notes = amendDialog.notes.trim()
      }
      await amendAdminTimeOff(amendDialog.request.id, body)
      setAmendDialog(null)
      await refresh()
    } catch (err) {
      const code = err?.response?.data?.detail?.code
      if (code === 'invalid_date_range') {
        setActionError('End must be after start.')
      } else if (code === 'time_off_request_terminal') {
        setActionError('That request was already decided. Refresh.')
        await refresh()
      } else {
        setActionError("Couldn't save that amendment.")
      }
    } finally {
      setBusyId(null)
    }
  }

  return (
    <Stack spacing={3}>
      <Box>
        <Typography variant="h4">Time off</Typography>
        <Typography variant="body2" color="text.secondary">
          Review and decide stylist time-off requests. Pending sit at
          the top. Amend lets you trim or shift the proposed dates
          before approving.
        </Typography>
      </Box>

      <ToggleButtonGroup
        value={rangeKey}
        exclusive
        onChange={(_, val) => val && setRangeKey(val)}
        size="small"
      >
        {RANGE_OPTIONS.map((opt) => (
          <ToggleButton key={opt.value} value={opt.value}>
            {opt.label}
          </ToggleButton>
        ))}
      </ToggleButtonGroup>

      {loadError && <Alert severity="error">{loadError}</Alert>}
      {actionError && (
        <Alert severity="error" onClose={() => setActionError(null)}>
          {actionError}
        </Alert>
      )}

      <Card>
        <CardContent sx={{ p: { xs: 2, sm: 3 } }}>
          {requests === null ? (
            <Box sx={{ display: 'flex', justifyContent: 'center', py: 3 }}>
              <CircularProgress size={28} />
            </Box>
          ) : requests.length === 0 ? (
            <Typography variant="body2" color="text.secondary">
              No requests in this window.
            </Typography>
          ) : (
            <Table size="small">
              <TableHead>
                <TableRow>
                  <TableCell>Stylist</TableCell>
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
                    <TableCell>
                      {r.user_full_name || r.user_username || `#${r.user_id}`}
                    </TableCell>
                    <TableCell>{formatLocal(r.created_at)}</TableCell>
                    <TableCell>{formatLocal(r.starts_at_local)}</TableCell>
                    <TableCell>{formatLocal(r.ends_at_local)}</TableCell>
                    <TableCell>{r.reason || ''}</TableCell>
                    <TableCell>{statusChip(r.status)}</TableCell>
                    <TableCell align="right">
                      {r.status === 'pending' ? (
                        <Stack
                          direction="row"
                          spacing={0.5}
                          justifyContent="flex-end"
                        >
                          <Button
                            size="small"
                            onClick={() =>
                              setAmendDialog({
                                request: r,
                                starts_at: r.starts_at_local
                                  ? r.starts_at_local.slice(0, 16)
                                  : '',
                                ends_at: r.ends_at_local
                                  ? r.ends_at_local.slice(0, 16)
                                  : '',
                                notes: '',
                              })
                            }
                          >
                            Amend
                          </Button>
                          <Button
                            size="small"
                            variant="contained"
                            disabled={busyId === r.id}
                            onClick={() =>
                              setDecideDialog({
                                request: r,
                                decision: 'approved',
                                notes: '',
                              })
                            }
                          >
                            Approve
                          </Button>
                          <Button
                            size="small"
                            color="error"
                            disabled={busyId === r.id}
                            onClick={() =>
                              setDecideDialog({
                                request: r,
                                decision: 'denied',
                                notes: '',
                              })
                            }
                          >
                            Deny
                          </Button>
                        </Stack>
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
        open={decideDialog !== null}
        onClose={() => setDecideDialog(null)}
        maxWidth="sm"
        fullWidth
      >
        <DialogTitle>
          {decideDialog?.decision === 'approved'
            ? 'Approve request?'
            : 'Deny request?'}
        </DialogTitle>
        <DialogContent>
          <DialogContentText sx={{ mb: 2 }}>
            {decideDialog?.decision === 'approved'
              ? "Approving locks the days as off. The stylist's schedule will hide those days and you'll see the decision in the audit timeline."
              : 'Denying records the decision and emails the stylist. The proposed dates stay on file for the audit trail.'}
          </DialogContentText>
          <TextField
            label="Decision notes (optional)"
            value={decideDialog?.notes || ''}
            onChange={(e) =>
              setDecideDialog((d) => ({ ...d, notes: e.target.value }))
            }
            multiline
            minRows={2}
            fullWidth
          />
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setDecideDialog(null)}>Cancel</Button>
          <Button
            variant="contained"
            color={decideDialog?.decision === 'approved' ? 'primary' : 'error'}
            onClick={submitDecision}
            disabled={busyId === decideDialog?.request?.id}
          >
            {decideDialog?.decision === 'approved' ? 'Approve' : 'Deny'}
          </Button>
        </DialogActions>
      </Dialog>

      <Dialog
        open={amendDialog !== null}
        onClose={() => setAmendDialog(null)}
        maxWidth="sm"
        fullWidth
      >
        <DialogTitle>Amend request</DialogTitle>
        <DialogContent>
          <DialogContentText sx={{ mb: 2 }}>
            Edit the proposed start or end. The request stays pending
            after the amendment so the stylist sees the change before
            you approve.
          </DialogContentText>
          <Stack spacing={2}>
            <TextField
              label="Start"
              type="datetime-local"
              value={amendDialog?.starts_at || ''}
              onChange={(e) =>
                setAmendDialog((d) => ({ ...d, starts_at: e.target.value }))
              }
              InputLabelProps={{ shrink: true }}
              fullWidth
            />
            <TextField
              label="End"
              type="datetime-local"
              value={amendDialog?.ends_at || ''}
              onChange={(e) =>
                setAmendDialog((d) => ({ ...d, ends_at: e.target.value }))
              }
              InputLabelProps={{ shrink: true }}
              fullWidth
            />
            <TextField
              label="Notes (optional)"
              value={amendDialog?.notes || ''}
              onChange={(e) =>
                setAmendDialog((d) => ({ ...d, notes: e.target.value }))
              }
              multiline
              minRows={2}
              fullWidth
            />
          </Stack>
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setAmendDialog(null)}>Cancel</Button>
          <Button
            variant="contained"
            onClick={submitAmend}
            disabled={busyId === amendDialog?.request?.id}
          >
            Save amendment
          </Button>
        </DialogActions>
      </Dialog>
    </Stack>
  )
}
