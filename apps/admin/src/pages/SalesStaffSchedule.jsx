import { useEffect, useMemo, useState } from 'react'
import { useParams, Link as RouterLink } from 'react-router-dom'
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
  IconButton,
  MenuItem,
  Select,
  Stack,
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableRow,
  TextField,
  Typography,
} from '@mui/material'
import DeleteOutlineIcon from '@mui/icons-material/DeleteOutline'

import {
  createAdminShift,
  createAdminShiftOverride,
  deleteAdminShift,
  deleteAdminShiftOverride,
  listAdminShiftOverlaps,
  listAdminShifts,
  listAdminShiftOverrides,
  listSalesStaff,
} from '../services/api'

// Owner shift-assignment page (Phase 8 Slice D), mounted at
// /settings/sales-staff/{user_id}/schedule. Three sections:
//
//   1. Base shifts — recurring weekly templates, anchored on a date
//      with `working_days` controlling which weekdays repeat.
//   2. Overrides — temporary date-range exceptions that swap in a
//      different shift template for those days.
//   3. Overlaps — read-only visualizer per the user's Slice C
//      enforcement #6: surfaces collisions in the calendar overlay,
//      never blocks a shift create.
//
// Shift edit is intentionally NOT in v1: easier and clearer to delete
// + recreate than to expose a partial PATCH dialog. The PATCH
// endpoint exists for the API contract; this page can grow into it.

const ISO_WEEKDAYS = [
  { value: 1, label: 'Mon' },
  { value: 2, label: 'Tue' },
  { value: 3, label: 'Wed' },
  { value: 4, label: 'Thu' },
  { value: 5, label: 'Fri' },
  { value: 6, label: 'Sat' },
  { value: 7, label: 'Sun' },
]

function formatTime(iso) {
  if (!iso) return ''
  try {
    return new Date(iso).toLocaleTimeString(undefined, {
      hour: 'numeric',
      minute: '2-digit',
    })
  } catch {
    return iso
  }
}

function workingDaysLabel(days) {
  if (!days || days.length === 0) return ''
  const set = new Set(days)
  return ISO_WEEKDAYS.filter((d) => set.has(d.value))
    .map((d) => d.label)
    .join(', ')
}

function todayIso() {
  const d = new Date()
  d.setHours(0, 0, 0, 0)
  return d.toISOString().slice(0, 10)
}

function plusDaysIso(isoDate, n) {
  const [y, m, d] = isoDate.split('-').map(Number)
  const dt = new Date(y, m - 1, d)
  dt.setDate(dt.getDate() + n)
  return dt.toISOString().slice(0, 10)
}

const DEFAULT_SHIFT_FORM = {
  starts_at: '',
  ends_at: '',
  working_days: [1, 2, 3, 4, 5],
  late_grace_period_minutes: 10,
  earliest_check_in_minutes: 60,
  early_out_grace_minutes: 10,
  auto_session_close_time: '22:00',
  max_session_hours: '',
  notes: '',
}

const DEFAULT_OVERRIDE_FORM = {
  shift_id: '',
  starts_on: '',
  ends_on: '',
  reason: '',
}

