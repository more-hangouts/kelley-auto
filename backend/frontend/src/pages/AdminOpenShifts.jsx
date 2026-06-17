import { useCallback, useEffect, useMemo, useState } from 'react'
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
  DialogTitle,
  MenuItem,
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
  cancelAdminOpenShift,
  createAdminOpenShift,
  listAdminOpenShifts,
} from '../services/api'

// Staff Management > Schedule & time off > Open shifts (Phase 3).
// Post a shift without an assignee; staff claim it from their portal and
// the claim is approved through the Shift requests queue.

const STATUS_CHIP = {
  open: { label: 'Open', color: 'info' },
  claimed: { label: 'Claimed', color: 'success' },
  cancelled: { label: 'Cancelled', color: 'default' },
  expired: { label: 'Expired', color: 'default' },
}

const STATUS_FILTERS = [
  { value: 'all', label: 'All' },
  { value: 'open', label: 'Open' },
  { value: 'claimed', label: 'Claimed' },
  { value: 'cancelled', label: 'Cancelled' },
  { value: 'expired', label: 'Expired' },
]

function statusChip(status) {
  const cfg = STATUS_CHIP[status] || { label: status, color: 'default' }
  return <Chip size="small" label={cfg.label} color={cfg.color} />
}

function isoDate(d) {
  const y = d.getFullYear()
  const m = String(d.getMonth() + 1).padStart(2, '0')
  const day = String(d.getDate()).padStart(2, '0')
  return `${y}-${m}-${day}`
}

function isoFromLocalInput(value) {
  if (!value) return null
  const d = new Date(value)
  return Number.isNaN(d.getTime()) ? null : d.toISOString()
}

function businessDateFromLocalInput(value) {
  if (!value) return ''
  const d = new Date(value)
  if (Number.isNaN(d.getTime())) return ''
  return isoDate(d)
}

function fmtDay(iso) {
  return new Date(`${iso}T00:00:00`).toLocaleDateString(undefined, {
    weekday: 'short',
    month: 'short',
    day: 'numeric',
  })
}

function fmtTime(iso) {
  if (!iso) return ''
  return new Date(iso).toLocaleTimeString(undefined, {
    hour: 'numeric',
    minute: '2-digit',
  })
}

export default function AdminOpenShifts() {
  const [statusFilter, setStatusFilter] = useState('all')
  const [posts, setPosts] = useState(null)
  const [loadError, setLoadError] = useState(null)

  const [createOpen, setCreateOpen] = useState(false)
  const [startInput, setStartInput] = useState('')
  const [endInput, setEndInput] = useState('')
  const [note, setNote] = useState('')
  const [busy, setBusy] = useState(false)
  const [dialogError, setDialogError] = useState(null)
  const [cancelBusyId, setCancelBusyId] = useState(null)

  const range = useMemo(() => {
    const today = new Date()
    const to = new Date(today)
    to.setDate(today.getDate() + 30)
    return { from_date: isoDate(today), to_date: isoDate(to) }
  }, [])

  const params = useMemo(() => {
    const p = { ...range }
    if (statusFilter !== 'all') p.status = statusFilter
    return p
  }, [range, statusFilter])

  const refresh = useCallback(() => {
    setLoadError(null)
    listAdminOpenShifts(params)
      .then((data) => setPosts(data.posts || []))
      .catch(() => {
        setLoadError("Couldn't load open shifts.")
        setPosts([])
      })
  }, [params])

  useEffect(() => {
    refresh()
  }, [refresh])

  function openCreate() {
    setStartInput('')
    setEndInput('')
    setNote('')
    setDialogError(null)
    setCreateOpen(true)
  }

  async function submitCreate() {
    const startIso = isoFromLocalInput(startInput)
    const endIso = isoFromLocalInput(endInput)
    if (!startIso || !endIso) {
      setDialogError('Pick a start and end time.')
      return
    }
    if (new Date(endIso) <= new Date(startIso)) {
      setDialogError('End must be after start.')
      return
    }
    setBusy(true)
    setDialogError(null)
    try {
      await createAdminOpenShift({
        business_date: businessDateFromLocalInput(startInput),
        starts_at_local: startIso,
        ends_at_local: endIso,
        manager_notes: note.trim() || undefined,
      })
      setCreateOpen(false)
      refresh()
    } catch (err) {
      const code = err?.response?.data?.detail?.code
      setDialogError(
        code === 'business_date_mismatch'
          ? 'Start time and date disagree — check the date.'
          : "Couldn't post the open shift. Try again.",
      )
    } finally {
      setBusy(false)
    }
  }

  async function cancel(id) {
    setCancelBusyId(id)
    setLoadError(null)
    try {
      await cancelAdminOpenShift(id)
      refresh()
    } catch {
      setLoadError("Couldn't cancel that post.")
    } finally {
      setCancelBusyId(null)
    }
  }

  return (
    <Stack spacing={2}>
      <Stack
        direction="row"
        justifyContent="space-between"
        alignItems="flex-start"
        spacing={2}
      >
        <Box>
          <Typography variant="h6">Open shifts</Typography>
          <Typography variant="body2" color="text.secondary">
            Post a shift with no assignee. Staff claim it from their
            portal; you approve the claim in Shift requests, which puts the
            shift on their schedule.
          </Typography>
        </Box>
        <Button variant="contained" onClick={openCreate}>
          Post open shift
        </Button>
      </Stack>

      <TextField
        select
        size="small"
        label="Status"
        value={statusFilter}
        onChange={(e) => setStatusFilter(e.target.value)}
        sx={{ maxWidth: 220 }}
      >
        {STATUS_FILTERS.map((s) => (
          <MenuItem key={s.value} value={s.value}>
            {s.label}
          </MenuItem>
        ))}
      </TextField>

      {loadError && <Alert severity="error">{loadError}</Alert>}

      <Card>
        <CardContent sx={{ p: { xs: 2, sm: 3 } }}>
          {posts === null ? (
            <Box sx={{ display: 'flex', justifyContent: 'center', py: 3 }}>
              <CircularProgress size={28} />
            </Box>
          ) : posts.length === 0 ? (
            <Typography variant="body2" color="text.secondary">
              No open shifts in the next 30 days.
            </Typography>
          ) : (
            <Table size="small">
              <TableHead>
                <TableRow>
                  <TableCell>Day</TableCell>
                  <TableCell>Time</TableCell>
                  <TableCell>Note</TableCell>
                  <TableCell>Status</TableCell>
                  <TableCell>Claimed by</TableCell>
                  <TableCell align="right">Action</TableCell>
                </TableRow>
              </TableHead>
              <TableBody>
                {posts.map((p) => (
                  <TableRow key={p.id}>
                    <TableCell>{fmtDay(p.business_date)}</TableCell>
                    <TableCell>
                      {fmtTime(p.starts_at_local)} to{' '}
                      {fmtTime(p.ends_at_local)}
                    </TableCell>
                    <TableCell>{p.manager_notes || ''}</TableCell>
                    <TableCell>{statusChip(p.status)}</TableCell>
                    <TableCell>{p.claimed_by_full_name || ''}</TableCell>
                    <TableCell align="right">
                      {p.status === 'open' ? (
                        <Button
                          size="small"
                          color="error"
                          variant="outlined"
                          disabled={cancelBusyId === p.id}
                          onClick={() => cancel(p.id)}
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
        open={createOpen}
        onClose={() => (busy ? null : setCreateOpen(false))}
        fullWidth
        maxWidth="xs"
      >
        <DialogTitle>Post open shift</DialogTitle>
        <DialogContent>
          <Stack spacing={2} sx={{ mt: 0.5 }}>
            <TextField
              label="Start"
              type="datetime-local"
              value={startInput}
              onChange={(e) => setStartInput(e.target.value)}
              size="small"
              fullWidth
              InputLabelProps={{ shrink: true }}
            />
            <TextField
              label="End"
              type="datetime-local"
              value={endInput}
              onChange={(e) => setEndInput(e.target.value)}
              size="small"
              fullWidth
              InputLabelProps={{ shrink: true }}
            />
            <TextField
              label="Note for staff (optional)"
              value={note}
              onChange={(e) => setNote(e.target.value)}
              size="small"
              fullWidth
              multiline
              minRows={2}
              inputProps={{ maxLength: 500 }}
            />
            {dialogError && <Alert severity="error">{dialogError}</Alert>}
          </Stack>
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setCreateOpen(false)} disabled={busy}>
            Cancel
          </Button>
          <Button variant="contained" onClick={submitCreate} disabled={busy}>
            Post
          </Button>
        </DialogActions>
      </Dialog>
    </Stack>
  )
}