export default function SalesStaffSchedule() {
  const { userId } = useParams()
  const numericUserId = Number(userId)

  const [stylist, setStylist] = useState(null)
  const [shifts, setShifts] = useState(null)
  const [overrides, setOverrides] = useState(null)
  const [overlaps, setOverlaps] = useState(null)
  const [loadError, setLoadError] = useState(null)
  const [actionError, setActionError] = useState(null)
  const [busyId, setBusyId] = useState(null)

  const [shiftDialogOpen, setShiftDialogOpen] = useState(false)
  const [shiftForm, setShiftForm] = useState(DEFAULT_SHIFT_FORM)
  const [overrideDialogOpen, setOverrideDialogOpen] = useState(false)
  const [overrideForm, setOverrideForm] = useState(DEFAULT_OVERRIDE_FORM)

  // Two-week overlap window starting today.
  const overlapWindow = useMemo(() => {
    const from = todayIso()
    return { from_date: from, to_date: plusDaysIso(from, 13) }
  }, [])

  async function refresh() {
    setLoadError(null)
    try {
      const [staffRows, shiftsResp, overridesResp, overlapsResp] =
        await Promise.all([
          stylist === null ? listSalesStaff() : Promise.resolve([stylist]),
          listAdminShifts({ user_id: numericUserId }),
          listAdminShiftOverrides({ user_id: numericUserId }),
          listAdminShiftOverlaps({
            user_id: numericUserId,
            ...overlapWindow,
          }),
        ])
      if (Array.isArray(staffRows) && staffRows.length > 0) {
        const s = staffRows.find((r) => r.id === numericUserId)
        if (s) setStylist(s)
      }
      setShifts(shiftsResp.shifts || [])
      setOverrides(overridesResp.overrides || [])
      setOverlaps(overlapsResp.overlaps || [])
    } catch {
      setLoadError("Couldn't load schedule data.")
      setShifts([])
      setOverrides([])
      setOverlaps([])
    }
  }

  useEffect(() => {
    refresh()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [numericUserId])

  function toggleWorkingDay(day) {
    setShiftForm((f) => {
      const set = new Set(f.working_days)
      if (set.has(day)) set.delete(day)
      else set.add(day)
      return { ...f, working_days: Array.from(set).sort() }
    })
  }

  async function handleCreateShift() {
    if (!shiftForm.starts_at || !shiftForm.ends_at) {
      setActionError('Pick a start and end.')
      return
    }
    if (shiftForm.working_days.length === 0) {
      setActionError('Pick at least one working day.')
      return
    }
    setActionError(null)
    setBusyId('create_shift')
    try {
      const body = {
        user_id: numericUserId,
        starts_at: new Date(shiftForm.starts_at).toISOString(),
        ends_at: new Date(shiftForm.ends_at).toISOString(),
        working_days: shiftForm.working_days,
        late_grace_period_minutes: Number(
          shiftForm.late_grace_period_minutes,
        ),
        earliest_check_in_minutes: Number(
          shiftForm.earliest_check_in_minutes,
        ),
        early_out_grace_minutes: Number(shiftForm.early_out_grace_minutes),
        auto_session_close_time: shiftForm.auto_session_close_time
          ? `${shiftForm.auto_session_close_time}:00`
          : null,
        max_session_hours: shiftForm.max_session_hours
          ? Number(shiftForm.max_session_hours)
          : null,
        notes: shiftForm.notes.trim() || null,
      }
      await createAdminShift(body)
      setShiftDialogOpen(false)
      setShiftForm(DEFAULT_SHIFT_FORM)
      await refresh()
    } catch (err) {
      const code = err?.response?.data?.detail?.code
      if (code === 'invalid_date_range') {
        setActionError('End must be after start.')
      } else {
        setActionError("Couldn't save that shift.")
      }
    } finally {
      setBusyId(null)
    }
  }

  async function handleDeleteShift(shift) {
    if (
      !window.confirm(
        `Delete this shift? Historical punches keep their record but their shift link goes blank.`,
      )
    ) {
      return
    }
    setActionError(null)
    setBusyId(shift.id)
    try {
      await deleteAdminShift(shift.id)
      await refresh()
    } catch {
      setActionError("Couldn't delete the shift.")
    } finally {
      setBusyId(null)
    }
  }

  async function handleCreateOverride() {
    if (
      !overrideForm.shift_id ||
      !overrideForm.starts_on ||
      !overrideForm.ends_on
    ) {
      setActionError('Pick a shift and a date range.')
      return
    }
    setActionError(null)
    setBusyId('create_override')
    try {
      await createAdminShiftOverride({
        user_id: numericUserId,
        shift_id: Number(overrideForm.shift_id),
        starts_on: overrideForm.starts_on,
        ends_on: overrideForm.ends_on,
        reason: overrideForm.reason.trim() || null,
      })
      setOverrideDialogOpen(false)
      setOverrideForm(DEFAULT_OVERRIDE_FORM)
      await refresh()
    } catch (err) {
      const code = err?.response?.data?.detail?.code
      if (code === 'invalid_date_range') {
        setActionError('End date must be on or after start date.')
      } else {
        setActionError("Couldn't save that override.")
      }
    } finally {
      setBusyId(null)
    }
  }

  async function handleDeleteOverride(ov) {
    if (!window.confirm('Delete this override?')) return
    setActionError(null)
    setBusyId(ov.id)
    try {
      await deleteAdminShiftOverride(ov.id)
      await refresh()
    } catch {
      setActionError("Couldn't delete the override.")
    } finally {
      setBusyId(null)
    }
  }

  if (shifts === null) {
    return (
      <Box sx={{ display: 'flex', justifyContent: 'center', py: 6 }}>
        <CircularProgress />
      </Box>
    )
  }

  return (
    <Stack spacing={3}>
      <Box>
        <Typography variant="h4">
          {stylist?.full_name || stylist?.username || 'Stylist'} — Schedule
        </Typography>
        <Typography variant="body2" color="text.secondary">
          Manage recurring shifts and one-off date overrides. Overlaps
          are visible below; the system never blocks scheduling on
          overlaps so you can model coverage handoffs.
        </Typography>
        <Button
          component={RouterLink}
          to="/settings/staff/profiles"
          size="small"
          sx={{ mt: 1 }}
        >
          Back to staff profiles
        </Button>
      </Box>

      {loadError && <Alert severity="error">{loadError}</Alert>}
      {actionError && (
        <Alert severity="error" onClose={() => setActionError(null)}>
          {actionError}
        </Alert>
      )}

      {/* Base shifts */}
      <Card>
        <CardContent sx={{ p: { xs: 2, sm: 3 } }}>
          <Stack
            direction="row"
            justifyContent="space-between"
            alignItems="center"
            sx={{ mb: 2 }}
          >
            <Typography variant="h6">Base shifts</Typography>
            <Button
              variant="contained"
              size="small"
              onClick={() => {
                setShiftForm(DEFAULT_SHIFT_FORM)
                setShiftDialogOpen(true)
              }}
            >
              Add shift
            </Button>
          </Stack>
          {shifts.length === 0 ? (
            <Typography variant="body2" color="text.secondary">
              No shifts yet. Add one to start scheduling this stylist.
            </Typography>
          ) : (
            <Table size="small">
              <TableHead>
                <TableRow>
                  <TableCell>Time</TableCell>
                  <TableCell>Days</TableCell>
                  <TableCell>Late grace</TableCell>
                  <TableCell>Earliest check-in</TableCell>
                  <TableCell>Auto-close</TableCell>
                  <TableCell>Max hours</TableCell>
                  <TableCell />
                </TableRow>
              </TableHead>
              <TableBody>
                {shifts.map((s) => (
                  <TableRow key={s.id}>
                    <TableCell>
                      {formatTime(s.starts_at)} to {formatTime(s.ends_at)}
                    </TableCell>
                    <TableCell>{workingDaysLabel(s.working_days)}</TableCell>
                    <TableCell>{s.late_grace_period_minutes}m</TableCell>
                    <TableCell>{s.earliest_check_in_minutes}m</TableCell>
                    <TableCell>{s.auto_session_close_time || '—'}</TableCell>
                    <TableCell>
                      {s.max_session_hours
                        ? `${s.max_session_hours}h`
                        : '—'}
                    </TableCell>
                    <TableCell align="right">
                      <IconButton
                        size="small"
                        color="error"
                        disabled={busyId === s.id}
                        onClick={() => handleDeleteShift(s)}
                      >
                        <DeleteOutlineIcon fontSize="small" />
                      </IconButton>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
        </CardContent>
      </Card>

      {/* Overrides */}
      <Card>
        <CardContent sx={{ p: { xs: 2, sm: 3 } }}>
          <Stack
            direction="row"
            justifyContent="space-between"
            alignItems="center"
            sx={{ mb: 2 }}
          >
            <Typography variant="h6">Overrides</Typography>
            <Button
              variant="outlined"
              size="small"
              disabled={shifts.length === 0}
              onClick={() => {
                setOverrideForm({
                  ...DEFAULT_OVERRIDE_FORM,
                  shift_id: shifts[0]?.id || '',
                })
                setOverrideDialogOpen(true)
              }}
            >
              Add override
            </Button>
          </Stack>
          {overrides.length === 0 ? (
            <Typography variant="body2" color="text.secondary">
              No overrides. Use these for one-off coverage like "Maria
              covers Saturday this week."
            </Typography>
          ) : (
            <Table size="small">
              <TableHead>
                <TableRow>
                  <TableCell>From</TableCell>
                  <TableCell>To</TableCell>
                  <TableCell>Shift</TableCell>
                  <TableCell>Reason</TableCell>
                  <TableCell />
                </TableRow>
              </TableHead>
              <TableBody>
                {overrides.map((o) => {
                  const shift = shifts.find((s) => s.id === o.shift_id)
                  return (
                    <TableRow key={o.id}>
                      <TableCell>{o.starts_on}</TableCell>
                      <TableCell>{o.ends_on}</TableCell>
                      <TableCell>
                        {shift
                          ? `${formatTime(shift.starts_at)} to ${formatTime(shift.ends_at)}`
                          : `Shift #${o.shift_id}`}
                      </TableCell>
                      <TableCell>{o.reason || ''}</TableCell>
                      <TableCell align="right">
                        <IconButton
                          size="small"
                          color="error"
                          disabled={busyId === o.id}
                          onClick={() => handleDeleteOverride(o)}
                        >
                          <DeleteOutlineIcon fontSize="small" />
                        </IconButton>
                      </TableCell>
                    </TableRow>
                  )
                })}
              </TableBody>
            </Table>
          )}
        </CardContent>
      </Card>

      {/* Overlaps (read-only) */}
      <Card>
        <CardContent sx={{ p: { xs: 2, sm: 3 } }}>
          <Stack
            direction="row"
            justifyContent="space-between"
            alignItems="center"
            sx={{ mb: 2 }}
          >
            <Typography variant="h6">Overlaps (next 14 days)</Typography>
            <Typography variant="caption" color="text.secondary">
              Read-only. Overlaps are surfaced for visibility, not blocked.
            </Typography>
          </Stack>
          {overlaps.length === 0 ? (
            <Typography variant="body2" color="text.secondary">
              No overlaps in the next two weeks.
            </Typography>
          ) : (
            <Stack spacing={1}>
              {overlaps.map((o, idx) => (
                <Alert key={idx} severity="warning" variant="outlined">
                  <Typography variant="body2" sx={{ fontWeight: 600 }}>
                    {o.business_date}
                  </Typography>
                  <Typography variant="caption">
                    {formatTime(o.a.starts_at_local)}
                    {' to '}
                    {formatTime(o.a.ends_at_local)}
                    {o.a.is_override && ' (override)'}
                    {' overlaps '}
                    {formatTime(o.b.starts_at_local)}
                    {' to '}
                    {formatTime(o.b.ends_at_local)}
                    {o.b.is_override && ' (override)'}
                  </Typography>
                </Alert>
              ))}
            </Stack>
          )}
        </CardContent>
      </Card>

      {/* Add shift dialog */}
      <Dialog
        open={shiftDialogOpen}
        onClose={() => setShiftDialogOpen(false)}
        maxWidth="sm"
        fullWidth
      >
        <DialogTitle>Add shift</DialogTitle>
        <DialogContent>
          <DialogContentText sx={{ mb: 2 }}>
            Pick a start, end, and the weekdays this shift repeats on.
            Overnight shifts are fine — just pick an end after midnight.
          </DialogContentText>
          <Stack spacing={2}>
            <TextField
              label="Start"
              type="datetime-local"
              value={shiftForm.starts_at}
              onChange={(e) =>
                setShiftForm({ ...shiftForm, starts_at: e.target.value })
              }
              InputLabelProps={{ shrink: true }}
              required
              fullWidth
            />
            <TextField
              label="End"
              type="datetime-local"
              value={shiftForm.ends_at}
              onChange={(e) =>
                setShiftForm({ ...shiftForm, ends_at: e.target.value })
              }
              InputLabelProps={{ shrink: true }}
              required
              fullWidth
            />
            <Box>
              <Typography variant="caption" color="text.secondary">
                Working days
              </Typography>
              <Stack direction="row" spacing={0.5} sx={{ mt: 1, flexWrap: 'wrap' }}>
                {ISO_WEEKDAYS.map((d) => (
                  <Chip
                    key={d.value}
                    label={d.label}
                    onClick={() => toggleWorkingDay(d.value)}
                    color={
                      shiftForm.working_days.includes(d.value)
                        ? 'primary'
                        : 'default'
                    }
                    variant={
                      shiftForm.working_days.includes(d.value)
                        ? 'filled'
                        : 'outlined'
                    }
                  />
                ))}
              </Stack>
            </Box>
            <Stack direction="row" spacing={1}>
              <TextField
                label="Late grace (min)"
                type="number"
                value={shiftForm.late_grace_period_minutes}
                onChange={(e) =>
                  setShiftForm({
                    ...shiftForm,
                    late_grace_period_minutes: e.target.value,
                  })
                }
                inputProps={{ min: 0, max: 120 }}
                fullWidth
              />
              <TextField
                label="Earliest check-in (min)"
                type="number"
                value={shiftForm.earliest_check_in_minutes}
                onChange={(e) =>
                  setShiftForm({
                    ...shiftForm,
                    earliest_check_in_minutes: e.target.value,
                  })
                }
                inputProps={{ min: 0, max: 720 }}
                fullWidth
              />
            </Stack>
            <Stack direction="row" spacing={1}>
              <TextField
                label="Early-out grace (min)"
                type="number"
                value={shiftForm.early_out_grace_minutes}
                onChange={(e) =>
                  setShiftForm({
                    ...shiftForm,
                    early_out_grace_minutes: e.target.value,
                  })
                }
                inputProps={{ min: 0, max: 120 }}
                fullWidth
              />
              <TextField
                label="Auto-close time"
                type="time"
                value={shiftForm.auto_session_close_time}
                onChange={(e) =>
                  setShiftForm({
                    ...shiftForm,
                    auto_session_close_time: e.target.value,
                  })
                }
                InputLabelProps={{ shrink: true }}
                fullWidth
              />
            </Stack>
            <TextField
              label="Max session hours (optional)"
              type="number"
              value={shiftForm.max_session_hours}
              onChange={(e) =>
                setShiftForm({
                  ...shiftForm,
                  max_session_hours: e.target.value,
                })
              }
              inputProps={{ min: 1, max: 24, step: 0.5 }}
              helperText="Sessions longer than this auto-close as 'max time reached.'"
              fullWidth
            />
            <TextField
              label="Notes (optional)"
              value={shiftForm.notes}
              onChange={(e) =>
                setShiftForm({ ...shiftForm, notes: e.target.value })
              }
              multiline
              minRows={2}
              fullWidth
            />
          </Stack>
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setShiftDialogOpen(false)}>Cancel</Button>
          <Button
            variant="contained"
            onClick={handleCreateShift}
            disabled={busyId === 'create_shift'}
          >
            Save shift
          </Button>
        </DialogActions>
      </Dialog>

      {/* Add override dialog */}
      <Dialog
        open={overrideDialogOpen}
        onClose={() => setOverrideDialogOpen(false)}
        maxWidth="sm"
        fullWidth
      >
        <DialogTitle>Add override</DialogTitle>
        <DialogContent>
          <DialogContentText sx={{ mb: 2 }}>
            Pick a shift template and the date range it covers. The
            override applies on every day in the range, even if the
            shift's normal weekdays don't include them — that's the
            whole point of an override.
          </DialogContentText>
          <Stack spacing={2}>
            <Select
              value={overrideForm.shift_id}
              onChange={(e) =>
                setOverrideForm({
                  ...overrideForm,
                  shift_id: e.target.value,
                })
              }
              fullWidth
              size="small"
            >
              {shifts.map((s) => (
                <MenuItem key={s.id} value={s.id}>
                  {formatTime(s.starts_at)} to {formatTime(s.ends_at)}
                  {' · '}
                  {workingDaysLabel(s.working_days)}
                </MenuItem>
              ))}
            </Select>
            <TextField
              label="From date"
              type="date"
              value={overrideForm.starts_on}
              onChange={(e) =>
                setOverrideForm({
                  ...overrideForm,
                  starts_on: e.target.value,
                })
              }
              InputLabelProps={{ shrink: true }}
              required
              fullWidth
            />
            <TextField
              label="To date"
              type="date"
              value={overrideForm.ends_on}
              onChange={(e) =>
                setOverrideForm({
                  ...overrideForm,
                  ends_on: e.target.value,
                })
              }
              InputLabelProps={{ shrink: true }}
              required
              fullWidth
            />
            <TextField
              label="Reason (optional)"
              value={overrideForm.reason}
              onChange={(e) =>
                setOverrideForm({
                  ...overrideForm,
                  reason: e.target.value,
                })
              }
              multiline
              minRows={2}
              fullWidth
            />
          </Stack>
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setOverrideDialogOpen(false)}>Cancel</Button>
          <Button
            variant="contained"
            onClick={handleCreateOverride}
            disabled={busyId === 'create_override'}
          >
            Save override
          </Button>
        </DialogActions>
      </Dialog>
    </Stack>
  )
}
